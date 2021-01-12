import requests
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse

from plantit.utils import get_repo_config, validate_config


@login_required
def list_all(request):
    response = requests.get(
        f"https://api.github.com/search/code?q=filename:plantit.yaml-user:Computational-Plant-Science-user:van-der-knaap-lab-user:burke-lab",
        headers={"Authorization": f"token {request.user.profile.github_token}"})
    pipelines = [{
        'repo': item['repository'],
        'config': get_repo_config(item['repository']['name'], item['repository']['owner']['login'], request.user.profile.github_token)
    } for item in response.json()['items']]

    return JsonResponse({'pipelines': [pipeline for pipeline in pipelines if pipeline['config']['public']]})


@login_required
def list(request, username):
    response = requests.get(
        f"https://api.github.com/search/code?q=filename:plantit.yaml+user:{username}",
        headers={
            "Authorization": f"token {request.user.profile.github_token}",
            "Accept": "application/vnd.github.mercy-preview+json"  # so repo topics will be returned
        })
    pipelines = [{
        'repo': item['repository'],
        'config': get_repo_config(item['repository']['name'], item['repository']['owner']['login'], request.user.profile.github_token)
    } for item in response.json()['items']]

    return JsonResponse({'pipelines': [pipeline for pipeline in pipelines if pipeline['config']['public']]})


@login_required
def get(request, username, name):
    repo = requests.get(f"https://api.github.com/repos/{username}/{name}",
                        headers={
                            "Authorization": f"token {request.user.profile.github_token}",
                            "Accept": "application/vnd.github.mercy-preview+json"  # so repo topics will be returned
                        }).json()

    return JsonResponse({
        'repo': repo,
        'config': get_repo_config(repo['name'], repo['owner']['login'], request.user.profile.github_token)
    })


@login_required
def validate(request, username, name):
    repo = requests.get(f"https://api.github.com/repos/{username}/{name}",
                        headers={"Authorization": f"token {request.user.profile.github_token}"}).json()
    config = get_repo_config(repo['name'], repo['owner']['login'], request.user.profile.github_token)
    result = validate_config(config, request.user.profile.cyverse_token)
    if isinstance(result, bool):
        return JsonResponse({'result': result})
    else:
        return JsonResponse({'result': result[0], 'errors': result[1]})
