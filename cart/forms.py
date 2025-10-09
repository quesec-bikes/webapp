# cart/forms.py
from django import forms
import re

MOBILE_REGEX  = re.compile(r"^[6-9]\d{9}$")
PINCODE_REGEX = re.compile(r"^\d{6}$")
GSTIN_REGEX   = re.compile(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$')

class CheckoutForm(forms.Form):
    mobile       = forms.CharField(label="Mobile", max_length=10)
    email        = forms.EmailField(label="Email")
    full_name    = forms.CharField(label="Full Name", max_length=120)
    full_address = forms.CharField(label="Full Address", widget=forms.Textarea(attrs={"rows":3}))
    pincode      = forms.CharField(label="Pincode", max_length=6)
    city         = forms.CharField(label="City", max_length=80, required=False)
    state        = forms.CharField(label="State", max_length=80, required=False)
    gst          = forms.CharField(label="GST (optional)", max_length=15, required=False)

    def clean_mobile(self):
        m = (self.cleaned_data.get("mobile") or "").strip()
        if not MOBILE_REGEX.match(m):
            raise forms.ValidationError("Please enter a valid 10-digit Indian mobile starting 6-9.")
        return m

    def clean_pincode(self):
        p = (self.cleaned_data.get("pincode") or "").strip()
        if not PINCODE_REGEX.match(p):
            raise forms.ValidationError("Please enter a valid 6-digit pincode.")
        return p

    def clean_gst(self):
        g = (self.cleaned_data.get("gst") or "").strip().upper()
        if not g:
            return ""  # optional: blank is OK
        # strict GSTIN check (15 chars with structure)
        if not GSTIN_REGEX.match(g):
            raise forms.ValidationError("Enter a valid 15-character GSTIN or leave blank.")
        return g
