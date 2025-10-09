# orders/urls.py
from django.urls import path
from . import views

app_name = "orders"

urlpatterns = [
    path("create/", views.create_order, name="create"),
    path("initiate/", views.initiate_payment, name="initiate"),
    path("success/<str:order_number>/", views.order_success, name="success"),
    path("failed/<str:order_number>/", views.order_failed, name="failed"),

    # Razorpay
    path("razorpay/callback/", views.razorpay_callback, name="razorpay_callback"),
    path("razorpay/webhook/", views.razorpay_webhook, name="razorpay_webhook"),  # optional

    # PayU
    path("payu/redirect/", views.payu_redirect, name="payu_redirect"),          # auto-post form
    path("payu/callback/", views.payu_callback, name="payu_callback"),
]
