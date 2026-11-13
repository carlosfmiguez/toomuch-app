from django.contrib.auth.decorators import login_required
from django.db import connection
from django.http import JsonResponse
from django.shortcuts import redirect


@login_required
def dashboard(request):
    """A raiz do sistema é o painel do back-office (admin/index.html com o shell do Unfold)."""
    return redirect("admin:index")


def healthz(request):
    """Verificação de saúde para a hospedagem: responde 200 se o banco responde."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        return JsonResponse({"status": "ok"})
    except Exception as exc:  # noqa: BLE001
        return JsonResponse({"status": "erro", "detail": str(exc)[:200]}, status=503)


def bad_request(request, exception=None):
    """
    400 com o motivo para quem é da equipe (ex.: limite de campos, arquivo suspeito, sessão interrompida);
    para os demais, a página genérica. O motivo também vai para o log do servidor.
    """
    import logging

    from django.http import HttpResponseBadRequest
    from django.utils.html import escape

    reason = f"{type(exception).__name__}: {exception}" if exception else "sem detalhe"
    logging.getLogger("django.request").warning("400 em %s: %s", request.path, reason)
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_staff", False):
        return HttpResponseBadRequest(
            "<h1>Bad Request (400)</h1><p>Motivo (visível só para a equipe): <code>" + escape(reason) + "</code></p>"
            f"<p>Endereço: <code>{escape(request.method)} {escape(request.path)}</code></p>")
    return HttpResponseBadRequest("<h1>Bad Request (400)</h1>")


def csrf_failure(request, reason=""):
    """
    403 de CSRF com explicação e caminho de volta, em vez da página crua do Django.
    Quase sempre é formulário aberto antes de um login/logout (o token muda) ou aba aberta há muito tempo.
    Para a equipe mostra o motivo, o domínio e a origem do envio; tudo vai para o log.
    """
    import logging

    from django.conf import settings
    from django.http import HttpResponseForbidden
    from django.utils.html import escape

    origin = request.META.get("HTTP_ORIGIN", "") or "(sem Origin)"
    referer = request.META.get("HTTP_REFERER", "")
    host = request.get_host() if request.META.get("HTTP_HOST") else "?"
    logging.getLogger("django.security.csrf").warning(
        "CSRF 403 em %s %s: %s | host=%s origin=%s referer=%s", request.method, request.path, reason, host, origin, referer)
    back = escape(referer or request.path)
    if "Origin checking failed" in reason or "Referer checking failed" in reason:
        hint = ("Você enviou o formulário por um endereço que o sistema não reconhece. "
                "Acesse pelo endereço oficial e tente de novo.")
    elif "CSRF cookie not set" in reason:
        hint = ("O navegador não guardou o cookie de segurança. Recarregue a página; "
                "se continuar, libere cookies para este site.")
    else:
        hint = ("A página ficou aberta de uma sessão anterior (por exemplo, você entrou ou saiu em outra aba). "
                "Recarregue a página e envie de novo — os dados digitados podem precisar ser preenchidos outra vez.")
    detail = ""
    user = getattr(request, "user", None)
    if user is not None and getattr(user, "is_staff", False) or settings.DEBUG:
        detail = (f"<p style='font-size:13px;color:#666'>Motivo técnico: <code>{escape(reason)}</code><br>"
                  f"Domínio: <code>{escape(host)}</code> · Origem: <code>{escape(origin)}</code><br>"
                  f"Domínios aceitos: <code>{escape(', '.join(settings.CSRF_TRUSTED_ORIGINS))}</code></p>")
    html = (
        "<!doctype html><html lang='pt-br'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'><title>Envio bloqueado</title></head>"
        "<body style='font-family:system-ui,sans-serif;max-width:560px;margin:60px auto;padding:0 16px;color:#222'>"
        "<h1 style='font-size:22px'>Envio bloqueado por segurança</h1>"
        f"<p>{hint}</p>"
        f"<p><a href='{back}' style='display:inline-block;padding:10px 16px;background:#222;color:#fff;"
        "border-radius:8px;text-decoration:none'>Voltar e recarregar</a> "
        "<a href='/' style='margin-left:12px'>Início</a></p>"
        f"{detail}</body></html>")
    return HttpResponseForbidden(html)
