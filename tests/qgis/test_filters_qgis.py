# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Filter expressions evaluated by real QGIS on memory and GeoPackage layers.

Run with QGIS' Python:  python tests/qgis/test_filters_qgis.py
"""

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from qgis.core import (QgsApplication, QgsVectorLayer, QgsFeature,  # noqa: E402
                       QgsFeatureRequest, QgsVectorFileWriter, QgsProject,
                       QgsExpression)
from qgis.PyQt.QtCore import QDate, QDateTime  # noqa: E402

APP = QgsApplication([], False)
APP.initQgis()

from core import fields as F  # noqa: E402
from core import filters as FL  # noqa: E402

ROWS = [
    # name,        v,     d,                 b,     dt
    ("RESIDENTIAL", 88.44, QDate(2024, 1, 2), True, QDateTime(2024, 1, 2, 23, 0, 0)),
    ("residential", 0.0, QDate(2024, 1, 3), False, QDateTime(2024, 1, 3, 1, 0, 0)),
    ("O'Brien 50%", None, None, None, None),
    ("Zürich_x", 60.0, QDate(2023, 12, 31), True, QDateTime(2023, 12, 31, 12, 0, 0)),
    ("", 61.5, QDate(2024, 1, 2), False, QDateTime(2024, 1, 2, 0, 0, 0)),
    (None, 59.99, QDate(2024, 2, 1), None, QDateTime(2024, 2, 1, 8, 30, 0)),
]


def memory_layer():
    lyr = QgsVectorLayer("None?field=name:string&field=v:double&field=d:date"
                         "&field=b:boolean&field=dt:datetime", "t", "memory")
    feats = []
    for row in ROWS:
        f = QgsFeature(lyr.fields())
        f.setAttributes(list(row))
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    return lyr


def gpkg_layer(src):
    path = os.path.join(tempfile.mkdtemp(), "t.gpkg")
    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    QgsVectorFileWriter.writeAsVectorFormatV3(
        src, path, QgsProject.instance().transformContext(), opts)
    lyr = QgsVectorLayer(path, "g", "ogr")
    assert lyr.isValid()
    return lyr


def names(layer, expr):
    exp = QgsExpression(expr)
    assert not exp.hasParserError(), (expr, exp.parserErrorString())
    out = []
    for f in layer.getFeatures(QgsFeatureRequest().setFilterExpression(expr)):
        out.append(f["name"] if not F.is_null(f["name"]) else None)
    return sorted(out, key=F.sort_key)


CASES = [
    (("name", F.TEXT, "contains", "resid"), ["residential", "RESIDENTIAL"]),
    (("name", F.TEXT, "eq", "RESIDENTIAL"), ["RESIDENTIAL"]),
    (("name", F.TEXT, "contains", "50%"), ["O'Brien 50%"]),
    (("name", F.TEXT, "contains", "h_x"), ["Zürich_x"]),
    (("name", F.TEXT, "contains", "o'b"), ["O'Brien 50%"]),
    (("name", F.TEXT, "starts", "res"), ["residential", "RESIDENTIAL"]),
    (("name", F.TEXT, "null", ""), [None]),
    (("v", F.REAL, "eq", "0"), ["residential"]),
    (("v", F.REAL, "gt", "60"), ["", "RESIDENTIAL"]),
    (("v", F.REAL, "ne", "0"), ["", "RESIDENTIAL", "Zürich_x", None]),
    (("v", F.REAL, "between", "60", "61.5"), ["", "Zürich_x"]),
    (("v", F.REAL, "null", ""), ["O'Brien 50%"]),
    (("d", F.DATE, "eq", "2024-01-02"), ["", "RESIDENTIAL"]),
    (("d", F.DATE, "lt", "2024-01-01"), ["Zürich_x"]),
    (("d", F.DATE, "between", "2024-01-02", "2024-01-03"),
     ["", "residential", "RESIDENTIAL"]),
    (("dt", F.DATETIME, "eq", "2024-01-02"), ["", "RESIDENTIAL"]),
    (("dt", F.DATETIME, "ge", "2024-01-03"), ["residential", None]),
    (("b", F.BOOL, "true", ""), ["RESIDENTIAL", "Zürich_x"]),
    (("b", F.BOOL, "false", ""), ["", "residential"]),
]


class QgisFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mem = memory_layer()
        cls.gpkg = gpkg_layer(cls.mem)

    def check(self, layer):
        for (field, kind, op, value, *rest), expected in CASES:
            value2 = rest[0] if rest else ""
            expr = FL.ColumnFilter(field, kind, op, value, value2).build()
            self.assertEqual(names(layer, expr),
                             sorted(expected, key=F.sort_key), expr)
        expr = FL.ColumnFilter("name", F.TEXT, "in",
                               values=["RESIDENTIAL", None]).build()
        self.assertEqual(names(layer, expr), ["RESIDENTIAL", None])
        expr = FL.ColumnFilter("v", F.REAL, "in", values=[0.0, 60]).build()
        self.assertEqual(names(layer, expr), ["residential", "Zürich_x"])

    def test_memory(self):
        self.check(self.mem)

    def test_geopackage(self):
        self.check(self.gpkg)


if __name__ == "__main__":
    _result = unittest.main(exit=False, verbosity=1).result
    QgisFilterTests.mem = QgisFilterTests.gpkg = None
    QgsProject.instance().clear()
    APP.exitQgis()
    sys.exit(0 if _result.wasSuccessful() else 1)
