"""
Importação em lote a partir de uma pasta.

    python manage.py import_folder /caminho/modelos --mapear planilha.xlsx     # etapa 1: gera a planilha
    python manage.py import_folder /caminho/modelos --aplicar planilha.xlsx    # etapa 2: importa a planilha revisada
    python manage.py import_folder /caminho/modelos --aplicar                  # importa direto com as sugestões
"""

from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from apps.catalog import importer


class Command(BaseCommand):
    help = "Varre uma pasta de modelos/fotos e importa como produtos em rascunho."

    def add_arguments(self, parser):
        parser.add_argument("pasta")
        parser.add_argument("--mapear", metavar="PLANILHA.xlsx", help="só gera a planilha de mapeamento para revisão")
        parser.add_argument("--aplicar", nargs="?", const="", metavar="PLANILHA.xlsx", help="importa (com a planilha revisada, se informada)")
        parser.add_argument("--usuario", help="username de quem importa (fica em 'enviado por')")

    def handle(self, *args, **options):
        root = Path(options["pasta"])
        if not root.is_dir():
            raise CommandError(f"Pasta não encontrada: {root}")
        if options["mapear"]:
            products = importer.scan(root)
            path = importer.write_mapping(products, options["mapear"])
            self.stdout.write(self.style.SUCCESS(f"{len(products)} produto(s) encontrados; planilha em {path}"))
            for sp in products:
                self.stdout.write(f"  {sp.sku_base:<18} {sp.name:<40} {sp.category_code}  {len(sp.models)} 3D / {len(sp.photos)} fotos  {sp.note}")
            return
        if options["aplicar"] is None:
            raise CommandError("Informe --mapear PLANILHA.xlsx ou --aplicar [PLANILHA.xlsx].")
        user = get_user_model().objects.filter(username=options["usuario"]).first() if options["usuario"] else None
        mapping = options["aplicar"] or None
        report = importer.apply(root, mapping, user=user)
        self.stdout.write(self.style.SUCCESS(
            f"Produtos criados: {report.created_products} · reaproveitados: {report.reused_products} · "
            f"arquivos novos: {report.files_added} · já existentes: {report.files_skipped} · linhas puladas: {report.skipped_rows}"
        ))
        for err in report.errors:
            self.stdout.write(self.style.WARNING(f"  {err}"))
