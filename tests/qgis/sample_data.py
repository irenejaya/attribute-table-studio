# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Made-up parcels for the README screenshots and the Help pictures (no real
place, owner or value): sample_parcels() and sample_update()."""

import os
import random
import tempfile

from qgis.core import (QgsFeature, QgsGeometry, QgsPointXY, QgsProject,
                       QgsVectorFileWriter, QgsVectorLayer)
from qgis.PyQt.QtCore import QDate

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
