from django.db import models
from django.urls import reverse

try:
    # django-ckeditor-5
    from django_ckeditor_5.fields import CKEditor5Field
    CKField = CKEditor5Field
except Exception:
    # fallback: normal TextField if ckeditor5 not found (dev safety)
    CKField = models.TextField


class Page(models.Model):
    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=200, unique=True, help_text="URL part e.g. 'privacy-policy'")
    content = CKField(blank=True, null=True, config_name="default")
    is_published = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("id",)
        verbose_name = "Page"
        verbose_name_plural = "Pages"

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse("pages:detail", kwargs={"slug": self.slug})
