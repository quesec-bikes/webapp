from django.contrib import admin
from .models import Page

try:
    from django_ckeditor_5.widgets import CKEditor5Widget
    from django import forms

    class PageAdminForm(forms.ModelForm):
        class Meta:
            model = Page
            fields = "__all__"
            widgets = {
                "content": CKEditor5Widget(config_name="default")
            }
except Exception:
    PageAdminForm = None


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    form = PageAdminForm if PageAdminForm else None
    list_display = ("title", "slug", "is_published", "updated_at")
    list_filter = ("is_published",)
    search_fields = ("title", "slug", "content")
    prepopulated_fields = {"slug": ("title",)}
