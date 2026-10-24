# -*- coding: utf-8 -*-
"""End-to-end tests of GeoTable Studio inside a headless QGIS.

Run with QGIS' Python (QGIS 3.40+ / 4.x):
    QT_QPA_PLATFORM=offscreen python tests/qgis/test_suite_qgis.py
"""

import csv
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

APP = H.start_qgis()
PKG = H.plugin_package()

from qgis.PyQt.QtCore import Qt  # noqa: E402
from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox  # noqa: E402
from qgis.core import (QgsFeatureRequest, QgsProject,  # noqa: E402
                       QgsVectorLayer)
from qgis.gui import QgsDockWidget  # noqa: E402

from geotable_studio import classFactory  # noqa: E402
from geotable_studio.core import filters as FL  # noqa: E402
from geotable_studio.core import fields as F  # noqa: E402
from geotable_studio.core import pivot as P  # noqa: E402

OUT = tempfile.mkdtemp(prefix="gts_out_")
_next_save = []


def _fake_save(*_args, **_kwargs):
    return (_next_save.pop(0) if _next_save else os.path.join(OUT, "x.csv"), "")


QFileDialog.getSaveFileName = staticmethod(_fake_save)
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: None))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)


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
        self.assertEqual(panel.filter_buttons["Added"].text(), "Added   12")
        path = os.path.join(OUT, "compare.csv")
        _next_save.append(path)
        panel.export_results()
        self.assertTrue(os.path.exists(path))
        host = self.plugin.compare_host
        self.assertEqual(host.window_title(), "Geometry and Table Compare")

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
            type(self.plugin.toolbar)) if tb.objectName() == "GeoTableStudioToolbar"]
        self.assertEqual(len(bars), 1)
        self.assertEqual(len(bars[0].actions()), 3)


if __name__ == "__main__":
    H.run_tests(APP)
