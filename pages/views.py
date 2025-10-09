from django.shortcuts import get_object_or_404
from django.views.generic import DetailView
from .models import Page
from django.http import Http404


class PageDetailView(DetailView):
    model = Page
    template_name = "pages/page.html"
    context_object_name = "page"

    def get_object(self, queryset=None):
        obj = get_object_or_404(Page, slug=self.kwargs.get("slug"))
        if not obj.is_published:
            raise Http404("Page not published")
        return obj

    def render_to_response(self, context, **response_kwargs):
        response = super().render_to_response(context, **response_kwargs)
        return response
