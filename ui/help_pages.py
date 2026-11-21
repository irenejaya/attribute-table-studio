# -*- coding: utf-8 -*-
# Attribute Table Studio - https://github.com/irenejaya/attribute-table-studio
# Copyright (C) 2026 Irene Jaya. Licensed under the GNU GPL v3 with an
# attribution term (section 7(b)): see LICENSE and ADDITIONAL_TERMS.md.
"""
The picture tabs of each tool's Help window.

A tab shows one picture of a part of the tool (``help/<key>.png``, made by
``tests/qgis/make_help_images.py`` from made-up data) with red numbered
badges, and beside it the numbered explanations below, in the same order.
Each item names its *target*, the widget the picture generator puts the
badge on (an attribute path of the tool's panel, ``a+b`` for two widgets
together, or a ``@name`` the generator works out, e.g. a cell of the
table), so a number in the picture always matches the text.

The last tab of every window keeps the full written help (More details).
"""

from . import icons


class Item:
    def __init__(self, target, title, text, icon=None):
        self.target = target
        self.title = title
        self.text = text
        self.icon = icon


class Tab:
    """*crops*: groups of targets; each group's area is one part of the
    picture (stacked top to bottom).  *setup*: what the generator prepares
    before taking it."""

    def __init__(self, key, title, intro, items, notes=(), crops=None, setup=None):
        self.key = key
        self.title = title
        self.intro = intro
        self.items = items
        self.notes = list(notes)
        self.crops = crops or []
        self.setup = setup or key


I = Item     # noqa: E741  (the lists below read better short)

# ============================================================================
# ATTRIBUTE EXPLORER
# ============================================================================

