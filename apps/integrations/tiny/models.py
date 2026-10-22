"""
Integração com o Tiny ERP (Olist) — API v3.

- TinyConnection: credenciais do aplicativo (client_id/secret), tokens OAuth e
  preferências de sincronização. Uma linha só (singleton).
- TinyProductLink: vínculo entre uma Variante daqui e um produto lá (id do Tiny).
- TinyOrderSync: cada pedido do Tiny já processado (idempotência) e a ordem gerada.
- TinySyncLog: histórico das sincronizações, para a tela de status.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone


class TinyConnection(models.Model):
    class Status(models.TextChoices):
        NOT_CONFIGURED = "unset", "Não configurado"
        AWAITING_AUTH = "awaiting", "Aguardando autorização"
        CONNECTED = "connected", "Conectado"
        ERROR = "error", "Erro"

    client_id = models.CharField("Client ID", max_length=200, blank=True)
    client_secret = models.CharField("Client Secret", max_length=200, blank=True)
    redirect_uri = models.URLField("URL de redirecionamento", blank=True, help_text="Cadastrada no aplicativo do Tiny; o sistema preenche ao conectar.")
    access_token = models.TextField(blank=True, editable=False)
    refresh_token = models.TextField(blank=True, editable=False)
    access_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    refresh_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.NOT_CONFIGURED, editable=False)
    last_error = models.CharField(max_length=300, blank=True, editable=False)
    oauth_state = models.CharField(max_length=64, blank=True, editable=False)

    # Sincronização de pedidos
    order_sync_enabled = models.BooleanField("importar pedidos", default=True)
    order_situacoes = models.CharField(
        "situações que viram ordem de produção", max_length=40, default="3",
        help_text="Códigos do Tiny separados por vírgula. 3 = Aprovada (pago), 1 = Faturada, 0 = Aberta.",
    )
    order_lookback_days = models.PositiveSmallIntegerField("buscar pedidos dos últimos (dias)", default=7)
    last_order_sync_at = models.DateTimeField(null=True, blank=True, editable=False)
    default_priority = models.PositiveSmallIntegerField("prioridade das ordens do Tiny", default=2)

    # Envio de produtos
    price_channel = models.ForeignKey(
        "costing.Channel", verbose_name="canal cujo preço vai ao Tiny", on_delete=models.SET_NULL, null=True, blank=True,
        help_text="Normalmente o canal Site; o Tiny repassa o preço aos marketplaces conectados a ele.",
    )
    product_push_enabled = models.BooleanField("enviar produtos", default=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "conexão com o Tiny"
        verbose_name_plural = "conexão com o Tiny"

    def __str__(self) -> str:
        return f"Tiny ERP ({self.get_status_display()})"

    @classmethod
    def get(cls) -> "TinyConnection":
        obj = cls.objects.first()
        return obj or cls.objects.create()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def connected(self) -> bool:
        return bool(self.refresh_token) and self.status == self.Status.CONNECTED

    @property
    def access_token_valid(self) -> bool:
        return bool(self.access_token and self.access_expires_at and self.access_expires_at > timezone.now() + timezone.timedelta(minutes=2))

    @property
    def situacoes(self) -> list[int]:
        out = []
        for part in self.order_situacoes.split(","):
            part = part.strip()
            if part.isdigit():
                out.append(int(part))
        return out or [3]


class TinyProductLink(models.Model):
    variant = models.OneToOneField("catalog.Variant", on_delete=models.CASCADE, related_name="tiny_link", verbose_name="variante")
    tiny_id = models.BigIntegerField("id no Tiny")
    tiny_sku = models.CharField("SKU no Tiny", max_length=80, blank=True)
    last_pushed_at = models.DateTimeField("último envio", null=True, blank=True)
    last_price = models.DecimalField("último preço enviado", max_digits=10, decimal_places=2, null=True, blank=True)
    last_payload_hash = models.CharField(max_length=64, blank=True, editable=False)
    last_error = models.CharField(max_length=300, blank=True)

    class Meta:
        verbose_name = "produto vinculado ao Tiny"
        verbose_name_plural = "produtos vinculados ao Tiny"

    def __str__(self) -> str:
        return f"{self.variant.sku} ↔ Tiny #{self.tiny_id}"


class TinyOrderSync(models.Model):
    class Result(models.TextChoices):
        CREATED = "created", "Ordens criadas"
        SKIPPED = "skipped", "Sem itens conhecidos"
        ERROR = "error", "Erro"

    tiny_order_id = models.BigIntegerField("id do pedido no Tiny", unique=True)
    numero_pedido = models.CharField("número do pedido", max_length=40, blank=True)
    situacao = models.PositiveSmallIntegerField("situação no Tiny", null=True, blank=True)
    canal = models.CharField("canal de venda", max_length=80, blank=True)
    cliente = models.CharField("cliente", max_length=160, blank=True)
    valor = models.DecimalField("valor", max_digits=10, decimal_places=2, null=True, blank=True)
    data_prevista = models.DateField("prazo", null=True, blank=True)
    result = models.CharField(max_length=10, choices=Result.choices)
    detail = models.CharField(max_length=300, blank=True)
    orders = models.ManyToManyField("production.ProductionOrder", blank=True, related_name="tiny_syncs", verbose_name="ordens geradas")
    raw = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "pedido do Tiny processado"
        verbose_name_plural = "pedidos do Tiny processados"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Pedido Tiny {self.numero_pedido or self.tiny_order_id} ({self.get_result_display()})"


class TinySyncLog(models.Model):
    class Kind(models.TextChoices):
        ORDERS = "orders", "Pedidos → ordens"
        PRODUCTS = "products", "Produtos → Tiny"
        AUTH = "auth", "Autenticação"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    ok = models.BooleanField(default=True)
    summary = models.CharField(max_length=300)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "registro de sincronização"
        verbose_name_plural = "registros de sincronização"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.get_kind_display()} · {self.summary}"
