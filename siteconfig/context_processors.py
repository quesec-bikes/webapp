# siteconfig/context_processors.py
from django.core.cache import cache
from django.db import models
from django.utils import timezone

from .models import (
    SiteBranding, TopBarMessage, MenuItem,
    FooterSection, ContactBlock, SocialLink, HomeSlide, MarqueeMessage
)

TTL = 60 * 60  # 1 hour


def _live_topbar_messages():
    key = "sc:topbar"
    data = cache.get(key)
    if data is not None:
        return data
    msgs = [m for m in TopBarMessage.objects.all() if m.is_live()]
    cache.set(key, msgs, TTL)
    return msgs


def _menu(group):
    key = f"sc:menu:{group}"
    data = cache.get(key)
    if data is not None:
        return data
    data = list(MenuItem.objects.filter(group=group, is_active=True).order_by("order", "id"))
    cache.set(key, data, TTL)
    return data


def _footer_sections():
    key = "sc:footer:sections"
    data = cache.get(key)
    if data is not None:
        return data
    sections = list(
        FooterSection.objects.filter(is_active=True).prefetch_related("links").order_by("order", "id")
    )
    cache.set(key, sections, TTL)
    return sections


def _branding():
    key = "sc:branding"
    data = cache.get(key)
    if data is not None:
        return data
    data = SiteBranding.get_solo()
    cache.set(key, data, TTL)
    return data


def _contact():
    key = "sc:contact"
    data = cache.get(key)
    if data is not None:
        return data
    data = ContactBlock.get_solo()
    cache.set(key, data, TTL)
    return data


def _social():
    key = "sc:social"
    data = cache.get(key)
    if data is not None:
        return data
    data = list(SocialLink.objects.filter(is_active=True).order_by("order", "id"))
    cache.set(key, data, TTL)
    return data


def _slides():
    key = "sc:slides"
    data = cache.get(key)
    if data is not None:
        return data
    today = timezone.now().date()
    qs = HomeSlide.objects.filter(is_active=True).order_by("order", "id")
    # date window respect
    qs = qs.filter(
        models.Q(start_date__isnull=True) | models.Q(start_date__lte=today),
        models.Q(end_date__isnull=True) | models.Q(end_date__gte=today),
    )
    data = list(qs)
    cache.set(key, data, TTL)
    return data

def _marquee():
    key = "sc:marquee"
    data = cache.get(key)
    if data is not None:
        return data
    today = timezone.now().date()
    qs = MarqueeMessage.objects.filter(is_active=True).order_by("order", "id")
    qs = qs.filter(
        models.Q(start_date__isnull=True) | models.Q(start_date__lte=today),
        models.Q(end_date__isnull=True) | models.Q(end_date__gte=today),
    )
    data = list(qs)
    cache.set(key, data, TTL)
    return data

def site_settings(request):
    return {
        "branding": _branding(),
        "topbar_messages": _live_topbar_messages(),
        "primary_menu": _menu("PRIMARY"),
        "utility_menu": _menu("UTILITY"),
        "footer_menu": _menu("FOOTER"),
        "footer_sections": _footer_sections(),
        "contact_block": _contact(),
        "social_links": _social(),
        "slides": _slides(),
        "marquee_messages": _marquee(),
    }
