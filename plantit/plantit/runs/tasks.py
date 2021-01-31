import binascii
import os
import traceback
from datetime import timedelta
from os import environ
from os.path import join

import yaml
from celery.result import AsyncResult
from celery.utils.log import get_task_logger
from django.contrib.auth.models import User
from django.utils import timezone

from plantit import settings
from plantit.celery import app
from plantit.runs.models import Run
from plantit.runs.ssh import SSH
from plantit.runs.utils import update_log, execute_command, old_flow_config_to_new, parse_walltime
from plantit.targets.models import Target

logger = get_task_logger(__name__)


def __create_run(username, flow, target, submission_task_id) -> Run:
    walltime = parse_walltime(flow['config']['target']['resources']['time']) if 'resources' in flow['config']['target'] else timedelta(minutes=10)
    now = timezone.now()
    run = Run.objects.create(
        user=User.objects.get(username=username),
        flow_owner=flow['repo']['owner']['login'],
        flow_name=flow['repo']['name'],
        target=target,
        created=now,
        submission_task_id=submission_task_id,
        work_dir=submission_task_id + "/",
        remote_results_path=submission_task_id + "/",
        token=binascii.hexlify(os.urandom(20)).decode(),
        walltime=walltime.total_seconds(),
        timeout=walltime.total_seconds() * int(settings.RUNS_TIMEOUT_MULTIPLIER))  # multiplier allows for cluster scheduler delay

    # add tags
    for tag in flow['config']['tags']:
        run.tags.add(tag)

    run.save()
    return run


