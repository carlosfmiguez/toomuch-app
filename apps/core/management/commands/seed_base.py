"""
Dados iniciais com as decisões da especificação (20/10/2026):
parâmetros globais, impressoras, canais com taxas, categorias e códigos de cor.
Idempotente: pode rodar mais de uma vez.
"""

from datetime import date
from decimal import Decimal

from django.core.management.base import BaseCommand

from apps.catalog.models import Category, Color
from apps.costing.models import Channel, ChannelFee, GlobalParams, Printer

EFFECTIVE = date(2026, 10, 20)

CATEGORIES = [
    ("LUM", "Luminárias de mesa", 1),
    ("VAS", "Vasos decorativos", 2),
    ("UTI", "Organizadores e utilidades", 3),
    ("REL", "Decoração religiosa", 4),
    ("SAZ", "Sazonais e presentes", 5),
    ("NAT", "Natal", 6),
    ("PAS", "Páscoa", 7),
    ("BAN", "Banheiro", 8),
    ("MAQ", "Maquiagem e acessórios", 9),
    ("ESC", "Escritório", 10),
    ("COZ", "Cozinha", 11),
    ("CAS", "Casa e decoração", 12),
]

COLORS = [
    ("BRC", "Branco", "#F6F4EF"),
    ("ARE", "Areia", "#D9C7A6"),
    ("VRM", "Vermelho", "#C94A2B"),
    ("VGA", "Verde-água", "#BFE3D8"),
    ("PRE", "Preto", "#2B2622"),
    ("CIN", "Cinza", "#8E8A84"),
    ("OLV", "Verde-oliva", "#6E7A4E"),
    ("ROS", "Rosa", "#F2A7A1"),
    ("PES", "Pêssego", "#E9B98F"),
    ("MOS", "Mostarda", "#D89E22"),
    ("AZM", "Azul-marinho", "#2F4A7A"),
    ("MAD", "Madeira", "#B88A5A"),
]

PRINTERS = [
    ("A1 #1", "Bambu Lab A1", 70),
    ("A1 #2", "Bambu Lab A1", 70),
    ("H2D", "Bambu Lab H2D", 120),
    ("VzBot 330", "VzBot 330 AWD", 150),
]

# Taxas de canal: estimativas da especificação, a conferir na página de canais.
CHANNELS = [
    {
        "name": "Site", "kind": Channel.Kind.SITE, "order": 1, "export": Channel.Export.TINY,
        "fee": dict(fee_pct="5.00", fixed_fee="0.00", shipping_subsidy="0.00", target_margin_pct="30", min_margin_pct="20",
                    note="Gateway de pagamento; estimativa"),
    },
    {
        "name": "Mercado Livre", "kind": Channel.Kind.MARKETPLACE, "order": 2, "export": Channel.Export.ML_CSV,
        "fee": dict(fee_pct="14.00", fixed_fee="0.00", shipping_subsidy="20.00", shipping_subsidy_from_price="79.00",
                    target_margin_pct="30", min_margin_pct="20", note="Anúncio clássico; frete subsidiado acima de R$ 79; estimativa"),
    },
    {
        "name": "Shopee", "kind": Channel.Kind.MARKETPLACE, "order": 3, "export": Channel.Export.SHOPEE_CSV,
        "fee": dict(fee_pct="20.00", fixed_fee="4.00", shipping_subsidy="0.00", target_margin_pct="30", min_margin_pct="20",
                    note="Comissão + programa de frete grátis; estimativa"),
    },
]


class Command(BaseCommand):
    help = "Cria parâmetros, impressoras, canais, categorias e cores iniciais."

    def handle(self, *args, **options):
        if not GlobalParams.objects.exists():
            GlobalParams.objects.create(
                effective_from=EFFECTIVE, energy_tariff_kwh=Decimal("0.95"), labor_cost_per_hour=Decimal("30.00"),
                waste_pct=Decimal("2.00"), failure_pct=Decimal("5.00"), simples_pct=Decimal("4.00"),
                packaging_default_cost=Decimal("0.00"), note="Decisões de 20/10/2026 (Celesc R$ 0,95/kWh; Simples Anexo I 1ª faixa)",
            )
            self.stdout.write("Parâmetros globais criados.")

        for code, name, order in CATEGORIES:
            Category.objects.get_or_create(code=code, defaults={"name": name, "order": order})
        for code, name, hex_ in COLORS:
            Color.objects.get_or_create(code=code, defaults={"name": name, "hex": hex_})
        self.stdout.write(f"{len(CATEGORIES)} categorias e {len(COLORS)} cores.")

        for name, model, power in PRINTERS:
            Printer.objects.get_or_create(
                name=name, defaults={"model": model, "purchase_price": Decimal("3000.00"), "lifetime_hours": 5000,
                                     "maintenance_per_hour": Decimal("0.20"), "power_w": power},
            )
        self.stdout.write(f"{len(PRINTERS)} impressoras (R$ 3.000 cada para formação de preço).")

        for spec in CHANNELS:
            channel, _ = Channel.objects.get_or_create(
                name=spec["name"], defaults={"kind": spec["kind"], "order": spec["order"], "export_format": spec["export"]},
            )
            if not channel.fees.exists():
                fee = {k: (Decimal(v) if isinstance(v, str) and k != "note" else v) for k, v in spec["fee"].items()}
                ChannelFee.objects.create(channel=channel, effective_from=EFFECTIVE, **fee)
        self.stdout.write(f"{len(CHANNELS)} canais com taxas iniciais (conferir valores reais).")
        self.stdout.write(self.style.SUCCESS("Dados iniciais prontos."))
