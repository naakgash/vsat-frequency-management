"""Rendering one table cell, one sort link, one filter value. §10.3.

Three small tags, and the reason they exist rather than the template branching on a column key:
the column registry says how a value renders, so a template that decided for itself would be a
second registry — and the two would disagree the first time a column changed type.

None of them format a value themselves. A frequency goes through ``|mhz`` and a timestamp
through ``|utc``, which are the sanctioned filters for those, so the table cannot quietly
develop its own rounding or its own idea of a time zone.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from django import template
from django.utils.html import format_html
from django.utils.safestring import SafeString, mark_safe

from inventory.templatetags.rf import mhz
from inventory.templatetags.utc_tags import utc

register = template.Library()

EMPTY = "—"


@register.filter
def get(mapping: dict[str, Any], key: str) -> Any:
    """``{{ mapping|get:key }}`` — the dictionary lookup Django's template language lacks."""
    if not hasattr(mapping, "get"):
        return ""
    return mapping.get(key, "")


@register.simple_tag
def cell(row: Any, column: Any) -> SafeString | str:
    """One cell, rendered the way its column says.

    ``link`` and ``status`` produce markup and are built with ``format_html``, so a code
    containing a bracket is escaped rather than rendered.
    """
    value = column.value_of(row)
    if value is None or value == "":
        return EMPTY

    match column.render:
        case "link":
            return format_html('<a href="{}">{}</a>', row.get_absolute_url(), value)
        case "status":
            return format_html(
                '<span class="badge text-bg-light border">{}</span>', row.get_status_display()
            )
        case "mhz":
            return mhz(value)
        case "utc":
            return utc(value)
        case "number":
            return f"{value:,}" if isinstance(value, int) else str(value)
        case _:
            return str(value)


@register.simple_tag(takes_context=True)
def sort_query(context: dict[str, Any], key: str, current: str) -> str:
    """The query string that sorts by this column, toggling direction on a repeat click.

    Built from the request's own parameters so that sorting keeps the filters and the chosen
    columns. Rebuilding it from scratch would silently reset the table every time somebody
    clicked a heading — the classic version of this bug.
    """
    request = context.get("request")
    parameters: list[tuple[str, str]] = []
    if request is not None:
        parameters = [
            (name, value) for name, value in request.GET.items() if name not in {"sort", "column"}
        ]
        parameters += [("column", value) for value in request.GET.getlist("column")]

    parameters.append(("sort", f"-{key}" if current == key else key))
    return mark_safe(urlencode(parameters))  # noqa: S308 - urlencode escapes its own output
