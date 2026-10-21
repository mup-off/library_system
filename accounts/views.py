from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.views import PasswordResetConfirmView
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from .forms import LoginForm, RegistrationForm
from .models import User

GENERIC_LOGIN_ERROR = "Invalid email or password, or the account is temporarily locked."


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
    """Authenticate members with their email and password."""
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
            nxt = _safe_next(request, request.POST.get("next") or request.GET.get("next"))
            request.session.cycle_key()
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            user.reset_login_failures()
            return redirect(nxt or settings.LOGIN_REDIRECT_URL)
    return render(request, "accounts/login.html", {"form": form, "next": request.GET.get("next", "")})


@require_POST
def logout_view(request):
    logout(request)
    messages.info(request, "You have been signed out.")
    return redirect("accounts:login")


class UnlockingPasswordResetConfirmView(PasswordResetConfirmView):
    """After a successful reset, clear any active login lockout."""

    def form_valid(self, form):
        response = super().form_valid(form)
        form.user.reset_login_failures()
        return response
