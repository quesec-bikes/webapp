# cartwatch/views.py
import json
from django.views.decorators.http import require_POST
from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt, csrf_protect
from .services import create_or_update_lead

@require_POST
@csrf_protect
def capture_lead(request: HttpRequest):
    """
    Expects:
      phone: string
      cart_snapshot: JSON string or object (optional)
      source_url: string (optional)
    """
    phone = request.POST.get("phone", "") or ""
    source_url = request.POST.get("source_url", "") or request.META.get("HTTP_REFERER", "")

    cart_snapshot_raw = request.POST.get("cart_snapshot", "")
    cart_snapshot = {}
    if cart_snapshot_raw:
        try:
            cart_snapshot = json.loads(cart_snapshot_raw)
        except Exception:
            cart_snapshot = {}

    lead = create_or_update_lead(request=request, phone=phone, cart_snapshot=cart_snapshot, source_url=source_url)
    if not lead:
        return JsonResponse({"ok": False, "message": "Invalid session or phone"}, status=400)

    return JsonResponse({"ok": True, "lead_id": lead.id, "status": lead.status})
