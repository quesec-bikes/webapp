from .utils import Cart

def cart_summary(request):
    """Make cart available in all templates"""
    cart = Cart(request)
    # Expose variant IDs currently in cart for template logic
    try:
        variant_ids = [int(k) for k in cart.cart.get('items', {}).keys()]
    except Exception:
        variant_ids = []

    checkout_ready = bool(request.session.get("checkout_form"))
    return {
        'cart_item_count': len(cart),
        'cart_subtotal': cart.get_subtotal(),
        'cart_variant_ids': variant_ids,
        'checkout_ready': checkout_ready,
    }

def cart_snapshot(request):
    """
    JSON-serializable snapshot for CartWatch:
    items: [{id, title, variation, qty, price}], total
    """
    cart = Cart(request)
    items = []
    try:
        for it in cart.get_items():
            variant = it.get("variant")        # Variant object (from your Cart)
            product = getattr(variant, "product", None)

            # Title: prefer product.title, fallback variant.title/SKU
            title = (
                getattr(product, "title", None)
                or getattr(variant, "title", None)
                or getattr(variant, "sku", None)
                or f"Product #{getattr(variant, 'id', '-')}"
            )

            # ID: prefer variant.id (stable for a chosen option)
            vid = getattr(variant, "id", None) or it.get("id", None) or "-"

            # Variation string (Color / Size etc.)
            variation_parts = []
            if hasattr(variant, "color_primary") and getattr(variant, "color_primary"):
                try:
                    variation_parts.append(str(variant.color_primary.name))
                except Exception:
                    variation_parts.append(str(variant.color_primary))
            if hasattr(variant, "size") and getattr(variant, "size"):
                try:
                    variation_parts.append(str(variant.size.name))
                except Exception:
                    variation_parts.append(str(variant.size))
            variation = " / ".join([p for p in variation_parts if p])

            items.append({
                "id": vid,
                "title": title,
                "variation": variation,              # e.g., "Red / 20 inch"
                "qty": it.get("quantity", 1),
                "price": float(it.get("price", 0)),
            })
    except Exception:
        items = []

    total = float(cart.get_subtotal() or 0)
    return {"cw_cart_items": items, "cw_cart_total": total}