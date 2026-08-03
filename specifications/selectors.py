"""Read-side access to the Specification Dictionary.

The template tag calls :func:`get_definition` on every rendered code, and a dense Satnet
Path table renders dozens of codes per page. A per-request cache keeps that to one query
regardless of how many codes a page shows.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ObjectDoesNotExist

from specifications.models import SpecificationCategory, SpecificationDefinition

#: Cache key on the request object. Request-scoped rather than process-scoped so an
#: administrator's edit is visible on the very next page load — a process cache would
#: leave other workers serving stale descriptions until they were recycled.
_CACHE_ATTR = "_specification_cache"


def _cache(request: Any) -> dict[str, SpecificationDefinition | None]:
    cache = getattr(request, _CACHE_ATTR, None)
    if cache is None:
        cache = {}
        setattr(request, _CACHE_ATTR, cache)
    return cache


def get_definition(code: str, request: Any = None) -> SpecificationDefinition | None:
    """Return a definition by code, or None when it is not in the dictionary.

    Returning None rather than raising is deliberate: a missing dictionary entry must
    degrade to showing the bare code, not break the page that referenced it. Missing
    entries are reported by ``manage.py check_specifications`` instead.
    """
    if request is not None:
        cache = _cache(request)
        if code in cache:
            return cache[code]

    try:
        definition = SpecificationDefinition.objects.select_related("category").get(code=code)
    except ObjectDoesNotExist:
        definition = None

    if request is not None:
        _cache(request)[code] = definition
    return definition


def prime_cache(codes: list[str], request: Any) -> None:
    """Load several definitions in one query.

    Call this from a view that is about to render a table: without it, each column
    header would issue its own query.
    """
    cache = _cache(request)
    missing = [code for code in codes if code not in cache]
    if not missing:
        return

    found = SpecificationDefinition.objects.select_related("category").filter(code__in=missing)
    for definition in found:
        cache[definition.code] = definition
    for code in missing:
        cache.setdefault(code, None)


def visible_in_tables() -> list[SpecificationDefinition]:
    return list(SpecificationDefinition.objects.for_table().select_related("category"))


def grouped_by_category() -> list[tuple[SpecificationCategory, list[SpecificationDefinition]]]:
    """All active specifications, grouped for the dictionary screen."""
    definitions = SpecificationDefinition.objects.select_related("category").order_by(
        "category__display_order", "display_order", "code"
    )
    grouped: dict[Any, list[SpecificationDefinition]] = {}
    for definition in definitions:
        grouped.setdefault(definition.category, []).append(definition)
    return list(grouped.items())


def incomplete_definitions() -> list[SpecificationDefinition]:
    """Definitions still awaiting engineering input.

    Specification section 26.20: an unresolved rule must stay visible as an explicit
    `OPEN QUESTION` rather than being quietly filled in with a guess.
    """
    return [d for d in SpecificationDefinition.objects.all() if d.needs_engineering_input]
