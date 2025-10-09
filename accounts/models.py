from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid

class OtpToken(models.Model):
    PURPOSE_LOGIN = "login"
    PURPOSE_CHOICES = [(PURPOSE_LOGIN, "Login")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(db_index=True)
    code_hash = models.CharField(max_length=128)  # store sha256 hex
    purpose = models.CharField(max_length=16, choices=PURPOSE_CHOICES, default=PURPOSE_LOGIN)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    is_used = models.BooleanField(default=False)
    sent_count_today = models.PositiveSmallIntegerField(default=1)  # basic throttle
    last_sent_at = models.DateTimeField(auto_now_add=True)
    requester_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["email", "purpose", "is_used"]),
        ]

    def is_expired(self):
        return timezone.now() > self.expires_at

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    mobile = models.CharField(max_length=15, blank=True, default="")
    full_name = models.CharField(max_length=120, blank=True, default="")
    address = models.TextField(blank=True, default="")
    pincode = models.CharField(max_length=10, blank=True, default="")
    city = models.CharField(max_length=80, blank=True, default="")
    state = models.CharField(max_length=80, blank=True, default="")
    gst = models.CharField(max_length=20, blank=True, default="")

    def __str__(self):
        return f"Profile({self.user.email or self.user.username})"
