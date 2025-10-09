"""
URL configuration for quesec project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from . import views

urlpatterns = [
    path("admin/", admin.site.urls),

    # home
    path("", views.home, name="home"),

    # siteconfig
    path("siteconfig/", include("siteconfig.urls")),

    # cart first, with a fixed prefix
    path('cart/', include(('cart.urls', 'cart'), namespace='cart')),

    # cartwatch
    path("cartwatch/", include("cartwatch.urls")),

    # accounts, with a fixed prefix
    path("accounts/", include("accounts.urls")),

    # orders, with a fixed prefix
    path("orders/", include("orders.urls", namespace="orders")),

    # pages
    path("page/", include(("pages.urls", "pages"), namespace="pages")),

    # then shop (jisme catch-all slugs honge)
    path("", include(("shop.urls", "shop"), namespace="shop")),
    
    path("ckeditor5/", include('django_ckeditor_5.urls')),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Custom error handlers
handler404 = "quesec.views.custom_404"
handler500 = "quesec.views.custom_500"
handler403 = "quesec.views.custom_403"
handler400 = "quesec.views.custom_400"