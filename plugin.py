# -*- coding: utf-8 -*-
"""
GeoTable Studio - plugin shell.

Owns the single "GeoTable Studio" toolbar with exactly three actions
(Attribute Explorer, Pivot Table, Geometry and Table Compare), the plugin
menu (the three tools, Settings, Help), the keyboard-shortcut registration,
the layer context-menu entry and the open tool windows.  The tools
themselves live in ``ui/``; each is shown by a ``ToolHost`` as a window or
a dock.
"""

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import QgsMessageLog, QgsProject, QgsVectorLayer

from .core.compat import LAYER_TYPE_VECTOR, MSG_INFO, MSG_WARNING, QAction
from .ui import icons

PLUGIN_NAME = "GeoTable Studio"
MENU = "&GeoTable Studio"
EXPLORER_SHORTCUT = "Ctrl+Alt+T"
PACKAGE = __name__.split(".")[0]


class GeoTableStudioPlugin:
    """Main plugin class for GeoTable Studio."""

    def __init__(self, iface):
        self.iface = iface
        self.toolbar = None
        self.actions = {}
        self.menu_actions = []
        self.context_action = None
        self.explorers = {}          # layer id -> ToolHost
        self.pivot_host = None
        self.compare_host = None
        self._signals = []

    # ====================================================================
    # GUI
    # ====================================================================

    def initGui(self):
        main = self.iface.mainWindow()
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("GeoTableStudioToolbar")
        self.toolbar.setToolTip(PLUGIN_NAME)

        specs = (
            ("explorer", icons.EXPLORER, "Attribute Explorer",
             "Attribute Explorer\nExplore, filter and profile the records of "
             "the active layer", self.open_explorer),
            ("pivot", icons.PIVOT, "Pivot Table",
             "Pivot Table\nGrouped summaries and cross tabs", self.open_pivot),
            ("compare", icons.COMPARE, "Geometry and Table Compare",
             "Geometry and Table Compare\nCompare OLD → NEW vector layers / "
             "tables: change detection, geometry comparison, validation and "
             "Excel / CSV export", self.open_compare),
        )
        for key, icon, text, tip, slot in specs:
            action = QAction(icons.tool(icon), text, main)
            action.setObjectName(f"mActionGeoTableStudio_{key}")
            action.setToolTip(tip)
            action.setWhatsThis(tip)
            action.triggered.connect(lambda _checked=False, s=slot: s())
            self.toolbar.addAction(action)
            self.iface.addPluginToMenu(MENU, action)
            self.actions[key] = action

        separator = QAction(main)
        separator.setSeparator(True)
        settings_action = QAction(icons.native(icons.SETTINGS), "Settings…", main)
        settings_action.setObjectName("mActionGeoTableStudio_settings")
        settings_action.triggered.connect(self.show_settings)
        help_action = QAction(icons.native(icons.HELP), "Help", main)
        help_action.setObjectName("mActionGeoTableStudio_help")
        help_action.triggered.connect(self.show_help)
        for action in (separator, settings_action, help_action):
            self.iface.addPluginToMenu(MENU, action)
            self.menu_actions.append(action)

        self._register_shortcuts()

        self.context_action = QAction(icons.tool(icons.EXPLORER),
                                      "Open in Attribute Explorer", main)
        self.context_action.setObjectName("mActionGeoTableStudio_layerExplorer")
        self.context_action.triggered.connect(
            lambda: self.open_explorer(self.iface.activeLayer()))
        self.iface.addCustomActionForLayerType(
            self.context_action, "", LAYER_TYPE_VECTOR, True)

        project = QgsProject.instance()
        self._connect(project.layersWillBeRemoved, self._on_layers_removed)
        self._connect(project.cleared, self._on_project_cleared)
        self._connect(QApplication.instance().aboutToQuit, self._close_all)
        self._warn_standalone_compare()

    def _connect(self, signal, slot):
        signal.connect(slot)
        self._signals.append((signal, slot))

    def _register_shortcuts(self):
        """Register the three actions with QGIS' shortcut manager (so they
        appear in Settings > Keyboard Shortcuts and custom keys persist).
        The proposed Explorer key is only used when nothing else owns it."""
        default = EXPLORER_SHORTCUT
        try:
            from qgis.gui import QgsGui
            owner = QgsGui.shortcutsManager().actionForSequence(
                QKeySequence(EXPLORER_SHORTCUT))
        except Exception:
            owner = None
        if owner is not None and owner is not self.actions["explorer"]:
            default = ""
            QgsMessageLog.logMessage(
                f"{EXPLORER_SHORTCUT} is already used by "
                f"'{owner.text().replace('&', '')}'; Attribute Explorer has no "
                "shortcut. Assign one in Settings > Keyboard Shortcuts.",
                PLUGIN_NAME, MSG_INFO)
        self.iface.registerMainWindowAction(self.actions["explorer"], default)
        self.iface.registerMainWindowAction(self.actions["pivot"], "")
        self.iface.registerMainWindowAction(self.actions["compare"], "")

    def _warn_standalone_compare(self):
        """The suite includes Compare; warn if the standalone plugin is on."""
        try:
            from qgis import utils
            others = [name for name, plugin in utils.plugins.items()
                      if name != PACKAGE
                      and type(plugin).__name__ == "GeoTableComparePlugin"]
        except Exception:
            others = []
        if others:
            self.iface.messageBar().pushMessage(
                PLUGIN_NAME,
                "The standalone 'Geometry and Table Compare' plugin is also "
                "enabled. GeoTable Studio already includes it - disable the "
                "standalone plugin in Plugins > Manage and Install Plugins.",
                level=MSG_WARNING, duration=12)

    def unload(self):
        self._close_all()
        for signal, slot in self._signals:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._signals = []
        if self.context_action is not None:
            self.iface.removeCustomActionForLayerType(self.context_action)
            self.context_action = None
        for action in self.actions.values():
            try:
                self.iface.unregisterMainWindowAction(action)
            except Exception:
                pass
            self.iface.removePluginMenu(MENU, action)
            if self.toolbar is not None:
                self.toolbar.removeAction(action)
        for action in self.menu_actions:
            self.iface.removePluginMenu(MENU, action)
        self.actions = {}
        self.menu_actions = []
        if self.toolbar is not None:
            self.iface.mainWindow().removeToolBar(self.toolbar)
            self.toolbar.deleteLater()
            self.toolbar = None

    def _close_all(self):
        for host in list(self.explorers.values()):
            host.close()
        self.explorers = {}
        for host in (self.pivot_host, self.compare_host):
            if host is not None:
                host.close()
        self.pivot_host = None
        self.compare_host = None

    # ====================================================================
    # ATTRIBUTE EXPLORER
    # ====================================================================

    def _shortcut_text(self):
        action = self.actions.get("explorer")
        if action is None or action.shortcut().isEmpty():
            return ""
        return action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)

    def open_explorer(self, layer=None):
        """Open (or raise) the explorer of *layer* (default: active layer)."""
        if layer is None:
            layer = self.iface.activeLayer()
        if not isinstance(layer, QgsVectorLayer):
            if layer is not None and layer.type() != LAYER_TYPE_VECTOR:
                text = (f"'{layer.name()}' is not a vector layer. Attribute "
                        "Explorer needs a vector layer or an attribute-only table.")
            else:
                text = ("Select a vector layer or an attribute-only table in "
                        "the Layers panel first.")
            self.iface.messageBar().pushMessage("Attribute Explorer", text,
                                                level=MSG_INFO, duration=6)
            return None
        host = self.explorers.get(layer.id())
        if host is not None:
            host.show_and_raise()
            return host.panel

        from .ui.attribute_explorer import ExplorerPanel
        from .ui.tool_host import ToolHost
        panel = ExplorerPanel(self.iface, layer, self._shortcut_text())
        host = ToolHost(self.iface, panel, "explorer", icons.tool(icons.EXPLORER),
                        QSize(1440, 780))
        panel.openPivotRequested.connect(self.open_pivot)
        panel.layerChangeRequested.connect(self._switch_explorer_layer)
        panel.titleChanged.connect(host.refresh_title)
        host.closed.connect(self._on_explorer_closed)
        cascade = sum(1 for h in self.explorers.values() if not h.is_docked)
        self.explorers[layer.id()] = host
        host.open(cascade=cascade)
        return panel

    def _switch_explorer_layer(self, panel, layer):
        other = self.explorers.get(layer.id())
        if other is not None and other.panel is not panel:
            panel.revert_combo()
            other.show_and_raise()
            return
        for lid, host in list(self.explorers.items()):
            if host.panel is panel:
                del self.explorers[lid]
                self.explorers[layer.id()] = host
                break
        panel.set_layer(layer)

    def _on_explorer_closed(self, host):
        for lid, h in list(self.explorers.items()):
            if h is host:
                del self.explorers[lid]

    def explorer_for(self, layer):
        if layer is None:
            return None
        host = self.explorers.get(layer.id())
        return host.panel if host is not None else None

    # ====================================================================
    # PIVOT TABLE
    # ====================================================================

    def open_pivot(self, layer=None, scope=None):
        if self.pivot_host is None:
            from .ui.pivot_table import PivotPanel
            from .ui.tool_host import ToolHost
            panel = PivotPanel(self.iface, explorer_lookup=self.explorer_for)
            host = ToolHost(self.iface, panel, "pivot", icons.tool(icons.PIVOT),
                            QSize(1440, 780))
            panel.drillRequested.connect(self._drill_through)
            panel.titleChanged.connect(host.refresh_title)
            host.closed.connect(self._on_pivot_closed)
            self.pivot_host = host
            host.open()
        else:
            self.pivot_host.show_and_raise()
        panel = self.pivot_host.panel
        if layer is None and panel.layer is None:
            active = self.iface.activeLayer()
            layer = active if isinstance(active, QgsVectorLayer) else None
        if layer is not None:
            panel.set_layer(layer, scope)
        self.pivot_host.refresh_title()
        return panel

    def _on_pivot_closed(self, _host):
        self.pivot_host = None

    def _drill_through(self, layer, fids, label):
        panel = self.open_explorer(layer)
        if panel is not None:
            panel.set_external_scope(fids, label)

    # ====================================================================
    # GEOMETRY AND TABLE COMPARE
    # ====================================================================

    def open_compare(self):
        if self.compare_host is not None:
            self.compare_host.show_and_raise()
            return self.compare_host.panel
        from .ui.geotable_compare import GeoTableCompareDialog
        from .ui.tool_host import ToolHost
        panel = GeoTableCompareDialog(self.iface)
        host = ToolHost(self.iface, panel, "compare", icons.tool(icons.COMPARE),
                        QSize(1440, 820))
        host.closed.connect(self._on_compare_closed)
        self.compare_host = host
        host.open()
        return panel

    def _on_compare_closed(self, _host):
        self.compare_host = None

    # ====================================================================
    # PROJECT
    # ====================================================================

    def _on_layers_removed(self, layer_ids):
        for lid in layer_ids:
            host = self.explorers.pop(lid, None)
            if host is not None:
                host.close()
        if self.pivot_host is not None:
            panel = self.pivot_host.panel
            if panel.layer is not None and panel.layer.id() in layer_ids:
                panel.set_layer(None)

    def _on_project_cleared(self):
        for host in list(self.explorers.values()):
            host.close()
        self.explorers = {}
        if self.pivot_host is not None:
            self.pivot_host.panel.set_layer(None)

    # ====================================================================
    # MENU
    # ====================================================================

    def show_settings(self):
        from .ui.settings_dialog import SettingsDialog
        SettingsDialog(self.iface.mainWindow()).exec()

    def show_help(self):
        from .ui.help_dialog import HelpDialog
        dlg = HelpDialog(self.iface.mainWindow())
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()
