# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""Attribute Table Studio help (Plugins > Attribute Table Studio > Help)."""

from qgis.PyQt.QtCore import QSize, Qt
from qgis.PyQt.QtWidgets import (
    QDialog, QDialogButtonBox, QTabWidget, QTextBrowser, QVBoxLayout,
)

from . import icons
from .theme import Metrics, Theme, stylesheet

EXPLORER_HELP = """
<h3>Attribute Explorer</h3>
<p>A table of the active layer with statistics attached above every
column. It opens read-only; <b>Edit</b> starts editing the layer. Open it
from the toolbar, the plugin menu, a layer's right-click menu (<i>Open in
Attribute Explorer</i>) or the shortcut (default <b>Ctrl+Alt+T</b> when it
is free). Each layer has its own explorer; opening it again brings that
window to the front.</p>
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
<li><b>Clear filters</b> (the funnel with a cross, top right) removes every
filter; <i>Clear this filter</i> in an operator menu or the ✕ in a filter box
clears one column. In the value list (▾), click <i>Value</i> or
<i>Records</i> to sort it.</li>
</ul>
<h4>Statistics</h4>
<p>Profiles describe exactly the records in scope (all filtered rows, not
only the visible screen): missing (NULL) and distinct counts, top
categories with shares of non-NULL values, histograms with min / max,
earliest / latest dates and true / false counts. Hover a bar (histogram,
category or true / false) for its count; hover elsewhere on the card for a
brief summary. Click a profile for the full statistics (sum, mean, median,
standard deviation, frequency list): select values or rows and press
<b>Ctrl+C</b>, or use <b>Copy ▾</b> (summary, distribution or everything, as
tab-separated text that pastes into Excel) and <b>Export ▾</b> (an Excel
workbook with Summary and Distribution sheets, or either as CSV). Shares are
exported as numbers; the workbook and <i>Everything</i> also record the
layer, field, active filters and record count.
NULL is missing; zero and empty text are values. <b>Summary: On / Off</b>
collapses the strip. Statistics are exact and calculated in the
background - visible columns first.</p>
<h4>Selection and map</h4>
<p>Select features with the tick boxes / row numbers (a click selects only
that feature, Ctrl+click adds or removes it, Shift+click or a drag selects a
range - also while editing); the table and the map selection stay in sync.
A click on a cell only frames it, as while editing: the arrow keys move the
frame and <b>Ctrl+C</b> copies the cell's value (<b>Ctrl+Shift+C</b> copies
the selected rows). Selecting features
on the map scrolls the table to the first of them. Filtering never changes
the map selection. <b>Selected only</b> limits the table and the
statistics to the selected features. <b>Zoom to selection</b> zooms the map;
double-click a row to zoom to one feature. The icon left of <b>Select ▾</b>
deselects the features of this layer. <b>Select ▾</b> adds <i>Select All
Features</i> and <i>Invert Selection</i> (each also for the <i>Whole
Layer</i> while the table is filtered), <i>Deselect Features from All
Layers</i>, <i>Select Features by Expression…</i>, <i>Select Features by
Value…</i> and pan / zoom to the selection; its first line says how many
selected features are outside the current view. Menu entries that open a
dialog end with "…".</p>
<h4>View</h4>
<ul>
<li><b>View ▾</b> switches between <b>Table View</b> and <b>Form View</b>
(the layer's configured form with a record list; edits in the form are
written when you move to another record or back to the table).</li>
<li>Scope, named as in the QGIS attribute table: <i>Show All Features</i>,
<i>Show Selected Features</i> (same as <b>Selected only</b>), <i>Show
Features Visible on Map</i> (follows the map extent), <i>Show Features with
Failing Constraints</i> (hard and soft counts in the scope line) and
<i>Show Edited and New Features</i> (unsaved changes). Column filters
always apply on top.</li>
<li><i>Advanced Filter (Expression)</i> replaces it or combines it with AND /
OR; <i>Stored Filter Expressions</i> reuses the layer's saved ones;
<i>Filter by Form</i> uses the layer's form in search mode. The expression shows as a
chip in the scope line; <b>Clear filters</b> clears it too.</li>
<li><i>Case-Sensitive Text Filters</i>, <i>Move Selection to Top</i> (keeps
the filtered records, only moves the selection up), <i>Sort by
Expression</i>, <i>Conditional Formatting</i> (the layer's own rules,
painted in the table) and <i>Autosize All Columns</i>. Right-click a column name for sorting,
widths, hiding, the field calculator and field properties.</li>
</ul>
<h4>Editing</h4>
<p>The second row holds the <i>Statistics for</i> line on the left and the
editing commands on the right. <b>Edit</b> starts or stops editing the
layer. The edit session is the layer's own: the map, the native attribute
table and every other QGIS tool share it, and edits made there show up here.
The right of the status bar says whether the layer is read only or being
edited (orange) and how many unsaved changes it has (red when a save
failed). Buttons that cannot be used say why in their tooltip.</p>
<ul>
<li>While editing, as while browsing, a click only frames a cell (it does
not change the selection); double-click, F2 or typing opens the field's configured QGIS
widget. <b>Enter</b> or <b>Tab</b> write the value, <b>Escape</b> cancels
that cell. A value breaking a hard constraint stays in the editor with the
reason; a soft constraint writes it and warns. Virtual, joined and read-only
fields cannot be edited.</li>
<li>Unsaved cells have an amber corner. <b>Save edits</b> (or
<b>Ctrl+S</b>) saves every unsaved change of the layer - including records
hidden by filters and edits made elsewhere - and editing stays on. If saving
fails the edits are kept and the provider's message is shown.
<b>Discard</b> asks first. <b>Undo</b> / <b>Redo</b> (<b>Ctrl+Z</b> /
<b>Ctrl+Y</b>) use the layer's undo stack.</li>
<li><b>Add</b> adds a record in the table (the arrow offers the form);
<b>Delete</b> deletes the selected features and tells you when some of them
are not shown. <b>Cut</b>, <b>Copy</b> and <b>Paste</b> use the QGIS feature
clipboard. A status link <i>Show edited and new</i> switches to that
scope.</li>
<li><b>Field calculator</b> opens QGIS's own Field Calculator for this
layer, exactly as the native attribute table does: update a field (or the
geometry), or create a new or virtual field, for the whole layer or only the
selected features. It starts editing when needed.</li>
<li><b>Quick update</b> is the native attribute table's field calculation
bar: pick a field and an expression, then <b>Update All</b> - or
<b>Update Filtered</b> while the table is filtered (column, expression or
form filters, or a scope such as selected / visible on map) - or
<b>Update Selected</b> (every selected feature, shown or not).
<b>Preview</b> shows the first values without writing. Values are calculated
and stored as QGIS does it (<i>$area</i> / <i>$length</i> on the project's
ellipsoid, <i>@row_number</i>, rounding to the field precision, values that
cannot be stored become NULL).</li>
<li>What can be edited is what the layer provides: with the <b>map
filter</b> (query) off, every record - even <b>Update All</b> and the Field
calculator reach records the table filters hide; with the map filter on,
only the records the map shows.</li>
<li><b>Multi edit</b> opens the form for the selected features: only fields
you change are written. Field calculator, Quick update and Multi edit are
each one undo step.</li>
<li><b>New field</b> / <b>Delete field</b> change the layer's schema (stored
or virtual fields); <b>Reload</b> re-reads the data source. The ☰ menu holds
the feature form, layer actions, copy as text, set NULL, <i>Review Unsaved
Changes</i>, field properties and the native attribute table.</li>
<li>Closing the explorer while the layer has unsaved edits asks: save and
close, close and keep the layer's edits, or cancel. Nothing is discarded
without asking.</li>
<li>While editing, the <b>map filter</b> is frozen: the table only shows the
records the map shows. Stop editing to change the map filter or turn it
off.</li>
</ul>
<p>Settings can open the explorer with <b>F6</b> (all records),
<b>Shift+F6</b> (selected) and <b>Ctrl+F6</b> (visible on map) instead of the
native attribute table; turning it off gives the keys back.</p>
<h4>Map filter</h4>
<p>The funnel button next to <b>Dock</b> (the mark QGIS shows beside a filtered
layer) turns the <b>map filter</b> on: the map (and every other tool) then
shows only the records of the table filters. Change a filter and the map
follows. The map filter is temporary: click the button again, close the
explorer or switch it to another layer and every feature is shown again,
while the table keeps its filters until <b>Clear filters</b>. A filter the
layer already has (Layer Properties &gt; Source &gt; Query Builder) is kept
underneath and comes back untouched; saving the project while the map
filter is on saves only that filter. If the Query Builder filter is changed
while the map filter is on, that change wins and the map filter goes off.
While the layer is being edited the map filter is frozen as it is (QGIS
does not allow filter changes during editing); stop editing to change it or
turn it off. An explorer closed during editing gives the layer its own
filter back as soon as editing stops.</p>
<h4>Columns, sorting, export</h4>
<p><b>Go to field</b> in the top row: type part of a field name and pick
it (or press Enter for the first match) to scroll that column to the middle
of the table; its heading is highlighted for a moment, and a hidden column
is shown again. The list shows each field's type icon, name and QGIS type.</p>
<p>Click a column name to sort (again for descending, a third time to
reset). Drag names to reorder, drag edges to resize, and use the
<b>Columns</b> icon to show, hide and reorder; the layout is saved with the
layer in the project. <b>Export</b> writes the filtered records to Excel
or CSV with the scope recorded; <b>Ctrl+Shift+C</b> copies the selected
rows (<b>Ctrl+C</b> the framed cell).
<b>Pivot Table</b> passes the filtered records to the Pivot Table.</p>
"""

