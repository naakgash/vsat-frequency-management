"""Template tags for rendering specification codes and values.

Specification section 2 requires one reusable component wherever a specification code is
displayed, and forbids hard-coding the same description in multiple templates. These tags
are that component; ``tests/specifications/test_no_hardcoded_descriptions.py`` enforces
that nothing else duplicates them.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Any

from django import template
from django.utils.safestring import SafeString

from specifications import selectors

register = template.Library()

#: Hz per MHz. RF and IF values are stored as integer Hz (specification section 14.1)
#: and displayed in MHz, so conversion happens once, here, in Decimal.
HZ_PER_MHZ = Decimal(1_000_000)


@register.inclusion_tag("partials/spec_info_button.html", takes_context=True)
def spec_code(context: dict[str, Any], code: str, label: str = "") -> dict[str, Any]:
    """Render a specification code with its information button.

    Section 10.3: tables show the code as the primary compact representation, with the
    explanation behind the info button. ``label`` overrides what is shown, for the rare
    place a display name reads better than the code.
    """
    definition = selectors.get_definition(code, context.get("request"))
    return {
        "code": code,
        "definition": definition,
        "visible_label": label or code,
        # A DOM id must be unique per rendering: the same code can legitimately appear
        # twice on one page, and duplicate ids break aria-controls.
        "popover_id": f"spec-pop-{uuid.uuid4().hex[:12]}",
    }


@register.inclusion_tag("partials/spec_info_button.html", takes_context=True)
def spec_label(context: dict[str, Any], code: str) -> dict[str, Any]:
    """Render a specification's human-readable name with its information button.

    For forms and detail views, where a bare code would be unhelpful.
    """
    definition = selectors.get_definition(code, context.get("request"))
    return {
        "code": code,
        "definition": definition,
        "visible_label": definition.label if definition else code,
        "popover_id": f"spec-pop-{uuid.uuid4().hex[:12]}",
    }


@register.simple_tag(takes_context=True)
def spec_value(context: dict[str, Any], value: Any, code: str, with_unit: bool = True) -> str:
    """Format a value using the dictionary's unit and display precision.

    Formatting lives here rather than in each template so that changing a precision is an
    administrator's edit rather than a code change (specification section 9.4: "Use clear
    units and display precision from the Specification Dictionary").
    """
    if value is None or value == "":
        return "—"  # em dash

    definition = selectors.get_definition(code, context.get("request"))
    if definition is None:
        return str(value)

    rendered = _format(value, definition)
    if with_unit and definition.unit:
        return f"{rendered} {definition.unit}"
    return rendered


@register.simple_tag(takes_context=True)
def spec_unit(context: dict[str, Any], code: str) -> str:
    definition = selectors.get_definition(code, context.get("request"))
    return definition.unit if definition else ""


@register.simple_tag(takes_context=True)
def prime_specs(context: dict[str, Any], *codes: str) -> SafeString:
    """Load several definitions in one query before rendering a table.

    Without this each column header issues its own query. Returns an empty string so it
    can be called for its effect at the top of a template.
    """
    request = context.get("request")
    if request is not None and codes:
        selectors.prime_cache(list(codes), request)
    return SafeString("")


def _format(value: Any, definition: Any) -> str:
    """Apply the dictionary's precision, converting Hz to MHz when that is the unit."""
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return str(value)

    # Frequencies are stored in integer Hz but almost always presented in MHz. The unit
    # recorded in the dictionary is what decides; no template performs this conversion.
    if definition.unit.upper() == "MHZ" and definition.data_type == "INTEGER_HZ":
        number = number / HZ_PER_MHZ

    quantized = number.quantize(Decimal(1).scaleb(-definition.display_precision))
    # Thousands separators: section 9.5 renders "29,145.000".
    return f"{quantized:,f}" if definition.display_precision else f"{quantized:,.0f}"
