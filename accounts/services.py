"""Email one-time-password (second factor) logic."""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from .models import EmailOTP

OK = "ok"
INVALID = "invalid"
EXPIRED = "expired"
NO_CODE = "no_code"
TOO_MANY = "too_many"


class OTPThrottled(Exception):
    def __init__(self, retry_after):
        super().__init__(f"Wait {retry_after}s before requesting another code.")
        self.retry_after = retry_after


def _hash_code(user_id, code):
    """HMAC-SHA256 keyed with SECRET_KEY, bound to the user, so a DB leak does not expose codes."""
    msg = f"{user_id}:{code}".encode()
    return hmac.new(settings.SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


def generate_code():
    return "".join(str(secrets.randbelow(10)) for _ in range(settings.OTP_LENGTH))  # CSPRNG


def issue_otp(user):
    """Create + email a fresh code, invalidating older ones. Raises OTPThrottled if requested too fast."""
    now = timezone.now()
    last = user.otps.order_by("-created_at").first()
    if last:
        wait = settings.OTP_RESEND_COOLDOWN_SECONDS - (now - last.created_at).total_seconds()
        if wait > 0:
            raise OTPThrottled(int(wait) + 1)

    user.otps.filter(consumed_at__isnull=True).update(consumed_at=now)
    code = generate_code()
    otp = EmailOTP.objects.create(
        user=user,
        code_hash=_hash_code(user.pk, code),
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
    )
    minutes = settings.OTP_TTL_SECONDS // 60
    send_mail(
        subject="Your library sign-in code",
        message=(
            f"Hello {user.get_short_name()},\n\n"
            f"Your sign-in code is: {code}\n\n"
            f"It expires in {minutes} minutes and can be used once.\n"
            "If you did not try to sign in, change your password right away."
        ),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
    return otp


def verify_otp(user, submitted):
    """Check a submitted code. Single-use, expiring, attempt-limited, constant-time comparison."""
    now = timezone.now()
    submitted = (submitted or "").strip()
    with transaction.atomic():
        otp = (
            user.otps.select_for_update()
            .filter(consumed_at__isnull=True)
            .order_by("-created_at")
            .first()
        )
        if otp is None:
            return NO_CODE
        if otp.expires_at <= now:
            otp.consumed_at = now
            otp.save(update_fields=["consumed_at"])
            return EXPIRED
        if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            otp.consumed_at = now
            otp.save(update_fields=["consumed_at"])
            return TOO_MANY

        otp.attempts += 1
        if hmac.compare_digest(otp.code_hash, _hash_code(user.pk, submitted)):
            otp.consumed_at = now
            otp.save(update_fields=["attempts", "consumed_at"])
            return OK

        result = INVALID
        if otp.attempts >= settings.OTP_MAX_ATTEMPTS:
            otp.consumed_at = now
            result = TOO_MANY
        otp.save(update_fields=["attempts", "consumed_at"])
        return result
