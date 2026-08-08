"""How the read paths behave as the plan grows. §21, **OQ-15**.

**These assert query counts and algorithmic shape, never wall-clock time.** A timing assertion
in CI fails on a busy runner and passes on a fast one, so it gets a wider bound, and then a
wider one, until it asserts nothing. What actually degrades a Django read path at scale is a
query per row, and that is a thing a test can pin exactly.

**OQ-15 has not been answered**, so the volumes here are the ones the specification's own text
implies (≤10⁵ Satnet Paths) scaled down to what a test suite can build in a second. The property
being held is the *slope*: the same number of queries at 3 rows and at 60. A path that is flat
at sixty is flat at sixty thousand; one that is not was going to fail whatever number was
chosen.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from satnet_paths.constants import PathStatus
from spectrum import selectors as spectrum_selectors

pytestmark = pytest.mark.django_db

MHZ = 1_000_000

#: The `lifecycle_world` entitlement is 0 to 100 MHz and an allocation here is 10 MHz wide, so an
#: **on-air** placement has to fit inside it — `ck_res_within_assignment` refuses anything that
#: does not, and rightly. Drafts hold no spectrum (**A-12**) and are placed anywhere.
ON_AIR_CENTRES = tuple(8 * MHZ + index * 11 * MHZ for index in range(8))


def queries_for(callable_) -> int:
    with CaptureQueriesContext(connection) as captured:
        callable_()
    return len(captured)


@pytest.fixture
def many_paths(lifecycle_world, make_path):
    """Enough allocations to show a slope, placed so none of them overlaps."""

    def build(count: int, *, start: int = 1):
        for index in range(start, start + count):
            # 1 MHz apart with a 10 MHz occupied width would overlap, so they are spaced by
            # more than the allocation is wide. The point is the count, not the packing.
            make_path(PathStatus.DRAFT, code=f"SC-{index}", centre=index * 12 * MHZ)

    return build


# ---------------------------------------------------------------------------
# The gap engine
# ---------------------------------------------------------------------------
def test_the_gap_engine_issues_the_same_queries_however_many_allocations_exist(
    lifecycle_world, make_path
):
    """ADR-0009: free capacity is computed, never stored — so this runs on every page that
    shows it, and a query per reservation would be felt immediately."""
    config = lifecycle_world["setup"].config
    leg = lifecycle_world["setup"].resource.leg

    for index, centre in enumerate(ON_AIR_CENTRES[:3]):
        make_path(PathStatus.ON_AIR, code=f"GAP-{index}", centre=centre)
    baseline = queries_for(lambda: spectrum_selectors.capacity(config, leg=leg))

    for index, centre in enumerate(ON_AIR_CENTRES[3:], start=3):
        make_path(PathStatus.ON_AIR, code=f"GAP-{index}", centre=centre)
    grown = queries_for(lambda: spectrum_selectors.capacity(config, leg=leg))

    assert grown == baseline, (
        f"The gap engine issued {grown} queries for {len(ON_AIR_CENTRES)} allocations and "
        f"{baseline} for 3. "
        f"That is a query per row, and it is the shape that fails at OQ-15's volumes."
    )


def test_the_gap_engine_returns_gaps_that_tile_the_assignment(lifecycle_world, make_path):
    """A correctness check at scale, not a performance one: with allocations placed across
    the assignment, no reported gap may overlap an allocation."""
    setup = lifecycle_world["setup"]
    for index, centre in enumerate(ON_AIR_CENTRES[:5]):
        make_path(PathStatus.ON_AIR, code=f"TILE-{index}", centre=centre)

    summary = spectrum_selectors.capacity(setup.config, leg=setup.resource.leg)
    reserved = [
        (row.allocated_start_hz, row.allocated_end_hz)
        for row in spectrum_selectors.reservations_on([str(setup.resource.pk)])
    ]

    for gap in summary.gaps:
        for start, end in reserved:
            assert gap.range.end_hz <= start or gap.range.start_hz >= end, (
                f"Gap {gap.range.start_hz}-{gap.range.end_hz} overlaps a reservation at "
                f"{start}-{end}. A gap engine that reports occupied spectrum as free is worse "
                f"than one that reports nothing."
            )


# ---------------------------------------------------------------------------
# The screens
# ---------------------------------------------------------------------------
def test_the_satnet_path_table_does_not_query_per_row(lifecycle_world, many_paths, client):
    """§10.3's table is the page an operator lives on, and every column that reaches through
    a relation is one N+1 away from a query per row."""
    client.force_login(lifecycle_world["admin"])
    url = reverse("reporting:satnet-paths")

    many_paths(3)
    baseline = queries_for(lambda: client.get(url))

    many_paths(20, start=100)
    grown = queries_for(lambda: client.get(url))

    assert grown <= baseline + 2, (
        f"{baseline} queries for 3 rows and {grown} for 23. The table is querying per row; "
        f"`reporting.selectors.ROW_RELATIONS` is where that is fixed."
    )


def test_the_spectrum_view_does_not_query_per_reservation(lifecycle_world, make_path, client):
    client.force_login(lifecycle_world["admin"])
    url = reverse("spectrum:beam", kwargs={"pk": lifecycle_world["setup"].beam.pk})

    for index, centre in enumerate(ON_AIR_CENTRES[:3]):
        make_path(PathStatus.ON_AIR, code=f"SV-{index}", centre=centre)
    baseline = queries_for(lambda: client.get(url))

    for index, centre in enumerate(ON_AIR_CENTRES[3:], start=3):
        make_path(PathStatus.ON_AIR, code=f"SV-{index}", centre=centre)
    grown = queries_for(lambda: client.get(url))

    assert grown <= baseline + 2, (
        f"{baseline} queries for 3 reservations and {grown} for {len(ON_AIR_CENTRES)}."
    )


def test_the_dashboard_is_flat_in_the_number_of_allocations(lifecycle_world, many_paths, client):
    """§16's figures are computed on every load (ADR-0009), which is the right call and the
    one that makes this test necessary."""
    client.force_login(lifecycle_world["admin"])
    url = reverse("home")

    many_paths(3)
    baseline = queries_for(lambda: client.get(url))

    many_paths(20, start=200)
    grown = queries_for(lambda: client.get(url))

    assert grown <= baseline + 2, f"{baseline} queries for 3 rows and {grown} for 23."


def test_the_audit_search_is_flat_in_the_number_of_events(lifecycle_world, many_paths, client):
    """The largest table in the product, and the one that grows monotonically (§20)."""
    client.force_login(lifecycle_world["admin"])
    url = reverse("audit:search")

    many_paths(3)
    baseline = queries_for(lambda: client.get(url))

    many_paths(20, start=300)
    grown = queries_for(lambda: client.get(url))

    assert grown <= baseline + 2, (
        f"{baseline} queries for a short trail and {grown} for a longer one."
    )


# ---------------------------------------------------------------------------
# The export
# ---------------------------------------------------------------------------
def test_the_export_is_flat_in_the_number_of_rows(lifecycle_world, many_paths):
    """§17.2. The export reuses the table's selector, so this holds for the same reason the
    table's does — and asserting it here is what stops the two drifting apart."""
    from imports_exports import services as export_services

    actor = lifecycle_world["admin"]
    many_paths(3)
    baseline = queries_for(lambda: export_services.export_satnet_paths(actor=actor))

    many_paths(20, start=400)
    grown = queries_for(lambda: export_services.export_satnet_paths(actor=actor))

    assert grown <= baseline + 2, f"{baseline} queries for 3 rows and {grown} for 23."