PIVOT_HELP = """
<h3>Pivot Table</h3>
<p>A pivot table <b>groups records</b> by the values of one or more fields
and <b>summarises</b> each group - for example the number of parcels and the
average land value for every land use, or for every land use and owner
type.</p>
<h4>Build one in five steps</h4>
<ol>
<li><b>Layer and input.</b> Pick the <b>Layer</b> at the top left, then
<b>Change scope</b> to choose which records feed the pivot: <i>All available
records</i>, the <i>Explorer filtered records</i> (open Attribute Explorer
and filter first) or the <i>Selected records</i>. The line next to the layer
says which input is in use.</li>
<li><b>Rows</b> (on the left, under <i>Build your table</i>): choose the field
to group by - one result row per value. <b>Add row</b> groups by a second
field inside the first (e.g. land use, then owner type).</li>
<li><b>Columns</b> (optional): choose a field to spread across the top. The
result becomes a cross tab - one column per value of that field. Leave it at
<i>None</i> for a simple grouped list.</li>
<li><b>Values</b>: what to calculate for each group. Pick a field and an
aggregation: <i>Count records</i> (no field needed), <i>Count non-NULL</i>,
<i>Count distinct</i>, or for numbers <i>Sum, Mean, Median, Min, Max</i> and
<i>Std dev (population)</i>. Type a label if you like. <b>Add value</b> adds
another measure; the same field may be used twice (e.g. its mean and its
max).</li>
<li>Optionally <b>Add filter</b> to use only part of the input (all filters
must match), then press <b>Build Pivot</b>.</li>
</ol>
<p><b>Example:</b> Rows = <i>LAND_USE</i>, Columns = <i>OWNER_TYPE</i>,
Values = <i>Count records</i> and <i>LAND_VALUE - Mean</i> gives, for every
land use, the number of parcels and their mean land value for each owner
type, with totals.</p>
<h4>Reading the result</h4>
<ul>
<li>The header shows how many records were used. <i>Show record count</i>
and <i>Show grand total</i> (under Options) add those rows and columns.</li>
<li><b>Double-click</b> a result to open the records behind it in Attribute
Explorer.</li>
</ul>
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
<b>Refresh input</b> then <b>Build Pivot</b>. Double-click a result to open
its contributing records in Attribute Explorer (paused while stale).</p>
<h4>Layouts and export</h4>
<p><b>Save layout</b> stores the recipe (fields, values, filters - never the
results) in the project or a file; <b>Load layout</b> restores it.
<b>Export</b> writes Excel, CSV or a non-spatial GeoPackage table with the
layout and scope recorded.</p>
"""

