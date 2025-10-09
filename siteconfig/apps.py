# siteconfig/apps.py
from django.apps import AppConfig

class SiteConfigConfig(AppConfig):
    name = "siteconfig"
    verbose_name = "Site Config"

    def ready(self):
        from . import signals  # noqa
