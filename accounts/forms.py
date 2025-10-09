from django import forms

class RequestOtpForm(forms.Form):
    email = forms.EmailField()

class VerifyOtpForm(forms.Form):
    email = forms.EmailField()
    code = forms.CharField(min_length=6, max_length=6)
