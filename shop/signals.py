# shop/signals.py

from django.core.cache import cache
from django.db.models.signals import pre_save, post_save, post_delete
from django.dispatch import receiver
from django.conf import settings
from django.apps import apps
from shop.models import Review
from shop.services.testimonials import invalidate_today_cache

from .models import Category, Product, Variant

FEATURED_CATS_VERSION = "v2"

def _clear_featured_cats_cache():
    # Wildcard deletion not built-in; simplest: clear all or maintain a key registry.
    # If you don't want to clear all, store composed keys in a list key and delete those.
    cache.clear()

@receiver(post_save, sender=Category)
@receiver(post_delete, sender=Category)
def on_category_change(sender, **kwargs):
    _clear_featured_cats_cache()

@receiver(post_save, sender=Product)
@receiver(post_delete, sender=Product)
def on_product_change(sender, **kwargs):
    _clear_featured_cats_cache()

@receiver(post_save, sender=Variant)
@receiver(post_delete, sender=Variant)
def on_variant_change(sender, **kwargs):
    _clear_featured_cats_cache()

# If Profile is in accounts app:
try:
    from accounts.models import Profile
except Exception:
    Profile = None


def _was_qualifying(old: Review | None) -> bool:
    if not old:
        return False
    return bool(getattr(old, "is_published", False) and getattr(old, "rating", 0) >= 4)


def _is_qualifying(new: Review) -> bool:
    return bool(getattr(new, "is_published", False) and getattr(new, "rating", 0) >= 4)


@receiver(pre_save, sender=Review)
def _review_pre_save_snapshot(sender, instance: Review, **kwargs):
    """
    Attach previous state to instance for comparison in post_save.
    """
    if instance.pk:
        try:
            instance._old_review = Review.objects.select_related(
                "user", "product", "variant", "order_item__order", "user__profile"
            ).get(pk=instance.pk)
        except Review.DoesNotExist:
            instance._old_review = None
    else:
        instance._old_review = None


@receiver(post_save, sender=Review)
def _review_post_save_refresh(sender, instance: Review, created, **kwargs):
    """
    Invalidate today's testimonial cache if qualifying set/content changed.
    Conditions:
      - Created & qualifies → invalidate.
      - Rating threshold crossed (to/from 4/5) or publish toggle → invalidate.
      - If still qualifying, and content fields changed (title/body) or user/product/variant changed → invalidate.
    """
    old = getattr(instance, "_old_review", None)
    old_q = _was_qualifying(old)
    new_q = _is_qualifying(instance)

    should_bust = False

    if created:
        should_bust = new_q
    else:
        # threshold / publish change
        if old_q != new_q:
            should_bust = True
        else:
            # Still qualifying: detect material edits
            if new_q and old:
                changed_fields = []
                for f in ["title", "body", "rating", "is_published", "user_id", "product_id", "variant_id"]:
                    if getattr(old, f, None) != getattr(instance, f, None):
                        changed_fields.append(f)
                if changed_fields:
                    should_bust = True

    if should_bust:
        invalidate_today_cache()


@receiver(post_delete, sender=Review)
def _review_deleted(sender, instance: Review, **kwargs):
    # If a qualifying review is deleted, bust cache
    if _is_qualifying(instance):
        invalidate_today_cache()
    else:
        # Even if not qualifying *now*, cautious bust (in case it was selected earlier and rating changed just before delete)
        invalidate_today_cache()


try:
    # Lazy fetch to avoid import-time issues / circular imports
    ProfileModel = apps.get_model("accounts", "Profile")
except Exception:
    ProfileModel = None

if ProfileModel:
    @receiver(post_save, sender=ProfileModel)
    def _profile_updated(sender, instance, **kwargs):  # no type hint to avoid editor warning
        # We don't try to diff old/new here; simplest is to bust today's cache.
        # This ensures the updated state shows up immediately if the user's review is selected today.
        invalidate_today_cache()