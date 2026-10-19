import time

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.views import PasswordResetConfirmView
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import services
from .forms import LoginForm, OTPForm, RegistrationForm
from .models import User

SESSION_USER = "pre_2fa_user_id"
SESSION_STARTED = "pre_2fa_started"
SESSION_NEXT = "pre_2fa_next"

GENERIC_LOGIN_ERROR = "Invalid email or password, or the account is temporarily locked."


def _clear_pending(request):
    for key in (SESSION_USER, SESSION_STARTED, SESSION_NEXT):
        request.session.pop(key, None)


def _pending_user(request):
    """The user who passed the password step but not yet the OTP step (or None)."""
    uid = request.session.get(SESSION_USER)
    started = request.session.get(SESSION_STARTED)
    if not uid or not started:
        return None
    if time.time() - started > settings.PRE_2FA_TTL_SECONDS:
        _clear_pending(request)
        return None
    return User.objects.filter(pk=uid, is_active=True).first()


def _safe_next(request, candidate):
    if candidate and url_has_allowed_host_and_scheme(candidate, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
        return candidate
    return None


def register(request):
    if request.user.is_authenticated:
        return redirect("catalog:book_list")
    form = RegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Account created. Sign in to continue.")
        return redirect("accounts:login")
    return render(request, "accounts/register.html", {"form": form})


@never_cache
def login_view(request):
    """Step 1 of 2: verify email + password. The user is NOT logged in until the OTP is verified."""
    if request.user.is_authenticated:
        return redirect("catalog:book_list")
    form = LoginForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        email = form.cleaned_data["email"].strip().lower()
        password = form.cleaned_data["password"]
        user = User.objects.filter(email__iexact=email).first()

        if user is None:
            User().set_password(password)  # burn comparable CPU time to blunt timing-based enumeration
            form.add_error(None, GENERIC_LOGIN_ERROR)
        elif user.is_locked():
            form.add_error(None, GENERIC_LOGIN_ERROR)  # never check the password while locked
        elif not user.is_active or not user.check_password(password):
            user.register_failed_login()
            form.add_error(None, GENERIC_LOGIN_ERROR)
        else:
            request.session.cycle_key()  # new session id at privilege boundary
            request.session[SESSION_USER] = user.pk
            request.session[SESSION_STARTED] = time.time()
            nxt = _safe_next(request, request.POST.get("next") or request.GET.get("next"))
            if nxt:
                request.session[SESSION_NEXT] = nxt
            try:
                services.issue_otp(user)
            except services.OTPThrottled:
                pass  # a valid code was sent moments ago; let the user enter it
            messages.info(request, "We emailed you a 6-digit code.")
            return redirect("accounts:verify_otp")
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@never_cache
def verify_otp(request):
    """Step 2 of 2: verify the emailed code, then complete the login."""
    user = _pending_user(request)
    if user is None:
        messages.error(request, "Your sign-in session expired. Please start again.")
        return redirect("accounts:login")

    form = OTPForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        result = services.verify_otp(user, form.cleaned_data["code"])
        if result == services.OK:
            nxt = request.session.get(SESSION_NEXT)
            _clear_pending(request)
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            user.reset_login_failures()
            return redirect(nxt or settings.LOGIN_REDIRECT_URL)
        if result == services.TOO_MANY:
            user.register_failed_login()
            _clear_pending(request)
            messages.error(request, "Too many incorrect codes. Sign in again to get a new code.")
            return redirect("accounts:login")
        if result in (services.EXPIRED, services.NO_CODE):
            form.add_error(None, "That code has expired. Request a new one below.")
        else:
            form.add_error(None, "Incorrect code. Check the latest email and try again.")
    return render(request, "accounts/verify_otp.html", {"form": form, "email": user.email})


@require_POST
def resend_otp(request):
    user = _pending_user(request)
    if user is None:
        messages.error(request, "Your sign-in session expired. Please start again.")
        return redirect("accounts:login")
    try:
        services.issue_otp(user)
        messages.success(request, "A new code is on its way.")
    except services.OTPThrottled as exc:
        messages.warning(request, f"Please wait {exc.retry_after} seconds before requesting another code.")
    return redirect("accounts:verify_otp")


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


class UnlockingPasswordResetConfirmView(PasswordResetConfirmView):
    """After a successful reset: clear lockout and kill any outstanding OTPs."""

    def form_valid(self, form):
        response = super().form_valid(form)
        user = form.user
        user.reset_login_failures()
        user.otps.filter(consumed_at__isnull=True).update(consumed_at=timezone.now())
        return response
