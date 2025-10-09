# shop/views.py
from django.shortcuts import render, get_object_or_404
from django.http import HttpResponsePermanentRedirect, JsonResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.urls import reverse
from django.db import models
from django.core.paginator import Paginator
from urllib.parse import urlencode
import time, random, json, re, html
from django.core.cache import cache
from django.views.decorators.csrf import ensure_csrf_cookie, csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.utils.html import strip_tags
from django.utils import timezone
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from django.db.models import Q, F, Value, IntegerField, DecimalField, Subquery, OuterRef, Count, Min, Avg
from django.http import Http404
from django.apps import apps
from django.db.models.functions import Coalesce, Greatest
from orders.models import OrderItem
from .models import Product, Category, Variant, FBTLink, Coupon, CouponRedemption, Review
from django.contrib.auth.decorators import login_required


def _canonical_product_url(product):
    cat = product.category
    if cat and cat.parent:
        return reverse(
            "shop:product_detail_child",
            kwargs={
                "parent_slug": cat.parent.slug,
                "child_slug": cat.slug,
                "slug": product.slug,
            },
        )
    elif cat:
        return reverse(
            "shop:product_detail_parent",
            kwargs={"parent_slug": cat.slug, "slug": product.slug},
        )
    return f"/{product.slug}"


def _cart_watch_count(product_id: int, variant_id: int | None, promo_active: bool = False) -> int:
    """
    Per-(product, variant) random-walk counter (10..99).
    Promo ON -> slightly higher values + upward bias + faster updates.
    Promo OFF -> neutral/slight-down bias + slower updates.
    """
    key = f"cartwatch:p{product_id}:v{variant_id or 0}"
    now = time.time()
    payload = cache.get(key)

    if not payload:
        seeded = random.Random(f"{product_id}:{variant_id or 0}")
        start = seeded.randint(44, 62)
        base_next = seeded.randint(300, 900) if not promo_active else seeded.randint(180, 600)
        payload = {"value": start, "next": now + base_next, "promo": promo_active}
        cache.set(key, payload, timeout=12 * 3600)
        return start

    value = int(payload.get("value", 50))
    next_at = float(payload.get("next", now + 600))
    prev_state = bool(payload.get("promo", False))

    if prev_state != promo_active:
        rng = random.Random(f"statechange:{int(now)}:{product_id}:{variant_id or 0}")
        if promo_active:
            value = min(99, value + rng.choice([0, 1, 2]))
            next_at = now + rng.randint(60, 180)
        else:
            value = max(10, value - rng.choice([0, 0, 1]))
            next_at = now + rng.randint(180, 480)
        cache.set(key, {"value": value, "next": next_at, "promo": promo_active}, timeout=12 * 3600)
        return value

    if promo_active and (next_at - now) > 480:
        next_at = now + 240
        payload["next"] = next_at
        cache.set(key, payload, timeout=12 * 3600)

    if now >= next_at:
        rng = random.Random(f"{int(now // 300)}:{product_id}:{variant_id or 0}:{int(promo_active)}")
        step = rng.choice([-1, 0, 0, 1])
        target = 70 if promo_active else 50
        diff = target - value
        if abs(diff) >= 1 and rng.random() < (0.60 if promo_active else 0.52):
            step += 1 if diff > 0 else -1
        if rng.random() < (0.15 if promo_active else 0.10):
            step += 1 if diff >= 0 else -1
        new_val = max(10, min(99, value + step))
        next_at = now + (rng.randint(180, 600) if promo_active else rng.randint(300, 900))
        cache.set(key, {"value": new_val, "next": next_at, "promo": promo_active}, timeout=12 * 3600)
        return new_val

    return value


# NEW: Convert CKEditor short_description HTML to bullet points
def _short_desc_points(text: str, limit: int = 12):
    if not text:
        return []
    li_matches = re.findall(r"<li[^>]*>(.*?)</li>", text, flags=re.I | re.S)
    if li_matches:
        points = []
        for raw in li_matches:
            plain = strip_tags(raw).strip()
            plain = html.unescape(plain)
            if plain:
                points.append(plain)
            if len(points) >= limit:
                break
        return points

    plain = strip_tags(text)
    plain = html.unescape(plain)
    plain = plain.replace("\r\n", "\n").replace("\r", "\n")
    parts = re.split(r"\n|•|▪|‣|●|○|\s-\s|\s–\s|\s—\s", plain)
    out = []
    for p in parts:
        s = re.sub(r"^\s*[-–—•▪‣●○]*\s*", "", p).strip()
        if s:
            out.append(s)
        if len(out) >= limit:
            break
    return out


# 🔹 Helper — given a color group’s variants, pick the first “available” size/variant.
def _first_available_variant_in_color(variants_in_color):
    avail = [v for v in variants_in_color if (getattr(v, "in_stock", False) or (v.stock_qty and v.stock_qty > 0) or v.backorder_allowed)]
    if avail:
        avail.sort(key=lambda v: (-(v.stock_qty or 0), v.id))
        return avail[0]
    return variants_in_color[0] if variants_in_color else None