EXPLORER = [
    Tab("explorer_overview", "Overview",
        "The two rows at the top work on the layer in the Layer box. A greyed-out "
        "button has nothing to do yet. Hover any button to see what it does.",
        [
            I("layer_combo", "Layer",
              "The layer the table shows. Pick another one, or drag a layer here from "
              "the Layers panel."),
            I("active_btn", "Load active layer",
              "Switch to the layer selected in the Layers panel.", icons.REFRESH),
            I("count_label", "Record count",
              "The layer's records, and in brackets the records shown when a filter or "
              "a scope narrows the table, e.g. 3,000 (1,830)."),
            I("view_btn", "View",
              "Table or form view; which records the table shows (all, selected, visible "
              "on the map, edited and new, failing constraints); the Advanced Filter "
              "(Expression), stored filters, Filter by Form, sorting and conditional "
              "formatting.", icons.TABLE),
            I("zoom_btn", "Zoom to selection",
              "Zoom the map to the selected features and flash them.", icons.ZOOM_SELECTED),
            I("selected_btn", "Selected only",
              "Show only the features selected on the map; the statistics follow.",
              icons.SELECTED_ONLY),
            I("deselect_btn", "Deselect",
              "Clear the selection of this layer.", icons.DESELECT_LAYER),
            I("select_btn", "Select",
              "Select all or invert (the records shown or the whole layer), select by "
              "expression or by value, pan or zoom to the selection.", icons.SELECT_ALL),
            I("field_box", "Go to field",
              "Type part of a field name to jump to its column."),
            I("summary_btn", "Σ Summary",
              "Show or hide the column statistics above the table.", icons.SUMMARY),
            I("chart_btn", "Visualize Statistics",
              "A full chart of the selected column (histogram or bar chart) for the "
              "records in the table. It has its own Help.", icons.CHART),
            I("pivot_btn", "Pivot Table",
              "Summarise the records in the table in a Pivot Table.", icons.PIVOT),
            I("clear_btn", "Clear filters",
              "Clear every column filter, the expression filter and the records handed "
              "over from a pivot.", icons.FILTER),
            I("columns_btn", "Columns",
              "Show, hide and reorder the columns. Only the view changes, never the "
              "layer's fields.", icons.COLUMNS),
            I("export_btn", "Export",
              "Write the records shown to Excel or CSV (with the scope and filters "
              "noted), or copy the selected rows.", icons.EXPORT),
            I("find_btn", "Find and Replace",
              "Find a value in any cell shown (Ctrl+F); replace it while editing "
              "(Ctrl+H). See the Quick update & Find tab.", icons.SEARCH),
            I("query_btn", "Map filter",
              "The map shows only the records of the table: the layer's own filter "
              "follows the column filters. Click again to show every feature.",
              icons.QUERY),
            I("dock_btn", "Dock",
              "Dock the window into QGIS, and undock it again; filters and results stay.",
              icons.DOCK),
            I("edit_bar.more_btn", "More",
              "The feature form, copy rows or a cell, set a cell to NULL, review unsaved "
              "changes, Layer Properties, the native attribute table, feature actions "
              "and this Help."),
            I("@status", "Status bar",
              "Which records are shown, how many are selected, the active filters and "
              "whether the statistics are exact. While editing it adds the unsaved "
              "changes."),
        ],
        notes=[
            "The second row holds the editing commands: see the Editing tab.",
            "Every filter, scope and selection change updates the table and the "
            "statistics in the background; the window never freezes.",
        ],
        crops=[["layer_combo", "count_label"], ["view_btn", "dock_btn", "edit_bar.more_btn"],
               ["@status"]]),

    Tab("explorer_statistics", "Statistics & filters",
        "Above every column: its statistics, its name and a filter box. The "
        "statistics describe exactly the records in the table (all of them, not only "
        "the rows on screen) and follow every filter.",
        [
            I("@card:AREA_M2", "Statistics of a number column",
              "Missing (NULL) and distinct counts, a histogram, the minimum and maximum. "
              "Hover a bar for its count. Click the card for the full statistics (sum, "
              "mean, median, standard deviation, frequency list) with Copy, Export and "
              "Chart…"),
            I("@card:LAND_USE", "Statistics of a text column",
              "The most common values with their share of the non-NULL values."),
            I("@card:SURVEY_DATE", "Statistics of a date column",
              "The earliest and latest date and a histogram over time."),
            I("@card:HAS_SOLAR", "Yes / no column",
              "How many records are true, false and NULL."),
            I("@name:AREA_M2", "Column name",
              "Click to select the whole column (Ctrl or Shift for more; Ctrl+C copies "
              "its values), double-click to sort (ascending, descending, off), drag to "
              "move it. Right-click for sorting, Freeze Column, Visualize Statistics, "
              "Rename Field, Field Properties and more."),
            I("@frozen", "Frozen column",
              "A frozen column stays at the left edge while the others scroll: "
              "right-click its name › Freeze Column (Unfreeze Column frees it). Only the "
              "view changes."),
            I("@op:AREA_M2", "Filter operator",
              "How the filter compares: equals, contains, greater or smaller than, "
              "between, is one of, is NULL, and the 'not' versions."),
            I("@cell:LAND_USE", "Filter value",
              "Type a value: the table and every statistic follow. For is one of, a list "
              "of the values with their record counts opens."),
            I("@clear:LAND_USE", "Clear this filter",
              "Remove the filter of this column only."),
            I("@cell:SURVEY_DATE", "Date filter",
              "Dates are typed as YYYY-MM-DD; the calendar button picks one."),
            I("@cell:HAS_SOLAR", "Yes / no filter",
              "Click to choose true, false, NULL or any."),
        ],
        notes=[
            "Filters of different columns must all match (AND); the values of is one "
            "of are alternatives (OR).",
            "NULL is missing; zero and empty text are values.",
            "On a big layer the records show first and the statistics follow in the "
            "background, the columns on screen first (see More details).",
        ],
        crops=[["@frozen", "@card:LAND_USE", "@cell:HAS_SOLAR"]]),

    Tab("explorer_selection", "Selection",
        "Selecting rows selects their features on the map, and features selected on "
        "the map are selected in the table.",
        [
            I("zoom_btn", "Zoom to selection",
              "Zoom the map to the selected features and flash them.", icons.ZOOM_SELECTED),
            I("selected_btn", "Selected only",
              "Show only the selected features; the statistics follow.",
              icons.SELECTED_ONLY),
            I("deselect_btn", "Deselect",
              "Clear the selection of this layer.", icons.DESELECT_LAYER),
            I("select_btn", "Select",
              "Select all, invert, select by expression or by value; pan or zoom to the "
              "selection.", icons.SELECT_ALL),
            I("@tick:3", "Row tick box",
              "Click to select only this feature; Ctrl+click adds or removes it; "
              "Shift+click or a drag over the tick boxes selects a range. Works while "
              "editing too."),
            I("@selected:2", "Selected rows",
              "Selected features are highlighted here and on the map. A feature selected "
              "on the map scrolls into view."),
            I("@frame:6,2", "Framed cell",
              "A click on a cell frames it without changing the selection. The arrow keys "
              "move the frame and Ctrl+C copies its value; while editing, typing changes "
              "it."),
            I("@status", "Selected count",
              "How many features are selected, and how many of them are outside the "
              "records shown."),
        ],
        notes=[
            "View › Move Selection to Top shows the selected rows first.",
            "Export › Copy Selected Rows copies them as text that pastes into Excel.",
            "Filtering never changes the map selection.",
        ],
        crops=[["zoom_btn", "select_btn"], ["@rows:0-8"], ["@status"]]),

    Tab("explorer_editing", "Editing",
        "The explorer opens read-only. Edit starts QGIS's own edit session for the "
        "layer: the map, the attribute table and every other view share it.",
        [
            I("edit_bar.edit_btn", "Edit",
              "Start or stop editing. Stopping with unsaved changes asks Save, Discard "
              "or Cancel.", icons.TOGGLE_EDIT),
            I("edit_bar.save_btn", "Save edits",
              "Write all unsaved changes of the layer; editing stays on.", icons.SAVE_EDITS),
            I("edit_bar.discard_btn", "Discard",
              "Throw away all unsaved changes (asks first).", icons.ROLLBACK),
            I("edit_bar.undo_btn", "Undo", "Undo the last change on the layer.", icons.UNDO),
            I("edit_bar.redo_btn", "Redo", "Redo what you just undid.", icons.REDO),
            I("edit_bar.add_btn", "Add a record",
              "A new row in the table; the arrow also offers a form.", icons.NEW_ROW),
            I("edit_bar.delete_btn", "Delete",
              "Delete the selected features (asks first; one undo step).",
              icons.DELETE_SELECTED),
            I("edit_bar.cut_btn", "Cut", "Cut the selected features to the QGIS clipboard.",
              icons.CUT),
            I("edit_bar.copy_btn", "Copy", "Copy the selected features.", icons.COPY),
            I("edit_bar.paste_btn", "Paste",
              "Paste features from the QGIS clipboard (fields matched by name).",
              icons.PASTE),
            I("edit_bar.calc_btn", "Field calculator",
              "QGIS's own field calculator: update a field, or create a new or virtual "
              "one.", icons.CALCULATE),
            I("edit_bar.quick_btn", "Quick update",
              "Show or hide the Quick update row (see the next tab).", icons.QUICK_UPDATE),
            I("edit_bar.multi_btn", "Multi edit",
              "Change several fields of the selected features at once.", icons.MULTI_EDIT),
            I("edit_bar.new_field_btn", "New field",
              "A stored field (the data source's types) or a virtual field (an "
              "expression).", icons.NEW_FIELD),
            I("edit_bar.delete_field_btn", "Delete field",
              "Delete a stored or a virtual field.", icons.DELETE_FIELD),
            I("edit_bar.join_btn", "Join table",
              "Add the fields of another layer, or of a CSV, Excel or GeoPackage file, "
              "through a QGIS join; see how many records match before joining.",
              icons.JOIN),
            I("edit_bar.field_props_btn", "Field properties",
              "Every field in one grid: visible, read only, name, alias, allow NULL, "
              "unique and default value.", icons.FIELD_PROPS),
            I("edit_bar.reload_btn", "Reload",
              "Read the layer again from its data source.", icons.RELOAD),
            I("@dirty", "Changed cell",
              "An orange corner marks a value changed and not saved yet."),
            I("status.extra", "Edit status",
              "Editing layer, the unsaved changes, and the reminder that the changes are "
              "shared with QGIS."),
        ],
        notes=[
            "Type in a cell to change it; Enter or Tab moves on, Escape cancels.",
            "In a narrow window the less used groups move into the More menu.",
            "Closing the window never throws edits away: it asks first.",
        ],
        crops=[["edit_bar.edit_btn", "edit_bar.reload_btn"], ["@rows:0-5"], ["status.extra"]]),

    Tab("explorer_quick", "Quick update & Find",
        "Quick update calculates one field, as QGIS's field calculation bar; Find and "
        "Replace searches the cells shown, as in a spreadsheet.",
        [
            I("quick_bar.field_combo", "Field to update",
              "The field the value goes into."),
            I("quick_bar.expression", "Value or expression",
              "A value or an expression; the ε button opens the expression builder."),
            I("quick_bar.preview_btn", "Preview",
              "Show the first new values; nothing is written.", icons.QUICK_UPDATE),
            I("quick_bar.run_btn", "Update All / Update Filtered",
              "Calculate the field for every feature, or only for the records shown while "
              "the table is filtered (one undo step)."),
            I("quick_bar.selected_btn", "Update Selected",
              "Calculate it for the selected features."),
            I("quick_bar.close_btn", "Close",
              "Hide the row (the draft stays). It opens when editing starts (Settings)."),
            I("find_bar.find_box", "Find",
              "What to find in the cells shown; the matches are highlighted and counted.",
              icons.SEARCH),
            I("find_bar.case_btn", "Match case", "Upper and lower case must match.",
              icons.SEARCH_CASE),
            I("find_bar.whole_btn", "Match whole cell",
              "The whole cell must match, not a part of it.", icons.SEARCH_WHOLE),
            I("find_bar.regex_btn", "Regular expression",
              "Find with a regular expression; in Replace, $1 … $99 put back a group.",
              icons.SEARCH_REGEX),
            I("find_bar.prev_btn+find_bar.next_btn", "Previous / next match",
              "Go to the previous (Shift+Enter) or next (Enter) match."),
            I("find_bar.expand_btn", "Replace row",
              "Show or hide the Replace row (Ctrl+H)."),
            I("find_bar.replace_box", "Replace with", "The new text."),
            I("find_bar.replace_btn", "Replace",
              "Replace this match, then go to the next one (needs editing)."),
            I("find_bar.replace_all_btn", "Replace All",
              "Replace every match in the records shown, as one undo step."),
            I("find_bar.close_btn", "Close", "Close the Find card (Escape)."),
        ],
        notes=[
            "Find looks at the cells as they are shown; Replace changes the stored value "
            "and keeps within the field's length.",
            "Update Filtered only touches the records the table shows, as the native "
            "attribute table does.",
        ],
        crops=[["quick_bar"], ["find_bar.expand_btn", "find_bar.find_box", "find_bar.close_btn",
                               "find_bar.replace_box"]]),
]

