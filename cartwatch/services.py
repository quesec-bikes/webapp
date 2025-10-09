# cartwatch/services.py
from typing import Dict, Any
from django.utils import timezone
from .models import CartWatchLead, CartWatchStatus
from .validators import normalize_indian_phone, is_valid_10_digit_indian_phone
from .utils import get_session_id, get_client_ip

def create_or_update_lead(*, request, phone: str, cart_snapshot: Dict[str, Any] | None = None, source_url: str = "") -> CartWatchLead | None:
    """
    Phone valid होते ही call karo. Same session_id par single open lead maintain karta hai.
    """
    session_id = get_session_id(request)
    if not session_id:
        return None

    phone_norm = normalize_indian_phone(phone)
    if not is_valid_10_digit_indian_phone(phone_norm):
        return None

    cart_snapshot = cart_snapshot or {}
    ip = get_client_ip(request) or None
    ua = request.META.get("HTTP_USER_AGENT", "")

    lead, _ = CartWatchLead.objects.get_or_create(
        session_id=session_id,
        status=CartWatchStatus.OPEN,
        defaults={
            "phone": phone_norm,
            "cart_snapshot": cart_snapshot,
            "source_url": source_url[:2000] if source_url else "",
            "ip_address": ip,
            "user_agent": ua[:8000],
        },
    )
    # Update if phone or snapshot changed, and bump last_seen
    changed = False
    if lead.phone != phone_norm:
        lead.phone = phone_norm
        changed = True
    if cart_snapshot and cart_snapshot != lead.cart_snapshot:
        lead.cart_snapshot = cart_snapshot
        changed = True
    if source_url and source_url != lead.source_url:
        lead.source_url = source_url[:2000]
        changed = True

    lead.last_seen_at = timezone.now()
    if changed:
        lead.save(update_fields=["phone", "cart_snapshot", "source_url", "last_seen_at", "updated_at"])
    else:
        lead.save(update_fields=["last_seen_at", "updated_at"])
    return lead

def mark_converted_by_session(*, request, order_id: str = "") -> int:
    """
    Order success पर call karo. OPEN leads (same session) -> CONVERTED.
    Returns: count converted
    """
    session_id = get_session_id(request)
    if not session_id:
        return 0
    qs = CartWatchLead.objects.filter(session_id=session_id, status=CartWatchStatus.OPEN)
    count = 0
    for lead in qs:
        lead.mark_converted(order_id=order_id)
        count += 1
    return count

def mark_converted_by_explicit_session(session_id: str, order_id: str = "") -> int:
    """
    Agar tum order flow me explicit session_id pass kar rahe ho to ye use karo.
    """
    if not session_id:
        return 0
    qs = CartWatchLead.objects.filter(session_id=session_id, status=CartWatchStatus.OPEN)
    count = 0
    for lead in qs:
        lead.mark_converted(order_id=order_id)
        count += 1
    return count
