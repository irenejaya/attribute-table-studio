# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
Window <-> dock host for a tool panel (like the native attribute table).

A tool is one QWidget *panel*.  :class:`ToolHost` shows it either in its own
top-level window (own taskbar entry, remembers size and position) or in a
``QgsDockWidget`` inside the QGIS main window.  The panel's Dock button
switches between the two by moving the same panel object, so filters,
results and selections survive the switch.  Closing the window or the dock
closes the tool.

Panel contract: attribute ``dock_btn`` (checkable QToolButton) and methods
``shutdown()`` (cancel work, disconnect signals) and ``window_title()``.
An optional ``confirm_close()`` can veto a close by the user (unsaved
layer edits: Save and close / Close and keep layer edits / Cancel);
programmatic closes (plugin unload, layer removed) never ask.
"""

import traceback

from qgis.PyQt.QtCore import QObject, QSize, Qt, QTimer, pyqtSignal
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget
from qgis.core import QgsMessageLog
from qgis.gui import QgsDockWidget

from ..core import settings
from ..core.compat import MSG_WARNING

APP_TITLE = "Attribute Table Studio"


class _ToolWindow(QWidget):
    closing = pyqtSignal()

    def __init__(self):
        super().__init__(None)            # top level -> own taskbar entry
        self.setWindowFlags(Qt.WindowType.Window)
        self.suppress_close_signal = False
        self.can_close = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

    def closeEvent(self, event):
        if not self.suppress_close_signal:
            if self.can_close is not None and not self.can_close():
                event.ignore()
                return
            self.closing.emit()
        super().closeEvent(event)


class _ToolDock(QgsDockWidget):
    closing = pyqtSignal()

    def __init__(self, title, parent):
        super().__init__(title, parent)
        self.suppress_close_signal = False
        self.can_close = None

    def closeEvent(self, event):
        if not self.suppress_close_signal:
            if self.can_close is not None and not self.can_close():
                event.ignore()
                return
            self.closing.emit()
        super().closeEvent(event)


class ToolHost(QObject):
    """Shows one tool panel as a window or a dock."""

    closed = pyqtSignal(object)           # emits the host

    _serial = 0

    def __init__(self, iface, panel, key, icon, default_size=QSize(1200, 720),
                 dock_area=Qt.DockWidgetArea.BottomDockWidgetArea):
        super().__init__()
        ToolHost._serial += 1
        self.iface = iface
        self.panel = panel
        self.key = key                    # settings key, e.g. "explorer"
        self.icon = icon
        self.default_size = default_size
        self.dock_area = dock_area
        self._window = None
        self._dock = None
        self._dock_name = f"AttributeTableStudio_{key}_{ToolHost._serial}"
        self._closing = False
        panel.dock_btn.toggled.connect(self._on_dock_toggled)

    # ------------------------------------------------------------------
    @property
    def is_docked(self):
        return self._dock is not None

    @property
    def dock_widget(self):
        return self._dock

    def window_title(self):
        full = getattr(self.panel, "full_window_title", None)
        if callable(full):
            return full()
        return f"{APP_TITLE}  |  {self.panel.window_title()}"

    def refresh_title(self):
        if self._window is not None:
            self._window.setWindowTitle(self.window_title())
        if self._dock is not None:
            self._dock.setWindowTitle(self.panel.window_title())

    def open(self, docked=None, cascade=0):
        """Show the tool; *docked* None uses the tool's Settings default.
        *cascade* > 0 offsets the window so several explorers don't stack
        exactly on top of each other."""
        if docked is None:
            docked = settings.value(f"{self.key}/openDocked")
        if docked:
            self._make_dock()
        else:
            self._make_window()
            if cascade and self._window is not None:
                step = 28 * (cascade % 8)
                self._window.move(self._window.pos().x() + step,
                                  self._window.pos().y() + step)
        self._sync_button()

    def show_and_raise(self):
        if self._window is not None:
            if self._window.isMinimized():
                self._window.showNormal()
            self._window.show()
            self._window.raise_()
            self._window.activateWindow()
        elif self._dock is not None:
            self._dock.setUserVisible(True)
            self._dock.raise_()

    def set_docked(self, docked):
        if docked == self.is_docked:
            return
        if docked:
            self._take_from_window()
            self._make_dock()
        else:
            self._take_from_dock()
            self._make_window()
        self._sync_button()
        QTimer.singleShot(0, self.show_and_raise)

    def close(self):
        """Close programmatically (plugin unload, layer removed)."""
        if self._closing:
            return
        self._closing = True
        self._teardown()

    # ------------------------------------------------------------------
    def _on_dock_toggled(self, checked):
        if checked != self.is_docked:
            self.set_docked(checked)

    def _sync_button(self):
        btn = self.panel.dock_btn
        btn.blockSignals(True)
        btn.setChecked(self.is_docked)
        btn.blockSignals(False)
        btn.setToolTip("Undock into a separate window" if self.is_docked
                       else "Dock into the QGIS main window")

    def _make_window(self):
        win = _ToolWindow()
        win.setWindowIcon(self.icon)
        win.layout().addWidget(self.panel)
        self.panel.show()
        win.closing.connect(self._on_user_close)
        win.can_close = getattr(self.panel, "confirm_close", None)
        self._window = win
        self.refresh_title()
        geometry = settings.raw(f"{self.key}/geometry")
        restored = False
        if geometry:
            try:
                restored = win.restoreGeometry(geometry)
            except TypeError:
                restored = False
        if not restored:
            win.resize(self._fit(self.default_size))
            self._center(win)
        win.show()
        win.raise_()
        win.activateWindow()

    def _make_dock(self):
        main = self.iface.mainWindow()
        dock = _ToolDock(self.panel.window_title(), main)
        dock.setObjectName(self._dock_name)
        dock.setWidget(self.panel)
        self.panel.show()
        dock.closing.connect(self._on_user_close)
        dock.can_close = getattr(self.panel, "confirm_close", None)
        siblings = [d.objectName() for d in main.findChildren(QgsDockWidget)
                    if d is not dock and d.objectName().startswith(
                        f"AttributeTableStudio_{self.key}_") and d.isVisible()]
        if siblings and hasattr(self.iface, "addTabifiedDockWidget"):
            self.iface.addTabifiedDockWidget(self.dock_area, dock, siblings, True)
        else:
            self.iface.addDockWidget(self.dock_area, dock)
        self._dock = dock
        dock.show()
        dock.raise_()
        if self.dock_area in (Qt.DockWidgetArea.BottomDockWidgetArea,
                              Qt.DockWidgetArea.TopDockWidgetArea):
            # start tall enough to show the header bands plus some rows
            height = max(380, int(main.height() * 0.45))
            try:
                main.resizeDocks([dock], [height], Qt.Orientation.Vertical)
            except (AttributeError, TypeError):
                pass

    def _take_from_window(self):
        win = self._window
        self._window = None
        if win is None:
            return
        settings.set_raw(f"{self.key}/geometry", win.saveGeometry())
        self.panel.setParent(None)
        win.suppress_close_signal = True
        win.close()
        win.deleteLater()

    def _take_from_dock(self):
        dock = self._dock
        self._dock = None
        if dock is None:
            return
        self.panel.setParent(None)
        dock.suppress_close_signal = True
        self.iface.removeDockWidget(dock)
        dock.deleteLater()

    def _on_user_close(self):
        if self._closing:
            return
        self._closing = True
        QTimer.singleShot(0, self._teardown)

    def _teardown(self):
        if self._window is not None:
            settings.set_raw(f"{self.key}/geometry",
                             self._window.saveGeometry())
        try:
            self.panel.shutdown()
        except Exception:                # never block closing; keep the trace
            QgsMessageLog.logMessage(traceback.format_exc(), "Attribute Table Studio",
                                     MSG_WARNING)
        self._take_from_window()
        self._take_from_dock()
        self.panel.deleteLater()
        self.closed.emit(self)

    @staticmethod
    def _fit(size):
        screen = QApplication.primaryScreen()
        if screen is None:
            return size
        avail = screen.availableGeometry()
        return QSize(min(size.width(), int(avail.width() * 0.92)),
                     min(size.height(), int(avail.height() * 0.88)))

    @staticmethod
    def _center(win):
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        frame = win.frameGeometry()
        frame.moveCenter(avail.center())
        win.move(frame.topLeft())
