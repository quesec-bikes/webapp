# shop/templatetags/qparams.py
from django import template
from urllib.parse import urlencode

register = template.Library()

@register.simple_tag(takes_context=True)
def qparams(context, **kwargs):
    """
    Build querystring by merging current GET with kwargs.
    Use like: ?{% qparams page=2 %}
    Pass None to remove a key (e.g. page=None).
    """
    req = context["request"]
    qd = req.GET.copy()
    for k, v in kwargs.items():
        if v is None:
            qd.pop(k, None)
        else:
            qd[k] = v
    return urlencode(qd, doseq=True)
