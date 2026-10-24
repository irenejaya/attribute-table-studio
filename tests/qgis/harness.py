# -*- coding: utf-8 -*-
"""Helpers for running GeoTable Studio inside a headless QGIS.

Creates a QGIS application, a minimal iface (main window + map canvas) and a
demo parcel GeoPackage that resembles the design mockups.
"""

import os
import random
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def plugin_package():
    """Import the repository as the 'geotable_studio' package."""
    parent = tempfile.mkdtemp(prefix="gts_plugins_")
    link = os.path.join(parent, "geotable_studio")
    os.symlink(ROOT, link)
    sys.path.insert(0, parent)
    import geotable_studio  # noqa: F401
    return geotable_studio


def start_qgis(theme=None):
    from qgis.core import QgsApplication
    app = QgsApplication([], True)
    app.initQgis()
    if theme:
        QgsApplication.setUITheme(theme)
    return app


class FakeIface:
    """Just enough of QgisInterface for the tools."""

    def __init__(self):
        from qgis.PyQt.QtWidgets import QMainWindow
        from qgis.gui import QgsMapCanvas
        self._main = QMainWindow()
        self._main.resize(1400, 900)
        self._canvas = QgsMapCanvas(self._main)
        self._main.setCentralWidget(self._canvas)
        self._active = None
        self.messages = []
        self.toolbars = []
        self.menu_actions = []
        self.layer_actions = []
        self.registered = []

    def mainWindow(self):
        return self._main

    def mapCanvas(self):
        return self._canvas

    def activeLayer(self):
        return self._active

    def setActiveLayer(self, layer):
        self._active = layer

    def addDockWidget(self, area, dock):
        self._main.addDockWidget(area, dock)

    def addTabifiedDockWidget(self, area, dock, names=(), raise_tab=False):
        self._main.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self._main.removeDockWidget(dock)

    def addToolBar(self, name):
        tb = self._main.addToolBar(name)
        self.toolbars.append(tb)
        return tb

    def addPluginToMenu(self, name, action):
        self.menu_actions.append((name, action))

    def removePluginMenu(self, name, action):
        self.menu_actions = [p for p in self.menu_actions if p[1] is not action]

    def addCustomActionForLayerType(self, action, menu, layer_type, all_layers):
        self.layer_actions.append(action)

    def removeCustomActionForLayerType(self, action):
        self.layer_actions = [a for a in self.layer_actions if a is not action]
        return True

    def registerMainWindowAction(self, action, shortcut):
        self.registered.append((action, shortcut))
        return True

    def unregisterMainWindowAction(self, action):
        self.registered = [p for p in self.registered if p[0] is not action]
        return True

    def messageBar(self):
        return self

    def pushMessage(self, *args, **kwargs):
        self.messages.append(args)


LAND_USES = (["RESIDENTIAL"] * 93 + ["INDUSTRIAL"] * 4 + ["COMMERCIAL"] * 2
             + ["RURAL"] + ["OTHER"])