COMPARE_HELP = """
<h3>Geometry and Table Compare</h3>
<p><b>What it is for:</b> tracking what changed between two versions of the
same dataset - an <b>OLD</b> baseline (for example the last issued layer)
and a <b>NEW</b> update (for example a revised delivery). Every feature is
matched by a unique ID and reported as <b>Added</b>, <b>Deleted</b>,
<b>Modified</b> or <b>Unchanged</b>, so you can check an update value by
value, decide which changes to accept, and keep a record of that review.
Your layers are <b>never edited</b>.</p>
<h4>How to use</h4>
<ol>
<li>Pick the <b>OLD</b> and <b>NEW</b> layers (<b>Swap</b> exchanges
them).</li>
<li>Choose the <b>Join field</b> - the unique ID present in both layers
(for example a parcel or asset ID).</li>
<li>Optionally limit the attributes with <b>Field(s) to Compare</b>, and
tick <b>Compare geometry</b> to compare the shapes too.</li>
<li>Press <b>Validate Data</b> to check for duplicate or missing IDs and
field differences (the broom button clears these results).</li>
<li>Press <b>Compare OLD → NEW</b>.</li>
</ol>
<h4>Reading the results</h4>
<ul>
<li>The <b>Results</b> row sorts every feature into four kinds, matched by
the <b>Join field</b>, each with its own row colour and, beside it, the
number of features of that kind:
<ul>
<li><b>Added</b> - in NEW only: its ID is not in OLD.</li>
<li><b>Deleted</b> - in OLD only: its ID is missing from NEW.</li>
<li><b>Modified</b> - in both, and at least one compared value differs
(with <b>Compare geometry</b>, a shape measure counts too).</li>
<li><b>Unchanged</b> - in both, with every compared value the same.</li>
</ul>
Untick a kind to hide its rows (or click its box); the counts stay the
same. <b>Filter by value</b> shows only the rows with a value containing the
text, in the field picked beside it (<b>All fields</b> searches every
field).</li>
<li>A pink <b>Changed</b> cell shows the OLD value on the left and the
NEW value on the right of the divider (blank = empty / NULL).</li>
</ul>
<h4>Reviewing changes</h4>
<ul>
<li>Right-click changed values and choose <b>Accept</b> (keeps the NEW value,
lime <b>Accepted</b>), <b>Reject</b> (keeps the OLD value, lavender
<b>Rejected</b>) or <b>Reset</b>. Select several cells to decide them
together.</li>
<li>The <b>Accept / Reject / Reset</b> buttons (or <i>Accept row</i> in the
right-click menu) decide every changed value of the selected rows, or of all
visible rows. Added rows are decided as a whole.</li>
<li>The <b>Decision</b> column follows the values: <i>Accepted</i>,
<i>Rejected</i>, <i>Mixed</i> (some each way) or <i>Pending 1/3</i> while
values are still undecided.</li>
</ul>
<h4>On the map</h4>
<p><b>Zoom to selection</b> (also in the right-click menu) selects the
features of the selected rows in the OLD and NEW layers, zooms to them and
flashes them. With <b>Compare geometry</b> ticked, <b>Show on map</b> adds the
geometry that was added (green) and removed (red), and a <b>review point</b>
marks every place that changed (<b>Method</b>: Singlepart matches each part,
Multipart compares whole features). Double-click a changed row to see its
changes on the map.</p>
<h4>Export</h4>
<p>Excel (coloured, as shown) or CSV (plain values) of the visible rows. A
rejected value exports its OLD value; accepted and pending values export the
NEW value. The Decision column records each row's decision.</p>
"""

