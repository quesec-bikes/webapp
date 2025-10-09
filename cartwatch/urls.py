# cartwatch/urls.py
from django.urls import path
from .views import capture_lead

app_name = "cartwatch"

urlpatterns = [
    path("capture/", capture_lead, name="capture"),
]
