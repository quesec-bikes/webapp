from __future__ import annotations
from typing import Dict, Any
from django_ckeditor_5.fields import CKEditor5Field
from django.db import models
from django.utils.text import slugify
from django.urls import reverse
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db.models import JSONField, Q
from django.utils import timezone
from django.conf import settings
from django.templatetags.static import static



# ---------- Category (Parent–Child tree) ----------
class Category(models.Model):
    name = models.CharField(max_length=120, unique=True)
    slug = models.SlugField(max_length=140, unique=True)
    parent = models.ForeignKey(
        "self", null=True, blank=True, related_name="children", on_delete=models.CASCADE
    )
    image = models.ImageField(
        upload_to="categories/%Y/%m/",
        blank=True, null=True,
        help_text="Ideal 600x600px, <200KB"
    )
    display_order = models.PositiveIntegerField(default=00, db_index=True)
    featured = models.BooleanField(default=False, db_index=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Categories"
        indexes = [models.Index(fields=["slug"])]
        ordering = ("display_order", "name", "id")

    def __str__(self):
        return self.full_path()
    
    def get_absolute_url(self):
        parent = getattr(self, "parent", None)
        if parent:
            return reverse("shop:category_child", kwargs={
                "parent_slug": parent.slug,
                "child_slug": self.slug,
            })
        return reverse("shop:category_parent", kwargs={
            "parent_slug": self.slug,
        })
    
    def image_tag(self):
        from django.utils.html import format_html
        return format_html('<img src="{}" style="height:40px;border-radius:6px;">', self.image.url) if self.image else "—"
    image_tag.short_description = "Image"

    def full_path(self) -> str:
        # e.g., Bicycles > Foldable > 20-inch
        parts = [self.name]
        p = self.parent
        while p:
            parts.append(p.name)
            p = p.parent
        return " > ".join(reversed(parts))

    def path_slugs(self) -> list[str]:
        # e.g., ["bicycles", "foldable", "20-inch"]
        parts = [self.slug]
        p = self.parent
        while p:
            parts.append(p.slug)
            p = p.parent
        return list(reversed(parts))

    @property
    def is_leaf(self) -> bool:
        return not self.children.exists()

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = base_unique_slug(Category, self.name)
        super().save(*args, **kwargs)


# ---------- Color & Size master ----------
class Color(models.Model):
    name = models.CharField(max_length=64, unique=True)
    hex_code = models.CharField(max_length=7, help_text="#RRGGBB", default="#000000")

    def __str__(self):
        return self.name


class Size(models.Model):
    name = models.CharField(max_length=64, unique=True)
    sort_order = models.IntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


# ---------- Product (SPU) ----------
FBT_STRATEGY_CHOICES = (
    ('FIRST_IN_STOCK','First in stock'),
    ('SAME_ATTRIBUTE_MATCH','Same attribute match'),
    ('PRICE_NEAREST','Price nearest'),
)

class Product(models.Model):
    title = models.CharField(max_length=180)
    slug = models.SlugField(max_length=200, unique=True)
    # FBT defaults (product-level)
    fbt_defaults = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='fbt_default_for')
    fbt_variant_strategy = models.CharField(max_length=32, choices=FBT_STRATEGY_CHOICES, default='FIRST_IN_STOCK')
    short_description = CKEditor5Field('Short Description', config_name='default', blank=True, null=True)

    # ✅ Single category only (old: categories M2M + primary_category removed)
    category = models.ForeignKey(
        Category, null=True, blank=True, related_name="products", on_delete=models.SET_NULL
    )

    # SEO
    meta_description = models.TextField(max_length=160, blank=True)

    # Flags
    is_active = models.BooleanField(default=True)
    is_published = models.BooleanField(default=False)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["slug"]), models.Index(fields=["is_active", "is_published"])]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = base_unique_slug(Product, self.title)
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        # /bicycles/foldable/20-inch/<product-slug> (if category set)
        if self.category:
            path = "/".join(self.category.path_slugs())
            return reverse("product-detail-slug", kwargs={"category_path": path, "slug": self.slug})
        return reverse("product-detail", kwargs={"slug": self.slug})

    @property
    def primary_image(self):
        return self.images.order_by("sort_order").first()

    # Build a dict from Specification rows (for Variant merge)
    def specs_dict(self) -> Dict[str, str]:
        return {s.title: s.value for s in self.specifications.all()}


