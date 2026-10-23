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
