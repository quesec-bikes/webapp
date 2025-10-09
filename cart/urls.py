from django.urls import path
from . import views

app_name = 'cart'

urlpatterns = [
    path('batch-add/', views.batch_add, name='cart_batch_add'),
    path('', views.cart_page, name='cart_page'),
    path('add/<int:variant_id>/', views.add_to_cart, name='cart_add'),
    path('update/<int:variant_id>/', views.update_item, name='cart_update'),
    path('remove/<int:variant_id>/', views.remove_item, name='cart_remove'),
    path('clear/', views.clear_cart, name='cart_clear'),
    path('api/coupons/for-cart/', views.coupons_for_cart, name='coupons_for_cart'),
    path('api/coupons/apply/', views.api_apply_coupon, name='api_apply_coupon'),
    path("checkout/", views.checkout_page, name="checkout"),
    path("api/pincode/", views.api_pincode, name="api_pincode"),
    path("checkout/step-2/", views.checkout_step2, name="checkout_step2"),
    path("checkout/success/", views.checkout_success, name="checkout_success"),
]