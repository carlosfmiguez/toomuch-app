"""
Servidor de autorização OAuth 2.1 mínimo para o conector do Claude.

- Metadados (RFC 8414 e RFC 9728) para o Claude descobrir os endpoints.
- Registro dinâmico de cliente (RFC 7591), só com redirect para o Claude ou localhost.
- Autorização com PKCE S256: exige login no sistema (usuário da equipe) e um clique em "Permitir".
- Token: authorization_code e refresh_token (o refresh é trocado a cada uso).
"""

from __future__ import annotations

import base64
import hashlib
import json
from urllib.parse import urlencode, urlparse

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import AccessToken, AuthCode, OAuthClient, digest

SCOPE = "toomuch"
ALLOWED_REDIRECT_HOSTS = ("claude.ai", "claude.com", "localhost", "127.0.0.1")


def base_url(request) -> str:
    return request.build_absolute_uri("/").rstrip("/")


def _cors(resp):
    resp["Access-Control-Allow-Origin"] = "*"
    resp["Access-Control-Allow-Headers"] = "Authorization, Content-Type, Mcp-Protocol-Version, Mcp-Session-Id"
    resp["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


def protected_resource_metadata(request, *args):
    base = base_url(request)
    return _cors(JsonResponse({
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "Sistema Toomuch",
    }))


def authorization_server_metadata(request, *args):
    base = base_url(request)
    return _cors(JsonResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "client_secret_post", "client_secret_basic"],
        "scopes_supported": [SCOPE],
    }))


def _redirect_ok(uri: str) -> bool:
    try:
        u = urlparse(uri)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    if host in ("localhost", "127.0.0.1"):
        return u.scheme in ("http", "https")
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in ALLOWED_REDIRECT_HOSTS)


def _error(code: str, desc: str = "", status: int = 400):
    return _cors(JsonResponse({"error": code, "error_description": desc}, status=status))


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def register(request):
    if request.method == "OPTIONS":
        return _cors(JsonResponse({}))
    try:
        data = json.loads(request.body or b"{}")
    except ValueError:
        return _error("invalid_client_metadata", "JSON inválido")
    uris = data.get("redirect_uris") or []
    if not uris or not all(isinstance(u, str) and _redirect_ok(u) for u in uris):
        return _error("invalid_redirect_uri", "Só são aceitos endereços de retorno do Claude (claude.ai/claude.com) ou localhost.")
    client = OAuthClient.objects.create(name=str(data.get("client_name") or "Claude")[:200], redirect_uris=uris)
    return _cors(JsonResponse({
        "client_id": client.client_id,
        "client_id_issued_at": int(client.created_at.timestamp()),
        "client_name": client.name,
        "redirect_uris": uris,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "scope": SCOPE,
    }, status=201))


@login_required
@require_http_methods(["GET", "POST"])
def authorize(request):
    p = request.GET if request.method == "GET" else request.POST
    client = OAuthClient.objects.filter(client_id=p.get("client_id", "")).first()
    redirect_uri = p.get("redirect_uri", "")
    if client is None:
        return HttpResponseBadRequest("Cliente desconhecido. Remova e adicione o conector de novo no Claude.")
    if redirect_uri not in client.redirect_uris:
        return HttpResponseBadRequest("Endereço de retorno não registrado para este cliente.")
    state = p.get("state", "")

    def back(**params):
        if state:
            params["state"] = state
        sep = "&" if "?" in redirect_uri else "?"
        return redirect(f"{redirect_uri}{sep}{urlencode(params)}")

    if p.get("response_type") != "code":
        return back(error="unsupported_response_type")
    challenge, method = p.get("code_challenge", ""), p.get("code_challenge_method", "")
    if not challenge or method != "S256":
        return back(error="invalid_request", error_description="PKCE S256 obrigatório")
    if not request.user.is_staff:
        return back(error="access_denied", error_description="Usuário sem acesso ao sistema")

    if request.method == "POST":
        if p.get("decision") != "allow":
            return back(error="access_denied")
        code = AuthCode.issue(client, request.user, redirect_uri, challenge, p.get("scope") or SCOPE)
        return back(code=code)
    return render(request, "connector/authorize.html", {"client": client, "params": request.GET.items()})


def _pkce_ok(verifier: str, challenge: str) -> bool:
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return calc == challenge


def _client_from_request(request):
    client_id = request.POST.get("client_id", "")
    auth = request.META.get("HTTP_AUTHORIZATION", "")
    if not client_id and auth.lower().startswith("basic "):
        try:
            client_id = base64.b64decode(auth[6:]).decode().split(":", 1)[0]
        except (ValueError, UnicodeDecodeError):
            client_id = ""
    return OAuthClient.objects.filter(client_id=client_id).first()


def _token_response(tok, access, refresh):
    return _cors(JsonResponse({
        "access_token": access, "token_type": "Bearer", "expires_in": int(AccessToken.ACCESS_TTL.total_seconds()),
        "refresh_token": refresh, "scope": tok.scope or SCOPE,
    }, headers={"Cache-Control": "no-store"}))


@csrf_exempt
@require_http_methods(["POST", "OPTIONS"])
def token(request):
    if request.method == "OPTIONS":
        return _cors(JsonResponse({}))
    client = _client_from_request(request)
    if client is None:
        return _error("invalid_client", "Cliente desconhecido", status=401)
    grant = request.POST.get("grant_type")
    now = timezone.now()
    if grant == "authorization_code":
        code = AuthCode.objects.filter(code_hash=digest(request.POST.get("code", "")), client=client).first()
        if not code or code.used or code.expires_at < now:
            return _error("invalid_grant", "Código inválido ou expirado")
        if request.POST.get("redirect_uri") and request.POST["redirect_uri"] != code.redirect_uri:
            return _error("invalid_grant", "redirect_uri diferente")
        if not _pkce_ok(request.POST.get("code_verifier", ""), code.code_challenge):
            return _error("invalid_grant", "PKCE inválido")
        code.used = True
        code.save(update_fields=["used"])
        tok, access, refresh = AccessToken.issue(client, code.user, code.scope)
        return _token_response(tok, access, refresh)
    if grant == "refresh_token":
        old = AccessToken.objects.filter(refresh_hash=digest(request.POST.get("refresh_token", "")), client=client, revoked=False).first()
        if not old or old.refresh_expires_at < now or not old.user.is_active:
            return _error("invalid_grant", "Refresh inválido ou expirado")
        old.revoked = True
        old.save(update_fields=["revoked"])
        tok, access, refresh = AccessToken.issue(client, old.user, old.scope)
        return _token_response(tok, access, refresh)
    return _error("unsupported_grant_type")


@csrf_exempt
@require_http_methods(["POST"])
def revoke(request):
    value = request.POST.get("token", "")
    h = digest(value)
    AccessToken.objects.filter(access_hash=h).update(revoked=True)
    AccessToken.objects.filter(refresh_hash=h).update(revoked=True)
    return _cors(JsonResponse({}))