@ensure_csrf_cookie
def product_detail(request, parent_slug, slug, child_slug=None):
    # 1) Resolve category path (parent -> optional child)
    parent_cat = get_object_or_404(
        Category.objects.select_related("parent"),
        slug=parent_slug,
        parent__isnull=True,
    )
    resolved_category = parent_cat
    if child_slug:
        resolved_category = get_object_or_404(
            Category.objects.select_related("parent"),
            slug=child_slug,
            parent=parent_cat,
        )

    # 2) Product
    product = get_object_or_404(
        Product.objects
        .select_related("category", "category__parent")
        .prefetch_related("images", "specifications"),
        slug=slug,
    )
    if product.category != resolved_category:
        return HttpResponsePermanentRedirect(_canonical_product_url(product))

    # 3) Active variants (with related color/size & images)
    qs_variants = (
        product.variants.filter(is_active=True)
        .select_related("color_primary", "color_secondary", "size")
        .prefetch_related("images")
        .order_by("id")
    )
    variants = list(qs_variants)

    # Guard invalid ?variant -> redirect to first active variant of THIS product
    if "variant" in request.GET:
        sel_variant_id = request.GET.get("variant")
        is_valid = any(str(v.id) == str(sel_variant_id) for v in variants)
        if not is_valid and variants:
            qs = request.GET.copy()
            qs["variant"] = str(variants[0].id)
            qs.pop("color", None)
            return HttpResponsePermanentRedirect(f"{request.path}?{qs.urlencode()}")

    # Default variant redirect (no ?variant and no ?color)
    if "variant" not in request.GET and "color" not in request.GET:
        if variants:
            default_variant_id = variants[0].id
            qs = request.GET.copy()
            qs["variant"] = str(default_variant_id)
            return HttpResponsePermanentRedirect(f"{request.path}?{qs.urlencode()}")

    # ---- Build COLOR groups
    color_groups = {}
    for v in variants:
        key = (v.color_primary_id, v.color_secondary_id or None)
        bucket = color_groups.setdefault(
            key, {"primary": v.color_primary, "secondary": v.color_secondary, "variants": []}
        )
        bucket["variants"].append(v)

    # ---- Determine selected_variant / selected_color
    sel_variant_id = request.GET.get("variant")
    selected_variant = None
    if sel_variant_id:
        selected_variant = next((v for v in variants if str(v.id) == str(sel_variant_id)), None)

    sel_color_param = request.GET.get("color")
    selected_color_key = None
    if selected_variant:
        selected_color_key = (selected_variant.color_primary_id, selected_variant.color_secondary_id or None)
    elif sel_color_param:
        try:
            p, s = sel_color_param.split("-")
            sc = (int(p), int(s) if s != "0" else None)
            if sc in color_groups:
                selected_color_key = sc
        except Exception:
            selected_color_key = None

    # ---- Auto-select rules (no JS)
    if not selected_variant:
        # (A) Only one color exists
        if len(color_groups) == 1:
            only_key = next(iter(color_groups.keys()))
            selected_color_key = selected_color_key or only_key
            sizes_in_color = {v.size_id for v in color_groups[only_key]["variants"] if v.size_id}
            if len(sizes_in_color) == 1:
                only_size_id = next(iter(sizes_in_color))
                selected_variant = next(
                    v for v in color_groups[only_key]["variants"] if v.size_id == only_size_id
                )
            else:
                best = _first_available_variant_in_color(color_groups[only_key]["variants"])
                if best:
                    qs = request.GET.copy()
                    qs["variant"] = str(best.id)
                    return HttpResponsePermanentRedirect(f"{request.path}?{qs.urlencode()}")

        # (B) A specific color is selected (via ?color=)
        elif selected_color_key:
            sizes_in_color = {v.size_id for v in color_groups[selected_color_key]["variants"] if v.size_id}
            if len(sizes_in_color) == 1:
                only_size_id = next(iter(sizes_in_color))
                selected_variant = next(
                    v for v in color_groups[selected_color_key]["variants"] if v.size_id == only_size_id
                )
            else:
                best = _first_available_variant_in_color(color_groups[selected_color_key]["variants"])
                if best:
                    qs = request.GET.copy()
                    qs["variant"] = str(best.id)
                    return HttpResponsePermanentRedirect(f"{request.path}?{qs.urlencode()}")

    # ---- Color options for template
    color_options = []

    def color_key_str(k):
        return f"{k[0]}-{k[1] if k[1] is not None else 0}"

    for key, data in color_groups.items():
        label = data["variants"][0].color_label
        sizes_here = {v.size_id for v in data["variants"] if v.size_id}
        if len(sizes_here) == 1:
            size_id = next(iter(sizes_here))
            target_variant = next(v for v in data["variants"] if v.size_id == size_id)
            href = f"?variant={target_variant.id}"
        else:
            href = f"?color={color_key_str(key)}"
        color_options.append(
            {
                "primary_hex": data["primary"].hex_code if data["primary"] else "#000000",
                "secondary_hex": data["secondary"].hex_code if data["secondary"] else None,
                "label": label,
                "href": href,
                "is_active": (selected_color_key == key)
                or (
                    selected_variant
                    and (selected_variant.color_primary_id, selected_variant.color_secondary_id or None) == key
                ),
            }
        )

    # ---- Size options depend on selected color
    size_options = []
    if selected_color_key:
        variants_in_color = color_groups[selected_color_key]["variants"]
        seen = set()
        for v in variants_in_color:
            if not v.size_id or v.size_id in seen:
                continue
            seen.add(v.size_id)
            size_options.append(
                {
                    "label": v.size.name,
                    "href": f"?variant={v.id}",
                    "disabled": False,
                    "is_active": bool(selected_variant and selected_variant.size_id == v.size_id),
                }
            )
    else:
        seen = set()
        for v in variants:
            if not v.size_id or v.size_id in seen:
                continue
            seen.add(v.size_id)
            size_options.append(
                {"label": v.size.name, "href": "", "disabled": True, "is_active": False}
            )

    # ---- Images for gallery (variant-first, then product gallery)
    variant_images = list(selected_variant.images.all()) if selected_variant else []
    product_images = list(product.images.all())
    all_images = variant_images + product_images

    # ---- Price & Discount (promo-aware) + countdown
    display_mrp = None
    display_price = None
    discount_percent = 0
    promo_active = False
    countdown_seconds = 0

    if selected_variant:
        try:
            promo_active = selected_variant.is_promo_active()
        except Exception:
            promo_active = False

        display_mrp = getattr(selected_variant, "mrp", None)
        display_price = (
            getattr(selected_variant, "promo_price", None) if promo_active
            else getattr(selected_variant, "sale_price", None)
        )

        try:
            countdown_seconds = selected_variant.promo_seconds_left() if promo_active else 0
        except Exception:
            countdown_seconds = 0

        try:
            if display_mrp is not None and display_price is not None:
                mrp_val = float(display_mrp)
                price_val = float(display_price)
                if mrp_val > 0 and price_val < mrp_val:
                    discount_percent = int(round((mrp_val - price_val) * 100.0 / mrp_val))
                    discount_percent = max(0, min(99, discount_percent))
        except Exception:
            discount_percent = 0

    # ---- Cart watchers (promo-aware)
    cart_watchers = _cart_watch_count(
        product.id,
        selected_variant.id if selected_variant else None,
        promo_active=promo_active,
    )

    # ---- Quantity clamp
    max_qty = selected_variant.stock_qty if (selected_variant and selected_variant.in_stock) else 0
    try:
        qty = int(request.GET.get("qty", 1 if max_qty > 0 else 0))
    except (TypeError, ValueError):
        qty = 1 if max_qty > 0 else 0

    if max_qty == 0:
        qty = 0
    else:
        qty = max(1, min(qty, max_qty))

    # ---- Build +/- URLs (preserve current selection)
    query_base = {}
    if selected_variant:
        query_base["variant"] = selected_variant.id
    elif selected_color_key:
        p, s = selected_color_key[0], (selected_color_key[1] or 0)
        query_base["color"] = f"{p}-{s}"

    minus_qty = max(1, qty - 1) if max_qty > 0 else 0
    plus_qty = min(max_qty, qty + 1) if max_qty > 0 else 0

    minus_qs = urlencode({**query_base, "qty": minus_qty}) if max_qty > 0 else ""
    plus_qs = urlencode({**query_base, "qty": plus_qty}) if max_qty > 0 else ""

    qty_minus_url = f"{request.path}?{minus_qs}" if minus_qs else request.path
    qty_plus_url = f"{request.path}?{plus_qs}" if plus_qs else request.path

    # ---- Effective specifications (product base + variant overrides)
    spec_qs = list(product.specifications.all().order_by("sort_order", "id"))
    order_map = {s.title: (s.sort_order, idx) for idx, s in enumerate(spec_qs)}
    try:
        base_specs = product.specs_dict() or {}
    except Exception:
        base_specs = {s.title: s.value for s in spec_qs}
    override_specs = {}
    if selected_variant and getattr(selected_variant, "specs_override", None):
        override_specs = selected_variant.specs_override or {}

    merged_specs = {}
    merged_specs.update(base_specs)
    merged_specs.update(override_specs)

    spec_rows = []
    for title, value in merged_specs.items():
        sort, seq = order_map.get(title, (9999, 9999))
        spec_rows.append({"title": title, "value": value, "sort": sort, "seq": seq})
        # end loop
    spec_rows.sort(key=lambda r: (r["sort"], r["seq"], str(r["title"]).lower()))

    # NEW: short description -> bullet points
    short_points = _short_desc_points(getattr(product, "short_description", "") or "")

    # ---- Combine Reviews from all variants for schema ----
    all_reviews = Review.objects.filter(variant__in=variants, is_published=True)
    group_rating_count = all_reviews.count()
    group_avg_rating = all_reviews.aggregate(Avg("rating"))["rating__avg"]
    if group_avg_rating is None:
        group_avg_rating = 0
    if group_rating_count is None:
        group_rating_count = 0

    # Pick top 3 reviews (4★/5★ first → else latest)
    top_high = all_reviews.filter(rating__in=[4, 5]).order_by("-created_at")[:3]
    if top_high.count() < 3:
        fill = all_reviews.exclude(id__in=top_high.values("id")).order_by("-created_at")[: 3 - top_high.count()]
        product_top_reviews = list(top_high) + list(fill)
    else:
        product_top_reviews = list(top_high)

    context = {
        "product": product,
        "category": resolved_category,
        "parent_category": parent_cat,
        "selected_variant": selected_variant,
        "selected_color_key": selected_color_key,
        "color_options": color_options,
        "size_options": size_options,
        "all_images": all_images,
        "variants": variants,

        # stock/qty
        "max_qty": max_qty,
        "qty": qty,
        "qty_minus_url": qty_minus_url,
        "qty_plus_url": qty_plus_url,

        # social proof counter
        "cart_watchers": cart_watchers,

        # price/discount (promo-aware)
        "display_mrp": display_mrp,
        "display_price": display_price,
        "discount_percent": discount_percent,
        "promo_active": promo_active,

        # countdown
        "countdown_seconds": countdown_seconds,

        # specs table
        "spec_rows": spec_rows,

        # NEW: features bullets from short_description
        "short_points": short_points,

        "group_avg_rating": group_avg_rating,
        "group_rating_count": group_rating_count,
        "product_top_reviews": product_top_reviews,
        "now": timezone.now(),
    }
    # --- Reviews context injection ---
    if selected_variant:
        can_review, _ = user_purchased_variant(request.user, selected_variant)
        reviews_ctx = build_reviews_context(product, selected_variant, page_number=1, sort="recent")
        context.update({
            "can_review": can_review,
            **reviews_ctx,
        })
    return render(request, "shop/product.html", context)


# ---------- FBT helpers & API ----------

def _pick_default_variant_for_product(product: Product, source_variant: Variant, strategy: str):
    # Prefer in-stock; safe fallback to all()
    qs = product.variants.filter(stock_qty__gt=0)
    if not qs.exists():
        qs = product.variants.all()
    if not qs.exists():
        return None

    if strategy == 'SAME_ATTRIBUTE_MATCH':
        try:
            matched = qs.filter(color_primary=source_variant.color_primary)
            return matched.first() or qs.first()
        except Exception:
            return qs.first()

    if strategy == 'PRICE_NEAREST':
        try:
            base = float(getattr(source_variant, "get_effective_price", lambda: source_variant.sale_price)())
            annotated = [(abs(float(getattr(v, "get_effective_price", lambda: v.sale_price)()) - base), v) for v in qs]
            annotated.sort(key=lambda t: t[0])
            return annotated[0][1]
        except Exception:
            return qs.first()

    return qs.first()


