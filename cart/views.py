# cart/views.py
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.views.decorators.http import require_POST, require_GET, require_http_methods
from django.views.decorators.csrf import csrf_protect
from django.http import JsonResponse, HttpResponseBadRequest
from django.urls import reverse
from django.utils import timezone
from django.db.models import Q
from decimal import Decimal, ROUND_HALF_UP
import json
import requests

from .forms import CheckoutForm
from shop.models import Variant, Product, Category, Coupon
from .utils import Cart

# Orders services (Step-2 snapshot + Semi-COD helpers)
from orders.services import (
    snapshot_cart_to_order,
    semi_cod_allowed,
    calc_semi_cod_advance,
)

# =======================
# Money / tiny helpers
# =======================
def _dround(n):
    try:
        return Decimal(n).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _get_applied_coupon_code(request):
    cart = Cart(request)
    if hasattr(cart, "get_applied_coupon_code"):
        try:
            code = cart.get_applied_coupon_code()
            if code:
                return str(code)
        except Exception:
            pass
    code = request.session.get("applied_coupon_code")
    return str(code) if code else ""


# =======================
# Cart lines & delivery
# =======================
def _cart_lines(request):
    """Return cart lines with product/variant/qty/subtotal per line."""
    cart = Cart(request)
    lines = []
    for item in cart.get_items():
        var = item["variant"]
        prod = var.product
        qty = int(item["quantity"])
        unit = Decimal(item["price"])
        lines.append({
            "product": prod,
            "variant": var,
            "qty": qty,
            "subtotal": _dround(unit * qty),
        })
    return lines


def _line_delivery_cost(line):
    """delivery_price priority: Variant → Product"""
    v, p = line["variant"], line["product"]
    price = getattr(v, "delivery_price", None)
    if price is None:
        price = getattr(p, "delivery_price", 0)
    try:
        price = Decimal(price)
    except Exception:
        price = Decimal("0")
    return _dround(price) * int(line["qty"])


# =======================
# Coupon scope helpers
# =======================
def _product_categories_chain(product):
    """product.category + all ancestors (if Category has parent)."""
    cats = set()
    try:
        c = getattr(product, "category", None)
        while c:
            cats.add(c)
            c = getattr(c, "parent", None)
    except Exception:
        pass
    return cats


def _scope_ok_for_line(line, c: Coupon):
    """
    True iff coupon c can apply to this line.
    Honors included_* and excluded_* (variants/products/categories).
    If no include lists exist, defaults to site-wide.
    """
    var, prod = line["variant"], line["product"]

    # --- Exclusions ---
    try:
        if hasattr(c, "excluded_variants") and c.excluded_variants.filter(pk=var.pk).exists():
            return False
    except Exception:
        pass
    try:
        if hasattr(c, "excluded_products") and c.excluded_products.filter(pk=prod.pk).exists():
            return False
    except Exception:
        pass
    try:
        if hasattr(c, "excluded_categories") and getattr(prod, "category", None):
            cats = _product_categories_chain(prod)
            if c.excluded_categories.filter(pk__in=[x.pk for x in cats]).exists():
                return False
    except Exception:
        pass

    # --- Includes (if any present, must match at least one) ---
    included_rule_present = False

    try:
        if hasattr(c, "included_variants") and c.included_variants.exists():
            included_rule_present = True
            if not c.included_variants.filter(pk=var.pk).exists():
                return False
    except Exception:
        pass

    try:
        if hasattr(c, "included_products") and c.included_products.exists():
            included_rule_present = True
            if not c.included_products.filter(pk=prod.pk).exists():
                return False
    except Exception:
        pass

    try:
        if hasattr(c, "included_categories") and c.included_categories.exists():
            included_rule_present = True
            if getattr(prod, "category", None):
                cats = _product_categories_chain(prod)
                if not c.included_categories.filter(pk__in=[x.pk for x in cats]).exists():
                    return False
            else:
                return False
    except Exception:
        pass

    # If no include lists at all → global coupon
    return True