class Specification(models.Model):
    """
    Key-Value specifications for a Product.
    Example:
      title='Frame', value='Carbon steel foldable frame'
    """
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="specifications")
    title = models.CharField(max_length=120)
    value = models.CharField(max_length=500)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]
        unique_together = [("product", "title")]

    def __str__(self):
        return f"{self.product.title} · {self.title}"


class ProductImage(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="products/%Y/%m/")
    alt_text = models.CharField(max_length=140, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.product.title} #{self.id}"


# ---------- Variant (SKU) ----------
class VariantQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_active=True, product__is_active=True)

    def in_stock(self):
        # NOTE: yahan aapke project ke hisaab se tweak kar sakte ho
        return self.filter(Q(stock_qty__gt=0) | Q(backorder_allowed=True))

    def featured(self):
        return self.active().in_stock().filter(featured=True)
    
class Variant(models.Model):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="variants")
    sku = models.CharField(max_length=64, unique=True)

    amazon_url = models.URLField(max_length=500, blank=True, help_text="Amazon listing URL for this SKU (optional)")

    # Dual-color support
    color_primary = models.ForeignKey(
        Color, null=True, blank=True, related_name="primary_variants", on_delete=models.SET_NULL
    )
    color_secondary = models.ForeignKey(
        Color, null=True, blank=True, related_name="secondary_variants", on_delete=models.SET_NULL
    )

    size = models.ForeignKey(Size, null=True, blank=True, on_delete=models.SET_NULL)

    mrp = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    sale_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)])
    delivery_price = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(0)], null=True, blank=True,)

    promo_price = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(0)],
        null=True, blank=True,
        help_text="Optional. If set with a valid window, this becomes active price during promo."
    )
    promo_start = models.DateTimeField(null=True, blank=True, help_text="Promo start (inclusive)")
    promo_end   = models.DateTimeField(null=True, blank=True, help_text="Promo end (exclusive)")

    stock_qty = models.PositiveIntegerField(default=0)
    backorder_allowed = models.BooleanField(default=False)
    featured = models.BooleanField(default=False)
    objects = VariantQuerySet.as_manager()


    weight_kg = models.DecimalField(max_digits=6, decimal_places=3, null=True, blank=True)
    length_cm = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    width_cm  = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    height_cm = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)

    is_active = models.BooleanField(default=True)

    # Per-variant overrides for specs (store ONLY changed fields)
    specs_override = JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # If NO secondary color -> unique per (product, size, primary)
            models.UniqueConstraint(
                fields=["product", "size", "color_primary"],
                condition=models.Q(color_secondary__isnull=True),
                name="uniq_product_size_primary_when_secondary_null",
            ),
            # If secondary color present -> unique per (product, size, primary, secondary)
            models.UniqueConstraint(
                fields=["product", "size", "color_primary", "color_secondary"],
                condition=models.Q(color_secondary__isnull=False),
                name="uniq_product_size_primary_secondary_when_secondary_set",
            ),
        ]
        indexes = [models.Index(fields=["product", "is_active"])]

    def __str__(self):
        parts = [self.product.title]
        c = self.color_label
        if c:
            parts.append(c)
        if self.size:
            parts.append(self.size.name)
        return f"{' / '.join(parts)} [{self.sku}]"

    @property
    def color_label(self) -> str:
        if self.color_primary and self.color_secondary:
            return f"{self.color_primary.name} & {self.color_secondary.name}"
        return self.color_primary.name if self.color_primary else ""
    
    @property
    def effective_price(self):
        if self.promo_price:
            return self.promo_price
        if self.sale_price:
            return self.sale_price
        return self.mrp

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.color_primary:
            raise ValidationError({"color_primary": "Primary color required."})
        if (
            self.color_primary
            and self.color_secondary
            and self.color_primary_id == self.color_secondary_id
        ):
            raise ValidationError({"color_secondary": "Secondary color cannot be same as primary."})
        # ✅ NEW: promo window ordering
        if self.promo_start and self.promo_end and self.promo_end <= self.promo_start:
            raise ValidationError({"promo_end": "Promo end must be after promo start."})

    @property
    def in_stock(self) -> bool:
        return self.stock_qty > 0 or self.backorder_allowed
    
    # ✅ NEW: helper — is promo active right now?
    def is_promo_active(self) -> bool:
        if not (self.promo_price and self.promo_start and self.promo_end):
            return False
        now = timezone.now()
        # Promo tabhi dikhayenge jab sale_price se sasta ho (varna countdown ka sense nahi)
        return (self.promo_start <= now < self.promo_end) and (self.promo_price < self.sale_price)

    # ✅ NEW: helper — current effective price (promo ya normal)
    def effective_price(self):
        return self.promo_price if self.is_promo_active() else self.sale_price

    # ✅ NEW: helper — seconds remaining for countdown
    def promo_seconds_left(self) -> int:
        if not self.is_promo_active():
            return 0
        now = timezone.now()
        return max(0, int((self.promo_end - now).total_seconds()))

    def effective_specs(self) -> Dict[str, Any]:
        """
        Merge Product.specs_dict() with specs_override (override wins).
        """
        base = dict(self.product.specs_dict() or {})
        override = dict(self.specs_override or {})
        return {**base, **override}
    
    def get_main_image_url(self):
        # 1) Variant image (agar hai)
        if hasattr(self, "images") and self.images.exists():
            img = self.images.first()
            try:
                return img.image.url
            except Exception:
                pass

        # 2) Product image fallback
        if hasattr(self, "product") and self.product.images.exists():
            img = self.product.images.first()
            try:
                return img.image.url
            except Exception:
                pass

        # 3) Last resort: ek placeholder image
        return static("https://fastly.picsum.photos/id/856/200/200.jpg")
    
    def get_absolute_url(self):
        from django.urls import reverse
        cat = getattr(self.product, "category", None)
        if cat and cat.parent:
            return reverse(
                "shop:product_detail_child",
                kwargs={
                    "parent_slug": cat.parent.slug,
                    "child_slug": cat.slug,
                    "slug": self.product.slug,
                },
            ) + f"?variant={self.id}"
        elif cat:
            return reverse(
                "shop:product_detail_parent",
                kwargs={
                    "parent_slug": cat.slug,
                    "slug": self.product.slug,
                },
            ) + f"?variant={self.id}"
        return f"/{self.product.slug}?variant={self.id}"

    # ✅ Combined helper — get full card info (title, image, link, price)
    def card_info(self):
        """
        Returns a dictionary safe to use in templates like testimonial/product cards.
        Uses promo price if active, otherwise sale price.
        """
        img_url = self.get_main_image_url()
        link = self.get_absolute_url()
        colors = (self.color_label or "").strip() 
        size = getattr(self.size, "name", "") or ""
        parts = [p for p in [colors, self.product.title, f"- {size}" if size else ""] if p]
        title = " ".join(parts).replace("  ", " ").strip()


        # ✅ Price logic
        if self.is_promo_active():
            price = self.promo_price
        else:
            price = self.sale_price

        mrp = getattr(self, "mrp", None)

        # ✅ Discount percentage (based on MRP vs display price)
        discount = None
        try:
            if mrp and price and mrp > price:
                discount = int(round(100 * (mrp - price) / mrp))
        except Exception:
            discount = None

        return {
            "title": title,
            "img": img_url,
            "link": link,
            "price": price,
            "mrp": mrp,
            "discount": discount,
            "is_promo_active": self.is_promo_active(),
        }


