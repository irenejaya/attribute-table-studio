# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Regenerate the screenshots in docs/screenshots from a made-up dataset.

Run with QGIS 4's Python, once per look (QT_QPA_PLATFORM=offscreen works):
light is QGIS 4's Fusion, dark its built-in dark look (Fusion with the
system's dark palette, see harness.qgis4_dark).

    python tests/qgis/make_screenshots.py docs/screenshots dark
    python tests/qgis/make_screenshots.py docs/screenshots light

Every value is invented: random parcel IDs, land use, owner type, area,
land value, storeys, year built, survey dates and two yes / no flags, on a
grid of squares in a neutral spot of EPSG:3857.  The dark run also draws
dark_map_filter.png (the top row, then the map without and with the map
filter).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "docs/screenshots")
THEME = (sys.argv[2] if len(sys.argv) > 2 else "dark").lower()
APP = H.start_qgis("dark" if THEME == "dark" else None)   # QGIS 4's own looks
H.plugin_package()

from qgis.core import QgsFeature, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QPoint, QSize, Qt  # noqa: E402
from qgis.PyQt.QtWidgets import QApplication, QVBoxLayout, QWidget  # noqa: E402

from attribute_table_studio import classFactory  # noqa: E402
from attribute_table_studio.core import fields as F  # noqa: E402
from attribute_table_studio.core import filters as FL  # noqa: E402
from attribute_table_studio.core import pivot as P  # noqa: E402
from attribute_table_studio.ui import explorer_edit as E  # noqa: E402
from attribute_table_studio.ui.attribute_explorer import ExplorerPanel  # noqa: E402

E.ASK = lambda parent, title, text, choices, default=None, icon="question": \
    default or choices[0][0]

from sample_data import sample_parcels, sample_update  # noqa: E402,F401


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

# Visualize Statistics: the area of the Residential parcels, with parcels
# selected on the map shown in each bar; then split by owner type
chart = ex.visualize_statistics(col(ex, "AREA_M2"))
chart_win = ex.chart_host._window
chart_win.resize(1180, 680)
H.wait_for(lambda: chart.col is not None and not chart.reader.busy, 60)
chart.bins_spin.setValue(32)
layer.selectByIds(sorted(ex.model.fids)[::9])
H.settle(0.6)
save(chart_win, "chart")
if THEME == "dark":
    chart.split_combo.set_current_name("OWNER_TYPE")
    H.wait_for(lambda: chart.col is not None and chart.col.split_name == "OWNER_TYPE"
               and not chart.reader.busy, 60)
    H.settle(0.4)
    save(chart_win, "chart_split")
    # text as a histogram: one column per value (all parcels, owner type)
    layer.removeSelection()
    chart.split_combo.set_current_name(None)
    chart.set_field(col(ex, "OWNER_TYPE"))
    H.wait_for(lambda: chart.col is not None and chart.col.name == "OWNER_TYPE"
               and chart.col.split_name is None and not chart.reader.busy, 60)
    chart.kind_combo.setCurrentIndex(chart.kind_combo.findData("histogram"))
    H.settle(0.4)
    save(chart_win, "chart_text")
