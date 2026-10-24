# -*- coding: utf-8 -*-
"""Persistent GeoTable Studio settings (QgsSettings, user profile scope)."""

from qgis.core import QgsSettings

PREFIX = "GeoTableStudio"

DEFAULTS = {
    "explorer/openDocked": False,
    "pivot/openDocked": False,
    "compare/openDocked": False,
    "explorer/summaryVisible": True,
    "explorer/filterDelayMs": 350,
    "pivot/cellLimit": 100000,
    "pivot/decimals": 2,
}


def _key(key):
    return f"{PREFIX}/{key}"


def value(key):
    default = DEFAULTS[key]
    raw = QgsSettings().value(_key(key), default)
    if isinstance(default, bool):
        if isinstance(raw, str):
            return raw.strip().lower() in ("true", "1", "yes")
        return bool(raw)
    if isinstance(default, int):
        try:
            return int(raw)
        except (TypeError, ValueError):
            return default
    return raw


def set_value(key, val):
    QgsSettings().setValue(_key(key), val)


def raw(key, default=None):
    """Untyped value outside DEFAULTS (window geometry, etc.)."""
    return QgsSettings().value(_key(key), default)


def set_raw(key, val):
    QgsSettings().setValue(_key(key), val)