def _scope_subtotal(lines, c: Coupon):
    """
    Subtotal visible to the coupon based on scope.
    If Coupon.applies_to == CART → full subtotal; else eligible lines only.
    """
    applies = getattr(c, "applies_to", None)
    if applies in (getattr(Coupon, "SCOPE_CART", "CART"), "CART", "cart"):
        return _dround(sum((l["subtotal"] for l in lines), Decimal("0.00")))

    elig = [l for l in lines if _scope_ok_for_line(l, c)]
    return _dround(sum((l["subtotal"] for l in elig), Decimal("0.00")))


def _discount_for_coupon(coupon: Coupon, lines):
    """Calculate discount on the coupon's scope subtotal (not whole cart)."""
    scope_sub = _scope_subtotal(lines, coupon)
    if scope_sub <= 0:
        return Decimal("0.00")

    if coupon.type == Coupon.TYPE_PERCENT:
        d = scope_sub * Decimal(coupon.value) / Decimal("100")
        m = getattr(coupon, "max_discount_amount", None)
        if m:
            try:
                d = min(d, Decimal(m))
            except Exception:
                pass
        return _dround(d)

    if coupon.type == Coupon.TYPE_FLAT:
        return _dround(min(Decimal(coupon.value), scope_sub))

    return Decimal("0.00")


def _validate_coupon_still_applicable(request):
    """
    If an applied coupon no longer has ANY eligible items in cart,
    remove it from session; otherwise keep it.
    """
    code = request.session.get("applied_coupon_code")
    if not code:
        return
    try:
        c = Coupon.objects.get(code__iexact=code)
    except Coupon.DoesNotExist:
        request.session.pop("applied_coupon_code", None)
        request.session.modified = True
        return

    lines = _cart_lines(request)
    if _scope_subtotal(lines, c) <= 0:
        request.session.pop("applied_coupon_code", None)
        request.session.modified = True


# =======================
# Totals (Checkout)
# =======================
def _compute_checkout_totals(request):
    """
    Returns dict in RUPEES (ints):
      item_total, discount_total, shipping_total, grand_total, cart_count
    Mirrors cart page logic (free shipping threshold etc.).
    """
    cart = Cart(request)
    subtotal = _dround(cart.get_subtotal())
    lines = _cart_lines(request)

    # Delivery threshold same as cart_page
    DELIVERY_FREE_THRESHOLD = Decimal("6000.00")
    if subtotal > DELIVERY_FREE_THRESHOLD:
        delivery_amount = Decimal("0.00")
    else:
        delivery_amount = _dround(sum(_line_delivery_cost(l) for l in lines))

    applied_code = _get_applied_coupon_code(request)
    discount = Decimal("0.00")
    if applied_code:
        try:
            c = Coupon.objects.get(code__iexact=applied_code)
            discount = _discount_for_coupon(c, lines)
        except Coupon.DoesNotExist:
            discount = Decimal("0.00")

    total_after_discount = _dround(subtotal - discount)
    grand_total = _dround(total_after_discount + delivery_amount)

    return {
        "item_total": int(total_after_discount + discount),  # equals subtotal
        "discount_total": int(discount),
        "shipping_total": int(delivery_amount),
        "grand_total": int(grand_total),
        "cart_count": len(cart),
    }


