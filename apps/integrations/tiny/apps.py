from django.apps import AppConfig


class TinyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.integrations.tiny"
    label = "tiny"
    verbose_name = "Tiny ERP"
