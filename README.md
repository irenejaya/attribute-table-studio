# Attribute Table Studio

One QGIS plugin, one toolbar, three tools for working with attribute tables:

| | Tool | What it does |
|---|---|---|
| <img src="icons/attribute_explorer.svg" width="24"> | **Attribute Explorer** | A fast, read-only attribute table with exact statistics attached above every column and a typed filter under every heading. |
| <img src="icons/pivot_table.svg" width="24"> | **Pivot Table** | Grouped summaries and cross tabs with correct totals, an explicit input scope and drill-through to the contributing records. |
| <img src="icons/geometry_compare.svg" width="24"> | **Geometry and Table Compare** | OLD → NEW comparison of two dataset versions: added, deleted, modified and unchanged features, field-level changes, geometry review points. |

Supports **QGIS 3.40 LTR+ (Qt5) and QGIS 4.x (Qt6)**, light and dark themes.

![Attribute Explorer](docs/screenshots/dark_explorer.png)

## Install

1. Build the zip: `python scripts/package.py` → `dist/attribute_table_studio-1.0.0.zip`
2. QGIS → *Plugins › Manage and Install Plugins › Install from ZIP*.
3. If the standalone *Geometry and Table Compare* plugin is installed, disable
   it — Attribute Table Studio includes it (the plugin warns when both are enabled).

## Windows and docking

Every tool opens in **its own window** (own taskbar entry) that remembers its
size and position. The **Dock** button at the top right of each tool docks it
into the QGIS main window — and undocks it again — keeping filters, results
and selections. *Plugins › Attribute Table Studio › Settings* can open each tool
docked by default, like *Open attribute table as docked window* in QGIS.

## Attribute Explorer

Open it from the toolbar, the plugin menu, a vector layer's right-click menu
(*Open in Attribute Explorer*) or the keyboard shortcut (**Ctrl+Alt+T** is
proposed and only assigned when no other command uses it; change it in
*Settings › Keyboard Shortcuts*). Each layer gets its own explorer.

- **Column profiles** above every column: missing (NULL) and distinct counts,
  top categories with shares of non-NULL values, histograms with min / max,
  earliest / latest dates, true / false counts. Click a profile for full
  statistics (sum, mean, median, population standard deviation, searchable
  frequency list, blank and whitespace values, histogram bins).
- **Filters** under every heading — text: contains, equals, starts with, is
  one of, is NULL, is not NULL; numbers: =, ≠, <, ≤, >, ≥, between, is one of;
  dates: on, before, after, between (calendar, YYYY-MM-DD hints); booleans:
  true, false, NULL.
  Columns combine with AND, values within a column with OR. Invalid input is
  outlined red and the last valid result stays. The ✕ in a filter box clears
  it (also for *is one of*); the value list sorts by value or by records.
- **Scope rules**: profiles describe exactly the filtered records (not just
  the visible screen). NULL is missing; zero and empty text are values.
  Sorting and hiding columns never change the population.
- **Selection** stays in sync with the map (click, Ctrl, Shift, drag or the
  tick boxes). A feature selected on the map scrolls its row into view.
  *Selected only* limits table and statistics to the selection. Filtering
  never changes the map selection.
- Sort by clicking a column name, drag to reorder, *Columns* to show / hide /
  reorder; layouts are saved with the layer in the project.
- *Export* to Excel or CSV with the scope recorded; *Ctrl+C* copies rows;
  *Open Pivot* hands the filtered records to the Pivot Table.

Statistics are exact and calculated in background tasks (visible columns
first, cancellable). Large layers show their records immediately and fill in
the profiles afterwards; stale results are never shown.

## Pivot Table

![Pivot Table](docs/screenshots/dark_pivot.png)

- Input scope: all available records, Explorer filtered records or selected
  records; the ids are captured, later changes mark the result stale
  (*Refresh input*, then *Build Pivot*).