# NEW: build a human label "Color / Size" for an FBT variant
def _variant_label(v: Variant) -> str:
    color_part = ""
    try:
        if getattr(v, "color_primary", None) and getattr(v.color_primary, "name", ""):
            if getattr(v, "color_secondary", None) and getattr(v.color_secondary, "name", ""):
                color_part = f"{v.color_primary.name} + {v.color_secondary.name}"
            else:
                color_part = v.color_primary.name
    except Exception:
        color_part = ""

    size_part = ""
    try:
        if getattr(v, "size", None) and getattr(v.size, "name", ""):
            size_part = v.size.name
    except Exception:
        size_part = ""

    parts = [p for p in [color_part, size_part] if p]
    return " / ".join(parts) if parts else f"#{getattr(v, 'id', '')}"


def api_fbt(request):
    vid = request.GET.get('variant')
    try:
        source = Variant.objects.select_related('product').get(pk=vid)
    except Exception:
        return JsonResponse({'items': [], 'source_variant': None, 'max_items': 3})

    MAX_ITEMS = 3
    items = []

    # 1) Variant-level links (ordered by priority)
    links = (
        FBTLink.objects
        .select_related('target_product', 'target_variant')
        .filter(source_variant=source, is_active=True)
        .order_by('priority', 'id')[:MAX_ITEMS + 5]
    )
    for link in links:
        v = link.target_variant
        if not v and link.target_product:
            v = _pick_default_variant_for_product(
                link.target_product, source, 'FIRST_IN_STOCK'
            )
        if v and v not in items:
            items.append(v)
        if len(items) >= MAX_ITEMS:
            break

    # 2) Product-level defaults (fill remaining)
    if len(items) < MAX_ITEMS:
        defaults = list(source.product.fbt_defaults.all()[:MAX_ITEMS * 2])
        for p in defaults:
            if len(items) >= MAX_ITEMS:
                break
            v = _pick_default_variant_for_product(
                p, source, source.product.fbt_variant_strategy or 'FIRST_IN_STOCK'
            )
            if v and v not in items:
                items.append(v)

    # ✅ promo-aware pricing (offer sirf active hone par)
    def _fbt_price_fields(v: Variant):
        try:
            promo_active = bool(v.is_promo_active())
        except Exception:
            promo_active = False

        if promo_active:
            price = float(
                getattr(v, "promo_price", None)
                or getattr(v, "sale_price", 0)
                or 0
            )
        else:
            price = float(getattr(v, "sale_price", 0) or 0)

        mrp = float(getattr(v, "mrp", 0) or 0)
        compare = mrp if mrp > 0 else price
        return price, compare

    def dump_variant(v: Variant):
        price, compare_at = _fbt_price_fields(v)

        # correct PDP URL
        try:
            base_url = _canonical_product_url(v.product)
        except Exception:
            base_url = (
                v.product.get_absolute_url()
                if hasattr(v.product, "get_absolute_url")
                else ""
            )
        link = f"{base_url}?variant={v.id}" if base_url else f"?variant={v.id}"

        return {
            "product_id": v.product_id,
            "product_title": getattr(v.product, "title", ""),
            "variant_id": v.id,
            "variant_title": _variant_label(v),  # 👈 Color / Size label
            "price": price,
            "compare_at_price": compare_at,  # MRP for strike-through
            "in_stock": (getattr(v, "stock_qty", 0) or 0) > 0
            or bool(getattr(v, "backorder_allowed", False)),
            "image": getattr(v, "get_main_image_url", lambda: "")(),
            "product_url": link,
            "source": "variant_link"
            if any(l.target_variant_id == v.id for l in links)
            else "product_default",
        }

    # Build payload with MAIN variant at the TOP of the list and flagged
    sv = dump_variant(source)
    sv["_isMain"] = True

    payload = {
        "source_variant": sv,                                     # full main-variant data
        "items": [sv] + [dump_variant(v) for v in items[:MAX_ITEMS]],  # main first, then FBTs
        "max_items": MAX_ITEMS,
    }
    return JsonResponse(payload)


# =========================
# ===== COUPONS: V1 =======
# =========================
TWOPLACES = Decimal("0.01")
REFERENCE_SUBTOTAL = Decimal("1000.00")  # for expected-savings sort

def _dround(val):
    try:
        return (Decimal(val) if not isinstance(val, Decimal) else val).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")

# --- safe model getters (already imported above) ---
def _get_product(pk): return Product.objects.filter(pk=pk).first() if pk else None
def _get_variant(pk): return Variant.objects.filter(pk=pk).first() if pk else None

# ---- cart read (very forgiving) ----
def _cart_lines(request):
    lines = []
    cart = request.session.get("cart") or {}
    items = cart.get("items") or []
    for it in items:
        prod = _get_product(it.get("product_id"))
        var = _get_variant(it.get("variant_id"))
        qty = int(it.get("qty") or 1)
        price = (
            getattr(var, "promo_price", None)
            or getattr(var, "sale_price", None)
            or getattr(var, "price", None)
            or it.get("unit_price")
            or getattr(prod, "price", None)
            or getattr(prod, "mrp", 0)
            or 0
        )
        price = _dround(price)
        lines.append({"product": prod, "variant": var, "qty": qty, "line_subtotal": _dround(price * qty)})
    return lines

def _cart_subtotal(lines): return _dround(sum((l["line_subtotal"] for l in lines), Decimal("0")))

def _product_categories(product):
    if not product: return []
    out = []
    if getattr(product, "category", None): out.append(product.category)
    if hasattr(product, "categories"):
        try: out.extend(list(product.categories.all()))
        except Exception: pass
    return out

# ---------- eligibility / math ----------
def _scope_ok_for_line(line, c):
    prod, var = line.get("product"), line.get("variant")
    try:
        if hasattr(c, "included_variants") and c.included_variants.exists():
            if not (var and var in c.included_variants.all()): return False
        elif hasattr(c, "included_products") and c.included_products.exists():
            if not (prod and prod in c.included_products.all()): return False
        elif hasattr(c, "included_categories") and c.included_categories.exists():
            cats = _product_categories(prod)
            if not any(cat in c.included_categories.all() for cat in cats): return False
    except Exception:
        pass
    try:
        if hasattr(c, "excluded_variants") and c.excluded_variants.exists() and var in c.excluded_variants.all(): return False
        if hasattr(c, "excluded_products") and c.excluded_products.exists() and prod in c.excluded_products.all(): return False
        if hasattr(c, "excluded_categories") and c.excluded_categories.exists():
            cats = _product_categories(prod)
            if any(cat in c.excluded_categories.all() for cat in cats): return False
    except Exception:
        pass
    return True

def _scope_subtotal(lines, c):
    try:
        if c.applies_to == Coupon.SCOPE_CART:
            return _cart_subtotal(lines)
    except Exception:
        return _cart_subtotal(lines)
    elig = [l for l in lines if _scope_ok_for_line(l, c)]
    return _dround(sum((l["line_subtotal"] for l in elig), Decimal("0")))

def _discount_amount(c, scope_subtotal):
    if scope_subtotal <= 0: return Decimal("0.00")
    try:
        if c.type == Coupon.TYPE_PERCENT:
            d = (scope_subtotal * Decimal(c.value) / Decimal("100"))
            if getattr(c, "max_discount_amount", None): d = min(d, Decimal(c.max_discount_amount))
            return _dround(d)
        elif c.type == Coupon.TYPE_FLAT:
            return _dround(min(Decimal(c.value), scope_subtotal))
    except Exception:
        pass
    return Decimal("0.00")

def _min_cart_ok(c, subtotal):
    try:
        return (c.min_cart_subtotal is None) or (Decimal(subtotal) >= Decimal(c.min_cart_subtotal))
    except Exception:
        return True

def _validate_common(request, c, lines):
    now = timezone.now()
    try:
        if not c.is_active_now(now): return (False, "Coupon not active or expired")
    except Exception:
        try:
            if c.starts_at and c.starts_at > now: return (False, "Not started")
            if c.ends_at and c.ends_at < now: return (False, "Expired")
        except Exception:
            pass
    subtotal = _cart_subtotal(lines)
    if not _min_cart_ok(c, subtotal):
        try: need = _dround(Decimal(c.min_cart_subtotal) - subtotal)
        except Exception: need = Decimal("0.00")
        return (False, f"Add ₹{need} more to use this coupon")
    if hasattr(c, "applies_to") and getattr(c, "applies_to") in (
        "products", "categories", "PRODUCTS", "CATEGORIES",
        getattr(Coupon, "SCOPE_PRODUCTS", None), getattr(Coupon, "SCOPE_CATEGORIES", None)
    ):
        if _scope_subtotal(lines, c) <= 0: return (False, "Not applicable on these items")
    return (True, "")

