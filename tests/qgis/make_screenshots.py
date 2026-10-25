# -*- coding: utf-8 -*-
"""Regenerate the screenshots in docs/screenshots from a made-up dataset.

Run with QGIS' Python, once per theme (QT_QPA_PLATFORM=offscreen works):

    python tests/qgis/make_screenshots.py docs/screenshots dark
    python tests/qgis/make_screenshots.py docs/screenshots light

Every value is invented: random parcel IDs, land use, owner type, area,
land value, storeys, year built, survey dates and two yes / no flags, on a
grid of squares in a neutral spot of EPSG:3857.  dark_map_filter.png is
made separately and is not touched.
"""

import os
import random
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
THEME = (sys.argv[2] if len(sys.argv) > 2 else "dark").lower()
APP = H.start_qgis("Night Mapping" if THEME == "dark" else None)
H.plugin_package()

from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsProject,  # noqa: E402
                       QgsVectorFileWriter, QgsVectorLayer)
from qgis.PyQt.QtCore import QDate, QPoint, QSize, Qt  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from attribute_table_studio import classFactory  # noqa: E402
from attribute_table_studio.core import fields as F  # noqa: E402
from attribute_table_studio.core import filters as FL  # noqa: E402
from attribute_table_studio.core import pivot as P  # noqa: E402
from attribute_table_studio.ui import explorer_edit as E  # noqa: E402
from attribute_table_studio.ui.attribute_explorer import ExplorerPanel  # noqa: E402

E.ASK = lambda parent, title, text, choices, default=None, icon="question": \
    default or choices[0][0]

# ----------------------------------------------------------------------------
# made-up data
# ----------------------------------------------------------------------------
LAND_USES = (("Residential", 62), ("Commercial", 12), ("Industrial", 8),
             ("Rural", 7), ("Open space", 6), ("Community", 5))
OWNERS = (("Private", 80), ("Council", 8), ("State", 5), ("Community trust", 4))
AREA = {"Residential": 620, "Commercial": 1400, "Industrial": 3200, "Rural": 18000,
        "Open space": 5200, "Community": 2400}
RATE = {"Residential": 950, "Commercial": 1400, "Industrial": 520, "Rural": 35,
        "Open space": 60, "Community": 300}
STOREYS = {"Residential": [1, 1, 1, 2, 2, 3], "Commercial": [1, 2, 2, 3, 4, 6],
           "Industrial": [1, 1, 2], "Rural": [1], "Community": [1, 2]}
FIELDS = ("&field=PARCEL_ID:string(10)&field=LAND_USE:string(20)"
          "&field=OWNER_TYPE:string(20)&field=AREA_M2:double(12,1)"
          "&field=LAND_VALUE:integer&field=SURVEY_DATE:date"
          "&field=HAS_SOLAR:boolean&field=HERITAGE:boolean"
          "&field=STOREYS:integer&field=YEAR_BUILT:integer")
NAMES = [part.split(":")[0] for part in FIELDS.split("&field=")[1:]]


def _pick(rnd, weighted):
    total = sum(w for _v, w in weighted)
    x = rnd.uniform(0, total)
    for value, weight in weighted:
        x -= weight
        if x <= 0:
            return value
    return weighted[-1][0]


def _square(x, y, size=25):
    return QgsGeometry.fromPolygonXY([[
        QgsPointXY(x, y), QgsPointXY(x + size, y), QgsPointXY(x + size, y + size),
        QgsPointXY(x, y + size), QgsPointXY(x, y)]])


def _record(rnd, fields, pid):
    f = QgsFeature(fields)
    use = _pick(rnd, LAND_USES)
    owner = _pick(rnd, OWNERS) if rnd.random() > .03 else None
    area = round(rnd.lognormvariate(0, .35) * AREA[use], 1)
    value = int(round(area * RATE[use] * rnd.gauss(1, .12), -3)) if rnd.random() > .02 else None
    built = None
    if use != "Open space" and rnd.random() > .03:
        built = rnd.randint(1925, 2024)
    survey = QDate(2021, 1, 4).addDays(rnd.randint(0, 1640)) if rnd.random() > .04 else None
    solar_share = {"Residential": .38, "Commercial": .25, "Industrial": .3}.get(use, .05)
    solar = rnd.random() < solar_share if rnd.random() > .02 else None
    heritage = rnd.random() < (.35 if built and built < 1950 else .03) \
        if rnd.random() > .01 else None
    storeys = rnd.choice(STOREYS[use]) if use in STOREYS else None
    for name, val in zip(NAMES, (pid, use, owner, area, value, survey, solar, heritage,
                                 storeys, built)):
        f.setAttribute(name, val)           # by name: a GeoPackage adds an fid field
    return f


