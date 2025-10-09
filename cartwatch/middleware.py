# cartwatch/middleware.py
from django.utils import timezone
from .models import CartWatchLead, CartWatchStatus
from .utils import get_session_id, SESSION_KEY_NAME

class CartWatchLastSeenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)

        try:
            # Ensure we have a stable session id (and set cookie if missing)
            sid = get_session_id(request)
            if sid:
                # bump last_seen for any OPEN lead on this session
                CartWatchLead.objects.filter(
                    session_id=sid, status=CartWatchStatus.OPEN
                ).update(last_seen_at=timezone.now())

                # set cookie if not present
                if not request.COOKIES.get(SESSION_KEY_NAME):
                    # 7 days (tune as you like)
                    max_age = 7 * 24 * 60 * 60
                    response.set_cookie(
                        SESSION_KEY_NAME,
                        sid,
                        max_age=max_age,
                        httponly=False,     # frontend can read if needed
                        samesite="Lax",
                    )
        except Exception:
            pass

        return response
