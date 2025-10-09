# shop/urls.py
from django.urls import path
from . import views

app_name = "shop"

urlpatterns = [
    # ---------- API routes: KEEP THESE FIRST ----------
    path("api/fbt/", views.api_fbt, name="api_fbt"),

    # Coupons APIs (both spellings supported; same names as templates)
    path("api/coupons/for-product/", views.api_coupons_for_product, name="api_coupons_for_product"),
    path("api/coupons-for-product/", views.api_coupons_for_product),  # alias for safety

    path("api/coupons/apply/", views.api_coupons_apply, name="api_coupons_apply"),
    path("api/coupons-apply/", views.api_coupons_apply),               # alias

    path("api/coupons/for-cart/", views.coupons_for_cart, name="coupons_for_cart"),
    path("api/coupons/list/", views.coupons_list, name="coupons_list"),
    
    # ----- NEW: Search endpoints -----
    path("search/", views.search_results, name="search_results"),
    path("api/search/", views.api_search, name="api_search"),
    path("api/track/click/", views.api_track_click, name="api_track_click"),
    path("api/categories/", views.api_categories, name="api_categories"),

    path('products/<slug:slug>/reviews/', views.reviews_list, name='reviews_list'),
    path('reviews/create/', views.review_create, name='review_create'), 

    # SHOP PAGE
    path("shop/", views.shop_index, name="shop_index"),

    # CATEGORY PAGES
    path("<slug:parent_slug>/", views.category_listing, name="category_parent"),
    path("<slug:parent_slug>/<slug:child_slug>/", views.category_listing, name="category_child"),

    # ---------- Slug PDP routes: KEEP THESE LAST ----------
    path("<slug:parent_slug>/<slug:slug>/", views.product_detail, name="product_detail_parent"),
    path("<slug:parent_slug>/<slug:child_slug>/<slug:slug>/", views.product_detail, name="product_detail_child"),

    
]
