from decimal import Decimal

from django import forms

from .models import GlobalParams, Printer


class QuickCalculatorForm(forms.Form):
    """Orça uma peça sem cadastrar nada: gramas, horas, componentes e minutos."""

    grams = forms.DecimalField(label="Filamento (g)", min_value=0, decimal_places=2, initial=Decimal("270"))
    cost_per_kg = forms.DecimalField(label="Custo do filamento (R$/kg)", min_value=0, decimal_places=2, initial=Decimal("95"))
    hours = forms.DecimalField(label="Tempo de impressão (h)", min_value=0, decimal_places=2, initial=Decimal("9"))
    printer = forms.ModelChoiceField(label="Impressora", queryset=Printer.objects.filter(status=Printer.Status.ACTIVE), required=False)
    labor_minutes = forms.IntegerField(label="Mão de obra (min)", min_value=0, initial=25)
    components = forms.DecimalField(label="Componentes comprados (R$)", min_value=0, decimal_places=2, initial=Decimal("18"))
    packaging = forms.DecimalField(label="Embalagem (R$)", min_value=0, decimal_places=2, initial=Decimal("6"))
    license_cost = forms.DecimalField(label="Licença por unidade (R$)", min_value=0, decimal_places=2, initial=Decimal("0"), required=False)
    waste_pct = forms.DecimalField(label="Desperdício (%)", min_value=0, decimal_places=2, required=False)
    failure_pct = forms.DecimalField(label="Taxa de falha (%)", min_value=0, decimal_places=2, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        params = GlobalParams.current()
        self.fields["waste_pct"].initial = params.waste_pct
        self.fields["failure_pct"].initial = params.failure_pct
        first = self.fields["printer"].queryset.first()
        if first:
            self.fields["printer"].initial = first.pk