def _expected_savings(c):
    try:
        if c.type == Coupon.TYPE_FLAT: return _dround(c.value)
        if c.type == Coupon.TYPE_PERCENT:
            raw = REFERENCE_SUBTOTAL * Decimal(c.value) / Decimal("100")
            if getattr(c, "max_discount_amount", None): raw = min(raw, Decimal(c.max_discount_amount))
            return _dround(raw)
    except Exception:
        pass
    return Decimal("0.00")

def _title_for(c):
    t = (getattr(c, "title", "") or "").strip()
    if t: return t
    try:
        if c.type == Coupon.TYPE_PERCENT:
            return f"{int(Decimal(c.value))}% OFF"
        return f"₹{_dround(c.value)} OFF"
    except Exception:
        return "Special Offer"

def _coupon_qs_public():
    if not Coupon: return []
    try:
        now = timezone.now()
        qs = Coupon.objects.all()
        if hasattr(Coupon, "is_public"): qs = qs.filter(is_public=True)
        if hasattr(Coupon, "show_in_listing"): qs = qs.filter(show_in_listing=True)
        qs = qs.filter(Q(starts_at__isnull=True) | Q(starts_at__lte=now)).filter(Q(ends_at__isnull=True) | Q(ends_at__gte=now))
        return list(qs)
    except Exception:
        return []

def _coupon_qs_all():
    if not Coupon: return []
    try:
        return list(Coupon.objects.all())
    except Exception:
        return []


# NEW: PDP targeting rule — only coupons explicitly mapped to product/variant/category
def _is_pdp_targeted(c, product: Product | None, var: Variant | None) -> bool:
    """
    PDP ke liye 'targeted' coupon: sirf wahi jo explicitly is product/variant/category par mapped ho.
    CART-scope coupons yahan nahi dikhte; aur jisme koi explicit include hi na ho, wo PDP se skip.
    """
    try:
        # Cart-scope skip
        if getattr(c, "applies_to", None) == getattr(Coupon, "SCOPE_CART", None):
            return False

        # Exclusions first
        if var and hasattr(c, "excluded_variants") and c.excluded_variants.filter(pk=var.pk).exists():
            return False
        if product and hasattr(c, "excluded_products") and c.excluded_products.filter(pk=product.pk).exists():
            return False
        if product and product.category and hasattr(c, "excluded_categories") and c.excluded_categories.filter(pk=product.category.pk).exists():
            return False

        # Explicit includes (priority: variant > product > category)
        if hasattr(c, "included_variants") and c.included_variants.exists():
            return bool(var) and c.included_variants.filter(pk=getattr(var, "pk", None)).exists()

        if hasattr(c, "included_products") and c.included_products.exists():
            return bool(product) and c.included_products.filter(pk=getattr(product, "pk", None)).exists()

        if hasattr(c, "included_categories") and c.included_categories.exists():
            cat = getattr(product, "category", None)
            return bool(cat) and c.included_categories.filter(pk=getattr(cat, "pk", None)).exists()

        # No includes configured => not targeted for PDP
        return False

    except Exception:
        return False


# -------- PUBLIC LIST (offers page) --------
@require_GET
def coupons_list(request):
    items = []
    for c in _coupon_qs_public():
        items.append({
            "code": c.code,
            "title": _title_for(c),
            "description": getattr(c, "notes", "") or getattr(c, "subtitle", "") or getattr(c, "description", "") or "",
            "expected_savings_hint": str(_expected_savings(c)),
            "ends_at": c.ends_at.isoformat() if getattr(c, "ends_at", None) else None,
            "allow_deeplink": getattr(c, "allow_deeplink", False),
            "vanity_slug": getattr(c, "vanity_slug", "") or None,
        })
    items.sort(key=lambda x: (Decimal(x["expected_savings_hint"]) * -1, x["ends_at"] or "9999-12-31T23:59:59"))
    return JsonResponse({"items": items})


# -------- PDP (product) --------
@require_GET
def coupons_for_product(request):
    """
    Returns {"items":[{code,title,description,savings_amount,ok,reason},...]}
    PDP par sirf targeted (product/variant/category) coupons aayenge; CART-scope & unmapped public coupons skip.
    """
    product_id = request.GET.get("product_id")
    qty = int(request.GET.get("qty") or 1)
    variant_id = request.GET.get("variant")

    if not product_id:
        return JsonResponse({"items": []})

    product = _get_product(product_id)
    var = _get_variant(variant_id) if variant_id else None

    # price fallback chain for savings calc
    base_price = (
        getattr(var, "promo_price", None)
        or getattr(var, "sale_price", None)
        or getattr(var, "price", None)
        or getattr(product, "price", None)
        or getattr(product, "mrp", 0)
        or 0
    )
    unit = _dround(base_price)
    lines = [{"product": product, "variant": var, "qty": qty, "line_subtotal": _dround(unit * max(1, qty))}]

    # coupon pool → PDP-targeted filter
    all_coupons = _coupon_qs_all() or _coupon_qs_public()
    targeted = [c for c in all_coupons if _is_pdp_targeted(c, product, var)]
    if not targeted:
        return JsonResponse({"items": []})

    items = []
    for c in targeted:
        ok, reason = _validate_common(request, c, lines)
        scope_sub = _scope_subtotal(lines, c)
        disc = _discount_amount(c, scope_sub) if ok else Decimal("0.00")
        items.append({
            "code": c.code,
            "title": _title_for(c),
            "description": getattr(c, "notes", "") or getattr(c, "description", "") or "",
            "savings_amount": float(disc),
            "ok": bool(ok),
            "reason": ("" if ok else reason),
        })

    # eligible first → highest savings → earliest expiry
    def sort_key(x):
        cobj = next((cx for cx in targeted if cx.code == x["code"]), None)
        ends = cobj.ends_at.isoformat() if (cobj and getattr(cobj, "ends_at", None)) else "9999-12-31T23:59:59"
        return (0 if x["ok"] else 1, -Decimal(str(x["savings_amount"])), ends)

    items.sort(key=sort_key)
    return JsonResponse({"items": items})


# -------- Cart --------
@require_GET
def coupons_for_cart(request):
    lines = _cart_lines(request)
    all_coupons = _coupon_qs_all() or _coupon_qs_public()
    items = []
    for c in all_coupons:
        ok, reason = _validate_common(request, c, lines)
        scope_sub = _scope_subtotal(lines, c)
        disc = _discount_amount(c, scope_sub) if ok else Decimal("0.00")
        items.append({
            "code": c.code,
            "title": _title_for(c),
            "description": getattr(c, "notes", "") or getattr(c, "subtitle", "") or getattr(c, "description", "") or "",
            "savings_amount": float(disc),
            "ok": bool(ok),
            "reason": ("" if ok else reason),
        })

    def sort_key(x):
        cobj = next((cx for cx in all_coupons if cx.code == x["code"]), None)
        ends = cobj.ends_at.isoformat() if (cobj and getattr(cobj, "ends_at", None)) else "9999-12-31T23:59:59"
        return (-Decimal(str(x["savings_amount"])), ends)

    items.sort(key=sort_key)
    applied_block = None
    applied_code = request.session.get("applied_coupon_code")
    if applied_code:
        applied_block = next((i for i in items if i["code"] == applied_code), None)
        if applied_block:
            items = [applied_block] + [i for i in items if i["code"] != applied_code]
    return JsonResponse({"applied": applied_block, "suggestions": items})


# -------- Apply (POST) --------
@csrf_exempt
@require_POST
def coupon_apply(request):
    # allow JSON body {code:"..."} or form
    code = (request.POST.get("code") or "").strip().upper()
    if request.content_type and "json" in request.content_type and not code:
        try:
            payload = json.loads((request.body or b"").decode("utf-8") or "{}")
            code = (payload.get("code") or "").upper().strip()
        except Exception:
            pass

    if not code or not Coupon:
        return JsonResponse({"applied": False, "reason": "Invalid code"}, status=400)

    c = next((cx for cx in (_coupon_qs_all() or _coupon_qs_public()) if cx.code.upper() == code), None)
    if not c:
        return JsonResponse({"applied": False, "reason": "Invalid code"}, status=400)

    # Validate on current cart lines
    lines = _cart_lines(request)
    ok, reason = _validate_common(request, c, lines)
    if not ok:
        return JsonResponse({"applied": False, "reason": reason}, status=400)

    request.session["applied_coupon_code"] = code
    request.session.modified = True
    scope_sub = _scope_subtotal(lines, c)
    return JsonResponse({"applied": True, "code": code, "savings_amount": float(_discount_amount(c, scope_sub))})


