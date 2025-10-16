# siteconfig/sitemaps.py
from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from django.utils import timezone
from django.db.models import Max

from shop.models import Category, Product, Variant


def _site_lastmod():
    """
    Site-level lastmod: latest update from products/variants only.
    (Category has no updated_at in your schema)
    """
    last_variant = Variant.objects.filter(is_active=True).aggregate(m=Max("updated_at")).get("m")
    last_product = Product.objects.filter(is_active=True).aggregate(m=Max("updated_at")).get("m")
    last = max([d for d in [last_variant, last_product] if d is not None], default=None)
    return last or timezone.now()


class HomeSitemap(Sitemap):
    changefreq = "daily"
    priority = 1.0
    def items(self):
        return ["__home__"]
    def location(self, item):
        try:
            return reverse("home")
        except Exception:
            return "/"
    def lastmod(self, item):
        return _site_lastmod()


class ShopSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.9
    def items(self):
        return ["shop:shop_index"]
    def location(self, item):
        return reverse(item)
    def lastmod(self, item):
        return _site_lastmod()


class CategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Category.objects.filter(is_active=True)

    def location(self, obj: Category):
        if hasattr(obj, "get_absolute_url"):
            return obj.get_absolute_url()
        if obj.parent_id:
            return f"/{obj.parent.slug}/{obj.slug}/"
        return f"/{obj.slug}/"

    def lastmod(self, obj: Category):
        """
        Category ke liye lastmod = max(updated_at of products/variants under this category).
        (Category me updated_at nahi hai, so don't query it.)
        """
        # direct products in this category
        p_last = (Product.objects
                  .filter(is_active=True, category=obj)
                  .aggregate(m=Max("updated_at"))
                  .get("m"))
        v_last = (Variant.objects
                  .filter(is_active=True, product__is_active=True, product__category=obj)
                  .aggregate(m=Max("updated_at"))
                  .get("m"))
        last = max([d for d in [p_last, v_last] if d is not None], default=None)
        return last or timezone.now()


class ProductVariantSitemap(Sitemap):
    changefreq = "daily"
    priority = 0.9

    def items(self):
        return (Variant.objects
                .filter(is_active=True, product__is_active=True, product__category__is_active=True)
                .select_related("product__category", "product__category__parent"))

    def location(self, v: Variant):
        p = v.product
        cat = p.category
        if getattr(cat, "parent_id", None):
            path = reverse("shop:product_detail_child", kwargs={
                "parent_slug": cat.parent.slug,
                "child_slug": cat.slug,
                "slug": p.slug
            })
        else:
            path = reverse("shop:product_detail_parent", kwargs={
                "parent_slug": cat.slug,
                "slug": p.slug
            })
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}variant={v.id}"

    def lastmod(self, v: Variant):
        return getattr(v, "updated_at", None) or getattr(v.product, "updated_at", None) or timezone.now()


SITEMAPS = {
    "home": HomeSitemap,
    "shop": ShopSitemap,
    "categories": CategorySitemap,
    "products": ProductVariantSitemap,
}
