# siteconfig/urls.py
from django.urls import path
from .views import newsletter_signup

urlpatterns = [
    path("newsletter/signup/", newsletter_signup, name="newsletter_signup"),
]
