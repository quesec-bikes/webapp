from django.contrib import admin, messages
from django.utils.html import format_html
from django import forms
from django.utils.safestring import mark_safe
from django.urls import path, reverse
from django.shortcuts import redirect, get_object_or_404
from django.http import HttpResponseRedirect
from django.utils.translation import gettext_lazy as _
import json

from .models import (
    Category, Color, Size,
    Product, ProductImage,
    Variant, VariantImage,
    Specification,
    Coupon, CouponRedemption,
    FBTLink, VariantStats, SearchClickVariant, ProductStats, SearchClick, Review
)

# =========================
# FBT Inline
# =========================
class FBTLinkInline(admin.TabularInline):
    model = FBTLink
    fk_name = 'source_variant'  # very important
    extra = 0
    autocomplete_fields = ['target_product', 'target_variant']
    fields = ('is_active', 'priority', 'target_product', 'target_variant')
    ordering = ('priority',)

# =========================
# Pretty JSON widget
# =========================
class JSONPrettyTextarea(forms.Textarea):
    def format_value(self, value):
        if value in (None, "null", ""):
            return ""
        try:
            if isinstance(value, str):
                parsed = json.loads(value)
            else:
                parsed = value
            return json.dumps(parsed, indent=2, ensure_ascii=False)
        except Exception:
            return value