class VariantImage(models.Model):
    variant = models.ForeignKey(Variant, on_delete=models.CASCADE, related_name="images")
    image = models.ImageField(upload_to="variants/%Y/%m/")
    alt_text = models.CharField(max_length=140, blank=True)
    sort_order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "id"]

    def __str__(self):
        return f"{self.variant.sku} #{self.id}"


# ---------- Utilities ----------
def base_unique_slug(model_cls: type[models.Model], seed: str, slug_field: str = "slug") -> str:
    """
    Create a unique slug based on `seed`. If collision, append -2, -3, ...
    """
    base = slugify(seed)[:190] or "item"
    candidate = base
    i = 2
    lookup = {f"{slug_field}": candidate}
    while model_cls.objects.filter(**lookup).exists():
        candidate = f"{base}-{i}"
        lookup[slug_field] = candidate
        i += 1
    return candidate


# --- Frequently Bought Together ---
class FBTLink(models.Model):
    source_variant = models.ForeignKey('Variant', on_delete=models.CASCADE, related_name='fbt_links')
    target_product = models.ForeignKey('Product', on_delete=models.CASCADE, null=True, blank=True, related_name='as_fbt_target_product')
    target_variant = models.ForeignKey('Variant', on_delete=models.CASCADE, null=True, blank=True, related_name='as_fbt_target_variant')
    priority = models.PositiveIntegerField(default=10)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['priority', 'id']
        constraints = [
            models.CheckConstraint(
                check=~(Q(target_product__isnull=True) & Q(target_variant__isnull=True)),
                name='fbt_target_required'
            )
        ]

    def __str__(self):
        tgt = self.target_variant or self.target_product
        return f"FBT[{self.source_variant_id} -> {tgt}]"


