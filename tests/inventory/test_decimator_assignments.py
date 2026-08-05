"""The Decimator is allocatable; its Assignment is what holds it. **OQ-10**, **A-27**, ADR-0021.

The answer's sentence is one constraint:

    *"The same Decimator must not have two different active configurations during overlapping
    periods."*

Everything here is about the boundary of that sentence — what it forbids, and the three things
it does **not** forbid, each of which is easy to over-enforce by accident:

* two configurations that merely *touch* in time;
* a retired configuration overlapping a live one;
* several Satnet Paths consuming one configuration, which the answer explicitly permits.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import DataError, IntegrityError, transaction
from django.utils import timezone

from inventory.models import DecimatorAssignment
from tests.inventory.factories import make_decimator, make_decimator_assignment

pytestmark = pytest.mark.django_db

NOW = timezone.now()
DAY = timedelta(days=1)


def test_one_decimator_cannot_hold_two_overlapping_configurations():
    """The OQ-10 answer, as a database refusal rather than a service rule."""
    decimator = make_decimator()
    make_decimator_assignment(decimator, effective_from=NOW, effective_until=NOW + 10 * DAY)

    with pytest.raises(IntegrityError, match="excl_decimator_assignment_overlap"):
        with transaction.atomic():
            make_decimator_assignment(
                decimator,
                input_connection="IN-2",
                effective_from=NOW + 5 * DAY,
                effective_until=NOW + 15 * DAY,
            )


def test_configurations_that_merely_touch_are_accepted():
    """**A-10**: the period is half-open, so a configuration that ends exactly when the next
    begins is a clean handover and not a double-booking."""
    decimator = make_decimator()
    make_decimator_assignment(decimator, effective_from=NOW, effective_until=NOW + 10 * DAY)

    make_decimator_assignment(
        decimator,
        input_connection="IN-2",
        effective_from=NOW + 10 * DAY,
        effective_until=NOW + 20 * DAY,
    )

    assert DecimatorAssignment.objects.filter(decimator=decimator).count() == 2


def test_a_retired_configuration_may_overlap_a_live_one():
    """History is allowed to overlap the present.

    The constraint is conditional on ``is_active`` for the same reason the window-version one
    is: a configuration that was withdrawn is a record of what used to be true, and refusing to
    keep it would push the platform towards deleting history to make room.
    """
    decimator = make_decimator()
    make_decimator_assignment(
        decimator, effective_from=NOW, effective_until=NOW + 10 * DAY, is_active=False
    )

    make_decimator_assignment(decimator, input_connection="IN-2", effective_from=NOW + 5 * DAY)

    assert DecimatorAssignment.objects.filter(decimator=decimator).count() == 2


def test_two_decimators_may_be_configured_over_the_same_period():
    """The constraint keys on the box, not on the frequency. Two decimators processing the
    same input range at the same time is a normal redundant installation."""
    first, second = make_decimator(code="DEC-A"), make_decimator(code="DEC-B")

    make_decimator_assignment(first, effective_from=NOW)
    make_decimator_assignment(second, effective_from=NOW)

    assert DecimatorAssignment.objects.count() == 2


def test_an_open_ended_configuration_blocks_everything_after_it():
    """An open-ended period runs to infinity, so the next configuration has to close it first —
    which is the point: an unbounded row that could be silently shadowed would make "what is
    this Decimator doing today" have two answers."""
    decimator = make_decimator()
    make_decimator_assignment(decimator, effective_from=NOW)

    with pytest.raises(IntegrityError, match="excl_decimator_assignment_overlap"):
        with transaction.atomic():
            make_decimator_assignment(
                decimator, input_connection="IN-2", effective_from=NOW + 100 * DAY
            )


def test_a_configuration_must_start_below_its_end():
    """Refused by ``tstzrange`` itself, before ``ck_decimator_assignment_period`` is reached.

    Worth stating rather than papering over: on a table with a *generated* range column the
    range function evaluates first, so an inverted period comes back as a ``DataError`` about
    range bounds and never as the named CHECK. The CHECK stays because it is what says the rule
    out loud, and because the column could be dropped without the rule going with it — but a
    test asserting the constraint name here would be asserting something PostgreSQL never
    reaches.
    """
    with pytest.raises(DataError, match="range lower bound"):
        with transaction.atomic():
            make_decimator_assignment(effective_from=NOW, effective_until=NOW - DAY)


def test_the_processed_range_is_half_open_and_ordered():
    """``int8range``, same story as the period above."""
    with pytest.raises(DataError, match="range lower bound"):
        with transaction.atomic():
            make_decimator_assignment(
                processed_start_hz=1_450_000_000, processed_end_hz=950_000_000
            )


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("channel_bandwidth_hz", 0, "ck_decimator_assignment_bandwidth"),
        ("decimation_factor", 0, "ck_decimator_assignment_factor"),
    ],
)
def test_a_stated_parameter_must_be_positive(field, value, constraint):
    """Nullable means "not stated"; zero means "stated, and wrong"."""
    with pytest.raises(IntegrityError, match=constraint):
        with transaction.atomic():
            make_decimator_assignment(**{field: value})


def test_neither_parameter_has_to_be_stated():
    """Deliberately *not* constrained. Which parameterisation a platform uses — a channel
    bandwidth, a decimation factor, or something the answer did not name — is a fact about the
    equipment, and refusing a row that carries neither would be a rule nobody confirmed."""
    assignment = make_decimator_assignment()

    assert assignment.channel_bandwidth_hz is None
    assert assignment.decimation_factor is None


def test_the_table_ships_empty():
    """§26.20. Which decimators exist and how they are configured is site data."""
    assert DecimatorAssignment.objects.count() == 0
