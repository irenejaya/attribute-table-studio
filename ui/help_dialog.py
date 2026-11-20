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
window to the front. <b>Layer</b> switches the explorer to another layer;
the refresh icon beside it loads the layer selected in the Layers panel
(greyed out when that layer is already shown or is not a vector layer or
table). The number beside them is the layer's record count, followed in
brackets by the records shown in the table when that is different: <b>9,434
(484)</b>.</p>
<h4>Filters</h4>
<ul>
<li>Every column has a filter below its name. Click the symbol on the left
to choose the operator: text <i>contains, does not contain, equals, is not,
starts with, is one of, is not one of, is NULL, is not NULL</i>; numbers
<i>=, ≠, &lt;, ≤, &gt;, ≥, between, is one of, is not one of</i>; dates
<i>on, before, after, between</i> (with a calendar); booleans <i>true, false,
NULL</i>. The "not" filters keep the NULL records (a NULL value is not
'Residential'), unless NULL is one of the values left out; picking values
(▾) from a "not" filter leaves them out.</li>
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
NULL is missing; zero and empty text are values. <b>Σ Summary: On / Off</b>
collapses the strip; the chart icon beside it opens Visualize Statistics. Statistics are exact and calculated in the
background - visible columns first.</p>
<h4>Selection and map</h4>
<p>Select features with the tick boxes / row numbers (a click selects only
that feature, Ctrl+click adds or removes it, Shift+click or a drag selects a
range - also while editing); the table and the map selection stay in sync.
A click on a cell only frames it, as while editing: the arrow keys move the
frame and <b>Ctrl+C</b> copies the cell's value (<b>Ctrl+Shift+C</b> copies
the selected rows). Selecting features
on the map scrolls the table to the first of them; after a sort or a filter
the table shows its top, or the first selected feature. Filtering never changes
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
Failing Constraints</i> (hard and soft counts in the status bar) and
<i>Show Edited and New Features</i> (unsaved changes). Column filters
always apply on top.</li>
<li><i>Advanced Filter (Expression)</i> opens the expression bar on the
left of the edit row, as the QGIS attribute table's filter box: type or edit
the expression there and press <b>Enter</b> or <b>Apply</b> (an invalid one
says why and cannot be applied; <b>Escape</b> puts the applied one back;
<b>…</b> opens QGIS's Expression Builder). <b>☆</b> saves the expression
among the layer's stored filter expressions (shared with QGIS) or loads one;
<b>×</b> removes the filter. In a narrow window the edit row moves its
less used commands into <b>More</b> to keep room for the box. <i>Stored
Filter Expressions</i> reuses the layer's saved ones; <i>Filter by Form</i>
uses the layer's form in search mode. <b>Clear filters</b> clears the
expression too.</li>
<li><i>Case-Sensitive Text Filters</i>, <i>Move Selection to Top</i> (keeps
the filtered records, only moves the selection up), <i>Sort by
Expression</i>, <i>Conditional Formatting</i> (the layer's own rules,
painted in the table) and <i>Autosize All Columns</i>. Right-click a column name for sorting,
widths, hiding, the field calculator, renaming, deleting and field properties.</li>
</ul>
<h4>Editing</h4>
<p>The second row holds the editing commands (and, while in use, the
expression filter on its left). The status bar's line says what the table
and its statistics show: the scope, the records shown of those available,
the selection and the filters (listed on hover). <b>Edit</b> starts or stops editing the
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
<li><b>Quick update</b> opens when editing starts, like the native
attribute table's field calculation bar (turn this off in Settings, <i>Show
Quick update when editing starts</i>; its button still hides it). Pick a
field and an expression, then <b>Update All</b> - or
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
or virtual fields); <b>Reload</b> re-reads the data source. <i>Rename
Field…</i> (right-click a column name) renames a field as QGIS's Fields tab
does: it starts editing when needed, checks the new name as you type (empty,
already used, longer than a shapefile's 10 characters) and is one undo step
until <b>Save edits</b>; the column keeps its filter, width, place and sort.
Expressions that use the old name are not changed. The ☰ menu holds
the feature form, layer actions, copy as text, set NULL, <i>Review Unsaved
Changes</i>, <i>Layer Properties…</i> (QGIS's own, on its usual page) and the
native attribute table.</li>
<li><b>Field properties</b> (beside <i>Join table</i>, also <i>Field
Properties…</i> in a column name's right-click menu, which picks that field)
shows every field in one grid, as QGIS's Fields tab and ArcGIS's Fields view:
<i>Visible</i> (the Explorer column), <i>Read only</i>, <i>Field name</i>,
<i>Alias</i>, <i>Allow NULL</i>, <i>Unique</i> and the <i>Default value</i>
(an expression, optionally also <i>On update</i>) can be changed; type,
length, precision, origin (data source, joined, virtual, new) and comment
are shown. <i>Search / Filter fields</i> narrows the rows; a row with a
change shows its name in bold. <b>OK</b> applies everything together (a new
name is renamed in the edit session, written by <b>Save edits</b>; the other
settings are saved in the project, as in Layer Properties); <b>Cancel</b>
changes nothing. Settings the data source fixes, or that do not apply to a
joined or virtual field, are greyed with the reason on hover.
<b>Layer Properties…</b> opens QGIS's Fields page for the rest.</li>
<li><b>Join table</b> (beside <i>New field</i>; no editing needed) adds a
QGIS layer join, as <i>Layer Properties › Joins</i> does. Choose the
<b>Join layer</b> in the box, or drop a CSV, Excel, GeoPackage or shapefile
on the window from your file manager. Under <b>Field match</b> the two fields
are proposed (a field name both share) and the window counts how many records
will match before you join: values match as text (12, 12.0 and "12" are the
same), NULL never matches, and when a join value repeats a record gets the
last matching row. Tick the fields to add (none are ticked at first; <i>Search / Filter fields</i> narrows the
list; <i>All</i> and <i>None</i> then tick the fields shown). The joined fields are named with
QGIS's default prefix (the join layer's name and _) unless you tick
<b>Custom field name prefix</b>, as in QGIS's own window; a name the layer
already has is left out by QGIS, and the window says so. A dropped file
becomes a layer of the project when you press <b>OK</b>. <i>Joins on this layer</i> lists the joins, each
with Remove.</li>
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
<p>Click a column name to select the whole column (Ctrl+click adds or removes
one, Shift+click a range; <b>Ctrl+C</b> copies their values for the records
shown, with the field names first; clicking a cell or <b>Escape</b> clears it).
Double-click a column name to sort (again for descending, a third time to
reset). Right-click a name and choose <b>Freeze Column</b> to keep it at the
left edge while the other columns scroll (as in ArcGIS Pro; several can be
frozen, in the order you freeze them); <b>Unfreeze Column</b> lets it scroll
again as the first scrolling column, and <b>Unfreeze All Columns</b> frees
them all. Only the view changes, never the layer's field order; frozen
columns are kept with the layer's column layout. Drag names to reorder, drag
edges to resize, and use the
<b>Columns</b> icon to show, hide and reorder (Shift+click selects a range
of fields, Ctrl+click adds one; <b>Space</b> or <b>Toggle Selection</b> ticks
them together); the layout is saved with the layer in the project. <b>Export</b> writes the filtered records to Excel
or CSV with the scope recorded; <b>Ctrl+Shift+C</b> copies the selected
rows (<b>Ctrl+C</b> the framed cell).
<b>Pivot Table</b> passes the filtered records to the Pivot Table.</p>
<p><b>Ctrl+Home</b> / <b>Ctrl+End</b> jump to the first / last record and
<b>Page Up</b> / <b>Page Down</b> move a page (click a cell first);
right-click the scroll bar for <i>Top</i> and <i>Bottom</i>.</p>
<h4>Find and Replace</h4>
<p>The magnifier left of the map filter, or <b>Ctrl+F</b>, opens the find
bar at the top right of the rows; <b>Ctrl+H</b> (or its chevron) adds the
Replace line. It finds the text in every cell shown, as the table shows it,
ignoring case unless <b>Match case</b> (Cc) is on; <b>Match whole cell</b>
(ab) and <b>Regular expression</b> (.*) refine it. NULL cells never match.
Matches are tinted and counted ("3 of 128"); the current one is framed
(<b>Ctrl+C</b> copies it). <b>Enter</b> / <b>Shift+Enter</b> (or <b>F3</b> /
<b>Shift+F3</b>) move to the next / previous match; <b>Escape</b> closes the
bar. Find never filters or selects.</p>
<p>Replace works while the layer is being edited (<b>Start editing</b> is in
the bar), in text fields that can be edited: <b>Replace</b> (Enter in the
Replace box) changes the framed match and moves on; <b>Replace All</b>
(<b>Ctrl+Alt+Enter</b>) changes every match in the records shown as one
undo step, unsaved until <b>Save edits</b>. Number, date and true / false
matches are found but never replaced. With a regular expression, $1 … $99
put back a group and $&amp; the whole match.</p>
"""

CHART_HELP = """
<h3>Visualize Statistics</h3>
<p>A full chart of one column of an Attribute Explorer, for the records the
explorer shows (its filters, View scope, map extent or pivot records).
Click the chart icon beside <b>Σ Summary</b> (it charts the selected column,
or the column of the current cell), right-click a column name and choose
<b>Visualize Statistics…</b>, or click <b>Chart…</b> in a column's statistics
popup. Each explorer has one chart
window; <b>Field</b> switches the column. It closes with its explorer, and
<b>Dock</b> puts it in the QGIS window (and back).</p>
<h4>Charts</h4>
<p><b>Chart</b> offers every field a <b>Histogram</b> (vertical columns) and
a <b>Bar chart</b> (horizontal bars); text starts as a bar chart, numbers
and dates as a histogram.</p>
<ul>
<li><b>Numbers</b> (and times): the histogram has equal-width bins from the
minimum to the maximum (the last bin holds the maximum); <b>Bins</b> sets
their number, whole numbers spanning a few values get one bar each. Mean,
median, ± 1 standard deviation and a normal distribution curve can be drawn
on it. The bar chart has one bar per value (handy for codes).</li>
<li><b>Text</b> and true / false: one bar or column per value. <b>Sort</b>
(as in ArcGIS Pro): A → Z or Z → A by the values (numbers: smallest or
largest value first), fewest or most records first (the default), or
<b>Custom order…</b>, where you drag the values into place (choose it again
to change the order). Show the top 10 or 25 and Other, or all values; <b>Values</b> shows records or shares, and NULL can be
a bar. Names that don't fit under their columns are slanted.</li>
<li><b>Dates</b>: records per day, week (from Monday), month, quarter or
year; <b>Auto</b> picks about 120 bars at most.</li>
</ul>
<p><b>Split by</b> counts every bar per value of a second field, or of an
expression (the ε button opens the QGIS expression builder, e.g.
<i>"AREA_M2" &gt; 1000</i>), stacked or side by side (<b>Bars</b>): the 8 most
common values get their own colour (checked for colour-blind readers), the
rest form Other. <b>Scale</b> (linear,
logarithmic, square root) lets small bars show beside very tall ones.</p>
<h4>Selecting</h4>
<p>Click a bar to select its records on the layer (the map and the table
follow); <b>Ctrl+click</b> adds a bar and a drag across bars selects the
range. With Split by, a click on a coloured part selects that value in that
bar, and a click on a legend entry selects that value in every bar. The
selected records of every bar are drawn from its base in the project's
selection colour, also for a selection made on the map, so a bar can be
partly selected; the legend and the hover card give the numbers.
<b>Selected only</b> charts only the selected records and <b>Clear the
chart selection</b> deselects them.</p>
<h4>Statistics and export</h4>
<p>The <b>Statistics</b> list matches the explorer's (population standard
deviation; NULL and unusable values counted apart) and adds skewness and
kurtosis (3 for a normal distribution). <b>Export</b> saves the chart as a
PNG or SVG image, copies it, or writes the bars to CSV with the scope
recorded next to it.</p>
<h4>Big layers</h4>
<p>The records are read in the background (progress and Cancel in the status
bar) and only compact columns are kept, about 25 bytes a record, so a layer
of 10 million records charts without freezing QGIS. Bins, sorting and
intervals are worked out again from those columns without reading the
layer; a new field, Split by or Selected only reads it again.</p>
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
<li><b>Values</b>: what to calculate for each group. Pick the operator
first, then the field it reads (the list offers the fields that suit it):
<i># Count records</i> (no field needed), <i>n Count non-NULL</i>,
<i>≠ Count distinct</i>, <i>≡ List distinct</i> (the different values of the
field, A to Z, separated by commas - e.g. the inlet types of every pit), or
for numbers <i>Σ Sum, x̄ Mean, x̃ Median, ↓ Min, ↑ Max</i> and
<i>σ Std dev (population)</i>. Hover an operator in the list for what it
does. Type a label if you like. <b>Add value</b> adds another measure; the
same field may be used twice (e.g. its mean and its max).</li>
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
<h4>Chart the result</h4>
<p>The <b>chart icon</b> next to <b>Build Pivot</b> opens the built pivot as
a chart, in the window of Visualize Statistics: <b>one bar per row</b> of
the pivot. <b>Value</b> picks which of the pivot's values the bars show (the
first one at first, or <i>Records</i>); <b>Chart</b> draws vertical columns
or horizontal bars. With a column field, the <b>columns of the pivot become
the colours</b> of the bars: <i>Stacked</i> (the parts add up to the row's
total) or <i>Side by side</i>. A mean, median, minimum, maximum, standard
deviation or distinct count does not add up, so those are always side by
side. More than 8 columns: the 8 with the most records keep their own
colour, the rest are shown together as <i>Other</i> (worked out from their
records, so a mean of Other is the mean of those records).</p>
<ul>
<li><b>Hover</b> a bar for its numbers: the value of each column, the total,
its records and how many of them are selected.</li>
<li><b>Click</b> a bar to select its records on the layer (<b>Ctrl+click</b>
adds, <b>drag</b> across bars selects the range); click a coloured part for
that column's records only, or a colour in the legend for that column across
the chart. A bar turns the selection colour once all its records are selected
(for a count of records, the selected ones fill it from the bottom).
<b>Right-click</b> a bar to <i>Inspect Contributing Records</i> in Attribute
Explorer, as a double-click on the table does.</li>
<li><b>Sort</b> keeps the table's order (sort the table by a column to
reorder the chart), or A → Z, Z → A, smallest or largest value first.
<b>Show</b> keeps the first rows of a long pivot. Negative values hang below
the 0 line (or left of it).</li>
<li>The chart follows the pivot: a new <b>Build Pivot</b> draws it again.
After an edit of the layer, selecting from the chart waits for a new
Build Pivot. <b>Export</b> saves the chart as a PNG or SVG image, copies it,
or writes the bars as CSV.</li>
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
<p><b>GeoPackage - features</b> writes the features of the visible rows to
one file, one layer per kind (<i>added</i>, <i>deleted</i>, <i>modified</i>,
<i>unchanged</i>), and adds them to the map in a group named after the file.
Every layer has the same fields: <b>CHANGE</b>, <b>DECISION</b> and
<b>CHANGED_FIELDS</b> (the columns that changed, and <i>geometry</i> when the
shape changed), then the table's columns. Each column holds the reviewed
value: NEW, or OLD where the change was rejected (a deleted feature keeps its
OLD values). A column that changed in a modified row is followed by
<b>&lt;name&gt;_OLD</b> with the OLD value (empty where it did not change).
Numbers, dates and yes / no values keep their types. The shape is NEW, or OLD
for deleted features and where a shape value (such as <i>_geom_area</i>) was
rejected, in the NEW layer's CRS.</p>
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
    "chart": ("Visualize Statistics", icons.CHART, CHART_HELP),
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
