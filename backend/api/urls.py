"""Version 1 of the REST API, mounted at /api/v1/."""

from django.urls import path

from . import views

app_name = "api"

urlpatterns = [
    path("health/", views.HealthView.as_view(), name="health"),
]
