# -*- coding: utf-8 -*-
"""
Geometry and Table Compare - QGIS Plugin

Visual comparison tool for vector layer attributes and geometry.
Detect added, deleted, and modified features between two dataset versions.
"""

__author__ = 'Irene Jaya'
__date__ = '2026-02-11'
__copyright__ = '(C) 2026, Irene Jaya'


def classFactory(iface):
    """Return the plugin instance for the given QGIS interface."""
    from .plugin import GeoTableComparePlugin
    return GeoTableComparePlugin(iface)
