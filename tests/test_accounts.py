from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from backend.accounts.forms import LOCKED_OUT_MESSAGE

from .factories import PASSWORD, create_user


class LoginTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = create_user()
        self.url = reverse("accounts:login")

    def login(self, password=PASSWORD, username="alice"):
        return self.client.post(self.url, {"username": username, "password": password})

    def test_login_page_renders(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sign in")

    def test_valid_login_redirects_to_dashboard(self):
        response = self.login()
        self.assertRedirects(response, reverse("dashboard:index"))
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_invalid_password_is_rejected(self):
        response = self.login(password="wrong")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertContains(response, "Please enter a correct username and password")

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.login()
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_authenticated_user_is_redirected_away_from_login(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(self.url), reverse("dashboard:index"))

    def test_external_next_url_is_ignored(self):
        response = self.client.post(
            self.url, {"username": "alice", "password": PASSWORD, "next": "https://evil.example.com/"}
        )
        self.assertRedirects(response, reverse("dashboard:index"))

    def test_login_requires_csrf_token(self):
        client = Client(enforce_csrf_checks=True)
        response = client.post(self.url, {"username": "alice", "password": PASSWORD})
        self.assertEqual(response.status_code, 403)

    def test_passwords_are_hashed(self):
        self.user.refresh_from_db()
        self.assertNotIn(PASSWORD, self.user.password)
        self.assertTrue(self.user.check_password(PASSWORD))


@override_settings(LOGIN_MAX_FAILED_ATTEMPTS=3, LOGIN_LOCKOUT_SECONDS=60)
class LoginLockoutTests(TestCase):
    def setUp(self):
        cache.clear()
        create_user()
        self.url = reverse("accounts:login")

    def login(self, password, username="alice"):
        return self.client.post(self.url, {"username": username, "password": password})

    def test_correct_password_refused_after_too_many_failures(self):
        for _ in range(3):
            self.login("wrong")
        response = self.login(PASSWORD)
        self.assertContains(response, LOCKED_OUT_MESSAGE)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_failures_below_limit_do_not_lock(self):
        for _ in range(2):
            self.login("wrong")
        self.assertRedirects(self.login(PASSWORD), reverse("dashboard:index"))

    def test_successful_login_resets_counter(self):
        for _ in range(2):
            self.login("wrong")
        self.login(PASSWORD)
        self.client.logout()
        for _ in range(2):
            self.login("wrong")
        self.assertRedirects(self.login(PASSWORD), reverse("dashboard:index"))

    def test_lockout_is_case_insensitive_on_username(self):
        for name in ("alice", "ALICE", "Alice"):
            self.login("wrong", username=name)
        self.assertContains(self.login(PASSWORD), LOCKED_OUT_MESSAGE)

    def test_lockout_of_one_ip_does_not_affect_another(self):
        for _ in range(3):
            self.login("wrong")
        other = Client(REMOTE_ADDR="10.0.0.99")
        response = other.post(self.url, {"username": "alice", "password": PASSWORD})
        self.assertRedirects(response, reverse("dashboard:index"))

    def test_ip_is_blocked_after_spraying_many_usernames(self):
        # Per-IP limit is 4x the per-account limit.
        for i in range(12):
            self.login("wrong", username=f"user{i}")
        self.assertContains(self.login(PASSWORD), LOCKED_OUT_MESSAGE)


class LogoutTests(TestCase):
    def setUp(self):
        self.user = create_user()
        self.client.force_login(self.user)
        self.url = reverse("accounts:logout")

    def test_logout_via_get_is_not_allowed(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)

    def test_logout_via_post_ends_session(self):
        response = self.client.post(self.url)
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)


class ProfileTests(TestCase):
    def test_requires_login(self):
        url = reverse("accounts:profile")
        self.assertRedirects(self.client.get(url), f"{reverse('accounts:login')}?next={url}")

    def test_shows_user_details(self):
        user = create_user(email="alice@example.com")
        self.client.force_login(user)
        response = self.client.get(reverse("accounts:profile"))
        self.assertContains(response, "alice@example.com")