# =======================
# Cart page
# =======================
def cart_page(request):
    cart = Cart(request)
    cart_items = cart.get_items()
    subtotal = _dround(cart.get_subtotal())

    # Build canonical product URL for each item
    for it in cart_items:
        v = it.get("variant")
        if not v:
            it["detail_url"] = reverse("cart:cart_page")
            continue
        p = getattr(v, "product", None)
        cat = getattr(p, "category", None)
        parent = getattr(cat, "parent", None) if cat else None
        try:
            if parent:
                path = reverse(
                    "shop:product_detail_child",
                    kwargs={"parent_slug": parent.slug, "child_slug": cat.slug, "slug": p.slug},
                )
            else:
                path = reverse(
                    "shop:product_detail_parent",
                    kwargs={"parent_slug": cat.slug if cat else "catalog", "slug": p.slug},
                )
            it["detail_url"] = f"{path}?variant={v.id}"
        except Exception:
            it["detail_url"] = reverse("cart:cart_page")

    # --- Delivery calculation ---
    DELIVERY_FREE_THRESHOLD = Decimal("6000.00")  # FREE only if subtotal > 6000
    lines = _cart_lines(request)

    if subtotal > DELIVERY_FREE_THRESHOLD:
        delivery_amount = Decimal("0.00")
    else:
        delivery_amount = _dround(sum(_line_delivery_cost(l) for l in lines))

    # --- Discount from applied coupon (if any) ---
    applied_code = _get_applied_coupon_code(request)
    discount = Decimal("0.00")
    if applied_code:
        try:
            c = Coupon.objects.get(code__iexact=applied_code)
            discount = _discount_for_coupon(c, lines)
        except Coupon.DoesNotExist:
            discount = Decimal("0.00")

    # --- Totals ---
    total_after_discount = _dround(subtotal - discount)
    grand_total = _dround(total_after_discount + delivery_amount)

    # --- Free shipping progress (based on subtotal) ---
    fs_threshold = DELIVERY_FREE_THRESHOLD
    fs_unlocked = subtotal > fs_threshold  # strictly greater
    fs_remaining = _dround(max(Decimal("0.00"), fs_threshold - subtotal))
    fs_progress = int(min(100, float((min(subtotal, fs_threshold) / fs_threshold) * 100))) if fs_threshold > 0 else 100

    response = render(
        request,
        "cart/cart.html",
        {
            "cart_items": cart_items,
            "subtotal": subtotal,
            "cart_count": len(cart),
            "applied_coupon_code": applied_code,
            "discount": discount,
            "delivery_amount": delivery_amount,
            "total_after_discount": total_after_discount,
            "grand_total": grand_total,
            "delivery_free_threshold": fs_threshold,
            "free_ship_unlocked": fs_unlocked,
            "free_ship_remaining": fs_remaining,
            "free_ship_progress": fs_progress,
        },
    )
    response["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    return response


# =======================
# Cart CRUD
# =======================
@require_POST
@csrf_protect
def add_to_cart(request, variant_id):
    cart = Cart(request)
    variant = get_object_or_404(Variant, id=variant_id, is_active=True)
    try:
        quantity = max(1, int(request.POST.get("quantity", 1)))
    except (TypeError, ValueError):
        quantity = 1
    if quantity > variant.stock_qty and not variant.backorder_allowed:
        messages.error(request, f"Sorry, only {variant.stock_qty} items available.")
        return redirect(request.POST.get("next", "cart:cart_page"))
    cart.add(variant_id=variant_id, quantity=quantity)
    messages.success(request, f"{variant.product.title} added to cart.")
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "cart_count": len(cart)})
    return redirect(request.POST.get("next", "cart:cart_page"))


@require_POST
@csrf_protect
def update_item(request, variant_id):
    cart = Cart(request)
    try:
        quantity = int(request.POST.get("quantity", 1))
    except (TypeError, ValueError):
        messages.error(request, "Invalid quantity.")
        return redirect("cart:cart_page")

    variant = get_object_or_404(Variant, id=variant_id, is_active=True)
    if quantity > variant.stock_qty and not variant.backorder_allowed:
        messages.error(request, f"Sorry, only {variant.stock_qty} items available.")
        return redirect("cart:cart_page")

    if quantity <= 0:
        cart.remove(variant_id)
        messages.info(request, "Item removed from cart.")
    else:
        cart.update_quantity(variant_id, quantity)
        messages.success(request, "Cart updated.")

    # Re-check coupon validity after change
    _validate_coupon_still_applicable(request)
    return redirect("cart:cart_page")