# ---- Thin wrappers to match template URL names ----
@require_GET
def api_coupons_for_product(request):  # name used in templates
    return coupons_for_product(request)

@csrf_exempt
@require_POST
def api_coupons_apply(request):        # name used in templates
    return coupon_apply(request)


# =========================
# ===== SEARCH: NEW =======
# =========================

import re, json
from django.http import JsonResponse, HttpResponseBadRequest
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.csrf import csrf_exempt
from django.db import models
from django.db.models import Q, F, Value, IntegerField, DecimalField, Subquery, OuterRef
from django.db.models.functions import Coalesce, Greatest
from django.apps import apps
from django.shortcuts import render, get_object_or_404


# ---------- tiny utils ----------
def _qnorm(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "")).strip().lower()

def _model_exists(app_label, model_name):
    try:
        apps.get_model(app_label, model_name)
        return True
    except LookupError:
        return False


# ---------- stats helpers ----------
def _ensure_stats(product):
    ProductStats = apps.get_model("shop", "ProductStats")
    try:
        return product.stats
    except ProductStats.DoesNotExist:
        return ProductStats.objects.create(product=product)

def _ensure_vstats(variant):
    VariantStats = apps.get_model("shop", "VariantStats")
    try:
        return variant.stats
    except VariantStats.DoesNotExist:
        return VariantStats.objects.create(variant=variant)


# ---------- PRODUCT-level queryset (full results page) ----------
def _base_search_queryset(q: str):
    """
    Filter + rank PRODUCTS. Used by /search (HTML page).
    Uses best matching VARIANT signals for ranking & price display.
    """
    Product = apps.get_model("shop", "Product")
    Variant = apps.get_model("shop", "Variant")

    qnorm = _qnorm(q)
    qs = Product.objects.filter(is_active=True)

    # dynamic category FK title/name
    cat_lookup = None
    try:
        cat_field = Product._meta.get_field("category")
        rel_model = cat_field.remote_field.model
        rel_names = {f.name for f in rel_model._meta.get_fields() if hasattr(f, "attname")}
        if "title" in rel_names:
            cat_lookup = "category__title__icontains"
        elif "name" in rel_names:
            cat_lookup = "category__name__icontains"
    except Exception:
        pass

    # filter only when query present
    if qnorm:
        cond = Q(title__icontains=qnorm) | Q(description__icontains=qnorm) | Q(slug__icontains=qnorm)
        if cat_lookup:
            cond |= Q(**{cat_lookup: qnorm})
        qs = qs.filter(cond)

        qs = qs.annotate(
            rel=models.Case(
                models.When(title__iexact=qnorm, then=Value(60)),
                models.When(title__istartswith=qnorm, then=Value(40)),
                models.When(title__icontains=qnorm, then=Value(20)),
                default=Value(10),
                output_field=IntegerField(),
            )
        )
    else:
        qs = qs.annotate(rel=Value(0, output_field=IntegerField()))

    # shortlist best VARIANT per product for this query
    vbase = Variant.objects.filter(product=OuterRef("pk"), is_active=True)

    if qnorm:
        # try attributes_text if exists
        try:
            Variant._meta.get_field("attributes_text")
            vrel = models.Case(
                models.When(sku__iexact=qnorm, then=Value(60)),
                models.When(sku__istartswith=qnorm, then=Value(40)),
                models.When(sku__icontains=qnorm, then=Value(25)),
                models.When(attributes_text__icontains=qnorm, then=Value(20)),
                default=Value(5),
                output_field=IntegerField(),
            )
        except Exception:
            vrel = models.Case(
                models.When(sku__iexact=qnorm, then=Value(60)),
                models.When(sku__istartswith=qnorm, then=Value(40)),
                models.When(sku__icontains=qnorm, then=Value(25)),
                default=Value(5),
                output_field=IntegerField(),
            )
        vbase = vbase.annotate(vrel=vrel)
    else:
        vbase = vbase.annotate(vrel=Value(0, output_field=IntegerField()))

    # popularity (optional if stats exist) + effective price
    if _model_exists("shop", "VariantStats"):
        vbase = vbase.annotate(
            vorders=Coalesce(F("stats__orders"), Value(0)),
            vatc=Coalesce(F("stats__add_to_cart"), Value(0)),
            vclicks=Coalesce(F("stats__clicks"), Value(0)),
            vviews=Coalesce(F("stats__views"), Value(0)),
        )
        vpop_expr = F("vorders")*5 + F("vatc")*3 + F("vclicks")*2 + F("vviews")
    else:
        vpop_expr = Value(0)

    vbase = vbase.annotate(
        vpop=vpop_expr,
        vprice=Coalesce(F("promo_price"), F("sale_price"), F("mrp")),
    ).order_by("-vrel", "-vpop", "-id")

    qs = qs.annotate(
        best_variant_id=Subquery(vbase.values("id")[:1]),
        best_variant_rel=Subquery(vbase.values("vrel")[:1]),
        best_variant_pop=Subquery(vbase.values("vpop")[:1]),
        best_variant_price=Subquery(vbase.values("vprice")[:1], output_field=DecimalField(max_digits=12, decimal_places=2)),
        best_variant_mrp=Subquery(vbase.values("mrp")[:1], output_field=DecimalField(max_digits=12, decimal_places=2)),
    )

    # product popularity (optional)
    if _model_exists("shop", "ProductStats"):
        qs = qs.annotate(
            orders=Coalesce(F("stats__orders"), Value(0)),
            atc=Coalesce(F("stats__add_to_cart"), Value(0)),
            clicks=Coalesce(F("stats__clicks"), Value(0)),
            views=Coalesce(F("stats__views"), Value(0)),
        )
        pop_expr = F("orders")*5 + F("atc")*3 + F("clicks")*2 + F("views")
    else:
        pop_expr = Value(0)

    qs = qs.annotate(pop=pop_expr)
    final_rel = Greatest(F("rel"), Coalesce(F("best_variant_rel"), Value(0)))
    final_pop = Greatest(F("pop"), Coalesce(F("best_variant_pop"), Value(0)))

    return qs.annotate(final_rel=final_rel, final_pop=final_pop).order_by("-final_rel", "-final_pop", "-id")


# ---------- nice label for variant ----------
def _variant_label(v):
    """
    e.g. 'Black & White / 26 inch'. Uses attributes_text if present.
    """
    try:
        if getattr(v, "attributes_text", None):
            return v.attributes_text
    except Exception:
        pass
    bits = []
    # color_primary & color_secondary objects (title/name) if exist
    try:
        cp = getattr(v, "color_primary", None)
        if cp:
            bits.append(getattr(cp, "title", None) or getattr(cp, "name", None) or str(cp))
    except Exception:
        pass
    try:
        cs = getattr(v, "color_secondary", None)
        if cs:
            sec = getattr(cs, "title", None) or getattr(cs, "name", None) or str(cs)
            if sec:
                if bits:
                    bits[-1] = f"{bits[-1]} & {sec}"
                else:
                    bits.append(sec)
    except Exception:
        pass
    try:
        sz = getattr(v, "size", None) or getattr(v, "size_id", None)
        if sz:
            s = getattr(sz, "title", None) or getattr(sz, "name", None) or str(sz)
            if s:
                bits.append(s)
    except Exception:
        pass
    return " / ".join(bits)


