# siteconfig/views.py
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.contrib import messages
from django.views.decorators.http import require_POST
from .models import NewsletterSignup

@require_POST
def newsletter_signup(request):
    email = (request.POST.get("email") or "").strip()
    next_url = request.META.get("HTTP_REFERER") or "/"
    if not email:
        messages.error(request, "Please enter a valid email.")
        return HttpResponseRedirect(next_url)
    try:
        obj, created = NewsletterSignup.objects.get_or_create(email=email, defaults={
            "source_url": request.path,
        })
        if created:
            messages.success(request, "You're subscribed!")
        else:
            messages.info(request, "You're already on the list.")
    except Exception:
        messages.error(request, "Something went wrong. Please try again.")
    return HttpResponseRedirect(next_url)
