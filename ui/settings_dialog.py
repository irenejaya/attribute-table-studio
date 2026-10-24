# -*- coding: utf-8 -*-
"""GeoTable Studio settings (Plugins > GeoTable Studio > Settings)."""

from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QLabel, QSpinBox, QVBoxLayout,
)

from ..core import settings
from . import icons
from . import widgets as W
from .theme import Metrics, Theme, stylesheet


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("GeoTable Studio — Settings")
        self.setWindowIcon(icons.native(icons.SETTINGS))
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        theme, m = Theme(), Metrics(self.font())
        self.setStyleSheet(stylesheet(theme, m))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(m.margin + 4, m.margin + 4, m.margin + 4, m.margin)
        lay.setSpacing(10)

        # ---- windows ----
        win = QGroupBox("Windows")
        wl = QVBoxLayout(win)
        note = QLabel("Every tool opens in its own window unless ticked below. "
                      "The Dock button in each tool switches at any time.")
        note.setObjectName("gtsDim")
        note.setWordWrap(True)
        wl.addWidget(note)
        self.dock = {}
        for key, text, icon in (
                ("explorer", "Open Attribute Explorer docked", icons.EXPLORER),
                ("pivot", "Open Pivot Table docked", icons.PIVOT),
                ("compare", "Open Geometry and Table Compare docked",
                 icons.COMPARE)):
            row = QHBoxLayout()
            lbl = QLabel()
            lbl.setPixmap(icons.tool(icon).pixmap(m.icon, m.icon))
            row.addWidget(lbl)
            cb = QCheckBox(text)
            row.addWidget(cb, 1)
            wl.addLayout(row)
            self.dock[key] = cb
        lay.addWidget(win)

        # ---- explorer ----
        exp = QGroupBox("Attribute Explorer")
        ef = QFormLayout(exp)
        self.summary_cb = QCheckBox("Show column statistics when opening")
        ef.addRow(self.summary_cb)
        self.delay = QSpinBox()
        self.delay.setRange(0, 3000)
        self.delay.setSingleStep(50)
        self.delay.setSuffix(" ms")
        self.delay.setFixedHeight(m.control_h)
        self.delay.setToolTip("How long to wait after typing before a filter "
                              "is applied (Enter applies at once)")
        ef.addRow("Filter delay while typing", self.delay)
        lay.addWidget(exp)

        # ---- pivot ----
        piv = QGroupBox("Pivot Table")
        pf = QFormLayout(piv)
        self.cell_limit = QSpinBox()
        self.cell_limit.setRange(1000, 10000000)
        self.cell_limit.setSingleStep(10000)
        self.cell_limit.setGroupSeparatorShown(True)
        self.cell_limit.setFixedHeight(m.control_h)
        self.cell_limit.setToolTip("Builds that would produce more cells stop "
                                   "and ask for fewer fields or a filter")
        pf.addRow("Result limit (cells)", self.cell_limit)
        self.decimals = QSpinBox()
        self.decimals.setRange(0, 10)
        self.decimals.setFixedHeight(m.control_h)
        self.decimals.setToolTip("Display rounding only; calculations and "
                                 "exports keep full precision")
        pf.addRow("Decimals shown", self.decimals)
        lay.addWidget(piv)

        # ---- buttons ----
        row = QHBoxLayout()
        self.shortcuts_btn = W.button(m, "Keyboard shortcuts…",
                                      icons.native(icons.SETTINGS),
                                      "Assign shortcuts to the three tools")
        self.shortcuts_btn.clicked.connect(self._shortcuts)
        row.addWidget(self.shortcuts_btn)
        row.addStretch()
        lay.addLayout(row)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                               | QDialogButtonBox.StandardButton.Cancel
                               | QDialogButtonBox.StandardButton.RestoreDefaults)
        box.accepted.connect(self._save)
        box.rejected.connect(self.reject)
        box.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(
            self._defaults)
        lay.addWidget(box)
        self._load()

    def _load(self, defaults=False):
        get = (lambda k: settings.DEFAULTS[k]) if defaults else settings.value
        for key, cb in self.dock.items():
            cb.setChecked(get(f"{key}/openDocked"))
        self.summary_cb.setChecked(get("explorer/summaryVisible"))
        self.delay.setValue(get("explorer/filterDelayMs"))
        self.cell_limit.setValue(get("pivot/cellLimit"))
        self.decimals.setValue(get("pivot/decimals"))

    def _defaults(self):
        self._load(defaults=True)

    def _save(self):
        for key, cb in self.dock.items():
            settings.set_value(f"{key}/openDocked", cb.isChecked())
        settings.set_value("explorer/summaryVisible", self.summary_cb.isChecked())
        settings.set_value("explorer/filterDelayMs", self.delay.value())
        settings.set_value("pivot/cellLimit", self.cell_limit.value())
        settings.set_value("pivot/decimals", self.decimals.value())
        self.accept()

    def _shortcuts(self):
        try:
            from qgis.gui import QgsConfigureShortcutsDialog, QgsGui
            dlg = QgsConfigureShortcutsDialog(self, QgsGui.shortcutsManager())
            dlg.exec()
        except Exception:   # pragma: no cover - very old builds
            pass