GENERAL_HELP = """
<h3>Attribute Table Studio</h3>
<p>Three tools in one toolbar: <b>Attribute Explorer</b>, <b>Pivot Table</b>
and <b>Geometry and Table Compare</b>.</p>
<h4>Windows and docking</h4>
<p>Each tool opens in its own window (with its own taskbar entry) and
remembers its size and position. Press the <b>Dock</b> button at the top
right of a tool to dock it into the QGIS main window, and again to undock.
To open a tool docked every time, tick it in <b>Plugins › Attribute Table Studio
› Settings</b>.</p>
<h4>Shortcuts</h4>
<p>All three tools are listed in <b>Settings › Keyboard Shortcuts</b> under
their names. Attribute Explorer proposes <b>Ctrl+Alt+T</b> when no other
command uses it; the normal attribute table shortcut (F6) is not
changed.</p>
<h4>Appearance</h4>
<p>The tools follow the QGIS light or dark theme automatically.</p>
<h4>License</h4>
<p>Attribute Table Studio - Copyright (C) 2026 Irene Jaya. Free software under
the GNU General Public License version 3, with an attribution term (section
7(b)); it comes with no warranty. Works based on it keep the notice
<i>Based on Attribute Table Studio by Irene Jaya</i>. Source code, license
and terms: https://github.com/irenejaya/attribute-table-studio</p>
"""


