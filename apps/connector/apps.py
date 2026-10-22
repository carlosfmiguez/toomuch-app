from django.apps import AppConfig


class ConnectorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.connector"
    verbose_name = "Conector do Claude (MCP)"