layer.removeSelection()
ex.chart_host.close()
H.settle(0.3)

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

    # Join table: a made-up zones table (one land use missing, so not every
    # record matches)
    from attribute_table_studio.ui.explorer_join import JoinTableDialog
    zones = QgsVectorLayer("None?field=LAND_USE:string(20)&field=ZONE:string(10)"
                           "&field=MAX_STOREYS:integer&field=LU_CODE:string(4)",
                           "Land use zones", "memory")
    zone_rows = [("Residential", "R2", 3, "RES"), ("Commercial", "B4", 8, "COM"),
                 ("Industrial", "IN1", 3, "IND"), ("Rural", "RU1", 2, "RUR"),
                 ("Open space", "RE1", 1, "OSP")]
    feats = []
    for values in zone_rows:
        f = QgsFeature(zones.fields())
        f.setAttributes(list(values))
        feats.append(f)
    zones.dataProvider().addFeatures(feats)
    QgsProject.instance().addMapLayer(zones)
    dlg = JoinTableDialog(ex)
    dlg.layer_combo.setLayer(zones)
    H.wait_for(lambda: dlg.counts is not None, 30)
    for i in (0, 1):                     # none are ticked at first
        dlg.field_list.item(i).setCheckState(Qt.CheckState.Checked)
    dlg.show()
    dlg.resize(600, dlg.sizeHint().height())
    save(dlg, "join_table")
    dlg.reject()
    QgsProject.instance().removeMapLayer(zones.id())

    # Field properties: an alias and a Not null rule changed, not applied
    from attribute_table_studio.ui.explorer_fields import COL, FieldPropertiesDialog
    fdlg = FieldPropertiesDialog(ex, "LAND_USE")
    names = [f.name() for f in layer.fields()]
    fdlg.table.item(names.index("OWNER_TYPE"), COL["alias"]).setText("Owner type")
    fdlg.table.item(names.index("LAND_VALUE"), COL["allownull"]).setCheckState(
        Qt.CheckState.Unchecked)
    fdlg.table.item(names.index("SURVEY_DATE"), COL["default"]).setText("now()")
    fdlg.show()
    fdlg.resize(1180, 470)
    save(fdlg, "field_properties")
    fdlg.reject()

# editing: a second filter, three selected rows, three unsaved values
filt(ex, "LAND_VALUE", F.INTEGER, "gt", "650000")
ex.refresh()
wait_profiles(ex)
layer.selectByIds(ex.model.fids[1:4])
H.settle(0.3)
ex.edit_bar.edit_btn.click()
H.settle(0.3)
ex.edit_bar.quick_btn.setChecked(False)     # opened by editing; shown on its own below
value_col = col(ex, "LAND_VALUE")
for r in (1, 2, 3):
    ex.model.setData(ex.model.index(r, value_col),
                     int(ex.model.raw_value(r, value_col)) + 25000)
H.wait_for(lambda: idle(ex))
H.settle(1.0)
ex.view.setCurrentIndex(ex.model.index(5, value_col))
save(win, "editing")

