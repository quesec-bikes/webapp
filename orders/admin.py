from decimal import Decimal
from django import forms
from django.contrib import admin
from .models import Order, PaymentAttempt

# ---- helpers ----
def p2r(value_paise):
    try:
        return (Decimal(value_paise or 0) / Decimal(100)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")

def r2p(value_rupees):
    return int(Decimal(str(value_rupees or 0)) * Decimal(100))

# ---- Order Admin ----
class OrderAdminForm(forms.ModelForm):
    item_total     = forms.DecimalField(label="Item total (₹)", max_digits=12, decimal_places=2)
    discount_total = forms.DecimalField(label="Discount total (₹)", max_digits=12, decimal_places=2, required=False)
    shipping_total = forms.DecimalField(label="Shipping total (₹)", max_digits=12, decimal_places=2, required=False)
    grand_total    = forms.DecimalField(label="Grand total (₹)", max_digits=12, decimal_places=2)
    amount_paid    = forms.DecimalField(label="Amount paid (₹)", max_digits=12, decimal_places=2, required=False)

    class Meta:
        model = Order
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        o = self.instance
        if o and o.pk:
            self.initial["item_total"]     = p2r(o.item_total)
            self.initial["discount_total"] = p2r(o.discount_total)
            self.initial["shipping_total"] = p2r(o.shipping_total)
            self.initial["grand_total"]    = p2r(o.grand_total)
            self.initial["amount_paid"]    = p2r(o.amount_paid)

    def clean_item_total(self):     return r2p(self.cleaned_data["item_total"])
    def clean_discount_total(self): return r2p(self.cleaned_data.get("discount_total") or 0)
    def clean_shipping_total(self): return r2p(self.cleaned_data.get("shipping_total") or 0)
    def clean_grand_total(self):    return r2p(self.cleaned_data["grand_total"])
    def clean_amount_paid(self):    return r2p(self.cleaned_data.get("amount_paid") or 0)

@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    form = OrderAdminForm
    list_display = ("order_number", "status", "full_name", "mobile", "grand_total_rupees", "amount_paid_rupees", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("order_number", "full_name", "mobile", "email")

    @admin.display(description="Grand total (₹)")
    def grand_total_rupees(self, obj):
        return f"₹{p2r(obj.grand_total)}"

    @admin.display(description="Amount paid (₹)")
    def amount_paid_rupees(self, obj):
        return f"₹{p2r(obj.amount_paid)}"

# ---- PaymentAttempt Admin ----
class PaymentAttemptAdminForm(forms.ModelForm):
    amount = forms.DecimalField(label="Amount (₹)", max_digits=12, decimal_places=2)

    class Meta:
        model = PaymentAttempt
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        pa = self.instance
        if pa and pa.pk:
            self.initial["amount"] = p2r(pa.amount)

    def clean_amount(self):
        return r2p(self.cleaned_data["amount"])

@admin.register(PaymentAttempt)
class PaymentAttemptAdmin(admin.ModelAdmin):
    form = PaymentAttemptAdminForm
    list_display = ("order", "method", "status", "amount_rupees", "created_at")

    @admin.display(description="Amount (₹)")
    def amount_rupees(self, obj):
        return f"₹{p2r(obj.amount)}"
