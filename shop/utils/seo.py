# shop/utils/seo.py
from urllib.parse import urlencode, urlsplit, urlunsplit

ALLOW_PARAMS_VARIANT = {"v", "variant"}  # sirf variant id param canonical me rahe

def build_canonical(request, keep_variant=True):
    parts = urlsplit(request.build_absolute_uri())
    query = {}
    if keep_variant:
        if "variant" in request.GET:
            query["variant"] = request.GET.get("variant")
        elif "v" in request.GET:
            query["variant"] = request.GET.get("v")  # normalize to ?variant=
    query_str = urlencode(query, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query_str, ""))

def is_filter_or_sort(request):
    """
    Agar 'v' ke alawa koi aur query param mila (color/size/sort/page etc.) to
    ise filtered/sorted page maan kar noindex karein.
    """
    for k in request.GET.keys():
        if k not in ALLOW_PARAMS_VARIANT:
            return True
    return False
