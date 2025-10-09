from django.core.management.base import BaseCommand
from django.utils import timezone
from accounts.models import OtpToken

class BaseCommand(BaseCommand):
    help = "Delete/Expire old OTP tokens."

    def handle(self, *args, **kwargs):
        cutoff = timezone.now() - timezone.timedelta(days=2)
        n = OtpToken.objects.filter(created_at__lt=cutoff).delete()[0]
        self.stdout.write(self.style.SUCCESS(f"Deleted {n} old OTP tokens"))