def __upload(flow, run, target, ssh):
    # update flow config before uploading
    flow['config']['submission_task_id'] = run.submission_task_id
    flow['config']['workdir'] = join(target.workdir, run.submission_task_id)
    flow['config']['log_file'] = f"{run.submission_task_id}.{target.name.lower()}.log"
    if 'output' in flow['config']:
        flow['config']['output']['from'] = join(target.workdir, run.work_dir, flow['config']['output']['from'])

    # if flow has outputs, make sure we don't push configuration or job scripts
    if 'output' in flow['config']:
        flow['config']['output']['exclude']['names'] = [
            "flow.yaml",
            "template_local_run.sh",
            "template_slurm_run.sh"]

    resources = None if 'resources' not in flow['config']['target'] else flow['config']['target']['resources']  # cluster resource requests, if any
    callback_url = settings.API_URL + 'runs/' + run.submission_task_id + '/status/'  # PlantIT status update callback URL
    work_dir = join(run.target.workdir, run.work_dir)
    new_flow = old_flow_config_to_new(flow, run, resources)  # TODO update flow UI page

    # create working directory
    execute_command(ssh_client=ssh, pre_command=':', command=f"mkdir {work_dir}", directory=run.target.workdir)

    # upload flow config and job script
    with ssh.client.open_sftp() as sftp:
        sftp.chdir(work_dir)

        # TODO refactor to allow multiple cluster schedulers
        sandbox = run.target.name == 'Sandbox'  # for now, we're either in the sandbox or on a SLURM cluster
        template = environ.get('CELERY_TEMPLATE_LOCAL_RUN_SCRIPT') if sandbox else environ.get('CELERY_TEMPLATE_SLURM_RUN_SCRIPT')
        template_name = template.split('/')[-1]

        # upload flow config file
        with sftp.open('flow.yaml', 'w') as flow_file:
            yaml.dump(new_flow, flow_file, default_flow_style=False)

        # compose and upload job script
        with open(template, 'r') as template_script, sftp.open(template_name, 'w') as script:
            for line in template_script:
                script.write(line)

            if not sandbox:  # we're on a SLURM cluster, so add resource requests
                script.write("#SBATCH -N 1\n")
                if 'tasks' in resources:
                    script.write(f"#SBATCH --ntasks={resources['tasks']}\n")
                if 'cores' in resources:
                    script.write(f"#SBATCH --cpus-per-task={resources['cores']}\n")
                if 'time' in resources:
                    script.write(f"#SBATCH --time={resources['time']}\n")
                if 'mem' in resources and (run.target.header_skip is None or '--mem' not in str(run.target.header_skip)):
                    script.write(f"#SBATCH --mem={resources['mem']}\n")
                if run.target.queue is not None and run.target.queue != '':
                    script.write(
                        f"#SBATCH --partition={run.target.gpu_queue if run.target.gpu and 'gpu' in flow['config'] and flow['config']['gpu'] else run.target.queue}\n")
                if run.target.project is not None and run.target.project != '':
                    script.write(f"#SBATCH -A {run.target.project}\n")
                script.write("#SBATCH --mail-type=END,FAIL\n")
                script.write(f"#SBATCH --mail-user={run.user.email}\n")
                script.write("#SBATCH --output=plantit.%j.out\n")
                script.write("#SBATCH --error=plantit.%j.err\n")

            # add precommands
            script.write(run.target.pre_commands + '\n')

            # if we have inputs, add pull command
            if 'input' in flow['config']:
                sftp.mkdir(join(run.target.workdir, run.work_dir, 'input'))
                pull_commands = f"plantit terrain pull {flow['config']['input']['from']}" \
                                f" -p {join(run.target.workdir, run.work_dir, 'input')}" \
                                f" {' '.join(['--pattern ' + pattern for pattern in flow['config']['input']['patterns']])}" \
                                f""f" --plantit_url '{callback_url}'" \
                                f""f" --plantit_token '{run.token}'" \
                                f""f" --terrain_token {run.user.profile.cyverse_token}\n"
                logger.info(f"Using pull command: {pull_commands}")
                script.write(pull_commands)

            # add run command
            run_commands = f"plantit run flow.yaml --plantit_url '{callback_url}' --plantit_token '{run.token}'"
            docker_username = environ.get('DOCKER_USERNAME', None)
            docker_password = environ.get('DOCKER_PASSWORD', None)
            if docker_username is not None and docker_password is not None:
                run_commands += f" --docker_username {docker_username} --docker_password {docker_password}"
            run_commands += "\n"
            logger.info(f"Using run command: {run_commands}")
            script.write(run_commands)

            # if we have outputs...
            if 'output' in flow:
                # add zip command...
                zip_commands = f"plantit zip {flow['output']['from']}"
                if 'include' in flow['output']:
                    if 'patterns' in flow['output']['include']:
                        zip_commands = zip_commands + ' '.join(['--include_pattern ' + pattern for pattern in flow['output']['include']['patterns']])
                    if 'names' in flow['output']['include']:
                        zip_commands = zip_commands + ' '.join(['--include_name ' + pattern for pattern in flow['output']['include']['names']])
                    if 'patterns' in flow['output']['exclude']:
                        zip_commands = zip_commands + ' '.join(['--exclude_pattern ' + pattern for pattern in flow['output']['exclude']['patterns']])
                    if 'names' in flow['output']['exclude']:
                        zip_commands = zip_commands + ' '.join(['--exclude_name ' + pattern for pattern in flow['output']['exclude']['names']])
                zip_commands += '\n'
                logger.info(f"Using zip command: {zip_commands}")

                # and add push command if we have a destination
                if 'to' in flow['output']:
                    push_commands = f"plantit terrain push {flow['output']['to']}" \
                                    f" -p {join(run.workdir, flow['output']['from'])}" \
                                    f" --plantit_url '{callback_url}'" \
                                    f" --plantit_token '{run.token}'" \
                                    f" --terrain_token {run.user.profile.cyverse_token}"
                    if 'include' in flow['output']:
                        if 'patterns' in flow['output']['include']:
                            push_commands = push_commands + ' '.join(
                                ['--include_pattern ' + pattern for pattern in flow['output']['include']['patterns']])
                        if 'names' in flow['output']['include']:
                            push_commands = push_commands + ' '.join(['--include_name ' + pattern for pattern in flow['output']['include']['names']])
                        if 'patterns' in flow['output']['exclude']:
                            push_commands = push_commands + ' '.join(
                                ['--exclude_pattern ' + pattern for pattern in flow['output']['exclude']['patterns']])
                        if 'names' in flow['output']['exclude']:
                            push_commands = push_commands + ' '.join(['--exclude_name ' + pattern for pattern in flow['output']['exclude']['names']])
                    push_commands += '\n'
                    script.write(push_commands)
                    logger.info(f"Using push command: {push_commands}")


def __submit(run, ssh):
    # TODO refactor to allow multiple cluster schedulers
    sandbox = run.target.name == 'Sandbox'  # for now, we're either in the sandbox or on a SLURM cluster
    template = environ.get('CELERY_TEMPLATE_LOCAL_RUN_SCRIPT') if sandbox else environ.get('CELERY_TEMPLATE_SLURM_RUN_SCRIPT')
    template_name = template.split('/')[-1]

    if run.is_sandbox:
        execute_command(
            ssh_client=ssh,
            pre_command='; '.join(str(run.target.pre_commands).splitlines()) if run.target.pre_commands else ':',
            command=f"chmod +x {template_name} && ./{template_name}" if sandbox else f"chmod +x {template_name} && sbatch {template_name}",
            directory=join(run.target.workdir, run.work_dir))
    else:
        job_id = execute_command(
            ssh_client=ssh,
            pre_command='; '.join(str(run.target.pre_commands).splitlines()) if run.target.pre_commands else ':',
            command=f"chmod +x {template_name} && ./{template_name}" if sandbox else f"chmod +x {template_name} && sbatch {template_name}",
            directory=join(run.target.workdir, run.work_dir))[-1].replace('Submitted batch job', '').strip()
        run.job_id = job_id
        run.save()


