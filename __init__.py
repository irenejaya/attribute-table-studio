# -*- coding: utf-8 -*-
"""
Attribute Table Studio - QGIS Plugin

Three tools in one toolbar: Attribute Explorer (records with attached
column statistics and per-column filters), Pivot Table (grouped summaries
and cross tabs) and Geometry and Table Compare (OLD -> NEW change
detection).
"""

__author__ = 'Irene Jaya'
__date__ = '2026-10-24'
__copyright__ = '(C) 2026, Irene Jaya'


def classFactory(iface):
    """Return the plugin instance for the given QGIS interface."""
    from .plugin import AttributeTableStudioPlugin
    return AttributeTableStudioPlugin(iface)
