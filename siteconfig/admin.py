# siteconfig/admin.py
from django.contrib import admin
from django import forms
from django.utils.html import format_html
from .models import (
    TopBarMessage, SiteBranding, MenuItem,
    FooterSection, FooterLink, ContactBlock,
    SocialLink, NewsletterSignup, HomeSlide, MarqueeMessage,
    validate_svg_or_raster
)

@admin.register(TopBarMessage)
class TopBarMessageAdmin(admin.ModelAdmin):
    list_display = ("text", "is_active", "order", "start_at", "end_at")
    list_filter = ("is_active",)
    search_fields = ("text",)
    ordering = ("order", "id")

class SiteBrandingForm(forms.ModelForm):
    # Force FileField (no Pillow check) + our validator
    logo = forms.FileField(required=True, validators=[validate_svg_or_raster])
    favicon = forms.FileField(required=False, validators=[validate_svg_or_raster])

    class Meta:
        model = SiteBranding
        fields = "__all__"

@admin.register(SiteBranding)
class SiteBrandingAdmin(admin.ModelAdmin):
    form = SiteBrandingForm
    def has_add_permission(self, request):
        return not SiteBranding.objects.exists()

@admin.register(MenuItem)
class MenuItemAdmin(admin.ModelAdmin):
    list_display = ("label", "group", "url", "is_active", "order", "new_tab")
    list_filter = ("group", "is_active")
    search_fields = ("label", "url")
    ordering = ("group", "order", "id")

class FooterLinkInline(admin.TabularInline):
    model = FooterLink
    extra = 1

@admin.register(FooterSection)
class FooterSectionAdmin(admin.ModelAdmin):
    list_display = ("title", "is_active", "order")
    list_filter = ("is_active",)
    inlines = [FooterLinkInline]
    ordering = ("order", "id")

@admin.register(ContactBlock)
class ContactBlockAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return not ContactBlock.objects.exists()

@admin.register(SocialLink)
class SocialLinkAdmin(admin.ModelAdmin):
    list_display = ("network", "url", "is_active", "order")
    list_filter = ("is_active", "network")
    list_editable = ("url", "is_active", "order")
    search_fields = ("url",)

@admin.register(NewsletterSignup)
class NewsletterSignupAdmin(admin.ModelAdmin):
    list_display = ("email", "created_at", "source_url")
    search_fields = ("email",)
    readonly_fields = ("created_at",)

@admin.register(HomeSlide)
class HomeSlideAdmin(admin.ModelAdmin):
    list_display = ("thumb", "title", "is_active", "order", "start_date", "end_date", "updated")
    list_editable = ("is_active", "order", "start_date", "end_date")
    search_fields = ("title", "link")
    list_filter = ("is_active",)
    readonly_fields = ("preview",)

    def thumb(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="height:34px;border-radius:6px;">', obj.image.url)
        return "—"
    thumb.short_description = "Preview"

    def preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-width:100%;height:auto;border-radius:10px;">', obj.image.url)
        return "—"
    
@admin.register(MarqueeMessage)
class MarqueeMessageAdmin(admin.ModelAdmin):
    list_display = ("text", "is_active", "order", "start_date", "end_date", "updated")
    list_editable = ("is_active", "order", "start_date", "end_date")
    search_fields = ("text",)
    list_filter = ("is_active",)