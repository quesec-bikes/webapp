from django.shortcuts import render, redirect, get_object_or_404
from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.conf import settings
from django.urls import reverse
from accounts.models import Profile
from .models import Order, PaymentAttempt
from cartwatch.services import mark_converted_by_session
from .services import (
    snapshot_cart_to_order,
    semi_cod_allowed,
    calc_semi_cod_advance,
    # Razorpay
    init_razorpay_order,
    verify_razorpay_signature,
    # PayU
    build_payu_payload,
    verify_payu_response_hash,
)

# ---- (NEW) Cart/session cleanup helper ----
# Prefer project-defined cart session key; fallback to common names if missing.
try:
    # If your project exposes CART_SESSION_KEY from cart.utils (recommended)
    from cart.utils import CART_SESSION_KEY  # type: ignore
except Exception:
    CART_SESSION_KEY = "shop_cart"  # safe fallback


def _clear_checkout_state(request):
    """
    Idempotent cleanup after successful payment:
    - Clear session-based cart
    - Clear checkout form/address session data
    Notes:
      * Does NOT log the user out or touch auth session
      * Safe to call multiple times
    """
    try:
        # Try using Cart class if available (optional, skip on import error)
        try:
            from cart.utils import Cart  # type: ignore
            try:
                Cart(request).clear()
            except Exception:
                # Fallback pop by key
                request.session.pop(CART_SESSION_KEY, None)
        except Exception:
            request.session.pop(CART_SESSION_KEY, None)
    except Exception:
        pass


def _update_profile_from_order(user, order):
    """
    Order ke snapshot se accounts.Profile ko update karta hai.
    Blank values se overwrite NAHI karta (sirf non-empty values set hoti hain).
    Field mapping (Order -> Profile):
      full_name -> full_name
      mobile    -> mobile
      address_line -> address
      pincode   -> pincode
      city      -> city
      state     -> state
      gstin     -> gst
    """
    try:
        profile, _ = Profile.objects.get_or_create(user=user)

        mapping = {
            "full_name": getattr(order, "full_name", "") or "",
            "mobile": getattr(order, "mobile", "") or "",
            "address": getattr(order, "address_line", "") or "",
            "pincode": getattr(order, "pincode", "") or "",
            "city": getattr(order, "city", "") or "",
            "state": getattr(order, "state", "") or "",
            "gst": getattr(order, "gstin", "") or "",
        }

        dirty = False
        for field, val in mapping.items():
            val = (val or "").strip()
            if val:  # sirf non-empty overwrite
                if getattr(profile, field, "") != val:
                    setattr(profile, field, val)
                    dirty = True

        if dirty:
            profile.save()
    except Exception:
        # fail-safe: profile issues kabhi payment flow ko na tode
        pass

# ---- User attach + auto-login helper ----
def _attach_user_and_auto_login(request, order, posted_email=None, posted_name=None):
    """Ensure an auth user exists for this order.email and attach the order.
    If browser request present, auto-login that user.
    Idempotent: safe to call multiple times.
    """
    try:
        User = get_user_model()
        email = (
            posted_email
            or getattr(order, "email", "")
            or (request.session.get("checkout_form", {}) or {}).get("email", "")
            or ""
        ).strip()
        if not email:
            return

        # Find / create user (case-insensitive on email)
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            base_username = (email.split("@")[0] or "user")
            username = base_username
            i = 1
            while User.objects.filter(username=username).exists():
                i += 1
                username = f"{base_username}{i}"
            user = User.objects.create_user(username=username, email=email)
            try:
                user.set_unusable_password()
                user.save(update_fields=["password"])
            except Exception:
                pass

        # Attach order.user
        if not order.user_id or order.user_id != user.id:
            order.user = user
            try:
                order.save(update_fields=["user"])
            except Exception:
                order.save()

        # >>> ADD THIS: profile ko order se sync karo
        _update_profile_from_order(user, order)

        # Optional: set first_name from posted_name/order.full_name
        name = posted_name or getattr(order, "full_name", "") or ""
        if name and hasattr(user, "first_name") and not getattr(user, "first_name", "").strip():
            first = str(name).split(" ")[0][:30]
            try:
                user.first_name = first
                user.save(update_fields=["first_name"])
            except Exception:
                pass

        # Auto-login (callbacks run in browser context)
        try:
            if hasattr(request, "user") and (not request.user.is_authenticated or request.user.id != user.id):
                login(request, user)
        except Exception:
            pass

    except Exception:
        # hard-fail avoid
        return


