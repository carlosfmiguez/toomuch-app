"""
Endpoint MCP (Streamable HTTP, sem sessão): POST /mcp com JSON-RPC 2.0.
Responde em JSON (o transporte permite JSON em vez de SSE). Autenticação por Bearer
emitido pelo OAuth deste sistema; sem token → 401 com o endereço dos metadados.
"""

from __future__ import annotations

import json
import logging
import traceback

from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from .models import AccessToken
from .oauth import _cors, base_url
from .tools import TOOLS, ToolError

log = logging.getLogger(__name__)

SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
INSTRUCTIONS = (
    "Sistema Toomuch: produtos impressos em 3D (decoração e utilidades). Fluxo de um produto: dados → peças com "
    "3MF fatiado (gramas e tempo viram custo) → variantes (tamanho/cor/filamento; custo calculado sozinho) → "
    "aprovar preço por canal → fotos → status active. Use ver_produto antes de alterar e mostre ao usuário o link "
    "'ficha' do produto. Preços em R$. Produtos de terceiros sem licença não entram no catálogo."
)


def _rpc_result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_error(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _handle(msg: dict, token: AccessToken, base: str):
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    if msg_id is None:  # notificação (ex.: notifications/initialized)
        return None
    if method == "initialize":
        wanted = params.get("protocolVersion")
        return _rpc_result(msg_id, {
            "protocolVersion": wanted if wanted in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "toomuch", "title": "Sistema Toomuch", "version": "1.0"},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return _rpc_result(msg_id, {})
    if method == "tools/list":
        return _rpc_result(msg_id, {"tools": [t["spec"] for t in TOOLS.values()]})
    if method == "tools/call":
        name = params.get("name")
        entry = TOOLS.get(name)
        if entry is None:
            return _rpc_error(msg_id, -32602, f"Ferramenta desconhecida: {name}")
        args = params.get("arguments") or {}
        try:
            data = entry["fn"](token.user, base, **args)
            text = json.dumps(data, ensure_ascii=False, default=str, indent=1)
            return _rpc_result(msg_id, {"content": [{"type": "text", "text": text}], "isError": False})
        except ToolError as exc:
            return _rpc_result(msg_id, {"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except TypeError as exc:
            return _rpc_result(msg_id, {"content": [{"type": "text", "text": f"Argumentos inválidos: {exc}"}], "isError": True})
        except Exception as exc:  # noqa: BLE001 - o erro volta para o Claude, não derruba o servidor
            log.error("MCP %s falhou: %s", name, traceback.format_exc())
            return _rpc_result(msg_id, {"content": [{"type": "text", "text": f"Erro interno: {type(exc).__name__}: {exc}"}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return _rpc_result(msg_id, {method.split("/")[0]: []})
    return _rpc_error(msg_id, -32601, f"Método não suportado: {method}")


@csrf_exempt
def endpoint(request):
    if request.method == "OPTIONS":
        return _cors(HttpResponse(status=204))
    base = base_url(request)
    token = AccessToken.from_bearer(request.META.get("HTTP_AUTHORIZATION", ""))
    if token is None:
        resp = JsonResponse({"error": "invalid_token"}, status=401)
        resp["WWW-Authenticate"] = f'Bearer resource_metadata="{base}/.well-known/oauth-protected-resource"'
        return _cors(resp)
    if request.method == "GET":
        return _cors(HttpResponse(status=405))  # sem stream do servidor
    if request.method == "DELETE":
        return _cors(HttpResponse(status=204))
    if request.method != "POST":
        return _cors(HttpResponse(status=405))
    AccessToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
    try:
        payload = json.loads(request.body or b"null")
    except ValueError:
        return _cors(JsonResponse(_rpc_error(None, -32700, "JSON inválido"), status=400))
    if isinstance(payload, list):
        out = [r for r in (_handle(m, token, base) for m in payload) if r is not None]
        return _cors(JsonResponse(out, safe=False)) if out else _cors(HttpResponse(status=202))
    if not isinstance(payload, dict):
        return _cors(JsonResponse(_rpc_error(None, -32600, "Requisição inválida"), status=400))
    result = _handle(payload, token, base)
    if result is None:
        return _cors(HttpResponse(status=202))
    return _cors(JsonResponse(result))
