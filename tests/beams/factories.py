"""Beam test builders.

Every frequency here exists only to satisfy a constraint. They are **not** RF engineering
values — see ``test_no_beams_are_seeded``.
"""

from __future__ import annotations

from typing import Any

from beams.constants import CANONICAL_LEG_DEFAULTS, Direction
from beams.models import Beam, BeamDirectionConfig, BeamDirectionEquipmentProfile
from inventory.constants import PolarizationType
from inventory.models import PayloadPolarizationMapping
from tests.inventory.factories import (
    make_band,
    make_equipment_profile,
    make_payload_path,
    make_satellite,
)


def make_beam(code: str = "BEAM-1", **extra: Any) -> Beam:
    """A Beam with both direction rows, enabled and unconfigured — i.e. INCOMPLETE."""
    beam = Beam.objects.create(
        code=code,
        name=extra.pop("name", f"Beam {code}"),
        satellite=extra.pop("satellite", None) or make_satellite(f"SAT-{code}"),
        band=extra.pop("band", None) or make_band(f"BAND-{code}"),
        **extra,
    )
    for direction in Direction.values:
        BeamDirectionConfig.objects.create(
            beam=beam, direction=direction, canonical_leg=CANONICAL_LEG_DEFAULTS[direction]
        )
    return beam


def configure_direction(
    beam: Beam,
    direction: str = Direction.FWD,
    *,
    with_mapping: bool = True,
    with_equipment: bool = True,
    **extra: Any,
) -> BeamDirectionConfig:
    """Give one direction a complete, valid chain.

    Builds the payload path on the Beam's own satellite and takes the windows *from* the
    path, which is what A-06 requires — a factory that let them differ would make the
    identity rule untestable by making every fixture violate it.
    """
    config = beam.direction_configs.get(direction=direction)
    path = extra.pop("payload_path", None) or make_payload_path(
        satellite=beam.satellite, code=f"PP-{beam.code}-{direction}", direction=direction
    )

    if with_mapping:
        PayloadPolarizationMapping.objects.get_or_create(
            payload_path=path,
            uplink_polarization=PolarizationType.RHCP,
            downlink_polarization=PolarizationType.RHCP,
        )

    config.payload_path = path
    config.uplink_window = path.uplink_window
    config.downlink_window = path.downlink_window
    config.canonical_leg = extra.pop("canonical_leg", CANONICAL_LEG_DEFAULTS[direction])
    config.uplink_polarization = extra.pop("uplink_polarization", PolarizationType.RHCP)
    config.downlink_polarization = extra.pop("downlink_polarization", PolarizationType.RHCP)
    for field, value in extra.items():
        setattr(config, field, value)
    config.save()

    if with_equipment:
        BeamDirectionEquipmentProfile.objects.create(
            direction_config=config,
            equipment_profile=make_equipment_profile(
                band=beam.band, code=f"BUC-{beam.code}-{direction}"
            ),
        )
    return config


def make_valid_beam(code: str = "BEAM-OK", **extra: Any) -> Beam:
    """A Beam whose FWD chain is complete and whose RTN direction is explicitly disabled.

    Disabling RTN rather than configuring it keeps the fixture small while exercising the
    §5.4 case that a single-direction Beam is legitimate.
    """
    beam = make_beam(code, **extra)
    configure_direction(beam, Direction.FWD)
    beam.direction_configs.filter(direction=Direction.RTN).update(is_enabled=False)
    return beam
