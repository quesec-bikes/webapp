from django.db import models
from django.conf import settings
from django.utils import timezone

class Order(models.Model):
    class Status(models.TextChoices):
        CREATED = "CREATED", "Created"
        PAID = "PAID", "Paid"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Partially paid"
        CANCELLED = "CANCELLED", "Cancelled"
        FAILED = "FAILED", "Failed"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )
    order_number = models.CharField(max_length=24, unique=True, db_index=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.CREATED)

    # Address snapshot (read-only once created)
    full_name = models.CharField(max_length=120)
    email = models.EmailField(max_length=254, blank=True, null=True)
    address_line = models.TextField()
    city = models.CharField(max_length=80)
    state = models.CharField(max_length=80)
    pincode = models.CharField(max_length=10)
    mobile = models.CharField(max_length=20)
    gstin = models.CharField(max_length=15, blank=True, null=True)

    # Money in paise (recommended) — but if your project uses rupees as int, keep consistent.
    # Yahan 'amount_*' paise me store ho raha hai:
    item_total = models.PositiveIntegerField(help_text="Subtotal in paise")
    discount_total = models.PositiveIntegerField(default=0, help_text="Discount in paise")
    shipping_total = models.PositiveIntegerField(default=0, help_text="Shipping in paise")
    grand_total = models.PositiveIntegerField(help_text="Final payable in paise")

    coupon_code = models.CharField(max_length=50, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    # Payment tracking
    amount_paid = models.PositiveIntegerField(default=0, help_text="Total paid so far (in paise)")
    paid_at = models.DateTimeField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def mark_paid_full(self):
        self.status = self.Status.PAID
        self.amount_paid = self.grand_total
        self.paid_at = timezone.now()
        self.save(update_fields=["status", "amount_paid", "paid_at", "updated_at"])

    def mark_paid_partial(self, amount):
        self.status = self.Status.PARTIALLY_PAID
        self.amount_paid = (self.amount_paid or 0) + int(amount)
        self.save(update_fields=["status", "amount_paid", "updated_at"])

    @property
    def amount_due(self):
        return max(self.grand_total - (self.amount_paid or 0), 0)

    def __str__(self):
        return f"{self.order_number} ({self.get_status_display()})"


class OrderItem(models.Model):
    order = models.ForeignKey(Order, related_name="items", on_delete=models.CASCADE)
    product = models.ForeignKey("shop.Product", on_delete=models.PROTECT)
    variant = models.ForeignKey("shop.Variant", null=True, blank=True, on_delete=models.PROTECT)

    # Snapshot fields
    title = models.CharField(max_length=200)
    variant_text = models.CharField(max_length=200, blank=True)
    qty = models.PositiveIntegerField()
    unit_price = models.PositiveIntegerField(help_text="Unit price in paise")
    line_total = models.PositiveIntegerField(help_text="Line total in paise")

    def __str__(self):
        return f"{self.title} x {self.qty}"


class PaymentAttempt(models.Model):
    class Method(models.TextChoices):
        RAZORPAY = "razorpay", "Razorpay"
        PAYU = "payu", "PayU"
        UPI_PROMO = "upi_hdfc_any", "UPI Promo (HDFC/any)"
        SEMI_COD = "semi_cod", "Semi-COD"

    class Status(models.TextChoices):
        INITIATED = "INITIATED", "Initiated"
        SUCCESS = "SUCCESS", "Success"
        FAILED = "FAILED", "Failed"

    order = models.ForeignKey(Order, related_name="payments", on_delete=models.CASCADE)
    method = models.CharField(max_length=20, choices=Method.choices)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    amount = models.PositiveIntegerField(help_text="Attempt amount in paise")

    provider_order_id = models.CharField(max_length=100, blank=True, null=True)
    provider_payment_id = models.CharField(max_length=100, blank=True, null=True)
    provider_signature = models.CharField(max_length=200, blank=True, null=True)

    raw_payload = models.JSONField(default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.order.order_number} - {self.method} - {self.status} - {self.amount}"
