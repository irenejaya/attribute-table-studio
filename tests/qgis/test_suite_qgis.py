# -*- coding: utf-8 -*-
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
        idx = self.layer.fields().indexOf("PARCEL_MASTER_FID")
        self.assertEqual(panel.profiles[idx].missing, self.layer.featureCount())
        # table values come from the layer
        first = next(self.layer.getFeatures(QgsFeatureRequest().setFilterFid(
            panel.model.fids[0])))
        lu = self.layer.fields().indexOf("LAND_USE_TYPE")
        self.assertEqual(panel.model.raw_value(0, lu), first["LAND_USE_TYPE"])

    def test_03_explorer_filters(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        fields = self.layer.fields()
        lu = fields.indexOf("LAND_USE_TYPE")
        fl = fields.indexOf("FL_1_PCT_MAX")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "RESIDENTIAL"))
        panel.header.cells[fl].set_filter(
            FL.ColumnFilter("FL_1_PCT_MAX", F.REAL, "gt", "60"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        expr = "\"LAND_USE_TYPE\" = 'RESIDENTIAL' AND \"FL_1_PCT_MAX\" > 60"
        n = expected_count(self.layer, expr)
        self.assertGreater(n, 0)
        self.assertEqual(len(panel.model.fids), n)
        self.assertTrue(H.wait_for(lambda: fl in panel.profiles))
        self.assertEqual(panel.profiles[fl].count, n)
        self.assertGreater(panel.profiles[fl].minimum, 60)
        self.assertIn("2 active filters", panel.scope_label.fullText())
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
        fl = self.layer.fields().indexOf("FL_1_PCT_MAX")
        panel._on_sort_clicked(fl)            # ascending
        panel._on_sort_clicked(fl)            # descending
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        top = panel.model.raw_value(0, fl)
        self.assertEqual(top, max(f["FL_1_PCT_MAX"] for f in self.layer.getFeatures()))
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
        lu = self.layer.fields().indexOf("LAND_USE_TYPE")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "INDUSTRIAL"))
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
        lu = self.layer.fields().indexOf("LAND_USE_TYPE")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "RURAL"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        before = len(panel.model.fids)
        target = next(f for f in self.layer.getFeatures()
                      if f["LAND_USE_TYPE"] == "RESIDENTIAL")
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
        fl = self.layer.fields().indexOf("FL_1_PCT_MAX")
        panel.header.cells[fl].set_filter(
            FL.ColumnFilter("FL_1_PCT_MAX", F.REAL, "ge", "50"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        panel._open_pivot()
        pivot = self.plugin.pivot_host.panel
        self.assertEqual(pivot.input["kind"], "explorer")
        self.assertEqual(len(pivot.input["fids"]), len(panel.model.fids))
        pivot.set_spec(P.PivotSpec(["LAND_USE_TYPE"], [], [
            P.Measure("FL_1_PCT_MAX", "mean"), P.Measure(None, "count")]))
        pivot.build()
        self.assertTrue(H.wait_for(lambda: pivot.model.result is not None
                                   and not pivot.runner.busy, 30))
        result = pivot.model.result
        vals = [f["FL_1_PCT_MAX"] for f in self.layer.getFeatures(
            QgsFeatureRequest().setFilterExpression(
                "\"LAND_USE_TYPE\" = 'RESIDENTIAL' AND \"FL_1_PCT_MAX\" >= 50"))]
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
        pivot.set_spec(P.PivotSpec(["LAND_USE_TYPE"], ["ZONE_CODE"], [
            P.Measure("FL_1_PCT_MAX", "mean")]),
            [FL.ColumnFilter("IN_FLOOD_ZONE", F.BOOL, "true")])
        pivot.build()
        self.assertTrue(H.wait_for(lambda: not pivot.runner.busy, 30))
        r = pivot.model.result
        self.assertTrue(r.has_cols)
        n_true = expected_count(self.layer, '"IN_FLOOD_ZONE" = TRUE')
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

    def test_13_compare(self):
        base = H.demo_layer(n=800, name="Parcels baseline")
        new = H.demo_updated_layer(base)
        panel = self.plugin.open_compare()
        self.assertIs(self.plugin.open_compare(), panel)
        panel.old_table_combo.setLayer(base)
        panel.new_table_combo.setLayer(new)
        panel.join_field_combo.setCurrentIndex(
            panel.join_field_combo.findText("PARCEL_VIEW_PFI"))
        panel.compare_tables()
        counts = panel._compare_info["counts"]
        self.assertEqual(counts["Added"], 12)
        self.assertGreater(counts["Modified"], 0)
        self.assertEqual(panel.filter_counts["Added"].text(), "· 12")
        self.assertEqual(panel.filter_boxes["Added"].text(), "Added (In NEW Only)")
        path = os.path.join(OUT, "compare.csv")
        _next_save.append(path)
        panel.export_results()
        self.assertTrue(os.path.exists(path))
        host = self.plugin.compare_host
        self.assertEqual(host.window_title(), "Geometry and Table Compare")

    def test_13b_round2_ui_rules(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        fl = self.layer.fields().indexOf("FL_1_PCT_MAX")
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
        # every panel keeps the status bar inside the window
        for p in (panel, compare):
            self.assertEqual(p.layout().contentsMargins().bottom(), p.metrics.margin)
        # pivot filters show as chips
        pivot = self.plugin.open_pivot(self.layer)
        pivot.set_spec(P.PivotSpec(["LAND_USE_TYPE"], [], [P.Measure(None, "count")]),
                       [FL.ColumnFilter("ZONE_CODE", F.INTEGER, "gt", "2"),
                        FL.ColumnFilter("IN_FLOOD_ZONE", F.BOOL, "true")])
        self.assertEqual(pivot.chips_flow.count(), 2)
        self.assertFalse(pivot.filters_empty.isVisibleTo(pivot))
        pivot._remove_filter(pivot.filters[0])
        self.assertEqual(pivot.chips_flow.count(), 1)

    def test_13c_round3_ui_rules(self):
        panel = self.plugin.open_explorer(self.layer)
        H.wait_for(lambda: idle(panel))
        t = panel.theme
        date = self.layer.fields().indexOf("SURVEY_DATE")
        flag = self.layer.fields().indexOf("IN_FLOOD_ZONE")
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
        num = panel.header.cells[self.layer.fields().indexOf("FL_1_PCT_MAX")]
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
        self.assertIn("[EPSG:28355]", combo.currentText())
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
        pfi = self.layer.fields().indexOf("PARCEL_VIEW_PFI")
        value = next(self.layer.getFeatures())["PARCEL_VIEW_PFI"]
        cell = panel.header.cells[pfi]
        cell.set_filter(FL.ColumnFilter("PARCEL_VIEW_PFI", F.TEXT, "in",
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
        lu = self.layer.fields().indexOf("LAND_USE_TYPE")
        picker = ValuePicker("LAND_USE_TYPE", panel._values_for(lu), [],
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
        i1, i2 = flds.indexOf("FL_1_PCT_MIN"), flds.indexOf("LAND_USE_TYPE")
        new.dataProvider().changeAttributeValues({
            f.id(): {i1: (f[i1] or 0) + 1.5, i2: "COMMERCIAL"}
            for k, f in enumerate(new.getFeatures()) if k % 50 == 7})
        panel = self.plugin.open_compare()
        panel.old_table_combo.setLayer(base)
        panel.new_table_combo.setLayer(new)
        panel.join_field_combo.setCurrentIndex(
            panel.join_field_combo.findText("PARCEL_VIEW_PFI"))

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
        self.assertTrue(panel.accepted_legend.text().startswith("Accepted value · "))
        self.assertIn("Mixed 1", panel.review_tally.text())

        # export: a rejected value gives OLD, an accepted one NEW
        path = os.path.join(OUT, "compare_review.csv")
        _next_save.append(path)
        panel.export_results()
        with open(path, newline="", encoding="utf-8") as fh:
            records = list(csv.DictReader(fh))
        key = tbl.item(r, 0).data(user)
        rec = next(d for d in records if d["PARCEL_VIEW_PFI"] == str(key))
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
        picker = ValuePicker("LAND_USE_TYPE", [("RESIDENTIAL", "RESIDENTIAL", 5)], [],
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
                           ("Advanced Filter (Expression)…", icons.ADVANCED_FILTER),
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

    def test_13f_map_filter(self):
        layer = H.demo_layer(n=1500, seed=5, name="Map filter parcels")
        panel = self.plugin.open_explorer(layer)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        btn = panel.query_btn

        def x(widget):
            return widget.mapTo(panel, QPoint(0, 0)).x()
        # top row, right aligned: View | Zoom | Selected only | Deselect |
        # Select | Columns | Summary | Pivot Table | Clear filters | Export |
        # query | dock
        order = [panel.view_btn, panel.zoom_btn, panel.selected_btn, panel.deselect_btn,
                 panel.select_btn, panel.columns_btn, panel.summary_btn, panel.pivot_btn,
                 panel.clear_btn, panel.export_btn, btn, panel.dock_btn]
        xs = [x(w) for w in order]
        self.assertEqual(xs, sorted(xs))
        for icon_only in (panel.zoom_btn, panel.selected_btn, panel.deselect_btn,
                          panel.export_btn):
            self.assertEqual(icon_only.text().strip(), "")
        self.assertEqual(panel.pivot_btn.text().strip(), "Pivot Table")
        self.assertEqual([a.text() for a in panel.export_btn.menu().actions()
                          if not a.isSeparator()],
                         ["Excel (.xlsx)…", "CSV (.csv)…", "Copy Selected Rows"])
        self.assertFalse(btn.isEnabled())            # no filter yet
        total = layer.featureCount()
        lu = layer.fields().indexOf("LAND_USE_TYPE")
        fl = layer.fields().indexOf("FL_1_PCT_MAX")
        cells = panel.header.cells
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "RESIDENTIAL"))
        cells[fl].set_filter(FL.ColumnFilter("FL_1_PCT_MAX", F.REAL, "gt", "60"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        shown = len(panel.model.fids)
        self.assertTrue(btn.isEnabled())

        # on: the layer's Query Builder filter is the table filter, readable
        btn.setChecked(True)
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(layer.subsetString(),
                         '("LAND_USE_TYPE" = \'RESIDENTIAL\') AND ("FL_1_PCT_MAX" > 60)')
        self.assertEqual(layer.featureCount(), shown)
        self.assertEqual(len(panel.model.fids), shown)
        self.assertIn(f"{shown:,} shown / {total:,} available",
                      panel.status.message.fullText())
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
        self.assertEqual(layer.subsetString(), '"LAND_USE_TYPE" = \'RESIDENTIAL\'')
        self.assertEqual(layer.featureCount(), len(panel.model.fids))
        self.assertEqual(panel.model.raw_value(0, lu), "RESIDENTIAL")
        self.assertIn("INDUSTRIAL", [e[1] for e in panel._values_for(lu)])

        # case-insensitive contains: SQL when exact, the ids otherwise
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "contains", "dent"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(layer.subsetString(), '"LAND_USE_TYPE" LIKE \'%dent%\'')
        self.assertEqual(layer.featureCount(), len(panel.model.fids))
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "contains", "dÉnt"))
        panel.refresh()
        self.assertTrue(H.wait_for(lambda: idle(panel)))
        self.assertEqual(len(panel.model.fids), 0)
        self.assertEqual(layer.subsetString(), "1 = 0")
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "contains", "dent"))
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
        mapped = '("ZONE_CODE" > 1) AND ("LAND_USE_TYPE" LIKE \'%dent%\')'
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
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "INDUSTRIAL"))
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
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "INDUSTRIAL"))
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
        cells[lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "RESIDENTIAL"))
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
        mem = QgsVectorLayer("Point?crs=EPSG:28355&field=name:string&field=v:double",
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
        lu = self.layer.fields().indexOf("LAND_USE_TYPE")
        panel.header.cells[lu].set_filter(
            FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "OTHER"))
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
        idx = self.layer.fields().indexOf("LAND_USE_TYPE")
        self.assertIsNotNone(panel.header.cells[idx].current_filter())
        self.layer.rollBack()
        self.assertTrue(H.wait_for(
            lambda: idle(panel) and len(panel.model.fields) == self.layer.fields().count(),
            10))
        panel.clear_filters()
        H.wait_for(lambda: idle(panel))

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
