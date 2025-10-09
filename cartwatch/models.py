# cartwatch/models.py
from django.db import models
from django.utils import timezone

class CartWatchStatus(models.TextChoices):
    OPEN = "OPEN", "Open"
    CONVERTED = "CONVERTED", "Converted"
    CLOSED = "CLOSED", "Closed"

class CartWatchLead(models.Model):
    session_id = models.CharField(max_length=64, db_index=True)
    phone = models.CharField(max_length=20, db_index=True)
    cart_snapshot = models.JSONField(default=dict, blank=True)

    status = models.CharField(
        max_length=16,
        choices=CartWatchStatus.choices,
        default=CartWatchStatus.OPEN,
        db_index=True,
    )

    converted_order_id = models.CharField(max_length=64, blank=True, default="")
    source_url = models.URLField(blank=True, default="")
    user_agent = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["session_id", "status"]),
            models.Index(fields=["phone", "status"]),
        ]
        verbose_name = "CartWatch Lead"
        verbose_name_plural = "CartWatch Leads"

    def __str__(self):
        return f"{self.phone} [{self.status}]"

    def mark_converted(self, order_id: str = ""):
        self.status = CartWatchStatus.CONVERTED
        if order_id:
            self.converted_order_id = str(order_id)
        self.save(update_fields=["status", "converted_order_id", "updated_at"])
