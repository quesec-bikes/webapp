from django.urls import path
from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_page, name="login"),
    path("request-otp/", views.request_otp, name="request_otp"),
    path("verify-otp/", views.verify_otp, name="verify_otp"),
    path("logout/", views.logout_view, name="logout"),
]
