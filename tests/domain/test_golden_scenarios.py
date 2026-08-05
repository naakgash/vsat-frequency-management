"""A golden example run against the platform, not only against the arithmetic. **OQ-22**.

``test_golden_examples.py`` proves the engine computes the bandwidths, edges and IF that RF
engineering says it should. That is half of what the OQ-22 answer asks for. The other half is
about *reuse*:

    *"It shall also include the expected rejection of an overlapping allocation made through
    another Hub, Beam or redundant ground site when both allocations use the same payload
    input and polarization. An allocation on an independently implemented polarization may be
    accepted. An allocation outside the Beam Spectrum Assignment or its validity period must
    be rejected."*

None of that is arithmetic. It is the **A-21** reuse model, and the only way to check it is to
build the master data the example describes and drive the real services — so this harness does
exactly that, from the same JSON file.

**Four scenario kinds**, matching the answer sentence for sentence:

======================================  ===========================================
``REJECT_SHARED_PAYLOAD_INPUT``         A second Satnet under a **different Beam, a
                                        different Hub and a different Gateway**, whose
                                        legs map to the *same* Spectrum Resources.
                                        Everything about it is separate except the
                                        payload input — which is the only thing that
                                        matters (ADR-0018).
``ACCEPT_INDEPENDENT_POLARIZATION``     A Beam on the orthogonal polarization with its
                                        own windows and its own resources, i.e. RF
                                        chains *"independently implemented"*.
``REJECT_OUTSIDE_ASSIGNMENT``           Inside the Payload Path Window, outside the Beam
                                        Spectrum Assignment (**A-24**).
``REJECT_OUTSIDE_VALIDITY``             Inside the assignment in frequency, outside it in
                                        time (**A-25**).
======================================  ===========================================

**No RF value is invented here.** Every frequency, period and polarization comes from the
file. What this module supplies is *structure* — two Gateways, three Beams, the resource
mapping that makes them share or not share — because structure is what the platform is being
asked to get right. The one exception is labelled and self-contained: a scaffolding example at
the bottom, which exists to prove the harness itself runs and asserts nothing about RF.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from beams import services as beam_services
from beams.models import (
    Beam,
    BeamDirectionConfig,
    BeamDirectionEquipmentProfile,
    BeamDirectionSpectrumResource,
    BeamSpectrumAssignment,
)
from inventory.constants import Direction, GuardMode, SpectrumLeg, SpectrumResourceKind
from inventory.models import (
    EquipmentProfile,
    GuardPolicy,
    PayloadPath,
    PayloadPolarizationMapping,
    SpectrumResource,
)
from satnet_paths import services as path_services
from satnet_paths.constants import InputMode, PathStatus
from satnets import services as satnet_services
from satnets.models import Satnet
from spectrum import selectors as spectrum_selectors
from tests.beams.factories import configure_direction, make_beam
from tests.domain import test_golden_examples as arithmetic
from tests.factories import make_admin
from tests.inventory.factories import (
    make_band,
    make_equipment_profile,
    make_frequency_window,
    make_gateway,
    make_hub,
    make_satellite,
)

pytestmark = pytest.mark.django_db

#: How far before the stated Beam Spectrum Assignment the Beam and Satnet are commissioned.
#: Structure, not an RF value: **A-25** requires a Path to sit inside all three periods, and a
#: Beam beginning at the same instant as its assignment would make every containment refusal
#: ambiguous about which parent did the refusing. A day is enough to make the assignment the
#: limiting parent, which is what a REJECT_OUTSIDE_VALIDITY scenario is about.
COMMISSIONED_BEFORE = timedelta(days=1)


@dataclasses.dataclass
class World:
    """The master data one golden example describes, plus the structure around it."""

    example: dict[str, Any]
    admin: Any
    config: BeamDirectionConfig
    satnet: Satnet
    #: Different Beam, different Hub, different Gateway — same Spectrum Resources.
    shared_input_satnet: Satnet
    #: Built only when a scenario needs it; the file has to state the polarization's windows.
    independent_satnet: Satnet | None

    @property
    def canonical_leg(self) -> str:
        return self.config.canonical_leg or self.config.payload_path.uplink_window_side


# ---------------------------------------------------------------------------
# The tests
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", arithmetic.example_files(), ids=lambda p: p.stem)
def test_a_golden_example_holds_up_in_the_platform(path: Path):
    """Build the example's world, allocate what it asks for, and check what it expects."""
    example = arithmetic.load(path)
    _require_periods_covering_the_present(example, path)

    world = build(example)
    _create_requested_allocation(world)
    _assert_free_capacity(world, path)

    for index, scenario in enumerate(example["expect"].get("scenarios", [])):
        _run_scenario(world, scenario, path, index)