# ============================================================================
# VISUALIZE STATISTICS
# ============================================================================

CHART = [
    Tab("chart_chart", "Chart",
        "A chart of one column for the records of the Attribute Explorer (its filters, "
        "scope and edits). Open it with the chart icon beside Σ Summary, a column name's "
        "right-click menu, or Chart… in a column's statistics.",
        [
            I("field_combo", "Field", "The column charted; any field type."),
            I("kind_combo", "Chart",
              "Histogram (vertical columns) or Bar chart (horizontal bars), for every "
              "field type.", icons.CHART),
            I("scope_label", "Records charted",
              "Which records: the explorer's scope, their number and its filters."),
            I("selected_btn", "Selected only",
              "Chart only the selected records.", icons.SELECTED_ONLY),
            I("clear_btn", "Clear selection",
              "Deselect the records (the chart's selection is the layer's).",
              icons.DESELECT_LAYER),
            I("export_btn", "Export",
              "Save the chart as a PNG or SVG image, copy it, or write the bars as CSV.",
              icons.EXPORT),
            I("help_btn", "Help", "Opens this window.", icons.HELP),
            I("dock_btn", "Dock", "Dock the chart into QGIS, and undock it.", icons.DOCK),
            I("@bar", "A bar",
              "Hover it for its numbers. Click to select its records (Ctrl+click adds, a "
              "drag selects a range); selected records show in the project's selection "
              "colour."),
            I("@legend", "Legend",
              "The mean and median lines and the selected records. With Split by, click "
              "a colour to select that category."),
            I("status", "Status bar",
              "Bins or values, the selected records, and the progress (with Cancel) while "
              "a big layer is read."),
        ],
        notes=[
            "Big layers are read in the background into compact columns: 10 million "
            "records chart without freezing QGIS.",
            "Changing bins, sorting or intervals never reads the layer again.",
        ],
        crops=[["field_combo", "dock_btn", "status"]]),

    Tab("chart_settings", "Settings",
        "The card at the right shapes the chart. Rows that do not apply to the field "
        "are hidden.",
        [
            I("bins_row", "Bins", "How many bins a histogram of numbers has."),
            I("scale_combo", "Scale",
              "Linear, logarithmic or square root: small bars stay visible next to very "
              "tall ones."),
            I("split_combo", "Split by",
              "Count each bar per value of a second field: the 8 most common values get "
              "their own colour, the rest form Other."),
            I("split_expr_btn", "Split by an expression",
              "Build the split in the QGIS expression builder, e.g. \"AREA_M2\" > 1000.",
              icons.EXPRESSION),
            I("split_mode", "Bars", "Stacked or side by side (with Split by)."),
            I("mean_tick", "Mean", "Show the mean as a dashed line."),
            I("median_tick", "Median", "Show the median as a dashed line."),
            I("std_tick", "± 1 Std. Dev.", "Shade one standard deviation around the mean."),
            I("normal_tick", "Normal Distribution Curve",
              "The bell curve with the same mean and standard deviation."),
            I("stats_box", "Statistics",
              "Records, count, NULL, minimum, maximum, sum, mean, median, standard "
              "deviation, skewness and kurtosis (most common values for text)."),
            I("title_edit", "Title", "The chart's title (changes with the field until "
                                     "you type your own)."),
            I("colour_btn", "Bar colour",
              "The colour of the bars (with Split by each category has its own)."),
            I("labels_tick", "Label bars with their count", "Write each bar's count on it."),
            I("grid_tick", "Grid lines", "Lines across the chart to read the bars off."),
        ],
        notes=[
            "Text, yes / no and one-bar-per-value charts add Sort (A → Z, Z → A, fewest "
            "or most records first, custom order…), Show (top 10 or 25 + Other, all "
            "values), Values (records or share) and Count NULL as a bar.",
            "Dates add Interval (day, week, month, quarter, year) and Show empty periods.",
        ],
        crops=[["@settings"]]),
]

