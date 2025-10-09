from django.contrib.auth.models import User
from django.contrib.auth import login, logout
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.decorators.csrf import csrf_protect
from django.db.models import Q

from .forms import RequestOtpForm, VerifyOtpForm
from .models import OtpToken
from .utils import generate_otp_code, hash_code, default_expiry, send_login_otp

# Throttling constants
MAX_ACTIVE_TOKENS_PER_EMAIL = 3
MAX_SENDS_PER_DAY_PER_EMAIL = 10
RESEND_COOLDOWN_SECONDS = 60

def _today_range():
    now = timezone.now()
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timezone.timedelta(days=1)
    return start, end

@csrf_protect
def login_page(request):
    # Render your theme login page (with email + otp blocks)
    return render(request, "accounts/login.html", {})

@require_POST
@csrf_protect
def request_otp(request):
    """
    Send OTP ONLY if the user already exists.
    If not, return: "No account found with this email address."
    """
    form = RequestOtpForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)

    email = form.cleaned_data["email"].lower().strip()
    ip = request.META.get("REMOTE_ADDR")

    # ✅ Check existing user (no auto-create)
    user = User.objects.filter(Q(email__iexact=email)).first()
    if not user:
        return JsonResponse(
            {"ok": False, "message": "No account found with this email address."},
            status=400
        )

    # per-day throttle
    start, end = _today_range()
    todays_sends = OtpToken.objects.filter(
        email=email,
        created_at__gte=start,
        created_at__lt=end
    ).count()
    if todays_sends >= MAX_SENDS_PER_DAY_PER_EMAIL:
        return JsonResponse({"ok": False, "message": "Daily OTP limit reached. Try tomorrow."}, status=429)

    # active tokens limit & cooldown
    active = OtpToken.objects.filter(
        email=email, purpose=OtpToken.PURPOSE_LOGIN, is_used=False, expires_at__gt=timezone.now()
    ).order_by("-created_at")

    if active.exists():
        latest = active.first()
        if (timezone.now() - latest.last_sent_at).total_seconds() < RESEND_COOLDOWN_SECONDS:
            # Quiet success to avoid leaking existence repeatedly
            return JsonResponse({"ok": True, "message": "OTP already sent. Please check your email."})

    if active.count() >= MAX_ACTIVE_TOKENS_PER_EMAIL:
        to_expire = active[MAX_ACTIVE_TOKENS_PER_EMAIL-1:]
        for t in to_expire:
            t.expires_at = timezone.now()
            t.save(update_fields=["expires_at"])

    # Issue a fresh OTP
    code = generate_otp_code()
    token = OtpToken.objects.create(
        email=email,
        code_hash=hash_code(code),
        expires_at=default_expiry(),
        requester_ip=ip
    )
    send_login_otp(email, code)
    token.last_sent_at = timezone.now()
    token.save(update_fields=["last_sent_at"])
    return JsonResponse({"ok": True, "message": "OTP sent to your email."})

@require_POST
@csrf_protect
def verify_otp(request):
    """
    Verify OTP and log in the existing user.
    No auto-create. If user not found (edge), error.
    """
    form = VerifyOtpForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors}, status=400)

    email = form.cleaned_data["email"].lower().strip()
    code = form.cleaned_data["code"].strip()
    code_h = hash_code(code)

    # Must belong to an existing user
    user = User.objects.filter(Q(email__iexact=email)).first()
    if not user:
        # This can happen if someone jumps straight to verify without a prior request
        return JsonResponse({"ok": False, "message": "No account found with this email address."}, status=400)

    # Find a matching, valid token
    qs = OtpToken.objects.filter(
        email=email,
        purpose=OtpToken.PURPOSE_LOGIN,
        is_used=False,
        expires_at__gt=timezone.now()
    ).order_by("-created_at")

    token = None
    for t in qs[:5]:
        if t.code_hash == code_h:
            token = t
            break

    if not token:
        latest = qs.first()
        if latest:
            latest.attempts += 1
            latest.save(update_fields=["attempts"])
            if latest.attempts >= latest.max_attempts:
                latest.expires_at = timezone.now()
                latest.save(update_fields=["expires_at"])
        return JsonResponse({"ok": False, "message": "Invalid or expired OTP."}, status=400)

    if token.attempts >= token.max_attempts:
        token.expires_at = timezone.now()
        token.save(update_fields=["expires_at"])
        return JsonResponse({"ok": False, "message": "Too many attempts. Request a new OTP."}, status=429)

    # Success → mark used and login
    token.is_used = True
    token.save(update_fields=["is_used"])

    login(request, user)
    return JsonResponse({"ok": True, "message": "Logged in successfully."})

def logout_view(request):
    logout(request)
    return redirect("/")
