from django.db.models import Q
from shop.models import Category, Variant

def menu_categories(request):
    """
    Flat menu list: parent + its children, but only those that have
    in-stock products (variants with stock_qty > 0). Ordered by Category.display_order.
    """

    # 1) In-stock categories (via variants)
    in_stock_cat_ids = set(
        Variant.objects.filter(
            is_active=True,
            product__is_active=True,
            stock_qty__gt=0,  # keep as-is; change if you want to include backorder_allowed
        )
        .values_list("product__category_id", flat=True)
        .distinct()
    )

    # 2) Pull active categories with parent (ordered globally by display_order)
    cats = list(
        Category.objects.filter(is_active=True)
        .select_related("parent")
        .only("id", "name", "slug", "parent_id", "image", "display_order")
        .order_by("display_order", "name", "id")
    )

    # 3) Group children by parent (children already sorted by the order above)
    children_by_parent = {}
    for c in cats:
        if c.parent_id:
            children_by_parent.setdefault(c.parent_id, []).append(c)

    # 4) Build flat menu: parent (if self/child has stock) → its children (that have stock)
    flat_items = []
    for c in cats:
        if c.parent_id is None:  # parent
            has_self = c.id in in_stock_cat_ids
            has_child = any(ch.id in in_stock_cat_ids for ch in children_by_parent.get(c.id, []))
            if has_self or has_child:
                flat_items.append(c)  # parent first (ordered by display_order)
                for ch in children_by_parent.get(c.id, []):
                    if ch.id in in_stock_cat_ids:
                        flat_items.append(ch)  # children in the same display_order

    return {"menu_flat_categories": flat_items}
