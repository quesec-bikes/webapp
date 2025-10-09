# siteconfig/models.py
from django.db import models
from django.utils import timezone
from django.core.validators import URLValidator, validate_email
from django.core.exceptions import ValidationError
from django.core.cache import cache


def validate_svg_or_raster(file):
    name = (file.name or "").lower()
    # Allow SVGs without Pillow
    if name.endswith(".svg") or name.endswith(".svgz"):
        return
    # Otherwise, must be a valid raster image (PNG/JPG/WEBP, etc.)
    try:
        from PIL import Image
        pos = file.tell()
        Image.open(file).verify()
        file.seek(pos)  # reset pointer
    except Exception:
        raise ValidationError("Upload a valid image (PNG/JPG/WEBP) or an SVG file.")

# ---------- Top Bar ----------
class TopBarMessage(models.Model):
    text = models.CharField(max_length=180)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=100)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("order", "id")

    def __str__(self):
        return self.text[:50]

    def is_live(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.start_at and now < self.start_at:
            return False
        if self.end_at and now > self.end_at:
            return False
        return True
    
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete("sc:topbar")

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete("sc:topbar")


# ---------- Branding (singleton) ----------
class SiteBranding(models.Model):
    # single row table; fetch via SiteBranding.get_solo()
    logo = models.ImageField(upload_to="branding/", validators=[validate_svg_or_raster])
    alt_text = models.CharField(max_length=120, default="Logo")
    logo_link = models.CharField(max_length=255, default="/")
    favicon = models.ImageField(upload_to="branding/", blank=True)

    def __str__(self):
        return "Site Branding"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ---------- Menus (single level) ----------
class MenuItem(models.Model):
    PRIMARY = "PRIMARY"
    UTILITY = "UTILITY"
    FOOTER = "FOOTER"
    GROUP_CHOICES = [
        (PRIMARY, "Primary (Header)"),
        (UTILITY, "Utility (Header small)"),
        (FOOTER, "Footer (flat list)"),
    ]
    label = models.CharField(max_length=80)
    url = models.CharField(max_length=255, help_text="Internal slug or external URL")
    group = models.CharField(max_length=16, choices=GROUP_CHOICES, default=PRIMARY)
    new_tab = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("group", "order", "id")

    def __str__(self):
        return f"{self.group}: {self.label}"


# ---------- Footer sections ----------
class FooterSection(models.Model):
    title = models.CharField(max_length=60)  # e.g., Shop, Help
    order = models.PositiveIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("order", "id")

    def __str__(self):
        return self.title


class FooterLink(models.Model):
    section = models.ForeignKey(FooterSection, on_delete=models.CASCADE, related_name="links")
    label = models.CharField(max_length=80)
    url = models.CharField(max_length=255)
    new_tab = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ("section", "order", "id")

    def __str__(self):
        return f"{self.section.title} → {self.label}"


# ---------- Contact block (singleton) ----------
class ContactBlock(models.Model):
    store_name = models.CharField(max_length=120, blank=True)
    address_html = models.TextField(blank=True, help_text="You can use <br> for line breaks")
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=60, blank=True)
    map_link = models.URLField(blank=True)

    def __str__(self):
        return "Contact Block"

    @classmethod
    def get_solo(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ---------- Social links ----------
class SocialLink(models.Model):
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"
    PINTEREST = "pinterest"
    X = "x"
    NETWORK_CHOICES = [
        (FACEBOOK, "Facebook"),
        (INSTAGRAM, "Instagram"),
        (PINTEREST, "Pinterest"),
        (X, "X (Twitter)"),
    ]
    network = models.CharField(max_length=20, choices=NETWORK_CHOICES)
    url = models.URLField()
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=100)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Social link"
        verbose_name_plural = "Social links"

    def __str__(self):
        return f"{self.get_network_display()}"

    # auto cache bust
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete("sc:social")

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete("sc:social")


# ---------- Newsletter signup storage (simple) ----------
class NewsletterSignup(models.Model):
    email = models.EmailField(validators=[validate_email])
    created_at = models.DateTimeField(auto_now_add=True)
    source_url = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [("email",)]
        ordering = ("-created_at",)

    def __str__(self):
        return self.email

class HomeSlide(models.Model):
    image = models.ImageField(upload_to="slides/%Y/%m/")
    title = models.CharField(max_length=200, blank=True)
    link = models.URLField(blank=True)  # optional: leave blank for no click
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Home slide"
        verbose_name_plural = "Home slides"

    def __str__(self):
        return self.title or f"Slide #{self.pk}"

    # Auto-clear cache used by context processor
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete("sc:slides")

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete("sc:slides")

class MarqueeMessage(models.Model):
    text = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    order = models.PositiveIntegerField(default=0)
    start_date = models.DateField(blank=True, null=True)
    end_date = models.DateField(blank=True, null=True)
    created = models.DateTimeField(auto_now_add=True)
    updated = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "Marquee message"
        verbose_name_plural = "Marquee messages"

    def __str__(self):
        return self.text[:50]

    # Auto cache clear
    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        cache.delete("sc:marquee")

    def delete(self, *args, **kwargs):
        super().delete(*args, **kwargs)
        cache.delete("sc:marquee")