def test_the_harness_runs_on_a_scaffolding_example():
    """Proof that this file works, kept honest about what it is.

    A harness that has never executed is the worst kind: it looks like coverage and would fail
    for its own reasons on the day a real example finally arrives — by which point nobody can
    tell whether the file or the harness is wrong. So the shapes are exercised here.

    ``SCAFFOLDING`` is **not** a golden example and is not in ``golden/``. Its numbers are
    arbitrary and chosen to be checkable by hand; nothing asserted below is an RF claim. What
    is asserted is that the *rules* hold — a shared payload input collides, an independent
    polarization does not — which the platform already guarantees elsewhere and which this
    proves the harness can actually observe.
    """
    example = SCAFFOLDING()
    world = build(example)
    _create_requested_allocation(world)
    _assert_free_capacity(world, Path("scaffolding"))

    for index, scenario in enumerate(example["expect"]["scenarios"]):
        _run_scenario(world, scenario, Path("scaffolding"), index)


# ---------------------------------------------------------------------------
# Building the world the example describes
# ---------------------------------------------------------------------------
def build(example: dict[str, Any]) -> World:
    admin = make_admin()
    windows = example["payload_path_window"]
    assignment = example["beam_spectrum_assignment"]

    master_from = _at(assignment["effective_from"]) - COMMISSIONED_BEFORE
    satellite = make_satellite("GOLD-SAT", effective_from=master_from)
    band = make_band(
        "GOLD-BAND",
        rf_min_hz=min(windows["uplink"][0], windows["downlink"][0]),
        rf_max_hz=max(windows["uplink"][1], windows["downlink"][1]),
    )

    payload_path = _payload_path(
        example,
        satellite=satellite,
        band=band,
        code="GOLD-PP",
        uplink=windows["uplink"],
        downlink=windows["downlink"],
        uplink_polarization=windows["uplink_polarization"],
        downlink_polarization=windows["downlink_polarization"],
        effective_from=master_from,
    )
    profile = _equipment_profile(example, band=band, code="GOLD-EQ", effective_from=master_from)

    resources = [
        SpectrumResource.objects.create(
            satellite=satellite,
            code=f"GOLD-SR-{leg}",
            name=f"Shared payload input, {leg}",
            kind=SpectrumResourceKind.PAYLOAD_INPUT,
            leg=leg,
            effective_from=master_from,
        )
        for leg in (payload_path.uplink_window_side, payload_path.downlink_window_side)
    ]

    config = _beam(
        example,
        code="GOLD-BEAM",
        payload_path=payload_path,
        profile=profile,
        resources=resources,
        band=band,
        satellite=satellite,
        admin=admin,
        assignment=assignment,
        master_from=master_from,
    )
    # A different Beam, at a different Hub, at a different Gateway — sharing the payload
    # input. Every axis the superseded A-01 would have separated on is separated here, so a
    # collision can only come from the resource (**A-21**).
    other = _beam(
        example,
        code="GOLD-BEAM-2",
        payload_path=payload_path,
        profile=profile,
        resources=resources,
        band=band,
        satellite=satellite,
        admin=admin,
        assignment=assignment,
        master_from=master_from,
    )

    satnet = _satnet(admin, config.beam, "GOLD", master_from)
    shared = _satnet(admin, other.beam, "GOLD-2", master_from)
    independent = None
    if _needs_independent_polarization(example):
        independent = _satnet(
            admin,
            _independent_beam(example, satellite, band, profile, admin, master_from).beam,
            "GOLD-3",
            master_from,
        )

    return World(
        example=example,
        admin=admin,
        config=config,
        satnet=satnet,
        shared_input_satnet=shared,
        independent_satnet=independent,
    )


