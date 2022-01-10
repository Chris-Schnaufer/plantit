from django.urls import path

from . import views

urlpatterns = [
    path(r'counts/', views.counts),
    path(r'institutions/', views.institutions),
    path(r'timeseries/', views.timeseries),
    path(r'workflow_timeseries/<owner>/<name>/<branch>/', views.workflow_timeseries)
]
