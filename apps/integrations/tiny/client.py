"""
Cliente HTTP da API v3 do Tiny ERP (Olist).

Autenticação OAuth 2 (authorization code) em accounts.tiny.com.br; token de acesso
dura 4 h e o refresh token 1 dia, então o cliente renova sozinho antes de cada
chamada e guarda os tokens na TinyConnection. Respeita X-RateLimit-* (limite por
minuto, por conta) esperando o reset quando necessário.

Referência: https://api-docs.erp.olist.com (autenticação, produtos, pedidos, estoque).
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests
from django.utils import timezone

from .models import TinyConnection

AUTH_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/auth"
TOKEN_URL = "https://accounts.tiny.com.br/realms/tiny/protocol/openid-connect/token"
API_BASE = "https://api.tiny.com.br/public-api/v3"

ACCESS_TOKEN_SECONDS = 4 * 3600
REFRESH_TOKEN_SECONDS = 24 * 3600


class TinyError(Exception):
    def __init__(self, message: str, status: int | None = None, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class TinyAuthError(TinyError):
    """Sem token válido: precisa reconectar pela tela."""


@dataclass
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int
    refresh_expires_in: int


def authorization_url(conn: TinyConnection, redirect_uri: str) -> str:
    """Gera a URL de autorização e guarda o `state` para conferir no retorno."""
    conn.oauth_state = secrets.token_urlsafe(24)
    conn.redirect_uri = redirect_uri
    conn.status = TinyConnection.Status.AWAITING_AUTH
    conn.save(update_fields=["oauth_state", "redirect_uri", "status"])
    params = {"client_id": conn.client_id, "redirect_uri": redirect_uri, "scope": "openid", "response_type": "code", "state": conn.oauth_state}
    return f"{AUTH_URL}?{urlencode(params)}"


def _token_request(data: dict, session: requests.Session | None = None) -> TokenSet:
    http = session or requests
    resp = http.post(TOKEN_URL, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    if resp.status_code != 200:
        raise TinyAuthError(f"Token: HTTP {resp.status_code} {resp.text[:200]}", resp.status_code)
    body = resp.json()
    return TokenSet(
        access_token=body["access_token"], refresh_token=body.get("refresh_token", ""),
        expires_in=int(body.get("expires_in", ACCESS_TOKEN_SECONDS)), refresh_expires_in=int(body.get("refresh_expires_in", REFRESH_TOKEN_SECONDS)),
    )


def _store(conn: TinyConnection, tokens: TokenSet) -> None:
    now = timezone.now()
    conn.access_token = tokens.access_token
    if tokens.refresh_token:
        conn.refresh_token = tokens.refresh_token
        conn.refresh_expires_at = now + timezone.timedelta(seconds=tokens.refresh_expires_in)
    conn.access_expires_at = now + timezone.timedelta(seconds=tokens.expires_in)
    conn.status = TinyConnection.Status.CONNECTED
    conn.last_error = ""
    conn.save(update_fields=["access_token", "refresh_token", "refresh_expires_at", "access_expires_at", "status", "last_error"])


def exchange_code(conn: TinyConnection, code: str, state: str, session: requests.Session | None = None) -> TinyConnection:
    if not conn.oauth_state or state != conn.oauth_state:
        raise TinyAuthError("Estado OAuth não confere; inicie a conexão de novo.")
    tokens = _token_request({
        "grant_type": "authorization_code", "client_id": conn.client_id, "client_secret": conn.client_secret,
        "redirect_uri": conn.redirect_uri, "code": code,
    }, session)
    conn.oauth_state = ""
    _store(conn, tokens)
    return conn


def refresh(conn: TinyConnection, session: requests.Session | None = None) -> TinyConnection:
    if not conn.refresh_token:
        raise TinyAuthError("Sem refresh token; conecte o Tiny pela tela de integração.")
    if conn.refresh_expires_at and conn.refresh_expires_at < timezone.now():
        conn.status = TinyConnection.Status.ERROR
        conn.last_error = "Refresh token expirado (1 dia sem uso); reconecte."
        conn.save(update_fields=["status", "last_error"])
        raise TinyAuthError(conn.last_error)
    try:
        tokens = _token_request({
            "grant_type": "refresh_token", "client_id": conn.client_id, "client_secret": conn.client_secret,
            "refresh_token": conn.refresh_token,
        }, session)
    except TinyAuthError as exc:
        conn.status = TinyConnection.Status.ERROR
        conn.last_error = str(exc)[:300]
        conn.save(update_fields=["status", "last_error"])
        raise
    _store(conn, tokens)
    return conn


class TinyClient:
    """Chamadas à API v3 com token sempre válido e respeito ao rate limit."""

    def __init__(self, conn: TinyConnection | None = None, session: requests.Session | None = None, sleep=time.sleep):
        self.conn = conn or TinyConnection.get()
        self.session = session or requests.Session()
        self._sleep = sleep

    # --- infra -----------------------------------------------------------
    def _ensure_token(self) -> str:
        if not self.conn.access_token_valid:
            refresh(self.conn, self.session)
        return self.conn.access_token

    def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None, retries: int = 2) -> Any:
        url = f"{API_BASE}{path}"
        for attempt in range(retries + 1):
            token = self._ensure_token()
            resp = self.session.request(method, url, params=params, json=json, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}, timeout=60)
            if resp.status_code == 401 and attempt < retries:
                refresh(self.conn, self.session)
                continue
            if resp.status_code == 429 and attempt < retries:
                wait = int(resp.headers.get("X-RateLimit-Reset", "15") or 15)
                self._sleep(min(max(wait, 1), 60))
                continue
            if resp.status_code >= 400:
                try:
                    payload = resp.json()
                except ValueError:
                    payload = resp.text[:300]
                raise TinyError(f"{method} {path}: HTTP {resp.status_code} {payload}", resp.status_code, payload)
            if resp.status_code == 204 or not resp.content:
                return None
            return resp.json()
        raise TinyError(f"{method} {path}: esgotou as tentativas")

    def get(self, path: str, **params):
        return self.request("GET", path, params={k: v for k, v in params.items() if v is not None})

    def post(self, path: str, json: Any):
        return self.request("POST", path, json=json)

    def put(self, path: str, json: Any):
        return self.request("PUT", path, json=json)

    # --- produtos --------------------------------------------------------
    def list_products(self, sku: str | None = None, situacao: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        return self.get("/produtos", codigo=sku, situacao=situacao, limit=limit, offset=offset)

    def find_product_by_sku(self, sku: str) -> dict | None:
        data = self.list_products(sku=sku) or {}
        for item in data.get("itens", []):
            if (item.get("sku") or item.get("codigo")) == sku:
                return item
        return None

    def create_product(self, payload: dict) -> dict:
        return self.post("/produtos", payload)

    def update_product(self, tiny_id: int, payload: dict) -> dict | None:
        return self.put(f"/produtos/{tiny_id}", payload)

    def update_price(self, tiny_id: int, preco, preco_promocional=None, preco_custo=None) -> dict | None:
        body = {"preco": float(preco)}
        if preco_promocional is not None:
            body["precoPromocional"] = float(preco_promocional)
        if preco_custo is not None:
            body["precoCusto"] = float(preco_custo)
        return self.put(f"/produtos/{tiny_id}/preco", body)

    def add_attachments(self, tiny_id: int, urls: list[str]) -> dict | None:
        return self.post(f"/produtos/{tiny_id}/anexos", {"anexos": [{"url": u, "externo": True} for u in urls]})

    def update_stock(self, tiny_id: int, quantity, kind: str = "B", observacoes: str = "") -> dict | None:
        """kind: B = balanço (define o saldo), E = entrada, S = saída."""
        return self.post(f"/estoque/{tiny_id}", {"tipo": kind, "quantidade": float(quantity), "observacoes": observacoes})

    # --- pedidos ---------------------------------------------------------
    def list_orders(self, situacao: int | None = None, data_inicial: str | None = None, data_final: str | None = None,
                    data_atualizacao: str | None = None, limit: int = 100, offset: int = 0) -> dict:
        return self.get("/pedidos", situacao=situacao, dataInicial=data_inicial, dataFinal=data_final,
                        dataAtualizacao=data_atualizacao, limit=limit, offset=offset, orderBy="asc")

    def iter_orders(self, **filters):
        offset = 0
        while True:
            page = self.list_orders(offset=offset, **filters) or {}
            items = page.get("itens", [])
            yield from items
            pag = page.get("paginacao", {})
            offset += pag.get("limit", 100)
            if not items or offset >= pag.get("total", 0):
                break

    def get_order(self, tiny_id: int) -> dict:
        return self.get(f"/pedidos/{tiny_id}")