def _payload_path(
    example: dict[str, Any],
    *,
    satellite: Any,
    band: Any,
    code: str,
    uplink: list[int],
    downlink: list[int],
    uplink_polarization: str,
    downlink_polarization: str,
    effective_from: datetime,
) -> PayloadPath:
    direction = example["direction"]
    up_side, down_side = (
        (SpectrumLeg.HUB_UPLINK, SpectrumLeg.REMOTE_DOWNLINK)
        if direction == Direction.FWD
        else (SpectrumLeg.REMOTE_UPLINK, SpectrumLeg.HUB_DOWNLINK)
    )
    translation = example["translation"]
    path = PayloadPath.objects.create(
        code=code,
        name=f"Payload path {code}",
        satellite=satellite,
        direction=direction,
        uplink_window=make_frequency_window(
            satellite,
            f"{code}-UL",
            up_side,
            band=band,
            polarization=uplink_polarization,
            rf_start_hz=uplink[0],
            rf_end_hz=uplink[1],
            effective_from=effective_from,
        ),
        downlink_window=make_frequency_window(
            satellite,
            f"{code}-DL",
            down_side,
            band=band,
            polarization=downlink_polarization,
            rf_start_hz=downlink[0],
            rf_end_hz=downlink[1],
            effective_from=effective_from,
        ),
        uplink_window_side=up_side,
        downlink_window_side=down_side,
        translation_method=translation["method"],
        translation_constant_hz=translation["constant_hz"],
        spectral_inversion=translation.get("spectral_inversion", False),
        effective_from=effective_from,
    )
    PayloadPolarizationMapping.objects.create(
        payload_path=path,
        uplink_polarization=uplink_polarization,
        downlink_polarization=downlink_polarization,
    )
    return path


def _equipment_profile(
    example: dict[str, Any], *, band: Any, code: str, effective_from: datetime
) -> EquipmentProfile:
    stated = example["equipment"]
    window = example["payload_path_window"]["uplink"]
    return make_equipment_profile(
        band=band,
        code=code,
        rf_min_hz=window[0],
        rf_max_hz=window[1],
        if_min_hz=stated["if_min_hz"],
        if_max_hz=stated["if_max_hz"],
        lo_hz=stated["lo_hz"],
        conversion_method=stated["conversion_method"],
        sideband=stated["sideband"],
        effective_from=effective_from,
    )


def _beam(
    example: dict[str, Any],
    *,
    code: str,
    payload_path: PayloadPath,
    profile: EquipmentProfile,
    resources: list[SpectrumResource],
    band: Any,
    satellite: Any,
    admin: Any,
    assignment: dict[str, Any],
    master_from: datetime,
) -> BeamDirectionConfig:
    """One Beam with one configured direction, validated and activated."""
    windows = example["payload_path_window"]
    beam = make_beam(code, satellite=satellite, band=band, effective_from=master_from)
    config = configure_direction(
        beam,
        example["direction"],
        payload_path=payload_path,
        with_mapping=False,
        with_equipment=False,
        with_resources=False,
        with_assignments=False,
        uplink_polarization=windows["uplink_polarization"],
        downlink_polarization=windows["downlink_polarization"],
    )
    beam.direction_configs.exclude(direction=example["direction"]).update(is_enabled=False)

    BeamDirectionEquipmentProfile.objects.create(direction_config=config, equipment_profile=profile)
    for resource in resources:
        BeamDirectionSpectrumResource.objects.create(
            direction_config=config, spectrum_resource=resource
        )
    for window, edges in (
        (config.uplink_window, assignment["uplink"]),
        (config.downlink_window, assignment["downlink"]),
    ):
        BeamSpectrumAssignment.objects.create(
            direction_config=config,
            frequency_window=window,
            payload_path=payload_path,
            rf_start_hz=edges[0],
            rf_end_hz=edges[1],
            window_rf_start_hz=window.rf_start_hz,
            window_rf_end_hz=window.rf_end_hz,
            effective_from=_at(assignment["effective_from"]),
            effective_until=_at(assignment.get("effective_until")),
        )

    beam_services.validate_beam(actor=admin, beam=beam)
    beam.refresh_from_db()
    beam_services.set_active(actor=admin, beam=beam, active=True)
    config.refresh_from_db()
    return config


