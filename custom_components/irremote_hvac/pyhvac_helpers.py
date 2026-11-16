"""Helpers for selecting pyhvac devices and migrating legacy entries."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from pyhvac import registry

from .const import (
    CONF_BRAND,
    CONF_LEGACY_PROTOCOL,
    CONF_MODEL,
    LEGACY_DEFAULT_MODEL,
    LEGACY_SUPPORTED_PROTOCOLS,
)

_LEGACY_PROTOCOL_ALIASES = {
    "DELONGHI_AC": "delonghi_ac",
    "ELECTRA_AC": "electra_ac",
    "FUJITSU_AC": "fujitsu_ac",
    "HAIER_AC_YRW02": "haier_yrw02",
    "HAIER_AC160": "haier160",
    "HAIER_AC176": "haier176",
    "HITACHI_AC1": "hitachi1",
    "HITACHI_AC264": "hitachi264",
    "HITACHI_AC296": "hitachi296",
    "HITACHI_AC344": "hitachi344",
    "HITACHI_AC424": "hitachi424",
    "LG": "lg-ac",
    "MITSUBISHI_HEAVY_88": "mitsubishi-heavy88",
    "MITSUBISHI_HEAVY_152": "mitsubishi_heavy152",
    "SAMSUNG_AC": "samsung_ac",
    "SANYO_AC": "sanyo_ac",
    "SHARP_AC": "sharp_ac",
    "TOSHIBA_AC": "toshiba_ac",
    "WHIRLPOOL_AC": "whirlpool_ac",
}


def get_device(brand: str, model: str) -> Any:
    """Return a registered device, raising a useful error for stale choices."""
    try:
        return registry.get_device(brand, model)
    except KeyError as err:
        raise ValueError(f"Unknown pyhvac device {brand!r} / {model!r}") from err


@lru_cache(maxsize=1)
def _devices_by_protocol() -> dict[str, list[tuple[str, str]]]:
    devices: dict[str, list[tuple[str, str]]] = {}
    for brand in registry.brands():
        for model in registry.models(brand):
            device = registry.get_device(brand, model)
            devices.setdefault(device.PROTOCOL.name, []).append((brand, model))
    return devices


def migrate_legacy_device(data: dict[str, Any]) -> dict[str, Any]:
    """Translate a legacy IRremoteESP8266 protocol entry when unambiguous."""
    if CONF_BRAND in data and isinstance(data.get(CONF_MODEL), str):
        return data

    protocol = data.get(CONF_LEGACY_PROTOCOL)
    if protocol not in LEGACY_SUPPORTED_PROTOCOLS.values():
        raise ValueError("This entry uses an unknown legacy pyhvac protocol.")

    try:
        model_variant = int(data.get(CONF_MODEL, LEGACY_DEFAULT_MODEL))
    except (TypeError, ValueError) as err:
        raise ValueError(
            "The saved legacy model is invalid; reconfigure this device."
        ) from err
    if model_variant != LEGACY_DEFAULT_MODEL:
        raise ValueError(
            "This legacy model variant cannot be mapped automatically. "
            "Reconfigure the device and select its brand and model."
        )

    protocol_name = _LEGACY_PROTOCOL_ALIASES.get(
        protocol, protocol.lower().replace("_", "-")
    )
    candidates = [
        (brand, model)
        for brand, model in _devices_by_protocol().get(protocol_name, [])
        if "protocol" in model.casefold()
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"Legacy protocol {protocol!r} does not have one unambiguous "
            "pyhvac 0.2 device. Reconfigure this device and select its brand "
            "and model."
        )

    brand, model = candidates[0]
    migrated = dict(data)
    migrated.pop(CONF_LEGACY_PROTOCOL, None)
    migrated[CONF_BRAND] = brand
    migrated[CONF_MODEL] = model
    return migrated
