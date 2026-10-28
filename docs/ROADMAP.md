# Roadmap

Ideas agreed for future versions of Attribute Table Studio. **Nothing here is
built yet**: the plugin does what it is meant to do today, and each item is
planned in detail (look, wording, edge cases) before any work starts.

Many of the ideas are inspired by the
[Data Wrangler](https://marketplace.visualstudio.com/items?itemName=ms-toolsai.datawrangler)
extension for VS Code, adapted to QGIS layers.

## High priority

### 1. Charts from the Pivot Table

Turn a built pivot into a chart without leaving the tool.

- Charts follow the pivot as built: its rows, columns, values and input
  scope.
- Light and dark themes, like the rest of the suite; export as an image.
- To settle when planning: which chart types (for example bar and stacked
  bar, line for date rows, a heatmap for row × column pivots), where the
  chart sits (beside the table or switching with it), and whether it is drawn
  with Qt itself (no extra packages, like the Summary histograms) or with a
  charting library.

## Planned

The order of these is decided when they are planned.

### 2. Expression history and recipes (Field Calculator and Quick update)

Repeatability without retyping: the expressions you run are kept so they can
be reused.

- Every expression applied in the Field Calculator or Quick update is
  recorded: the field, the expression, the scope (all, selected or filtered
  records) and when.
- One click puts a past expression back (for example `$area / 1000000` into
  `AREA_KM2`), on this layer or another one.
- Favourite expressions can be named and saved as recipes, in the project or
  a file, and replayed on next month's delivery.
- To settle: where the history lives (per user, per project or both) and how
  long it is kept.

### 3. Data quality issues in the Summary row

No new panel, and the Summary section keeps its look. The issues are
incorporated into the existing row of summary cards:

- A card shows a small marker only when its column has an issue; clean
  columns look exactly as they do now.
- The statistics popup lists the column's issues, each with *Show rows*,
  which filters the table to the affected records.
- Checks: values with stray spaces at either end, the same value written
  differently (capitals, typos; see 4), numbers or dates stored as text,
  values outside a field's allowed values (value map), constraint failures
  and duplicate IDs (see 5). NULL, empty and whitespace-only counts are
  already in the popup.
- The checks use the values the Summary already reads, so no extra loading.
- The issues are also the way into 4, 5 and 6.

### 4. Group similar values

Find the spellings of the same value in a text column and merge them.

- Two methods, as in OpenRefine:
  - **Same fingerprint:** values that match once case, surrounding spaces,
    punctuation and word order are ignored ("Residential", "RESIDENTIAL ",
    "residential.").
  - **Near matches:** small typos ("Residental", "Comercial").
- Suggestions only: each group is reviewed, the value to keep is chosen (the
  most common one by default) and the merge is applied as one undo step while
  editing.
- Meant for category fields (land use, suburb, owner type), not IDs or free
  text.

### 5. Duplicate finder

- Duplicates on chosen fields: exact, or ignoring case and surrounding
  spaces.
- Features with identical geometry.
- Results shown as groups, with a filter to the duplicates and selection on
  the map.

### 6. Field consistency check

Check that one field always goes with the same value of another, for example
*every ZONE_CODE always has the same ZONE_NAME*, and show the records that
break the rule.

### 7. Geometry columns (optional)

Geometry properties shown as extra columns with their own summary cards and
filters, as Geometry and Table Compare already does for its comparison.

- An on / off option like *Summary*, off by default. The table does not read
  geometry today, and reading it is the expensive part, so it only happens
  while the option is on.
- Area, length or perimeter, vertex count, number of parts, empty and
  validity. The validity check is the slow one and may run on demand.

### 8. Find (Ctrl+F)

Like Excel: type a value, every matching cell in any field is highlighted,
with next / previous to move between them.

### 9. Frozen columns

Pin one or more columns (an ID, for example) on the left so they stay in view
while scrolling across.

### 10. Totals in the status bar

Sum, average and count of the selected records for a number column, shown in
the Explorer's bottom status bar, where there is plenty of room. To settle:
which column (the framed cell's column is the natural choice).

### 11. Compare change report

Export a report of a comparison for whoever approves the update: counts per
kind (added, deleted, modified, unchanged), the fields that changed most, and
the review decisions. Format to settle (HTML, an Excel summary sheet or both).

## To discuss

### 12. Statistics computed by the database

Today the Summary reads every record into QGIS (in the background) and counts
in Python. The idea is to let the data source do the counting and send back
only the results. Nothing would look different, and the numbers must be
identical.

- **PostGIS and other databases:** the biggest gain, since records no longer
  travel over the network.
- **GeoPackage:** a SQLite database, so the same idea applies; likely faster
  on large files.
- **Shapefile and File Geodatabase:** a limited gain. GDAL can compute simple
  counts, sums, minimums and maximums itself, but histograms, medians and top
  values still need every record.
- **Limits:** while editing, unsaved changes exist only in QGIS, so the
  current method must be used; filters the data source cannot express fall
  back to it too; and the data source's text comparison rules must be checked
  so its results match exactly.
- **Next step:** measure the Summary on a million-record GeoPackage,
  shapefile and File Geodatabase first, and decide from the numbers.

## Considered and not planned

- **An operations panel** (one-click cleaning steps): the Field Calculator and
  Quick update already cover this, and the expression history (2) covers
  repeatability.
- **A side panel for statistics:** the statistics popup stays as it is.
- Transform by example, cleaning into a new layer, data bars, automated tests
  on GitHub, translations and AI-assisted filters.
