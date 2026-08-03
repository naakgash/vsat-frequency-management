"""Display filters for radio-frequency values.

The read side of :mod:`inventory.units`. A window's edges are stored as integer Hz and
shown as MHz, and ``{{ window.rf_start_hz|mhz }}`` is the only sanctioned way to do that
in a template — never arithmetic in the template itself, which would be performed in
whatever numeric type the template engine happens to pick.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django import template

from inventory import units

register = template.Library()

#: Em dash, matching what the specification tags render for an absent value.
EMPTY = "—"


@register.filter
def mhz(value: Any, precision: int = 3) -> str:
    """Render a value in Hz as MHz with thousands separators.

    Three decimal places by default, which is 1 kHz — fine enough to distinguish any two
    edges the platform allocates, and coarse enough to read. A caller needing the exact
    Hz asks for ``|mhz:6``.
    """
    if value is None or value == "":
        return EMPTY
    try:
        converted = units.to_mhz(int(value))
    except (TypeError, ValueError):
        return str(value)
    assert converted is not None  # int input never yields None
    quantized = converted.quantize(Decimal(1).scaleb(-precision))
    return f"{quantized:,f}"


@register.filter
def hz(value: Any) -> str:
    """Render a value in Hz with thousands separators, for the rare place Hz is wanted."""
    if value is None or value == "":
        return EMPTY
    try:
        return f"{int(value):,d}"
    except (TypeError, ValueError):
        return str(value)