class Coupon(models.Model):
    TYPE_PERCENT = "PERCENT"
    TYPE_FLAT = "FLAT"
    TYPE_CHOICES = [
        (TYPE_PERCENT, "Percent"),
        (TYPE_FLAT, "Flat"),
    ]

    SCOPE_CART = "CART"
    SCOPE_PRODUCTS = "PRODUCTS"
    SCOPE_CATEGORIES = "CATEGORIES"
    SCOPE_CHOICES = [
        (SCOPE_CART, "Cart"),
        (SCOPE_PRODUCTS, "Products"),
        (SCOPE_CATEGORIES, "Categories"),
    ]

    STATUS_ACTIVE = "ACTIVE"
    STATUS_PAUSED = "PAUSED"
    STATUS_EXPIRED = "EXPIRED"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_PAUSED, "Paused"),
        (STATUS_EXPIRED, "Expired"),
    ]

    # Identity
    code = models.CharField(max_length=32, unique=True)
    title = models.CharField(max_length=140, blank=True, default="")
    notes = models.TextField(blank=True, default="")

    # Discount
    type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    max_discount_amount = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )

    # Window & Status
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_ACTIVE)

    # Scope
    applies_to = models.CharField(max_length=16, choices=SCOPE_CHOICES, default=SCOPE_CART)
    included_products = models.ManyToManyField(
        "shop.Product", blank=True, related_name="coupon_included_set"
    )
    excluded_products = models.ManyToManyField(
        "shop.Product", blank=True, related_name="coupon_excluded_set"
    )
    included_categories = models.ManyToManyField(
        "shop.Category", blank=True, related_name="coupon_included_cat_set"
    )
    excluded_categories = models.ManyToManyField(
        "shop.Category", blank=True, related_name="coupon_excluded_cat_set"
    )
    # ⭐ Variant-level control
    included_variants = models.ManyToManyField(
        "shop.Variant", blank=True, related_name="coupon_included_variant_set",
        help_text="If set, ONLY these variants qualify."
    )
    excluded_variants = models.ManyToManyField(
        "shop.Variant", blank=True, related_name="coupon_excluded_variant_set",
        help_text="Variants to exclude even if product/category qualifies."
    )

    # Eligibility
    min_cart_subtotal = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    first_order_only = models.BooleanField(default=False)
    per_user_limit = models.PositiveIntegerField(null=True, blank=True)
    global_limit = models.PositiveIntegerField(null=True, blank=True)
    eligible_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL, blank=True, related_name="eligible_coupons"
    )

    # Visibility & Delivery
    is_public = models.BooleanField(default=True)
    show_in_listing = models.BooleanField(default=True)
    allow_deeplink = models.BooleanField(default=False)
    vanity_slug = models.SlugField(max_length=64, null=True, blank=True)

    # Stacking (future-friendly; v1 policy: single coupon)
    is_stackable = models.BooleanField(default=False)
    stack_group = models.CharField(max_length=32, null=True, blank=True)

    created = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["code"]),
            models.Index(fields=["status"]),
            models.Index(fields=["starts_at"]),
            models.Index(fields=["ends_at"]),
        ]

    def __str__(self):
        return self.code

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.upper().strip()
        super().save(*args, **kwargs)

    # Helpers
    def is_active_now(self, at=None):
        at = at or timezone.now()
        if self.status != self.STATUS_ACTIVE:
            return False
        if self.starts_at and at < self.starts_at:
            return False
        if self.ends_at and at > self.ends_at:
            return False
        return True