TOPICS = {
    "general": ("Attribute Table Studio", icons.HELP, GENERAL_HELP),
    "explorer": ("Attribute Explorer", icons.EXPLORER, EXPLORER_HELP),
    "pivot": ("Pivot Table", icons.PIVOT, PIVOT_HELP),
    "compare": ("Geometry and Table Compare", icons.COMPARE, COMPARE_HELP),
}


def _topic_icon(name):
    return icons.native(name) if name.startswith("/") else icons.tool(name)


class HelpDialog(QDialog):
    """Every tool in tabs, or one tool alone (*topic*, e.g. Compare's help
    button)."""

    def __init__(self, parent=None, tab=0, topic=None):
        super().__init__(parent)
        theme, metrics = Theme(), Metrics(self.font())
        lay = QVBoxLayout(self)
        lay.setContentsMargins(metrics.margin, metrics.margin,
                               metrics.margin, metrics.margin)
        if topic is not None:
            title, icon, html = TOPICS[topic]
            self.setWindowTitle(f"{title} — Help")
            self.setWindowIcon(_topic_icon(icon))
            self.resize(640, 620)
            view = QTextBrowser()
            view.setOpenExternalLinks(True)
            view.setHtml(html)
            lay.addWidget(view, 1)
        else:
            self.setWindowTitle("Attribute Table Studio — Help")
            self.setWindowIcon(icons.native(icons.HELP))
            self.resize(720, 640)
            tabs = QTabWidget()
            tabs.setIconSize(QSize(metrics.icon, metrics.icon))
            for title, icon, html in TOPICS.values():
                view = QTextBrowser()
                view.setOpenExternalLinks(True)
                view.setHtml(html)
                tabs.addTab(view, _topic_icon(icon), title)
            tabs.setCurrentIndex(tab)
            lay.addWidget(tabs, 1)
        box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        box.rejected.connect(self.reject)
        box.accepted.connect(self.accept)
        lay.addWidget(box)
        self.setStyleSheet(stylesheet(theme, metrics))
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
