# quesec/views.py
from django.utils import timezone
from django.urls import reverse
from django.shortcuts import render
from siteconfig.models import SiteBranding, ContactBlock, SocialLink
from shop.models import Variant
from shop.utils.deal_progress import deal_progress
from shop.services.featured import get_featured_tabs, get_featured_categories_tabs
from shop.services.testimonials import get_home_testimonials
from shop.models import Category
from shop.utils.seo import build_canonical

def _canonical_product_url(product):
    cat = product.category
    if cat and cat.parent:
        return reverse(
            "shop:product_detail_child",
            kwargs={
                "parent_slug": cat.parent.slug,
                "child_slug": cat.slug,
                "slug": product.slug,
            },
        )
    elif cat:
        return reverse(
            "shop:product_detail_parent",
            kwargs={"parent_slug": cat.slug, "slug": product.slug},
        )
    return f"/{product.slug}"

def home(request):
    now = timezone.now()

    deals_qs = (
        Variant.objects
        .select_related("product", "size", "product__category", "product__category__parent")
        .filter(
            is_active=True,
            product__is_active=True,
            stock_qty__gt=0,                # ✅ real stock gate
            promo_price__isnull=False,
            promo_start__lte=now,
            promo_end__gt=now,
        )
        .order_by("promo_end")[:18]
    )

    deal_cards = []
    for v in deals_qs:
        prog = deal_progress(request, v)
        if not prog:
            continue

        card = v.card_info()                 # <-- image, title, price, mrp, discount
        href = v.get_absolute_url()          # <-- canonical product URL + ?variant=

        deal_cards.append({
            "variant": v,
            "title": card["title"],
            "href": href,
            "thumb": card["img"],            # image url (variant→product→placeholder)
            "promo_price": card["price"],    # promo active? to promo_price; else sale_price
            "mrp": card["mrp"],              # old-price (strike-through)
            "discount_pct": card["discount"] or 0,
            "progress": prog,
        })

    featured_tabs = get_featured_tabs(limit=12, per_tab_limit=12)
    featured_category_tabs = get_featured_categories_tabs(
        limit_per_cat=5,
        max_sections=6,
        use_cache=True,
    )
    testimonials = get_home_testimonials(limit=9)
    featured_cats = Category.objects.filter(is_active=True, featured=True).order_by("display_order", "id")
    branding = SiteBranding.objects.first()
    contact_block = ContactBlock.objects.first()
    social_links = list(SocialLink.objects.filter(is_active=True))

    seo = {
        "index": 1,
        "canonical": build_canonical(request, keep_variant=False),
        "og_type": "website",
    }
    ctx = {
        "deal_cards": deal_cards,
        "featured_tabs": featured_tabs, 
        "featured_category_tabs": featured_category_tabs,
        "testimonials": testimonials,
        "featured_categories": featured_cats,
        "branding": branding,
        "contact_block": contact_block,
        "social_links": social_links,
        "seo": seo,
    }
    return render(request, "home/index.html", ctx)

# --- Error handlers ---

def custom_404(request, exception):
    # Not Found
    return render(request, "errors/404.html", status=404)

def custom_500(request):
    # Server Error
    return render(request, "errors/500.html", status=500)

def custom_403(request, exception):
    # Forbidden (CSRF fail bhi yahi aata hai)
    return render(request, "errors/403.html", status=403)

def custom_400(request, exception):
    # Bad Request
    return render(request, "errors/400.html", status=400)