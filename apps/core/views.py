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