@require_POST
@csrf_protect
def remove_item(request, variant_id):
    cart = Cart(request)
    if cart.remove(variant_id):
        messages.info(request, "Item removed from cart.")

    # Re-check coupon validity after removal
    _validate_coupon_still_applicable(request)

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return JsonResponse({"success": True, "cart_count": len(cart), "subtotal": str(cart.get_subtotal())})
    return redirect("cart:cart_page")


@require_POST
@csrf_protect
def clear_cart(request):
    cart = Cart(request)
    cart.clear()
    messages.info(request, "Cart cleared.")

    # Always clear coupon on empty cart
    request.session.pop("applied_coupon_code", None)
    request.session.modified = True

    return redirect("cart:cart_page")


@require_POST
@csrf_protect
def batch_add(request):
    try:
        data = json.loads(request.body.decode("utf-8"))
    except Exception:
        return JsonResponse({"ok": False, "error": "invalid_json"}, status=400)

    lines = data.get("lines") or []
    if not isinstance(lines, list) or not lines:
        return JsonResponse({"ok": False, "error": "no_lines"}, status=400)

    cart = Cart(request)
    try:
        existing_ids = {it["variant"].id for it in cart.get_items() if it.get("variant")}
    except Exception:
        existing_ids = set()

    added, seen_in_request = [], set()
    for line in lines:
        try:
            vid = int(line.get("variant_id"))
            qty = max(1, int(line.get("qty", 1)))
        except Exception:
            continue

        if vid in seen_in_request or vid in existing_ids:
            continue
        seen_in_request.add(vid)

        variant = get_object_or_404(Variant, pk=vid, is_active=True)
        if qty > variant.stock_qty and not getattr(variant, "backorder_allowed", False):
            continue

        cart.add(variant_id=variant.id, quantity=qty)
        added.append({"variant_id": vid, "qty": qty})

    return JsonResponse({"ok": True, "added": added, "cart_count": len(cart), "subtotal": str(cart.get_subtotal())})


# =======================
# Coupons APIs
# =======================
@require_GET
def coupons_for_cart(request):
    """
    Return coupons list for cart modal.
    - Only ACTIVE & within time window
    - Respect 'show_in_listing' (if field exists): only show when True
    - Sort: applicable first, then by savings desc
    """
    lines = _cart_lines(request)
    now = timezone.now()

    # Base queryset: active + time window
    qs = Coupon.objects.filter(
        Q(starts_at__isnull=True) | Q(starts_at__lte=now),
        Q(ends_at__isnull=True) | Q(ends_at__gte=now),
        status=getattr(Coupon, "STATUS_ACTIVE", "ACTIVE"),
    )

    # Hide from list if "Show in listing" is unchecked (listing only; code apply still allowed)
    if hasattr(Coupon, "show_in_listing"):
        qs = qs.filter(show_in_listing=True)

    items = []
    for c in qs:
        disc = _discount_for_coupon(c, lines)  # scope-aware
        ok = disc > 0
        items.append({
            "code": c.code,
            "title": getattr(c, "title", c.code),
            "description": getattr(c, "description", ""),
            "savings_amount": float(disc),
            "ok": ok,
        })

    # applicable first, then highest savings
    items.sort(key=lambda x: ((0 if x["ok"] else 1), -x["savings_amount"]))
    return JsonResponse({"items": items})


