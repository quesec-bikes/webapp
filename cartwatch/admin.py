# cartwatch/admin.py
from django.contrib import admin, messages
from django.utils.html import format_html, format_html_join
import json
from decimal import Decimal
from .models import CartWatchLead, CartWatchStatus

def _to_money(v):
    try:
        return f"₹{Decimal(v):.2f}"
    except Exception:
        try:
            return f"₹{float(v):.2f}"
        except Exception:
            return f"₹{v}"

@admin.register(CartWatchLead)
class CartWatchLeadAdmin(admin.ModelAdmin):
    list_display = (
        "phone", "session_id", "status", "created_at", "last_seen_at", "converted_order_id"
    )
    list_filter = ("status", "created_at")
    search_fields = ("phone", "session_id", "converted_order_id")
    readonly_fields = (
        "created_at", "updated_at", "last_seen_at",
        "cart_items_pretty", "cart_snapshot_pretty",
    )

    fieldsets = (
        (None, {
            "fields": ("session_id", "phone", "status", "converted_order_id")
        }),
        ("Context", {
            "fields": ("source_url", "user_agent", "created_at", "last_seen_at", "updated_at")
        }),
        ("Cart", {
            "fields": ("cart_items_pretty", "cart_snapshot_pretty")
        }),
    )

    # --- Pretty parsed items (human readable) ---
    def cart_items_pretty(self, obj: CartWatchLead):
        snap = obj.cart_snapshot or {}
        items = snap.get("items") or []
        if not items:
            return format_html("<em>No items in snapshot.</em>")

        rows = []
        total = Decimal("0")
        for it in items:
            pid   = it.get("id") or "-"
            title = it.get("title") or f"Product #{pid}"
            variation = it.get("variation") or ""
            qty   = it.get("qty") or 1
            price = it.get("price") or 0
            line_total = Decimal(str(price)) * Decimal(str(qty))
            total += line_total

            display_title = f"{title} ({variation})" if variation else title

            rows.append((
                display_title,
                pid,
                qty,
                _to_money(price),
                _to_money(line_total),
            ))

        list_html = format_html_join(
            "",
            "<div>• <strong>{}</strong> <span style='opacity:.7'>(#{})</span> — qty <strong>{}</strong> — price {} — line total {}</div>",
            rows
        )
        footer = format_html("<div style='margin-top:8px'><strong>Snapshot total:</strong> {}</div>", _to_money(total))
        return format_html("{}{}", list_html, footer)

    cart_items_pretty.short_description = "Cart items (parsed)"

    # --- Pretty JSON (expandable) ---
    def cart_snapshot_pretty(self, obj: CartWatchLead):
        try:
            pretty = json.dumps(obj.cart_snapshot or {}, indent=2, ensure_ascii=False)
        except Exception:
            pretty = str(obj.cart_snapshot)
        return format_html(
            "<details><summary>Show raw JSON</summary>"
            "<pre style='white-space:pre-wrap; font-size:12px; background:#f6f8fa; padding:8px; border-radius:6px;'>{}</pre>"
            "</details>",
            pretty
        )

    cart_snapshot_pretty.short_description = "Cart snapshot (raw JSON)"

    # --- Bulk actions (as before) ---
    actions = ["mark_selected_converted", "mark_selected_closed", "reopen_selected"]

    def mark_selected_converted(self, request, queryset):
        updated = queryset.update(status=CartWatchStatus.CONVERTED)
        self.message_user(request, f"{updated} lead(s) marked as CONVERTED.", messages.SUCCESS)
    mark_selected_converted.short_description = "Mark selected as CONVERTED"

    def mark_selected_closed(self, request, queryset):
        updated = queryset.update(status=CartWatchStatus.CLOSED)
        self.message_user(request, f"{updated} lead(s) marked as CLOSED.", messages.SUCCESS)
    mark_selected_closed.short_description = "Mark selected as CLOSED"

    def reopen_selected(self, request, queryset):
        updated = queryset.update(status=CartWatchStatus.OPEN)
        self.message_user(request, f"{updated} lead(s) re-opened.", messages.SUCCESS)
    reopen_selected.short_description = "Re-open selected (set to OPEN)"
