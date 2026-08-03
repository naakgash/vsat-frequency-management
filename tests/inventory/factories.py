"""Inventory test builders.

Frequencies here are arbitrary and exist only to satisfy constraints. They are **not** RF
engineering values and must never be mistaken for seed data — see
``test_no_inventory_is_seeded``.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from inventory.constants import ConversionMethod, EquipmentType, OrbitType, Sideband
from inventory.models import Band, BandPolarization, EquipmentProfile, Gateway, Hub, Satellite


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
