import random
import string
import json
import hashlib
import hmac
import uuid
from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone
from django.conf import settings
from django.shortcuts import render
from django.urls import reverse

from .models import Order, OrderItem, PaymentAttempt
from .constants import SEMI_COD_MIN, SEMI_COD_MAX, SEMI_COD_ADV_PCT


# --------------------- Common helpers ---------------------

def generate_order_number(prefix="QSR"):
    # e.g., QSR-2025-AB12CD
    y = timezone.now().strftime("%Y")
    rand = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"{prefix}-{y}-{rand}"


def rupees_to_paise(rupees: int) -> int:
    # if rupees is like 35999 (int), convert to paise
    return int(rupees) * 100


def paise_to_rupees_str(paise: int) -> str:
    # "3599900" → "35999.00"
    rupees = Decimal(paise) / Decimal(100)
    return format(rupees.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def semi_cod_allowed(grand_total_rupees: int) -> bool:
    try:
        v = int(grand_total_rupees)
        return SEMI_COD_MIN <= v <= SEMI_COD_MAX
    except Exception:
        return False


def calc_semi_cod_advance(grand_total_rupees: int) -> int:
    """returns advance amount in paise (20% of total)"""
    rupees = int(round((SEMI_COD_ADV_PCT / 100.0) * int(grand_total_rupees)))
    return rupees_to_paise(rupees)


# --------------------- Order snapshot ---------------------

@transaction.atomic
def snapshot_cart_to_order(*, user, addr, cart_items, totals, coupon_code=None) -> Order:
    """
    Creates a fresh CREATED order with snapshot of items & address.
    Expects:
      - addr = {full_name, address_line, city, state, pincode, mobile}
      - totals = {item_total, discount_total, shipping_total,  grand_total} in RUPEES
      - cart_items = [{product_id, variant_id, title, variant_text, qty, unit_price, line_total}] in RUPEES
    Stores money in PAISA in DB to be gateway-safe.
    """
    order = Order.objects.create(
        user=user,
        order_number=generate_order_number(),
        status=Order.Status.CREATED,
        full_name=addr["full_name"],
        email=addr.get("email", "") or None, 
        address_line=addr["address_line"],
        city=addr["city"],
        state=addr["state"],
        pincode=addr["pincode"],
        mobile=addr["mobile"],
        gstin=addr.get("gst", "") or None,
        item_total=rupees_to_paise(totals["item_total"]),
        discount_total=rupees_to_paise(totals.get("discount_total", 0)),
        shipping_total=rupees_to_paise(totals.get("shipping_total", 0)),
        grand_total=rupees_to_paise(totals["grand_total"]),
        coupon_code=coupon_code or totals.get("coupon_code"),
    )

    try:
        if addr:
            changed = False
            for k in ["full_name", "mobile", "email", "address_line", "pincode", "city", "state"]:
                val = (addr.get(k) or "").strip()
                if val and hasattr(order, k) and not getattr(order, k, None):
                    setattr(order, k, val)
                    changed = True
            if changed:
                order.save()
    except Exception:
        pass

    for it in cart_items:
        OrderItem.objects.create(
            order=order,
            product_id=it["product_id"],
            variant_id=it.get("variant_id"),
            title=it["title"],
            variant_text=it.get("variant_text", ""),
            qty=int(it["qty"]),
            unit_price=rupees_to_paise(it["unit_price"]),
            line_total=rupees_to_paise(it["line_total"]),
        )
    return order


# --------------------- Razorpay ---------------------

def init_razorpay_order(request, order: Order, advance_only: bool = False):
    import razorpay
    amount_paise = order.grand_total
    if advance_only:
        amount_paise = int(Decimal(order.grand_total) * Decimal("0.20"))

    client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
    rzp_order = client.order.create(dict(
        amount=amount_paise,
        currency="INR",
        receipt=order.order_number,
        payment_capture=1,
    ))

    PaymentAttempt.objects.create(
        order=order,
        method=PaymentAttempt.Method.RAZORPAY,
        amount=amount_paise,
        provider_order_id=rzp_order.get("id"),
    )

    callback_absolute = request.build_absolute_uri(
        reverse("orders:razorpay_callback")
    )
    
    options = {
        "key": settings.RAZORPAY_KEY_ID,
        "amount": amount_paise,
        "currency": "INR",
        "name": "Quesec Rides",
        "order_id": rzp_order.get("id"),
        "prefill": {
            "name": order.full_name,
            "email": (order.email or (order.user.email if order.user and getattr(order.user, "email", None) else "")),
            "contact": order.mobile,
        },
        "notes": {"order_number": order.order_number},
        "callback_url": callback_absolute,
        "redirect": True,
    }

    # 🔒 Hide EMI ONLY for Semi-COD advance flow
    if advance_only:
        options["config"] = {
            "display": {
                "emi": False,                   
                "paylater": False,              
                "cardlessEmi": False,       
                "hide": [
                    {"method": "emi"},
                    {"method": "paylater"},
                    {"method": "cardlessEmi"},
                ],
            }
        }

    return render(request, "orders/razorpay_checkout.html", {"rzp_options": json.dumps(options)})


def verify_razorpay_signature(order_id: str, payment_id: str, signature: str) -> bool:
    try:
        msg = f"{order_id}|{payment_id}".encode("utf-8")
        secret = settings.RAZORPAY_KEY_SECRET.encode("utf-8")
        expected = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


# --------------------- PayU ---------------------

def build_payu_payload(request, order: Order, upi_only: bool = False):
    key = settings.PAYU_MERCHANT_KEY
    salt = settings.PAYU_SALT
    base = settings.PAYU_BASE_URL.rstrip("/")   # e.g. https://test.payu.in

    amount = paise_to_rupees_str(order.grand_total)   # "11200.00"
    # ✅ txnid: max 25 chars, only [A-Za-z0-9_-], unique every attempt
    rand = uuid.uuid4().hex[:6].upper()
    txnid = f"TXN{order.order_number.replace('-', '')[:16]}{rand}"[:25]

    surl = request.build_absolute_uri(reverse("orders:payu_callback"))
    furl = request.build_absolute_uri(reverse("orders:payu_callback"))

    email = order.email or (order.user.email if order.user and getattr(order.user, "email", None) else "guest@example.com")
    firstname = (order.full_name or "").split(" ")[0][:60] or "Guest"
    productinfo = f"Order {order.order_number}"

    udf = {f"udf{i}": "" for i in range(1, 11)}

    # Request hash (v2 – sha512)
    seq = "|".join([
        key, txnid, amount, productinfo, firstname, email,
        udf["udf1"], udf["udf2"], udf["udf3"], udf["udf4"], udf["udf5"],
        udf["udf6"], udf["udf7"], udf["udf8"], udf["udf9"], udf["udf10"],
        salt
    ])
    req_hash = hashlib.sha512(seq.encode("utf-8")).hexdigest().lower()

    payload = {
        "key": key,
        "txnid": txnid,
        "amount": amount,
        "productinfo": productinfo,
        "firstname": firstname,
        "email": email,
        "phone": order.mobile or "",
        "surl": surl,
        "furl": furl,
        "hash": req_hash,
        # ❌ DO NOT send deprecated "service_provider"
        # "service_provider": "payu_paisa",
    }
    payload.update(udf)

    if upi_only:
        # ✅ safely hint UPI; PayU ignore unknowns on some skins
        payload.update({
            "pg": "UPI",
            "bankcode": "UPI",
            "enforce_paymethod": "UPI",
            "show_payment_mode": "1",
            "payment_option": "upi",
        })

    PaymentAttempt.objects.create(
        order=order,
        method=PaymentAttempt.Method.PAYU if not upi_only else PaymentAttempt.Method.UPI_PROMO,
        amount=order.grand_total,
        provider_order_id=txnid,
        raw_payload={"request_payload": payload},
    )

    return f"{base}/_payment", payload


def verify_payu_response_hash(posted: dict) -> bool:
    """
    Response hash (success):
      sha512(SALT|status||||||udf5|udf4|udf3|udf2|udf1|email|firstname|productinfo|amount|txnid|key)
    If additionalCharges present:
      sha512(additionalCharges|SALT|status|....)
    """
    try:
        key = posted.get("key", "")
        salt = settings.PAYU_SALT
        status = posted.get("status", "")
        txnid = posted.get("txnid", "")
        amount = posted.get("amount", "")
        productinfo = posted.get("productinfo", "")
        email = posted.get("email", "")
        firstname = posted.get("firstname", "")
        udf = [posted.get(f"udf{i}", "") for i in range(1, 6)]

        rev_seq_base = "|".join([
            salt, status, "", "", "", "", "",    # 7 blanks per PayU doc alignment (udf10..udf6)
            udf[4], udf[3], udf[2], udf[1], udf[0],
            email, firstname, productinfo, amount, txnid, key
        ])

        add_charges = posted.get("additionalCharges")
        if add_charges:
            rev_seq = "|".join([add_charges, rev_seq_base])
        else:
            rev_seq = rev_seq_base

        calc_hash = hashlib.sha512(rev_seq.encode("utf-8")).hexdigest().lower()
        return hmac.compare_digest(calc_hash, posted.get("hash", "").lower())
    except Exception:
        return False


