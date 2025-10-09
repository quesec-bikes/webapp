# shop/utils.py
import hashlib
from math import ceil
from django.utils import timezone

DEAL_CAP = 100          # virtual cap (UI only)
VIRTUAL_MAX_DRAIN = 30  # time-based virtual claims within the promo window

def _personal_offset(session_key: str, variant_id: int, promo_end) -> int:
    """
    Stable 1–2 units offset per user+variant+deal-period.
    Same user refresh/revisit pe same rahega; new deal window pe naya.
    """
    seed = f"{session_key}:{variant_id}:{promo_end.isoformat()}"
    h = hashlib.sha1(seed.encode("utf-8")).hexdigest()
    return 1 + (int(h[:2], 16) % 2)  # 1 or 2

def deal_progress(request, variant, cap: int = DEAL_CAP):
    """
    Returns dict with UI numbers for the Available/Claimed line + bar + timer.
    - Uses ONLY virtual cap (100) for hype.
    - Guards: promo active + real stock > 0 required (filter in view).
    - base_claimed = real_claimed(optional) + reservations(optional) + virtual_drain
    - shown_* adds a stable personal offset (1–2) so refresh pe same rahe.
    """
    now = timezone.now()
    start = getattr(variant, "promo_start", None)
    end = getattr(variant, "promo_end", None)

    if not start or not end or end <= now or start >= end:
        return None

    total = (end - start).total_seconds()
    elapsed = max(0, (now - start).total_seconds())
    elapsed_ratio = min(1.0, elapsed / total) if total > 0 else 1.0

    # --- Virtual drain time-based (tunable) ---
    virtual_drain = ceil(elapsed_ratio * VIRTUAL_MAX_DRAIN)

    # --- Real claimed in promo (optional hook; keep 0 if you don't track) ---
    real_claimed = 0
    if hasattr(variant, "orders_in_promo"):
        try:
            real_claimed = int(variant.orders_in_promo(start, end))  # implement if you want
        except Exception:
            real_claimed = 0

    # --- Active reservations (optional; carts/checkout soft-holds) ---
    active_reservations = 0
    if hasattr(variant, "active_reservations"):
        try:
            active_reservations = int(variant.active_reservations())  # implement if you want
        except Exception:
            active_reservations = 0

    base_claimed = min(cap, max(0, real_claimed) + max(0, active_reservations) + max(0, virtual_drain))

    # Ensure session_key exists for stable offset
    if not request.session.session_key:
        request.session.save()
    offset = _personal_offset(request.session.session_key, variant.id, end)

    shown_claimed = min(cap, max(base_claimed, base_claimed + offset))
    shown_available = max(0, cap - shown_claimed)

    # Urgency levels for microcopy/pulse
    seconds_left = max(0, int((end - now).total_seconds()))
    if shown_available <= 3 or seconds_left <= 3600:
        urgency = "critical"
    elif shown_available <= 10:
        urgency = "low"
    elif shown_available <= 20:
        urgency = "mid"
    else:
        urgency = "normal"

    return {
        "cap": cap,
        "base_claimed": base_claimed,
        "shown_claimed": shown_claimed,
        "shown_available": shown_available,
        "seconds_left": seconds_left,
        "urgency": urgency,
        "end_iso": end.isoformat(),
    }
