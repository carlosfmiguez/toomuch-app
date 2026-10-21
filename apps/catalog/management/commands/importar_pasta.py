"""
Importação em lote pela linha de comando.

    python manage.py importar_pasta /caminho/da/pasta --mapa mapeamento.xlsx   # gera a planilha
    python manage.py importar_pasta /caminho/da/pasta --aplicar mapeamento.xlsx # importa conforme a planilha revisada
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.catalog import importer


class Command(BaseCommand):
    help = "Lê uma pasta com fotos e arquivos 3D, gera a planilha de mapeamento ou importa os produtos."

    def add_arguments(self, parser):
        parser.add_argument("pasta", help="Pasta com os arquivos (subpasta por produto, ou arquivos soltos)")
        parser.add_argument("--mapa", help="Caminho onde salvar a planilha de mapeamento (XLSX)")
        parser.add_argument("--aplicar", help="Planilha de mapeamento revisada para importar")

    def handle(self, *args, **options):
        root = Path(options["pasta"])
        if not root.is_dir():
            raise CommandError(f"Pasta não encontrada: {root}")
        if options["aplicar"]:
            mapping = importer.read_mapping(Path(options["aplicar"]).read_bytes())
            report = importer.run(mapping, root)
            self.stdout.write(f"Criados: {len(report.created)} · atualizados: {len(report.updated)} · ignorados: {len(report.skipped)} · arquivos anexados: {report.assets}")
            for line in report.created:
                self.stdout.write(f"  + {line}")
            for line in report.updated:
                self.stdout.write(f"  ~ {line}")
            for line in report.errors:
                self.stdout.write(self.style.ERROR(f"  ! {line}"))
            return
        candidates = importer.scan(root)
        out = Path(options["mapa"] or (root / "mapeamento-toomuch.xlsx"))
        out.write_bytes(importer.write_mapping(candidates))
        named = sum(1 for c in candidates if c.name)
        self.stdout.write(self.style.SUCCESS(f"{len(candidates)} produtos detectados ({named} com nome). Planilha: {out}"))
        self.stdout.write("Revise a planilha e rode de novo com --aplicar.")