# ---------- VARIANT-level queryset (popup) ----------
def _variant_search_queryset(q: str):
    """
    Filter + rank VARIANTS. Used by /api/search (popup).
    """
    Product = apps.get_model("shop", "Product")
    Variant = apps.get_model("shop", "Variant")

    qnorm = _qnorm(q)
    vqs = Variant.objects.select_related("product").filter(product__is_active=True, is_active=True)

    # dynamic category lookup via product
    cat_lookup = None
    try:
        cat_field = Product._meta.get_field("category")
        rel_model = cat_field.remote_field.model
        rel_names = {f.name for f in rel_model._meta.get_fields() if hasattr(f, "attname")}
        if "title" in rel_names:
            cat_lookup = "product__category__title__icontains"
        elif "name" in rel_names:
            cat_lookup = "product__category__name__icontains"
    except Exception:
        pass

    if qnorm:
        cond = Q(product__title__icontains=qnorm) | Q(sku__icontains=qnorm)
        # attributes_text if exists
        try:
            Variant._meta.get_field("attributes_text")
            cond |= Q(attributes_text__icontains=qnorm)
        except Exception:
            pass
        if cat_lookup:
            cond |= Q(**{cat_lookup: qnorm})
        vqs = vqs.filter(cond)

        rel = models.Case(
            models.When(product__title__iexact=qnorm, then=Value(70)),
            models.When(product__title__istartswith=qnorm, then=Value(50)),
            models.When(product__title__icontains=qnorm, then=Value(35)),
            models.When(sku__iexact=qnorm, then=Value(60)),
            models.When(sku__istartswith=qnorm, then=Value(40)),
            models.When(sku__icontains=qnorm, then=Value(25)),
            default=Value(10),
            output_field=IntegerField(),
        )
        try:
            Variant._meta.get_field("attributes_text")
            rel = models.Case(
                models.When(product__title__iexact=qnorm, then=Value(70)),
                models.When(product__title__istartswith=qnorm, then=Value(50)),
                models.When(product__title__icontains=qnorm, then=Value(35)),
                models.When(attributes_text__icontains=qnorm, then=Value(30)),
                models.When(sku__iexact=qnorm, then=Value(60)),
                models.When(sku__istartswith=qnorm, then=Value(40)),
                models.When(sku__icontains=qnorm, then=Value(25)),
                default=Value(10),
                output_field=IntegerField(),
            )
        except Exception:
            pass
        vqs = vqs.annotate(vrel=rel)
    else:
        vqs = vqs.annotate(vrel=Value(0, output_field=IntegerField()))

    # popularity & effective price on variant
    if _model_exists("shop", "VariantStats"):
        vqs = vqs.annotate(
            vorders=Coalesce(F("stats__orders"), Value(0)),
            vatc=Coalesce(F("stats__add_to_cart"), Value(0)),
            vclicks=Coalesce(F("stats__clicks"), Value(0)),
            vviews=Coalesce(F("stats__views"), Value(0)),
            vpop=F("vorders")*5 + F("vatc")*3 + F("vclicks")*2 + F("vviews"),
        )
    else:
        vqs = vqs.annotate(vpop=Value(0))

    vqs = vqs.annotate(
        vprice=Coalesce(F("promo_price"), F("sale_price"), F("mrp")),
        vmrp=F("mrp"),
    ).order_by("-vrel", "-vpop", "-id")

    return vqs


# ---------- popup payloads ----------
def _variant_popup_payload(variants):
    out = []
    for v in variants:
        p = v.product

        # base URL
        try:
            base_url = _canonical_product_url(p)
            url = f"{base_url}?variant={v.id}"
        except Exception:
            url = f"/{p.slug}?variant={v.id}"

        # variant gallery
        imgs = []
        try:
            if hasattr(v, "images"):
                imgs = list(v.images.all()[:2])
        except Exception:
            pass

        # thumb = variant.image → gallery[0] → product.primary_image → product.images[0]
        thumb = ""
        if getattr(v, "image", None):
            thumb = v.image.url
        elif imgs and getattr(imgs[0], "image", None):
            thumb = imgs[0].image.url
        elif getattr(p, "primary_image", None):
            thumb = p.primary_image.url
        elif getattr(p, "images", None) and p.images.all().first():
            thumb = p.images.all().first().image.url
        else:
            thumb = ""   # 👈 no placeholder, just empty

        # thumb2 = gallery[1] → fallback = thumb
        thumb2 = ""
        if len(imgs) > 1 and getattr(imgs[1], "image", None):
            thumb2 = imgs[1].image.url
        else:
            thumb2 = thumb

        # price/mrp as NUMBERS (not strings!)
        price = getattr(v, "vprice", None)
        mrp   = getattr(v, "vmrp", None)
        try:
            price_num = float(price) if price is not None else None
        except Exception:
            price_num = None
        try:
            mrp_num = float(mrp) if mrp is not None else None
        except Exception:
            mrp_num = None

        promo = False
        try:
            promo = bool(mrp_num and price_num and price_num < mrp_num)
        except Exception:
            promo = False

        out.append({
            "id": p.id,
            "variant_id": v.id,
            "title": p.title,
            "variant_label": _variant_label(v),
            "url": url,
            "thumb": thumb,
            "thumb2": thumb2,
            "price": price_num,
            "mrp": mrp_num,
            "promo": promo,
        })
    return out



def _product_popup_payload(products):
    """
    (fallback) product items payload – not used now but handy if needed.
    """
    out = []
    Variant = apps.get_model("shop", "Variant")
    for p in products:
        v = None
        vid = getattr(p, "best_variant_id", None)
        if vid:
            try:
                v = Variant.objects.get(pk=vid)
            except Variant.DoesNotExist:
                v = None

        url = ""
        try:
            if v and hasattr(v, "get_absolute_url"):
                url = v.get_absolute_url()
        except Exception:
            pass
        if not url:
            try:
                url = p.get_absolute_url()
            except Exception:
                url = "#"

        thumb = ""
        try:
            if v and getattr(v, "image", None):
                thumb = v.image.url
        except Exception:
            pass
        if not thumb:
            try:
                if getattr(p, "primary_image", None):
                    thumb = p.primary_image.url
            except Exception:
                pass

        price = getattr(p, "best_variant_price", None)
        mrp = getattr(p, "best_variant_mrp", None)
        if price is None:
            try:
                if hasattr(p, "effective_price"):
                    price = p.effective_price()
            except Exception:
                pass
        if mrp is None:
            mrp = getattr(p, "mrp", None)

        vlabel = ""
        if v:
            vlabel = getattr(v, "attributes_text", "") or getattr(v, "sku", "")

        promo = False
        try:
            promo = bool(mrp and price and float(price) < float(mrp))
        except Exception:
            promo = False

        out.append({
            "id": p.id,
            "variant_id": vid or None,
            "title": p.title,
            "variant_label": vlabel,
            "url": url,
            "thumb": thumb,
            "price": ("" if price is None else str(price)),
            "mrp": ("" if mrp is None else str(mrp)),
            "promo": promo,
        })
    return out


# ---------- API + page views ----------
@require_GET
def api_search(request):
    q = (request.GET.get("q") or "").strip()
    try:
        limit = max(1, int(request.GET.get("limit", 5)))
    except Exception:
        limit = 5

    vqs = _variant_search_queryset(q)
    total = vqs.count()
    items = list(vqs[:limit]) if total else []
    data = _variant_popup_payload(items)

    # make image URLs absolute (only if they are relative)
    base = request.build_absolute_uri("/")
    for it in data:
        t = it.get("thumb") or ""
        if t and not t.startswith("http"):
            if t.startswith("/"):
                it["thumb"] = request.build_absolute_uri(t)
            else:
                it["thumb"] = base.rstrip("/") + "/" + t.lstrip("/")

    return JsonResponse({"items": data, "total": int(total)})

@require_GET
def search_results(request):
    """
    Full results HTML page → VARIANTS (same logic as popup).
    """
    q = request.GET.get("q", "")
    vqs = _variant_search_queryset(q)

    total = vqs.count()
    page = max(1, int(request.GET.get("page", 1)))
    per_page = 12
    start, end = (page - 1) * per_page, page * per_page

    variants = list(vqs[start:end])

    # reuse popup builder so image/url/price logic stays identical
    items = _variant_popup_payload(variants)

    # make image URLs absolute (nice to have when served via MEDIA_URL)
    base = request.build_absolute_uri("/")
    for it in items:
        t = it.get("thumb") or ""
        if t and not t.startswith("http"):
            it["thumb"] = (request.build_absolute_uri(t) if t.startswith("/") else base.rstrip("/") + "/" + t.lstrip("/"))

    more = end < total

    ctx = {
        "q": q,
        "total": total,
        "items": items,     # 👈 template will loop over this
        "page": page,
        "more": more,
        "next_page": page + 1 if more else None,
    }
    return render(request, "search/results.html", ctx)


@csrf_exempt
@require_POST
def api_track_click(request):
    """
    Track clicks from popup (product + optional variant).
    """
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except Exception:
        return HttpResponseBadRequest("Invalid JSON")

    q = _qnorm(payload.get("q", ""))
    pid = int(payload.get("product_id", 0) or 0)
    vid = int(payload.get("variant_id", 0) or 0)
    if pid <= 0:
        return HttpResponseBadRequest("Missing product_id")

    Product = apps.get_model("shop", "Product")
    product = get_object_or_404(Product, id=pid)

    # product stats
    stats = _ensure_stats(product)
    ProductStats = apps.get_model("shop", "ProductStats")
    ProductStats.objects.filter(pk=stats.pk).update(clicks=F("clicks") + 1)

    # per-query product click
    SearchClick = apps.get_model("shop", "SearchClick")
    obj, _ = SearchClick.objects.get_or_create(query=q, product=product)
    SearchClick.objects.filter(pk=obj.pk).update(clicks=F("clicks") + 1)

    # variant stats (if provided)
    if vid > 0:
        Variant = apps.get_model("shop", "Variant")
        variant = get_object_or_404(Variant, id=vid, product=product)

        vstats = _ensure_vstats(variant)
        VariantStats = apps.get_model("shop", "VariantStats")
        VariantStats.objects.filter(pk=vstats.pk).update(clicks=F("clicks") + 1)

        SCV = apps.get_model("shop", "SearchClickVariant")
        vobj, _ = SCV.objects.get_or_create(query=q, variant=variant)
        SCV.objects.filter(pk=vobj.pk).update(clicks=F("clicks") + 1)

    return JsonResponse({"ok": True})