class CouponRedemption(models.Model):
    coupon = models.ForeignKey(Coupon, on_delete=models.PROTECT, related_name="redemptions")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="coupon_redemptions"
    )
    order_reference = models.CharField(max_length=64, blank=True, default="")  # placeholder until Order FK
    session_id = models.CharField(max_length=64, blank=True, default="")

    used_at = models.DateTimeField(auto_now_add=True)
    amount_discounted = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["used_at"]),
            models.Index(fields=["coupon", "used_at"]),
        ]

    def __str__(self):
        return f"{self.coupon.code} − ₹{self.amount_discounted} @ {self.used_at:%Y-%m-%d}"


# --- Search & Popularity tracking ---

class ProductStats(models.Model):
    """
    Site-wide popularity counters per Product.
    Used for inspiration (Top 5) and as a tie-break in search ranking.
    """
    product = models.OneToOneField("shop.Product", on_delete=models.CASCADE, related_name="stats")
    views = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    add_to_cart = models.PositiveIntegerField(default=0)
    orders = models.PositiveIntegerField(default=0)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["orders", "add_to_cart", "clicks"])]

    def score(self):
        # orders > add_to_cart > clicks > views
        return (self.orders * 5) + (self.add_to_cart * 3) + (self.clicks * 2) + self.views


class SearchClick(models.Model):
    """
    Per-query click counts for a Product (used to learn popularity within a query).
    """
    query = models.CharField(max_length=128, db_index=True)
    product = models.ForeignKey("shop.Product", on_delete=models.CASCADE, related_name="search_clicks")
    clicks = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("query", "product")
        indexes = [models.Index(fields=["query", "clicks"])]

class VariantStats(models.Model):
    variant = models.OneToOneField("shop.Variant", on_delete=models.CASCADE, related_name="stats")
    views = models.PositiveIntegerField(default=0)
    clicks = models.PositiveIntegerField(default=0)
    add_to_cart = models.PositiveIntegerField(default=0)
    orders = models.PositiveIntegerField(default=0)
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [models.Index(fields=["orders", "add_to_cart", "clicks"])]

    def score(self):
        return (self.orders * 5) + (self.add_to_cart * 3) + (self.clicks * 2) + self.views


class SearchClickVariant(models.Model):
    query = models.CharField(max_length=128, db_index=True)
    variant = models.ForeignKey("shop.Variant", on_delete=models.CASCADE, related_name="search_clicks")
    clicks = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("query", "variant")
        indexes = [models.Index(fields=["query", "clicks"])]

class Review(models.Model):
    product = models.ForeignKey(
        'shop.Product',
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    variant = models.ForeignKey(
        'shop.Variant',
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='reviews'
    )
    order_item = models.ForeignKey(
        'orders.OrderItem',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='reviews'
    )
    rating = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(5)]
    )
    title = models.CharField(max_length=140, blank=True)
    body = models.TextField(blank=True)
    is_published = models.BooleanField(default=True)
    is_verified_purchase = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            # 1 user can review a variant only once
            models.UniqueConstraint(
                fields=['user', 'variant'], name='uniq_user_variant_review'
            ),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.product} / {self.variant} — {self.user} ({self.rating})"