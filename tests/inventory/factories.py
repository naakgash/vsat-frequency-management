"""Inventory test builders.

Frequencies here are arbitrary and exist only to satisfy constraints. They are **not** RF
engineering values and must never be mistaken for seed data — see
``test_no_inventory_is_seeded``.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from inventory.constants import (
    ConversionMethod,
    Direction,
    EquipmentType,
    GuardMode,
    OrbitType,
    PolarizationType,
    Sideband,
    SpectrumLeg,
    TranslationMethod,
)
from inventory.models import (
    Band,
    BandPolarization,
    EquipmentProfile,
    FrequencyWindow,
    Gateway,
    GuardPolicy,
    Hub,
    PayloadPath,
    Satellite,
)


def make_satellite(code: str = "SAT-1", **extra: Any) -> Satellite:
    return Satellite.objects.create(
        code=code,
        name=extra.pop("name", f"Satellite {code}"),
        orbit_type=extra.pop("orbit_type", OrbitType.GEO),
        effective_from=extra.pop("effective_from", timezone.now()),
        **extra,
    )


def make_band(code: str = "BAND-1", **extra: Any) -> Band:
    return Band.objects.create(
        code=code,
        name=extra.pop("name", f"Band {code}"),
        rf_min_hz=extra.pop("rf_min_hz", 27_500_000_000),
        rf_max_hz=extra.pop("rf_max_hz", 30_000_000_000),
        **extra,
    )


def add_polarization(band: Band, polarization: str) -> BandPolarization:
    return BandPolarization.objects.create(band=band, polarization=polarization)


def make_gateway(code: str = "GW-1", **extra: Any) -> Gateway:
    return Gateway.objects.create(code=code, name=extra.pop("name", f"Gateway {code}"), **extra)


def make_hub(gateway: Gateway | None = None, code: str = "HUB-1", **extra: Any) -> Hub:
    return Hub.objects.create(
        code=code,
        name=extra.pop("name", f"Hub {code}"),
        gateway=gateway or make_gateway(),
        **extra,
    )


def make_equipment_profile(
    band: Band | None = None, code: str = "BUC-1", **extra: Any
) -> EquipmentProfile:
    return EquipmentProfile.objects.create(
        code=code,
        name=extra.pop("name", f"Profile {code}"),
        type=extra.pop("type", EquipmentType.BUC),
        band=band or make_band(),
        rf_min_hz=extra.pop("rf_min_hz", 29_000_000_000),
        rf_max_hz=extra.pop("rf_max_hz", 30_000_000_000),
        if_min_hz=extra.pop("if_min_hz", 950_000_000),
        if_max_hz=extra.pop("if_max_hz", 1_950_000_000),
        lo_hz=extra.pop("lo_hz", 28_050_000_000),
        conversion_method=extra.pop("conversion_method", ConversionMethod.LO_PLUS_IF),
        sideband=extra.pop("sideband", Sideband.LOW_SIDE),
        effective_from=extra.pop("effective_from", timezone.now()),
        **extra,
    )


def make_guard_policy(code: str = "GUARD-1", **extra: Any) -> GuardPolicy:
    return GuardPolicy.objects.create(
        code=code,
        name=extra.pop("name", f"Guard policy {code}"),
        mode=extra.pop("mode", GuardMode.FIXED),
        fixed_left_hz=extra.pop("fixed_left_hz", 500_000),
        fixed_right_hz=extra.pop("fixed_right_hz", 500_000),
        **extra,
    )


def make_frequency_window(
    satellite: Satellite | None = None,
    code: str = "FW-1",
    side: str = SpectrumLeg.HUB_UPLINK,
    **extra: Any,
) -> FrequencyWindow:
    return FrequencyWindow.objects.create(
        code=code,
        name=extra.pop("name", f"Window {code}"),
        satellite=satellite or make_satellite(),
        band=extra.pop("band", None) or make_band(f"BAND-{code}"),
        side=side,
        polarization=extra.pop("polarization", PolarizationType.RHCP),
        rf_start_hz=extra.pop("rf_start_hz", 29_000_000_000),
        rf_end_hz=extra.pop("rf_end_hz", 29_500_000_000),
        effective_from=extra.pop("effective_from", timezone.now()),
        **extra,
    )


def make_payload_path(
    satellite: Satellite | None = None,
    code: str = "PP-1",
    direction: str = Direction.FWD,
    **extra: Any,
) -> PayloadPath:
    """Build a payload path with windows whose sides match its direction.

    The sides are not a detail the caller should have to get right: a FWD path runs hub
    uplink to remote downlink, and the database refuses anything else.
    """
    satellite = satellite or make_satellite()
    band = make_band(f"BAND-{code}")

    if direction == Direction.FWD:
        up_side, down_side = SpectrumLeg.HUB_UPLINK, SpectrumLeg.REMOTE_DOWNLINK
    else:
        up_side, down_side = SpectrumLeg.REMOTE_UPLINK, SpectrumLeg.HUB_DOWNLINK

    uplink = extra.pop("uplink_window", None) or make_frequency_window(
        satellite, f"{code}-UL", up_side, band=band
    )
    downlink = extra.pop("downlink_window", None) or make_frequency_window(
        satellite,
        f"{code}-DL",
        down_side,
        band=band,
        rf_start_hz=19_000_000_000,
        rf_end_hz=19_500_000_000,
    )

    return PayloadPath.objects.create(
        code=code,
        name=extra.pop("name", f"Payload path {code}"),
        satellite=satellite,
        direction=direction,
        uplink_window=uplink,
        downlink_window=downlink,
        uplink_window_side=uplink.side,
        downlink_window_side=downlink.side,
        translation_method=extra.pop("translation_method", TranslationMethod.OFFSET_SUBTRACT),
        translation_constant_hz=extra.pop("translation_constant_hz", 10_000_000_000),
        effective_from=extra.pop("effective_from", timezone.now()),
        **extra,
    )
