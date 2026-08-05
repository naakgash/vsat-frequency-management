"""Making a spreadsheet cell inert. §21.12.

A code field an operator typed is data. Excel disagrees: a cell whose text begins with ``=``,
``+``, ``-`` or ``@`` is a *formula*, and formulas can reach the shell through DDE. So a Satnet
Path called ``=cmd|'/c calc'!A1`` is a working attack on whoever opens the export — and the
platform, not the reader, is what has to stop it.

**The value is neutralised, not the display.** openpyxl can set ``quotePrefix`` on a cell, which
tells Excel to treat the content as text while leaving the stored string starting with ``=``.
That is prettier and it is not enough: the moment the workbook is saved as CSV, pasted into
another tool, or read by anything that does not honour the flag, the formula is back. Prefixing
the value with an apostrophe survives every one of those, at the cost of one visible character
on the handful of cells that were dangerous — which is a reasonable thing for a cell to say
about itself.

**Numbers never come through here.** A frequency, a bandwidth and a count are written as numeric
cells, so ``-5`` stays ``-5``. Only strings are inspected, which is why a negative number is not
mangled into text.
"""

from __future__ import annotations

from typing import Any

#: The characters Excel and LibreOffice treat as the start of a formula. The two control
#: characters are here because both applications strip leading whitespace before deciding, so a
#: tab followed by ``=`` is still a formula.
FORMULA_STARTS = ("=", "+", "-", "@", "\t", "\r")

#: What is put in front of a dangerous value. An apostrophe is the mitigation OWASP names, and
#: the only one that survives a CSV round trip.
GUARD = "'"


def neutralise(value: Any) -> Any:
    """Return a value that no spreadsheet will evaluate.

    Non-strings are returned unchanged: they are written as typed cells and cannot be read as
    formulas. Strings that do not begin with a formula character are returned unchanged too, so
    the overwhelming majority of cells are untouched and the export stays readable.
    """
    if not isinstance(value, str):
        return value
    if value.startswith(FORMULA_STARTS):
        return f"{GUARD}{value}"
    return value


def is_dangerous(value: Any) -> bool:
    """Would this value be evaluated if it were written as-is?

    Exposed so a test can assert the *property* — no cell in a produced workbook is dangerous —
    rather than re-listing the characters and drifting from the guard it is checking.
    """
    return isinstance(value, str) and value.startswith(FORMULA_STARTS)
