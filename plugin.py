# -*- coding: utf-8 -*-
"""
Geometry and Table Compare - Main Plugin Class

Registers the toolbar/menu entry and launches the comparison dialog.
"""

import os

from qgis.PyQt.QtGui import QIcon

try:
    from qgis.PyQt.QtGui import QAction  # Qt6
except ImportError:
    from qgis.PyQt.QtWidgets import QAction  # Qt5

PLUGIN_NAME = "Geometry and Table Compare"


class GeoTableComparePlugin:
    """Main plugin class for Geometry and Table Compare."""

    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.menu_name = PLUGIN_NAME
        self.toolbar = None
        self.action = None
        self.dlg = None

    def initGui(self):
        """Create the toolbar button and menu entry."""
        self.toolbar = self.iface.addToolBar(self.menu_name)
        self.toolbar.setObjectName("GeoTableCompareToolbar")

        self.action = QAction(
            QIcon(os.path.join(self.plugin_dir, 'icon.svg')),
            PLUGIN_NAME,
            self.iface.mainWindow()
        )
        self.action.setToolTip(
            "Compare OLD → NEW vector layers/tables\n"
            "with change detection, geometry comparison,\n"
            "validation, filtering, and Excel / CSV export"
        )
        self.action.triggered.connect(self.run)
        self.toolbar.addAction(self.action)
        self.iface.addPluginToMenu(self.menu_name, self.action)

    def run(self):
        """Launch the comparison dialog.

        parent=None keeps it a top-level window with its own taskbar entry
        (like TUFLOW Sentinel). It is non-modal, so the map stays usable for
        'Show on map'. An already-open dialog is simply brought to the front.
        """
        if self.dlg is not None:
            try:
                if self.dlg.isVisible():
                    self.dlg.showNormal()
                    self.dlg.raise_()
                    self.dlg.activateWindow()
                    return
            except RuntimeError:   # underlying Qt object already deleted
                pass
        from .ui.geotable_compare import GeoTableCompareDialog
        self.dlg = GeoTableCompareDialog(self.iface, parent=None)
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

    def unload(self):
        """Remove the menu entry, toolbar and any open dialog."""
        if self.action is not None:
            self.iface.removePluginMenu(self.menu_name, self.action)
            if self.toolbar is not None:
                self.toolbar.removeAction(self.action)
            self.action = None
        self.toolbar = None

        if self.dlg is not None:
            self.dlg.close()
            self.dlg = None
