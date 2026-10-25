# -*- coding: utf-8 -*-
"""Attribute Explorer editing, forms, bulk tools and browse parity, in real
QGIS (run with the QGIS Python: python tests/qgis/test_editing_qgis.py)."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness as H  # noqa: E402

APP = H.start_qgis()
H.plugin_package()

from qgis.PyQt import sip  # noqa: E402
from qgis.PyQt.QtCore import QEvent, QModelIndex, QPoint, Qt  # noqa: E402
from qgis.PyQt.QtGui import QColor, QKeyEvent, QKeySequence  # noqa: E402
from qgis.PyQt.QtWidgets import QMenu, QMessageBox  # noqa: E402
from qgis.core import (Qgis, QgsConditionalStyle, QgsFeatureRequest, QgsField,  # noqa: E402
                       QgsFieldConstraints, QgsRectangle)

from attribute_table_studio import classFactory  # noqa: E402
from attribute_table_studio.core import fields as F  # noqa: E402
from attribute_table_studio.core import filters as FL  # noqa: E402
from attribute_table_studio.core import settings  # noqa: E402
from attribute_table_studio.ui import explorer_dialogs as D  # noqa: E402
from attribute_table_studio.ui import explorer_edit as E  # noqa: E402
from attribute_table_studio.ui import icons  # noqa: E402

for _name in ("information", "warning", "critical"):
    setattr(QMessageBox, _name, staticmethod(lambda *a, **k: None))


class Asker:
    """Scripted answers for the Explorer prompts (records what was asked)."""

    def __init__(self):
        self.answers, self.asked = [], []

    def __call__(self, parent, title, text, choices, default=None, icon="question"):
        self.asked.append((title, text, [c[0] for c in choices]))
        if self.answers:
            return self.answers.pop(0)
        return default or choices[-1][0]


ASK = Asker()
E.ASK = ASK


def idle(panel):
    return panel._has_result and not panel.runner.busy and not panel._data_timer.isActive() \
        and not panel._mutation_timer.isActive()


def settle(panel):
    assert H.wait_for(lambda: idle(panel), 60)
    H.settle(0.05)
    assert H.wait_for(lambda: idle(panel), 60)


class EditingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.iface = H.FakeIface()
        cls.plugin = classFactory(cls.iface)
        cls.plugin.initGui()

    @classmethod
    def tearDownClass(cls):
        cls.plugin.unload()

    def setUp(self):
        ASK.answers, ASK.asked = [], []
        self.layer = H.demo_layer(n=400, seed=21, name=f"Edit parcels {self.id()[-12:]}")
        self.iface.setActiveLayer(self.layer)
        self.panel = self.plugin.open_explorer(self.layer)
        settle(self.panel)
        self.fl = self.layer.fields().indexOf("FL_1_PCT_MAX")
        self.lu = self.layer.fields().indexOf("LAND_USE_TYPE")

    def tearDown(self):
        layer = self.layer
        if layer.isEditable():
            layer.rollBack()
        host = self.plugin.explorers.get(layer.id())
        if host is not None:
            host.close()
        H.settle(0.1)

    def start(self):
        self.panel.edit_bar.edit_btn.click()
        self.assertTrue(self.layer.isEditable())
        H.settle(0.15)

    # ------------------------------------------------------------------
    def test_01_rows_icons_and_quick_update_visibility(self):
        p, b = self.panel, self.panel.edit_bar
        top = p.columns_btn.mapTo(p, QPoint(0, 0)).y()
        self.assertGreater(b.mapTo(p, QPoint(0, 0)).y(), top)
        # View and Select sit before Columns, layer info stays left
        x = lambda w: w.mapTo(p, QPoint(0, 0)).x()
        self.assertLess(x(p.view_btn), x(p.select_btn))
        self.assertLess(x(p.select_btn), x(p.columns_btn))
        self.assertLess(x(p.layer_combo), x(p.view_btn))
        # both rows end at the same right edge (Dock / More)
        right = lambda w: w.mapTo(p, QPoint(w.width(), 0)).x()
        self.assertLessEqual(abs(right(p.dock_btn) - right(b.more_btn)), 2)
        self.assertNotEqual(icons.CALCULATE, icons.QUICK_UPDATE)
        self.assertEqual(icons.QUICK_UPDATE, "/mActionProcessSelected.svg")
        # the scope line sits on the left of the edit row (no row of its own);
        # the edit status is in the status bar, dim while read only
        y = lambda w: w.mapTo(p, QPoint(0, w.height() // 2)).y()
        self.assertLessEqual(abs(y(p.scope_label) - y(b.edit_btn)), 2)
        self.assertLess(x(p.scope_label), x(b.edit_btn))
        self.assertTrue(p.scope_label.fullText().startswith("Statistics for:"))
        self.assertLess(p.edit_bar.mapTo(p, QPoint(0, b.height())).y(),
                        p.view.mapTo(p, QPoint(0, 0)).y())
        extra = p.status.extra
        self.assertEqual(extra.fullText(), "Read only")
        self.assertEqual(extra.property("level"), "info")                # dim
        for btn in (b.save_btn, b.discard_btn, b.undo_btn, b.add_btn, b.calc_btn,
                    b.quick_btn, b.multi_btn, b.new_field_btn):
            self.assertFalse(btn.isEnabled(), btn.toolTip())
            self.assertIn("Unavailable", btn.toolTip())
        self.assertTrue(b.edit_btn.isEnabled())
        self.assertTrue(b.reload_btn.isEnabled())
        self.assertFalse(p.quick_bar.isVisibleTo(p))
        self.start()
        self.assertTrue(extra.fullText().startswith("Editing layer  •  No unsaved changes"))
        self.assertEqual(extra.property("level"), "editing-clean")      # orange
        self.assertTrue(b.quick_btn.isEnabled())
        self.assertFalse(b.quick_btn.isChecked())
        self.assertFalse(p.quick_bar.isVisibleTo(p))     # editing alone: hidden
        b.quick_btn.click()
        self.assertTrue(p.quick_bar.isVisibleTo(p))
        b.quick_btn.click()
        self.assertFalse(p.quick_bar.isVisibleTo(p))
        b.quick_btn.click()
        b.edit_btn.click()                                # stop (no changes)
        self.assertFalse(self.layer.isEditable())
        self.assertFalse(b.quick_btn.isChecked())
        self.assertFalse(p.quick_bar.isVisibleTo(p))

    def test_02_shared_session_and_stop_prompt(self):
        p, b = self.panel, self.panel.edit_bar
        self.layer.startEditing()                         # elsewhere in QGIS
        H.settle(0.2)
        self.assertTrue(b.edit_btn.isChecked())
        self.assertIn("Editing layer", p.status.extra.fullText())
        fid = p.model.fid(0)
        self.layer.changeAttributeValue(fid, self.lu, "OTHER")   # e.g. the map
        settle(p)
        self.assertIn("1 unsaved cell", p.status.extra.fullText())
        self.assertEqual(p.status.extra.property("level"), "editing")
        ASK.answers = ["cancel"]
        b.edit_btn.click()
        self.assertTrue(self.layer.isEditable())
        self.assertEqual(ASK.asked[-1][2], ["save", "discard", "cancel"])
        ASK.answers = ["discard"]
        b.edit_btn.click()
        self.assertFalse(self.layer.isEditable())
        self.assertNotEqual(self.layer.getFeature(fid)["LAND_USE_TYPE"], "OTHER")

    def test_03_cell_edit_undo_redo_save(self):
        p = self.panel
        self.start()
        row = 0
        fid = p.model.fid(row)
        idx = p.model.index(row, self.fl)
        self.assertTrue(p.model.flags(idx) & Qt.ItemFlag.ItemIsEditable)
        self.assertTrue(p.model.setData(idx, 12.5))
        self.assertEqual(self.layer.getFeature(fid)["FL_1_PCT_MAX"], 12.5)
        settle(p)
        self.assertTrue(p.model.is_dirty(fid, self.fl))
        self.assertTrue(p.edit_bar.save_btn.isEnabled())
        self.assertEqual(p.edit.counts()["cells"], 1)
        p.edit.undo()
        self.assertNotEqual(self.layer.getFeature(fid)["FL_1_PCT_MAX"], 12.5)
        p.edit.redo()
        self.assertEqual(self.layer.getFeature(fid)["FL_1_PCT_MAX"], 12.5)
        self.assertTrue(p.save_edits())
        self.assertTrue(self.layer.isEditable())             # stays editing
        self.assertFalse(self.layer.isModified())
        saved = next(self.layer.dataProvider().getFeatures(
            QgsFeatureRequest().setFilterFid(fid)))
        self.assertEqual(saved["FL_1_PCT_MAX"], 12.5)
        settle(p)
        self.assertFalse(p.model.is_dirty(fid, self.fl))
        # profiles use the saved / unsaved values (12.5 is the new minimum)
        self.assertTrue(H.wait_for(lambda: self.fl in p.profiles, 30))
        self.assertEqual(p.profiles[self.fl].minimum, 12.5)

    def test_04_constraints_and_read_only_fields(self):
        p = self.panel
        layer = self.layer
        layer.setFieldConstraint(self.lu, QgsFieldConstraints.Constraint.ConstraintNotNull,
                                 QgsFieldConstraints.ConstraintStrength.ConstraintStrengthHard)
        layer.addExpressionField('"FL_1_PCT_MAX" * 2', QgsField("DOUBLE_MAX", D._MT.Double))
        settle(p)
        self.start()
        rejected = []
        p.model.editRejected.connect(rejected.append)
        idx = p.model.index(0, self.lu)
        before = p.model.raw_value(0, self.lu)
        self.assertFalse(p.model.setData(idx, None))
        self.assertTrue(rejected and "LAND_USE_TYPE" in rejected[-1])
        self.assertEqual(p.model.raw_value(0, self.lu), before)
        virtual = layer.fields().indexFromName("DOUBLE_MAX")
        fid_idx = layer.fields().indexFromName("fid")
        self.assertFalse(p.edit.field_editable(virtual)[0])
        self.assertIn("Virtual", p.edit.field_editable(virtual)[1])
        self.assertFalse(p.edit.field_editable(fid_idx)[0])
        self.assertNotIn(virtual, p.model.editable_cols)
        self.assertNotIn(fid_idx, p.model.editable_cols)
        layer.removeExpressionField(virtual)

    def test_05_save_failure_keeps_edits(self):
        p = self.panel
        self.start()
        p.model.setData(p.model.index(0, self.fl), 1.25)
        layer = self.layer
        layer.commitChanges = lambda *a: False
        layer.commitErrors = lambda: ["ERROR: simulated provider failure"]
        try:
            self.assertFalse(p.save_edits())
        finally:
            del layer.commitChanges
            del layer.commitErrors
        self.assertTrue(layer.isEditable())
        self.assertTrue(layer.isModified())
        self.assertIn("Save failed", p.status.extra.fullText())
        self.assertEqual(p.status.extra.property("level"), "error")
        self.assertIn("simulated provider failure", ASK.asked[-1][1])

    def test_06_add_delete_and_edited_scope(self):
        p = self.panel
        self.start()
        n = self.layer.featureCount()
        p.add_record_in_table()
        self.assertEqual(self.layer.featureCount(), n + 1)
        self.assertTrue(p.status.action_btn.isVisibleTo(p))
        self.assertEqual(p.status.action_btn.text(), "Show edited and new")
        p.status.action_btn.click()
        settle(p)
        self.assertEqual(p.view_scope, "edited")
        self.assertEqual(len(p.model.fids), 1)
        self.assertLess(p.model.fids[0], 0)                  # temporary id
        p.set_view_scope("all")
        settle(p)
        # delete selected, some of them hidden by a filter
        p.header.cells[self.lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq",
                                                           "RESIDENTIAL"))
        p.refresh()
        settle(p)
        shown = p.model.fids[:2]
        hidden = [f for f in self.layer.allFeatureIds() if f not in set(p.model.fids)][:1]
        self.layer.selectByIds(shown + hidden)
        ASK.answers = ["delete"]
        p.delete_selected()
        self.assertIn("1 of them are not shown", ASK.asked[-1][1])
        self.assertEqual(self.layer.featureCount(), n + 1 - 3)
        p.edit.undo()                                        # one undo step
        self.assertEqual(self.layer.featureCount(), n + 1)

    def test_07_feature_clipboard(self):
        p = self.panel
        self.layer.selectByIds(p.model.fids[:2])
        H.settle(0.1)
        self.assertTrue(p.edit_bar.copy_btn.isEnabled())      # browsing too
        self.assertFalse(p.edit_bar.paste_btn.isEnabled())
        p.copy_features()
        self.start()
        n = self.layer.featureCount()
        p.paste_features()
        self.assertEqual(self.layer.featureCount(), n + 2)
        self.layer.selectByIds(p.model.fids[:1])
        H.settle(0.1)
        p.cut_features()
        self.assertEqual(self.layer.featureCount(), n + 1)

    def test_08_new_and_delete_fields(self):
        p = self.panel
        cells = p.header.cells
        cells[self.lu].set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "RESIDENTIAL"))
        p.refresh()
        settle(p)
        self.start()
        dlg = D.NewFieldDialog(p)
        self.assertTrue(dlg.physical.isEnabled())
        dlg.name_edit.setText("NOTES")
        dlg.type_combo.setCurrentIndex(max(0, dlg.type_combo.findText("Text (string)")))
        dlg._apply()
        settle(p)
        self.assertGreaterEqual(self.layer.fields().indexFromName("NOTES"), 0)
        # unrelated filters survive the schema change
        lu = self.layer.fields().indexFromName("LAND_USE_TYPE")
        self.assertIsNotNone(p.header.cells[lu].current_filter())
        dlg = D.NewFieldDialog(p)
        dlg.virtual.setChecked(True)
        dlg.name_edit.setText("HALF_MAX")
        dlg.expr.setExpression('"FL_1_PCT_MAX" / 2')
        dlg._apply()
        settle(p)
        virtual = self.layer.fields().indexFromName("HALF_MAX")
        self.assertEqual(self.layer.fields().fieldOrigin(virtual),
                         Qgis.FieldOrigin.Expression)
        ASK.answers = ["delete"]
        dlg = D.DeleteFieldDialog(p, "NOTES")
        dlg._apply()
        settle(p)
        self.assertLess(self.layer.fields().indexFromName("NOTES"), 0)
        ASK.answers = ["delete"]
        dlg = D.DeleteFieldDialog(p, "HALF_MAX")
        dlg._apply()
        self.assertLess(self.layer.fields().indexFromName("HALF_MAX"), 0)

    def test_09_field_calculator_targets(self):
        p = self.panel
        self.start()
        self.layer.selectByIds(p.model.fids[:3])
        H.settle(0.1)
        counts = p.target_counts()
        self.assertEqual(counts["selected_layer"], 3)
        self.assertEqual(counts["layer"], self.layer.featureCount())
        dlg = D.FieldCalculatorDialog(p, "FL_1_PCT_MAX")
        self.assertEqual(dlg.mode(), "update")
        dlg.target_combo.setCurrentIndex(dlg.target_combo.findData("selected_layer"))
        dlg.builder.setExpressionText("7")
        dlg._update_preview()
        self.assertIn("3 feature(s)", dlg.preview.text())
        depth = self.layer.undoStack().count()
        dlg._apply()
        self.assertEqual(self.layer.undoStack().count(), depth + 1)   # one step
        for fid in p.model.fids[:3]:
            self.assertEqual(self.layer.getFeature(fid)["FL_1_PCT_MAX"], 7)
        dlg = D.FieldCalculatorDialog(p)
        dlg.mode_new.setChecked(True)
        dlg._on_mode()
        dlg.name_edit.setText("CALC")
        dlg.type_combo.setCurrentIndex(max(0, dlg.type_combo.findText("Decimal number (double)")))
        dlg.builder.setExpressionText('"FL_1_PCT_MAX" + 1')
        dlg.target_combo.setCurrentIndex(dlg.target_combo.findData("selected_layer"))
        dlg._apply()
        calc = self.layer.fields().indexFromName("CALC")
        self.assertGreaterEqual(calc, 0)
        first = sorted(self.layer.selectedFeatureIds())[0]
        self.assertEqual(self.layer.getFeature(first)["CALC"], 8)

    def test_10_quick_update_preview_apply_undo(self):
        p = self.panel
        self.start()
        self.layer.selectByIds(p.model.fids[:4])
        H.settle(0.1)
        p.edit_bar.quick_btn.click()
        q = p.quick_bar
        q.field_combo.setField("FL_1_PCT_MAX")
        q.expression.setExpression('"FL_1_PCT_MAX" + 1000')
        q.target_combo.setCurrentIndex(q.target_combo.findData("selected_shown"))
        before = {f: self.layer.getFeature(f)["FL_1_PCT_MAX"] for f in p.model.fids[:4]}
        p.quick_preview()
        self.assertTrue(q.preview.isVisibleTo(p))
        self.assertIn("4 targeted features", q.preview_title.text())
        self.assertEqual(q.apply_btn.text(), "Apply to 4 features")
        self.assertEqual({f: self.layer.getFeature(f)["FL_1_PCT_MAX"] for f in before},
                         before)                              # preview writes nothing
        depth = self.layer.undoStack().count()
        p.quick_apply()
        self.assertEqual(self.layer.undoStack().count(), depth + 1)
        for f, v in before.items():
            self.assertAlmostEqual(self.layer.getFeature(f)["FL_1_PCT_MAX"], v + 1000)
        p.edit.undo()
        self.assertEqual({f: self.layer.getFeature(f)["FL_1_PCT_MAX"] for f in before},
                         before)

    def test_11_multi_edit(self):
        p = self.panel
        self.start()
        ids = p.model.fids[:3]
        self.layer.selectByIds(ids)
        dlg = D.MultiEditDialog(p, ids)
        dlg.form.changeAttribute("LAND_USE_TYPE", "MIXED")
        dlg._apply()
        for fid in ids:
            self.assertEqual(self.layer.getFeature(fid)["LAND_USE_TYPE"], "MIXED")

    def test_12_form_view(self):
        p = self.panel
        fid = p.model.fid(5)
        p.view.setCurrentIndex(p.model.index(5, self.lu))
        p.set_form_view(True)
        self.assertTrue(p.is_form_view())
        self.assertEqual(p.form_view.current, fid)
        self.start()
        p.form_view.form.changeAttribute("LAND_USE_TYPE", "FORM EDIT")
        p.set_form_view(False)                               # writes the form
        self.assertFalse(p.is_form_view())
        self.assertEqual(self.layer.getFeature(fid)["LAND_USE_TYPE"], "FORM EDIT")
        settle(p)
        row = p.model.row_of(fid)
        self.assertEqual(p.view.currentIndex().row(), row)

    def test_13_scopes(self):
        p = self.panel
        layer = self.layer
        # selected scope and the Selected only button are one setting
        layer.selectByIds(p.model.fids[:5])
        p.selected_btn.click()
        settle(p)
        self.assertEqual(p.view_scope, "selected")
        self.assertEqual(len(p.model.fids), 5)
        p.set_view_scope("all")
        self.assertFalse(p.selected_btn.isChecked())
        settle(p)
        # visible on map
        canvas = self.iface.mapCanvas()
        canvas.setDestinationCrs(layer.crs())
        ext = layer.extent()
        canvas.setExtent(QgsRectangle(ext.xMinimum(), ext.yMinimum(),
                                      ext.center().x(), ext.center().y()))
        p.set_view_scope("map")
        settle(p)
        self.assertGreater(len(p.model.fids), 0)
        self.assertLess(len(p.model.fids), layer.featureCount())
        self.assertIn("visible on the map", p.scope_label.fullText())
        # failing constraints
        layer.setFieldConstraint(self.lu, QgsFieldConstraints.Constraint.ConstraintExpression,
                                 QgsFieldConstraints.ConstraintStrength.ConstraintStrengthSoft)
        layer.setConstraintExpression(self.lu, '"LAND_USE_TYPE" <> \'RURAL\'')
        p.set_view_scope("failing")
        settle(p)
        rural = sum(1 for f in layer.getFeatures() if f["LAND_USE_TYPE"] == "RURAL")
        self.assertEqual(len(p.model.fids), rural)
        self.assertIn(f"(0 hard, {rural} soft)", p.scope_label.fullText())

    def test_14_expression_filter_and_stored(self):
        p = self.panel
        total = self.layer.featureCount()
        p.set_expression_filter('"FL_1_PCT_MAX" > 80')
        settle(p)
        a = len(p.model.fids)
        self.assertLess(a, total)
        self.assertIn("Expression", p.scope_label.toolTip())
        self.assertTrue(p.expr_chip.isVisibleTo(p))
        p.set_expression_filter('"LAND_USE_TYPE" = \'RESIDENTIAL\'', "and")
        settle(p)
        self.assertLessEqual(len(p.model.fids), a)
        p.set_expression_filter('"FL_1_PCT_MAX" < 10', "or")
        settle(p)
        manager = self.layer.storedExpressionManager()
        dlg = D.StoredExpressionsDialog(p)
        before = len(manager.storedExpressions())
        manager.addStoredExpression("high", '"FL_1_PCT_MAX" > 90')
        dlg._fill()
        self.assertEqual(dlg.list.topLevelItemCount(), before + 1)
        dlg.list.setCurrentItem(dlg.list.topLevelItem(dlg.list.topLevelItemCount() - 1))
        dlg._apply()
        p.set_expression_filter(dlg.applied)
        settle(p)
        self.assertEqual(p.expr_filter, '"FL_1_PCT_MAX" > 90')
        p.clear_filters()
        settle(p)
        self.assertIsNone(p.expr_filter)
        self.assertEqual(len(p.model.fids), total)

    def test_15_select_menu_sort_and_selected_top(self):
        p = self.panel
        layer = self.layer
        p.select_layer_all()
        self.assertEqual(layer.selectedFeatureCount(), layer.featureCount())
        p.deselect_all()
        layer.selectByIds(p.model.fids[:2])
        p.invert_layer_selection()
        self.assertEqual(layer.selectedFeatureCount(), layer.featureCount() - 2)
        p.deselect_all()
        shown = []
        original = QMenu.exec
        QMenu.exec = lambda menu, *a: shown.extend(
            act.text() for act in menu.actions() if not act.isSeparator())
        try:
            p.select_menu.aboutToShow.emit()
            for act in p.select_menu.actions():
                shown.append(act.text())
        finally:
            QMenu.exec = original
        for text in ("Select all shown records", "Select all available layer records",
                     "Invert layer selection", "Select by expression…",
                     "Select by value / form…", "Pan to selection"):
            self.assertIn(text, shown)
        # sort by expression (descending)
        p.sort_expr = ('"FL_1_PCT_MAX" * -1', False)
        p.refresh(sort_only=True)
        settle(p)
        values = [p.model.raw_value(r, self.fl) for r in range(5)]
        self.assertEqual(values, sorted(values, reverse=True))
        # selected rows to the top, filter membership unchanged
        n = len(p.model.fids)
        last = p.model.fids[-1]
        layer.selectByIds([last])
        p.set_selected_top(True)
        self.assertEqual(p.model.fids[0], last)
        self.assertEqual(len(p.model.fids), n)

    def test_16_conditional_formatting(self):
        p = self.panel
        style = QgsConditionalStyle('"FL_1_PCT_MAX" > 50')
        style.setBackgroundColor(QColor("#123456"))
        self.layer.conditionalStyles().setRowStyles([style])
        H.settle(0.1)
        rows = [r for r in range(min(40, len(p.model.fids)))]
        hits = [r for r in rows if (p.model.raw_value(r, self.fl) or 0) > 50]
        misses = [r for r in rows if (p.model.raw_value(r, self.fl) or 0) <= 50]
        bg = p.model.data(p.model.index(hits[0], self.lu), Qt.ItemDataRole.BackgroundRole)
        self.assertEqual(QColor(bg).name(), "#123456")
        self.assertIsNone(p.model.data(p.model.index(misses[0], self.lu),
                                       Qt.ItemDataRole.BackgroundRole))
        self.layer.conditionalStyles().setRowStyles([])

    def test_17_keyboard_ownership(self):
        p = self.panel
        self.start()
        view = p.view
        # a click focuses the cell without changing the feature selection
        self.layer.removeSelection()
        rect = view.visualRect(p.model.index(2, self.fl))
        from qgis.PyQt.QtTest import QTest
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.assertEqual(self.layer.selectedFeatureCount(), 0)
        self.assertEqual(view.currentIndex().row(), 2)
        p.model.setData(p.model.index(2, self.fl), 3.5)
        fid = p.model.fid(2)
        # Ctrl+Z belongs to the Explorer outside an editor (not QGIS)
        override = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_Z,
                             Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(view.event(override))
        self.assertTrue(override.isAccepted())
        QTest.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertNotEqual(self.layer.getFeature(fid)["FL_1_PCT_MAX"], 3.5)
        # Ctrl+S saves the layer while editing (accepted at the panel)
        p.model.setData(p.model.index(2, self.fl), 4.5)
        save = QKeyEvent(QEvent.Type.ShortcutOverride, Qt.Key.Key_S,
                         Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(p.event(save))
        QTest.keyClick(p, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
        self.assertFalse(self.layer.isModified())

    def test_18_close_prompt_keeps_buffer(self):
        p = self.panel
        self.start()
        p.model.setData(p.model.index(0, self.fl), 2.5)
        host = self.plugin.explorers[self.layer.id()]
        ASK.answers = ["cancel"]
        host._window.close()
        H.settle(0.2)
        self.assertIn(self.layer.id(), self.plugin.explorers)
        ASK.answers = ["keep"]
        host._window.close()
        H.settle(0.3)
        self.assertNotIn(self.layer.id(), self.plugin.explorers)
        self.assertTrue(self.layer.isEditable())
        self.assertTrue(self.layer.isModified())             # nothing discarded

    def test_19_map_filter_frozen_while_editing(self):
        p = self.panel
        cell = p.header.cells[self.lu]
        cell.set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "RESIDENTIAL"))
        p.refresh()
        settle(p)
        p.query_btn.setChecked(True)
        settle(p)
        frozen = self.layer.subsetString()
        self.assertIn("RESIDENTIAL", frozen)
        self.start()
        self.assertFalse(p.query_btn.isEnabled())
        self.assertIn("frozen", p.query_btn.toolTip())
        cell.set_filter(FL.ColumnFilter("LAND_USE_TYPE", F.TEXT, "eq", "INDUSTRIAL"))
        p.refresh()
        settle(p)
        self.assertEqual(self.layer.subsetString(), frozen)    # unchanged
        self.assertEqual(len(p.model.fids), 0)                 # only map records
        p.edit_bar.edit_btn.click()                            # stop (no changes)
        settle(p)
        self.assertIn("INDUSTRIAL", self.layer.subsetString())
        self.assertTrue(p.query_btn.isEnabled())
        p.query_btn.setChecked(False)

    def test_20_more_and_header_menus(self):
        p = self.panel
        self.start()
        shown = []
        original = QMenu.exec
        QMenu.exec = lambda menu, *a: shown.extend(
            act.text() for act in menu.actions() if not act.isSeparator())
        try:
            p._fill_more_menu()
            texts = [a.text() for a in p.edit_bar.more_menu.actions()]
            p._header_menu(QPoint(p.header.sectionViewportPosition(self.fl) + 5, 40))
        finally:
            QMenu.exec = original
        for text in ("Open feature form…", "Layer feature actions", "Copy selected rows as text",
                     "Copy cell value", "Set cell to NULL", "Review unsaved changes…",
                     "Layer field properties…", "Open native attribute table", "Help"):
            self.assertIn(text, texts)
        for text in ("Sort FL_1_PCT_MAX ascending", "Autosize this column",
                     "Set column width…", "Calculate FL_1_PCT_MAX…"):
            self.assertIn(text, shown)

    def test_22_cell_editor_widgets(self):
        from qgis.PyQt.QtTest import QTest
        from qgis.PyQt.QtWidgets import QAbstractItemView
        p, view, layer = self.panel, self.panel.view, self.layer
        layer.setFieldConstraint(self.lu, QgsFieldConstraints.Constraint.ConstraintNotNull,
                                 QgsFieldConstraints.ConstraintStrength.ConstraintStrengthHard)
        self.start()
        delegate = view.itemDelegate()
        idx = p.model.index(1, self.lu)
        fid = p.model.fid(1)
        before = layer.getFeature(fid)["LAND_USE_TYPE"]
        view.setCurrentIndex(idx)
        view.edit(idx)
        H.settle(0.05)
        editor = view.indexWidget(idx)
        self.assertIsNotNone(editor)                 # the field's QGIS widget
        wrapper = delegate._wrappers[sip.unwrapinstance(editor)][0]
        self.assertEqual(wrapper.field().name(), "LAND_USE_TYPE")
        wrapper.setValues(None, [])
        QTest.keyClick(editor, Qt.Key.Key_Return)    # hard constraint: stays open
        H.settle(0.05)
        self.assertEqual(view.state(), QAbstractItemView.State.EditingState)
        self.assertEqual(layer.getFeature(fid)["LAND_USE_TYPE"], before)
        wrapper.setValues("TYPED", [])
        view.commit_editor()
        H.settle(0.1)
        self.assertNotEqual(view.state(), QAbstractItemView.State.EditingState)
        self.assertEqual(layer.getFeature(fid)["LAND_USE_TYPE"], "TYPED")
        self.assertFalse(delegate._wrappers)         # wrapper released with the editor
        # Escape cancels only this cell
        view.edit(p.model.index(2, self.lu))
        H.settle(0.05)
        editor = view.indexWidget(p.model.index(2, self.lu))
        delegate._wrappers[sip.unwrapinstance(editor)][0].setValues("CANCELLED", [])
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        H.settle(0.05)
        self.assertNotEqual(layer.getFeature(p.model.fid(2))["LAND_USE_TYPE"], "CANCELLED")
        self.assertEqual(layer.getFeature(fid)["LAND_USE_TYPE"], "TYPED")

    # ---- the Field calculator evaluates like QGIS's own -------------------
    @staticmethod
    def _pick_type(dlg, names):
        for i in range(dlg.type_combo.count()):
            if dlg.type_combo.itemData(i)[1].upper() in names:
                dlg.type_combo.setCurrentIndex(i)
                return
        raise AssertionError(f"no {names} type")

    def test_23_calculator_measures_like_qgis(self):
        from qgis.core import QgsDistanceArea, QgsProject
        from attribute_table_studio.ui import explorer_ops as OPS
        project = QgsProject.instance()
        saved = (project.crs(), project.ellipsoid(), project.areaUnits(),
                 project.distanceUnits())
        project.setCrs(self.layer.crs())     # QGIS has no ellipsoid without a project CRS
        project.setEllipsoid("EPSG:7030")
        project.setAreaUnits(Qgis.AreaUnit.Hectares)
        project.setDistanceUnits(Qgis.DistanceUnit.Kilometers)
        try:
            p, layer = self.panel, self.layer
            self.start()
            ids = sorted(p.model.fids[:2])
            layer.selectByIds(ids)
            dlg = D.FieldCalculatorDialog(p)
            dlg.mode_new.setChecked(True)
            dlg._on_mode()
            dlg.name_edit.setText("AREA_HA")
            self._pick_type(dlg, ("REAL", "DOUBLE"))
            dlg.builder.setExpressionText("$area")
            dlg.target_combo.setCurrentIndex(dlg.target_combo.findData("selected_layer"))
            dlg._update_preview()
            dlg._apply()
            da = QgsDistanceArea()
            da.setSourceCrs(layer.crs(), project.transformContext())
            da.setEllipsoid("EPSG:7030")
            for fid in ids:
                geom = layer.getFeature(fid).geometry()
                got = layer.getFeature(fid)["AREA_HA"]
                expected = da.convertAreaMeasurement(da.measureArea(geom),
                                                     Qgis.AreaUnit.Hectares)
                self.assertAlmostEqual(got, expected, places=9)
                self.assertNotAlmostEqual(got, geom.area() / 10000, places=7)  # not planar
            # the Quick update preview uses the same set-up ($perimeter in km)
            idx = layer.fields().indexFromName("AREA_HA")
            for fid, _old, new in OPS.evaluate_preview(layer, idx, "$perimeter", ids):
                geom = layer.getFeature(fid).geometry()
                expected = da.convertLengthMeasurement(da.measurePerimeter(geom),
                                                       Qgis.DistanceUnit.Kilometers)
                self.assertAlmostEqual(new, expected, places=9)
        finally:
            project.setCrs(saved[0])
            project.setEllipsoid(saved[1])
            project.setAreaUnits(saved[2])
            project.setDistanceUnits(saved[3])

    def test_24_calculator_row_number(self):
        from attribute_table_studio.ui import explorer_ops as OPS
        p, layer = self.panel, self.layer
        self.start()
        ids = sorted(p.model.fids[:3])
        layer.selectByIds(ids)
        dlg = D.FieldCalculatorDialog(p, "ZONE_CODE")
        self.assertIn("row_number", dlg.builder.expressionContext().highlightedVariables())
        dlg.builder.setExpressionText("@row_number * 10")
        dlg.target_combo.setCurrentIndex(dlg.target_combo.findData("selected_layer"))
        dlg._update_preview()
        self.assertIn("Output preview: 10", dlg.preview.text())
        dlg._apply()
        self.assertEqual([layer.getFeature(f)["ZONE_CODE"] for f in ids], [10, 20, 30])
        zone = layer.fields().indexFromName("ZONE_CODE")
        preview = OPS.evaluate_preview(layer, zone, "@row_number", ids)
        self.assertEqual([new for _f, _o, new in preview], [1, 2, 3])

    def test_25_calculator_recent_shared_with_qgis(self):
        from qgis.core import QgsSettings
        key = "expressions/recent/fieldcalc"       # read by QGIS's own calculator
        settings_ = QgsSettings()
        saved = settings_.value(key)
        try:
            settings_.remove(key)
            p, layer = self.panel, self.layer
            self.start()
            layer.selectByIds(p.model.fids[:1])
            dlg = D.FieldCalculatorDialog(p, "FL_1_PCT_MAX")
            dlg.builder.setExpressionText('"FL_1_PCT_MAX" * 3')
            dlg.target_combo.setCurrentIndex(dlg.target_combo.findData("selected_layer"))
            dlg._apply()
            recent = QgsSettings().value(key) or []
            recent = [recent] if isinstance(recent, str) else list(recent)
            self.assertEqual(recent[0], '"FL_1_PCT_MAX" * 3')
            # and the next calculator lists it under Recent
            texts = []
            model = D.FieldCalculatorDialog(p).builder.expressionTree().model()

            def walk(parent):
                for r in range(model.rowCount(parent)):
                    i = model.index(r, 0, parent)
                    texts.append(model.data(i))
                    walk(i)
            walk(QModelIndex())
            self.assertIn('"FL_1_PCT_MAX" * 3', texts)
        finally:
            if saved is None:
                QgsSettings().remove(key)
            else:
                QgsSettings().setValue(key, saved)

    def test_26_open_qgis_field_calculator(self):
        p = self.panel
        self.iface.setActiveLayer(None)
        p._fill_more_menu()
        act = next(a for a in p.edit_bar.more_menu.actions()
                   if a.text() == "Open QGIS field calculator…")
        self.assertTrue(act.isEnabled())
        self.assertIn("ignores the Explorer's filters unless the map filter is on",
                      act.toolTip())
        self.assertTrue(p.edit_bar.more_menu.toolTipsVisible())
        act.trigger()
        self.assertIs(self.iface.activeLayer(), self.layer)    # QGIS uses the active layer
        self.assertEqual(self.iface.calls[-1], ("field_calculator", self.layer.id()))

    def test_21_replacement_keys(self):
        from qgis.gui import QgsGui
        from attribute_table_studio.core.compat import QAction
        mgr = QgsGui.shortcutsManager()
        native = QAction("Open Attribute Table", self.iface.mainWindow())
        native.setObjectName("mActionOpenTableTest")
        mgr.registerAction(native, "F6")
        mgr.setKeySequence(native, "F6")
        self.assertIs(mgr.actionForSequence(QKeySequence("F6")), native)
        settings.set_value("explorer/replaceTableKeys", True)
        try:
            self.plugin.apply_table_keys()
            self.assertTrue(native.shortcut().isEmpty())
            keys = {a.shortcut().toString() for a in self.plugin.table_keys}
            self.assertEqual(keys, {"F6", "Shift+F6", "Ctrl+F6"})
            self.plugin.table_keys[1].trigger()               # Shift+F6: selected
            panel = self.plugin.explorer_for(self.layer)
            self.assertEqual(panel.view_scope, "selected")
        finally:
            settings.set_value("explorer/replaceTableKeys", False)
            self.plugin.apply_table_keys()
        self.assertEqual(native.shortcut().toString(), "F6")   # given back
        self.assertEqual(self.plugin.table_keys, [])
        mgr.unregisterAction(native)


if __name__ == "__main__":
    H.run_tests(APP)