# =========================
# Variant form (effective specs preview)
# =========================
class VariantAdminForm(forms.ModelForm):
    merged_specs_preview = forms.CharField(
        label="Effective Specs (Preview)",
        required=False,
        widget=JSONPrettyTextarea(attrs={"rows": 10, "readonly": "readonly", "style": "font-family:monospace"})
    )

    class Meta:
        model = Variant
        fields = "__all__"
        widgets = {
            "specs_override": JSONPrettyTextarea(attrs={"rows": 10, "style": "font-family:monospace"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            if self.instance and self.instance.pk:
                self.fields["merged_specs_preview"].initial = self.instance.effective_specs()
            else:
                product = getattr(self.instance, "product", None)
                if product:
                    self.fields["merged_specs_preview"].initial = product.specs_dict()
        except Exception:
            pass

# =========================
# Inlines
# =========================
class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image", "alt_text", "sort_order")
    ordering = ("sort_order", "id")

class ProductStatsInline(admin.StackedInline):
    model = ProductStats
    can_delete = False
    extra = 0
    readonly_fields = ("views", "clicks", "add_to_cart", "orders", "last_seen")
    fields = ("views", "clicks", "add_to_cart", "orders", "last_seen")

class SpecificationInline(admin.TabularInline):
    model = Specification
    extra = 1
    fields = ("title", "value", "sort_order")
    ordering = ("sort_order", "id")

    def get_formset(self, request, obj=None, **kwargs):
        FormSet = super().get_formset(request, obj, **kwargs)

        prefill_id = request.GET.get("prefill_specs")
        if prefill_id and obj is None:
            try:
                source = Product.objects.get(pk=prefill_id)
            except Product.DoesNotExist:
                return FormSet

            # build clean initial list
            init = []
            for s in source.specifications.all().order_by("id"):
                init.append({
                    "title": getattr(s, "title", "") or "",
                    "value": getattr(s, "value", "") or "",
                    "sort_order": getattr(s, "sort_order", 0) or 0,
                })

            if not init:
                return FormSet

            extra_count = max(len(init), 1)

            # Return a custom FormSet class that shows EXACTLY the initial rows (no surprises)
            class PrefilledFormSet(FormSet):
                extra = extra_count            # ✅ CRUCIAL: initial will fill these extra forms
                def __init__(self, *args, **kwargs):
                    kwargs["initial"] = init   # ✅ initial applied to those extra forms
                    super().__init__(*args, **kwargs)

            return PrefilledFormSet

        return FormSet

class VariantImageInline(admin.TabularInline):
    model = VariantImage
    extra = 1
    fields = ("image", "alt_text", "sort_order")
    ordering = ("sort_order", "id")

class VariantInline(admin.TabularInline):
    model = Variant
    extra = 0
    form = VariantAdminForm
    fields = (
        "sku", "amazon_url",
        "color_primary", "color_secondary", "size",
        "mrp", "sale_price",
        "promo_price", "promo_start", "promo_end",
        "stock_qty", "backorder_allowed",
        "is_active", "specs_override",
        "merged_specs_preview",
    )
    readonly_fields = ("merged_specs_preview",)
    show_change_link = True

    def merged_specs_preview(self, obj):
        if not obj or not obj.pk:
            return "-"
        data = obj.effective_specs() or {}
        pretty = json.dumps(data, indent=2, ensure_ascii=False)
        return mark_safe(f"<pre style='white-space:pre-wrap'>{pretty}</pre>")
    merged_specs_preview.short_description = "Effective Specs (Preview)"

# =========================
# Product bulk duplicate
# =========================
@admin.action(description="Duplicate selected products (with variants, images & specs)")
def duplicate_products(modeladmin, request, queryset):
    count = 0
    for product in queryset:
        new_p = Product.objects.create(
            title=f"{product.title} (Copy)",
            short_description=product.short_description,
            description=product.description,
            meta_title=product.meta_title,
            meta_description=product.meta_description,
            is_active=product.is_active,
            is_published=False,
            category=product.category,
        )
        for img in product.images.all():
            ProductImage.objects.create(
                product=new_p, image=img.image, alt_text=img.alt_text, sort_order=img.sort_order
            )
        for s in product.specifications.all():
            Specification.objects.create(
                product=new_p, title=s.title, value=s.value, sort_order=s.sort_order
            )
        for v in product.variants.all():
            new_v = Variant.objects.create(
                product=new_p,
                sku=f"{v.sku}-COPY",
                amazon_url=v.amazon_url,
                color_primary=v.color_primary,
                color_secondary=v.color_secondary,
                size=v.size,
                mrp=v.mrp,
                sale_price=v.sale_price,
                promo_price=v.promo_price,
                promo_start=v.promo_start,
                promo_end=v.promo_end,
                stock_qty=v.stock_qty,
                backorder_allowed=v.backorder_allowed,
                weight_kg=v.weight_kg,
                length_cm=v.length_cm,
                width_cm=v.width_cm,
                height_cm=v.height_cm,
                is_active=v.is_active,
                specs_override=v.specs_override,
            )
            for vi in v.images.all():
                VariantImage.objects.create(
                    variant=new_v, image=vi.image, alt_text=vi.alt_text, sort_order=vi.sort_order
                )
        count += 1
    messages.success(request, f"Duplicated {count} product(s).")

# =========================
# Product Admin
# =========================
@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("title", "is_published", "is_active", "category", "preview_url")
    list_filter = ("is_active", "is_published", "category")
    search_fields = ("title", "slug")
    inlines = [ProductImageInline, SpecificationInline, VariantInline, ProductStatsInline]
    filter_horizontal = ('fbt_defaults',)
    actions = [duplicate_products]
    prepopulated_fields = {"slug": ("title",)}

    def preview_url(self, obj):
        try:
            return format_html('<a href="{}" target="_blank">View</a>', obj.get_absolute_url())
        except Exception:
            return "-"
    preview_url.short_description = "Frontend"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/duplicate-specs/",
                self.admin_site.admin_view(self.duplicate_specs_prefill_view),
                name="shop_product_duplicate_specs",
            ),
        ]
        return custom + urls

    def duplicate_specs_prefill_view(self, request, object_id, *args, **kwargs):
        obj = get_object_or_404(Product, pk=object_id)
        add_url = reverse("admin:shop_product_add")
        target = f"{add_url}?prefill_specs={obj.pk}"
        count = obj.specifications.count()
        messages.info(request, f"Prefilling {count} specification(s) from: {obj}")
        return redirect(target)


# =========================
# Variant Admin
# =========================
@admin.register(Variant)
class VariantAdmin(admin.ModelAdmin):
    change_form_template = "admin/shop/variant/change_form.html"
    form = VariantAdminForm

    list_display = (
        "sku", "product", "featured", "color_primary", "color_secondary", "size",
        "sale_price", "stock_qty", "is_active"
    )
    list_filter = ("featured", "is_active", "color_primary", "color_secondary", "size", "product__category")
    search_fields = ("sku", "product__title")
    inlines = [VariantImageInline, FBTLinkInline]

    actions = ("mark_featured", "unmark_featured")

    def mark_featured(self, request, queryset):
        updated = queryset.update(featured=True)
        self.message_user(request, f"{updated} variants marked as featured.")
    mark_featured.short_description = "Mark selected as Featured"

    def unmark_featured(self, request, queryset):
        updated = queryset.update(featured=False)
        self.message_user(request, f"{updated} variants unmarked.")
    unmark_featured.short_description = "Unmark selected as Featured"

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "<path:object_id>/duplicate/",
                self.admin_site.admin_view(self.duplicate_prefill_view),
                name="shop_variant_duplicate",
            ),
        ]
        return custom + urls

    def duplicate_prefill_view(self, request, object_id: str):
        v = self.get_object(request, object_id)
        if not v:
            from django.http import Http404
            raise Http404("Variant not found")

        messages.info(
            request,
            mark_safe(
                "Duplicate mode: Most fields prefilled. "
                "<strong>SKU empty</strong>, colors/size/images not copied."
            ),
        )
        add_url = reverse("admin:shop_variant_add")
        return HttpResponseRedirect(f"{add_url}?prefill={v.pk}")

    def get_changeform_initial_data(self, request):
        initial = super().get_changeform_initial_data(request)
        prefill_id = request.GET.get("prefill")
        if prefill_id:
            v = Variant.objects.filter(pk=prefill_id).select_related("product").first()
            if v:
                base = v.product.specs_dict() if hasattr(v.product, "specs_dict") else {}
                override = v.specs_override or {}
                effective = {}
                effective.update(base or {})
                effective.update(override or {})

                initial.update({
                    "product": v.product_id,
                    "amazon_url": v.amazon_url,
                    "mrp": v.mrp,
                    "sale_price": v.sale_price,
                    "promo_price": v.promo_price,
                    "promo_start": v.promo_start,
                    "promo_end": v.promo_end,
                    "stock_qty": v.stock_qty,
                    "backorder_allowed": v.backorder_allowed,
                    "weight_kg": v.weight_kg,
                    "length_cm": v.length_cm,
                    "width_cm": v.width_cm,
                    "height_cm": v.height_cm,
                    "is_active": v.is_active,
                    "specs_override": json.dumps(override, ensure_ascii=False) if override else "",
                    "merged_specs_preview": json.dumps(effective, indent=2, ensure_ascii=False),
                })
        return initial