def _independent_beam(
    example: dict[str, Any],
    satellite: Any,
    band: Any,
    profile: EquipmentProfile,
    admin: Any,
    master_from: datetime,
) -> BeamDirectionConfig:
    """A Beam on the orthogonal polarization, with its own chains all the way down.

    Its resources are its own — that is what *"independently implemented"* means, and it is
    the whole reason the answer allows this allocation to be accepted. Sharing the resource
    rows here would make the acceptance impossible and the test meaningless.
    """
    stated = example["independent_polarization"]
    polarization = stated["polarization"]
    path = _payload_path(
        example,
        satellite=satellite,
        band=band,
        code="GOLD-PP-XPOL",
        uplink=stated["uplink"],
        downlink=stated["downlink"],
        uplink_polarization=polarization,
        downlink_polarization=polarization,
        effective_from=master_from,
    )
    resources = [
        SpectrumResource.objects.create(
            satellite=satellite,
            code=f"GOLD-SR-XPOL-{leg}",
            name=f"Independent {polarization} chain, {leg}",
            kind=SpectrumResourceKind.RF_CHAIN,
            leg=leg,
            polarization=polarization,
            effective_from=master_from,
        )
        for leg in (path.uplink_window_side, path.downlink_window_side)
    ]
    # The whole of its own windows: the file states this polarization's windows, not a
    # sub-range of them, and inventing a narrower assignment would decide by accident whether
    # the accepted allocation fits.
    assignment = {
        "uplink": stated["uplink"],
        "downlink": stated["downlink"],
        "effective_from": example["beam_spectrum_assignment"]["effective_from"],
        "effective_until": example["beam_spectrum_assignment"].get("effective_until"),
    }
    return _beam(
        {**example, "payload_path_window": _window_section(stated)},
        code="GOLD-BEAM-XPOL",
        payload_path=path,
        profile=profile,
        resources=resources,
        band=band,
        satellite=satellite,
        admin=admin,
        assignment=assignment,
        master_from=master_from,
    )


def _window_section(stated: dict[str, Any]) -> dict[str, Any]:
    return {
        "uplink": stated["uplink"],
        "downlink": stated["downlink"],
        "uplink_polarization": stated["polarization"],
        "downlink_polarization": stated["polarization"],
    }


def _satnet(admin: Any, beam: Beam, code: str, master_from: datetime) -> Satnet:
    gateway = make_gateway(f"GW-{code}")
    return satnet_services.create(
        actor=admin,
        values={
            "code": f"SN-{code}",
            "name": f"Satnet {code}",
            "beam": beam,
            "hub": make_hub(gateway, f"HUB-{code}"),
            "effective_from": master_from,
        },
    )


# ---------------------------------------------------------------------------
# Running what the example expects
# ---------------------------------------------------------------------------
def _create_requested_allocation(world: World) -> Any:
    return path_services.create(
        actor=world.admin,
        satnet=world.satnet,
        values=_values(world.example, code="GOLD-1"),
    )


def _values(
    example: dict[str, Any],
    *,
    code: str,
    centre_hz: int | None = None,
    valid_from: str | None = None,
    valid_until: str | None = None,
) -> dict[str, Any]:
    validity = example["validity"]
    symbol_rate = example.get("symbol_rate_sps")
    return {
        "code": code,
        "direction": example["direction"],
        "input_mode": InputMode.SYMBOL_RATE if symbol_rate else InputMode.OCCUPIED_BW,
        "input_value": symbol_rate or example["occupied_bandwidth_hz"],
        "rolloff": Decimal(example["rolloff"]),
        "canonical_center_hz": centre_hz or example["uplink_centre_hz"],
        "valid_from": _at(valid_from or validity["valid_from"]),
        "valid_until": _at(valid_until if valid_until else validity.get("valid_until")),
        "guard_policy": _guard_policy(example),
        "status": PathStatus.PLANNED,
    }


