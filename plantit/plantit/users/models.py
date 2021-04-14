from django.contrib.auth.models import User
from django.db import models

from plantit.clusters.models import Cluster


class Profile(models.Model):
    user: User = models.OneToOneField(User, on_delete=models.CASCADE)
    github_username: str = models.CharField(max_length=255, blank=True, default='')
    github_token: str = models.CharField(max_length=500, blank=True, default='')
    cyverse_token: str = models.CharField(max_length=1500, blank=True, default='')
    dark_mode: bool = models.BooleanField(default=False)
    interactive_mode = models.ForeignKey(Cluster, null=True, blank=True, on_delete=models.PROTECT)
    push_notification_status = models.CharField(max_length=10, null=False, blank=False, default='disabled')
    push_notification_topic_arn = models.CharField(max_length=255, null=True, blank=True)
    push_notification_sub_arn = models.CharField(max_length=255, null=True, blank=True)
