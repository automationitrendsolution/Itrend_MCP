"""Small helpers for creating test data."""

from django.contrib.auth import get_user_model

PASSWORD = "Correct-Horse-Battery-9"


def create_user(username: str = "alice", *, password: str = PASSWORD, **extra):
    return get_user_model().objects.create_user(username=username, password=password, **extra)
