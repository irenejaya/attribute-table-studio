# -*- coding: utf-8 -*-
"""GeoTable Studio help (Plugins > GeoTable Studio > Help)."""

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QTabWidget, QTextBrowser, QVBoxLayout,
)

from . import icons
from .theme import Metrics, Theme, stylesheet

EXPLORER_HELP = """
<h3>Attribute Explorer</h3>
<p>A read-only table of the active layer with statistics attached above
every column. Open it from the toolbar, the plugin menu, a layer's
right-click menu (<i>Open in Attribute Explorer</i>) or the shortcut
(default <b>Ctrl+Alt+T</b> when it is free). Each layer has its own
explorer; opening it again brings that window to the front.</p>
<h4>Filters</h4>
<ul>
<li>Every column has a filter below its name. Click the symbol on the left
to choose the operator: text <i>contains, equals, starts with, is one of,
is NULL, is not NULL</i>; numbers <i>=, ≠, &lt;, ≤, &gt;, ≥, between, is one
of</i>; dates <i>on, before, after, between</i> (with a calendar);
booleans <i>true, false, NULL</i>.</li>
<li>Filters in different columns combine with <b>AND</b>; ticked values in
one column combine with <b>OR</b> (use ▾ to pick values).</li>
<li>Typing applies after a short pause; <b>Enter</b> applies at once. An
invalid value is outlined red and the last valid result stays on screen.</li>
<li><b>Clear filters</b> removes every filter; <i>Clear this filter</i> in an
operator menu clears one column.</li>
</ul>
<h4>Statistics</h4>
<p>Profiles describe exactly the records in scope (all filtered rows, not
only the visible screen): missing (NULL) and distinct counts, top
categories with shares of non-NULL values, histograms with min / max,
earliest / latest dates and true / false counts. Click a profile for the
full statistics (sum, mean, median, standard deviation, frequency list).
NULL is missing; zero and empty text are values. <b>Summary: On / Off</b>
collapses the strip. Statistics are exact and calculated in the
background - visible columns first.</p>
<h4>Selection and map</h4>
<p>Click, Ctrl+click, Shift+click or drag rows (or tick the boxes) to select
features; the table and the map selection stay in sync. Filtering never
changes the map selection. <b>Selected only</b> limits the table and the
statistics to the selected features. <b>Zoom to selection</b> zooms the map;
double-click a row to zoom to one feature.</p>
<h4>Columns, sorting, export</h4>
<p>Click a column name to sort (again for descending, a third time to
reset). Drag names to reorder, drag edges to resize, and use
<b>Columns</b> to show, hide and reorder; the layout is saved with the
layer in the project. <b>Export</b> writes the filtered records to Excel
or CSV with the scope recorded; <b>Ctrl+C</b> copies the selected rows.
<b>Open Pivot</b> passes the filtered records to the Pivot Table.</p>
"""

PIVOT_HELP = """
<h3>Pivot Table</h3>
<p>Grouped summaries and cross tabs in a separate window.</p>
<ol>
<li>Choose the <b>Layer</b> and the input with <b>Change scope</b>: all
available records, the records filtered in Attribute Explorer, or the
selected records.</li>
<li>Pick one or more <b>Rows</b> fields. Add <b>Columns</b> to turn the
summary into a cross tab.</li>
<li>Add <b>Values</b>: a field, an aggregation (count records, count
non-NULL, count distinct, sum, mean, median, min, max, population standard
deviation) and an optional label. The same field may be used twice.</li>
<li>Optionally add <b>Filters</b>, then press <b>Build pivot</b>.</li>
</ol>
<h4>Rules</h4>
<ul>
<li>Numeric measures skip NULL and keep zero; a group with no valid value
shows NULL. A missing cross-tab combination shows NULL (Count records: 0).</li>
<li>Totals are calculated from the contributing records, never by
averaging the group results.</li>
<li>NULL groups are shown separately from the text "NULL".</li>
</ul>
<h4>Input and refresh</h4>
<p>The records of the scope are captured when the input is set; later
edits, selection or Explorer filter changes mark the result stale - use
<b>Refresh input</b> then <b>Build pivot</b>. Double-click a result to open
its contributing records in Attribute Explorer (paused while stale).</p>
<h4>Layouts and export</h4>
<p><b>Save layout</b> stores the recipe (fields, values, filters - never the
results) in the project or a file; <b>Load layout</b> restores it.
<b>Export</b> writes Excel, CSV or a non-spatial GeoPackage table with the
layout and scope recorded.</p>
"""

