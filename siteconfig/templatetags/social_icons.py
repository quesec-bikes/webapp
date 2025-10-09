from django import template

register = template.Library()

ICON_MAP = {
    "facebook": "icon-fb",
    "instagram": "icon-instagram",
    "pinterest": "icon-pinterest-1",
    "x": "icon-Icon-x",  # theme ke hisaab se
}

@register.filter
def icon_for(network: str) -> str:
    return ICON_MAP.get((network or "").lower(), "")
