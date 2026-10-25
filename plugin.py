# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Attribute Table Studio - plugin shell.

Owns the single "Attribute Table Studio" toolbar with exactly three actions
(Attribute Explorer, Pivot Table, Geometry and Table Compare), the plugin
menu (the three tools, Settings, Help), the keyboard-shortcut registration,
the layer context-menu entry and the open tool windows.  The tools
themselves live in ``ui/``; each is shown by a ``ToolHost`` as a window or
a dock.
"""

import json

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtGui import QKeySequence
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import QgsMessageLog, QgsProject, QgsVectorLayer

from .core import settings
from .core import subset as SUB
from .core.compat import LAYER_TYPE_VECTOR, MSG_INFO, MSG_WARNING, QAction
from .ui import icons

PLUGIN_NAME = "Attribute Table Studio"
MENU = "&Attribute Table Studio"
EXPLORER_SHORTCUT = "Ctrl+Alt+T"
PACKAGE = __name__.split(".")[0]


class AttributeTableStudioPlugin:
    """Main plugin class for Attribute Table Studio."""

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
        self.table_keys = []         # F6 / Shift+F6 / Ctrl+F6 when opted in

    # ====================================================================
    # GUI
    # ====================================================================

    def initGui(self):
        main = self.iface.mainWindow()
        self.toolbar = self.iface.addToolBar(PLUGIN_NAME)
        self.toolbar.setObjectName("AttributeTableStudioToolbar")
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
            action.setObjectName(f"mActionAttributeTableStudio_{key}")
            action.setToolTip(tip)
            action.setWhatsThis(tip)
            action.triggered.connect(lambda _checked=False, s=slot: s())
            self.toolbar.addAction(action)
            self.iface.addPluginToMenu(MENU, action)
            self.actions[key] = action

        separator = QAction(main)
        separator.setSeparator(True)
        settings_action = QAction(icons.native(icons.SETTINGS), "Settings…", main)
        settings_action.setObjectName("mActionAttributeTableStudio_settings")
        settings_action.triggered.connect(self.show_settings)
        help_action = QAction(icons.native(icons.HELP), "Help", main)
        help_action.setObjectName("mActionAttributeTableStudio_help")
        help_action.triggered.connect(self.show_help)
        for action in (separator, settings_action, help_action):
            self.iface.addPluginToMenu(MENU, action)
            self.menu_actions.append(action)

        self._register_shortcuts()

        self.context_action = QAction(icons.tool(icons.EXPLORER),
                                      "Open in Attribute Explorer", main)
        self.context_action.setObjectName("mActionAttributeTableStudio_layerExplorer")
        self.context_action.triggered.connect(
            lambda: self.open_explorer(self.iface.activeLayer()))
        self.iface.addCustomActionForLayerType(
            self.context_action, "", LAYER_TYPE_VECTOR, True)

        project = QgsProject.instance()
        self._connect(project.layersWillBeRemoved, self._on_layers_removed)
        self._connect(project.cleared, self._on_project_cleared)
        self._connect(project.layersAdded, self._restore_forgotten_filters)
        # a project saved while a map filter is on keeps the layer's own filter
        self._connect(project.writeMapLayer, SUB.write_layer)
        self._connect(project.writeProject, SUB.write_project)
        self._connect(QApplication.instance().aboutToQuit, self._close_all)
        self._restore_forgotten_filters(list(project.mapLayers().values()))
        self.apply_table_keys()
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
                "enabled. Attribute Table Studio already includes it - disable the "
                "standalone plugin in Plugins > Manage and Install Plugins.",
                level=MSG_WARNING, duration=12)

    def unload(self):
        self._close_all()
        self._release_table_keys()
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
            except (AttributeError, RuntimeError, TypeError):
                pass                     # not registered / already deleted
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

    # opt-in replacement keys: (key, starting scope, action text)
    TABLE_KEYS = (("F6", "all", "Attribute Explorer (all records)"),
                  ("Shift+F6", "selected", "Attribute Explorer (selected records)"),
                  ("Ctrl+F6", "map", "Attribute Explorer (records visible on the map)"))

    def apply_table_keys(self):
        """Take or give back F6 / Shift+F6 / Ctrl+F6 to match the setting."""
        wanted = settings.value("explorer/replaceTableKeys")
        if wanted and not self.table_keys:
            self._take_table_keys()
        elif not wanted and self.table_keys:
            self._release_table_keys()

    def _shortcuts_manager(self):
        try:
            from qgis.gui import QgsGui
            return QgsGui.shortcutsManager()
        except Exception:
            return None

    def _take_table_keys(self):
        """Clear the keys from their current owners (remembered, so only
        those bindings are restored later) and give them to Explorer."""
        mgr = self._shortcuts_manager()
        try:
            taken = json.loads(settings.raw("explorer/takenKeys", "{}") or "{}")
        except (TypeError, ValueError):
            taken = {}
        main = self.iface.mainWindow()
        for seq, scope, text in self.TABLE_KEYS:
            owner = mgr.actionForSequence(QKeySequence(seq)) if mgr is not None else None
            if owner is not None and owner.objectName() and owner not in self.table_keys:
                taken[seq] = owner.objectName()
                mgr.setKeySequence(owner, "")
            act = QAction(icons.tool(icons.EXPLORER), text, main)
            act.setObjectName(f"mActionAttributeTableStudio_table_{scope}")
            act.setShortcut(QKeySequence(seq))
            act.setShortcutContext(Qt.ShortcutContext.ApplicationShortcut)
            act.triggered.connect(lambda _c=False, s=scope: self.open_explorer(None, s))
            main.addAction(act)
            self.table_keys.append(act)
        settings.set_raw("explorer/takenKeys", json.dumps(taken))

    def _release_table_keys(self):
        """Remove Explorer's keys and give back only the bindings it took
        (and only when their owner has not been given another key)."""
        main = self.iface.mainWindow()
        for act in self.table_keys:
            try:
                main.removeAction(act)
                act.deleteLater()
            except RuntimeError:
                pass
        self.table_keys = []
        mgr = self._shortcuts_manager()
        try:
            taken = json.loads(settings.raw("explorer/takenKeys", "{}") or "{}")
        except (TypeError, ValueError):
            taken = {}
        for seq, name in taken.items():
            owner = mgr.actionByName(name) if mgr is not None else None
            if owner is not None and owner.shortcut().isEmpty():
                mgr.setKeySequence(owner, seq)
        settings.set_raw("explorer/takenKeys", "{}")

    def open_explorer(self, layer=None, scope=None):
        """Open (or raise) the explorer of *layer* (default: active layer);
        *scope* ('all', 'selected', 'map') sets its starting View scope."""
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
            if scope:
                host.panel.set_view_scope(scope)
            return host.panel

        from .ui.attribute_explorer import ExplorerPanel
        from .ui.tool_host import ToolHost
        panel = ExplorerPanel(self.iface, layer, self._shortcut_text())
        if scope and scope != "all":
            panel.set_view_scope(scope)
        host = ToolHost(self.iface, panel, "explorer", icons.tool(icons.EXPLORER),
                        QSize(1600, 820))
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
        if not panel.confirm_close("switch"):
            panel.revert_combo()
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
            SUB.release(lid)
        if self.pivot_host is not None:
            panel = self.pivot_host.panel
            if panel.layer is not None and panel.layer.id() in layer_ids:
                panel.set_layer(None)

    def _restore_forgotten_filters(self, layers):
        """Projects saved by earlier versions while an explorer's map filter
        was on: give those layers their own filter back (the map filter only
        lives while the explorer is open)."""
        for layer in layers:
            if isinstance(layer, QgsVectorLayer) and SUB.restore_forgotten(layer):
                QgsMessageLog.logMessage(
                    f"Restored the original filter of '{layer.name()}' "
                    "(an Attribute Explorer map filter was saved with the "
                    "project)", PLUGIN_NAME, MSG_INFO)

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
        if SettingsDialog(self.iface.mainWindow()).exec():
            self.apply_table_keys()

    def show_help(self):
        from .ui.help_dialog import HelpDialog
        dlg = HelpDialog(self.iface.mainWindow())
        dlg.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dlg.show()
