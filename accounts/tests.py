"""Authentication tests for password login, lockout, reset, and sessions."""
import re
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import User

PASSWORD = "Correct-Horse-Battery-9"


def make_user(email="reader@example.com", **kw):
    return User.objects.create_user(email=email, password=PASSWORD, full_name="Rita Reader", **kw)


class PasswordStorageTests(TestCase):
    def test_password_is_hashed_not_plaintext(self):
        user = make_user()
        self.assertNotIn(PASSWORD, user.password)
        self.assertTrue(user.password.startswith("pbkdf2_sha256$"))
        self.assertTrue(user.check_password(PASSWORD))

    def test_weak_passwords_rejected_at_registration(self):
        for weak in ["short1", "password123", "1234567890", "reader@example.com"]:
            response = self.client.post(reverse("accounts:register"), {
                "full_name": "X Y", "email": "new@example.com", "password1": weak, "password2": weak,
            })
            self.assertEqual(response.status_code, 200, weak)
            self.assertFalse(User.objects.filter(email="new@example.com").exists(), weak)

    def test_registration_ok_and_email_case_insensitive_unique(self):
        response = self.client.post(reverse("accounts:register"), {
            "full_name": "A B", "email": "Mixed@Example.com", "password1": PASSWORD, "password2": PASSWORD,
        })
        self.assertRedirects(response, reverse("accounts:login"))
        self.assertTrue(User.objects.filter(email="mixed@example.com").exists())
        response = self.client.post(reverse("accounts:register"), {
            "full_name": "A B", "email": "MIXED@example.com", "password1": PASSWORD, "password2": PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)


class PasswordLoginTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.url = reverse("accounts:login")

    def login(self, client=None, **extra):
        return (client or self.client).post(self.url, {
            "email": "reader@example.com", "password": PASSWORD, **extra,
        })

    def test_valid_password_logs_in_directly_without_email_code(self):
        response = self.login()
        self.assertRedirects(response, reverse("catalog:book_list"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertEqual(mail.outbox, [])

    def test_session_key_rotates_at_login(self):
        self.client.get(self.url)
        before = self.client.session.session_key
        self.login()
        self.assertNotEqual(before, self.client.session.session_key)

    def test_next_param_open_redirect_blocked(self):
        response = self.login(next="https://evil.example/steal")
        self.assertRedirects(response, reverse("catalog:book_list"), fetch_redirect_response=False)

    def test_next_param_local_path_honoured(self):
        response = self.login(next="/loans/")
        self.assertEqual(response["Location"], "/loans/")

    def test_removed_code_verification_endpoints_are_not_available(self):
        self.assertEqual(self.client.get("/accounts/verify/").status_code, 404)
        self.assertEqual(self.client.post("/accounts/verify/resend/").status_code, 404)


class LockoutAndEnumerationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.url = reverse("accounts:login")

    def test_same_error_for_unknown_email_and_wrong_password(self):
        unknown = self.client.post(self.url, {"email": "nobody@example.com", "password": "whatever-123"})
        wrong = self.client.post(self.url, {"email": "reader@example.com", "password": "whatever-123"})
        self.assertEqual(unknown.status_code, 200)
        self.assertEqual(wrong.status_code, 200)
        self.assertEqual(list(unknown.context["form"].non_field_errors()), list(wrong.context["form"].non_field_errors()))

    def test_account_locks_after_repeated_failures(self):
        for _ in range(settings.LOGIN_MAX_FAILURES):
            self.client.post(self.url, {"email": "reader@example.com", "password": "bad-password-1"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_locked())
        response = self.client.post(self.url, {"email": "reader@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])

    def test_lock_expires_and_success_resets_failures(self):
        self.user.locked_until = timezone.now() - timedelta(seconds=1)
        self.user.failed_login_attempts = 3
        self.user.save()
        response = self.client.post(self.url, {"email": "reader@example.com", "password": PASSWORD})
        self.assertRedirects(response, reverse("catalog:book_list"), fetch_redirect_response=False)
        self.user.refresh_from_db()
        self.assertEqual(self.user.failed_login_attempts, 0)
        self.assertIsNone(self.user.locked_until)

    def test_inactive_user_cannot_sign_in(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(self.url, {"email": "reader@example.com", "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_is_case_insensitive_on_email(self):
        response = self.client.post(self.url, {"email": "READER@Example.COM", "password": PASSWORD})
        self.assertRedirects(response, reverse("catalog:book_list"), fetch_redirect_response=False)

    def test_sql_injection_string_is_just_a_bad_login(self):
        response = self.client.post(self.url, {"email": "a@b.co' OR '1'='1", "password": "x"})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_reset_email_sent_for_known_user_and_same_response_for_unknown(self):
        known = self.client.post(reverse("accounts:password_reset"), {"email": "reader@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        unknown = self.client.post(reverse("accounts:password_reset"), {"email": "ghost@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(known["Location"], unknown["Location"])

    def _link(self):
        self.client.post(reverse("accounts:password_reset"), {"email": "reader@example.com"})
        return re.search(r"https?://testserver(\S+)", mail.outbox[-1].body).group(1)

    def test_full_reset_flow_unlocks_account_and_new_password_works(self):
        self.user.locked_until = timezone.now() + timedelta(minutes=10)
        self.user.save()
        response = self.client.get(self._link(), follow=True)
        post_url = response.redirect_chain[-1][0]
        new_password = "Brand-New-Passphrase-42"
        response = self.client.post(post_url, {"new_password1": new_password, "new_password2": new_password})
        self.assertRedirects(response, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_locked())
        self.assertTrue(self.user.check_password(new_password))
        self.assertFalse(self.user.check_password(PASSWORD))

    def test_reset_link_is_single_use(self):
        link = self._link()
        response = self.client.get(link, follow=True)
        post_url = response.redirect_chain[-1][0]
        new_password = "Another-Strong-Pass-77"
        self.client.post(post_url, {"new_password1": new_password, "new_password2": new_password})
        response = Client().get(link, follow=True)
        self.assertFalse(response.context["validlink"])

    def test_reset_rejects_weak_password(self):
        link = self._link()
        post_url = self.client.get(link, follow=True).redirect_chain[-1][0]
        response = self.client.post(post_url, {"new_password1": "12345678", "new_password2": "12345678"})
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_tampered_token_rejected(self):
        link = self._link()
        bad_link = link[:-3] + ("aaa" if not link.endswith("aaa") else "bbb")
        response = Client().get(bad_link, follow=True)
        self.assertFalse(response.context["validlink"])

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_token_rejected(self):
        response = Client().get(self._link(), follow=True)
        self.assertFalse(response.context["validlink"])

    def test_reset_signs_out_existing_sessions(self):
        existing_client = Client()
        existing_client.force_login(self.user)
        self.assertEqual(existing_client.get(reverse("catalog:my_loans")).status_code, 200)
        response = self.client.get(self._link(), follow=True)
        post_url = response.redirect_chain[-1][0]
        new_password = "Yet-Another-Strong-Pass-5"
        self.client.post(post_url, {"new_password1": new_password, "new_password2": new_password})
        self.assertEqual(existing_client.get(reverse("catalog:my_loans")).status_code, 302)


class HttpHardeningTests(TestCase):
    def test_csrf_enforced_on_login_and_logout(self):
        make_user()
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.post(reverse("accounts:login"), {
            "email": "reader@example.com", "password": PASSWORD,
        }).status_code, 403)
        self.assertEqual(client.post(reverse("accounts:logout")).status_code, 403)

    def test_security_headers_present(self):
        response = self.client.get(reverse("accounts:login"))
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Referrer-Policy"], "same-origin")
        self.assertIn("no-store", response["Cache-Control"])

    def test_cookie_flags(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")

    def test_logout_requires_post_and_ends_session(self):
        user = make_user()
        client = Client()
        client.force_login(user)
        self.assertEqual(client.get(reverse("accounts:logout")).status_code, 405)
        self.assertIn("_auth_user_id", client.session)
        client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", client.session)

    def test_admin_requires_staff(self):
        user = make_user()
        client = Client()
        client.force_login(user)
        response = client.get("/admin/")
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])
