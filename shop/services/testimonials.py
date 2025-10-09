# shop/services/testimonials.py
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import random

from shop.models import Review

IST_TZ = ZoneInfo(getattr(settings, "TIME_ZONE", "Asia/Kolkata") or "Asia/Kolkata")
CACHE_KEY_FMT = "home:testimonials:{date}"  # e.g. home:testimonials:20251007
CACHE_TTL_SECONDS = None  # we’ll compute until midnight IST dynamically


def _today_ist_date_str():
    now_ist = timezone.now().astimezone(IST_TZ)
    return now_ist.strftime("%Y%m%d")


def _seconds_until_midnight_ist():
    now_ist = timezone.now().astimezone(IST_TZ)
    next_midnight = (now_ist + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return int((next_midnight - now_ist).total_seconds())


def _extract_state(review: Review) -> str:
    """
    Priority:
      1) review.user.profile.state (if available & non-empty)
      2) review.order_item.order.state (snapshot)
    """
    # 1) Profile.state
    try:
        st = (getattr(review.user, "profile", None) and review.user.profile.state) or ""
        st = (st or "").strip()
        if st:
            return st
    except Exception:
        pass

    # 2) Order snapshot state via order_item
    try:
        if review.order_item and review.order_item.order:
            st2 = (review.order_item.order.state or "").strip()
            if st2:
                return st2
    except Exception:
        pass

    return ""  # fallback


def _serialize_review_card(review: Review) -> dict:
    """
    Minimal payload required by template.
    """
    product = review.product
    variant = review.variant
    rating = int(getattr(review, "rating", 0) or 0)
    rating = max(0, min(5, rating))
    return {
        "id": review.id,
        "rating": review.rating,
        "title": review.title or "",
        "body": review.body or "",
        "user_name": getattr(review.user, "first_name", "") or getattr(review.user, "username", "") or "Customer",
        "state": _extract_state(review) or "India",
        "product_title": getattr(product, "title", "") if product else "",
        "variant_title": getattr(variant, "name", "") if variant else "",
        "product": product,
        "variant": variant,
        "is_verified_purchase": review.is_verified_purchase,
        "stars_filled": list(range(rating)),
        "stars_empty": list(range(5 - rating)),
    }


def _pick_random_ids(qs, k: int = 9):
    """
    Efficient random pick without ORDER BY('?') on large tables.
    We sample IDs in Python once per day; cached thereafter.
    """
    ids = list(qs.values_list("id", flat=True))
    if not ids:
        return []
    k = min(k, len(ids))
    # Seed by date so first selection of the day is deterministic before caching
    seed = int(_today_ist_date_str())
    rnd = random.Random(seed)
    return rnd.sample(ids, k)


def get_home_testimonials(limit: int = 9):
    """
    Returns list[dict] of 9 review cards (4/5-star, published),
    stable within the day, but refreshed on cache invalidation.
    """
    date_str = _today_ist_date_str()
    cache_key = CACHE_KEY_FMT.format(date=date_str)
    data = cache.get(cache_key)
    if data is not None:
        return data

    base_qs = (
        Review.objects
        .select_related("user", "product", "variant", "order_item__order", "user__profile")
        .filter(is_published=True, rating__gte=4)
    )

    ids = _pick_random_ids(base_qs, k=limit)
    if not ids:
        cache.set(cache_key, [], _seconds_until_midnight_ist())
        return []

    # Preserve order of ids
    picked_qs = base_qs.filter(id__in=ids)
    by_id = {r.id: r for r in picked_qs}
    ordered = [by_id[i] for i in ids if i in by_id]

    payload = [_serialize_review_card(r) for r in ordered]
    cache.set(cache_key, payload, _seconds_until_midnight_ist())
    return payload


def invalidate_today_cache():
    cache_key = CACHE_KEY_FMT.format(date=_today_ist_date_str())
    cache.delete(cache_key)