def demo_layer(n=7411, seed=7, path=None, name="Parcels flood results"):
    """GeoPackage of n square parcels with flood statistics."""
    from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
                           QgsVectorFileWriter, QgsVectorLayer)
    from qgis.PyQt.QtCore import QDate
    rnd = random.Random(seed)
    mem = QgsVectorLayer(
        "Polygon?crs=EPSG:28355"
        "&field=PARCEL_VIEW_PFI:string(20)&field=PARCEL_MASTER_FID:integer"
        "&field=LAND_USE_TYPE:string(30)&field=FL_1_PCT_MAX:double(10,2)"
        "&field=FL_1_PCT_MIN:double(10,2)&field=FL_2_PCT:double(10,2)"
        "&field=FL_5_PCT:double(10,2)&field=SURVEY_DATE:date"
        "&field=IN_FLOOD_ZONE:boolean&field=ZONE_CODE:integer",
        name, "memory")
    feats = []
    for i in range(n):
        f = QgsFeature(mem.fields())
        pfi = str(rnd.choice([2200000, 2450000, 3500000, 4070000, 4300000,
                              421480000]) + rnd.randint(0, 9999))
        if i < 12:
            pfi = ["421481771", "4079219", "4302955", "2206867", "3536545",
                   "421498140", "2457434", "2457440", "2457808", "2457792",
                   "4347804", "4125936"][i]
        base = rnd.choice([rnd.gauss(38, 6), rnd.gauss(59, 3), rnd.gauss(88, 4)])
        base = max(26.43, min(99.21, base))
        lu = rnd.choice(LAND_USES)
        if i in (9, 11):
            lu = "INDUSTRIAL"
        if i == 10:
            lu = "COMMERCIAL"
        d = QDate(2023, 1, 1).addDays(rnd.randint(0, 600))
        f.setAttributes([
            pfi, None, lu, round(base, 2), round(base - rnd.random(), 2),
            round(base - rnd.random() * 0.1, 2),
            round(base - rnd.random() * 0.3, 2) if rnd.random() > .3 else 0.0,
            d if rnd.random() > .05 else None,
            rnd.random() > .4 if rnd.random() > .02 else None,
            rnd.choice([1, 2, 2, 3, 3, 3, 4, 5])])
        x, y = 320000 + (i % 100) * 30, 5810000 + (i // 100) * 30
        f.setGeometry(QgsGeometry.fromPolygonXY([[
            QgsPointXY(x, y), QgsPointXY(x + 25, y), QgsPointXY(x + 25, y + 25),
            QgsPointXY(x, y + 25), QgsPointXY(x, y)]]))
        feats.append(f)
    mem.dataProvider().addFeatures(feats)
    path = path or os.path.join(tempfile.mkdtemp(prefix="gts_data_"), "parcels.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "parcels"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        mem, path, QgsProject.instance().transformContext(), opts)
    layer = QgsVectorLayer(path + "|layername=parcels", name, "ogr")
    assert layer.isValid(), path
    QgsProject.instance().addMapLayer(layer)
    return layer


def shutdown(app):
    """Release the project and exit QGIS cleanly (avoids crashes in C++
    static destructors when a headless test process ends)."""
    from qgis.core import QgsProject
    pump()
    QgsProject.instance().clear()
    pump()
    app.exitQgis()


def run_tests(app):
    """unittest.main() followed by a clean QGIS shutdown."""
    import unittest
    result = unittest.main(exit=False, verbosity=2).result
    shutdown(app)
    sys.exit(0 if result.wasSuccessful() else 1)


def pump():
    """processEvents() plus deferred deletes (like a running event loop)."""
    from qgis.PyQt.QtCore import QEvent
    from qgis.PyQt.QtWidgets import QApplication
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def wait_for(predicate, timeout=60.0):
    """Process events until predicate() is true (or timeout)."""
    end = time.time() + timeout
    while time.time() < end:
        pump()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def settle(seconds=0.3):
    end = time.time() + seconds
    while time.time() < end:
        pump()
        time.sleep(0.01)


def demo_updated_layer(base, n_added=12, n_deleted=5, n_modified=28, seed=11,
                       name="Parcels updated"):
    """Copy of *base* with added, deleted and modified features."""
    from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
                           QgsVectorFileWriter, QgsVectorLayer)
    rnd = random.Random(seed)
    path = os.path.join(tempfile.mkdtemp(prefix="gts_data_"), "parcels_new.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = "parcels"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        base, path, QgsProject.instance().transformContext(), opts)
    layer = QgsVectorLayer(path + "|layername=parcels", name, "ogr")
    feats = list(layer.getFeatures())
    layer.startEditing()
    idx = layer.fields().indexOf("FL_1_PCT_MAX")
    for f in rnd.sample(feats[20:], n_modified):
        layer.changeAttributeValue(f.id(), idx, round(f[idx] + rnd.uniform(.1, 2), 2))
    for f in feats[:3]:
        g = f.geometry()
        g.translate(4, 0)
        layer.changeGeometry(f.id(), g)
    layer.deleteFeatures([f.id() for f in feats[-n_deleted:]])
    for i in range(n_added):
        f = QgsFeature(layer.fields())
        f.setAttribute("PARCEL_VIEW_PFI", str(995001 + i))
        f.setAttribute("LAND_USE_TYPE", "COMMERCIAL")
        f.setAttribute("FL_1_PCT_MAX", 57.64)
        x, y = 290000 + i * 30, 5800000
        f.setGeometry(QgsGeometry.fromPolygonXY([[
            QgsPointXY(x, y), QgsPointXY(x + 25, y), QgsPointXY(x + 25, y + 25),
            QgsPointXY(x, y + 25), QgsPointXY(x, y)]]))
        layer.addFeature(f)
    layer.commitChanges()
    QgsProject.instance().addMapLayer(layer)
    return layer