def sample_parcels(n=3000, seed=42, name="Sample parcels"):
    """GeoPackage of n made-up parcels (unique random IDs)."""
    rnd = random.Random(seed)
    mem = QgsVectorLayer("Polygon?crs=EPSG:3857" + FIELDS, name, "memory")
    ids = rnd.sample(range(100000, 900000), n)
    feats = []
    for i in range(n):
        f = _record(rnd, mem.fields(), f"P-{ids[i]}")
        col, row = i % 80, i // 80
        f.setGeometry(_square(20000 + col * 30 + (col // 8) * 12,
                              20000 + row * 30 + (row // 4) * 12))
        feats.append(f)
    mem.dataProvider().addFeatures(feats)
    path = os.path.join(tempfile.mkdtemp(prefix="ats_shots_"), "sample.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "parcels"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        mem, path, QgsProject.instance().transformContext(), opts)
    layer = QgsVectorLayer(path + "|layername=parcels", name, "ogr")
    QgsProject.instance().addMapLayer(layer)
    return layer


def sample_update(base, seed=5, name="Sample parcels (updated)"):
    """NEW version of *base*: revalued, re-zoned, moved, deleted and added parcels."""
    rnd = random.Random(seed)
    path = os.path.join(tempfile.mkdtemp(prefix="ats_shots_"), "sample_new.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "parcels"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        base, path, QgsProject.instance().transformContext(), opts)
    layer = QgsVectorLayer(path + "|layername=parcels", name, "ogr")
    flds = layer.fields()
    i_value, i_use, i_storeys, i_solar = (flds.indexOf(n) for n in
                                          ("LAND_VALUE", "LAND_USE", "STOREYS", "HAS_SOLAR"))
    feats = list(layer.getFeatures())
    layer.startEditing()
    revalued = rnd.sample([f for f in feats[20:-10] if f[i_value]], 30)
    for f in revalued:
        layer.changeAttributeValue(f.id(), i_value,
                                   int(round(f[i_value] * rnd.uniform(1.03, 1.12), -3)))
    for f in revalued[:8]:
        if f[i_use] == "Residential":
            layer.changeAttributeValue(f.id(), i_use, "Commercial")
            layer.changeAttributeValue(f.id(), i_storeys, (f[i_storeys] or 1) + 1)
    for f in rnd.sample(feats[20:-10], 6):
        layer.changeAttributeValue(f.id(), i_solar, True)
    for f in feats[:3]:
        g = f.geometry()
        g.translate(4, 0)
        layer.changeGeometry(f.id(), g)
    layer.deleteFeatures([f.id() for f in feats[-5:]])
    for i in range(12):
        f = _record(rnd, flds, f"P-{900001 + i}")
        f.setGeometry(_square(19000 + i * 30, 19000))
        layer.addFeature(f)
    layer.commitChanges()
    QgsProject.instance().addMapLayer(layer)
    return layer


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def save(widget, name, crop=None):
    H.settle(0.5)
    pm = widget.grab()
    if crop is not None:
        pm = pm.copy(*crop)
    path = os.path.join(OUT, f"{THEME}_{name}.png")
    pm.save(path)
    print("saved", os.path.relpath(path))


def idle(panel):
    return (panel._has_result and not panel.runner.busy
            and not panel._data_timer.isActive() and not panel._refresh_timer.isActive())


def wait_profiles(panel):
    H.wait_for(lambda: idle(panel) and len(panel.profiles) >= len(panel.model.fields), 120)


def col(panel, name):
    return [f.name() for f in panel.model.fields].index(name)


def filt(panel, name, kind, op, value=None, value2=None, values=None):
    logical = col(panel, name)
    panel.header.cells[logical].set_filter(
        FL.ColumnFilter(name, kind, op, value, value2, values=values))


# ----------------------------------------------------------------------------
# shots
# ----------------------------------------------------------------------------
iface = H.FakeIface()
iface.mainWindow().show()
plugin = classFactory(iface)
plugin.initGui()
layer = sample_parcels()
iface.setActiveLayer(layer)

ex = plugin.open_explorer(layer)
host = plugin.explorers[layer.id()]
win = host._window
win.resize(1600, 820)
wait_profiles(ex)
filt(ex, "LAND_USE", F.TEXT, "eq", "Residential")
ex.refresh()
wait_profiles(ex)
save(win, "explorer")

if THEME == "dark":
    # full statistics of a numeric column
    logical = col(ex, "AREA_M2")
    ex._show_profile_details(logical)
    H.settle(0.6)
    pops = [w for w in QApplication.topLevelWidgets()
            if type(w).__name__ == "ProfileDetails" and w.isVisible()]
    save(pops[-1], "statistics")
    pops[-1].close()

    # the operator menu of a number filter
    cell = ex.header.cells[col(ex, "AREA_M2")]
    cell.op_menu.popup(cell.mapToGlobal(QPoint(0, cell.height())))
    H.settle(0.4)
    save(cell.op_menu, "filter_menu")
    cell.op_menu.hide()

# editing: a second filter, three selected rows, three unsaved values
filt(ex, "LAND_VALUE", F.INTEGER, "gt", "650000")
ex.refresh()
wait_profiles(ex)
layer.selectByIds(ex.model.fids[1:4])
H.settle(0.3)
ex.edit_bar.edit_btn.click()
H.settle(0.3)
value_col = col(ex, "LAND_VALUE")
for r in (1, 2, 3):
    ex.model.setData(ex.model.index(r, value_col),
                     int(ex.model.raw_value(r, value_col)) + 25000)
H.wait_for(lambda: idle(ex))
H.settle(1.0)
ex.view.setCurrentIndex(ex.model.index(5, value_col))
save(win, "editing")

if THEME == "dark":
    ex.edit_bar.quick_btn.click()
    H.settle(0.3)
    ex.quick_bar.field_combo.setField("LAND_VALUE")
    ex.quick_bar.expression.setExpression('round("LAND_VALUE" * 1.05, -3)')
    ex.quick_bar.preview_btn.setChecked(True)
    ex.quick_preview()
    H.settle(0.4)
    save(win, "quick_update")
    ex.quick_bar.preview_btn.setChecked(False)
    ex.quick_preview()
    ex.edit_bar.quick_btn.click()
    H.settle(0.2)

    ex.set_form_view(True)
    H.settle(0.6)
    save(win, "form_view")
    ex.set_form_view(False)
    H.settle(0.3)

layer.rollBack()
H.wait_for(lambda: idle(ex))

if THEME == "dark":
    # dates and yes / no columns side by side (own panel, own layer copy)
    other = sample_parcels(name="Sample parcels")
    box = QWidget()
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    panel = ExplorerPanel(H.FakeIface(), other)
    lay.addWidget(panel)
    box.resize(1440, 560)
    box.show()
    wait_profiles(panel)
    hdr = panel.header
    for target, name in enumerate(("SURVEY_DATE", "HAS_SOLAR", "HERITAGE", "PARCEL_ID")):
        logical = col(panel, name)
        hdr.moveSection(hdr.visualIndex(logical), target)
    panel.header.cells[col(panel, "SURVEY_DATE")].set_op("between")
    H.settle(0.6)
    save(box, "dates", crop=(0, 0, 900, 400))
    box.close()

# Pivot Table
pv = plugin.open_pivot(layer)
pwin = plugin.pivot_host._window
pwin.resize(1440, 780)
pv.set_spec(P.PivotSpec(["LAND_USE"], [], [P.Measure("LAND_VALUE", "mean"),
                                           P.Measure("AREA_M2", "sum")]))
pv.build()
H.wait_for(lambda: pv.model.result is not None and not pv.runner.busy, 60)
pv.table.sortByColumn(1, Qt.SortOrder.DescendingOrder)
save(pwin, "pivot")

# Geometry and Table Compare
base = sample_parcels(n=2000, seed=7, name="Sample parcels (baseline)")
new = sample_update(base)
cp = plugin.open_compare()
cwin = plugin.compare_host._window
cwin.resize(1440, 820)
cp.old_table_combo.setLayer(base)
cp.new_table_combo.setLayer(new)
cp.join_field_combo.setCurrentIndex(cp.join_field_combo.findText("PARCEL_ID"))
cp.geom_compare_cb.setChecked(True)
cp.compare_tables()
H.settle(1.6)
cp.filter_buttons["Unchanged"].setChecked(False)
save(cwin, "compare")

if THEME == "dark":
    # reviewing: validation report, decided values and rows
    cp.geom_compare_cb.setChecked(False)
    cp.validate_data()
    cp.compare_tables()
    H.settle(1.6)
    for status in ("Unchanged", "Added", "Deleted"):
        cp.filter_buttons[status].setChecked(False)
    tbl = cp.results_table
    rows = [r for r in range(tbl.rowCount()) if not tbl.isRowHidden(r)]
    multi = [r for r in rows if len(cp._row_values(r)) >= 2]
    single = [r for r in rows if len(cp._row_values(r)) == 1]
    tbl.clearSelection()
    cp._row_values(single[0])[0].setSelected(True)
    cp._decide_values("Accepted")
    values = cp._row_values(multi[0])
    tbl.clearSelection()
    values[0].setSelected(True)
    cp._decide_values("Accepted")
    tbl.clearSelection()
    values[1].setSelected(True)
    cp._decide_values("Rejected")
    cp._decide_rows([multi[1]], "Accepted")
    cp._decide_rows([single[1]], "Rejected")
    tbl.clearSelection()
    tbl.selectRow(rows[6])
    save(cwin, "review")

    plugin.toolbar.setIconSize(QSize(24, 24))
    save(plugin.toolbar, "toolbar")

    host.set_docked(True)
    iface.mainWindow().resize(1500, 950)
    save(iface.mainWindow(), "docked")

plugin.unload()
H.shutdown(APP)
