# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Data Plot in a headless QGIS, on made-up parcels.

Every plot's numbers are checked against values read straight from the
layer; then the records choices, live updates, two-way selection, saving in
the project and to files, export, the plot list, a second window, Help and
docking.

    python tests/qgis/test_data_plot_qgis.py
"""

import csv
import json
import math
import os
import statistics
import sys
import tempfile
import unittest
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

APP = H.start_qgis()
PKG = H.plugin_package()

from qgis.PyQt.QtCore import QPoint, Qt  # noqa: E402
from qgis.PyQt.QtWidgets import QMessageBox  # noqa: E402
from qgis.core import (  # noqa: E402
    QgsCategorizedSymbolRenderer, QgsProject, QgsRectangle, QgsRendererCategory,
    QgsSymbol,
)
from qgis.PyQt.QtGui import QColor  # noqa: E402

from attribute_table_studio import classFactory  # noqa: E402
from attribute_table_studio.core import fields as F  # noqa: E402
from attribute_table_studio.core import plots as P  # noqa: E402
from attribute_table_studio.ui import data_plot as DP  # noqa: E402

COLOURS = {"RESIDENTIAL": "#4c9be8", "COMMERCIAL": "#e8833a", "INDUSTRIAL": "#7b5ea7",
           "RURAL": "#3aa76d"}


def ready(panel):
    return (panel.data is not None and not panel.reader.busy and not panel.builder.busy
            and not panel.selector.busy)


class DataPlotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layer = H.demo_layer(n=2000, seed=21, name="Plot parcels")
        cats = []
        for value, colour in COLOURS.items():
            sym = QgsSymbol.defaultSymbol(cls.layer.geometryType())
            sym.setColor(QColor(colour))
            cats.append(QgsRendererCategory(value, sym, value.title()))
        cls.layer.setRenderer(QgsCategorizedSymbolRenderer("LAND_USE", cats))
        cls.iface = H.FakeIface()
        cls.iface.setActiveLayer(cls.layer)
        cls.plugin = classFactory(cls.iface)
        cls.plugin.initGui()
        cls.feats = list(cls.layer.getFeatures())

    @classmethod
    def tearDownClass(cls):
        cls.plugin.unload()

    def panel(self):
        panel = self.plugin.open_data_plot(self.layer)
        panel.window().resize(1300, 760)
        H.settle(0.2)
        return panel

    def make(self, panel, kind, **data):
        panel.new_plot(kind)
        plot = panel.current_plot()
        plot["data"].update(data)
        panel._write(plot)
        panel._show_plot(plot["id"])
        self.assertTrue(H.wait_for(lambda: ready(panel), 60))
        H.settle(0.2)
        return panel.current_plot()

    def values(self, name, rows=None):
        out = []
        for ft in self.feats if rows is None else rows:
            v = ft[name]
            out.append(None if v is None or F.is_null(v) else v)
        return out

    # ------------------------------------------------------------------
    def test_01_toolbar_and_window(self):
        texts = [a.text() for a in self.plugin.toolbar.actions()]
        self.assertEqual(texts[2], "Data Plot")
        panel = self.panel()
        self.assertIs(panel.layer, self.layer)
        self.assertEqual(panel.window_title(), "Data Plot — Plot parcels")
        self.assertIs(self.plugin.open_data_plot(), panel)          # one main window
        # a new plot asks for its fields first, as ArcGIS Pro does
        panel.new_plot("scatter")
        H.settle(0.2)
        self.assertIn("Choose", panel.status.message.fullText())
        self.assertEqual(panel.plot_list.count(), 1)
        self.assertEqual([t for t, _l in P.TYPES if t in P.BUILT],
                         ["bar", "line", "scatter"])
        self.assertEqual(len(panel.new_menu.actions()), len(P.BUILT))

    def test_02_scatter_numbers_trend_and_colours(self):
        panel = self.panel()
        self.make(panel, "scatter", x="SCORE_MAX", y="SCORE_5Y", trend=True)
        data = panel.data
        pairs = [(a, b) for a, b in zip(self.values("SCORE_MAX"), self.values("SCORE_5Y"))
                 if a is not None and b is not None]
        self.assertEqual(len(data), len(pairs))
        self.assertEqual(data.missing, len(self.feats) - len(pairs))
        xs = [a for a, _b in pairs]
        ys = [b for _a, b in pairs]
        r = statistics.correlation(xs, ys)
        self.assertAlmostEqual(data.trend.r2, r * r, places=9)
        # the layer's categorised colours, one legend class per category
        table = panel.table
        self.assertEqual(table.class_labels, [v.title() for v in COLOURS])
        self.assertEqual(table.class_colours, list(COLOURS.values()))
        fids = table.np(None)
        by_fid = {ft.id(): ft["LAND_USE"] for ft in self.feats}
        for k in range(0, len(fids), 97):
            cls_ = table.classes[k]
            expected = list(COLOURS).index(by_fid[int(fids[k])]) \
                if by_fid[int(fids[k])] in COLOURS else -1
            self.assertEqual(cls_, expected)
        legend = panel.view._legend_entries()
        self.assertIn("Residential", [e[2] for e in legend])
        self.assertIn("Linear trend", [e[2] for e in legend])
        # one colour instead
        plot = panel.current_plot()
        plot["data"]["colour"] = "single"
        plot["format"]["colour"] = "#ff0000"
        panel._write(plot)
        panel.load()
        self.assertTrue(H.wait_for(lambda: ready(panel)))
        self.assertEqual(panel.model.single.name(), "#ff0000")
        # a log axis leaves out the values of 0 or less (SCORE_5Y has zeros)
        plot = panel.current_plot()
        plot["axes"]["y_log"] = True
        panel._write(plot)
        panel.props.set_plot(dict(plot), self.layer.fields())
        panel._on_edited(False)
        self.assertTrue(H.wait_for(lambda: ready(panel)))
        zeros = sum(1 for _a, b in pairs if b <= 0)
        self.assertGreater(zeros, 0)
        self.assertEqual(panel.data.not_positive, zeros)

    def test_03_line_by_month(self):
        panel = self.panel()
        self.make(panel, "line", x="SURVEY_DATE", agg="mean", fields=["SCORE_MAX"],
                  unit="months", align="reference", reference="2023-01-01")
        data = panel.data
        groups = defaultdict(list)
        for ft in self.feats:
            d, v = ft["SURVEY_DATE"], ft["SCORE_MAX"]
            if d is None or F.is_null(d):
                continue
            groups[(d.year(), d.month())].append(v)
        self.assertEqual(len(data), len(groups))
        for i, label in enumerate(data.labels):
            y, m = int(label[:4]), int(label[5:7])
            self.assertAlmostEqual(data.values[0][i], statistics.fmean(groups[(y, m)]),
                                   places=9)
        self.assertEqual(data.labels[0], "2023-01-01")
        self.assertEqual(data.excluded, sum(1 for ft in self.feats
                                            if F.is_null(ft["SURVEY_DATE"])))

    def test_04_bar_split_and_sort(self):
        panel = self.panel()
        self.make(panel, "bar", x="LAND_USE", agg="sum", fields=["SCORE_MIN"],
                  split="IS_VACANT", sort="value_desc")
        data = panel.data
        sums = defaultdict(float)
        totals = defaultdict(float)
        for ft in self.feats:
            v = ft["IS_VACANT"]
            key = F.NULL_TEXT if v is None or F.is_null(v) else ("true" if v else "false")
            sums[(ft["LAND_USE"], key)] += ft["SCORE_MIN"]
            totals[ft["LAND_USE"]] += ft["SCORE_MIN"]
        self.assertEqual(sorted(data.series), sorted({k for _l, k in sums}))
        self.assertEqual(data.labels, sorted(totals, key=lambda k: -totals[k]))
        for i, lu in enumerate(data.labels):
            for s, key in enumerate(data.series):
                got = data.values[s][i]
                want = sums.get((lu, key))
                if want is None:
                    self.assertTrue(math.isnan(got))
                else:
                    self.assertAlmostEqual(got, want, places=6)
        # dates as categories, by year
        self.make(panel, "bar", x="SURVEY_DATE", agg="count", unit="years",
                  align="reference", reference="2023-01-01")
        years = Counter(ft["SURVEY_DATE"].year() for ft in self.feats
                        if not F.is_null(ft["SURVEY_DATE"]))
        self.assertEqual(panel.data.values[0], [years[y] for y in sorted(years)])

    def test_05_records_choices(self):
        panel = self.panel()
        self.make(panel, "bar", x="LAND_USE", agg="count")
        self.assertEqual(sum(panel.data.records), len(self.feats))
        # selected records
        ids = [ft.id() for ft in self.feats[:300]]
        self.layer.selectByIds(ids)
        panel.set_scope("selected")
        self.assertTrue(H.wait_for(lambda: ready(panel) and len(panel.table) == 300))
        self.assertEqual(sum(panel.data.records), 300)
        self.layer.selectByIds(ids[:50])
        self.assertTrue(H.wait_for(lambda: ready(panel) and len(panel.table) == 50, 10))
        self.layer.removeSelection()
        # visible map extent: the parcels of the first rows of the grid
        canvas = self.iface.mapCanvas()
        canvas.setDestinationCrs(self.layer.crs())
        canvas.setExtent(QgsRectangle(10000, 10000, 13000, 10200))
        panel.set_scope("extent")
        inside = sum(1 for ft in self.feats
                     if ft.geometry().boundingBox().intersects(canvas.extent()))
        self.assertTrue(H.wait_for(lambda: ready(panel) and len(panel.table) == inside, 20))
        self.assertLess(inside, len(self.feats))
        # Attribute Explorer filtered records
        from attribute_table_studio.core import filters as FL
        exp = self.plugin.open_explorer(self.layer)
        self.assertTrue(H.wait_for(lambda: exp._has_result and not exp.runner.busy))
        lu = self.layer.fields().indexOf("LAND_USE")
        exp.header.cells[lu].set_filter(FL.ColumnFilter("LAND_USE", F.TEXT, "eq",
                                                        "RESIDENTIAL"))
        exp.refresh()
        self.assertTrue(H.wait_for(lambda: exp._has_result and not exp.runner.busy))
        panel.set_scope("explorer")
        n = sum(1 for ft in self.feats if ft["LAND_USE"] == "RESIDENTIAL")
        self.assertTrue(H.wait_for(lambda: ready(panel) and len(panel.table) == n, 20))
        self.assertEqual(panel.data.labels, ["RESIDENTIAL"])
        # the explorer closes: back to all records
        self.plugin.explorers[self.layer.id()].close()
        self.assertTrue(H.wait_for(lambda: panel.scope == "all" and ready(panel)
                                   and len(panel.table) == len(self.feats), 20))

    def test_06_live_edits(self):
        panel = self.panel()
        self.make(panel, "bar", x="LAND_USE", agg="max", fields=["SCORE_MAX"])
        before = dict(zip(panel.data.labels, panel.data.values[0]))
        target = self.feats[5]
        self.layer.startEditing()
        try:
            idx = self.layer.fields().indexOf("SCORE_MAX")
            self.layer.changeAttributeValue(target.id(), idx, 1234.5)
            self.assertTrue(H.wait_for(
                lambda: ready(panel) and max(panel.data.values[0]) == 1234.5, 20))
            after = dict(zip(panel.data.labels, panel.data.values[0]))
            self.assertEqual(after[target["LAND_USE"]], 1234.5)
        finally:
            self.layer.rollBack()
        self.assertTrue(H.wait_for(
            lambda: ready(panel) and dict(zip(panel.data.labels, panel.data.values[0]))
            == before, 20))

    def test_07_two_way_selection(self):
        from qgis.PyQt.QtTest import QTest
        panel = self.panel()
        self.make(panel, "bar", x="LAND_USE", agg="count")
        view = panel.view
        view.resize(900, 600)
        view.repaint()
        H.settle(0.2)
        no, ctrl = Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ControlModifier

        def centre(i):
            rect = [r for r, j, _s in view._geo["items"] if j == i][0]
            return rect.center().toPoint()

        labels = panel.data.labels
        of = lambda lu: {ft.id() for ft in self.feats if ft["LAND_USE"] == lu}  # noqa: E731
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, no, centre(0))
        H.settle(0.3)
        self.assertEqual(set(self.layer.selectedFeatureIds()), of(labels[0]))
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, ctrl, centre(2))
        H.settle(0.3)
        self.assertEqual(set(self.layer.selectedFeatureIds()), of(labels[0]) | of(labels[2]))
        # the selection shows in the plot: Count bars show their selected part
        self.assertTrue(H.wait_for(lambda: ready(panel) and panel.model.sel_count ==
                                   len(of(labels[0]) | of(labels[2]))))
        self.assertEqual(panel.model.sel_per[0], len(of(labels[0])))
        # the legend's Selected swatch is filled with the selection colour
        img = view.render_image(1.0)
        centre_pt = view._geo["sel_swatch"].center().toPoint()
        want = QgsProject.instance().selectionColor()
        got = QColor(img.pixel(centre_pt))
        self.assertEqual((got.red(), got.green(), got.blue()),
                         (want.red(), want.green(), want.blue()))
        self.assertEqual(panel.model.sel_part[0][2], len(of(labels[2])))
        # a drag across bars selects the range
        QTest.mousePress(view, Qt.MouseButton.LeftButton, no, centre(1))
        QTest.mouseMove(view, centre(2))
        QTest.mouseMove(view, centre(3))
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, no, centre(3))
        H.settle(0.3)
        self.assertEqual(set(self.layer.selectedFeatureIds()),
                         of(labels[1]) | of(labels[2]) | of(labels[3]))
        # scatter: a box selects the points inside it
        self.make(panel, "scatter", x="SCORE_MAX", y="SCORE_MIN", colour="single")
        view.repaint()
        H.settle(0.2)
        xa, ya = view._geo["x"], view._geo["y"]
        lo, hi = 50.0, 70.0
        a = QPoint(int(xa.map(lo)), int(ya.map(hi)))
        b = QPoint(int(xa.map(hi)), int(ya.map(lo)))
        QTest.mousePress(view, Qt.MouseButton.LeftButton, no, a)
        QTest.mouseMove(view, QPoint((a.x() + b.x()) // 2, (a.y() + b.y()) // 2))
        QTest.mouseMove(view, b)
        QTest.mouseRelease(view, Qt.MouseButton.LeftButton, no, b)
        H.settle(0.3)
        x0, x1 = sorted((xa.value_at(a.x()), xa.value_at(b.x())))
        y0, y1 = sorted((ya.value_at(a.y()), ya.value_at(b.y())))
        want = {ft.id() for ft in self.feats
                if x0 <= ft["SCORE_MAX"] <= x1 and y0 <= ft["SCORE_MIN"] <= y1}
        self.assertGreater(len(want), 10)
        self.assertEqual(set(self.layer.selectedFeatureIds()), want)
        # a click on one point selects its record
        k = int(panel.data.rows[0])
        fid = int(panel.table.fids[k])
        pt = QPoint(int(round(xa.map(float(panel.data.x[0])))),
                    int(round(ya.map(float(panel.data.y[0])))))
        QTest.mouseClick(view, Qt.MouseButton.LeftButton, no, pt)
        H.settle(0.3)
        self.assertIn(fid, self.layer.selectedFeatureIds())
        self.layer.removeSelection()

    def test_08_project_and_files(self):
        panel = self.panel()
        self.make(panel, "line", x="SURVEY_DATE", agg="count", unit="weeks")
        plot = panel.current_plot()
        plot["general"]["title"] = "Surveys per week"
        plot["guides"].append({"axis": "horizontal", "value": "5", "to": None,
                               "label": "Five"})
        panel._write(plot)
        names = [p["name"] for p in panel.store.plots(self.layer.id())]
        text, _ok = QgsProject.instance().readEntry(DP.PROJECT_SCOPE, DP.PROJECT_PLOTS, "")
        self.assertEqual([p["name"] for p in json.loads(text)[self.layer.id()]], names)
        folder = tempfile.mkdtemp(prefix="gts_plots_")
        path = os.path.join(folder, "plots.qgz")
        self.assertTrue(QgsProject.instance().write(path))
        layer_id = self.layer.id()
        # the stored plots come back when the project is read again
        panel.store._data = None
        again = panel.store.plots(layer_id)
        self.assertEqual([p["name"] for p in again], names)
        self.assertEqual(panel.store.get(layer_id, plot["id"])["general"]["title"],
                         "Surveys per week")
        # save to and load from a file
        file_path = os.path.join(folder, "plot.json")
        from qgis.PyQt.QtWidgets import QFileDialog
        orig = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (file_path, ""))
        try:
            panel.save_to_file()
        finally:
            QFileDialog.getSaveFileName = orig
        with open(file_path, encoding="utf-8") as fh:
            saved = json.load(fh)
        self.assertIn(DP.FILE_TAG, saved)
        self.assertNotIn("id", saved["plot"])
        count = len(panel.store.plots(layer_id))
        panel.load_from_file(file_path)
        self.assertEqual(len(panel.store.plots(layer_id)), count + 1)
        self.assertEqual(panel.current_plot()["general"]["title"], "Surveys per week")
        # exports
        self.assertTrue(H.wait_for(lambda: ready(panel)))
        png, svg, csv_path = (os.path.join(folder, n) for n in ("p.png", "p.svg", "p.csv"))
        panel.export_image("png", png)
        panel.export_image("svg", svg)
        panel.export_csv(csv_path)
        self.assertGreater(os.path.getsize(png), 5000)
        self.assertGreater(os.path.getsize(svg), 2000)
        with open(csv_path, encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        header = next(r for r in rows if r and r[0] == "SURVEY_DATE")
        self.assertEqual(header[:4], ["SURVEY_DATE", "From", "To", "Count"])
        body = rows[rows.index(header) + 1:]
        self.assertEqual(sum(int(r[4]) for r in body if r and r[4]),
                         sum(1 for ft in self.feats if not F.is_null(ft["SURVEY_DATE"])))

    def test_09_plot_list(self):
        panel = self.panel()
        start = len(panel.store.plots(self.layer.id()))
        self.make(panel, "bar", x="LAND_USE", agg="count")
        pid = panel.plot_id
        panel.duplicate_plot()
        self.assertEqual(len(panel.store.plots(self.layer.id())), start + 2)
        self.assertTrue(panel.current_plot()["name"].endswith("(copy)"))
        copy_id = panel.plot_id
        panel._move(-1)
        ids = [p["id"] for p in panel.store.plots(self.layer.id())]
        self.assertLess(ids.index(copy_id), ids.index(pid))
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
        try:
            panel.delete_plot()
        finally:
            QMessageBox.question = orig
        self.assertNotIn(copy_id, [p["id"] for p in panel.store.plots(self.layer.id())])
        # another window shows a plot beside this one, and sees list changes
        panel._fill_list(pid)
        panel.open_in_new_window()
        H.settle(0.3)
        self.assertEqual(len(self.plugin.plot_hosts), 2)
        second = self.plugin.plot_hosts[1].panel
        self.assertEqual(second.plot_id, pid)
        self.assertTrue(H.wait_for(lambda: ready(second)))
        panel.new_plot("line")
        H.settle(0.2)
        self.assertEqual(second.plot_list.count(), panel.plot_list.count())
        self.plugin.plot_hosts[1].close()
        H.settle(0.3)
        self.assertEqual(len(self.plugin.plot_hosts), 1)

    def test_10_help_dock_and_layer_removal(self):
        panel = self.panel()
        panel.help_btn.click()
        dlg = panel._help_dialog
        self.assertTrue(dlg.isVisible())
        self.assertEqual(dlg.windowTitle(), "Data Plot — Help")
        self.assertIn("Scatter Plot", dlg.details.toPlainText())
        dlg.close()
        host = self.plugin.plot_hosts[0]
        panel.dock_btn.click()
        H.settle(0.3)
        self.assertTrue(host.is_docked)
        panel.dock_btn.click()
        H.settle(0.3)
        self.assertFalse(host.is_docked)
        other = H.demo_layer(n=50, seed=4, name="Short-lived parcels")
        panel.set_layer(other)
        H.settle(0.2)
        self.assertIs(panel.layer, other)
        QgsProject.instance().removeMapLayer(other.id())
        H.settle(0.3)
        self.assertIsNone(panel.layer)


if __name__ == "__main__":
    H.run_tests(APP)
