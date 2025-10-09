import hashlib, random
from django.conf import settings
from django.core.mail import send_mail
from django.utils import timezone
from datetime import timedelta

def generate_otp_code():
    return f"{random.randint(0, 999999):06d}"

def hash_code(code: str) -> str:
    # add a small salt from settings (set e.g. OTP_PEPPER)
    pepper = getattr(settings, "OTP_PEPPER", "qs_default_pepper")
    return hashlib.sha256((pepper + code).encode()).hexdigest()

def default_expiry():
    minutes = getattr(settings, "OTP_EXPIRY_MINUTES", 10)
    return timezone.now() + timedelta(minutes=minutes)

def send_login_otp(email: str, code: str):
    subject = "Your Quesec OTP"
    msg = f"Your one-time login code is: {code}\nIt expires in 10 minutes."
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", None)
    send_mail(subject, msg, from_email, [email], fail_silently=False)