def _guard_policy(example: dict[str, Any]) -> GuardPolicy | None:
    guard = example.get("guard")
    if not guard or not guard.get("mode"):
        return None
    policy, _ = GuardPolicy.objects.get_or_create(
        code="GOLD-GUARD",
        defaults={
            "name": "Guard policy stated by the golden example",
            "mode": GuardMode(guard["mode"]),
            "fixed_left_hz": guard.get("left_hz"),
            "fixed_right_hz": guard.get("right_hz"),
            "percent_left": guard.get("percent_left"),
            "percent_right": guard.get("percent_right"),
        },
    )
    return policy


def _assert_free_capacity(world: World, path: Path) -> None:
    stated = world.example["expect"]["free_capacity"]
    leg = stated.get("leg") or world.canonical_leg
    summary = spectrum_selectors.capacity(world.config, leg=leg)

    assert [[gap.range.start_hz, gap.range.end_hz] for gap in summary.gaps] == [
        list(gap) for gap in stated["gaps"]
    ], f"{path.name}: the free spectrum on {leg} is not what the example states."
    if stated.get("total_free_hz") is not None:
        assert summary.free_hz == stated["total_free_hz"], path.name


def _run_scenario(world: World, scenario: dict[str, Any], path: Path, index: int) -> None:
    kind = scenario["kind"]
    satnet = {
        "REJECT_SHARED_PAYLOAD_INPUT": world.shared_input_satnet,
        "ACCEPT_INDEPENDENT_POLARIZATION": world.independent_satnet,
    }.get(kind, world.satnet)
    assert satnet is not None, (
        f"{path.name}: a {kind} scenario needs an 'independent_polarization' section stating "
        f"that polarization's windows."
    )

    values = _values(
        world.example,
        code=f"GOLD-S{index}",
        centre_hz=scenario.get("uplink_centre_hz"),
        valid_from=scenario.get("valid_from"),
        valid_until=scenario.get("valid_until"),
    )

    if scenario["expect"] == "ACCEPT":
        created = path_services.create(actor=world.admin, satnet=satnet, values=values)
        assert created.pk, f"{path.name}: {scenario['name']} should have been accepted."
        return

    with pytest.raises(path_services.PathBlockedError) as blocked:
        path_services.create(actor=world.admin, satnet=satnet, values=values)

    codes = {finding.code for finding in blocked.value.findings}
    expected = scenario.get("expect_finding")
    if expected:
        assert expected in codes, (
            f"{path.name}: {scenario['name']} was refused, but for {sorted(codes)} rather "
            f"than {expected}."
        )


# ---------------------------------------------------------------------------
# Reading the file
# ---------------------------------------------------------------------------
def _at(value: str | None) -> datetime | None:
    if value in (None, ""):
        return None
    parsed = parse_datetime(value)
    assert parsed is not None, f"{value!r} is not an ISO 8601 timestamp."
    assert parsed.tzinfo is not None, (
        f"{value!r} carries no time zone. Every timestamp in a golden example is UTC and says "
        f"so (A-28) — a naive value would be read differently by the file's author and by the "
        f"platform."
    )
    return parsed


def _needs_independent_polarization(example: dict[str, Any]) -> bool:
    stated = example.get("independent_polarization") or {}
    wanted = any(
        scenario.get("kind") == "ACCEPT_INDEPENDENT_POLARIZATION"
        for scenario in example["expect"].get("scenarios", [])
    )
    return bool(wanted and stated.get("polarization"))


def _require_periods_covering_the_present(example: dict[str, Any], path: Path) -> None:
    """The example's master data has to be in force *now*, and here is why.

    ``spectrum.selectors`` resolves active assignments and held reservations as at the current
    instant. An example whose assignment expired last year would produce an empty entitlement
    and every scenario would be refused for the wrong reason — a green-looking REJECT that
    proves nothing.

    That is a real limitation of the platform rather than of the file, and it is stated here
    instead of being worked around, because working around it would mean rewriting an
    engineer's dates.
    """
    now = timezone.now()
    assignment = example["beam_spectrum_assignment"]
    starts = _at(assignment["effective_from"])
    ends = _at(assignment.get("effective_until"))
    assert starts is not None and starts <= now, (
        f"{path.name}: the Beam Spectrum Assignment starts at {starts}, which is in the "
        f"future. The platform resolves entitlements and reservations as at now, so a "
        f"forward-dated example cannot be run end to end."
    )
    assert ends is None or ends > now, (
        f"{path.name}: the Beam Spectrum Assignment ended at {ends}. See above — the example "
        f"must be one that is in force, which the answer asks for anyway: a *currently "
        f"operational* Forward Satnet Path."
    )