@require_POST
def api_apply_coupon(request):
    """
    Apply coupon code to current cart
    Body: {"code": "XXXX"}
    """
    try:
        data = json.loads(request.body.decode("utf-8"))
        code = (data.get("code") or "").strip()
    except Exception:
        return JsonResponse({"applied": False, "reason": "Invalid payload"}, status=400)

    if not code:
        return JsonResponse({"applied": False, "reason": "Coupon code required"}, status=400)

    # Load and basic checks
    try:
        c = Coupon.objects.get(code__iexact=code)
        now = timezone.now()
        if c.starts_at and c.starts_at > now:
            return JsonResponse({"applied": False, "reason": "Coupon not started"}, status=400)
        if c.ends_at and c.ends_at < now:
            return JsonResponse({"applied": False, "reason": "Coupon expired"}, status=400)
        if getattr(c, "status", "ACTIVE") != getattr(Coupon, "STATUS_ACTIVE", "ACTIVE"):
            return JsonResponse({"applied": False, "reason": "Coupon inactive"}, status=400)
    except Coupon.DoesNotExist:
        return JsonResponse({"applied": False, "reason": "Invalid coupon"}, status=404)

    # Min subtotal (against full subtotal by default)
    cart = Cart(request)
    subtotal = _dround(cart.get_subtotal())
    if subtotal <= 0:
        return JsonResponse({"applied": False, "reason": "Cart is empty"}, status=400)

    if getattr(c, "min_cart_subtotal", None):
        try:
            if subtotal < Decimal(c.min_cart_subtotal):
                need = _dround(Decimal(c.min_cart_subtotal) - subtotal)
                return JsonResponse({"applied": False, "reason": f"Add ₹{need:.2f} more"}, status=400)
        except Exception:
            pass

    # Scope-aware discount check
    discount = _discount_for_coupon(c, _cart_lines(request))
    if discount <= 0:
        return JsonResponse({"applied": False, "reason": "Not applicable to items in cart"}, status=400)

    # Persist selection
    if hasattr(cart, "apply_coupon"):
        try:
            cart.apply_coupon(code)
        except Exception:
            request.session["applied_coupon_code"] = code
            request.session.modified = True
    else:
        request.session["applied_coupon_code"] = code
        request.session.modified = True

    return JsonResponse({"applied": True, "code": code, "discount": str(discount)})


# =======================
# Checkout: Step-1 → Step-2
# =======================
CHECKOUT_SESSION_KEY = "checkout_form"

@require_http_methods(["GET", "POST"])
def checkout_page(request):
    """
    Step-1 (Address form):
    POST valid → session me save → Step-2 (payments) page.
    """
    initial = request.session.get(CHECKOUT_SESSION_KEY, {})
    if request.method == "POST":
        form = CheckoutForm(request.POST)
        if form.is_valid():
            request.session[CHECKOUT_SESSION_KEY] = form.cleaned_data
            request.session.modified = True
            return redirect(reverse("cart:checkout_step2"))
    else:
        form = CheckoutForm(initial=initial)

    # NOTE: Tumhari file: checkout_step1.html
    return render(request, "orders/checkout_step1.html", {"form": form})