if THEME == "dark":
    ex.edit_bar.quick_btn.setChecked(True)
    H.settle(0.3)
    ex.quick_bar.field_combo.setField("LAND_VALUE")
    ex.quick_bar.expression.setExpression('round("LAND_VALUE" * 1.05, -3)')
    ex.quick_bar.preview_btn.setChecked(True)
    ex.quick_preview()
    H.settle(0.4)
    save(win, "quick_update")
    ex.quick_bar.preview_btn.setChecked(False)
    ex.quick_preview()
    ex.edit_bar.quick_btn.setChecked(False)
    H.settle(0.2)

    ex.set_form_view(True)
    H.settle(0.6)
    save(win, "form_view")
    ex.set_form_view(False)
    H.settle(0.3)

    # find and replace (Ctrl+H) while editing
    bar = ex.find_bar
    bar.open(replace=True)
    bar.find_box.edit.setText("Council")
    bar.replace_box.edit.setText("City council")
    H.wait_for(lambda: not bar._timer.isActive() and not bar._runner.busy)
    H.settle(0.4)
    save(win, "find_replace")
    bar.dismiss()
    H.settle(0.2)

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

    # map filter: the top row, then the map without and with it
    from qgis.core import (QgsFillSymbol, QgsMapRendererCustomPainterJob,
                           QgsMapSettings, QgsSingleSymbolRenderer)
    from qgis.PyQt.QtCore import QRect
    from qgis.PyQt.QtGui import QColor, QImage, QPainter
    layer.setRenderer(QgsSingleSymbolRenderer(QgsFillSymbol.createSimple(
        {"color": "#e8b931", "outline_color": "#5a4a10", "outline_width": "0.1"})))
    extent = layer.extent()

    def render():
        ms = QgsMapSettings()
        ms.setLayers([layer])
        ms.setDestinationCrs(layer.crs())
        ms.setOutputSize(QSize(560, 240))
        ms.setExtent(extent.buffered(extent.width() * 0.04))
        ms.setBackgroundColor(QColor("white"))
        image = QImage(ms.outputSize(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("white"))
        painter = QPainter(image)
        job = QgsMapRendererCustomPainterJob(ms, painter)
        job.start()
        job.waitForFinished()
        painter.end()
        return image

    def wrect(widget):
        return QRect(widget.mapTo(win, QPoint(0, 0)), widget.size())

    layer.removeSelection()
    off = render()
    ex.query_btn.setChecked(True)
    H.wait_for(lambda: idle(ex))
    H.settle(0.4)
    on = render()
    strip = win.grab(wrect(ex.view_btn).united(wrect(ex.dock_btn))
                     .adjusted(-6, -6, 6, 6)).toImage()
    gap, pad, cap = 20, 10, 22
    width = max(strip.width(), off.width() * 2 + gap) + 2 * pad
    sheet = QImage(width, pad + strip.height() + 12 + cap + off.height() + pad,
                   QImage.Format.Format_ARGB32)
    sheet.fill(QColor("#323232"))
    painter = QPainter(sheet)
    painter.drawImage(width - pad - strip.width(), pad, strip)
    y = pad + strip.height() + 12
    x0 = width - pad - (off.width() * 2 + gap)
    painter.setPen(QColor("#e6e6e6"))
    align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
    painter.drawText(QRect(x0, y, off.width(), cap), align, "Map filter off — every feature")
    painter.drawText(QRect(x0 + off.width() + gap, y, off.width(), cap), align,
                     "Map filter on — only the filtered records")
    painter.drawImage(x0, y + cap, off)
    painter.drawImage(x0 + off.width() + gap, y + cap, on)
    painter.end()
    sheet.save(os.path.join(OUT, "dark_map_filter.png"))
    print("saved", os.path.relpath(os.path.join(OUT, "dark_map_filter.png")))
    ex.query_btn.setChecked(False)
    H.wait_for(lambda: idle(ex))

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

if THEME == "dark":
    # the pivot as a chart: land use by owner type, one bar selected
    pv.set_spec(P.PivotSpec(["LAND_USE"], ["OWNER_TYPE"], [P.Measure("AREA_M2", "sum")]))
    pv.build()
    H.wait_for(lambda: pv.model.result is not None and not pv.runner.busy, 60)
    pc = pv.open_chart()
    pc_win = pv.chart_host._window
    pc_win.resize(1180, 680)
    pc.sort_combo.setCurrentIndex(pc.sort_combo.findData("count"))
    H.settle(0.3)
    pc.view.barsPicked.emit([1], False, None)
    H.settle(0.5)
    save(pc_win, "pivot_chart")
    pv.chart_host.close()
    layer.removeSelection()

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

    # the changed fields, from the Changed chip of the Review row
    tbl.clearSelection()
    pop = cp.show_changed_fields()
    H.settle(0.4)
    chip = cp.changed_field_legend
    corner = chip.mapTo(cwin, chip.rect().bottomRight())
    shot = cwin.grab()
    painter = QPainter(shot)
    painter.drawPixmap(corner.x() - pop.width(), corner.y() + 4, pop.grab())
    painter.end()
    shot = shot.copy(0, 0, shot.width(), min(shot.height(), corner.y() + pop.height() + 90))
    shot.save(os.path.join(OUT, "dark_changed_fields.png"))
    print("saved", os.path.relpath(os.path.join(OUT, "dark_changed_fields.png")))
    pop.close()

    plugin.toolbar.setIconSize(QSize(24, 24))
    save(plugin.toolbar, "toolbar")

    host.set_docked(True)
    iface.mainWindow().resize(1500, 950)
    save(iface.mainWindow(), "docked")

plugin.unload()
H.shutdown(APP)