# ============================================================================
# PIVOT TABLE
# ============================================================================

PIVOT = [
    Tab("pivot_overview", "Overview",
        "A pivot table groups records by the values of one or more fields and "
        "summarises each group. The top row chooses the records and builds it.",
        [
            I("layer_combo", "Layer", "The layer to summarise."),
            I("input_label", "Input", "Which records feed the pivot now."),
            I("scope_btn", "Change scope",
              "All available records, the Attribute Explorer's filtered records, or the "
              "selected records.", icons.FILTER),
            I("load_btn", "Load layout",
              "Load a saved recipe (fields, values, filters) from the project or a file.",
              icons.OPEN),
            I("save_btn", "Save layout",
              "Save the recipe in the project or a file (never the results).", icons.SAVE),
            I("refresh_btn", "Refresh input",
              "Capture the records of the scope again (after the layer, the selection or "
              "the explorer's filter changed).", icons.REFRESH),
            I("build_btn", "Build Pivot", "Calculate the pivot from the input."),
            I("chart_btn", "Chart",
              "The built pivot as a chart (see the Chart tab).", icons.CHART),
            I("export_btn", "Export",
              "The built pivot to Excel, CSV or a GeoPackage table (with its layout and "
              "scope), or copy it.", icons.EXPORT),
            I("help_btn", "Help", "Opens this window.", icons.HELP),
            I("dock_btn", "Dock", "Dock the window into QGIS, and undock it.", icons.DOCK),
            I("@status", "Status bar",
              "The groups of the result, and a warning when the source or the layout "
              "changed after the build."),
        ],
        notes=[
            "The records of the scope are captured when the input is set; later changes "
            "mark the result stale: Refresh input, then Build Pivot.",
        ],
        crops=[["layer_combo", "dock_btn"], ["@status"]]),

    Tab("pivot_build", "Build your table",
        "On the left: what to group by and what to calculate, then Build Pivot.",
        [
            I("row_rows[0].combo", "Rows", "The field to group by: one result row per value."),
            I("add_row_btn", "Add row",
              "Group by a second field inside the first (e.g. land use, then owner type).",
              icons.ADD),
            I("col_rows[0].combo", "Columns (optional)",
              "Spread a field across the top: one column per value (a cross tab). Leave "
              "it at None for a grouped list."),
            I("add_col_btn", "Add column", "Spread by a second field too.", icons.ADD),
            I("measure_rows[0].agg", "Operator",
              "Count records, count non-NULL, count distinct, list distinct, or for "
              "numbers sum, mean, median, min, max and standard deviation. Hover one in "
              "the list for what it does."),
            I("measure_rows[0].field", "Field",
              "The field the value reads (the list offers the fields that suit the "
              "operator)."),
            I("measure_rows[0].label", "Label", "Your own name for the value (optional)."),
            I("measure_rows[0].remove_btn", "Remove", "Remove this value.", icons.REMOVE),
            I("add_value_btn", "Add value",
              "Another measure; the same field may be used twice (e.g. mean and max).",
              icons.ADD),
            I("chips", "Filters",
              "Use only part of the input: click a filter to change it, × removes it. All "
              "filters must match."),
            I("add_filter_btn", "Add filter", "A new filter on any field.", icons.ADD),
            I("show_count_cb", "Show record count", "A Records column for every group."),
            I("show_total_cb", "Show grand total", "Total row and column."),
        ],
        notes=[
            "Totals are worked out from the contributing records, never by averaging the "
            "groups.",
            "NULL groups are kept apart from the text \"NULL\".",
        ],
        crops=[["@builder"]]),

    Tab("pivot_result", "Result",
        "The built pivot. It describes exactly the input captured at Build Pivot.",
        [
            I("title_label+subtitle", "Title and input line",
              "What the pivot shows; below it the scope, the filters and the rules used."),
            I("records_label+groups_label", "Records used",
              "How many records went in, and how many groups came out."),
            I("@heading:2", "Column heading",
              "Click to sort the table by this column (the chart follows)."),
            I("@result:1,2", "A result",
              "Double-click (or right-click › Inspect contributing records) to open its "
              "records in Attribute Explorer."),
            I("@total", "Grand total", "From all the contributing records."),
        ],
        notes=[
            "A group with no valid value shows NULL; a missing cross-tab cell shows NULL "
            "(Count records: 0).",
            "Copy (Ctrl+C) copies the selected cells or the whole table.",
        ],
        crops=[["title_label", "@result_area"]]),

    Tab("pivot_chart", "Chart",
        "The chart icon next to Build Pivot opens the built pivot as a chart: one bar "
        "per row, the pivot's columns as colours.",
        [
            I("value_combo", "Value", "Which of the pivot's values the bars show."),
            I("kind_combo", "Chart", "Vertical columns or horizontal bars.", icons.CHART),
            I("scope_label", "Pivot", "The records and rows of the pivot charted."),
            I("clear_btn", "Clear selection", "Deselect the records.", icons.DESELECT_LAYER),
            I("export_btn", "Export", "PNG, SVG, image copy or the bars as CSV.",
              icons.EXPORT),
            I("help_btn", "Help", "Opens this window.", icons.HELP),
            I("dock_btn", "Dock", "Dock the chart into QGIS, and undock it.", icons.DOCK),
            I("@bar", "A bar",
              "Hover for the value of each column, the total and its records. Click to "
              "select its records (Ctrl+click adds, drag for a range); right-click to "
              "inspect them in Attribute Explorer."),
            I("@legend", "Columns of the pivot",
              "Each column has its own colour (the 8 with the most records; the rest are "
              "Other). Click a colour to select that column's records."),
            I("sort_combo", "Sort",
              "As in the pivot table, A → Z, Z → A, smallest or largest value first."),
            I("top_combo", "Show", "All rows, or the first 10, 25, 50 or 100."),
            I("scale_combo", "Scale", "Linear, logarithmic or square root."),
            I("split_mode", "Bars",
              "Stacked (counts and sums, which add up) or side by side."),
            I("stats_box", "Summary",
              "Rows, columns, records, grand total, largest and smallest row."),
        ],
        notes=[
            "The chart follows the pivot: a new Build Pivot draws it again.",
            "Negative values hang below (or left of) the 0 line.",
        ],
        crops=[["value_combo", "dock_btn", "status"]]),
]