COMPARE_HELP = """
<h3>Geometry and Table Compare</h3>
<h4>How to use</h4>
<ol>
<li>Select the <b>OLD</b> (baseline) and <b>NEW</b> (updated) layers.</li>
<li>Choose a <b>Join field</b> - a unique ID (e.g. asset_id) that links
matching rows in both layers.</li>
<li>Optionally enable <b>geometry comparison</b>.</li>
<li>Press <b>Validate Data</b> to check for problems, then
<b>Compare OLD → NEW</b>.</li>
</ol>
<h4>Reading the results</h4>
<ul>
<li><b>Added</b> - only in NEW &nbsp; <b>Deleted</b> - only in OLD</li>
<li><b>Modified</b> - in both, but a compared value differs. Highlighted
cells show <i>old → new</i> (blank = empty).</li>
<li><b>Unchanged</b> - identical in both.</li>
</ul>
<h4>Geometry &amp; review points</h4>
<p>With <b>Compare geometry</b> ticked, multipart input is detected
automatically. <b>Method → Singlepart</b> splits every feature into its
parts and matches them one by one; <b>Multipart</b> compares each feature as
a single shape. A <b>review point</b> is created at every place that
changed (<b>Review points</b>: temporary points, a shapefile or a
geopackage) so you can visit it. <b>Show on map</b> adds the added (green)
and removed (red) geometry; double-click a row to zoom to it.</p>
<h4>Review &amp; export</h4>
<p>Use <b>Accept / Reject / Reset</b> to record a decision on Added and
Modified rows. Your layers are <b>never edited</b>; decisions are stored in
the export (a Rejected row exports its OLD value, otherwise the NEW
value).</p>
"""

GENERAL_HELP = """
<h3>GeoTable Studio</h3>
<p>Three tools in one toolbar: <b>Attribute Explorer</b>, <b>Pivot Table</b>
and <b>Geometry and Table Compare</b>.</p>
<h4>Windows and docking</h4>
<p>Each tool opens in its own window (with its own taskbar entry) and
remembers its size and position. Press the <b>Dock</b> button at the top
right of a tool to dock it into the QGIS main window, and again to undock.
To open a tool docked every time, tick it in <b>Plugins › GeoTable Studio
› Settings</b>.</p>
<h4>Shortcuts</h4>
<p>All three tools are listed in <b>Settings › Keyboard Shortcuts</b> under
their names. Attribute Explorer proposes <b>Ctrl+Alt+T</b> when no other
command uses it; the normal attribute table shortcut (F6) is not
changed.</p>
<h4>Appearance</h4>
<p>The tools follow the QGIS light or dark theme automatically.</p>
"""


class HelpDialog(QDialog):
    def __init__(self, parent=None, tab=0):
        super().__init__(parent)
        self.setWindowTitle("GeoTable Studio — Help")
        self.setWindowIcon(icons.native(icons.HELP))
        self.resize(720, 640)
        theme, metrics = Theme(), Metrics(self.font())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(metrics.margin, metrics.margin,
                               metrics.margin, metrics.margin)
        tabs = QTabWidget()
        tabs.setIconSize(QSize(metrics.icon, metrics.icon))
        for title, icon, html in (
                ("GeoTable Studio", icons.native(icons.HELP), GENERAL_HELP),
                ("Attribute Explorer", icons.tool(icons.EXPLORER), EXPLORER_HELP),
                ("Pivot Table", icons.tool(icons.PIVOT), PIVOT_HELP),
                ("Geometry and Table Compare", icons.tool(icons.COMPARE),
                 COMPARE_HELP)):
            view = QTextBrowser()
            view.setOpenExternalLinks(True)
            view.setHtml(html)
            tabs.addTab(view, icon, title)
        tabs.setCurrentIndex(tab)
        lay.addWidget(tabs, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        lay.addWidget(box)
        self.setStyleSheet(stylesheet(theme, metrics))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
