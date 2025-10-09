# cartwatch/utils.py
from django.http import HttpRequest

SESSION_KEY_NAME = "session_id"

def get_session_id(request: HttpRequest) -> str:
    # 1) Try explicit session_id in Django session (if you ever mirror it)
    sid = request.session.get(SESSION_KEY_NAME)
    if sid:
        return sid

    # 2) Try cookie 'session_id' (if your frontend sets it)
    sid = request.COOKIES.get(SESSION_KEY_NAME, "")
    if sid:
        return sid

    # 3) Fallback: use Django's own session key (always available after save())
    if not request.session.session_key:
        request.session.save()  # ensure a session is created
    return request.session.session_key or ""

def get_client_ip(request: HttpRequest) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")