# ============================================================================
# GEOMETRY AND TABLE COMPARE
# ============================================================================

COMPARE = [
    Tab("compare_setup", "Setup",
        "Compare an OLD baseline and a NEW update of the same dataset, matched by a "
        "unique ID.",
        [
            I("old_table_combo", "OLD layer", "The baseline version."),
            I("new_table_combo", "NEW layer", "The updated version."),
            I("old_info+new_info", "Layer details",
              "Geometry type, features and fields of each layer."),
            I("swap_btn", "Swap", "Swap the OLD and NEW layers.", icons.SWAP),
            I("dock_btn", "Dock", "Dock the window into QGIS, and undock it.", icons.DOCK),
            I("join_field_combo", "Join field",
              "The unique ID that matches a feature in OLD with the same feature in NEW."),
            I("column_filter_btn", "Field(s) to Compare",
              "Choose which fields are compared (all by default)."),
            I("help_btn", "Help", "Opens this window.", icons.HELP),
            I("clear_validation_btn", "Clear validation",
              "Hide the validation results.", icons.CLEAR),
            I("validate_btn", "Validate Data",
              "Check both layers first: duplicate IDs, common fields, geometry types."),
            I("compare_button", "Compare OLD → NEW", "Run the comparison."),
            I("geom_compare_cb", "Compare geometry",
              "Compare the shapes too, with review points where they changed."),
            I("geom_length_cb+geom_vertices_cb", "Geometry properties",
              "Shape length, shape area, centroid XY and vertex count as extra columns."),
            I("geom_tol_spin", "Tolerance",
              "Differences smaller than this (in layer units) are not changes."),
            I("geom_mode_combo", "Method",
              "Multipart (whole feature) or singlepart (each part) comparison."),
            I("review_out_combo", "Review points",
              "Where the review points go: temporary points or a file."),
            I("validation_frame", "Validation report", "What Validate Data found."),
        ],
        notes=[
            "Nothing is written to your layers: decisions are kept in the comparison and "
            "its exports.",
        ],
        crops=[["old_table_combo", "dock_btn", "validation_frame"]]),

    Tab("compare_results", "Results",
        "Every feature is reported as Added, Deleted, Modified or Unchanged.",
        [
            I("@filter:Added", "Added", "Features only in NEW. Untick to hide them; the "
                                        "number counts them."),
            I("@filter:Deleted", "Deleted", "Features in OLD that are missing from NEW."),
            I("@filter:Modified", "Modified", "Features whose compared values changed."),
            I("@filter:Unchanged", "Unchanged", "Features identical in both."),
            I("filter_field_combo", "Field", "The field the value filter looks in (All "
                                             "fields by default)."),
            I("search_edit", "Filter by value",
              "Show only the rows with this value; suggestions come from the field."),
            I("export_btn", "Export",
              "Excel (coloured, as shown), CSV (plain values) or a GeoPackage of the "
              "features (one layer per kind of change).", icons.EXPORT),
            I("@heading:Change", "Change", "What happened to the feature."),
            I("@heading:Decision", "Decision", "Accepted, Rejected, Mixed or Pending "
                                               "(see the Review tab)."),
            I("@changed", "A changed value",
              "OLD on the left, NEW on the right of the divider (blank = empty or NULL)."),
            I("rows_label", "Rows shown", "How many rows the filters leave."),
        ],
        notes=[
            "The comparison engine is the one of the standalone Geometry and Table "
            "Compare v1.1.",
        ],
        crops=[["@filter:Added", "export_btn", "@table_top"], ["rows_label"]]),

    Tab("compare_review", "Review",
        "Decide what to keep: accept a change (the NEW value) or reject it (the OLD "
        "value). Exports keep your decisions.",
        [
            I("review_scope", "Rows", "The buttons apply to the selected rows or to all "
                                      "visible rows."),
            I("accept_btn", "Accept", "Keep the NEW values of the rows.", icons.SUCCESS),
            I("reject_btn", "Reject", "Keep the OLD values (exports write OLD).",
              icons.DELETE),
            I("reset_btn", "Reset", "Back to Pending.", icons.UNDO),
            I("zoom_btn", "Zoom to selection",
              "Select the rows' features in OLD and NEW, zoom to them and flash them.",
              icons.ZOOM_SELECTED),
            I("map_btn", "Show on map",
              "Add the geometry added (green) and removed (red) to the map.",
              icons.GEOM_DIFF),
            I("changed_field_legend", "Changed",
              "Changed values still pending. Click for the changed fields: how many "
              "records each one changed in; pick one to show only its changes."),
            I("accepted_legend", "Accepted", "Accepted values (lime)."),
            I("rejected_legend", "Rejected", "Rejected values (lavender)."),
            I("geom_summary_label", "Geometry changes", "How many shapes changed."),
            I("@accepted", "A decided value",
              "Right-click changed values to accept, reject or reset them one by one; "
              "the cell then shows the value kept."),
            I("@decision", "Decision",
              "Follows the values: Accepted, Rejected, Mixed or Pending 1/3."),
            I("review_tally", "Tally", "Accepted, rejected and pending rows."),
        ],
        notes=[
            "Added rows are decided as a whole; a Rejected row exports its OLD values.",
        ],
        crops=[["review_scope", "geom_summary_label", "@table_top"], ["review_tally"]]),
]

PAGES = {"explorer": EXPLORER, "chart": CHART, "pivot": PIVOT, "compare": COMPARE}
