# shop/services/featured.py
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from random import randint
from django.utils import timezone
from django.urls import reverse, NoReverseMatch

from shop.models import Variant, Category  # adjust imports as per project

# ---- Settings / Defaults ----
TTL = getattr(settings, "FEATURED_TABS_TTL", 60 * 10)  # 10 minutes for product-feature tabs
FEATURED_CATS_TTL = getattr(settings, "FEATURED_CATS_TTL", 60 * 10)  # 10 minutes for category-feature tabs
FEATURED_CATS_MAX_SECTIONS = getattr(settings, "FEATURED_CATS_MAX_SECTIONS", 6)
FEATURED_CATS_VERSION = "v2"  # bump on schema/ordering change


# ============================================================
# A) FEATURED PRODUCTS — EXISTING LOGIC
# ============================================================
def get_featured_tabs(limit=12, per_tab_limit=12):
    """
    Tabs of FEATURED VARIANTS grouped by their product.category.
    Returns: list of dicts
    """
    key = f"home:featured_tabs:v1:{limit}:{per_tab_limit}"
    data = cache.get(key)
    if data is not None:
        return data

    vqs = Variant.objects.featured().select_related("product", "product__category")

    cat_id_to_variants = {}
    for v in vqs.iterator():
        c = getattr(v.product, "category", None)
        if not c:
            continue
        bucket = cat_id_to_variants.setdefault(c.id, {"cat": c, "variants": []})
        if len(bucket["variants"]) < per_tab_limit:
            bucket["variants"].append(v)

    tabs = []
    for _, entry in cat_id_to_variants.items():
        c = entry["cat"]
        tabs.append({
            "cat": c,
            "slug": getattr(c, "slug", str(c.id)),
            "name": getattr(c, "name", str(c)),
            "count": len(entry["variants"]),
            "variants": entry["variants"],
        })

    def _tab_sort_key(x):
        cat = x["cat"]
        disp = getattr(cat, "display_order", None)
        return (
            1 if disp is None else 0,
            disp if disp is not None else 10**9,
            x["name"].lower(),
            cat.id,
        )

    tabs.sort(key=_tab_sort_key)
    tabs = tabs[:limit]

    cache.set(key, tabs, TTL)
    return tabs


# ============================================================
# B) FEATURED CATEGORIES — NEW LOGIC (multiple sections on Home)
# ============================================================
from django.db.models import Q
from django.db.models.functions import Random
from django.utils import timezone

FEATURED_CATS_VERSION = "v10"  # bump cache version

def _python_sort_categories_nulls_last(categories):
    return sorted(
        categories,
        key=lambda c: (
            1 if getattr(c, "display_order", None) is None else 0,
            getattr(c, "display_order", 10**9) if getattr(c, "display_order", None) is not None else 10**9,
            (getattr(c, "name", "") or "").lower(),
            c.id,
        ),
    )

def _get_featured_categories_queryset():
    qs = Category.objects.filter(is_active=True)
    if hasattr(Category, "featured"):
        qs = qs.filter(featured=True)
    return qs

def _get_category_and_children_ids(cat):
    ids = [cat.id]
    if hasattr(Category, "parent"):
        ids += list(Category.objects.filter(is_active=True, parent=cat).values_list("id", flat=True))
    return ids

def _get_rotating_random_variants_for_category(cat, limit=5, seed=None):
    """
    Simple random (DB-level). Daily seed-based md5 ordering optional;
    helpers centralize price/url/image, so selection randomness only matters here.
    """
    cat_ids = _get_category_and_children_ids(cat)
    base = (
        Variant.objects
        .filter(product__category_id__in=cat_ids)
        .select_related("product", "product__category", "size", "color_primary", "color_secondary")
        .prefetch_related("images", "product__images")
    )
    if hasattr(Variant, "is_active"):
        base = base.filter(is_active=True)
    try:
        base = base.filter(product__is_active=True)
    except Exception:
        pass

    # stock filter (if field exists in your model)
    if hasattr(Variant, "stock_qty"):
        base = base.filter(Q(stock_qty__gt=0) | Q(backorder_allowed=True))

    return list(base.order_by(Random())[:limit])

def get_featured_categories_tabs(limit_per_cat=5, max_sections=None, use_cache=True):
    max_sections = max_sections or FEATURED_CATS_MAX_SECTIONS
    cache_key = f"home:featured_cats:{FEATURED_CATS_VERSION}:lp{limit_per_cat}:ms{max_sections}"

    if use_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    cats = _python_sort_categories_nulls_last(list(_get_featured_categories_queryset()))
    if max_sections:
        cats = cats[:max_sections]

    results = []
    for cat in cats:
        variants = _get_rotating_random_variants_for_category(cat, limit=limit_per_cat)
        if not variants:
            continue

        # Category URL — rely on model's get_absolute_url if present
        try:
            url = cat.get_absolute_url()
        except Exception:
            url = f"/{cat.slug}/" if getattr(cat, "slug", None) else "/shop/"

        items = []
        for v in variants:
            card = v.card_info()  # title, img, price (promo if active), mrp, discount, is_promo_active
            items.append({
                "variant": v,
                "title": card["title"],
                "href": v.get_absolute_url(),
                "thumb": card["img"],
                "promo_price": card["price"],  # template compatible
                "mrp": card["mrp"],
                "discount": card.get("discount") or 0,
            })

        results.append({
            "cat": cat,
            "name": getattr(cat, "name", f"Category {cat.id}"),
            "slug": getattr(cat, "slug", str(cat.id)),
            "url": url,
            "display_order": getattr(cat, "display_order", None),
            "items": items,
        })

    if use_cache:
        cache.set(cache_key, results, FEATURED_CATS_TTL)

    return results