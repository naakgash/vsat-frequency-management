"""Reading a spreadsheet into allocations. §17.1, ADR-0015.

Named ``importer`` rather than ``import``: the plan's filename is a Python keyword, and
``from imports_exports.import import parse`` is a syntax error.

Five stages, in order, each one a module:

``parse``
    Bytes to rows of cells, safely. Nothing is evaluated and nothing is trusted.
``normalize``
    Cells to the platform's own types and units, or to a reason why not.
``mapping``
    Spreadsheet labels to inventory records, remembering answers between runs.
``classify``
    One of the seven outcomes per row, most blocking first.
``commit``
    The committable rows, through the same service the wizard uses.

The dividing line that matters runs through all five: **a value calculated by Excel is never
used.** The importer reads the operator's inputs and recomputes everything else through
`satnet_paths.services`, so an imported allocation and a typed one are the same record produced
the same way. Where the file carries a derived value, it is *compared* and disagreements are
reported — which is the opposite of trusting it.
"""
