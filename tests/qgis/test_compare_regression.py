# -*- coding: utf-8 -*-
"""Geometry and Table Compare regression: exports must match the standalone
v1.1 plugin byte for byte.

The fixtures in tests/fixtures were exported by the original v1.1 module
from the same deterministic demo data (run with ``--record ORIGINAL.py`` to
re-record them from another module).
"""

import filecmp
import importlib.util
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

APP = H.start_qgis()
H.plugin_package()

from qgis.PyQt.QtWidgets import QFileDialog, QMessageBox  # noqa: E402

FIXTURES = os.path.join(H.ROOT, "tests", "fixtures")
_queue = []
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (_queue.pop(0), ""))
for _name in ("information", "warning", "critical"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))


def run_compare(cls, iface, base, new, geom, path):
    d = cls(iface)
    d.old_table_combo.setLayer(base)
    d.new_table_combo.setLayer(new)
    d.join_field_combo.setCurrentIndex(d.join_field_combo.findText("PARCEL_ID"))
    d.geom_compare_cb.setChecked(geom)
    d.compare_tables()
    d.results_table.selectRow(0)
    d.results_table.selectRow(1)
    d._apply_decision("Accepted")
    d.results_table.clearSelection()
    d.results_table.selectRow(3)
    d._apply_decision("Rejected")
    _queue[:] = [path]
    d.export_results()
    return d._compare_info["counts"]


def demo():
    base = H.demo_layer(n=400, name="Parcels baseline")
    return base, H.demo_updated_layer(base)


class CompareRegression(unittest.TestCase):
    def test_exports_match_v11(self):
        from attribute_table_studio.ui.geotable_compare import GeoTableCompareDialog
        base, new = demo()
        out = tempfile.mkdtemp()
        for geom in (False, True):
            name = f"compare_v11_{'geom' if geom else 'attr'}.csv"
            path = os.path.join(out, name)
            run_compare(GeoTableCompareDialog, H.FakeIface(), base, new, geom, path)
            self.assertTrue(filecmp.cmp(path, os.path.join(FIXTURES, name),
                                        shallow=False), name)


if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--record":
        spec = importlib.util.spec_from_file_location("orig_compare", sys.argv[2])
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        base, new = demo()
        for geom in (False, True):
            name = f"compare_v11_{'geom' if geom else 'attr'}.csv"
            print(name, run_compare(mod.GeoTableCompareDialog, H.FakeIface(),
                                    base, new, geom, os.path.join(FIXTURES, name)))
    else:
        H.run_tests(APP)