@require_GET
def checkout_step2(request):
    """
    Step-2 (Payments page):
    - Requires Step-1 data present in session.
    - Creates a fresh Order(status=CREATED) snapshot (paise in DB).
    - Passes totals, items, address, semi-COD context to template.
    """
    addr = request.session.get(CHECKOUT_SESSION_KEY)
    if not addr:
        messages.error(request, "Please fill your address first.")
        return redirect(reverse("cart:checkout"))

    # Build cart items payload (RUPEES) for snapshot
    cart = Cart(request)
    items = []
    for it in cart.get_items():
        var = it["variant"]
        prod = var.product
        qty = int(it["quantity"])
        unit = Decimal(it["price"]) 
        img_url = None
        try:
            v_imgs = getattr(var, "images", None)
            v_first = v_imgs.first() if v_imgs else None
            if v_first and getattr(v_first, "image", None):
                img_url = v_first.image.url
            elif getattr(prod, "primary_image", None) and getattr(prod.primary_image, "image", None):
                img_url = prod.primary_image.image.url
            elif hasattr(prod, "images"):
                p_first = prod.images.first()
                if p_first and getattr(p_first, "image", None):
                    img_url = p_first.image.url
        except Exception:
            img_url = None
        items.append({
            "product_id": prod.id,
            "variant_id": var.id,
            "title": prod.title,
            "variant_text": " / ".join(filter(None, [
                getattr(getattr(var, "color_primary", None), "name", None) or getattr(var, "color_primary", None) or "",
                getattr(getattr(var, "size", None), "name", None) or getattr(var, "size", None) or "",
            ])),
            "qty": qty,
            "unit_price": int(unit),         # RUPEES integer expected by snapshot
            "line_total": int(unit * qty),   # RUPEES integer
            "image_url": img_url,
        })

    totals = _compute_checkout_totals(request)

    # Create the CREATED order snapshot
    order = snapshot_cart_to_order(
        user=request.user if request.user.is_authenticated else None,
        addr={
            "full_name": addr.get("full_name",""),
            "email": addr.get("email",""),
            "address_line": addr.get("full_address",""),
            "city": addr.get("city",""),
            "state": addr.get("state",""),
            "pincode": addr.get("pincode",""),
            "mobile": addr.get("mobile",""),
            "gst": addr.get("gst",""),
        },
        cart_items=items,
        totals=totals,
        coupon_code=_get_applied_coupon_code(request),
    )

    # Semi-COD (rupees): allowed & amounts
    allow_semi = semi_cod_allowed(totals["grand_total"])
    adv_paise = calc_semi_cod_advance(totals["grand_total"]) if allow_semi else 0
    adv_rupees = int(Decimal(adv_paise) / Decimal(100))
    rem_rupees = totals["grand_total"] - adv_rupees

    ctx = {
        "pending_order": order,                    # .order_number for hidden field
        "totals": totals,                          # all in RUPEES ints
        "semi_cod_allowed": allow_semi,
        "semi_cod_advance_rupees": adv_rupees,
        "semi_cod_remaining_rupees": rem_rupees,
        # address block for display
        "addr": {
            "full_name": addr.get("full_name",""),
            "address_line": addr.get("full_address",""),
            "city": addr.get("city",""),
            "state": addr.get("state",""),
            "pincode": addr.get("pincode",""),
            "mobile": addr.get("mobile",""),
            "email": addr.get("email",""),
            "gst": addr.get("gst",""),
        },
        # order items list for “Your Order”
        "cart_items": [
            {
                "title": it["title"],
                "variant_text": it["variant_text"],
                "qty": it["qty"],
                "line_total": it["line_total"],
                "image_url": it.get("image_url"),  # add if you store image on variant/product
            } for it in items
        ],
    }

    # NOTE: Tumhari file: checkout_step2.html
    return render(request, "orders/checkout_step2.html", ctx)


def checkout_success(request):
    # Legacy success page (optional)
    data = request.session.get(CHECKOUT_SESSION_KEY, {})
    return render(request, "orders/checkout_step1_success.html", {"data": data})


# =======================
# Pincode API
# =======================
@require_GET
def api_pincode(request):
    pincode = (request.GET.get("pincode") or "").strip()
    if not pincode.isdigit() or len(pincode) != 6:
        return HttpResponseBadRequest("Invalid pincode")

    # Backend proxy to api.postalpincode.in
    try:
        resp = requests.get(
            f"https://api.postalpincode.in/pincode/{pincode}",
            timeout=6,
            headers={"User-Agent": "QuesecWebApp/1.0"}
        )
        data = resp.json()
        city = state = ""
        if isinstance(data, list) and data and data[0].get("Status") == "Success":
            po_list = data[0].get("PostOffice") or []
            if po_list:
                # Prefer first entry
                city = (po_list[0].get("District") or "").strip()
                state = (po_list[0].get("State") or "").strip()
        return JsonResponse({"ok": True, "city": city, "state": state})
    except Exception:
        return JsonResponse({"ok": False, "city": "", "state": ""}, status=200)
