from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.index, name="index"),
    path("dashboard/system-status/", views.system_status, name="system-status"),
]
