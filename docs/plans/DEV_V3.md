# dev-v3 plan (not built yet)

Three improvements, the first one in detail.

## 1. Attribute Explorer: lighter and faster on big layers

### Where the time and memory go today

Measured in QGIS 4.2 on a made-up GeoPackage table of 10,000,000 records
with 5 fields (a number, a decimal, a text, a date and an id):

| Step | Today | Memory (QGIS itself used 312 MB) |
| --- | --- | --- |
| Records shown in the table | 15 s | 720 MB |
| All column statistics ready | 181 s | 1,364 MB, **peak 3,053 MB** |
| A filter (Residential, 6 million left) | 115 s (records after 9 s) | peak 2,159 MB |
| Sort by a number column | 26 s | peak 2,025 MB |

The causes:

1. **One full read of the layer per column.** The statistics keep every
   value of a column in memory as Python objects, so to stay within the
   memory budget each read handles only one column at 10 million records:
   5 columns = 5 reads of 22 – 38 s each (137 s), plus 27 s of counting.
2. **Python objects per value** (40 – 100 bytes each). The date column alone
   pushed memory to 3 GB.
3. **Every filter reads everything again.** The table shows the filtered
   records after 9 s, then all statistics are recalculated by reading the
   layer once per column again (106 s).
4. **The record list** is a Python list: about 40 bytes per record, 400 MB
   at 10 million.
5. **Sorting** reads the column and sorts Python objects (26 s).

What already works well and stays: the records show first (15 s), the
table fetches only the rows on screen (256 at a time), and everything runs
in the background with progress and Cancel.

### The plan

Same exact statistics as now (same rules: NULL apart, population standard
deviation, exact median and distinct count); only how they are worked out
changes, in the way Visualize Statistics already does it.

**Step 1 – A compact record list.** The ids of the records in the table
are kept as 8-byte numbers instead of Python objects (400 MB → about 80 MB
at 10 million). Finding a record's row (selection, the framed cell) uses a
sorted index built only when needed, instead of a dictionary of every
record.

**Step 2 – All statistics in one read, kept compact.** The shown columns
are read together in one pass. Numbers and dates are kept as 8-byte values
(the exact median and distinct count need them), text and yes / no values
are counted as they are read. A memory budget decides how many columns
share a read (at 10 million records about 7 number or date columns per
read, any number of text columns). numpy does the counting when QGIS has
it (it nearly always does), plain Python otherwise.

**Step 3 – Filters reuse what was read.** The compact columns of the
layer's records are kept (within a memory budget, about 1 GB by default),
so a filter, a View scope change or a drill-through works out the
statistics of the remaining records from them, without reading the layer
again. An edit refreshes just what changed or, for big edits, reads again.
Visualize Statistics can use the same columns, so a chart opens at once.

**Step 4 – Faster sorting.** Sorting by a number or date column sorts the
compact values (numpy) instead of Python objects, and uses the kept column
when there is one.

### Targets (to be measured before and after, at 1 and 10 million records)

| Step | Today | Target |
| --- | --- | --- |
| Records shown | 15 s | 15 s (unchanged) |
| All statistics | 181 s | about 40 – 50 s |
| Peak memory while opening | 3.0 GB | about 1 GB |
| Memory once open | 1.4 GB | about 0.6 – 0.8 GB |
| A filter | 115 s | about 10 – 15 s |
| A sort by a number column | 26 s | under 10 s once read |

### How it is checked

- The new statistics are compared with today's on random data (numbers,
  dates, text, NULL, NaN, empty text) and must match exactly.
- The existing test suites on QGIS 4.2 and 3.44.
- Timings and memory at 1 and 10 million records, before and after.

### Later (not in this plan)

For GeoPackage and PostGIS layers without unsaved edits, the database
itself could count (SQL), faster still; it only works for some formats and
filters, so it would come after this.

## 2. Pivot Table: chart the result

A chart icon in the Pivot Table's top row (the Visualize Statistics icon)
opens the pivot result as a chart in the same window as Visualize
Statistics: one bar per row of the pivot, its value the first measure; with
a column field, the columns become the Split by colours (stacked or side by
side). Hover shows the numbers, a click selects that row's records on the
layer, as the drill-through does.

## 3. Geometry and Table Compare: changed-fields summary

No new row: the existing **Changed · 36** chip in the Review row becomes a
button that opens a popup, like the Explorer's statistics popup:

```
 Changed fields  ·  35 modified records
 ──────────────────────────────────────────────
 LAND_VALUE     32   ███████████████████  91%
 LAND_USE        4   ██                   11%
 HAS_SOLAR       2   █                     6%
 ──────────────────────────────────────────────
 Click a field to show only its changes      All
```

- One line per field that changed, most changes first, with the share of
  the modified records and a small bar.
- A click on a field shows only the records where that field changed and
  scrolls to its column; **All** shows every change again.
- Accepted and rejected counts per field on hover.
