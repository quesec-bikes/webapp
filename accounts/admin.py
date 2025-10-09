from django.contrib import admin
from .models import OtpToken, Profile

@admin.register(OtpToken)
class OtpTokenAdmin(admin.ModelAdmin):
    list_display = ("email", "purpose", "created_at", "expires_at", "is_used", "attempts", "last_sent_at")
    list_filter = ("purpose", "is_used",)
    search_fields = ("email",)

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("full_name", "city", "state")
    search_fields = ("user__username", "user__email", "mobile", "pincode", "city", "state")