# ---------------------------------------------------------------------------
# Scaffolding — not a golden example
# ---------------------------------------------------------------------------
MHZ = 1_000_000


def SCAFFOLDING() -> dict[str, Any]:
    """Arbitrary numbers, hand-checkable, chosen only to exercise this file.

    The uplink assignment is ``[29_100, 29_200) MHz``; a 10 MHz occupied bandwidth centred at
    29_150 MHz with 1 MHz guards allocates ``[29_144, 29_156) MHz``, leaving two gaps of
    44 MHz. Nothing here came from RF engineering and nothing here is asserted to be right —
    see ``test_the_harness_runs_on_a_scaffolding_example``.
    """
    started = (timezone.now() - timedelta(days=30)).isoformat()
    return {
        "name": "scaffolding",
        "source": "not a golden example",
        "direction": Direction.FWD,
        "payload_path_window": {
            "uplink": [29_000 * MHZ, 29_500 * MHZ],
            "uplink_polarization": "RHCP",
            "downlink": [19_000 * MHZ, 19_500 * MHZ],
            "downlink_polarization": "RHCP",
        },
        "beam_spectrum_assignment": {
            "uplink": [29_100 * MHZ, 29_200 * MHZ],
            "downlink": [19_100 * MHZ, 19_200 * MHZ],
            "effective_from": started,
            "effective_until": None,
        },
        "equipment": {
            "conversion_method": "LO_PLUS_IF",
            "sideband": "LOW_SIDE",
            "lo_hz": 28_050 * MHZ,
            "if_min_hz": 950 * MHZ,
            "if_max_hz": 1_950 * MHZ,
        },
        "validity": {
            "valid_from": (timezone.now() - timedelta(days=1)).isoformat(),
            "valid_until": None,
        },
        "occupied_bandwidth_hz": 10 * MHZ,
        "rolloff": "0.2",
        "uplink_centre_hz": 29_150 * MHZ,
        "guard": {"mode": "FIXED", "left_hz": 1 * MHZ, "right_hz": 1 * MHZ},
        "translation": {"method": "OFFSET_SUBTRACT", "constant_hz": 10_000 * MHZ},
        "independent_polarization": {
            "polarization": "LHCP",
            "uplink": [29_100 * MHZ, 29_200 * MHZ],
            "downlink": [19_100 * MHZ, 19_200 * MHZ],
        },
        "expect": {
            "free_capacity": {
                "leg": SpectrumLeg.HUB_UPLINK,
                "gaps": [[29_100 * MHZ, 29_144 * MHZ], [29_156 * MHZ, 29_200 * MHZ]],
                "total_free_hz": 88 * MHZ,
            },
            "scenarios": [
                {
                    "name": "another Hub, Beam and site on the same payload input",
                    "kind": "REJECT_SHARED_PAYLOAD_INPUT",
                    "uplink_centre_hz": 29_150 * MHZ,
                    "expect": "REJECT",
                    "expect_finding": "SPECTRUM_CONFLICT",
                },
                {
                    "name": "the same frequency on an independent polarization",
                    "kind": "ACCEPT_INDEPENDENT_POLARIZATION",
                    "uplink_centre_hz": 29_150 * MHZ,
                    "expect": "ACCEPT",
                },
                {
                    "name": "inside the window, outside the assignment",
                    "kind": "REJECT_OUTSIDE_ASSIGNMENT",
                    "uplink_centre_hz": 29_300 * MHZ,
                    "expect": "REJECT",
                    "expect_finding": "OUTSIDE_ENTITLEMENT",
                },
                {
                    "name": "before the assignment took effect",
                    "kind": "REJECT_OUTSIDE_VALIDITY",
                    "uplink_centre_hz": 29_150 * MHZ,
                    "valid_from": (timezone.now() - timedelta(days=45)).isoformat(),
                    "expect": "REJECT",
                    "expect_finding": "PERIOD_NOT_CONTAINED",
                },
            ],
        },
    }
