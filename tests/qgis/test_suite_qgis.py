# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""End-to-end tests of Attribute Table Studio inside a headless QGIS.

Run with QGIS' Python (QGIS 3.40+ / 4.x):
    QT_QPA_PLATFORM=offscreen python tests/qgis/test_suite_qgis.py
"""

import csv
import math
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

APP = H.start_qgis()
PKG = H.plugin_package()

from qgis.PyQt.QtCore import QPoint, Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor  # noqa: E402
from qgis.PyQt.QtWidgets import (QFileDialog, QMenu, QMessageBox,  # noqa: E402
                                 QTextBrowser)
from qgis.core import (QgsFeatureRequest, QgsProject,  # noqa: E402
                       QgsVectorLayer)
from qgis.gui import QgsDockWidget  # noqa: E402

from attribute_table_studio import classFactory  # noqa: E402
from attribute_table_studio.core import filters as FL  # noqa: E402
from attribute_table_studio.core import fields as F  # noqa: E402
from attribute_table_studio.core import pivot as P  # noqa: E402
from attribute_table_studio.core import subset as SUB  # noqa: E402
from attribute_table_studio.core.compat import QAction, SELECT_SET  # noqa: E402

OUT = tempfile.mkdtemp(prefix="gts_out_")
_next_save = []


def _fake_save(*_args, **_kwargs):
    return (_next_save.pop(0) if _next_save else os.path.join(OUT, "x.csv"), "")


QFileDialog.getSaveFileName = staticmethod(_fake_save)
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)


def _ink_width(btn):
    """Width of what a compact button draws inside its border (the icon),
    menu arrow excluded."""
    img = btn.grab().toImage()
    w, h = img.width(), img.height()
    ratio = img.devicePixelRatio() or 1
    bg = QColor(img.pixel(int(4 * ratio), h // 2))
    right = w - int((16 if btn.menu() is not None else 4) * ratio)
    cols = set()
    for x in range(int(4 * ratio), right):
        for y in range(int(4 * ratio), h - int(4 * ratio)):
            p = QColor(img.pixel(x, y))
            if max(abs(p.red() - bg.red()), abs(p.green() - bg.green()),
                   abs(p.blue() - bg.blue())) > 40:
                cols.add(x)
                break
    return (max(cols) - min(cols) + 1) / ratio if cols else 0


def idle(panel):
    return panel._has_result and not panel.runner.busy


def expected_count(layer, expr):
    req = QgsFeatureRequest().setFilterExpression(expr) if expr else QgsFeatureRequest()
    return sum(1 for _ in layer.getFeatures(req))


class SuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layer = H.demo_layer(n=3000)
        cls.other = H.demo_layer(n=500, seed=3, name="Other parcels")
        cls.iface = H.FakeIface()
        cls.iface.setActiveLayer(cls.layer)
        cls.plugin = classFactory(cls.iface)
        cls.plugin.initGui()

    @classmethod
    def tearDownClass(cls):
        cls.plugin.unload()

    # ------------------------------------------------------------------
    def test_01_toolbar_menu_shortcuts(self):
        tb = self.plugin.toolbar
        texts = [a.text() for a in tb.actions()]
        self.assertEqual(texts, ["Attribute Explorer", "Pivot Table",
                                 "Geometry and Table Compare"])
        menu = [a.text() for _n, a in self.iface.menu_actions if not a.isSeparator()]
        self.assertEqual(menu, ["Attribute Explorer", "Pivot Table",
                                "Geometry and Table Compare", "Settings…", "Help"])
        shortcuts = {a.text(): s for a, s in self.iface.registered}
        self.assertEqual(shortcuts["Attribute Explorer"], "Ctrl+Alt+T")
        self.assertEqual(shortcuts["Pivot Table"], "")
        self.assertEqual(len(self.iface.layer_actions), 1)

    def test_02_explorer_open_and_profiles(self):
        panel = self.plugin.open_explorer()
        self.assertIsNotNone(panel)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), self.layer.featureCount())
        # a second open raises the same window
        self.assertIs(self.plugin.open_explorer(self.layer), panel)
        self.assertEqual(len(self.plugin.explorers), 1)
        # every shown column has a profile that reconciles with the table
        self.assertTrue(H.wait_for(lambda: len(panel.profiles) ==
                                   len(panel.header.visible_logicals())))
        for logical, prof in panel.profiles.items():
            self.assertEqual(prof.count, len(panel.model.fids))
            self.assertEqual(prof.missing + prof.non_null, prof.count)
        idx = self.layer.fields().indexOf("OWNER_ID")
        self.assertEqual(panel.profiles[idx].missing, self.layer.featureCount())
        # table values come from the layer
        first = next(self.layer.getFeatures(QgsFeatureRequest().setFilterFid(
            panel.model.fids[0])))
        lu = self.layer.fields().indexOf("LAND_USE")
        self.assertEqual(panel.model.raw_value(0, lu), first["LAND_USE"])

    def test_03_explorer_filters(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        fields = self.layer.fields()
        lu = fields.indexOf("LAND_USE")
        fl = fields.indexOf("SCORE_MAX")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "RESIDENTIAL"))
        panel.header.cells[fl].set_filter(
            FL.ColumnFilter("SCORE_MAX", F.REAL, "gt", "60"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        expr = "\"LAND_USE\" = 'RESIDENTIAL' AND \"SCORE_MAX\" > 60"
        n = expected_count(self.layer, expr)
        self.assertGreater(n, 0)
        self.assertEqual(len(panel.model.fids), n)
        self.assertTrue(H.wait_for(lambda: fl in panel.profiles))
        self.assertEqual(panel.profiles[fl].count, n)
        self.assertGreater(panel.profiles[fl].minimum, 60)
        self.assertIn("2 filters: LAND_USE, SCORE_MAX", panel.status.message.fullText())
        self.assertIn("2 active filters", panel.scope_description())
        # invalid input keeps the last valid result
        before = list(panel.model.fids)
        panel.header.cells[fl].edit.setText("abc")
        panel.refresh()
        H.settle(0.2)
        self.assertEqual(panel.model.fids, before)
        self.assertEqual(panel.header.cells[fl].property("state"), "error")
        panel.clear_filters()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), self.layer.featureCount())

    def test_03b_not_filters(self):
        from qgis.PyQt.QtWidgets import QApplication, QLabel
        panel = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        layer = self.layer
        cell = panel.header.cells[layer.fields().indexOf("LAND_USE")]
        # each "not" operator sits under its positive twin, with its symbol
        self.assertEqual(list(cell._op_actions)[:7], [
            "contains", "ncontains", "eq", "ne", "starts", "in", "notin"])
        self.assertEqual(cell._op_actions["notin"].text(), "is not one of")
        self.assertFalse(cell._op_actions["ncontains"].icon().isNull())

        def shown():
            self.assertTrue(H.wait_for(lambda: idle(panel)))
            return len(panel.model.fids)
        keep_null = ' OR "LAND_USE" IS NULL'
        cell.set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "ne", "RESIDENTIAL"),
                        emit=True)
        self.assertEqual(shown(), expected_count(
            layer, "\"LAND_USE\" <> 'RESIDENTIAL'" + keep_null))
        self.assertEqual(cell.op_btn.text(), "≠")
        cell.set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "ncontains", "resid"),
                        emit=True)
        self.assertEqual(shown(), expected_count(
            layer, "NOT (\"LAND_USE\" ILIKE '%resid%')" + keep_null))
        # values picked from a "not" operator are left out
        cell._on_values_applied(["RESIDENTIAL", "INDUSTRIAL"])
        self.assertEqual(cell.op, "notin")
        self.assertEqual(shown(), expected_count(
            layer, "\"LAND_USE\" NOT IN ('RESIDENTIAL', 'INDUSTRIAL')" + keep_null))
        cell._pick_values()
        H.settle(0.2)
        picker = [w for w in QApplication.topLevelWidgets()
                  if type(w).__name__ == "ValuePicker" and w.isVisible()][-1]
        self.assertTrue(any("leave out" in lbl.text()
                            for lbl in picker.findChildren(QLabel)))
        picker.close()
        # from "is one of" the value list still keeps the values
        cell.set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "contains", "x"))
        cell._on_values_applied(["RESIDENTIAL"])
        self.assertEqual(cell.op, "in")
        cell.clear(emit=True)
        self.assertEqual(shown(), layer.featureCount())

    def test_03c_columns_window_selection_and_icon(self):
        from qgis.PyQt.QtCore import QItemSelectionModel
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QAbstractItemView, QToolButton
        from attribute_table_studio.ui import icons, widgets as W
        on, off = Qt.CheckState.Checked, Qt.CheckState.Unchecked
        entries = [(n, icons.native(icons.COLUMNS), "", True)
                   for n in ("A", "B", "C", "D", "E")]
        from qgis.PyQt.QtWidgets import QApplication
        from attribute_table_studio.ui.theme import Metrics, Theme
        dlg = W.FieldChecklistDialog(entries, Metrics(QApplication.font()), Theme(), "Columns",
                                     movable=True, icon=icons.native(icons.COLUMNS))
        lst = dlg.listw
        # the window carries the Columns button's icon
        self.assertEqual(dlg.windowIcon().pixmap(16, 16).toImage(),
                         icons.native(icons.COLUMNS).pixmap(16, 16).toImage())
        # Shift / Ctrl select several rows
        self.assertEqual(lst.selectionMode(),
                         QAbstractItemView.SelectionMode.ExtendedSelection)
        sel = lst.selectionModel()
        flags = QItemSelectionModel.SelectionFlag
        lst.setCurrentRow(1, flags.ClearAndSelect)
        for r in (2, 3):
            sel.select(lst.model().index(r, 0), flags.Select)
        # Space unticks (or ticks) every selected row like the current one
        lst.setFocus()
        QTest.keyClick(lst, Qt.Key.Key_Space)
        self.assertEqual([lst.item(i).checkState() for i in range(5)],
                         [on, off, off, off, on])
        # Toggle Selection inverts the selected rows ...
        toggle = next(b for b in dlg.findChildren(QToolButton)
                      if b.text().strip() == "Toggle Selection")
        lst.item(2).setCheckState(on)
        toggle.click()
        self.assertEqual([lst.item(i).checkState() for i in range(5)],
                         [on, on, off, on, on])
        # ... or every listed row when none is selected
        lst.clearSelection()
        toggle.click()
        self.assertEqual([lst.item(i).checkState() for i in range(5)],
                         [off, off, on, off, off])
        dlg.close()

    def test_04_selection_sync_and_selected_only(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        self.layer.removeSelection()
        panel._on_row_pressed(0, Qt.KeyboardModifier.NoModifier)
        self.assertEqual(self.layer.selectedFeatureIds(), [panel.model.fids[0]])
        panel._on_row_pressed(4, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(sorted(self.layer.selectedFeatureIds()),
                         sorted(panel.model.fids[0:5]))
        panel._on_row_pressed(2, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(self.layer.selectedFeatureIds()), 4)
        # map selection -> table
        self.layer.selectByIds(panel.model.fids[10:13])
        H.settle(0.1)
        self.assertEqual(panel.model.selected, set(panel.model.fids[10:13]))
        panel.selected_btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(sorted(panel.model.fids),
                         sorted(self.layer.selectedFeatureIds()))
        self.layer.removeSelection()
        H.wait_for(lambda: idle(panel) and len(panel.model.fids) == 0, 10)
        self.assertEqual(len(panel.model.fids), 0)       # empty selection -> 0
        panel.selected_btn.setChecked(False)
        self.assertTrue(H.wait_for(lambda: idle(panel)))

    def test_05_sort(self):
        panel = self.plugin.open_explorer(self.layer)
        fl = self.layer.fields().indexOf("SCORE_MAX")
        panel._on_sort_clicked(fl)            # ascending
        panel._on_sort_clicked(fl)            # descending
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        top = panel.model.raw_value(0, fl)
        self.assertEqual(top, max(f["SCORE_MAX"] for f in self.layer.getFeatures()))
        sd = self.layer.fields().indexOf("SURVEY_DATE")
        panel._on_sort_clicked(sd)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertTrue(F.is_null(panel.model.raw_value(len(panel.model.fids) - 1, sd)))
        panel._on_sort_clicked(sd)
        panel._on_sort_clicked(sd)            # third click: back to id order
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(panel.model.fids, sorted(panel.model.fids))

    def test_06_dock_round_trip_keeps_state(self):
        panel = self.plugin.open_explorer(self.layer)
        host = self.plugin.explorers[self.layer.id()]
        lu = self.layer.fields().indexOf("LAND_USE")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "INDUSTRIAL"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        n = len(panel.model.fids)
        panel.dock_btn.click()
        H.settle(0.2)
        self.assertTrue(host.is_docked)
        docks = self.iface.mainWindow().findChildren(QgsDockWidget)
        self.assertTrue(any(d.widget() is panel for d in docks))
        self.assertEqual(len(panel.model.fids), n)
        self.assertTrue(panel.dock_btn.isChecked())
        panel.dock_btn.click()
        H.settle(0.2)
        self.assertFalse(host.is_docked)
        self.assertTrue(host._window.isVisible())
        self.assertEqual(len(panel.model.fids), n)
        panel.clear_filters()
        self.assertTrue(H.wait_for(lambda: idle(panel)))

    def test_07_layout_persists_per_layer(self):
        panel = self.plugin.open_explorer(self.layer)
        h = panel.header
        zc = self.layer.fields().indexOf("ZONE_CODE")
        h.setSectionHidden(zc, True)
        h.resizeSection(1, 222)
        panel._save_layout()
        self.plugin.explorers[self.layer.id()].close()
        H.settle(0.2)
        self.assertNotIn(self.layer.id(), self.plugin.explorers)
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        self.assertTrue(panel.header.isSectionHidden(zc))
        self.assertEqual(panel.header.sectionSize(1), 222)
        panel.header.setSectionHidden(zc, False)
        panel._save_layout()

    def test_08_live_edits_refresh(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        lu = self.layer.fields().indexOf("LAND_USE")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "RURAL"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        before = len(panel.model.fids)
        target = next(f for f in self.layer.getFeatures()
                      if f["LAND_USE"] == "RESIDENTIAL")
        self.layer.startEditing()
        self.layer.changeAttributeValue(target.id(), lu, "RURAL")
        self.assertTrue(H.wait_for(
            lambda: idle(panel) and len(panel.model.fids) == before + 1, 10))
        self.layer.rollBack()
        self.assertTrue(H.wait_for(
            lambda: idle(panel) and len(panel.model.fids) == before, 10))
        panel.clear_filters()
        H.wait_for(lambda: idle(panel))

    def test_09_explorer_export(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        path = os.path.join(OUT, "explorer.csv")
        _next_save.append(path)
        panel.export("csv")
        self.assertTrue(H.wait_for(lambda: not panel.runner.busy, 30))
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(len(rows) - 1, len(panel.model.fids))
        self.assertTrue(os.path.exists(os.path.join(OUT, "explorer_scope.txt")))
        xpath = os.path.join(OUT, "explorer.xlsx")
        _next_save.append(xpath)
        panel.export("xlsx")
        self.assertTrue(H.wait_for(lambda: not panel.runner.busy, 30))
        self.assertTrue(os.path.getsize(xpath) > 1000)

    def test_10_switch_layer(self):
        panel = self.plugin.open_explorer(self.layer)
        self.plugin._switch_explorer_layer(panel, self.other)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), self.other.featureCount())
        self.assertIn(self.other.id(), self.plugin.explorers)
        self.plugin._switch_explorer_layer(panel, self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))

    def test_11_pivot_from_explorer_and_drill(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        fl = self.layer.fields().indexOf("SCORE_MAX")
        panel.header.cells[fl].set_filter(
            FL.ColumnFilter("SCORE_MAX", F.REAL, "ge", "50"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        panel._open_pivot()
        pivot = self.plugin.pivot_host.panel
        self.assertEqual(pivot.input["kind"], "explorer")
        self.assertEqual(len(pivot.input["fids"]), len(panel.model.fids))
        pivot.set_spec(P.PivotSpec(["LAND_USE"], [], [
            P.Measure("SCORE_MAX", "mean"), P.Measure(None, "count")]))
        pivot.build()
        self.assertTrue(H.wait_for(lambda: pivot.model.result is not None
                                   and not pivot.runner.busy, 30))
        result = pivot.model.result
        vals = [f["SCORE_MAX"] for f in self.layer.getFeatures(
            QgsFeatureRequest().setFilterExpression(
                "\"LAND_USE\" = 'RESIDENTIAL' AND \"SCORE_MAX\" >= 50"))]
        self.assertTrue(math.isclose(result.value(("RESIDENTIAL",), (), 0),
                                     sum(vals) / len(vals)))
        self.assertEqual(result.record_count(P.TOTAL, P.TOTAL), len(panel.model.fids))
        self.assertIsNone(pivot._stale_reason())
        # drill-through opens the records of one group
        row = [r for r, k in enumerate(result.row_keys) if k == ("RESIDENTIAL",)][0]
        pivot._drill(pivot.model.index(row, 0))
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), len(vals))
        self.assertIsNotNone(panel.external)
        # explorer filter change marks the pivot input stale
        panel.clear_filters()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(pivot._stale_reason(), "input")
        pivot.refresh_input()
        self.assertEqual(len(pivot.input["fids"]), self.layer.featureCount())

    def test_12_pivot_cross_tab_layouts_export(self):
        pivot = self.plugin.open_pivot(self.layer)
        pivot._capture("all")
        pivot.set_spec(P.PivotSpec(["LAND_USE"], ["ZONE_CODE"], [
            P.Measure("SCORE_MAX", "mean")]),
            [FL.ColumnFilter("IS_VACANT", F.BOOL, "true")])
        pivot.build()
        self.assertTrue(H.wait_for(lambda: not pivot.runner.busy, 30))
        r = pivot.model.result
        self.assertTrue(r.has_cols)
        n_true = expected_count(self.layer, '"IS_VACANT" = TRUE')
        self.assertEqual(r.n_input, n_true)
        # stale after an edit
        self.layer.startEditing()
        f = next(self.layer.getFeatures())
        self.layer.changeAttributeValue(f.id(), 3, "OTHER")
        H.settle(0.1)
        self.assertEqual(pivot._stale_reason(), "input")
        self.layer.rollBack()
        # layouts
        recipe = pivot._recipe()
        pivot.set_spec(P.PivotSpec(["ZONE_CODE"], [], [P.Measure(None, "count")]))
        pivot._apply_recipe(recipe)
        self.assertEqual(pivot.spec().cols, ["ZONE_CODE"])
        self.assertEqual(len(pivot.filters), 1)
        # exports
        pivot.refresh_input()
        pivot.build()
        self.assertTrue(H.wait_for(lambda: not pivot.runner.busy, 30))
        for fmt in ("csv", "xlsx", "gpkg"):
            path = os.path.join(OUT, f"pivot.{fmt}")
            _next_save.append(path)
            pivot.export(fmt)
            self.assertTrue(os.path.exists(path), fmt)
        lyr = QgsVectorLayer(os.path.join(OUT, "pivot.gpkg"), "p", "ogr")
        self.assertTrue(lyr.isValid())
        names = [s.split("!!::!!")[1] for s in lyr.dataProvider().subLayers()]
        self.assertEqual(len(names), 2)

    def test_12b_pivot_list_distinct_and_values_row(self):
        pivot = self.plugin.open_pivot(self.layer)
        pivot._capture("all")
        pivot.set_spec(P.PivotSpec(["ZONE_CODE"], [], [
            P.Measure("LAND_USE", "list_distinct"), P.Measure(None, "count")]))
        pivot.build()
        self.assertTrue(H.wait_for(lambda: not pivot.runner.busy, 30))
        model = pivot.model
        col = next(c for c in range(model.columnCount()) if model.is_text(c))
        # each zone lists its land uses once, A to Z (NULL left out)
        uses = {}
        for f in self.layer.getFeatures():
            if not F.is_null(f["LAND_USE"]) and not F.is_null(f["ZONE_CODE"]):
                uses.setdefault(f["ZONE_CODE"], set()).add(f["LAND_USE"])
        for row in range(model.rowCount() - 1):
            zone = model.cell(row, 0)["value"]
            if zone in uses:
                self.assertEqual(model.text(row, col),
                                 ", ".join(sorted(uses[zone], key=str.lower)))
        self.assertEqual(model.data(model.index(0, col), Qt.ItemDataRole.TextAlignmentRole),
                         model.data(model.index(0, 0), Qt.ItemDataRole.TextAlignmentRole))
        # the GeoPackage keeps the list as text
        path = os.path.join(OUT, "pivot_list.gpkg")
        _next_save.append(path)
        pivot.export("gpkg")
        source = QgsVectorLayer(path, "p", "ogr")
        names = [s.split("!!::!!")[1] for s in source.dataProvider().subLayers()]
        table = next(n for n in names if n.endswith("_pivot"))
        lyr = QgsVectorLayer(f"{path}|layername={table}", "p", "ogr")
        listed = [f for f in lyr.fields() if f.name().lower().startswith("list")]
        self.assertEqual(len(listed), 1)
        self.assertEqual(F.field_kind(listed[0]), F.TEXT)
        # the Values row: operator (with its symbol and explanation) above
        # the field; Count records needs no field
        row = pivot.measure_rows[0]
        top = row.layout().itemAt(0).layout()
        self.assertIs(top.itemAt(0).widget(), row.agg)
        self.assertFalse(row.agg.itemIcon(row.agg.findData("sum")).isNull())
        self.assertIn("different values", row.agg.itemData(
            row.agg.findData("list_distinct"), Qt.ItemDataRole.ToolTipRole))
        count_row = pivot.measure_rows[1]
        self.assertFalse(count_row.field.isEnabled())
        self.assertIsNone(count_row.measure().field)
        # from Count records to Sum: a number field is picked for you
        count_row.agg.setCurrentIndex(count_row.agg.findData("sum"))
        self.assertTrue(count_row.field.isEnabled())
        self.assertIn(count_row.field.kind(), F.NUMERIC)

    def test_12c_pivot_chart(self):
        """The chart icon of the Pivot Table: one bar per row of the built
        pivot, the columns as coloured parts; clicks select records."""
        from attribute_table_studio.core import pivot_chart as PC
        pivot = self.plugin.open_pivot(self.other)
        self.assertFalse(pivot.chart_btn.isEnabled())       # nothing built yet
        pivot.set_layer(self.layer)
        pivot._capture("all")
        pivot.set_spec(P.PivotSpec(["LAND_USE"], ["ZONE_CODE"], [
            P.Measure("SCORE_MAX", "sum"), P.Measure("SCORE_MAX", "mean")]))
        pivot.build()
        self.assertTrue(H.wait_for(lambda: pivot.model.result is not None
                                   and not pivot.runner.busy, 30))
        self.assertTrue(pivot.chart_btn.isEnabled())
        pivot.chart_btn.click()
        chart = pivot.chart_host.panel
        chart.window().resize(1180, 700)
        H.settle(0.3)
        result = pivot.model.result
        self.assertEqual(len(chart.bars), result.group_count)
        self.assertEqual([chart.value_combo.itemText(i)
                          for i in range(chart.value_combo.count())],
                         ["Sum SCORE_MAX", "Mean SCORE_MAX", "Records"])
        self.assertEqual(chart.bars.series, [P.key_label(ck) for ck in result.col_keys])
        self.assertEqual(chart.split_mode.currentData(), "stacked")
        self.assertIn("Sum SCORE_MAX by LAND_USE", chart.title_edit.text())
        # the bars follow the table's order; sorting the table reorders them
        self.assertEqual(chart.bars.row_keys, [r[0]["rk"] for r in pivot.model.rows])
        sum_col = next(c for c, h in enumerate(pivot.model.header)
                       if h["kind"] == "value" and h["ck"] is P.TOTAL and h["measure"] == 0)
        pivot.model.sort(sum_col, Qt.SortOrder.DescendingOrder)
        H.settle(0.1)
        self.assertEqual(chart.bars.values, sorted(chart.bars.values, reverse=True))
        # a mean does not add up: side by side only
        chart.value_combo.setCurrentIndex(1)
        self.assertEqual(chart.split_mode.currentData(), "side")
        stacked = chart.split_mode.model().item(chart.split_mode.findData("stacked"))
        self.assertFalse(stacked.isEnabled())
        chart.value_combo.setCurrentIndex(0)
        self.assertTrue(stacked.isEnabled())
        # a click on a bar selects its records; the bar is drawn selected
        res_row = next(i for i, rk in enumerate(chart.bars.row_keys) if rk == ("RESIDENTIAL",))
        chart.view.barsPicked.emit([res_row], False, None)
        H.settle(0.3)
        want = set(result.fids(("RESIDENTIAL",), P.TOTAL))
        self.assertEqual(set(self.layer.selectedFeatureIds()), want)
        self.assertEqual(chart.sel_records[res_row], len(want))
        self.assertEqual(chart.sel[res_row], chart.bars.counts[res_row])
        # a part of a bar, then a whole column of the pivot from the legend
        chart.view.barsPicked.emit([res_row], False, 0)
        H.settle(0.3)
        ck = chart.bars.series_keys[0][0]
        self.assertEqual(set(self.layer.selectedFeatureIds()),
                         set(result.fids(("RESIDENTIAL",), ck)))
        chart.view.seriesPicked.emit(0, False)
        H.settle(0.3)
        self.assertEqual(set(self.layer.selectedFeatureIds()),
                         set(result.fids(P.TOTAL, ck)))
        chart._clear_selection()
        H.settle(0.3)
        self.assertEqual(self.layer.selectedFeatureCount(), 0)
        # hovering shows the row's numbers
        title, lines, _foot = chart.view.card_lines(res_row)
        self.assertEqual(title, "RESIDENTIAL")
        self.assertTrue(any(text.startswith("Total: ") for _c, text, _s in lines))
        # the drill-through of the table, from a right-click on a bar
        chart.inspect(res_row)
        explorer = self.plugin.explorer_for(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(explorer)))
        self.assertEqual(set(explorer.model.fids), want)
        explorer.clear_external_scope()
        # bars as CSV
        path = os.path.join(OUT, "pivot_chart.csv")
        self.assertTrue(chart.write_csv(path))
        with open(path, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(rows[0][:4], ["LAND_USE", "Sum SCORE_MAX", "Records", "Selected"])
        self.assertEqual(len(rows), len(chart.bars) + 1)
        # an edit pauses selecting until the pivot is built again
        self.layer.startEditing()
        f = next(self.layer.getFeatures())
        self.layer.changeAttributeValue(f.id(), self.layer.fields().indexOf("SCORE_MAX"), 1.0)
        H.settle(0.1)
        self.assertTrue(chart._data_changed())
        chart.view.barsPicked.emit([res_row], False, None)
        H.settle(0.2)
        self.assertEqual(self.layer.selectedFeatureCount(), 0)
        self.assertIn("press Build Pivot", chart.status.message.fullText())
        self.layer.rollBack()
        # a new build: the chart follows (one row field, no columns)
        pivot.refresh_input()
        pivot.set_spec(P.PivotSpec(["ZONE_CODE"], [], [P.Measure(None, "count")]))
        pivot.build()
        self.assertTrue(H.wait_for(lambda: not pivot.runner.busy, 30))
        H.settle(0.2)
        self.assertIsNone(chart.bars.split)
        self.assertEqual(chart.value_combo.count(), 1)       # Records is the measure
        self.assertEqual(sum(chart.bars.values), self.layer.featureCount())
        self.assertFalse(chart.split_mode_row.isVisibleTo(chart))
        # another layer: nothing built for it yet
        pivot.set_layer(self.other)
        H.settle(0.1)
        self.assertIsNone(chart.bars)
        self.assertIn("Build the pivot", chart.view.message)
        # closing the pivot closes its chart
        self.plugin.pivot_host.close()
        H.settle(0.1)
        self.assertIsNone(pivot.chart_host)
        self.assertEqual(PC.SORTS[0], "table")

    def test_12d_pivot_chart_negative_values(self):
        """Values below 0: columns hang below the 0 line, a linear axis."""
        from qgis.core import QgsFeature
        lyr = QgsVectorLayer("None?field=DISTRICT:string&field=CHANGE:double",
                             "Made-up changes", "memory")
        feats = []
        for k, (d, v) in enumerate([("East", 4.0), ("East", 2.5), ("West", -6.0),
                                    ("West", 1.0), ("North", -0.5)]):
            f = QgsFeature(lyr.fields())
            f.setAttributes([d, v])
            feats.append(f)
        lyr.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(lyr)
        try:
            pivot = self.plugin.open_pivot(lyr)
            pivot._capture("all")
            pivot.set_spec(P.PivotSpec(["DISTRICT"], [], [P.Measure("CHANGE", "sum")]))
            pivot.build()
            self.assertTrue(H.wait_for(lambda: pivot.model.result is not None
                                       and not pivot.runner.busy, 30))
            chart = pivot.open_chart()
            chart.window().resize(1000, 600)
            chart.scale_combo.setCurrentIndex(chart.scale_combo.findData("linear"))
            chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("count"))
            H.settle(0.3)
            self.assertEqual(chart.bars.labels, ["East", "North", "West"])
            self.assertEqual(chart.bars.values, [6.5, -0.5, -5.0])
            self.assertTrue(chart.bars.negative)
            log = chart.scale_combo.model().item(chart.scale_combo.findData("log"))
            self.assertFalse(log.isEnabled())
            view = chart.view
            plot = view._geo["plot"]
            west = next(r for r, i, s in view._geo["bars"] if i == 2 and s is None)
            east = next(r for r, i, s in view._geo["bars"] if i == 0 and s is None)
            self.assertLess(east.top(), west.top())            # up, then down from 0
            self.assertGreater(west.bottom(), east.bottom())
            self.assertGreater(west.bottom(), plot.center().y())
            self.assertEqual(view.hit(QPoint(int(west.center().x()),
                                             int(west.bottom() - 2)))[0], 2)
            chart.view.barsPicked.emit([2], False, None)
            H.settle(0.2)
            self.assertEqual(lyr.selectedFeatureCount(), 2)
            chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("bar"))
            H.settle(0.3)
            west = next(r for r, i, s in view._geo["bars"] if i == 2 and s is None)
            east = next(r for r, i, s in view._geo["bars"] if i == 0 and s is None)
            self.assertLess(west.left(), east.left())          # left of the 0 line
            self.plugin.pivot_host.close()
        finally:
            QgsProject.instance().removeMapLayer(lyr.id())

    def test_13_compare(self):
        base = H.demo_layer(n=800, name="Parcels baseline")
        new = H.demo_updated_layer(base)
        panel = self.plugin.open_compare()
        self.assertIs(self.plugin.open_compare(), panel)
        panel.old_table_combo.setLayer(base)
        panel.new_table_combo.setLayer(new)
        panel.join_field_combo.setCurrentIndex(
            panel.join_field_combo.findText("PARCEL_ID"))
        panel.compare_tables()
        counts = panel._compare_info["counts"]
        self.assertEqual(counts["Added"], 12)
        self.assertGreater(counts["Modified"], 0)
        self.assertEqual(panel.filter_counts["Added"].text(), "· 12")
        self.assertEqual(panel.filter_boxes["Added"].text(), "Added")
        path = os.path.join(OUT, "compare.csv")
        _next_save.append(path)
        panel.export_results()
        self.assertTrue(os.path.exists(path))
        host = self.plugin.compare_host
        self.assertEqual(host.window_title(), "Geometry and Table Compare")

    def test_13b_round2_ui_rules(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        fl = self.layer.fields().indexOf("SCORE_MAX")
        cell = panel.header.cells[fl]
        texts = [a.text() for a in cell.op_menu.actions()
                 if not a.isSeparator() and a.data()]
        # each operator once, as words (the symbol is the icon)
        self.assertEqual(texts[:6], ["equal to", "not equal to", "less than",
                                     "less than or equal to", "greater than",
                                     "greater than or equal to"])
        self.assertTrue(all(not a.icon().isNull() for a in cell.op_menu.actions()
                            if a.data()))
        # no ellipsis in any filter hint
        for c in panel.header.cells.values():
            for op_id, _label in FL.operators(c.kind):
                if op_id in FL.NO_VALUE_OPS:
                    continue
                c.set_op(op_id)
                self.assertNotIn("…", c.edit.placeholderText())
                self.assertNotIn("…", c.edit2.placeholderText())
            c.set_filter(None)
        # only Compare has a progress bar; Explorer / Pivot show Cancel only
        self.assertFalse(hasattr(panel.status, "progress_bar"))
        compare = self.plugin.open_compare()
        self.assertTrue(hasattr(compare, "progress_bar"))
        # Compare's bar is QGIS's own (no style sheet rule), "XX%" centred
        bar = compare.progress_bar
        self.assertNotIn("QProgressBar", compare.styleSheet())
        self.assertEqual(bar.format(), "%p%")
        self.assertTrue(bar.alignment() & Qt.AlignmentFlag.AlignHCenter)
        self.assertNotEqual(bar.minimumHeight(), bar.maximumHeight())
        # every panel keeps the status bar inside the window
        for p in (panel, compare):
            self.assertEqual(p.layout().contentsMargins().bottom(), p.metrics.margin)
        # pivot filters show as chips
        pivot = self.plugin.open_pivot(self.layer)
        pivot.set_spec(P.PivotSpec(["LAND_USE"], [], [P.Measure(None, "count")]),
                       [FL.ColumnFilter("ZONE_CODE", F.INTEGER, "gt", "2"),
                        FL.ColumnFilter("IS_VACANT", F.BOOL, "true")])
        self.assertEqual(pivot.chips_flow.count(), 2)
        self.assertFalse(pivot.filters_empty.isVisibleTo(pivot))
        pivot._remove_filter(pivot.filters[0])
        self.assertEqual(pivot.chips_flow.count(), 1)

    def test_13c_round3_ui_rules(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        t = panel.theme
        date = self.layer.fields().indexOf("SURVEY_DATE")
        flag = self.layer.fields().indexOf("IS_VACANT")
        # dates: one blue for both themes; booleans purple
        self.assertEqual(t.data_temporal, "#3A80EA")
        self.assertEqual(t.data_bool, "#C9A0DC" if t.dark else "#7B1FA2")
        self.assertEqual(panel.model._colours[date], QColor(t.data_temporal))
        self.assertEqual(panel.model._colours[flag], QColor(t.data_bool))
        # every date value box says YYYY-MM-DD, 'between' included
        cell = panel.header.cells[date]
        for op_id in ("eq", "lt", "le", "gt", "ge", "between"):
            cell.set_op(op_id)
            self.assertEqual(cell.edit.placeholderText(), "YYYY-MM-DD")
        self.assertEqual(cell.edit2.placeholderText(), "YYYY-MM-DD")
        self.assertGreaterEqual(panel.header.sectionSize(date),
                                cell._between_width())
        cell.set_filter(None)
        num = panel.header.cells[self.layer.fields().indexOf("SCORE_MAX")]
        num.set_op("between")
        self.assertEqual((num.edit.placeholderText(), num.edit2.placeholderText()),
                         ("from", "to"))
        num.set_filter(None)

        # Build Pivot: blue, label only, as tall as its neighbours
        pivot = self.plugin.open_pivot(self.layer)
        b = pivot.build_btn
        self.assertEqual(b.text(), "Build Pivot")
        self.assertTrue(b.icon().isNull())
        self.assertEqual(b.objectName(), "gtsPrimary")
        for other in (pivot.refresh_btn, pivot.export_btn, pivot.dock_btn):
            self.assertEqual(b.height(), other.height())
        # a narrow window collapses the row to icons drawn at full size
        win = self.plugin.pivot_host._window
        win.resize(1000, win.height())
        row = pivot.refresh_btn.parentWidget()
        self.assertTrue(H.wait_for(lambda: row._compact, 5))
        H.settle(0.2)
        self.assertTrue(pivot.refresh_btn.property("gtsCompact"))
        for btn in (pivot.refresh_btn, pivot.export_btn):      # Export is an icon anyway
            self.assertGreaterEqual(_ink_width(btn), 13, btn.toolTip())
        win.resize(2000, win.height())          # room for every label
        self.assertTrue(H.wait_for(lambda: not row._compact, 5))
        win.resize(1440, win.height())

        # Compare: the v1.1 action buttons
        compare = self.plugin.open_compare()
        v, c = compare.validate_btn, compare.compare_button
        self.assertEqual(v.size(), c.size())
        self.assertEqual(v.height(), 28)
        self.assertGreaterEqual(v.width(), 190)
        self.assertEqual((v.objectName(), c.objectName()), ("actionBtn", "actionBtn"))
        self.assertEqual(c.property("role"), "primary")
        self.assertEqual(v.iconSize().width(), 18)
        self.assertEqual(v.text(), " Validate Data")

    def test_13d_round4_explorer(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        # layer pickers list each layer with its CRS, in a list wide enough
        combo = panel.layer_combo
        self.assertTrue(combo.showCrs())
        self.assertIn("[EPSG:3857]", combo.currentText())
        combo.showPopup()
        H.settle(0.1)
        self.assertGreaterEqual(combo.view().minimumWidth(),
                                combo.view().fontMetrics().horizontalAdvance(
                                    combo.currentText()))
        combo.hidePopup()
        self.assertTrue(self.plugin.open_pivot(self.layer).layer_combo.showCrs())

        # a feature selected on the map scrolls into view ...
        view = panel.view
        view.scrollToTop()
        H.settle(0.1)
        fid = panel.model.fids[-40]
        self.layer.selectByIds([fid])
        H.settle(0.1)
        rect = view.visualRect(panel.model.index(panel.model.row_of(fid), 0))
        self.assertTrue(view.viewport().rect().contains(rect.center()))
        # ... but a selection made in the table never moves it
        view.scrollToTop()
        H.settle(0.1)
        panel._select([panel.model.fids[-10]], SELECT_SET)
        H.settle(0.1)
        self.assertEqual(view.verticalScrollBar().value(), 0)
        self.layer.removeSelection()

        # the clear button empties an 'is one of' filter
        pid = self.layer.fields().indexOf("PARCEL_ID")
        value = next(self.layer.getFeatures())["PARCEL_ID"]
        cell = panel.header.cells[pid]
        cell.set_filter(FL.ColumnFilter("PARCEL_ID", F.TEXT, "in",
                                        values=[value]), emit=True)
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertLess(len(panel.model.fids), self.layer.featureCount())
        clear = cell.edit.findChild(QAction, "_q_qlineeditclearaction")
        self.assertIsNotNone(clear)
        clear.trigger()
        self.assertIsNone(cell.current_filter())
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), self.layer.featureCount())

        # the value list sorts by value or by records, NULL kept on top
        from attribute_table_studio.ui.explorer_header import ValuePicker
        lu = self.layer.fields().indexOf("LAND_USE")
        picker = ValuePicker("LAND_USE", panel._values_for(lu), [],
                             panel.theme, panel.metrics, panel)
        tree = picker.tree

        def column(c):
            return [tree.topLevelItem(i).text(c)
                    for i in range(tree.topLevelItemCount())]
        names = column(0)
        self.assertEqual(names[0], F.NULL_TEXT)
        self.assertEqual(names[1:], sorted(names[1:], key=str.lower))
        tree.sortByColumn(1, Qt.SortOrder.DescendingOrder)
        counts = [int(c.replace(",", "")) for c in column(1)[1:]]
        self.assertEqual(column(0)[0], F.NULL_TEXT)
        self.assertEqual(counts, sorted(counts, reverse=True))
        tree.sortByColumn(0, Qt.SortOrder.DescendingOrder)
        self.assertEqual(column(0)[0], F.NULL_TEXT)
        self.assertEqual(column(0)[1:], sorted(names[1:], key=str.lower, reverse=True))
        picker.close()

    def test_13e_round4_compare(self):
        base = H.demo_layer(n=600, name="Review baseline")
        new = H.demo_updated_layer(base)
        flds = new.fields()
        i1, i2 = flds.indexOf("SCORE_MIN"), flds.indexOf("LAND_USE")
        new.dataProvider().changeAttributeValues({
            f.id(): {i1: (f[i1] or 0) + 1.5, i2: "COMMERCIAL"}
            for k, f in enumerate(new.getFeatures()) if k % 50 == 7})
        panel = self.plugin.open_compare()
        panel.old_table_combo.setLayer(base)
        panel.new_table_combo.setLayer(new)
        panel.join_field_combo.setCurrentIndex(
            panel.join_field_combo.findText("PARCEL_ID"))

        # Help, clear validation, Validate Data, Compare OLD -> NEW (no icon)
        row = (panel.help_btn, panel.clear_validation_btn, panel.validate_btn,
               panel.compare_button)
        xs = [w.mapTo(panel, QPoint(0, 0)).x() for w in row]
        self.assertEqual(xs, sorted(xs))
        self.assertTrue(panel.compare_button.icon().isNull())
        self.assertEqual(panel.compare_button.text(), "Compare OLD → NEW")
        self.assertFalse(panel.clear_validation_btn.isEnabled())
        panel.validate_data()
        self.assertTrue(panel.validation_frame.isVisibleTo(panel))
        self.assertTrue(panel.clear_validation_btn.isEnabled())
        panel.clear_validation()
        self.assertFalse(panel.validation_frame.isVisibleTo(panel))
        self.assertFalse(panel.clear_validation_btn.isEnabled())
        panel.show_help()
        self.assertTrue(panel._help_dialog.isVisible())
        self.assertIn("tracking what changed",
                      panel._help_dialog.findChild(QTextBrowser).toPlainText())
        panel._help_dialog.close()

        panel.compare_tables()
        tbl = panel.results_table
        delegate = tbl.itemDelegate()
        rows = [r for r in range(tbl.rowCount()) if len(panel._row_values(r)) >= 2]
        self.assertGreaterEqual(len(rows), 2)
        r = rows[0]
        values = panel._row_values(r)
        user, decided = Qt.ItemDataRole.UserRole, Qt.ItemDataRole.UserRole + 2
        # a pending changed value is drawn split, OLD | NEW
        self.assertIsNotNone(delegate.pair(tbl.indexFromItem(values[0])))
        # accept one value: one cell with the NEW value; row Pending 1/n
        tbl.clearSelection()
        values[0].setSelected(True)
        panel._decide_values("Accepted")
        old0, new0 = values[0].data(user)
        self.assertEqual(values[0].text(), new0)
        self.assertIsNone(delegate.pair(tbl.indexFromItem(values[0])))
        self.assertEqual(tbl.item(r, 1).text(), f"Pending 1/{len(values)}")
        # reject the others: the row is Mixed, the cells show OLD
        tbl.clearSelection()
        for item in values[1:]:
            item.setSelected(True)
        panel._decide_values("Rejected")
        self.assertEqual(tbl.item(r, 1).text(), "◐ Mixed")
        self.assertEqual(values[1].text(), values[1].data(user)[0])
        # a row decision decides every changed value of the row
        r2 = rows[1]
        panel._decide_rows([r2], "Accepted")
        self.assertTrue(all(it.data(decided) == "Accepted"
                            for it in panel._row_values(r2)))
        self.assertEqual(tbl.item(r2, 1).text(), "✓ Accepted")
        self.assertTrue(panel.accepted_legend.text().startswith("Accepted · "))
        self.assertIn("Mixed 1", panel.review_tally.text())

        # export: a rejected value gives OLD, an accepted one NEW
        path = os.path.join(OUT, "compare_review.csv")
        _next_save.append(path)
        panel.export_results()
        with open(path, newline="", encoding="utf-8") as fh:
            records = list(csv.DictReader(fh))
        key = tbl.item(r, 0).data(user)
        rec = next(d for d in records if d["PARCEL_ID"] == str(key))
        name = [tbl.horizontalHeaderItem(it.column()).text() for it in values]
        self.assertEqual(rec[name[0]], new0)
        self.assertEqual(rec[name[1]], values[1].data(user)[0])
        self.assertEqual(rec["Decision"], "Mixed")

        # the right-click menu offers value, row and zoom actions
        shown = []
        original = QMenu.exec
        QMenu.exec = lambda menu, *a: shown.extend(
            act.text() for act in menu.actions() if not act.isSeparator())
        try:
            panel._on_table_menu(tbl.visualItemRect(values[0]).center())
        finally:
            QMenu.exec = original
        for text in ("Accept value", "Reject value", "Accept row", "Zoom to feature"):
            self.assertIn(text, shown)

        # zoom to selection selects the row's features in OLD and NEW
        tbl.clearSelection()
        tbl.selectRow(r)
        panel.zoom_to_selection()
        self.assertEqual(new.selectedFeatureIds(), [panel._fid_maps["new"][key]])
        self.assertEqual(base.selectedFeatureIds(), [panel._fid_maps["old"][key]])
        canvas_extent = self.iface.mapCanvas().extent()
        self.assertFalse(canvas_extent.isEmpty())

    def test_13n_compare_feature_export(self):
        from attribute_table_studio.core import compare_export as CX
        from qgis.core import QgsFeature, QgsField
        from qgis.PyQt.QtCore import QMetaType
        base = H.demo_layer(n=300, name="Export baseline")
        new = H.demo_updated_layer(base)
        i_lu = new.fields().indexOf("LAND_USE")
        new.dataProvider().changeAttributeValues({
            f.id(): {i_lu: "MIXED USE"}
            for k, f in enumerate(new.getFeatures()) if k % 40 == 21})
        panel = self.plugin.open_compare()
        panel.old_table_combo.setLayer(base)
        panel.new_table_combo.setLayer(new)
        panel.join_field_combo.setCurrentIndex(
            panel.join_field_combo.findText("PARCEL_ID"))
        panel.geom_compare_cb.setChecked(True)
        panel.columns_to_check = []
        panel.compare_tables()
        tbl = panel.results_table
        user = Qt.ItemDataRole.UserRole
        names = [tbl.horizontalHeaderItem(c).text() for c in range(tbl.columnCount())]

        def rows_of(status):
            return [r for r in range(tbl.rowCount()) if tbl.item(r, 0).text() == status]

        def key(r):
            return tbl.item(r, 0).data(user)

        modified = rows_of("Modified")
        scored = [r for r in modified if "SCORE_MAX" in
                  [names[it.column()] for it in panel._row_values(r)]]
        rejected_row, accepted_row = scored[0], scored[1]
        panel._decide_rows([rejected_row], "Rejected")
        panel._decide_rows([accepted_row], "Accepted")
        # a moved feature: rejecting its geometry values keeps the OLD shape
        moved = [r for r in modified
                 if tbl.item(r, names.index("_geom_status")).text() == "Changed"]
        self.assertGreaterEqual(len(moved), 2)
        tbl.clearSelection()
        for it in panel._row_values(moved[0]):
            if names[it.column()] in CX.GEOMETRY_VALUES:
                it.setSelected(True)
        panel._decide_values("Rejected")
        # only the rows shown are exported: hide Unchanged
        panel.filter_buttons["Unchanged"].setChecked(False)
        H.pump()

        path = os.path.join(OUT, "compare_features.gpkg")
        _next_save.append(path)
        panel.export_features()
        out = {n: QgsVectorLayer(f"{path}|layername={n}", n, "ogr")
               for n in ("added", "deleted", "modified", "unchanged")}
        self.assertFalse(out["unchanged"].isValid())
        counts = panel._compare_info["counts"]
        for kind in ("Added", "Deleted", "Modified"):
            self.assertTrue(out[kind.lower()].isValid())
            self.assertEqual(out[kind.lower()].featureCount(), counts[kind])

        # one schema for every layer: fixed columns, then the table's columns,
        # each changed column followed by its _OLD
        fields = [f.name() for f in out["modified"].fields()]
        for layer in out.values():
            if layer.isValid():
                self.assertEqual([f.name() for f in layer.fields()], fields)
        self.assertEqual(fields[:4], ["fid", "CHANGE", "DECISION", "CHANGED_FIELDS"])
        fixed = ("CHANGE", "DECISION", "CHANGED_FIELDS")
        self.assertEqual([n for n in fields if n not in fixed
                          and not n.endswith("_OLD")], names[2:])
        self.assertEqual(fields[fields.index("SCORE_MAX") + 1], "SCORE_MAX_OLD")
        self.assertEqual(fields[fields.index("LAND_USE") + 1], "LAND_USE_OLD")
        self.assertNotIn("OWNER_ID_OLD", fields)
        ofields = out["modified"].fields()
        self.assertEqual(F.field_kind(ofields.field("SCORE_MAX")), F.REAL)
        self.assertEqual(F.field_kind(ofields.field("SURVEY_DATE")), F.DATE)
        self.assertEqual(F.field_kind(ofields.field("_centroid_x")), F.REAL)

        def feature(layer, k):
            return next(f for f in out[layer].getFeatures() if f["PARCEL_ID"] == k)

        def source(layer, k):
            side = "old" if layer is base else "new"
            return layer.getFeature(panel._fid_maps[side][k])

        k = key(rejected_row)
        f, old, upd = feature("modified", k), source(base, k), source(new, k)
        self.assertEqual(f["CHANGE"], "Modified")
        self.assertEqual(f["DECISION"], "Rejected")
        self.assertIn("SCORE_MAX", f["CHANGED_FIELDS"])
        self.assertEqual(f["SCORE_MAX"], old["SCORE_MAX"])          # OLD kept
        self.assertEqual(f["SCORE_MAX_OLD"], old["SCORE_MAX"])
        self.assertEqual(f["fid"], upd["fid"])         # the GeoPackage id stays
        k = key(accepted_row)
        f, old, upd = feature("modified", k), source(base, k), source(new, k)
        self.assertEqual(f["DECISION"], "Accepted")
        self.assertEqual(f["SCORE_MAX"], upd["SCORE_MAX"])          # NEW kept
        self.assertEqual(f["SCORE_MAX_OLD"], old["SCORE_MAX"])
        if old["LAND_USE"] == upd["LAND_USE"]:
            self.assertTrue(F.is_null(f["LAND_USE_OLD"]))
        self.assertEqual(f.geometry().asWkt(6), upd.geometry().asWkt(6))
        k = key(moved[0])
        f, old, upd = feature("modified", k), source(base, k), source(new, k)
        self.assertIn("geometry", f["CHANGED_FIELDS"])
        self.assertEqual(f["_geom_status"], "Changed")
        self.assertEqual(f.geometry().asWkt(6), old.geometry().asWkt(6))        # OLD shape
        self.assertEqual(f["_centroid_x_OLD"], f["_centroid_x"])
        k = key(moved[1])
        f, upd = feature("modified", k), source(new, k)
        self.assertEqual(f["DECISION"], "Pending")
        self.assertEqual(f.geometry().asWkt(6), upd.geometry().asWkt(6))        # NEW shape
        k = key(rows_of("Deleted")[0])
        f, old = feature("deleted", k), source(base, k)
        self.assertTrue(F.is_null(f["DECISION"]))
        self.assertTrue(F.is_null(f["CHANGED_FIELDS"]))
        self.assertTrue(F.is_null(f["SCORE_MAX_OLD"]))
        self.assertEqual(f["SCORE_MAX"], old["SCORE_MAX"])
        self.assertEqual(f["fid"], old["fid"])
        self.assertEqual(f.geometry().asWkt(6), old.geometry().asWkt(6))
        f = feature("added", key(rows_of("Added")[0]))
        self.assertEqual((f["CHANGE"], f["DECISION"]), ("Added", "Pending"))

        # added to the map as a group named after the file; exporting to the
        # same file again replaces those layers
        root = QgsProject.instance().layerTreeRoot()

        def groups():
            return [g for g in root.children() if g.name() == "compare_features"]
        self.assertEqual(len(groups()), 1)
        self.assertEqual([n.name() for n in groups()[0].children()],
                         ["Added", "Deleted", "Modified"])
        first_ids = list(panel._feature_export[1])
        _next_save.append(path)
        panel.export_features()
        self.assertEqual(len(groups()), 1)
        self.assertTrue(all(QgsProject.instance().mapLayer(i) is None for i in first_ids))
        self.assertEqual(len(panel._feature_export[1]), 3)
        # a file other layers of the project still read is not overwritten
        stamp = os.path.getmtime(path)
        other = QgsVectorLayer(f"{path}|layername=added", "Other", "ogr")
        QgsProject.instance().addMapLayer(other)
        kept = list(panel._feature_export[1])
        _next_save.append(path)
        panel.export_features()
        self.assertEqual(os.path.getmtime(path), stamp)
        self.assertEqual(panel._feature_export[1], kept)       # nothing removed
        self.assertTrue(all(QgsProject.instance().mapLayer(i) for i in kept))
        QgsProject.instance().removeMapLayer(other.id())

        # field definitions: OLD values must fit, differing types become text
        _MT = getattr(QMetaType, "Type", QMetaType)
        merged = CX.merged_field
        self.assertEqual(F.type_id(merged("A", QgsField("A", _MT.Int), QgsField("A", _MT.LongLong))[0].type()),
                         F.type_id(_MT.LongLong))
        self.assertEqual(F.field_kind(merged("A", QgsField("A", _MT.Int), QgsField("A", _MT.Double))[0]), F.REAL)
        self.assertEqual(merged("A", QgsField("A", _MT.QString, "string", 20),
                                QgsField("A", _MT.QString, "string", 30))[0].length(), 30)
        self.assertEqual(merged("A", QgsField("A", _MT.QDate), QgsField("A", _MT.QString))[1], True)

        # tables without geometry; a column called fid that is not the key
        old_t = QgsVectorLayer("None?field=ID:string(10)&field=V:integer&field=fid:integer",
                               "Old table", "memory")
        new_t = QgsVectorLayer("None?field=ID:string(10)&field=V:string(10)&field=fid:integer",
                               "New table", "memory")
        for layer, rows in ((old_t, [("A", 1, 7), ("B", 2, 7), ("C", 3, 8)]),
                            (new_t, [("A", "1", 7), ("B", "two", 7), ("D", "4", 9)])):
            feats = []
            for values in rows:
                f = QgsFeature(layer.fields())
                f.setAttributes(list(values))
                feats.append(f)
            layer.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayers([old_t, new_t])
        panel.geom_compare_cb.setChecked(False)
        panel.old_table_combo.setLayer(old_t)
        panel.new_table_combo.setLayer(new_t)
        panel.join_field_combo.setCurrentIndex(panel.join_field_combo.findText("ID"))
        panel.columns_to_check = []
        panel.compare_tables()
        for status in ("Added", "Deleted", "Modified", "Unchanged"):
            panel.filter_buttons[status].setChecked(True)
        H.pump()
        path = os.path.join(OUT, "compare_tables.gpkg")
        _next_save.append(path)
        panel.export_features()
        mod = QgsVectorLayer(f"{path}|layername=modified", "m", "ogr")
        dele = QgsVectorLayer(f"{path}|layername=deleted", "d", "ogr")
        self.assertTrue(mod.isValid() and dele.isValid())
        self.assertFalse(mod.isSpatial())
        self.assertEqual([f.name() for f in mod.fields()][:5],
                         ["ogc_fid", "CHANGE", "DECISION", "CHANGED_FIELDS", "ID"])
        self.assertEqual(F.field_kind(mod.fields().field("V")), F.TEXT)
        self.assertEqual(F.field_kind(mod.fields().field("V_OLD")), F.INTEGER)
        f = next(f for f in mod.getFeatures() if f["ID"] == "B")
        self.assertEqual((f["V"], f["V_OLD"], f["fid"]), ("two", 2, 7))
        self.assertEqual(sorted(f["fid"] for f in dele.getFeatures()), [8])
        unchanged = QgsVectorLayer(f"{path}|layername=unchanged", "u", "ogr")
        self.assertEqual([f["fid"] for f in unchanged.getFeatures()], [7])
        QgsProject.instance().removeMapLayers([old_t.id(), new_t.id()])

    def test_13o_count_and_join_table(self):
        from qgis.core import QgsFeature, QgsLayerTreeModel
        from qgis.PyQt.QtCore import QMimeData, QUrl
        from attribute_table_studio.core import joins as J
        from attribute_table_studio.ui import explorer_join as EJ
        base = H.demo_layer(n=400, name="Join parcels")
        panel = self.plugin.open_explorer(base)
        self.assertTrue(H.wait_for(lambda: idle(panel)))

        # the count: the layer's records, then the records shown when fewer
        self.assertEqual(panel.count_label.text(), "400")
        lu = base.fields().indexOf("LAND_USE")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "RESIDENTIAL"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        shown = len(panel.model.fids)
        self.assertLess(shown, 400)
        self.assertEqual(panel.count_label.text(), f"400 ({shown:,})")
        self.assertIn(f"{shown:,} of 400", panel.status.message.fullText())
        panel.clear_filters()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(panel.count_label.text(), "400")

        # matching rules: text of the value, NULL never matches
        self.assertEqual([J.join_key(v) for v in (12, 12.0, "12", 2.5, True, None)],
                         ["12", "12", "12", "2.5", "true", None])

        # a made-up zones table: ZONE_CODE as text, one code repeats
        zones = QgsVectorLayer("None?field=ZONE_CODE:string(5)&field=ZONE_NAME:string(30)"
                               "&field=MAX_HEIGHT:double", "Zones", "memory")
        rows = [("1", "Low density", 9.0), ("2", "Medium density", 12.5),
                ("3", "High density (old)", 18.0), ("3", "High density", 21.0),
                ("4", "Mixed use", 15.0)]
        feats = []
        for values in rows:
            f = QgsFeature(zones.fields())
            f.setAttributes(list(values))
            feats.append(f)
        zones.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(zones)

        # the Join table button sits with New field / Delete field
        bar = panel.edit_bar
        self.assertIn(bar.join_btn, bar.groups[5])
        self.assertTrue(bar.join_btn.isEnabled())
        dlg = EJ.JoinTableDialog(panel)
        self.assertFalse(dlg.join_btn.isEnabled())
        # OK is a plain text button like Cancel: no icon, not blue, same size
        self.assertTrue(dlg.join_btn.icon().isNull())
        self.assertEqual(dlg.join_btn.text().strip(), "OK")
        self.assertEqual(dlg.join_btn.objectName(), dlg.cancel_btn.objectName())
        dlg.show()
        H.settle(0.1)
        self.assertEqual(dlg.join_btn.size(), dlg.cancel_btn.size())
        self.assertEqual(dlg.chosen_fields(), [])
        # dragged from the Layers panel
        tree = QgsLayerTreeModel(QgsProject.instance().layerTreeRoot())
        node = QgsProject.instance().layerTreeRoot().findLayer(zones.id())
        self.assertIs(dlg.accept_mime(tree.mimeData([tree.node2index(node)])), zones)
        self.assertIs(dlg.join_layer(), zones)
        # the shared field name is matched; the join field is not offered
        self.assertEqual((dlg.target_combo.current_name(), dlg.join_combo.current_name()),
                         ("ZONE_CODE", "ZONE_CODE"))
        # no field is ticked at first, so OK waits for one
        self.assertEqual([it.text() for it in dlg._field_items()], ["ZONE_NAME", "MAX_HEIGHT"])
        self.assertEqual(dlg.chosen_fields(), [])
        self.assertFalse(dlg.join_btn.isEnabled())
        self.assertIn("Tick at least one field", dlg.join_btn.toolTip())
        # the filter box shows the matching fields; All / None act on those
        head = dlg.field_search.parentWidget()
        x = [w.mapTo(head, QPoint(0, 0)).x() for w in
             (dlg.field_search, dlg.all_link, dlg.none_link)]
        self.assertEqual(x, sorted(x))
        self.assertEqual(dlg.field_search.placeholderText(), "Search / Filter fields")
        dlg.field_search.setText("name")
        shown = [it.text() for it in dlg._field_items() if not it.isHidden()]
        self.assertEqual(shown, ["ZONE_NAME"])
        dlg.all_link.click()
        self.assertEqual(dlg.chosen_fields(), ["ZONE_NAME"])
        dlg.field_search.clear()
        self.assertTrue(all(not it.isHidden() for it in dlg._field_items()))
        dlg.all_link.click()
        self.assertEqual(dlg.chosen_fields(), ["ZONE_NAME", "MAX_HEIGHT"])
        dlg.none_link.click()
        self.assertEqual(dlg.chosen_fields(), [])
        dlg.all_link.click()
        # the prefix is QGIS's default, greyed, until Custom field name
        # prefix is ticked (which opens the box)
        self.assertEqual(dlg.prefix(), "Zones_")
        self.assertFalse(dlg.custom_prefix())
        self.assertTrue(dlg.prefix_group.isCollapsed())
        self.assertFalse(dlg.prefix_edit.isEnabled())
        self.assertEqual(dlg.prefix_edit.text(), "Zones_")
        dlg.prefix_group.setChecked(True)
        self.assertFalse(dlg.prefix_group.isCollapsed())
        self.assertTrue(dlg.prefix_edit.isEnabled())
        # the prefix box spans the window (as in QGIS's Joins dialog)
        dlg.show()
        H.settle(0.2)
        self.assertGreater(dlg.prefix_edit.width(), dlg.prefix_group.width() - 40)
        self.assertTrue(H.wait_for(lambda: dlg.counts is not None, 20))
        codes = [f["ZONE_CODE"] for f in base.getFeatures()]
        matched = sum(1 for c in codes if c in (1, 2, 3, 4))
        self.assertEqual(dlg.counts["matched"], matched)
        self.assertEqual(dlg.counts["repeated"], 1)
        self.assertEqual(dlg.counts["repeated_records"], codes.count(3))
        self.assertTrue(dlg.match_head.text().startswith(f"{matched:,} of 400 records match"))
        self.assertIn("compared as text", dlg.match_detail.text())
        # a name already in the layer is left out by QGIS: say so
        dlg.prefix_edit.setText("")
        dlg._update_names()
        dlg.field_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(J.clashes(base.fields(), dlg.chosen_fields(), ""), [])
        self.assertEqual(J.clashes(base.fields(), ["ZONE_CODE", "LAND_USE"], ""),
                         ["ZONE_CODE", "LAND_USE"])
        dlg.field_list.item(0).setCheckState(Qt.CheckState.Checked)
        dlg.prefix_edit.setText("Z_")
        dlg._update_names()
        self.assertEqual(dlg.prefix(), "Z_")
        dlg.field_list.item(1).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(dlg.chosen_fields(), ["ZONE_NAME"])
        self.assertTrue(dlg.join_btn.isEnabled())
        self.assertTrue(dlg.add_join())
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        names = [f.name() for f in base.fields()]
        self.assertEqual(names[-1], "Z_ZONE_NAME")
        self.assertNotIn("Z_MAX_HEIGHT", names)
        self.assertIn("Z_ZONE_NAME", [f.name() for f in panel.model.fields])
        self.assertIn("Joined Zones", panel.status.message.fullText())
        # QGIS gives a repeated code the last matching row
        f3 = next(f for f in base.getFeatures() if f["ZONE_CODE"] == 3)
        self.assertEqual(f3["Z_ZONE_NAME"], "High density")
        # listed with Remove; removing takes the column away
        dlg2 = EJ.JoinTableDialog(panel)
        self.assertEqual(dlg2.joins_layout.count(), 1)
        dlg2.remove_join(zones.id(), "Zones")
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertNotIn("Z_ZONE_NAME", [f.name() for f in base.fields()])
        self.assertEqual(len(base.vectorJoins()), 0)
        dlg2.reject()

        # a CSV dropped from the file manager joins once OK is pressed;
        # cancelled, it never reaches the project
        path = os.path.join(OUT, "zone_heights.csv")
        with open(path, "w", encoding="utf-8", newline="") as fh:
            fh.write("ZONE_CODE,HEIGHT_LIMIT\n1,9\n2,12.5\n5,30\n")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        before = set(QgsProject.instance().mapLayers())
        dlg3 = EJ.JoinTableDialog(panel)
        csv_layer = dlg3.accept_mime(mime)
        self.assertIsNotNone(csv_layer)
        self.assertEqual(csv_layer.providerType(), "delimitedtext")
        self.assertEqual(set(QgsProject.instance().mapLayers()), before)
        self.assertEqual(F.field_kind(csv_layer.fields().field("ZONE_CODE")), F.INTEGER)
        self.assertTrue(H.wait_for(lambda: dlg3.counts is not None, 20))
        self.assertEqual(dlg3.counts["matched"],
                         codes.count(1) + codes.count(2) + codes.count(5))
        dlg3.reject()
        self.assertEqual(set(QgsProject.instance().mapLayers()), before)
        dlg4 = EJ.JoinTableDialog(panel)
        csv_layer = dlg4.accept_mime(mime)
        self.assertTrue(H.wait_for(lambda: dlg4.counts is not None, 20))
        self.assertFalse(dlg4.add_join())               # nothing ticked yet
        dlg4.all_link.click()
        self.assertTrue(dlg4.add_join())
        self.assertIsNotNone(QgsProject.instance().mapLayer(csv_layer.id()))
        self.assertIn("zone_heights_HEIGHT_LIMIT", [f.name() for f in base.fields()])
        # this layer itself cannot be the join layer
        dlg5 = EJ.JoinTableDialog(panel)
        node = QgsProject.instance().layerTreeRoot().findLayer(base.id())
        self.assertIsNone(dlg5.accept_mime(tree.mimeData([tree.node2index(node)])))
        self.assertTrue(dlg5.drop_problem.text())
        dlg5.reject()
        base.removeJoin(csv_layer.id())
        panel.close()
        H.settle(0.1)

    def test_13p_column_select_and_field_properties(self):
        from qgis.core import QgsFieldConstraints
        from qgis.PyQt.QtCore import QMetaType
        from qgis.PyQt.QtGui import QGuiApplication
        from qgis.PyQt.QtTest import QTest
        from qgis.core import QgsField
        from attribute_table_studio.ui.explorer_fields import COL, FieldPropertiesDialog
        layer = H.demo_layer(n=300, name="Field parcels")
        _MT = getattr(QMetaType, "Type", QMetaType)
        layer.addExpressionField('"SCORE_MAX" - "SCORE_MIN"', QgsField("SCORE_RANGE", _MT.Double))
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        fields = layer.fields()
        lu, sm, s2 = (fields.indexOf(n) for n in ("LAND_USE", "SCORE_MAX", "SCORE_2Y"))
        h = panel.header
        ctrl, shift = Qt.KeyboardModifier.ControlModifier, Qt.KeyboardModifier.ShiftModifier

        def name_point(logical):
            panel.view.scrollTo(panel.model.index(0, logical))   # in view first
            H.pump()
            return QPoint(h.sectionViewportPosition(logical) + 12, h.name_top() + 5)

        def apart():
            # clicks closer than the double-click time count as one (Qt5)
            from qgis.PyQt.QtWidgets import QApplication
            QTest.qWait(QApplication.doubleClickInterval() + 60)

        # a click on the name selects the whole column; nothing is sorted
        apart()
        QTest.mouseClick(h.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, name_point(lu))
        self.assertEqual(panel.model.selected_cols, {lu})
        self.assertEqual(h.selected_cols, {lu})
        self.assertIsNone(panel.sort)
        self.assertEqual(h.sortIndicatorSection(), -1)     # no sort arrow either
        # the name turns the selection blue (also on Qt5, whose header leaves a
        # clip behind)
        H.settle(0.1)
        img = h.viewport().grab().toImage()
        ratio = img.devicePixelRatio() or 1
        spot = QPoint(h.sectionViewportPosition(lu) + h.sectionSize(lu) - 30,
                      h.name_top() + 3)
        self.assertEqual(QColor(img.pixel(int(spot.x() * ratio), int(spot.y() * ratio))).name(),
                         QColor(panel.theme.selected).name())
        apart()
        QTest.mouseClick(h.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, name_point(sm))
        self.assertEqual(h.sortIndicatorSection(), -1)
        self.assertEqual(panel.model.selected_cols, {sm})
        panel._on_header_clicked(lu)
        # Ctrl adds or removes one, Shift takes the range of shown columns
        panel._on_header_clicked(s2, ctrl)
        self.assertEqual(panel.model.selected_cols, {lu, s2})
        panel._on_header_clicked(s2, ctrl)
        self.assertEqual(panel.model.selected_cols, {lu})
        panel._on_header_clicked(lu)                  # the range starts here
        panel._on_header_clicked(s2, shift)
        span = {h.logicalIndex(v) for v in range(h.visualIndex(lu), h.visualIndex(s2) + 1)}
        self.assertEqual(panel.model.selected_cols, span)
        # Ctrl+C copies the selected columns for the records shown
        panel._on_header_clicked(lu)
        panel._on_header_clicked(sm, ctrl)
        panel.copy_current_cell()
        lines = QGuiApplication.clipboard().text().split("\n")
        self.assertEqual(lines[0], "LAND_USE\tSCORE_MAX")
        self.assertEqual(len(lines), len(panel.model.fids) + 1)
        self.assertEqual(lines[1].split("\t")[0], str(panel.model.raw_value(0, lu)))
        # a right-click selects the column as well (one already selected
        # keeps the selection)
        original = QMenu.exec
        QMenu.exec = lambda *_a, **_k: None
        try:
            panel._header_menu(QPoint(h.sectionViewportPosition(s2) + 5, h.name_top() + 5))
            self.assertEqual(panel.model.selected_cols, {s2})
            panel._on_header_clicked(lu, ctrl)
            panel._header_menu(QPoint(h.sectionViewportPosition(lu) + 5, h.name_top() + 5))
            self.assertEqual(panel.model.selected_cols, {s2, lu})
        finally:
            QMenu.exec = original
        panel._on_header_clicked(lu)
        panel._on_header_clicked(sm, ctrl)
        # clicking a cell or Escape clears it
        panel.view.pressed.emit(panel.model.index(0, 0))
        self.assertEqual(panel.model.selected_cols, set())
        panel._on_header_clicked(lu)
        panel.view.escapePressed.emit()
        self.assertEqual(h.selected_cols, set())
        # a double click sorts: ascending, descending, then off
        for expected in ((lu, False), (lu, True), None):
            apart()
            QTest.mouseDClick(h.viewport(), Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier, name_point(lu))
            self.assertTrue(H.wait_for(lambda: idle(panel)))
            self.assertEqual(panel.sort, expected)
            self.assertEqual(h.sortIndicatorSection(), lu if expected else -1)
            if expected:
                self.assertEqual(h.sortIndicatorOrder(), Qt.SortOrder.DescendingOrder
                                 if expected[1] else Qt.SortOrder.AscendingOrder)
        # a click on a sorted column's name keeps its arrow as it is
        apart()
        QTest.mouseDClick(h.viewport(), Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier, name_point(lu))
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        apart()
        QTest.mouseClick(h.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, name_point(lu))
        self.assertEqual((h.sortIndicatorSection(), h.sortIndicatorOrder()),
                         (lu, Qt.SortOrder.AscendingOrder))
        apart()
        QTest.mouseClick(h.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, name_point(sm))
        self.assertEqual(h.sortIndicatorSection(), lu)
        panel._on_sort_clicked(lu)
        panel._on_sort_clicked(lu)                  # back to unsorted
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(h.sortIndicatorSection(), -1)
        self.assertIn("double-click to sort",
                      panel.model.headerData(lu, Qt.Orientation.Horizontal,
                                             Qt.ItemDataRole.ToolTipRole))

        # Field Properties: the window, opened on a field
        bar = panel.edit_bar
        self.assertIn(bar.field_props_btn, bar.groups[5])
        self.assertEqual(bar.groups[5].index(bar.field_props_btn),
                         bar.groups[5].index(bar.join_btn) + 1)
        dlg = FieldPropertiesDialog(panel, "LAND_USE")
        t = dlg.table
        self.assertEqual(t.currentRow(), lu)
        self.assertEqual(t.rowCount(), fields.count())
        self.assertEqual(dlg.count_label.text(), f"{fields.count()} fields")
        rng = fields.indexOf("SCORE_RANGE")
        fid = fields.indexOf("fid")
        self.assertEqual(t.item(rng, COL["origin"]).text(), "Virtual")
        self.assertEqual(t.item(lu, COL["origin"]).text(), "Data source")
        self.assertFalse(t.item(rng, COL["readonly"]).flags() & Qt.ItemFlag.ItemIsEnabled)
        self.assertFalse(t.item(rng, COL["default"]).flags() & Qt.ItemFlag.ItemIsEditable)
        self.assertFalse(t.item(lu, COL["type"]).flags() & Qt.ItemFlag.ItemIsEditable)
        cons = fields.at(fid).constraints()
        if cons.constraints() & QgsFieldConstraints.Constraint.ConstraintNotNull:
            self.assertFalse(t.item(fid, COL["allownull"]).flags() & Qt.ItemFlag.ItemIsEnabled)
        # the filter box narrows the rows
        dlg.search.setText("score")
        shown = [t.item(r, COL["name"]).text() for r in range(t.rowCount())
                 if not t.isRowHidden(r)]
        self.assertEqual(shown, ["SCORE_MAX", "SCORE_MIN", "SCORE_2Y", "SCORE_5Y",
                                 "SCORE_RANGE"])
        dlg.search.clear()
        # changes; a bad name or default value stops OK
        own, zc, mn, s5 = (fields.indexOf(n) for n in ("OWNER_ID", "ZONE_CODE",
                                                      "SCORE_MIN", "SCORE_5Y"))
        t.item(s5, COL["name"]).setText("LAND_USE")
        self.assertFalse(dlg.ok_btn.isEnabled())
        self.assertIn("already named", dlg.problem.text())
        t.item(s5, COL["name"]).setText("SCORE_5YR")
        t.item(lu, COL["default"]).setText("'RESIDENTIAL' +")
        self.assertFalse(dlg.ok_btn.isEnabled())
        t.item(lu, COL["default"]).setText("'RESIDENTIAL'")
        t.item(lu, COL["onupdate"]).setCheckState(Qt.CheckState.Checked)
        self.assertTrue(dlg.ok_btn.isEnabled())
        t.item(own, COL["alias"]).setText("Owner")
        t.item(zc, COL["allownull"]).setCheckState(Qt.CheckState.Unchecked)
        t.item(zc, COL["unique"]).setCheckState(Qt.CheckState.Checked)
        t.item(mn, COL["readonly"]).setCheckState(Qt.CheckState.Checked)
        t.item(s2, COL["visible"]).setCheckState(Qt.CheckState.Unchecked)
        self.assertEqual(sorted(dlg.changed_rows()), sorted([lu, own, zc, mn, s2, s5]))
        self.assertTrue(t.item(own, COL["name"]).font().bold())
        self.assertFalse(t.item(sm, COL["name"]).font().bold())
        self.assertIn("6 changed", dlg.count_label.text())
        self.assertFalse(layer.isEditable())
        self.assertTrue(dlg.apply())
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        fields = layer.fields()
        self.assertEqual(fields.at(own).alias(), "Owner")
        c = fields.at(zc).constraints()
        self.assertTrue(c.constraints() & QgsFieldConstraints.Constraint.ConstraintNotNull)
        self.assertTrue(c.constraints() & QgsFieldConstraints.Constraint.ConstraintUnique)
        self.assertTrue(layer.editFormConfig().readOnly(mn))
        dv = layer.defaultValueDefinition(lu)
        self.assertEqual((dv.expression(), dv.applyOnUpdate()), ("'RESIDENTIAL'", True))
        self.assertTrue(panel.header.isSectionHidden(s2))
        # the rename is in the edit session, as Rename Field does
        self.assertTrue(layer.isEditable())
        self.assertEqual(fields.at(s5).name(), "SCORE_5YR")
        self.assertIn("1 field(s) renamed", panel.status.message.fullText())
        # nothing changed: OK just closes
        dlg2 = FieldPropertiesDialog(panel)
        self.assertEqual(dlg2.changed_rows(), [])
        self.assertTrue(dlg2.apply())
        layer.rollBack()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        panel.close()
        H.settle(0.1)

    def test_13q_visualize_statistics(self):
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication
        from attribute_table_studio.core import charts as C
        from attribute_table_studio.core import stats as S
        from attribute_table_studio.ui.explorer_header import ProfileDetails
        layer = H.demo_layer(n=800, name="Chart parcels")
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        f = layer.fields()
        sm, lu, sd = (f.indexOf(n) for n in ("SCORE_MAX", "LAND_USE", "SURVEY_DATE"))
        feats = list(layer.getFeatures())
        h = panel.header
        # a column name's menu offers the chart
        shown = []
        original = QMenu.exec
        QMenu.exec = lambda menu, *a: shown.extend(
            act.text() for act in menu.actions() if not act.isSeparator())
        try:
            panel.view.scrollTo(panel.model.index(0, sm))
            H.pump()
            panel._header_menu(QPoint(h.sectionViewportPosition(sm) + 5, h.name_top() + 5))
        finally:
            QMenu.exec = original
        self.assertIn("Visualize Statistics…", shown)

        # the Visualize Statistics button beside Summary opens the selected column
        panel.select_columns({sm})
        panel.chart_btn.click()
        chart = panel.chart_host.panel
        self.assertEqual(chart.field_name(), "SCORE_MAX")

        def ready(name, split=None):
            return H.wait_for(lambda: chart.col is not None and chart.col.name == name
                              and chart.col.split_name == split and not chart.reader.busy
                              and not panel.runner.busy, 60)

        self.assertTrue(ready("SCORE_MAX"))
        self.assertIs(panel.chart_host.panel, chart)
        self.assertIs(panel.visualize_statistics(sm), chart)        # one per explorer
        self.assertEqual(chart.kind_combo.currentText(), "Histogram")
        self.assertIn("Visualize Statistics — SCORE_MAX", chart.window_title())
        # the explorer's statistics, every record in a bin
        vals = [ft["SCORE_MAX"] for ft in feats]
        prof = S.compute_profile(F.REAL, vals)
        st = chart.col.stats
        self.assertEqual((st["records"], st["null"]), (800, prof.missing))
        self.assertEqual((st["min"], st["max"], st["median"]),
                         (prof.minimum, prof.maximum, prof.median))
        self.assertAlmostEqual(st["mean"], prof.mean, places=9)
        self.assertAlmostEqual(st["std"], prof.std, places=9)
        self.assertEqual(sum(chart.bars.counts), prof.valid)
        self.assertEqual(chart.bins_spin.value(), C.default_bins(prof.valid))
        # other bins come from the kept values, without reading again
        col = chart.col
        chart.bins_spin.setValue(10)
        self.assertEqual(len(chart.bars), 10)
        self.assertIs(chart.col, col)
        self.assertEqual(sum(chart.bars.counts), prof.valid)

        win = chart.window()
        win.resize(1100, 680)
        H.settle(0.3)
        v = chart.view

        def bar_point(i):
            for rect, bi, s in v._geo["bars"]:
                if bi == i and s is None:
                    return QPoint(int(rect.center().x()), int(rect.bottom() - 4))

        def in_bins(*idxs):
            bars = chart.bars
            out = set()
            for ft in feats:
                x = ft["SCORE_MAX"]
                for i in idxs:
                    last = i == len(bars) - 1
                    if bars.lows[i] <= x and (x < bars.highs[i] or (last and x <= bars.highs[i])):
                        out.add(ft.id())
            return out

        # a click selects the bar's records, Ctrl+click adds, a drag selects the range
        # (empty bins have no click area)
        full = [i for i, c in enumerate(chart.bars.counts) if c]
        a, b, c, d = full[1], full[2], full[3], full[5]
        no, ctrl = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier
        QTest.mouseClick(v, Qt.MouseButton.LeftButton, no, bar_point(a))
        H.settle(0.3)
        self.assertEqual(set(layer.selectedFeatureIds()), in_bins(a))
        self.assertEqual(chart.sel[a], chart.bars.counts[a])
        QTest.mouseClick(v, Qt.MouseButton.LeftButton, ctrl, bar_point(b))
        H.settle(0.3)
        self.assertEqual(set(layer.selectedFeatureIds()), in_bins(a, b))
        QTest.mousePress(v, Qt.MouseButton.LeftButton, no, bar_point(c))
        QTest.mouseMove(v, bar_point(full[4]))
        QTest.mouseMove(v, bar_point(d))
        QTest.mouseRelease(v, Qt.MouseButton.LeftButton, no, bar_point(d))
        H.settle(0.3)
        self.assertEqual(set(layer.selectedFeatureIds()), in_bins(*range(c, d + 1)))
        # a selection made elsewhere shows as part of the bars
        some = [ft.id() for ft in feats[::7]]
        layer.selectByIds(some)
        H.settle(0.4)
        self.assertEqual(sum(chart.sel), len(some))
        self.assertIn(f"{len(some):,} of 800 records selected", chart.status.message.fullText())
        # hover card: range, records, selected
        v._hover = (a, None)
        title, lines, _foot = v.card_lines(a)
        self.assertIn(" – ", title)
        self.assertIn(f"{chart.bars.counts[a]:,} records", lines[0][1])
        v._hover = None
        # the card only answers on a bar, not in the empty space above it
        above = bar_point(a)
        self.assertEqual(v.hit(above), (a, None))
        above.setY(int(v._geo["plot"].top()) + 2)
        self.assertIsNone(v.hit(above))
        chart.clear_btn.click()
        self.assertEqual(layer.selectedFeatureCount(), 0)
        H.settle(0.3)
        self.assertEqual(sum(chart.sel), 0)

        # the chart follows the explorer's filters
        panel.set_column_filter("LAND_USE", FL.ColumnFilter("LAND_USE", F.TEXT, "in",
                                                            values=["RESIDENTIAL"]))
        res = sum(1 for ft in feats if ft["LAND_USE"] == "RESIDENTIAL")
        self.assertTrue(H.wait_for(lambda: idle(panel) and chart.col is not None
                                   and chart.col.scope == res and not chart.reader.busy, 60))
        self.assertIn(f"{res:,} of 800", chart.scope_label.fullText())
        panel.clear_filters()
        self.assertTrue(H.wait_for(lambda: idle(panel) and chart.col.scope == 800
                                   and not chart.reader.busy, 60))
        # Selected only
        layer.selectByIds([ft.id() for ft in feats[:100]])
        chart.selected_btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: chart.col.scope == 100 and not chart.reader.busy, 60))
        chart.selected_btn.setChecked(False)
        self.assertTrue(H.wait_for(lambda: chart.col.scope == 800 and not chart.reader.busy, 60))
        layer.removeSelection()

        # text: bars by records, top 10 + Other, NULL as a bar
        chart.set_field(lu)
        self.assertTrue(ready("LAND_USE"))
        self.assertEqual(chart.kind_combo.currentText(), "Bar chart")
        self.assertEqual(chart.bars.labels[0], "RESIDENTIAL")
        self.assertEqual(chart.bars.counts[0], res)
        self.assertTrue(chart.sort_row.isVisible() and not chart.bins_row.isVisible())
        # sorts as ArcGIS Pro's, with icons; most records first by default
        self.assertEqual([chart.sort_combo.itemData(i) for i in range(5)],
                         ["az", "za", "fewest", "count", "custom"])
        self.assertTrue(all(not chart.sort_combo.itemIcon(i).isNull() for i in range(5)))
        self.assertEqual(chart.sort_combo.currentData(), "count")
        chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("az"))
        self.assertEqual(chart.bars.labels, sorted(chart.bars.labels))
        chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("fewest"))
        self.assertEqual(chart.bars.counts, sorted(chart.bars.counts))
        # Custom order…: the order set in its window
        from attribute_table_studio.ui import explorer_dialogs as D
        wanted = ["RURAL", "COMMERCIAL"]

        class _Order:
            def __init__(self, panel, field, entries):
                self.entries = entries

            def exec(self):
                return True

            def order(self):
                rest = [label for label, _n in self.entries if label not in wanted]
                return wanted + rest

        real = D.CustomOrderDialog
        D.CustomOrderDialog = _Order
        try:
            chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("custom"))
            chart._on_sort_activated(chart.sort_combo.currentIndex())
        finally:
            D.CustomOrderDialog = real
        self.assertEqual(chart.bars.labels[:2], wanted)
        # the real window: move a value to the top, reset
        dlg = D.CustomOrderDialog(chart, "LAND_USE", chart.custom_order_entries())
        self.assertEqual(dlg.order()[:2], wanted)
        dlg.list.setCurrentRow(dlg.list.count() - 1)
        last = dlg.order()[-1]
        dlg._move("top")
        self.assertEqual(dlg.order()[0], last)
        dlg._reset()
        self.assertEqual(dlg.order()[0], "RESIDENTIAL")
        self.assertEqual(dlg.ok_btn.width(), dlg.cancel_btn.width())
        dlg.reject()
        chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("az"))
        QTest.mouseClick(v, Qt.MouseButton.LeftButton, no, bar_point(0))
        H.settle(0.3)
        self.assertEqual(layer.selectedFeatureCount(),
                         sum(1 for ft in feats if ft["LAND_USE"] == chart.bars.labels[0]))
        layer.removeSelection()
        # dates: records over time, every dated record in a period
        chart.set_field(sd)
        self.assertTrue(ready("SURVEY_DATE"))
        dated = sum(1 for ft in feats if not F.is_null(ft["SURVEY_DATE"]))
        self.assertEqual(chart.kind_combo.currentText(), "Histogram")      # by period
        self.assertEqual(sum(chart.bars.counts), dated)
        chart.interval_combo.setCurrentIndex(chart.interval_combo.findData("month"))
        self.assertEqual(chart.bars.interval, "month")
        self.assertRegex(chart.bars.labels[0], r"^\d{4}-\d{2}$")
        self.assertEqual(sum(chart.bars.counts), dated)
        self.assertTrue(chart.interval_combo.itemText(0).startswith("Auto ("))
        # ... or as a bar chart: one horizontal bar per period
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("bar"))
        self.assertEqual(chart.bars.chart, C.TIME)
        self.assertTrue(v.model.horizontal)
        self.assertEqual(sum(chart.bars.counts), dated)

        # text as a histogram: one column per value, the same counts
        chart.set_field(lu)
        self.assertTrue(ready("LAND_USE"))
        self.assertEqual(chart.kind_combo.currentData(), "bar")         # its usual chart
        before = list(zip(chart.bars.labels, chart.bars.counts))
        # the sort icons and axis names turn with the chart
        self.assertIn("records along the bottom (X axis)", chart.sort_combo.toolTip())
        bar_icon = chart.sort_combo.itemIcon(chart.sort_combo.findData("count")).cacheKey()
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("histogram"))
        self.assertIn("records up the side (Y axis)", chart.sort_combo.toolTip())
        self.assertNotEqual(
            chart.sort_combo.itemIcon(chart.sort_combo.findData("count")).cacheKey(), bar_icon)
        self.assertFalse(v.model.horizontal)
        self.assertEqual(list(zip(chart.bars.labels, chart.bars.counts)), before)
        self.assertTrue(chart.sort_row.isVisible())
        H.settle(0.2)                                        # drawn as columns
        QTest.mouseClick(v, Qt.MouseButton.LeftButton, no, bar_point(0))
        H.settle(0.3)
        self.assertEqual(layer.selectedFeatureCount(), chart.bars.counts[0])
        layer.removeSelection()

        # numbers as a bar chart: one bar per value; NULL can be a bar
        zc, oi = f.indexOf("ZONE_CODE"), f.indexOf("OWNER_ID")
        chart.set_field(zc)
        self.assertTrue(ready("ZONE_CODE"))
        self.assertEqual(chart.kind_combo.currentData(), "histogram")
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("bar"))
        codes = {}
        for ft in feats:
            codes[str(ft["ZONE_CODE"])] = codes.get(str(ft["ZONE_CODE"]), 0) + 1
        self.assertEqual(dict(zip(chart.bars.labels, chart.bars.counts)), codes)
        self.assertEqual(chart.sort_combo.itemText(0), "Smallest value first")
        chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("az"))
        self.assertEqual(chart.bars.labels, sorted(codes, key=int))
        H.settle(0.2)
        QTest.mouseClick(v, Qt.MouseButton.LeftButton, no, bar_point(1))
        H.settle(0.3)
        self.assertEqual(set(layer.selectedFeatureIds()),
                         {ft.id() for ft in feats if str(ft["ZONE_CODE"]) == chart.bars.labels[1]})
        chart.sort_combo.setCurrentIndex(chart.sort_combo.findData("count"))
        chart.set_field(oi)                                  # every OWNER_ID is NULL
        self.assertTrue(ready("OWNER_ID"))
        chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("bar"))
        chart.null_tick.setChecked(True)
        self.assertEqual(chart.bars.labels, ["NULL"])
        chart._select([chart.bars.null_bar], False)
        H.settle(0.3)
        self.assertEqual(layer.selectedFeatureCount(), 800)
        chart.null_tick.setChecked(False)
        layer.removeSelection()

        # Split by: each bar per land use, a legend entry selects one in every bar
        chart.set_field(sm)
        chart.split_combo.set_current_name("LAND_USE")
        self.assertTrue(ready("SCORE_MAX", "LAND_USE"))
        bars = chart.bars
        self.assertEqual(bars.series[0], "RESIDENTIAL")
        self.assertEqual([sum(r) for r in bars.split], bars.counts)
        self.assertFalse(chart.colour_btn.isEnabled())
        chart._on_series_picked(0, False)
        H.settle(0.3)
        self.assertEqual(layer.selectedFeatureCount(), res)
        self.assertEqual(sum(chart.sel), res)
        chart.split_mode.setCurrentIndex(1)                       # side by side
        H.settle(0.2)
        self.assertEqual(v.model.split_mode, "side")
        self.assertTrue(chart.split_mode_row.isVisible())
        layer.removeSelection()
        # ... or by an expression from the QGIS expression builder
        from attribute_table_studio.ui import explorer_dialogs as D

        class _Builder:
            def __init__(self, panel, title, text="", order=True):
                self.order = order

            def exec(self):
                return True

            def expression(self):
                return '"ZONE_CODE" > 2'

        real = D.ExpressionDialog
        D.ExpressionDialog = _Builder
        try:
            chart.edit_split_expression()
        finally:
            D.ExpressionDialog = real
        self.assertTrue(ready("SCORE_MAX", '"ZONE_CODE" > 2'))
        high = sum(1 for ft in feats if ft["ZONE_CODE"] > 2)
        series = dict(zip(chart.bars.series, map(sum, zip(*chart.bars.split))))
        self.assertEqual(series, {"true": high, "false": 800 - high})
        self.assertIn('by "ZONE_CODE" > 2', chart.title_edit.text())

        # exports: PNG, SVG, the bars as CSV with the scope beside it
        png, svg, csv_path = (os.path.join(OUT, n) for n in ("c.png", "c.svg", "c.csv"))
        _next_save.extend([png])
        chart.export_image("png")
        _next_save.extend([svg])
        chart.export_image("svg")
        _next_save.extend([csv_path])
        chart.export_csv()
        for path in (png, svg, csv_path, os.path.join(OUT, "c_scope.txt")):
            self.assertTrue(os.path.getsize(path) > 0, path)
        with open(csv_path, encoding="utf-8-sig") as fh:
            head = fh.readline()
        self.assertTrue(head.startswith("Bar,From,To,Records,Share (%),Selected,"))
        self.assertIn('""ZONE_CODE"" > 2: true', head)          # the Split by expression

        # bar values show only with "Label bars with their count"
        chart.split_combo.set_current_name(None)
        chart.set_field(lu)
        self.assertTrue(ready("LAND_USE"))
        H.settle(0.2)
        plain = v.grab().toImage()
        chart.labels_tick.setChecked(True)
        H.settle(0.2)
        self.assertNotEqual(v.grab().toImage(), plain)
        chart.labels_tick.setChecked(False)
        H.settle(0.2)
        self.assertEqual(v.grab().toImage(), plain)
        chart.set_field(sm)

        # the statistics popup has a Chart… button
        self.assertTrue(ready("SCORE_MAX"))
        H.wait_for(lambda: sm in panel.profiles, 30)
        panel._show_profile_details(sm)
        H.settle(0.2)
        pops = [w for w in QApplication.topLevelWidgets()
                if isinstance(w, ProfileDetails) and w.isVisible()]
        self.assertEqual(len(pops), 1)
        self.assertEqual(pops[0].chart_btn.text().strip(), "Chart…")
        pops[0].chart_btn.click()
        H.settle(0.2)
        self.assertFalse(any(isinstance(w, ProfileDetails) and w.isVisible()
                             for w in QApplication.topLevelWidgets()))     # closed
        # Dock and back, then the chart closes with its explorer
        chart.dock_btn.click()
        H.settle(0.2)
        self.assertTrue(panel.chart_host.is_docked)
        chart.dock_btn.click()
        H.settle(0.2)
        self.assertFalse(panel.chart_host.is_docked)
        self.plugin.explorers[layer.id()].close()
        H.settle(0.3)
        self.assertIsNone(panel.chart_host)

    def test_13r_freeze_columns(self):
        """Freeze / Unfreeze Column (as ArcGIS Pro): only the view changes."""
        from qgis.PyQt.QtTest import QTest
        layer = H.demo_layer(n=300, name="Frozen parcels")
        order_before = layer.fields().names()
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        win = self.plugin.explorers[layer.id()]._window
        win.resize(1000, 560)
        H.settle(0.3)
        f = layer.fields()
        lu, sd, zc = (f.indexOf(n) for n in ("LAND_USE", "SURVEY_DATE", "ZONE_CODE"))
        h, fz = panel.header, panel.frozen

        def menu_texts(logical, header=None):
            shown = []
            original = QMenu.exec
            QMenu.exec = lambda menu, *a: shown.extend(
                a for a in menu.actions() if not a.isSeparator())
            try:
                hdr = header or h
                if hdr is h:
                    panel.view.scrollTo(panel.model.index(0, logical))
                    H.pump()
                panel._header_menu(QPoint(hdr.sectionViewportPosition(logical) + 5,
                                          hdr.name_top() + 5), hdr)
            finally:
                QMenu.exec = original
            return {a.text(): a for a in shown}

        # freeze from the column name's menu: it goes to the left edge
        acts = menu_texts(lu)
        self.assertNotIn("Unfreeze All Columns", acts)
        acts["Freeze Column LAND_USE"].trigger()
        menu_texts(sd)["Freeze Column SURVEY_DATE"].trigger()
        H.settle(0.3)
        self.assertEqual([h.logicalIndex(v) for v in range(2)], [lu, sd])
        self.assertEqual(fz.names, ["LAND_USE", "SURVEY_DATE"])
        self.assertEqual(layer.fields().names(), order_before)      # data untouched
        self.assertTrue(fz.view.isVisible())
        self.assertEqual(fz.view.width(), h.sectionSize(lu) + h.sectionSize(sd))
        self.assertIs(h.cards[lu].parent(), fz.header.viewport())    # its card and filter
        self.assertIs(h.cells[sd].parent(), fz.header.viewport())
        # scrolling sideways leaves the strip; up and down moves both
        geometry = fz.view.geometry()
        bar = panel.view.horizontalScrollBar()
        bar.setValue(bar.maximum())
        H.settle(0.2)
        self.assertEqual(fz.view.geometry(), geometry)
        self.assertTrue(h.cards[lu].isVisible() and h.cells[lu].isVisible())
        panel.view.verticalScrollBar().setValue(40)
        self.assertEqual(fz.view.verticalScrollBar().value(), 40)
        # going to a scrolling column keeps it clear of the strip
        bar.setValue(bar.maximum())
        panel.view.scrollTo(panel.model.index(0, f.indexOf("PARCEL_ID")))
        self.assertGreaterEqual(panel.view.columnViewportPosition(f.indexOf("PARCEL_ID")),
                                fz.width())
        # the frozen column's filter, sort and cells work as before
        h.cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "in",
                                               values=["RESIDENTIAL"]))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        res = sum(1 for ft in layer.getFeatures() if ft["LAND_USE"] == "RESIDENTIAL")
        self.assertEqual(len(panel.model.fids), res)
        panel.clear_filters()
        self.assertTrue(H.wait_for(lambda: idle(panel) and len(panel.model.fids) == 300))
        panel._on_sort_clicked(sd)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(panel.sort, (sd, False))
        self.assertEqual(fz.header.sortIndicatorSection(), sd)
        H.settle(0.2)
        cell = fz.view.visualRect(panel.model.index(2, lu)).center()
        QTest.mouseClick(fz.view.viewport(), Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier, cell)
        self.assertEqual((panel.view.currentIndex().row(), panel.view.currentIndex().column()),
                         (2, lu))                                   # one framed cell
        # the arrows go on from the strip into the scrolling columns
        QTest.keyClick(fz.view, Qt.Key.Key_Right)
        self.assertEqual(panel.view.currentIndex().column(), sd)
        QTest.keyClick(fz.view, Qt.Key.Key_Right)
        self.assertEqual(panel.view.currentIndex().column(), h.logicalIndex(2))
        QTest.keyClick(panel.view, Qt.Key.Key_Left)
        self.assertEqual(panel.view.currentIndex().column(), sd)
        # editing a frozen cell
        layer.startEditing()
        H.settle(0.3)
        fz.view.edit(panel.model.index(2, lu))
        self.assertTrue(panel.cell_editing())
        panel.flush_editors()
        self.assertFalse(panel.cell_editing())
        layer.rollBack()
        H.settle(0.2)
        # kept with the layer: close and open the Explorer again
        self.plugin.explorers[layer.id()].close()
        H.settle(0.3)
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        h, fz = panel.header, panel.frozen
        self.assertEqual(fz.names, ["LAND_USE", "SURVEY_DATE"])
        self.assertEqual([h.logicalIndex(v) for v in range(2)], [lu, sd])
        # unfreeze from the strip's menu: it stays first among the scrolling ones
        acts = menu_texts(lu, fz.header)
        acts["Unfreeze Column LAND_USE"].trigger()
        H.settle(0.2)
        self.assertEqual(fz.names, ["SURVEY_DATE"])
        self.assertEqual([h.logicalIndex(v) for v in range(2)], [sd, lu])
        self.assertIs(h.cards[lu].parent(), h.viewport())            # back in the table
        menu_texts(zc)["Freeze Column ZONE_CODE"].trigger()
        menu_texts(zc, fz.header)["Unfreeze All Columns"].trigger()
        H.settle(0.2)
        self.assertEqual(fz.names, [])
        self.assertFalse(fz.view.isVisible())
        self.assertEqual([h.logicalIndex(v) for v in range(2)], [sd, zc])
        self.assertEqual(layer.fields().names(), order_before)
        self.plugin.explorers[layer.id()].close()
        QgsProject.instance().removeMapLayer(layer.id())

    def test_13g_round9_ui(self):
        from qgis.PyQt.QtWidgets import QToolButton
        from attribute_table_studio.ui import icons
        from attribute_table_studio.ui.explorer_header import ValuePicker
        from attribute_table_studio.ui.help_dialog import HelpDialog
        # Explorer: deselect this layer (icon) vs all layers (Select menu)
        other = H.demo_layer(n=50, seed=3, name="Other parcels")
        panel = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.layer.selectByIds(panel.model.fids[:3])
        other.selectByIds(list(other.allFeatureIds())[:2])
        H.settle(0.1)
        self.assertTrue(panel.deselect_btn.isEnabled())
        self.assertFalse(icons.native(icons.DESELECT_LAYER).isNull())
        panel.deselect_btn.click()
        self.assertEqual(self.layer.selectedFeatureCount(), 0)
        self.assertEqual(other.selectedFeatureCount(), 2)       # only this layer
        self.assertFalse(panel.deselect_btn.isEnabled())
        self.layer.selectByIds(panel.model.fids[:3])
        panel.select_menu.aboutToShow.emit()
        act = next(a for a in panel.select_menu.actions()
                   if a.text() == "Deselect Features from All Layers")
        act.trigger()
        self.assertEqual((self.layer.selectedFeatureCount(), other.selectedFeatureCount()),
                         (0, 0))
        # map filter: QGIS's 'filtered layer' funnel; More: drawn bars, no arrow room
        self.assertEqual(icons.QUERY, "/mIndicatorFilter.svg")
        self.assertFalse(icons.native(icons.QUERY).isNull())
        self.assertFalse(panel.edit_bar.more_btn.icon().isNull())
        self.assertIn("padding-right: 0px", panel.edit_bar.more_btn.styleSheet())
        # the value list's Apply is as tall as All / None
        picker = ValuePicker("LAND_USE", [("RESIDENTIAL", "RESIDENTIAL", 5)], [],
                             panel.theme, panel.metrics, panel)
        buttons = {b.text().strip(): b for b in picker.findChildren(QToolButton)}
        self.assertEqual(buttons["Apply"].height() or buttons["Apply"].maximumHeight(),
                         buttons["All"].maximumHeight())
        picker.close()
        QgsProject.instance().removeMapLayer(other.id())
        # Compare: narrow field picker, icon export, a minimum width the rows fit
        compare = self.plugin.open_compare()
        self.assertLess(compare.filter_field_combo.width() or
                        compare.filter_field_combo.maximumWidth(), 170)
        self.assertEqual(compare.export_btn.text().strip(), "")
        self.assertIsNotNone(compare.export_btn.menu())
        win = self.plugin.compare_host._window
        win.resize(400, 700)
        H.settle(0.3)
        self.assertGreater(win.width(), 400)
        for bar in (compare.filter_field_combo.parentWidget(),
                    compare.review_scope.parentWidget()):
            self.assertGreaterEqual(bar.width(), bar.minimumSizeHint().width())
        # short kinds; the filter box shrinks down to the field picker's width
        self.assertEqual(compare.filter_boxes["Deleted"].text(), "Deleted")
        self.assertEqual(compare.search_edit.minimumWidth(),
                         compare.filter_field_combo.width())
        # at its narrowest the review buttons and Field(s) to Compare are
        # icons, named in their tooltips; with room again the labels return
        icon_only = Qt.ToolButtonStyle.ToolButtonIconOnly
        accept = compare.accept_btn
        self.assertEqual(accept.toolButtonStyle(), icon_only)
        self.assertEqual(compare.column_filter_btn.toolButtonStyle(), icon_only)
        self.assertTrue(accept.toolTip().startswith("Accept\n"))
        win.resize(2400, 700)
        H.settle(0.3)
        self.assertNotEqual(accept.toolButtonStyle(), icon_only)
        self.assertNotEqual(compare.column_filter_btn.toolButtonStyle(), icon_only)
        self.assertGreaterEqual(accept.width(), 96)
        self.assertFalse(accept.toolTip().startswith("Accept\n"))
        # 'Compare geometry' off: its options are greyed but ticked for the
        # OLD layer (a polygon: all four)
        compare.old_table_combo.setLayer(self.layer)
        H.settle(0.1)
        self.assertFalse(compare.geom_compare_cb.isChecked())
        for cb in compare._geom_checkboxes():
            self.assertTrue(cb.isChecked() and not cb.isEnabled(), cb.text())
        # a greyed, unticked box still draws its frame (it used to vanish)
        cb = compare.geom_length_cb
        cb.setChecked(False)
        image = cb.grab().toImage()
        frame = {image.pixel(x, y) for x in range(min(18, image.width()))
                 for y in range(image.height())}
        self.assertGreater(len(frame), 2)
        cb.setChecked(True)
        compare.geom_compare_cb.setChecked(True)
        self.assertTrue(all(cb.isEnabled() for cb in compare._geom_checkboxes()))
        compare.geom_compare_cb.setChecked(False)
        self.assertFalse(any(cb.isEnabled() for cb in compare._geom_checkboxes()))
        # Pivot: icon export, then Help, then Dock
        pivot = self.plugin.open_pivot(self.layer)
        x = lambda w: w.mapTo(pivot, QPoint(0, 0)).x()
        self.assertLess(x(pivot.export_btn), x(pivot.help_btn))
        self.assertLess(x(pivot.help_btn), x(pivot.dock_btn))
        self.assertEqual(pivot.export_btn.text().strip(), "")
        pivot.help_btn.click()
        H.settle(0.1)
        dlg = pivot._help_dialog
        self.assertIsInstance(dlg, HelpDialog)
        self.assertTrue(dlg.isVisible())
        self.assertIn("Pivot Table", dlg.windowTitle())
        dlg.close()

    def test_13h_view_menu_like_qgis_and_form_size(self):
        from qgis.PyQt.QtWidgets import QScrollArea
        from attribute_table_studio.ui import explorer_dialogs as D
        from attribute_table_studio.ui import icons
        panel = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        # View: the scopes named, drawn and ordered as in the QGIS attribute table
        panel.view_menu.aboutToShow.emit()
        actions = {a.text(): a for a in panel.view_menu.actions() if not a.isSeparator()}
        names = [a.text() for a in panel.view_menu.actions() if a.text().startswith("Show ")]
        self.assertEqual(names, ["Show All Features", "Show Selected Features",
                                 "Show Features Visible on Map",
                                 "Show Features with Failing Constraints",
                                 "Show Edited and New Features"])
        for name, icon in (("Show Features Visible on Map", icons.TABLE_VISIBLE),
                           ("Show Features with Failing Constraints", icons.TABLE_INVALID),
                           ("Show Edited and New Features", icons.TABLE_EDITED),
                           ("Advanced Filter (Expression)", icons.ADVANCED_FILTER),
                           ("Stored Filter Expressions…", icons.STORED_FILTER)):
            self.assertIn(name, actions)
            self.assertFalse(icons.native(icon).isNull())
        # Select: QGIS's Title Case names, "…" where a dialog opens
        panel.select_menu.aboutToShow.emit()
        self.assertIn("Invert Selection", [a.text() for a in panel.select_menu.actions()])
        # the row menu uses the same names
        from attribute_table_studio.ui import widgets as W
        shown = []
        original = QMenu.exec
        QMenu.exec = lambda menu, *a: shown.extend(
            act.text() for act in menu.actions() if not act.isSeparator())
        try:
            panel._context_menu(QPoint(0, 0), 0, 0)
        finally:
            QMenu.exec = original
        for text in ("Open Form…", "Copy Cell Value", "Select All Features",
                     "Invert Selection", "Deselect All Features", "Zoom to Selection"):
            self.assertIn(text, shown)
        self.assertEqual(W.title_case("Show or hide the Quick update row"),
                         "Show or Hide the Quick Update Row")
        self.assertEqual(W.title_case("Set cell to NULL"), "Set Cell to NULL")
        # Filter by form / Select by value open showing the whole form width
        # (within the screen)
        for select in (False, True):
            dlg = D.FormSearchDialog(panel, select=select)
            dlg.show()
            H.settle(0.3)
            scroll = dlg.findChild(QScrollArea)
            widest = int(dlg.screen().availableGeometry().width() * 0.9)
            if dlg.width() < widest:
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            else:
                self.assertEqual(dlg.width(), widest)
            self.assertGreater(dlg.width(), 560)
            dlg.close()

    def test_13i_go_to_field_and_value_list(self):
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication
        from attribute_table_studio.ui import widgets as W
        panel = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.plugin.explorers[self.layer.id()]._window.resize(900, 700)
        H.settle(0.3)
        box = panel.field_box
        # a filter-cell look, as tall as the top row buttons
        self.assertEqual(box.objectName(), "gtsFilterCell")
        self.assertEqual(box.height(), panel.summary_btn.height())
        # every field with its QGIS type; a hidden column is listed, flagged
        fields = self.layer.fields()
        target = fields.indexOf("SCORE_5Y")
        panel._hide_column(target)
        box.edit.setFocus()
        box.show_list("")
        self.assertTrue(box.list.isVisible())
        model = box.list.model()
        self.assertEqual(model.rowCount(), fields.count())

        def item(name):
            return next(model.index(i, 0) for i in range(model.rowCount())
                        if model.index(i, 0).data() == name)
        self.assertEqual(item("SCORE_5Y").data(W.FIELD_TYPE), fields.at(target).typeName())
        self.assertTrue(item("SCORE_5Y").data(W.FIELD_HIDDEN))
        self.assertFalse(item("LAND_USE").data(W.FIELD_HIDDEN))
        self.assertIn(f"{fields.count()} fields", box.list.count_label.text())
        # hovering a row makes it current (blue); the footer keys are not cut
        lst = box.list
        QTest.mouseMove(lst.viewport(), lst.visualRect(model.index(3, 0)).center())
        self.assertEqual(lst.currentIndex().row(), 3)
        keys = lst.keys_label
        self.assertGreaterEqual(keys.width(), keys.sizeHint().width())
        self.assertLessEqual(keys.geometry().right(), lst.footer.width())
        # typing narrows it (anywhere in the name, any case)
        box.edit.setText("score")
        box.show_list("score")
        self.assertEqual(model.rowCount(), 4)
        self.assertIn(f"4 of {fields.count()} fields", box.list.count_label.text())
        # choosing: the hidden column is shown again, scrolled to the middle
        # (as near as the scroll goes) and flashed; the box clears
        box.choose(item("SCORE_5Y"))
        H.settle(0.2)
        h, bar = panel.header, panel.view.horizontalScrollBar()
        half = panel.view.viewport().width() // 2

        def middle(logical):
            want = h.sectionPosition(logical) + h.sectionSize(logical) // 2 - half
            return max(0, min(want, bar.maximum()))
        self.assertFalse(h.isSectionHidden(target))
        self.assertGreater(bar.value(), 0)
        self.assertLessEqual(abs(bar.value() - middle(target)), 1)
        self.assertTrue(h._flash.isVisible())
        self.assertEqual(box.edit.text(), "")
        self.assertFalse(box.list.isVisible())
        # Enter without opening the list: the first field that matches
        bar.setValue(0)
        box.edit.setText("land")
        QTest.keyClick(box.edit, Qt.Key.Key_Return)
        H.settle(0.2)
        lu = fields.indexOf("LAND_USE")
        self.assertLessEqual(abs(bar.value() - middle(lu)), 1)
        # a column away from both ends sits right in the middle of the view
        mid = fields.indexOf("SCORE_MIN")
        panel.go_to_field(mid)
        H.settle(0.2)
        self.assertTrue(0 < bar.value() < bar.maximum())
        centre = h.sectionViewportPosition(mid) + h.sectionSize(mid) // 2
        self.assertLessEqual(abs(centre - half), 1)
        # the value list of a column is a fixed, compact ~6.5 rows
        panel.header.cells[fields.indexOf("PARCEL_ID")]._pick_values()
        H.settle(0.3)
        pick = [w for w in QApplication.topLevelWidgets()
                if type(w).__name__ == "ValuePicker" and w.isVisible()][-1]
        row_h, head = pick.tree.sizeHintForRow(0), pick.tree.header().height()
        self.assertGreaterEqual(pick.tree.height(), head + 6 * row_h)
        self.assertLessEqual(pick.tree.height(), head + 7 * row_h + 4)
        pick.close()

    def test_13j_cell_frame_and_copy_while_browsing(self):
        from qgis.PyQt.QtCore import QEvent, QPointF
        from qgis.PyQt.QtGui import QGuiApplication, QKeyEvent, QMouseEvent
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QApplication
        panel = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        view, layer, model = panel.view, self.layer, panel.model
        layer.removeSelection()
        col = layer.fields().indexOf("LAND_USE")
        # a click on a cell only frames it, as while editing
        rect = view.visualRect(model.index(2, col))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertEqual(layer.selectedFeatureCount(), 0)
        self.assertEqual((view.currentIndex().row(), view.currentIndex().column()),
                         (2, col))
        # the arrow keys move the frame, still without selecting
        QTest.keyClick(view, Qt.Key.Key_Down)
        self.assertEqual(view.currentIndex().row(), 3)
        self.assertEqual(layer.selectedFeatureCount(), 0)
        # Ctrl+C copies the framed cell (the Explorer's key, not QGIS's
        # Copy Features)
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_C,
                             Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(view.event(override))
        clip = QGuiApplication.clipboard()
        clip.setText("")
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(clip.text(), model.display_text(3, col))
        self.assertTrue(clip.text())
        # the row numbers select: a click, then a drag extends the range
        header = view.row_header

        def mouse(kind, row, buttons):
            y = header.sectionViewportPosition(row) + header.sectionSize(row) // 2
            QApplication.sendEvent(header.viewport(), QMouseEvent(
                kind, QPointF(8, y), Qt.MouseButton.LeftButton, buttons,
                Qt.KeyboardModifier.NoModifier))
        left, none = Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton
        mouse(QEvent.Type.MouseButtonPress, 1, left)
        mouse(QEvent.Type.MouseMove, 4, left)
        mouse(QEvent.Type.MouseButtonRelease, 4, none)
        self.assertEqual(set(layer.selectedFeatureIds()), set(model.fids[1:5]))
        # Ctrl+Shift+C copies the selected rows (names + 4 rows)
        QTest.keyClick(view, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier
                       | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(len(clip.text().splitlines()), 5)
        self.assertEqual(panel.copy_action.shortcut().toString(), "Ctrl+Shift+C")
        layer.removeSelection()

    def test_13k_load_active_layer(self):
        panel = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        btn = panel.active_btn
        # between the Layer box and the record count
        x = lambda w: w.mapTo(panel, QPoint(0, 0)).x()
        self.assertLess(x(panel.layer_combo), x(btn))
        self.assertLess(x(btn), x(panel.count_label))
        # greyed out, saying why, when there is nothing to load
        self.iface.setActiveLayer(self.layer)
        self.assertFalse(btn.isEnabled())
        self.assertIn("is already shown", btn.toolTip())
        self.iface.setActiveLayer(None)
        self.assertFalse(btn.isEnabled())
        self.assertIn("Select a layer", btn.toolTip())
        # another layer: one click loads it here, as the Layer box would
        other = H.demo_layer(n=40, seed=3, name="Other parcels")
        self.iface.setActiveLayer(other)
        self.assertTrue(btn.isEnabled())
        self.assertIn("Other parcels", btn.toolTip())
        btn.click()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertIs(panel.layer, other)
        self.assertIs(panel.layer_combo.currentLayer(), other)
        self.assertIs(self.plugin.explorer_for(other), panel)
        self.assertFalse(btn.isEnabled())
        # and back
        self.iface.setActiveLayer(self.layer)
        btn.click()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertIs(panel.layer, self.layer)
        QgsProject.instance().removeMapLayer(other.id())

    def test_13l_sort_shows_the_start(self):
        layer = H.demo_layer(n=3000, seed=11, name="Sort parcels")
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        view, model = panel.view, panel.model
        bar = view.verticalScrollBar()

        def sort(name):
            panel._on_sort_clicked(layer.fields().indexOf(name))
            self.assertTrue(H.wait_for(lambda: idle(panel)))
            H.settle(0.1)
        # the table has the keyboard: Qt frames its first cell. A sort keeps
        # the frame on that feature but stays at the top (it used to jump
        # to wherever the feature went)
        view.setFocus()
        view.setCurrentIndex(model.index(0, 0))
        fid = model.fid(0)
        sort("SCORE_MAX")
        self.assertEqual(bar.value(), 0)
        self.assertGreater(model.row_of(fid), 0)
        self.assertEqual(view.currentIndex().row(), model.row_of(fid))
        # with a selection, a sort shows the first selected feature
        layer.selectByIds([model.fid(1500), model.fid(2500)])
        bar.setValue(0)
        sort("SCORE_MIN")
        first = min(model.row_of(f) for f in layer.selectedFeatureIds())
        self.assertGreater(first, 0)
        self.assertTrue(view.rowAt(0) <= first <= view.rowAt(view.viewport().height() - 1))
        layer.removeSelection()
        # a data refresh (an edit, Quick update) keeps the view where it is,
        # also when the framed feature is off screen (no jump back to it) ...
        view.setCurrentIndex(model.index(40, 0))
        bar.setValue(bar.maximum())
        panel._on_data_settled()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        H.settle(0.1)
        self.assertEqual(bar.value(), bar.maximum())
        # ... and follows the framed feature when an edit moves it out of view
        # (the table is sorted by SCORE_MIN)
        bar.setValue(0)
        H.settle(0.1)
        view.setCurrentIndex(model.index(2, 0))
        fid = model.fid(2)
        layer.startEditing()
        layer.changeAttributeValue(fid, layer.fields().indexOf("SCORE_MIN"), 99999.0)
        self.assertTrue(H.wait_for(lambda: idle(panel) and model.row_of(fid) > 2000, 20))
        H.settle(0.3)
        row = model.row_of(fid)
        self.assertTrue(view.rowAt(0) <= row <= view.rowAt(view.viewport().height() - 1))
        layer.rollBack()
        self.plugin.explorers[layer.id()].close()
        QgsProject.instance().removeMapLayer(layer.id())

    def test_13m_find_bar(self):
        from qgis.PyQt.QtCore import QEvent
        from qgis.PyQt.QtGui import QKeyEvent
        from qgis.PyQt.QtTest import QTest
        panel = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        view, model, bar = panel.view, panel.model, panel.find_bar
        layer = self.layer
        lu = layer.fields().indexOf("LAND_USE")

        def found():
            return H.wait_for(lambda: not bar._timer.isActive() and not bar._runner.busy)
        # the search icon sits left of the Query Builder button
        x = lambda w: w.mapTo(panel, QPoint(0, 0)).x()
        self.assertLess(x(panel.find_btn), x(panel.query_btn))
        # Ctrl+F belongs to the Explorer (not QGIS) and opens the bar
        view.setFocus()
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_F,
                             Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(panel.event(override))
        QTest.keyClick(view, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(bar.isVisible())
        self.assertTrue(panel.find_btn.isChecked())
        self.assertFalse(bar.replace_row.isVisible())
        self.assertTrue(bar.find_box.edit.hasFocus())
        # top right of the rows
        vp = view.viewport().geometry()
        self.assertLessEqual(bar.geometry().right(), vp.right())
        self.assertGreaterEqual(bar.geometry().top(), vp.top())
        # every cell shown whose text contains it, case ignored, row by row
        expected = [(r, c) for r in range(model.rowCount())
                    for c in panel._shown_columns()
                    if "industrial" in model.display_text(r, c).lower()
                    and not F.is_null(model.raw_value(r, c))]
        QTest.keyClicks(bar.find_box.edit, "indus")
        self.assertTrue(found())
        self.assertEqual(bar.count_label.text(), f"1 of {len(expected)}")
        first = view.currentIndex()
        self.assertEqual((first.row(), first.column()), expected[0])
        self.assertEqual(model.highlight(*expected[0]), 2)
        self.assertEqual(model.highlight(*expected[1]), 1)
        self.assertEqual(model.highlight(expected[0][0], 0), 0)
        # Enter: next; Shift+Enter: previous (wrapping round)
        QTest.keyClick(bar.find_box.edit, Qt.Key.Key_Return)
        self.assertEqual((view.currentIndex().row(), view.currentIndex().column()),
                         expected[1])
        self.assertEqual(bar.count_label.text(), f"2 of {len(expected)}")
        for _ in range(2):
            QTest.keyClick(bar.find_box.edit, Qt.Key.Key_Return,
                           Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual((view.currentIndex().row(), view.currentIndex().column()),
                         expected[-1])
        # match case, whole cell, regular expression
        bar.case_btn.setChecked(True)
        self.assertTrue(found())
        self.assertEqual(bar.count_label.text(), "No results")
        bar.case_btn.setChecked(False)
        bar.whole_btn.setChecked(True)
        bar.find_box.edit.setText("industrial")
        self.assertTrue(found())
        whole = sum(1 for r in range(model.rowCount())
                    if model.display_text(r, lu).lower() == "industrial")
        self.assertTrue(bar.count_label.text().endswith(f"of {whole}"))
        bar.whole_btn.setChecked(False)
        bar.regex_btn.setChecked(True)
        bar.find_box.edit.setText("(")
        self.assertTrue(found())
        self.assertEqual(bar.count_label.text(), "Invalid pattern")
        self.assertTrue(bar.find_box.property("error"))
        bar.find_box.edit.setText("^P-17000[1-3]$")
        self.assertTrue(found())
        self.assertTrue(bar.count_label.text().endswith("of 3"))
        bar.regex_btn.setChecked(False)
        # a sort searches again: the matches follow their rows
        bar.find_box.edit.setText("indus")
        self.assertTrue(found())
        panel._on_sort_clicked(lu)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertTrue(found())
        rows = [r for r in range(model.rowCount())
                if "industrial" in model.display_text(r, lu).lower()]
        self.assertEqual(model.highlight(rows[0], lu), 1 if view.currentIndex().row()
                         != rows[0] else 2)
        panel._on_sort_clicked(lu)
        panel._on_sort_clicked(lu)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        # Ctrl+H opens the Replace line; not editing: greyed, Start editing
        QTest.keyClick(bar.find_box.edit, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(bar.replace_row.isVisible())
        self.assertFalse(bar.replace_btn.isEnabled())
        self.assertFalse(bar.replace_all_btn.isEnabled())
        self.assertTrue(bar.start_btn.isVisible())
        # Escape closes it and clears the highlights
        QTest.keyClick(bar.find_box.edit, Qt.Key.Key_Escape)
        self.assertFalse(bar.isVisible())
        self.assertIsNone(model.highlight)
        self.assertFalse(panel.find_btn.isChecked())
        # the icon opens it again, with the last search
        panel.find_btn.click()
        self.assertTrue(bar.isVisible())
        self.assertTrue(found())
        self.assertIsNotNone(model.highlight)
        panel.find_btn.click()
        self.assertFalse(bar.isVisible())

    def test_13f_map_filter(self):
        layer = H.demo_layer(n=1500, seed=5, name="Map filter parcels")
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        btn = panel.query_btn

        def x(widget):
            return widget.mapTo(panel, QPoint(0, 0)).x()
        # top row, right aligned: View | Zoom | Selected only | Deselect |
        # Select | Go to field | Summary | Pivot Table | Clear filters |
        # Columns | Export | query | dock
        order = [panel.view_btn, panel.zoom_btn, panel.selected_btn, panel.deselect_btn,
                 panel.select_btn, panel.field_box, panel.summary_btn, panel.pivot_btn,
                 panel.clear_btn, panel.columns_btn, panel.export_btn, btn, panel.dock_btn]
        xs = [x(w) for w in order]
        self.assertEqual(xs, sorted(xs))
        for icon_only in (panel.zoom_btn, panel.selected_btn, panel.deselect_btn,
                          panel.clear_btn, panel.columns_btn, panel.export_btn):
            self.assertEqual(icon_only.text().strip(), "")
        self.assertEqual(panel.pivot_btn.text().strip(), "Pivot Table")
        self.assertEqual([a.text() for a in panel.export_btn.menu().actions()
                          if not a.isSeparator()],
                         ["Excel (.xlsx)…", "CSV (.csv)…", "Copy Selected Rows"])
        self.assertFalse(btn.isEnabled())            # no filter yet
        total = layer.featureCount()
        lu = layer.fields().indexOf("LAND_USE")
        fl = layer.fields().indexOf("SCORE_MAX")
        cells = panel.header.cells
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "RESIDENTIAL"))
        cells[fl].set_filter(FL.ColumnFilter("SCORE_MAX", F.REAL, "gt", "60"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        shown = len(panel.model.fids)
        self.assertTrue(btn.isEnabled())

        # on: the layer's Query Builder filter is the table filter, readable
        btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(layer.subsetString(),
                         '("LAND_USE" = \'RESIDENTIAL\') AND ("SCORE_MAX" > 60)')
        self.assertEqual(layer.featureCount(), shown)
        self.assertEqual(len(panel.model.fids), shown)
        self.assertIn(f"{shown:,} of {total:,}", panel.status.message.fullText())
        self.assertIn("Map filter on", panel.status.extra.fullText())
        held = SUB.held(layer)                     # kept in memory, not on the layer
        self.assertEqual((held.base, held.applied), ("", layer.subsetString()))
        self.assertIsNone(layer.customProperty(SUB.STATE))

        # the table's own reads lift the map filter unseen: listeners only
        # ever see the map filter itself
        seen = []

        def note():
            seen.append(layer.subsetString())
        layer.subsetStringChanged.connect(note)
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        panel._values_for(lu)
        layer.subsetStringChanged.disconnect(note)
        self.assertTrue(all(s == held.applied for s in seen), seen)

        # the table still reaches every record: loosening a filter works
        cells[fl].set_filter(None)
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertGreater(len(panel.model.fids), shown)
        self.assertEqual(layer.subsetString(), '"LAND_USE" = \'RESIDENTIAL\'')
        self.assertEqual(layer.featureCount(), len(panel.model.fids))
        self.assertEqual(panel.model.raw_value(0, lu), "RESIDENTIAL")
        self.assertIn("INDUSTRIAL", [e[1] for e in panel._values_for(lu)])

        # case-insensitive contains: SQL when exact, the ids otherwise
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "contains", "dent"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(layer.subsetString(), '"LAND_USE" LIKE \'%dent%\'')
        self.assertEqual(layer.featureCount(), len(panel.model.fids))
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "contains", "dÉnt"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), 0)
        self.assertEqual(layer.subsetString(), "1 = 0")
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "contains", "dent"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        kept = len(panel.model.fids)

        # off: the layer is back, the table keeps its filters - and the status
        # bar says so on both sides
        btn.setChecked(False)
        self.assertEqual(layer.subsetString(), "")
        self.assertEqual(layer.featureCount(), total)
        self.assertEqual(len(panel.model.fids), kept)
        self.assertIsNotNone(cells[lu].current_filter())
        self.assertIsNone(SUB.held(layer))
        self.assertIn("Map filter off", panel.status.message.fullText())
        self.assertNotIn("Map filter on", panel.status.extra.fullText())

        # temporary: a filter the layer already has stays underneath, a
        # project saved meanwhile keeps only that filter, and closing the
        # explorer gives it back
        layer.setSubsetString('"ZONE_CODE" > 1')
        self.assertTrue(H.wait_for(lambda: idle(panel) and not panel._data_timer.isActive()))
        btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        mapped = '("ZONE_CODE" > 1) AND ("LAND_USE" LIKE \'%dent%\')'
        self.assertEqual(layer.subsetString(), mapped)
        path = os.path.join(tempfile.mkdtemp(), "map_filter.qgs")
        self.assertTrue(QgsProject.instance().write(path))
        QgsProject.instance().setFileName("")
        with open(path, encoding="utf-8") as fh:
            saved = fh.read()
        self.assertNotIn("%dent%", saved)
        self.assertIn("ZONE_CODE&quot; > 1", saved.replace("&gt;", ">"))
        self.assertEqual(layer.subsetString(), mapped)          # still on
        self.plugin.explorers[layer.id()].close()
        H.settle(0.2)
        self.assertEqual(layer.subsetString(), '"ZONE_CODE" > 1')
        self.assertIsNone(SUB.held(layer))

        # reopening starts with the button off and every record
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        btn, cells = panel.query_btn, panel.header.cells
        self.assertFalse(btn.isChecked())
        self.assertIsNone(cells[lu].current_filter())
        self.assertEqual(len(panel.model.fids), layer.featureCount())

        # switching the explorer to another layer gives the filter back too
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "INDUSTRIAL"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertIn("INDUSTRIAL", layer.subsetString())
        other = H.demo_layer(n=40, seed=8, name="Map filter other")
        panel.layerChangeRequested.emit(panel, other)
        self.assertTrue(H.wait_for(lambda: panel.layer is other and idle(panel)))
        self.assertEqual(layer.subsetString(), '"ZONE_CODE" > 1')
        self.assertFalse(btn.isChecked())
        self.assertEqual(other.subsetString(), "")
        panel.layerChangeRequested.emit(panel, layer)
        self.assertTrue(H.wait_for(lambda: panel.layer is layer and idle(panel)))
        QgsProject.instance().removeMapLayer(other.id())
        cells = panel.header.cells               # rebuilt for the layer

        # a Query Builder change made while it is on wins ...
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "INDUSTRIAL"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        layer.setSubsetString('"ZONE_CODE" = 2')
        self.assertTrue(H.wait_for(lambda: idle(panel) and not panel._data_timer.isActive()))
        self.assertFalse(btn.isChecked())
        self.assertIsNone(SUB.held(layer))
        self.assertEqual(layer.subsetString(), '"ZONE_CODE" = 2')
        self.assertIn("changed outside", panel.status.message.fullText())
        # ... but an old copy of the map filter (Layer Properties applying what
        # its Query Builder showed when it opened) is not a change: it is put back
        btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        old = layer.subsetString()
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "RESIDENTIAL"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        current = layer.subsetString()
        self.assertNotEqual(current, old)
        layer.setSubsetString(old)
        H.settle(0.2)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertTrue(btn.isChecked())
        self.assertEqual(layer.subsetString(), current)
        btn.setChecked(False)
        self.assertEqual(layer.subsetString(), '"ZONE_CODE" = 2')
        self.plugin.explorers[layer.id()].close()
        H.settle(0.2)

        # projects saved by earlier versions with the map filter on get the
        # layer filter back
        layer.setCustomProperty(SUB.PROPERTY, "")
        layer.setSubsetString('"ZONE_CODE" = 3')
        self.plugin._restore_forgotten_filters([layer])
        self.assertEqual(layer.subsetString(), "")
        self.assertIsNone(layer.customProperty(SUB.PROPERTY))
        layer.setSubsetString('"ZONE_CODE" = 7')
        layer.setCustomProperty(SUB.STATE, json.dumps(
            {"version": 1, "base": '"ZONE_CODE" > 1', "applied": '"ZONE_CODE" = 7'}))
        self.plugin._restore_forgotten_filters([layer])
        self.assertEqual(layer.subsetString(), '"ZONE_CODE" > 1')
        self.assertIsNone(layer.customProperty(SUB.STATE))
        layer.setSubsetString("")

        # memory layers take the table's own QGIS expression
        from qgis.core import QgsFeature, QgsGeometry, QgsPointXY
        mem = QgsVectorLayer("Point?crs=EPSG:3857&field=name:string&field=v:double",
                             "Map filter points", "memory")
        feats = []
        for i in range(40):
            f = QgsFeature(mem.fields())
            f.setAttributes([f"p{i}", float(i)])
            f.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(i, i)))
            feats.append(f)
        mem.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(mem)
        panel2 = self.plugin.open_explorer(mem)
        self.assertTrue(H.wait_for(lambda: idle(panel2)))
        panel2.header.cells[1].set_filter(FL.ColumnFilter("v", F.REAL, "gt", "29"))
        panel2.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel2)))
        panel2.query_btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel2)))
        self.assertEqual(mem.subsetString(), '"v" > 29')
        self.assertEqual(mem.featureCount(), 10)
        path = os.path.join(tempfile.mkdtemp(), "map_filter_memory.qgs")
        self.assertTrue(QgsProject.instance().write(path))
        QgsProject.instance().setFileName("")
        with open(path, encoding="utf-8") as fh:
            self.assertNotIn("&quot;v&quot; > 29", fh.read().replace("&gt;", ">"))
        self.plugin.explorers[mem.id()].close()
        H.settle(0.2)
        self.assertEqual(mem.subsetString(), "")                 # gone with the explorer

    def test_14a_attribute_only_table(self):
        table = QgsVectorLayer("None?field=name:string&field=v:double",
                               "Lookup table", "memory")
        from qgis.core import QgsFeature
        feats = []
        for i in range(25):
            f = QgsFeature(table.fields())
            f.setAttributes([f"n{i % 4}", None if i % 5 == 0 else float(i)])
            feats.append(f)
        table.dataProvider().addFeatures(feats)
        QgsProject.instance().addMapLayer(table)
        panel = self.plugin.open_explorer(table)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), 25)
        self.assertFalse(panel.zoom_btn.isEnabled())
        self.assertEqual(panel.profiles[1].missing, 5)
        self.assertTrue(panel.profiles[0].is_categorical())
        self.plugin.explorers[table.id()].close()
        H.settle(0.1)

    def test_14b_schema_change_keeps_filters(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        lu = self.layer.fields().indexOf("LAND_USE")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE", F.TEXT, "eq", "OTHER"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        n = len(panel.model.fids)
        from qgis.core import QgsField
        from qgis.PyQt.QtCore import QMetaType
        self.layer.startEditing()
        try:
            fld = QgsField("NOTE", QMetaType.Type.QString)
        except (AttributeError, TypeError):
            from qgis.PyQt.QtCore import QVariant
            fld = QgsField("NOTE", QVariant.String)
        self.layer.addAttribute(fld)
        self.assertTrue(H.wait_for(
            lambda: idle(panel) and len(panel.model.fields) == self.layer.fields().count(),
            10))
        self.assertEqual(len(panel.model.fids), n)
        idx = self.layer.fields().indexOf("LAND_USE")
        self.assertIsNotNone(panel.header.cells[idx].current_filter())
        self.layer.rollBack()
        self.assertTrue(H.wait_for(
            lambda: idle(panel) and len(panel.model.fields) == self.layer.fields().count(),
            10))
        panel.clear_filters()
        H.wait_for(lambda: idle(panel))

    def test_14d_removing_the_sorted_join(self):
        """Sorted by a joined field, then the join is removed (Layer
        Properties or the Join table window): the sort goes, the table
        reads the layer again and switching layer works (it raised
        IndexError)."""
        from qgis.core import QgsVectorLayerJoinInfo
        layer = H.demo_layer(n=200, name="Sorted join parcels")
        lookup = H.demo_layer(n=60, seed=4, name="lookup")
        other = H.demo_layer(n=30, seed=8, name="Next layer")
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        n = len(panel.model.fields)
        info = QgsVectorLayerJoinInfo()
        info.setJoinLayer(lookup)
        info.setJoinFieldName("PARCEL_ID")
        info.setTargetFieldName("PARCEL_ID")
        info.setUsingMemoryCache(True)
        info.setJoinFieldNamesSubset(["SCORE_MAX"])
        layer.addJoin(info)
        self.assertTrue(H.wait_for(lambda: idle(panel) and len(panel.model.fields) == n + 1))
        panel.sort_by(n, True)                               # lookup_SCORE_MAX
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        layer.removeJoin(lookup.id())                        # as Layer Properties does
        self.assertTrue(H.wait_for(lambda: idle(panel) and len(panel.model.fields) == n))
        self.assertIsNone(panel.sort)
        self.assertEqual(len(panel.model.fids), 200)
        self.assertIn("no longer sorted by lookup_SCORE_MAX", panel.status.message.fullText())
        panel._save_layout()                                 # raised IndexError
        panel.set_layer(other)                               # what switching layer does
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.plugin.explorers[layer.id()].close()            # still listed under the first
        for lyr in (layer, lookup, other):
            QgsProject.instance().removeMapLayer(lyr.id())

    def test_14c_close_while_scanning(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        panel.refresh()
        self.plugin.explorers[self.layer.id()].close()   # job still running
        H.settle(1.0)
        self.assertNotIn(self.layer.id(), self.plugin.explorers)

    def test_14_layer_removal_and_reload(self):
        panel = self.plugin.open_explorer(self.other)
        H.wait_for(lambda: idle(panel))
        other_id = self.other.id()
        self.assertIn(other_id, self.plugin.explorers)
        QgsProject.instance().removeMapLayer(other_id)
        H.settle(0.2)
        self.assertNotIn(other_id, self.plugin.explorers)
        # unload / reload leaves exactly one toolbar with three actions
        self.plugin.unload()
        H.settle(0.2)
        self.assertEqual(self.iface.registered, [])
        self.assertEqual(self.iface.layer_actions, [])
        self.plugin.initGui()
        bars = [tb for tb in self.iface.mainWindow().findChildren(
            type(self.plugin.toolbar)) if tb.objectName() == "AttributeTableStudioToolbar"]
        self.assertEqual(len(bars), 1)
        self.assertEqual(len(bars[0].actions()), 3)


if __name__ == "__main__":
    H.run_tests(APP)
