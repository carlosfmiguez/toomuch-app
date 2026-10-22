"""Filtros das telas próprias."""

from django import template

register = template.Library()


@register.filter
def hm(minutes):
    """340 → '5h40'; 45 → '45 min'."""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return "—"
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60}h{minutes % 60:02d}"


@register.filter
def dot(value):
    """Decimal com ponto, para atributos data-* lidos pelo JavaScript."""
    if value is None:
        return ""
    return str(value)


@register.filter
def brl(value):
    """118.9 → '118,90' (sem o R$)."""
    if value is None or value == "":
        return "—"
    try:
        s = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return value
    return s.replace(",", "X").replace(".", ",").replace("X", ".")
