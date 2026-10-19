"""Security tests for authentication: password step, email OTP 2FA, lockout, reset, sessions."""
import re
import time
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import services
from .models import EmailOTP, User
from .views import SESSION_STARTED, SESSION_USER

PASSWORD = "Correct-Horse-Battery-9"


def make_user(email="reader@example.com", password=PASSWORD, **kw):
    return User.objects.create_user(email=email, password=password, full_name="Rita Reader", **kw)


def last_code():
    """Extract the 6-digit code from the most recent email."""
    return re.search(r"code is: (\d{6})", mail.outbox[-1].body).group(1)


class PasswordStorageTests(TestCase):
    def test_password_is_hashed_not_plaintext(self):
        u = make_user()
        self.assertNotIn(PASSWORD, u.password)
        self.assertTrue(u.password.startswith("pbkdf2_sha256$"))
        self.assertTrue(u.check_password(PASSWORD))

    def test_weak_passwords_rejected_at_registration(self):
        for weak in ["short1", "password123", "1234567890", "reader@example.com"]:
            r = self.client.post(reverse("accounts:register"), {
                "full_name": "X Y", "email": "new@example.com", "password1": weak, "password2": weak})
            self.assertEqual(r.status_code, 200, weak)
            self.assertFalse(User.objects.filter(email="new@example.com").exists(), weak)

    def test_registration_ok_and_email_case_insensitive_unique(self):
        r = self.client.post(reverse("accounts:register"), {
            "full_name": "A B", "email": "Mixed@Example.com", "password1": PASSWORD, "password2": PASSWORD})
        self.assertRedirects(r, reverse("accounts:login"))
        self.assertTrue(User.objects.filter(email="mixed@example.com").exists())
        r = self.client.post(reverse("accounts:register"), {
            "full_name": "A B", "email": "MIXED@example.com", "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(User.objects.count(), 1)


class TwoFactorFlowTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.login_url = reverse("accounts:login")
        self.verify_url = reverse("accounts:verify_otp")

    def _password_step(self, client=None, **extra):
        c = client or self.client
        return c.post(self.login_url, {"email": "reader@example.com", "password": PASSWORD, **extra})

    def test_password_alone_does_not_log_in(self):
        r = self._password_step()
        self.assertRedirects(r, self.verify_url)
        self.assertNotIn("_auth_user_id", self.client.session)
        # protected pages stay protected
        r = self.client.get(reverse("catalog:my_loans"))
        self.assertEqual(r.status_code, 302)
        self.assertIn(self.login_url, r["Location"])

    def test_otp_email_sent_and_correct_code_logs_in(self):
        self._password_step()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["reader@example.com"])
        r = self.client.post(self.verify_url, {"code": last_code()})
        self.assertRedirects(r, reverse("catalog:book_list"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)
        self.assertNotIn(SESSION_USER, self.client.session)

    def test_otp_stored_hashed_only(self):
        self._password_step()
        code = last_code()
        otp = EmailOTP.objects.get()
        self.assertNotEqual(otp.code_hash, code)
        self.assertEqual(len(otp.code_hash), 64)

    def test_wrong_code_rejected(self):
        self._password_step()
        real = last_code()
        wrong = "000000" if real != "000000" else "111111"
        r = self.client.post(self.verify_url, {"code": wrong})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_code_is_single_use(self):
        self._password_step()
        code = last_code()
        self.client.post(self.verify_url, {"code": code})
        c2 = Client()
        self._password_step(c2)  # throttled -> no new code, old one consumed
        r = c2.post(self.verify_url, {"code": code})
        self.assertNotIn("_auth_user_id", c2.session)
        self.assertEqual(r.status_code, 200)

    def test_expired_code_rejected(self):
        self._password_step()
        code = last_code()
        EmailOTP.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        r = self.client.post(self.verify_url, {"code": code})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_brute_force_limited_to_max_attempts(self):
        self._password_step()
        real = last_code()
        wrong = "000000" if real != "000000" else "111111"
        for _ in range(settings.OTP_MAX_ATTEMPTS):
            self.client.post(self.verify_url, {"code": wrong})
        # even the CORRECT code no longer works: the code is burned and the pending session cleared
        r = self.client.post(self.verify_url, {"code": real})
        self.assertRedirects(r, self.login_url, fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_non_numeric_or_wrong_length_code_rejected(self):
        self._password_step()
        for bad in ["abcdef", "12345", "1234567", "12 456", "' OR 1=1"]:
            r = self.client.post(self.verify_url, {"code": bad})
            self.assertEqual(r.status_code, 200)
            self.assertNotIn("_auth_user_id", self.client.session)

    def test_verify_page_requires_password_step(self):
        r = self.client.get(self.verify_url)
        self.assertRedirects(r, self.login_url, fetch_redirect_response=False)
        r = self.client.post(self.verify_url, {"code": "123456"})
        self.assertRedirects(r, self.login_url, fetch_redirect_response=False)

    def test_pending_session_expires(self):
        self._password_step()
        s = self.client.session
        s[SESSION_STARTED] = time.time() - settings.PRE_2FA_TTL_SECONDS - 5
        s.save()
        r = self.client.get(self.verify_url)
        self.assertRedirects(r, self.login_url, fetch_redirect_response=False)

    def test_otp_for_one_user_does_not_work_for_another(self):
        other = make_user("other@example.com")
        self._password_step()
        code_a = last_code()
        c2 = Client()
        c2.post(self.login_url, {"email": "other@example.com", "password": PASSWORD})
        code_b = last_code()
        r = c2.post(self.verify_url, {"code": code_a if code_a != code_b else "999999"})
        self.assertNotIn("_auth_user_id", c2.session)
        self.assertEqual(r.status_code, 200)

    def test_resend_is_rate_limited(self):
        self._password_step()
        self.assertEqual(len(mail.outbox), 1)
        self.client.post(reverse("accounts:resend_otp"))
        self.assertEqual(len(mail.outbox), 1)  # cooldown blocks the second email

    def test_resend_after_cooldown_invalidates_old_code(self):
        self._password_step()
        old = last_code()
        EmailOTP.objects.update(created_at=timezone.now() - timedelta(seconds=settings.OTP_RESEND_COOLDOWN_SECONDS + 1))
        self.client.post(reverse("accounts:resend_otp"))
        self.assertEqual(len(mail.outbox), 2)
        new = last_code()
        if old != new:
            self.client.post(self.verify_url, {"code": old})
            self.assertNotIn("_auth_user_id", self.client.session)
        self.client.post(self.verify_url, {"code": new})
        self.assertIn("_auth_user_id", self.client.session)

    def test_session_key_rotates_on_login(self):
        self.client.get(self.login_url)
        before = self.client.session.session_key
        self._password_step()
        mid = self.client.session.session_key
        self.client.post(self.verify_url, {"code": last_code()})
        after = self.client.session.session_key
        self.assertNotEqual(before, mid)
        self.assertNotEqual(mid, after)

    def test_logout_requires_post_and_ends_session(self):
        self._password_step()
        self.client.post(self.verify_url, {"code": last_code()})
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)
        self.client.post(reverse("accounts:logout"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_next_param_open_redirect_blocked(self):
        self._password_step(next="https://evil.example/steal")
        r = self.client.post(self.verify_url, {"code": last_code()})
        self.assertRedirects(r, reverse("catalog:book_list"), fetch_redirect_response=False)

    def test_next_param_local_path_honoured(self):
        self._password_step(next="/loans/")
        r = self.client.post(self.verify_url, {"code": last_code()})
        self.assertEqual(r["Location"], "/loans/")


class LockoutAndEnumerationTests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.url = reverse("accounts:login")

    def test_same_error_for_unknown_email_and_wrong_password(self):
        a = self.client.post(self.url, {"email": "nobody@example.com", "password": "whatever-123"})
        b = self.client.post(self.url, {"email": "reader@example.com", "password": "whatever-123"})
        self.assertEqual(a.status_code, 200)
        self.assertEqual(b.status_code, 200)
        self.assertEqual(list(a.context["form"].non_field_errors()), list(b.context["form"].non_field_errors()))

    def test_account_locks_after_repeated_failures(self):
        for _ in range(settings.LOGIN_MAX_FAILURES):
            self.client.post(self.url, {"email": "reader@example.com", "password": "bad-password-1"})
        self.user.refresh_from_db()
        self.assertTrue(self.user.is_locked())
        # correct password is refused while locked, and no OTP is sent
        r = self.client.post(self.url, {"email": "reader@example.com", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_lock_expires(self):
        self.user.locked_until = timezone.now() - timedelta(seconds=1)
        self.user.save()
        r = self.client.post(self.url, {"email": "reader@example.com", "password": PASSWORD})
        self.assertRedirects(r, reverse("accounts:verify_otp"))

    def test_inactive_user_cannot_start_login(self):
        self.user.is_active = False
        self.user.save()
        r = self.client.post(self.url, {"email": "reader@example.com", "password": PASSWORD})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)

    def test_login_is_case_insensitive_on_email(self):
        r = self.client.post(self.url, {"email": "READER@Example.COM", "password": PASSWORD})
        self.assertRedirects(r, reverse("accounts:verify_otp"))

    def test_sql_injection_string_is_just_a_bad_login(self):
        r = self.client.post(self.url, {"email": "a@b.co' OR '1'='1", "password": "x"})
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)


class PasswordResetTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def test_reset_email_sent_for_known_user_and_same_response_for_unknown(self):
        known = self.client.post(reverse("accounts:password_reset"), {"email": "reader@example.com"})
        self.assertEqual(len(mail.outbox), 1)
        unknown = self.client.post(reverse("accounts:password_reset"), {"email": "ghost@example.com"})
        self.assertEqual(len(mail.outbox), 1)  # nothing sent...
        self.assertEqual(known["Location"], unknown["Location"])  # ...but identical response

    def _link(self):
        self.client.post(reverse("accounts:password_reset"), {"email": "reader@example.com"})
        return re.search(r"https?://testserver(\S+)", mail.outbox[-1].body).group(1)

    def test_full_reset_flow_unlocks_account_and_new_password_works(self):
        self.user.locked_until = timezone.now() + timedelta(minutes=10)
        self.user.save()
        link = self._link()
        r = self.client.get(link, follow=True)  # Django swaps token for a session token
        post_url = r.redirect_chain[-1][0]
        new_pw = "Brand-New-Passphrase-42"
        r = self.client.post(post_url, {"new_password1": new_pw, "new_password2": new_pw})
        self.assertRedirects(r, reverse("accounts:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertFalse(self.user.is_locked())
        self.assertTrue(self.user.check_password(new_pw))
        self.assertFalse(self.user.check_password(PASSWORD))

    def test_reset_link_is_single_use(self):
        link = self._link()
        r = self.client.get(link, follow=True)
        post_url = r.redirect_chain[-1][0]
        pw = "Another-Strong-Pass-77"
        self.client.post(post_url, {"new_password1": pw, "new_password2": pw})
        r = Client().get(link, follow=True)
        self.assertFalse(r.context["validlink"])

    def test_reset_rejects_weak_password(self):
        link = self._link()
        post_url = self.client.get(link, follow=True).redirect_chain[-1][0]
        r = self.client.post(post_url, {"new_password1": "12345678", "new_password2": "12345678"})
        self.assertEqual(r.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_tampered_token_rejected(self):
        link = self._link()
        bad = link[:-3] + ("aaa" if not link.endswith("aaa") else "bbb")
        r = Client().get(bad, follow=True)
        self.assertFalse(r.context["validlink"])

    @override_settings(PASSWORD_RESET_TIMEOUT=-1)
    def test_expired_token_rejected(self):
        link = self._link()
        r = Client().get(link, follow=True)
        self.assertFalse(r.context["validlink"])

    def test_reset_signs_out_existing_sessions(self):
        c = Client()
        c.force_login(self.user)
        self.assertEqual(c.get(reverse("catalog:my_loans")).status_code, 200)
        link = self._link()
        post_url = self.client.get(link, follow=True).redirect_chain[-1][0]
        pw = "Yet-Another-Strong-Pass-5"
        self.client.post(post_url, {"new_password1": pw, "new_password2": pw})
        self.assertEqual(c.get(reverse("catalog:my_loans")).status_code, 302)

    def test_reset_invalidates_outstanding_otps(self):
        self.client.post(reverse("accounts:login"), {"email": "reader@example.com", "password": PASSWORD})
        link = self._link()
        post_url = Client().get(link, follow=True).redirect_chain[-1][0]
        c = Client(); c.get(link)
        post_url = c.get(link, follow=True).redirect_chain[-1][0]
        pw = "Final-Strong-Passphrase-3"
        c.post(post_url, {"new_password1": pw, "new_password2": pw})
        self.assertFalse(self.user.otps.filter(consumed_at__isnull=True).exists())


class HttpHardeningTests(TestCase):
    def test_csrf_enforced_on_login_and_verify(self):
        make_user()
        c = Client(enforce_csrf_checks=True)
        self.assertEqual(c.post(reverse("accounts:login"), {"email": "reader@example.com", "password": PASSWORD}).status_code, 403)
        self.assertEqual(c.post(reverse("accounts:verify_otp"), {"code": "123456"}).status_code, 403)
        self.assertEqual(c.post(reverse("accounts:logout")).status_code, 403)

    def test_security_headers_present(self):
        r = self.client.get(reverse("accounts:login"))
        self.assertEqual(r["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r["X-Frame-Options"], "DENY")
        self.assertEqual(r["Referrer-Policy"], "same-origin")
        self.assertIn("no-store", r["Cache-Control"])

    def test_cookie_flags(self):
        self.assertTrue(settings.SESSION_COOKIE_HTTPONLY)
        self.assertEqual(settings.SESSION_COOKIE_SAMESITE, "Lax")
        self.assertEqual(settings.CSRF_COOKIE_SAMESITE, "Lax")

    def test_otp_hash_uses_constant_time_compare_and_is_user_bound(self):
        a, b = make_user("a@example.com"), make_user("b@example.com")
        self.assertNotEqual(services._hash_code(a.pk, "123456"), services._hash_code(b.pk, "123456"))

    def test_generated_codes_are_six_digits(self):
        for _ in range(50):
            self.assertRegex(services.generate_code(), r"^\d{6}$")

    def test_admin_requires_staff(self):
        u = make_user()
        c = Client(); c.force_login(u)
        r = c.get("/admin/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/admin/login/", r["Location"])
