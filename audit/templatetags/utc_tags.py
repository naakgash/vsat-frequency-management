"""Rendering a timestamp so that nobody has to guess which zone it is in. **A-28**, ADR-0022.

**In ``audit`` for the layering reason, not a conceptual one, and it has moved once before.**
Display filters are loaded by name from templates but *imported* by other Python — S13's table
cells call this one — so the filter has to live in the lowest module that owns templates, or
some module above it ends up depending on one beside it. S13 put it in ``inventory`` because
that was the lowest at the time; S16 gives ``audit`` its own screens, and ``audit`` is the
bottom of the graph and may import nothing. Moving the filter down is what lets the audit
templates render a timestamp without the trail depending on the inventory.

There is exactly one definition of this rule, which is the point (§2, ADR-0011). A second copy
in ``audit`` would be a second answer to "how does this platform print a time", and the two
would part company the first time one of them was improved.

The **OQ-23** answer makes UTC the authoritative operational *and* display time zone:

    *"All persisted timestamps, validity checks, overlap calculations, API values and audit
    records shall use UTC. Operational screens shall display UTC explicitly."*

"Explicitly" is the part that needs code. Django renders a timestamp in the *active* zone and
prints no zone name, so a screen that shows ``2026-08-05 07:15`` is telling an operator in
Istanbul something different from what it is telling one in Denver, and neither of them is told
which. ``{{ path.valid_from|utc }}`` renders ``2026-08-05 07:15 UTC``, converting from whatever
the value carries rather than trusting it to already be right.

Deliberately a **display filter and not a request-level zone**. Offering a local zone later
means changing what this renders alongside the UTC value; it must never mean calling
``timezone.activate()``, because that would reach the forms that parse an operator's input and
the answer is explicit that a secondary zone *"shall not affect validation or stored values"*.
"""

from __future__ import annotations

import datetime
from typing import Any

from django import template
from django.template.defaultfilters import date as date_filter

register = template.Library()

#: Em dash, matching ``inventory.templatetags.rf`` and the specification tags. Restated rather
#: than imported: this module may import nothing (`docs/design/01` §1).
EMPTY = "—"

#: Minute resolution. Seconds are noise on a screen about validity periods, and the platform
#: has no allocation whose meaning turns on them.
DEFAULT_FORMAT = "Y-m-d H:i"


@register.filter
def utc(value: Any, fmt: str = DEFAULT_FORMAT) -> str:
    """Render a datetime in UTC, with the zone named on the face of the value.

    A naive datetime is treated as already UTC rather than guessed at: ``USE_TZ`` is on, so
    the only way one reaches a template is from code that built it without a zone, and
    attaching the platform's own zone is the reading that cannot silently shift the value.
    """
    if value in (None, ""):
        return EMPTY
    if not isinstance(value, datetime.datetime):
        return str(value)

    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.UTC)
    return f"{date_filter(value.astimezone(datetime.UTC), fmt)} UTC"