def create_order(request):
    """
    (Optional path) Step-2 page se direct POST aane par cart/session ko snapshot karke
    Order(status=CREATED) banata hai. Most cases me hum Step-2 render time par hi snapshot
    create kar rahe hain, par yeh endpoint bhi available hai.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")

    # ↓↓↓ Project ke real data se map karo ↓↓↓
    user = request.user if request.user.is_authenticated else None
    addr = request.session.get("checkout_address") or {
        "full_name": "",
        "mobile": "",
        "email": "",
        "address_line": "",
        "pincode": "",
        "city": "",
        "state": "",
    }
    order = snapshot_cart_to_order(request, user=user, address=addr)
    messages.success(request, "Order created.")
    return redirect("orders:initiate")  # or directly to payment options page


def initiate_payment(request):
    """
    Accepts POST: order_number, payment_method (razorpay|payu|semi_cod)
    Semi-COD guard bhi isi me apply hota hai.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")

    order_number = request.POST.get("order_number")
    method = request.POST.get("payment_method")
    if not (order_number and method):
        return HttpResponseBadRequest("Missing params")

    order = get_object_or_404(Order, order_number=order_number, status=Order.Status.CREATED)

    # Semi-COD guard (rule rupees me 5000..27000)
    if method == "semi_cod":
        if not semi_cod_allowed(order.grand_total // 100):
            messages.error(request, "Semi-COD not available for this amount.")
            return redirect("orders:failed", order_number=order.order_number)

    # Razorpay flow (full or advance)
    if method in ("razorpay", "semi_cod"):
        return init_razorpay_order(request, order, advance_only=(method == "semi_cod"))

    # PayU flow
    if method in ("payu", "upi_hdfc_any"):
        upi_only = (method == "upi_hdfc_any")
        payu_url, payload = build_payu_payload(request, order, upi_only=upi_only)
        return render(request, "orders/payu_redirect.html", {"payu_url": payu_url, "payload": payload})

    return HttpResponseBadRequest("Unsupported method")


def order_success(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    _attach_user_and_auto_login(request, order)
    try:
        mark_converted_by_session(request=request, order_id=str(order.id))
    except Exception as e:
        # safe fail — don't block order success page
        print("CartWatch conversion failed:", e)
    _clear_checkout_state(request)
    return render(request, "orders/success.html", {"order": order})


def order_failed(request, order_number):
    order = get_object_or_404(Order, order_number=order_number)
    return render(request, "orders/failed.html", {"order": order})


# ------------------- Razorpay callbacks -------------------

@csrf_exempt
def razorpay_callback(request):
    """
    Razorpay standard checkout POST:
    Fields: razorpay_order_id, razorpay_payment_id, razorpay_signature
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")

    rp_order_id = request.POST.get("razorpay_order_id")
    rp_payment_id = request.POST.get("razorpay_payment_id")
    rp_signature = request.POST.get("razorpay_signature")

    pa = PaymentAttempt.objects.filter(provider_order_id=rp_order_id).order_by("-id").first()
    if not pa:
        return HttpResponseBadRequest("PaymentAttempt not found")

    ok = verify_razorpay_signature(rp_order_id, rp_payment_id, rp_signature)
    if not ok:
        pa.status = PaymentAttempt.Status.FAILED
        pa.provider_payment_id = rp_payment_id
        pa.provider_signature = rp_signature
        pa.raw_payload = dict(request.POST)
        pa.save(update_fields=["status", "provider_payment_id", "provider_signature", "raw_payload"])
        messages.error(request, "Payment verification failed. You were not charged. Please try again.")
        return redirect("orders:failed", order_number=pa.order.order_number)

    # Success
    pa.status = PaymentAttempt.Status.SUCCESS
    pa.provider_payment_id = rp_payment_id
    pa.provider_signature = rp_signature
    pa.raw_payload = dict(request.POST)
    pa.save(update_fields=["status", "provider_payment_id", "provider_signature", "raw_payload"])

    order = pa.order
    if pa.amount >= order.grand_total:
        order.mark_paid_full()
    else:
        order.mark_paid_partial(pa.amount)

    _attach_user_and_auto_login(request, order)

    # (NEW) Clear cart + checkout session on success
    _clear_checkout_state(request)

    return redirect("orders:success", order_number=order.order_number)


# ------------------- PayU callbacks -------------------

@csrf_exempt
def payu_callback(request):
    """
    PayU returns to this callback (POST) with fields incl. status, hash, txnid, email, amount, etc.
    """
    if request.method != "POST":
        return HttpResponseBadRequest("Invalid method")

    posted = {k: (v if isinstance(v, str) else v[0]) for k, v in request.POST.lists()}
    status = posted.get("status", "").lower()
    txnid = posted.get("txnid")

    pa = PaymentAttempt.objects.filter(provider_order_id=txnid).order_by("-id").first()
    if not pa:
        return HttpResponseBadRequest("PaymentAttempt not found")

    # Early exit on failure/invalid hash
    verified = verify_payu_response_hash(posted)
    if not verified or status != "success":
        pa.status = PaymentAttempt.Status.FAILED
        pa.raw_payload = posted
        pa.save(update_fields=["status", "raw_payload"])
        messages.error(request, "Payment verification failed. You were not charged. Please try again.")
        return redirect("orders:failed", order_number=pa.order.order_number)

    # Success
    pa.status = PaymentAttempt.Status.SUCCESS
    pa.provider_payment_id = posted.get("payuMoneyId") or posted.get("mihpayid")
    pa.raw_payload = posted
    pa.save(update_fields=["status", "provider_payment_id", "raw_payload"])

    order = pa.order
    if pa.amount >= order.grand_total:
        order.mark_paid_full()
    else:
        order.mark_paid_partial(pa.amount)

    # Try to use posted email/name if present; else helper will pick from order fields.
    _attach_user_and_auto_login(request, order, posted_email=posted.get("email"), posted_name=posted.get("firstname"))

    # (NEW) Clear cart + checkout session on success (parity with Razorpay)
    _clear_checkout_state(request)

    return redirect("orders:success", order_number=order.order_number)


def payu_redirect(request):
    # reserved (we directly render payu_redirect.html in initiate_payment)
    return HttpResponse("OK")


@csrf_exempt
def razorpay_webhook(request):
    """
    Optional webhook endpoint (configure in Razorpay dashboard).
    Verify header: X-Razorpay-Signature with your webhook secret.
    """
    try:
        # Intentionally minimal; extend as per events (payment.captured etc.)
        return HttpResponse(status=200)
    except Exception:
        return HttpResponse(status=400)
