"""
Credenciais do conector MCP: clientes OAuth registrados (registro dinâmico), códigos
de autorização e tokens. Tokens e códigos são guardados só como hash SHA-256.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


def new_secret(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class OAuthClient(models.Model):
    client_id = models.CharField(max_length=64, unique=True, default=new_secret)
    name = models.CharField("nome", max_length=200, blank=True)
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "cliente OAuth"
        verbose_name_plural = "clientes OAuth"

    def __str__(self) -> str:
        return self.name or self.client_id[:12]


class AuthCode(models.Model):
    code_hash = models.CharField(max_length=64, unique=True)
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    redirect_uri = models.TextField()
    code_challenge = models.CharField(max_length=128)
    scope = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField()
    used = models.BooleanField(default=False)

    @classmethod
    def issue(cls, client, user, redirect_uri, challenge, scope="") -> str:
        code = new_secret()
        cls.objects.create(
            code_hash=digest(code), client=client, user=user, redirect_uri=redirect_uri, code_challenge=challenge,
            scope=scope, expires_at=timezone.now() + timedelta(minutes=10),
        )
        return code


class AccessToken(models.Model):
    """Par access/refresh. O access vale 8 h; o refresh, 60 dias (e é trocado a cada uso)."""

    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="tokens")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="mcp_tokens")
    access_hash = models.CharField(max_length=64, unique=True)
    refresh_hash = models.CharField(max_length=64, unique=True)
    scope = models.CharField(max_length=200, blank=True)
    expires_at = models.DateTimeField()
    refresh_expires_at = models.DateTimeField()
    revoked = models.BooleanField("revogado", default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    ACCESS_TTL = timedelta(hours=8)
    REFRESH_TTL = timedelta(days=60)

    class Meta:
        verbose_name = "acesso do Claude"
        verbose_name_plural = "acessos do Claude"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.client} · {self.user} · {self.created_at:%d/%m/%Y %H:%M}"

    @classmethod
    def issue(cls, client, user, scope="") -> tuple["AccessToken", str, str]:
        access, refresh = new_secret(), new_secret()
        now = timezone.now()
        tok = cls.objects.create(
            client=client, user=user, access_hash=digest(access), refresh_hash=digest(refresh), scope=scope,
            expires_at=now + cls.ACCESS_TTL, refresh_expires_at=now + cls.REFRESH_TTL,
        )
        return tok, access, refresh

    @classmethod
    def from_bearer(cls, header: str):
        if not header or not header.lower().startswith("bearer "):
            return None
        tok = cls.objects.select_related("user").filter(access_hash=digest(header[7:].strip()), revoked=False).first()
        if not tok or tok.expires_at < timezone.now() or not tok.user.is_active or not tok.user.is_staff:
            return None
        return tok