@app.task(bind=True, track_started=True)
def submit_run(self, username, flow):
    try:
        target = Target.objects.get(name=flow['config']['target']['name'])
        run = __create_run(username, flow, target, submit_run.request.id)
        log = f"Deploying run {submit_run.request.id} to {target.name}"
        logger.info(log)
        update_log(submit_run.request.id, log)

        # TODO orchestrate these steps independently within this task, rather than stringing them together in 1 script?
        ssh = SSH(run.target.hostname, run.target.port, run.target.username)
        with ssh:
            log = f"Creating working directory and uploading files"
            logger.info(log)
            self.update_state(state='UPLOADING', meta={'description': log})
            update_log(submit_run.request.id, log)
            __upload(flow, run, target, ssh)

            log = 'Running script' if run.target.name == 'Sandbox' else 'Submitting script to scheduler'
            logger.info(log)
            self.update_state(state='RUNNING', meta={'description': log})
            update_log(submit_run.request.id, log)
            __submit(run, ssh)

            if not run.is_sandbox:  # schedule a task to poll the cluster scheduler for job status
                poll_cluster_scheduler.s(run.submission_task_id).apply_async(countdown=0)
    except Exception:
        log = f"Failed to submit run {submit_run.request.id}: {traceback.format_exc()}."
        logger.error(log)
        update_log(submit_run.request.id, log)
        raise


def check_cluster_scheduler(run: Run) -> (str, str):
    ssh = SSH(run.target.hostname, run.target.port, run.target.username)
    with ssh:
        lines = execute_command(
            ssh_client=ssh,
            pre_command=":",
            command=f"squeue --me",
            directory=join(run.target.workdir, run.work_dir))

        job_line = next(l for l in lines if run.job_id in l)
        job_split = job_line.split()
        job_walltime = job_split[-3]
        job_status = job_split[-4]
        return job_status, job_walltime


@app.task()
def poll_cluster_scheduler(submission_task_id):
    task = AsyncResult(submission_task_id, app=app)
    run = Run.objects.get(submission_task_id=submission_task_id)
    logger.info(f"Checking {run.target.name} scheduler status for run {task.id} (SLURM job {run.job_id})")

    try:
        job_status, job_walltime = check_cluster_scheduler(run)
        print(f"Job {run.job_id} is {job_status} for {job_walltime}")
        run.job_status = job_status
        run.job_walltime = job_walltime
        run.save()
        poll_cluster_scheduler.s(submission_task_id).apply_async(countdown=int(environ.get('RUNS_REFRESH_SECONDS')))
    except StopIteration:
        print(f"Could not find {run.job_id}, not rescheduling poll")
        # cleanup.s(submit_run.request.id).apply_async(countdown=run.timeout)
        pass

    # TODO:
    # - if the run failed (check the cluster scheduler), delete everything in the run working directory but the log files

    # otherwise schedule a cleanup task to run after timeout elapses
    # cleanup.s(execute.request.id).apply_async(countdown=run.timeout)  # TODO adjustable period (1 day? 1 week?)


@app.task()
def cleanup(submission_task_id):
    task = AsyncResult(submission_task_id, app=app)
    run = Run.objects.get(submission_task_id=submission_task_id)

    logger.info(f"Cleaning up run {submission_task_id}")

    # TODO:
    # - if the run failed, delete everything in the run working directory but the log files
    # - if the run completed,

    # try:
    #     # if task is completed or failed, cancel its task
    #     if run.status != 0 and run.status != 6:
    #         app.control.terminate(task_id)
    #         logger.info(f"Timed out after {timedelta(seconds=run.timeout)}")
    #         update_status(run, Status.FAILED, f"Timed out after {timedelta(seconds=run.timeout)}")
    # except:
    #     logger.error(f"Cleanup failed: {traceback.format_exc()}")
    #     update_status(run, Status.FAILED, f"Cleanup failed: {traceback.format_exc()}")
