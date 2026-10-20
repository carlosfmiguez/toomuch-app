"""Testes de regressão do motor de custo: o exemplo da Solaris da especificação."""

from decimal import Decimal as D

from django.test import TestCase

from apps.costing import engine


class SolarisExampleTests(TestCase):
    """Luminária Solaris G: cúpula 160 g (5h40) + base 110 g (3h20) na A1; kit R$ 18; embalagem R$ 6."""

    def setUp(self):
        self.hourly = engine.hourly_cost(D("3000"), 5000, D("0.20"), 70, D("0.95"))
        self.inp = engine.CostInput(
            filaments=[
                engine.FilamentUse(D("160"), D("0.095"), label="cúpula"),
                engine.FilamentUse(D("110"), D("0.095"), label="base"),
            ],
            machines=[
                engine.MachineUse(D("340") / 60, self.hourly, "cúpula"),
                engine.MachineUse(D("200") / 60, self.hourly, "base"),
            ],
            components=[
                engine.ComponentUse(D("1"), D("18.00"), "kit E27"),
                engine.ComponentUse(D("1"), D("6.00"), "caixa", is_packaging=True),
            ],
            labor_minutes=D("25"), labor_rate_per_hour=D("30"), waste_pct=D("2"), failure_pct=D("5"),
        )

    def test_hourly_cost_is_shown_with_two_decimals(self):
        self.assertEqual(self.hourly, D("0.87"))

    def test_cost_matches_specification(self):
        b = engine.compute_cost(self.inp)
        self.assertEqual(engine.q2(b.filament), D("26.16"))
        self.assertEqual(engine.q2(b.machine), D("7.83"))
        self.assertEqual(engine.q2(b.labor), D("12.50"))
        self.assertEqual(engine.q2(b.components), D("18.00"))
        self.assertEqual(engine.q2(b.packaging), D("6.00"))
        self.assertEqual(engine.q2(b.failure), D("1.70"))
        self.assertEqual(b.total, D("72.19"))

    def test_prices_by_channel_match_specification(self):
        cost = engine.compute_cost(self.inp).total
        site = engine.ChannelTerms(fee_pct=D("5"), tax_pct=D("4"), target_margin_pct=D("30"))
        shopee = engine.ChannelTerms(fee_pct=D("20"), fixed_fee=D("4"), tax_pct=D("4"), target_margin_pct=D("30"))
        ml = engine.ChannelTerms(fee_pct=D("14"), shipping_subsidy=D("20"), shipping_subsidy_from_price=D("79"), tax_pct=D("4"), target_margin_pct=D("30"))
        self.assertEqual(engine.suggest_price(cost, site).suggested, D("118.90"))
        self.assertEqual(engine.suggest_price(cost, shopee).suggested, D("165.90"))
        self.assertEqual(engine.suggest_price(cost, ml).suggested, D("177.90"))
        for terms, price in ((site, D("118.90")), (shopee, D("165.90")), (ml, D("177.90"))):
            self.assertGreaterEqual(engine.margin_at(price, cost, terms), D("30"))


class RoundingAndMarginTests(TestCase):
    def test_round_up_to_90(self):
        self.assertEqual(engine.round_up_90(D("118.34")), D("118.90"))
        self.assertEqual(engine.round_up_90(D("118.95")), D("119.90"))
        self.assertEqual(engine.round_up_90(D("118.90")), D("118.90"))
        self.assertEqual(engine.round_up_90(D("119")), D("119.90"))

    def test_fixed_fee_only_below_threshold(self):
        terms = engine.ChannelTerms(fee_pct=D("14"), fixed_fee=D("6"), fixed_fee_below_price=D("79"), tax_pct=D("4"), target_margin_pct=D("30"))
        cheap = engine.suggest_price(D("20"), terms)
        self.assertEqual(cheap.fixed_fee_applied, D("6"))
        expensive = engine.suggest_price(D("72.19"), terms)
        self.assertEqual(expensive.fixed_fee_applied, D("0"))

    def test_impossible_margin_raises(self):
        with self.assertRaises(ValueError):
            engine.suggest_price(D("10"), engine.ChannelTerms(fee_pct=D("60"), tax_pct=D("10"), target_margin_pct=D("30")))

    def test_weighted_average_cost(self):
        # 400 g a 0,10 + 1000 g a 0,085 → (40 + 85) / 1400
        avg = engine.weighted_average_cost(D("400"), D("0.10"), D("1000"), D("0.085"))
        self.assertEqual(avg.quantize(D("0.000001")), D("0.089286"))
        self.assertEqual(engine.weighted_average_cost(D("0"), D("0"), D("1000"), D("0.095")), D("0.095"))
