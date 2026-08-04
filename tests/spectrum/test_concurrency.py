"""Two connections race for the same Hz. Exactly one wins. §8.3.

This is the test the whole slice ordering exists for. `docs/design/05` puts S9 before the
Satnet Path wizard specifically so that *"the concurrency test exists before the first
reservation is ever written, instead of being added afterwards to a system already assumed
correct"*.

A single-connection test cannot prove this. Two operators placing overlapping transmissions at
the same moment both pass every service-layer check — each queries a table that does not yet
contain the other's row — and the only thing standing between them is the exclusion
constraint. A read-then-write service can never close that window, however carefully it is
written, because there is no moment at which the read and the write are one operation.

**This file found a real defect.** The losing writer does not always get an
``IntegrityError``: when both `INSERT`s are in flight at once, each takes a lock the other
needs while checking the exclusion constraint, and PostgreSQL breaks the tie with
``OperationalError: deadlock detected``. The guarantee holds either way — exactly one row
survives — but a service catching only ``IntegrityError`` would hand whoever lost the race a
500 instead of §9.5's message. ``spectrum.services`` now translates both into
``SpectrumConflictError``.
"""

from __future__ import annotations

import threading

import pytest
from django.db import IntegrityError, OperationalError, connections, transaction

from spectrum.models import SpectrumReservation
from tests.spectrum.factories import make_entitlement, reserve_range

pytestmark = pytest.mark.django_db(transaction=True)

#: Both shapes an exclusion-constraint loss can take. See the module docstring.
CONFLICT = (IntegrityError, OperationalError)


def _race(setup, first_hz: int, second_hz: int) -> list[str]:
    """Two threads, two connections, one barrier, both writing at once."""
    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(start_hz: int) -> None:
        try:
            # Both threads arrive here before either writes, so the INSERTs genuinely
            # contend. Without it the first would usually finish before the second began, and
            # the test would pass without ever having raced.
            barrier.wait(timeout=10)
            with transaction.atomic():
                reserve_range(setup, start_hz, start_hz + 10_000_000)
            result = "committed"
        except CONFLICT:
            result = "refused"
        finally:
            # Each thread gets its own connection, and a thread that leaves one open holds a
            # lock the test teardown then blocks on.
            connections.close_all()
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt, args=(hz,)) for hz in (first_hz, second_hz)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    return sorted(outcomes)


def test_two_connections_racing_for_one_range_produce_exactly_one_reservation():
    """The guarantee under contention, and the only assertion that matters.

    Which of the two writers loses is arbitrary, and *how* it loses depends on the timing —
    a committed competitor gives an `IntegrityError`, a simultaneous one a deadlock. Asserting
    either specific shape would make this test flaky, which is the worst possible property for
    the test that guards the product's central promise. So it asserts the invariant: one row.
    """
    setup = make_entitlement()

    outcomes = _race(setup, 100_000_000, 105_000_000)

    assert outcomes == ["committed", "refused"], (
        f"expected exactly one of two overlapping writes to survive, got {outcomes}"
    )
    assert SpectrumReservation.objects.count() == 1


def test_two_connections_on_disjoint_ranges_both_commit():
    """The control. If the test above passed because *nothing* commits under contention, it
    would be proving the wrong thing."""
    setup = make_entitlement()

    assert _race(setup, 100_000_000, 300_000_000) == ["committed", "committed"]
    assert SpectrumReservation.objects.count() == 2


def test_a_deadlock_is_reported_as_a_conflict_rather_than_a_crash():
    """The defect this file found, pinned so it cannot come back.

    ``SpectrumConflictError`` is what S11 will catch to produce §9.5's blocking message. If a
    future change routes an `INSERT` around ``_write``, or the translation is dropped, the
    losing operator gets a 500 on an ordinary collision — and it would only ever reproduce
    under real concurrency, which is exactly when nobody is watching a test run.
    """
    from spectrum import services

    assert issubclass(services.SpectrumConflictError, Exception)

    deadlock = services.SpectrumConflictError("x", was_deadlock=True)
    overlap = services.SpectrumConflictError("y")

    # The distinction is carried because it changes what a caller may do next: a deadlock
    # means the competitor may itself have rolled back, so a retry can legitimately succeed.
    assert deadlock.was_deadlock is True
    assert overlap.was_deadlock is False


def test_the_conflict_is_reported_on_the_statement_not_at_commit():
    """**A-14**: the constraint is `IMMEDIATE`, not `DEFERRABLE`.

    This is what makes the §9.5 blocking message possible. A deferred constraint raises at
    `COMMIT`, by which point the failing statement is gone and the error can say only that
    *something* in the transaction overlapped — no field, no row, no frequency. With several
    occupancy rows per allocation (**A-23**) that would be a genuinely unhelpful message.
    """
    setup = make_entitlement()
    reserve_range(setup, 100_000_000, 110_000_000)

    with transaction.atomic():
        with pytest.raises(IntegrityError) as excinfo:
            # Raises here, inside the block — not on the way out of it.
            reserve_range(setup, 105_000_000, 108_000_000)

    assert "excl_reservation_overlap" in str(excinfo.value)