# =========================
# Category / lookups
# =========================
@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "display_order", "featured", "is_active")
    list_editable = ("featured", "display_order")
    list_filter = ("is_active", "parent")
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}
    ordering = ("display_order", "name", "id")

admin.site.register(Color)
admin.site.register(Size)

# =========================
# Coupons
# =========================
@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "type", "value", "status", "applies_to", "starts_at", "ends_at", "is_public", "show_in_listing")
    list_filter = ("status", "type", "applies_to", "is_public", "show_in_listing")
    search_fields = ("code", "title", "notes")
    filter_horizontal = (
        "included_products", "excluded_products",
        "included_categories", "excluded_categories",
        "included_variants", "excluded_variants",
    )
    exclude = ("eligible_users",)
    readonly_fields = ("created",)

    fieldsets = (
        ("Basics", {
            "fields": ("code", "title", "notes", "type", "value", "max_discount_amount", "status", "starts_at", "ends_at"),
        }),
        ("Scope & Targeting", {
            "description": "Pick Products/Categories OR target specific Variants. "
                           "If 'Included variants' is set, only those variants qualify. "
                           "Exclusions always override inclusions.",
            "fields": (
                "applies_to",
                "included_products", "excluded_products",
                "included_categories", "excluded_categories",
                "included_variants", "excluded_variants",
            ),
        }),
        ("Eligibility & Limits", {
            "fields": ("min_cart_subtotal", "first_order_only", "per_user_limit", "global_limit"),
        }),
        ("Visibility & Delivery", {
            "fields": ("is_public", "show_in_listing", "allow_deeplink", "vanity_slug"),
        }),
        ("Stacking (future)", {
            "fields": ("is_stackable", "stack_group"),
        }),
        ("Meta", {"fields": ("created",)}),
    )

@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ("coupon", "user", "order_reference", "session_id", "amount_discounted", "used_at")
    list_filter = ("coupon",)
    search_fields = ("coupon__code", "order_reference", "session_id")
    readonly_fields = ("used_at",)

# =========================
# HIDE stats & search click models from sidebar/index
# =========================
class _HiddenAdmin(admin.ModelAdmin):
    """Hide model from the admin app index & sidebar, but keep it usable via inlines."""
    def get_model_perms(self, request):
        return {}

# unregister if already registered somewhere
for mdl in (ProductStats, VariantStats, SearchClick, SearchClickVariant):
    try:
        admin.site.unregister(mdl)
    except Exception:
        pass

@admin.register(ProductStats)
class ProductStatsHiddenAdmin(_HiddenAdmin):
    pass

@admin.register(VariantStats)
class VariantStatsHiddenAdmin(_HiddenAdmin):
    pass

@admin.register(SearchClick)
class SearchClickHiddenAdmin(_HiddenAdmin):
    pass

@admin.register(SearchClickVariant)
class SearchClickVariantHiddenAdmin(_HiddenAdmin):
    pass

@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "variant", "user", "rating", "is_verified_purchase", "is_published", "created_at")
    list_filter = ("is_published", "is_verified_purchase", "rating", "product")
    search_fields = ("product__title", "variant__sku", "variant__title", "user__email", "title", "body")
    autocomplete_fields = ("product", "variant", "user")  # <- yaha se 'order_item' hata do
    date_hierarchy = "created_at"