- Rows (one or more), optional columns (cross tab), several values — count
  records, count non-NULL, count distinct, sum, mean, median, min, max,
  population standard deviation — with optional labels.
- *Add filter* opens a small dialog; each filter then shows as a chip
  (click to edit, × to remove). All filters must match (AND).
- Totals are calculated from contributing records (never by averaging group
  results); NULL groups are separate from the text "NULL"; missing cross-tab
  cells show NULL (Count records: 0).
- Double-click a result to open its records in Attribute Explorer.
- Save / load layouts (recipes only) in the project or a JSON file; export to
  Excel, CSV or a non-spatial GeoPackage table with the layout and scope.

## Geometry and Table Compare

![Geometry and Table Compare](docs/screenshots/dark_compare.png)

![Reviewing changed values](docs/screenshots/dark_review.png)

A change-tracking tool: an OLD baseline and a NEW update of the same dataset
are matched by a unique ID and every feature is reported as Added, Deleted,
Modified or Unchanged. The comparison engine is unchanged from the standalone
plugin v1.1 (the regression test checks that exports match it byte for byte).

- A **changed value** shows OLD and NEW side by side, split by a divider.
  Right-click values to **accept** (keeps NEW, lime *Accepted value*),
  **reject** (keeps OLD, lavender *Rejected value*) or reset them, one cell or
  many at once; the cell then shows the one value kept.
- *Accept / Reject / Reset* (buttons or right-click) decide every changed
  value of whole rows. The Decision column follows the values: Accepted,
  Rejected, Mixed or *Pending 1/3*. Exports keep OLD for rejected values and
  NEW otherwise.
- **Zoom to selection** (button or right-click) selects the rows' features in
  the OLD and NEW layers, zooms to them and flashes them.
- The help button explains the tool; the broom button clears the validation
  results. The v1.1 *Validate Data* and *Compare OLD → NEW* buttons, Results
  legend and bottom bar with its progress bar are kept.

## Look and sizes

All three tools share one palette, native QGIS icons (16 px) and one size
ladder: inputs, buttons and square icon buttons are 24 px, including the blue
*Build Pivot*; Compare's *Validate Data* and *Compare OLD → NEW* (190 × 28,
as in v1.1) and *Swap* are 28 px. When a window is too narrow for the button
labels, the buttons turn icon-only together and keep their 16 px icons.
The status bar sits fully inside every window.

Attribute Explorer colours values by type: text orange, numbers green, dates
blue (one blue that reads on light and dark themes), booleans purple, NULL
grey.

## Project layout

```
plugin.py              toolbar, menu, shortcuts, layer menu, window registry
core/                  UI-free engines (pure Python where possible)
  fields.py            field kinds, NULL handling, formatting
  stats.py             column profiles
  filters.py           column filters → QGIS expressions
  pivot.py             pivot engine
  scan.py              background feature scans (no geometry)
  jobs.py              QgsTask wrapper with stale-result protection
  export.py            CSV / Excel writers with scope records
ui/
  theme.py             shared palette, metrics and style sheet
  widgets.py           uniform buttons, status strip, field dialogs
  icons.py             native QGIS icons + tool icons
  tool_host.py         window ↔ dock host
  attribute_explorer.py, explorer_header.py, explorer_table.py
  pivot_table.py
  geotable_compare.py
  settings_dialog.py, help_dialog.py
icons/                 toolbar icons
tests/                 unit tests + headless QGIS tests
```

## Tests

```
python -m unittest discover -s tests          # engines, no QGIS needed
# with QGIS' Python (QT_QPA_PLATFORM=offscreen for headless):
python tests/qgis/test_filters_qgis.py        # expressions on memory + GeoPackage layers
python tests/qgis/test_suite_qgis.py          # end-to-end: all three tools
python tests/qgis/test_compare_regression.py  # Compare exports == v1.1
```

All suites pass on QGIS 4.2 (Qt 6.11) and QGIS 3.44 LTR (Qt 5.15).

## License

MIT — see [LICENSE](LICENSE).
