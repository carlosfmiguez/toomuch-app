"""
Cria (ou atualiza a senha de) um superusuário a partir de variáveis de ambiente,
para o primeiro acesso em hospedagens sem shell:

    DJANGO_SUPERUSER_USERNAME=carlos
    DJANGO_SUPERUSER_EMAIL=carlos@exemplo.com
    DJANGO_SUPERUSER_PASSWORD=uma-senha-longa

Sem as variáveis, não faz nada. Só redefine a senha se DJANGO_SUPERUSER_RESET_PASSWORD=1.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Garante o superusuário definido em DJANGO_SUPERUSER_USERNAME/EMAIL/PASSWORD."

    def handle(self, *args, **options):
        username = os.environ.get("DJANGO_SUPERUSER_USERNAME", "").strip()
        password = os.environ.get("DJANGO_SUPERUSER_PASSWORD", "")
        email = os.environ.get("DJANGO_SUPERUSER_EMAIL", "").strip()
        if not username or not password:
            self.stdout.write("DJANGO_SUPERUSER_* não definido; nada a fazer.")
            return
        User = get_user_model()
        user, created = User.objects.get_or_create(username=username, defaults={"email": email, "is_staff": True, "is_superuser": True})
        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Superusuário '{username}' criado."))
        elif os.environ.get("DJANGO_SUPERUSER_RESET_PASSWORD") == "1":
            user.set_password(password)
            user.is_staff = user.is_superuser = True
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Senha de '{username}' redefinida."))
        else:
            self.stdout.write(f"Superusuário '{username}' já existe.")