@require_GET
def api_categories(request):
    """
    Return categories for quick links (label only, link '#').
    """
    Category = apps.get_model("shop", "Category")

    # top-level popular categories; adjust ordering/limit as you like
    qs = Category.objects.all().order_by("id")[:12]

    items = []
    for c in qs:
        label = getattr(c, "title", None) or getattr(c, "name", None) or str(c)
        items.append({"id": c.id, "label": label})
    return JsonResponse({"items": items})


# =========================
# ======== SHOP ===========
# =========================

def _descendant_map():
    """Return (by_parent dict) for active categories."""
    all_cats = Category.objects.filter(is_active=True).only("id", "parent_id", "slug", "name", "image")
    by_parent = {}
    for c in all_cats:
        by_parent.setdefault(c.parent_id, []).append(c)
    return by_parent

def _descendants_ids(cat_id, by_parent):
    stack = [cat_id]
    res = []
    while stack:
        cid = stack.pop()
        kids = by_parent.get(cid, [])
        for k in kids:
            res.append(k.id)
            stack.append(k.id)
    return res

def shop_index(request):
    """
    Shop page:
    - Show all PARENT categories (is_active=True) ordered by display_order
    - Show CHILD categories that have >=1 products (incl. descendants), ordered by display_order
    """

    # parents (order by display_order, fallback to id)
    parents = list(
        Category.objects.filter(is_active=True, parent__isnull=True)
        .only("id", "name", "slug", "image", "display_order")
        .annotate(_do=Coalesce("display_order", Value(999999), output_field=IntegerField()))
        .order_by("_do", "id")
    )

    # build child list but hide zero-product childs (descendants-inclusive)
    by_parent = _descendant_map()

    # direct product counts per category (fast)
    direct_counts = dict(
        Product.objects.filter(is_active=True)
        .values_list("category_id")
        .annotate(c=Count("id"))
        .values_list("category_id", "c")
    )

    non_empty_children = []
    for p in parents:
        children = by_parent.get(p.id, [])
        for ch in children:
            pool_ids = [ch.id] + _descendants_ids(ch.id, by_parent)
            total = sum(direct_counts.get(cid, 0) for cid in pool_ids)
            if total > 0:
                non_empty_children.append(ch)

    # order children by display_order (fallback to id)
    # ensure display_order is fetched to avoid extra queries
    child_ids = [c.id for c in non_empty_children]
    child_qs = (
        Category.objects.filter(id__in=child_ids)
        .only("id", "name", "slug", "image", "display_order")
        .annotate(_do=Coalesce("display_order", Value(999999), output_field=IntegerField()))
        .order_by("_do", "id")
    )
    # keep list in final, preserving the sorted queryset order
    non_empty_children_sorted = list(child_qs)

    ctx = {
        "parent_cats": parents,
        "child_cats": non_empty_children_sorted,
    }
    return render(request, "shop/shop.html", ctx)


# =========================
# ====== CATAGORIES =======
# =========================

# ---------- Helpers ----------

def _get_category_or_404(parent_slug, child_slug=None):
    if child_slug:
        try:
            cat = Category.objects.select_related("parent").get(
                slug=child_slug, parent__slug=parent_slug, is_active=True
            )
        except Category.DoesNotExist:
            raise Http404("Category not found")
    else:
        try:
            cat = Category.objects.select_related("parent").get(
                slug=parent_slug, parent__isnull=True, is_active=True
            )
        except Category.DoesNotExist:
            cat = get_object_or_404(Category, slug=parent_slug, is_active=True)
            if cat.parent_id:
                raise Http404("Category not found")
    return cat


def _children_for_grid(parent_cat):
    """
    Parent page ki subcategory grid: sirf wahi child dikhaye jisme
    (child + uske descendants) me >=1 direct products hon.
    """
    all_cats = Category.objects.filter(is_active=True).only("id", "parent_id", "slug", "name", "image")
    by_parent = {}
    for c in all_cats:
        by_parent.setdefault(c.parent_id, []).append(c)

    def descendants_ids(cat_id):
        stack = [cat_id]
        res = []
        while stack:
            cid = stack.pop()
            kids = by_parent.get(cid, [])
            for k in kids:
                res.append(k.id)
                stack.append(k.id)
        return res

    direct_counts = dict(
        Product.objects.filter(is_active=True)
        .values_list("category_id")
        .annotate(c=Count("id"))
        .values_list("category_id", "c")
    )

    kids = by_parent.get(parent_cat.id, [])
    grid = []
    for child in kids:
        pool_ids = [child.id] + descendants_ids(child.id)
        total = sum(direct_counts.get(cid, 0) for cid in pool_ids)
        if total > 0:
            grid.append(child)
    return grid


# ---------- VARIANT filters / facets / paginate ----------

def _apply_variant_filters(vqs, request):
    """
    Variant-level filtering + sorting
    Price = promo -> sale -> mrp (eff_price)
    """
    # effective price
    vqs = vqs.annotate(
        eff_price=Coalesce(F("promo_price"), F("sale_price"), F("mrp")),
        vmrp=F("mrp"),
    )

    # ---- price range ----
    min_price = (request.GET.get("min") or "").strip()
    max_price = (request.GET.get("max") or "").strip()
    if min_price:
        try:
            vqs = vqs.filter(eff_price__gte=float(min_price))
        except ValueError:
            pass
    if max_price:
        try:
            vqs = vqs.filter(eff_price__lte=float(max_price))
        except ValueError:
            pass

    # ---- color (PRIMARY/SECONDARY; case-insensitive by name; NO slug) ----
    def _csv(v): return [s.strip() for s in v.split(",") if s.strip()]

    cq = (request.GET.get("color") or "").strip()
    if cq:
        colors = _csv(cq)
        col_q = Q()
        for name in colors:
            col_q |= Q(color_primary__name__iexact=name) | Q(color_secondary__name__iexact=name)
        if col_q:
            vqs = vqs.filter(col_q)

    # ---- size (case-insensitive by name; NO slug) ----
    sq = (request.GET.get("size") or "").strip()
    if sq:
        sizes = _csv(sq)
        size_q = Q()
        for name in sizes:
            size_q |= Q(size__name__iexact=name)
        if size_q:
            vqs = vqs.filter(size_q)

    # ---- in-stock ----
    if request.GET.get("in_stock") in ("1", "true", "True"):
        vqs = vqs.filter(Q(stock_qty__gt=0) | Q(in_stock=True) | Q(backorder_allowed=True))

    # ---- sorting ----
    sort = (request.GET.get("sort") or "").lower()
    if sort == "price_asc":
        vqs = vqs.order_by("eff_price", "id")
    elif sort == "price_desc":
        vqs = vqs.order_by("-eff_price", "-id")
    elif sort == "newest":
        vqs = vqs.order_by("-created_at" if hasattr(Variant, "created_at") else "-id")
    elif sort == "popular" and hasattr(Variant, "stats"):
        vqs = vqs.annotate(
            vorders=Coalesce(F("stats__orders"), Value(0)),
            vatc=Coalesce(F("stats__add_to_cart"), Value(0)),
            vclicks=Coalesce(F("stats__clicks"), Value(0)),
            vviews=Coalesce(F("stats__views"), Value(0)),
            vpop=F("vorders")*5 + F("vatc")*3 + F("vclicks")*2 + F("vviews"),
        ).order_by("-vpop", "-id")
    else:
        vqs = vqs.order_by("-id")

    return vqs


def _collect_variant_facets(vbase, request):
    """
    Dynamic facets from variants under current category.
    Facets respect price/in_stock; color/size selection ko ignore karte hain.
    """
    vqs = vbase

    # respect price & in_stock
    min_price = (request.GET.get("min") or "").strip()
    max_price = (request.GET.get("max") or "").strip()
    if min_price:
        try:
            vqs = vqs.annotate(eff_price=Coalesce(F("promo_price"), F("sale_price"), F("mrp"))).filter(
                eff_price__gte=float(min_price)
            )
        except ValueError:
            pass
    if max_price:
        try:
            vqs = vqs.annotate(eff_price=Coalesce(F("promo_price"), F("sale_price"), F("mrp"))).filter(
                eff_price__lte=float(max_price)
            )
        except ValueError:
            pass
    if request.GET.get("in_stock") in ("1", "true", "True"):
        vqs = vqs.filter(Q(stock_qty__gt=0) | Q(in_stock=True) | Q(backorder_allowed=True))

    # ----- COLORS: primary + secondary merged -----
    color_counts = defaultdict(int)

    rows_p = (
        vqs.exclude(color_primary__isnull=True)
           .values("color_primary__name")
           .annotate(count=Count("id"))
    )
    for r in rows_p:
        n = r["color_primary__name"]
        if n:
            color_counts[n] += r["count"]

    rows_s = (
        vqs.exclude(color_secondary__isnull=True)
           .values("color_secondary__name")
           .annotate(count=Count("id"))
    )
    for r in rows_s:
        n = r["color_secondary__name"]
        if n:
            color_counts[n] += r["count"]

    colors = [
        {"value": name, "label": name, "count": cnt}
        for name, cnt in sorted(color_counts.items(), key=lambda t: t[0].lower())
    ]

    # ----- SIZES -----
    sizes = []
    size_rows = (
        vqs.exclude(size__isnull=True)
           .values("size__name")
           .annotate(count=Count("id"))
           .order_by("size__name")
    )
    for r in size_rows:
        name = r["size__name"]
        if name:
            sizes.append({"value": name, "label": name, "count": r["count"]})

    return {"colors": colors, "sizes": sizes}


