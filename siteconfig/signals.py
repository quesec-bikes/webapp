# siteconfig/signals.py
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.core.cache import cache
from .models import (
    TopBarMessage, SiteBranding, MenuItem,
    FooterSection, FooterLink, ContactBlock,
    SocialLink, NewsletterSignup
)

WATCH = [TopBarMessage, SiteBranding, MenuItem, FooterSection, FooterLink,
         ContactBlock, SocialLink, NewsletterSignup]

def _bust():
    # conservative: wipe only our keys prefix
    for key in list(getattr(cache, "_cache", {}).keys()):
        if str(key).startswith("sc:"):
            cache.delete(key)

for model in WATCH:
    @receiver(post_save, sender=model)
    @receiver(post_delete, sender=model)
    def _invalidate_cache(*args, **kwargs):
        _bust()
