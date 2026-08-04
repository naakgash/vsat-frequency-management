"""Half-open time periods and their intersection. **OQ-32**, ADR-0020.

Pure and Django-free. The open-ended cases are the reason this is a module rather than three
inline comparisons: an absent upper bound means *infinity*, and every wrong way to handle it
fails quietly — treating ``None`` as zero forbids everything, treating it as a far-future date
invents an expiry nobody agreed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from calculations.periods import TimePeriod, intersect

JAN = datetime(2026, 1, 1, tzinfo=UTC)


def at(days: int) -> datetime:
    return JAN + timedelta(days=days)


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------
def test_a_period_must_run_forwards():
    with pytest.raises(ValueError, match="must run forwards"):
        TimePeriod(at(10), at(5))


def test_a_zero_width_period_is_refused():
    """Half-open, so ``[x, x)`` contains no instant. Allowing it would let a Path be created
    with a validity nothing could ever fall inside."""
    with pytest.raises(ValueError):
        TimePeriod(at(5), at(5))


def test_an_absent_end_is_open_ended():
    assert TimePeriod(JAN).is_open_ended is True
    assert TimePeriod(JAN, at(10)).is_open_ended is False


# ---------------------------------------------------------------------------
# contains
# ---------------------------------------------------------------------------
def test_a_period_contains_itself():
    period = TimePeriod(JAN, at(10))

    assert period.contains(period)


def test_containment_is_half_open_at_the_upper_edge():
    """**A-10**. A child ending exactly when its parent does is contained; one ending a moment
    later is not."""
    parent = TimePeriod(JAN, at(10))

    assert parent.contains(TimePeriod(JAN, at(10))) is True
    assert parent.contains(TimePeriod(JAN, at(10) + timedelta(seconds=1))) is False


def test_an_open_ended_parent_contains_anything_starting_within_it():
    parent = TimePeriod(JAN)

    assert parent.contains(TimePeriod(at(100), at(200))) is True
    assert parent.contains(TimePeriod(at(100))) is True


def test_a_bounded_parent_does_not_contain_an_open_ended_child():
    """The asymmetry that decides the common case.

    Somebody leaves a Satnet Path's end date blank, meaning "until further notice", under a
    Beam that expires in March. That is refused — and it has to be, or the allocation outlives
    the Beam it depends on by simply not saying when it stops.
    """
    parent = TimePeriod(JAN, at(90))

    assert parent.contains(TimePeriod(at(10))) is False


def test_a_child_starting_before_its_parent_is_not_contained():
    parent = TimePeriod(at(10), at(90))

    assert parent.contains(TimePeriod(at(5), at(50))) is False


# ---------------------------------------------------------------------------
# intersect
# ---------------------------------------------------------------------------
def test_the_intersection_is_the_latest_start_and_the_earliest_end():
    """**OQ-32**: the maximum permitted period is the intersection of the three."""
    result = intersect(
        [TimePeriod(JAN, at(100)), TimePeriod(at(10), at(90)), TimePeriod(at(5), at(120))]
    )

    assert result == TimePeriod(at(10), at(90))


def test_an_open_ended_period_does_not_bound_the_end():
    result = intersect([TimePeriod(JAN), TimePeriod(at(10), at(90))])

    assert result == TimePeriod(at(10), at(90))


def test_all_open_ended_gives_an_open_ended_intersection():
    result = intersect([TimePeriod(JAN), TimePeriod(at(10))])

    assert result == TimePeriod(at(10))
    assert result.is_open_ended


def test_periods_that_never_overlap_intersect_to_none():
    """``None`` rather than an empty period, deliberately.

    "There is no moment when all three are valid" and "the window is zero wide" are different
    statements, and only the first is true when a Beam expires before its Satnet begins. A
    caller that got an empty period back would offer it as a maximum.
    """
    assert intersect([TimePeriod(JAN, at(10)), TimePeriod(at(20), at(30))]) is None


def test_periods_that_merely_touch_intersect_to_none():
    """Half-open again: ``[…, 10)`` and ``[10, …)`` share no instant."""
    assert intersect([TimePeriod(JAN, at(10)), TimePeriod(at(10), at(30))]) is None


def test_intersecting_nothing_is_an_error_rather_than_an_unbounded_default():
    """An empty input has no constraints to satisfy and therefore no answer. Returning
    "unbounded" would silently permit everything the first time a caller passed a list it
    thought was full."""
    with pytest.raises(ValueError, match="at least one"):
        intersect([])


# ---------------------------------------------------------------------------
# Properties
# ---------------------------------------------------------------------------
DAYS = st.integers(min_value=0, max_value=3650)


def periods() -> st.SearchStrategy[TimePeriod]:
    return st.tuples(DAYS, st.one_of(st.none(), DAYS)).map(
        lambda pair: TimePeriod(at(pair[0]), None if pair[1] is None else at(pair[0] + pair[1] + 1))
    )


@given(members=st.lists(periods(), min_size=1, max_size=4))
def test_the_intersection_is_contained_by_every_member(members):
    """The property that matters: the maximum permitted period must actually be permitted by
    all three parents, or the platform offers a period one of them will refuse."""
    result = intersect(members)
    assume(result is not None)

    for member in members:
        assert member.contains(result)


@given(members=st.lists(periods(), min_size=1, max_size=4))
def test_intersecting_is_order_independent(members):
    forwards = intersect(members)
    backwards = intersect(list(reversed(members)))

    assert forwards == backwards


@given(member=periods())
def test_intersecting_one_period_returns_it(member):
    assert intersect([member]) == member


@given(members=st.lists(periods(), min_size=2, max_size=4))
def test_intersecting_twice_changes_nothing(members):
    once = intersect(members)
    assume(once is not None)

    assert intersect([once, *members]) == once