def _paginate(request, qs, per_page=24):
    page = request.GET.get("page", 1)
    paginator = Paginator(qs, per_page)
    return paginator.get_page(page)


def _category_slug_path(cat):
    # returns /category/<parent>/ or /category/<parent>/<child>/
    if cat.parent_id:
        return reverse("shop:category_child", kwargs={
            "parent_slug": cat.parent.slug,
            "child_slug": cat.slug,
        })
    return reverse("shop:category_parent", kwargs={"parent_slug": cat.slug})


# ---------- View ----------

def category_listing(request, parent_slug, child_slug=None):
    """
    Category page:
      - Parent with NO direct products => show ONLY subcategory grid
      - Otherwise => list VARIANTS (each variant as a separate card)
    """
    category = _get_category_or_404(parent_slug, child_slug)

    # subcategory grid (hide zero-product subcats)
    subcats = _children_for_grid(category) if (category.parent_id is None or not child_slug) else []

    # direct products under this exact category
    direct_products = Product.objects.filter(is_active=True, category=category)

    show_variants = direct_products.exists()

    variants_page = None
    facets = {"colors": [], "sizes": []}
    products_page = None  # backward-compat for old templates

    if show_variants:
        # base variants for this category's direct products
        vbase = (
            Variant.objects
            .select_related("product", "color_primary", "color_secondary", "size")
            .filter(is_active=True, product__is_active=True, product__in=direct_products)
        )

        # facets BEFORE applying color/size (but respect price/in_stock)
        facets = _collect_variant_facets(vbase, request)

        # apply filters + sort + paginate
        vfiltered = _apply_variant_filters(vbase, request)
        variants_page = _paginate(request, vfiltered, per_page=12)

    # breadcrumbs
    chain, crumbs = [], []
    cur = category
    while cur:
        chain.append(cur)
        cur = cur.parent
    for c in reversed(chain):
        crumbs.append({"name": c.name, "url": _category_slug_path(c)})

    qs_keep = request.GET.copy()
    if 'page' in qs_keep:
        del qs_keep['page']
    qs_keep_str = qs_keep.urlencode()

    tpl = "categories/child.html" if (category.parent_id and child_slug) else "categories/parent.html"

    ctx = {
        "category": category,
        "breadcrumbs": crumbs,
        "subcategories": subcats,
        "show_products": False,        # old flag OFF
        "products_page": products_page,
        "variants_page": variants_page,   # templates should loop over this
        "list_mode": "VARIANTS" if show_variants else "NONE",
        "facets": facets,
        "applied": {
            "min": request.GET.get("min") or "",
            "max": request.GET.get("max") or "",
            "color": request.GET.get("color") or "",
            "size": request.GET.get("size") or "",
            "in_stock": request.GET.get("in_stock") in ("1", "true", "True"),
            "sort": request.GET.get("sort") or "",
        },
        "qs_keep": qs_keep_str,
    }
    return render(request, tpl, ctx)


# =========================
# ======== REVIEW =========
# =========================

# ---------- Helper: Check if user purchased this variant ----------
def user_purchased_variant(user, variant):
    if not user.is_authenticated:
        return False, None

    # OrderItem with this variant, under an order paid/partially paid, belonging to this user (or email fallback)
    qs = OrderItem.objects.select_related("order").filter(variant=variant)
    qs = qs.filter(order__status__in=["PAID", "PARTIALLY_PAID"])  # adapt if your choices differ

    # ownership check
    owned = qs.filter(order__user=user)
    if owned.exists():
        return True, owned.first()

    # fallback to email match if your flow allows guest -> auto attach by email
    if hasattr(user, "email") and user.email:
        owned_email = qs.filter(order__email=user.email)
        if owned_email.exists():
            return True, owned_email.first()

    return False, None

# ---------- Helper: aggregates for a variant ----------
def build_reviews_context(product, variant, page_number=1, sort="recent", per_page=5):
    base = Review.objects.filter(product=product, variant=variant, is_published=True)

    if sort == "rating_high":
        base = base.order_by("-rating", "-created_at")
    elif sort == "rating_low":
        base = base.order_by("rating", "-created_at")
    else:
        base = base.order_by("-created_at")

    paginator = Paginator(base, per_page)
    page_obj = paginator.get_page(page_number)

    agg = Review.objects.filter(product=product, variant=variant, is_published=True).aggregate(
        avg=Avg("rating"), cnt=Count("id")
    )
    avg_rating = agg["avg"] or 0
    rating_count = agg["cnt"] or 0

    # histogram (as before)
    hist = {}
    for r in range(1, 6):
        hist[r] = Review.objects.filter(
            product=product, variant=variant, is_published=True, rating=r
        ).count()

    hist_rows = []
    for star in range(5, 0, -1):
        count = hist.get(star, 0)
        pct = int(round((count * 100.0) / rating_count)) if rating_count else 0
        hist_rows.append({"star": star, "count": count, "pct": pct})

    # ✅ NEW: integer filled stars (floor)
    avg_rating_int = int(avg_rating)  # or: floor(avg_rating) / round(avg_rating)

    return {
        "reviews_page": page_obj,
        "avg_rating": avg_rating,
        "avg_rating_int": avg_rating_int,   # <-- add this
        "rating_count": rating_count,
        "histogram": hist,
        "hist_rows": hist_rows,
        "sort": sort,
    }
# ---------- AJAX: list + summary partial ----------
@require_GET
def reviews_list(request, slug):
    product = get_object_or_404(Product, slug=slug)
    variant_id = request.GET.get("variant")
    if not variant_id:
        return HttpResponseBadRequest("variant is required")

    variant = get_object_or_404(Variant, id=variant_id, product=product)

    sort = request.GET.get("sort", "recent")
    page = request.GET.get("page", 1)

    ctx = {"product": product, "variant": variant}
    ctx.update(build_reviews_context(product, variant, page_number=page, sort=sort))

    # eligibility flag for showing form (frontend can decide to show/hide)
    can_review, _ = user_purchased_variant(request.user, variant)
    ctx["can_review"] = can_review

    # Return HTML fragments so we can replace on page
    return render(request, "shop/_reviews_wrapper.html", ctx)  # wrapper includes summary + list + (form if eligible)

# ---------- POST: create review ----------
@require_POST
@login_required
def review_create(request):
    variant_id = request.POST.get("variant")
    product_id = request.POST.get("product")
    rating = request.POST.get("rating")
    title = request.POST.get("title", "").strip()
    body = request.POST.get("body", "").strip()

    if not (variant_id and product_id and rating):
        return JsonResponse({"ok": False, "error": "Missing fields."}, status=400)

    product = get_object_or_404(Product, id=product_id)
    variant = get_object_or_404(Variant, id=variant_id, product=product)

    can_review, order_item = user_purchased_variant(request.user, variant)
    if not can_review:
        return JsonResponse({"ok": False, "error": "You can review only if you purchased this variant."}, status=403)

    try:
        rating_int = int(rating)
        if rating_int < 1 or rating_int > 5:
            raise ValueError()
    except ValueError:
        return JsonResponse({"ok": False, "error": "Invalid rating."}, status=400)

    review, created = Review.objects.get_or_create(
        user=request.user, variant=variant,
        defaults={
            "product": product,
            "rating": rating_int,
            "title": title[:140],
            "body": body,
            "order_item": order_item,
            "is_verified_purchase": True,
            "is_published": True,  # if you want moderation, set False and approve in admin
        }
    )
    if not created:
        # Update existing (allow user to edit their one review)
        review.rating = rating_int
        review.title = title[:140]
        review.body = body
        review.order_item = order_item or review.order_item
        review.is_verified_purchase = True
        review.save(update_fields=["rating", "title", "body", "order_item", "is_verified_purchase", "updated_at"])

    return JsonResponse({"ok": True})