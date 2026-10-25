# -*- coding: utf-8 -*-
"""Qt5 / Qt6 and QGIS 3.40+ / 4.x compatibility names used across the suite."""

from qgis.PyQt.QtCore import QT_VERSION_STR
from qgis.core import Qgis, QgsTask

try:  # Qt6
    from qgis.PyQt.QtGui import QAction, QActionGroup, QShortcut
except ImportError:  # Qt5
    from qgis.PyQt.QtWidgets import QAction, QActionGroup, QShortcut

__all__ = ["QAction", "QActionGroup", "QShortcut"]   # re-exported for Qt5 / Qt6

QT6 = int(QT_VERSION_STR.split(".")[0]) >= 6

VECTOR_LAYER_FILTER = Qgis.LayerFilter.VectorLayer
LAYER_TYPE_VECTOR = Qgis.LayerType.Vector

GEOM_POINT = Qgis.GeometryType.Point
GEOM_LINE = Qgis.GeometryType.Line
GEOM_POLYGON = Qgis.GeometryType.Polygon
GEOM_NULL = Qgis.GeometryType.Null

NO_GEOMETRY = Qgis.FeatureRequestFlag.NoGeometry

SELECT_SET = Qgis.SelectBehavior.SetSelection
SELECT_ADD = Qgis.SelectBehavior.AddToSelection
SELECT_REMOVE = Qgis.SelectBehavior.RemoveFromSelection

MSG_INFO = Qgis.MessageLevel.Info
MSG_WARNING = Qgis.MessageLevel.Warning
MSG_CRITICAL = Qgis.MessageLevel.Critical
MSG_SUCCESS = Qgis.MessageLevel.Success

# quick background jobs: cancellable, no prompt, kept out of the task list
TASK_QUIET = (QgsTask.Flag.CanCancel | QgsTask.Flag.CancelWithoutPrompt
              | QgsTask.Flag.Hidden)
# long jobs the user may want to watch in the QGIS task manager
TASK_VISIBLE = QgsTask.Flag.CanCancel | QgsTask.Flag.CancelWithoutPrompt


def enum_int(value):
    """int() for Qt5 sip enums and Qt6 Python enums alike."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(value.value)
