"""Tela da integração: configurar o app do Tiny, conectar por OAuth, sincronizar e ver o histórico."""

from __future__ import annotations

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from apps.costing.models import Channel

from . import client as tiny_client
from . import services
from .models import TinyConnection, TinyOrderSync, TinyProductLink, TinySyncLog


class ConnectionForm(forms.ModelForm):
    class Meta:
        model = TinyConnection
        fields = ["client_id", "client_secret", "price_channel", "product_push_enabled", "order_sync_enabled", "order_situacoes", "order_lookback_days", "default_priority"]
        widgets = {"client_secret": forms.PasswordInput(render_value=True)}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["price_channel"].queryset = Channel.objects.filter(status=Channel.Status.ACTIVE)


@login_required
def status(request):
    conn = TinyConnection.get()
    form = ConnectionForm(request.POST or None, instance=conn)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Configuração salva.")
        return redirect("tiny:status")
    return render(request, "tiny/status.html", {
        "conn": conn, "form": form,
        "redirect_uri": request.build_absolute_uri(reverse("tiny:callback")),
        "links": TinyProductLink.objects.select_related("variant__product").order_by("-last_pushed_at")[:20],
        "orders": TinyOrderSync.objects.prefetch_related("orders")[:20],
        "logs": TinySyncLog.objects.all()[:20],
    })


@login_required
def connect(request):
    conn = TinyConnection.get()
    if not conn.configured:
        messages.error(request, "Preencha Client ID e Client Secret antes de conectar.")
        return redirect("tiny:status")
    return redirect(tiny_client.authorization_url(conn, request.build_absolute_uri(reverse("tiny:callback"))))


@login_required
def callback(request):
    conn = TinyConnection.get()
    code, state = request.GET.get("code"), request.GET.get("state", "")
    if not code:
        messages.error(request, f"O Tiny não devolveu o código de autorização ({request.GET.get('error', 'sem detalhe')}).")
        return redirect("tiny:status")
    try:
        tiny_client.exchange_code(conn, code, state)
    except tiny_client.TinyAuthError as exc:
        conn.status = TinyConnection.Status.ERROR
        conn.last_error = str(exc)[:300]
        conn.save(update_fields=["status", "last_error"])
        TinySyncLog.objects.create(kind=TinySyncLog.Kind.AUTH, ok=False, summary=str(exc)[:300])
        messages.error(request, f"Falha ao conectar: {exc}")
        return redirect("tiny:status")
    TinySyncLog.objects.create(kind=TinySyncLog.Kind.AUTH, ok=True, summary="Conectado ao Tiny")
    messages.success(request, "Tiny conectado. Os tokens serão renovados automaticamente.")
    return redirect("tiny:status")


@login_required
@require_POST
def disconnect(request):
    conn = TinyConnection.get()
    conn.access_token = conn.refresh_token = ""
    conn.access_expires_at = conn.refresh_expires_at = None
    conn.status = TinyConnection.Status.NOT_CONFIGURED if not conn.configured else TinyConnection.Status.AWAITING_AUTH
    conn.save()
    messages.info(request, "Tokens apagados; conecte de novo quando quiser.")
    return redirect("tiny:status")


@login_required
@require_POST
def push_products(request):
    conn = TinyConnection.get()
    if not conn.connected:
        messages.error(request, "Conecte o Tiny primeiro.")
        return redirect("tiny:status")
    try:
        report = services.push_all(tiny_client.TinyClient(conn), base_url=request.build_absolute_uri("/"), force=request.POST.get("force") == "1")
    except tiny_client.TinyAuthError as exc:
        messages.error(request, f"Autenticação: {exc}")
        return redirect("tiny:status")
    messages.success(request, f"Produtos: {report.created} criados, {report.updated} atualizados, {report.unchanged} sem mudança, {len(report.skipped)} pulados.")
    for err in report.errors[:5]:
        messages.warning(request, err)
    return redirect("tiny:status")


@login_required
@require_POST
def pull_orders(request):
    conn = TinyConnection.get()
    if not conn.connected:
        messages.error(request, "Conecte o Tiny primeiro.")
        return redirect("tiny:status")
    try:
        report = services.sync_orders(tiny_client.TinyClient(conn), user=request.user)
    except tiny_client.TinyAuthError as exc:
        messages.error(request, f"Autenticação: {exc}")
        return redirect("tiny:status")
    messages.success(request, f"Pedidos: {report.seen} vistos, {report.created_orders} ordens de produção criadas, {report.skipped} sem itens conhecidos.")
    for err in report.errors[:5]:
        messages.warning(request, err)
    return redirect("tiny:status")
