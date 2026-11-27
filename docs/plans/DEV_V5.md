# dev-v5 plan: Data Plot

A new tool, **Data Plot**, that makes plots from a layer's records, with 12
plot types and the options ArcGIS Pro gives each of its charts. It is a tool
of its own, apart from Visualize Statistics and the Pivot Table chart, which
stay as they are.

| Round | Content | Status |
| --- | --- | --- |
| 1 | The Data Plot window, plus Scatter Plot, Line Chart and Bar Chart | Planned |
| 2 | Combo Chart, Pie Chart, Histogram, Box Plot | Planned |
| 3 | Scatter Plot Matrix, QQ Plot, Matrix Heat Chart, Calendar Heat Chart, Data Clock | Planned |
| 4 | Help, README, screenshots, 1.4 changelog | Planned |

Each round is tested on QGIS 4.4.0 and then QGIS 3.44.14, committed and pushed to
dev-v5, and shown with screenshots before the next round starts. The version
becomes 1.4 when the four rounds are done.

## Decisions

| Question | Decision |
| --- | --- |
| Name | Data Plot (toolbar, plugin menu, window) |
| Drawing | Qt's own painter, like the current charts: no extra package, the QGIS light or dark look, hover cards, selecting records |
| Opened from | A new toolbar button after Pivot Table and a plugin menu entry, with its own layer box |
| Fields | Fields only, as in ArcGIS Pro (no expressions); a virtual field covers a calculated value |
| Records | Live: All records, the Attribute Explorer's filtered records, Selected records or Visible map extent; the plot redraws by itself when those records or their values change |
| Plot types | All 12, in rounds 1 to 3 |
| Kept | A list of plots per layer, saved in the QGIS project; a plot can also be saved to or loaded from a file |
| Selection | Two-way: selecting on the plot selects the records on the layer, and records selected anywhere are highlighted in the plot |
| Options | As in ArcGIS Pro, listed per type below; options not confirmed on Esri's pages are left out |
| Many points | Every point drawn while it stays readable; past a limit measured in round 1, each spot is shaded by how many records fall on it, with every record counted |
| Window | Plot list on the left, the plot in the middle, its properties on the right; Open in new window shows another plot beside it; dockable |
| Help | Overview, one tab per plot type (12), More details |
| Version | 1.4 |

## The Data Plot window

```
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ Layer [Parcels ▾]  Records: All records ▾   + New plot ▾  ⧉  🗑  ⤓ Export ? ▣ │
 ├──────────────┬──────────────────────────────────────┬─────────────────────┤
 │ Plots        │                                      │ Data  Axes  Guides  │
 │ ▸ Value vs   │            (the plot)                │ Format  General     │
 │   area       │                                      │                     │
 │   Sales per  │                                      │ X  [AREA_M2    ▾]   │
 │   month      │                                      │ Y  [LAND_VALUE ▾]   │
 │   Land use   │                                      │ Size by [ ... ▾]    │
 │              │                                      │ ☑ Trend line        │
 ├──────────────┴──────────────────────────────────────┴─────────────────────┤
 │ 12,480 records · 3 selected                                               │
 └───────────────────────────────────────────────────────────────────────────┘
```

- **Layer**: any vector layer, with or without geometry.
- **Records**: All records, Explorer filtered records (when an Attribute
  Explorer is open on the layer), Selected records or Visible map extent. The
  plot follows them live: a new filter, selection, map extent or edit redraws
  it. Big layers are read in the background with progress and Cancel in the
  status bar; QGIS stays usable.
- **New plot**: a list of the 12 types, each with its own icon (drawn for
  this plugin, in the style of its other icons). The new plot is added to the
  layer's list and opens with sensible first fields.
- **Plot list**: the plots made for this layer. Click to show one; rename,
  duplicate, delete, move up or down; **Open in new window** shows it in a
  second Data Plot window, so two plots can sit side by side.
- **Properties**: tabs as in ArcGIS Pro's Chart Properties: **Data** (the
  type's own options, below), **Axes**, **Guides**, **Format**, **General**.
  Every change redraws at once; only a new field or a new set of records
  reads the layer again.
- **Export**: PNG, SVG, copy the image, or the plotted numbers as CSV with
  the layer and records noted.
- **Saving**: every plot (type, fields, options, title) is saved in the QGIS
  project with its layer, and comes back when the project opens. **Save to
  file** and **Load from file** (JSON) move a plot to another project or
  layer with the same field names.
- **Dock**: like the other tools, as a window or docked into QGIS.

### Records and big layers

- Only the fields a plot uses are read, into compact columns (8 bytes a
  value, a small code per text value), as Visualize Statistics already does.
  Changing options, bins, classes, sorting or colours never reads the layer
  again.
- Numbers follow the rules the Explorer and Visualize Statistics already use:
  NULL is counted apart, NaN and infinity are invalid, the standard deviation
  is the population one. Where a type uses the same calculation as Visualize
  Statistics (bins, periods), it uses the same tested code so the numbers
  agree.
- Scatter Plot, Scatter Plot Matrix and QQ Plot draw every point while the
  plot stays readable. Past a limit that I measure in round 1, they shade each
  spot by how many records fall on it (a legend shows the shades). Nothing is
  dropped or sampled, and selecting still works on every record.

### Selection, both ways

- A click on a point, bar, slice, box, cell or wedge selects its records on
  the layer; **Ctrl+click** adds; a **drag** draws a box and selects
  everything inside it (points in Scatter, a range of bars or periods in Bar
  and Line). The map, the Attribute Explorer and other plots follow.
- Records selected anywhere (map, Explorer, another plot) are shown in the
  plot in the project's selection colour: selected points are drawn on top;
  bars, columns and slices show the selected part of each; cells and wedges
  holding selected records get a selection-colour outline and the selected
  count in their hover card.
- Right-click offers **Inspect in Attribute Explorer** for the records under
  the mouse.
- With Records set to Selected records, a selection made on the plot also
  changes the plot's records, as it would in ArcGIS Pro with its selection
  filter on.

## Options common to every plot

From ArcGIS Pro's Chart Properties tabs.

- **Axes**: minimum and maximum of each axis (the data's range by default,
  with Reset), **Log axis** for number axes, axis titles.
- **Guides**: add a horizontal or vertical guide at a value; a **to** value
  makes it a range; an optional label.
- **Format**: series colours (the colour-blind checked colours of the current
  charts by default, each one changeable), grid lines on or off, axis lines.
  The background and text follow the QGIS light or dark look.
- **General**: title, a description shown under the plot, axis titles (named
  from the fields and type until changed), legend on or off and its place.

## The 12 plot types

Options are from Esri's ArcGIS Pro help and ArcPy chart pages. Options I could
not confirm on those pages are left out.

### Round 1

**Scatter Plot**: the relationship between two number fields.
- X and Y: number fields.
- **Size by**: an optional third number field sizes each point (a bubble
  plot), with smallest and largest point sizes.
- **Colour**: the layer's own symbology colours (default, as in ArcGIS Pro,
  read from the layer's QGIS style), or one colour.
- **Trend line**: on or off; Linear, or the non-linear Exponential,
  Logarithmic, Power and Polynomial; shows R² and the equation, with a note
  that R² assumes a straight line for the non-linear fits.
- Many points: density shading as above.

**Line Chart**: values over time or along a number.
- X: a date or number field.
- **Aggregation**: Count, Sum, Mean, Median, Minimum, Maximum, of one or more
  number fields (one line each), or **Split by** a category field (one line
  per category).
- **Time binning**: interval size and unit (default from the data's time
  span), alignment to the first or last record or a reference time, **Trim
  incomplete interval**.
- **Empty bins**: Treat as zero, Connect line or Break line.

**Bar Chart**: a value per category.
- Category: a text, number or date field (dates binned as in Line Chart).
- **Aggregation**: Count, Sum, Mean, Median, Minimum, Maximum, of one or more
  number fields, or **Split by** a category field.
- **Display multiple series as**: Side by side (default), Stacked, 100%
  Stacked.
- **Sort**: by category or by value, ascending or descending.

### Round 2

**Combo Chart**: bars and lines on one X axis.
- X: a date, number or category field (dates binned as in Line Chart). By
  default the record count of each category or interval is a bar series on the
  left axis.
- **Series**: each series is a field and an aggregation, drawn as **bar** or
  **line**, on the **Left y-axis** or **Right y-axis**. Each axis scales to
  its own series; an axis holding a bar series starts at 0.
- **Empty bins**: Treat as zero or Treat as null.

**Pie Chart**: shares of a whole.
- Slices are the categories of a field (count, or an aggregation of a number
  field), or several number fields (one slice each).
- **Grouping threshold**: slices under this percentage are put together as
  **Other** (default 0%, every slice shown).
- **Donut**: the hole as a percentage of the pie (0 is a full pie).

**Histogram**: the distribution of one number field.
- **Number of bins**: the square root of the record count by default,
  changeable.
- **Mean**, **Median** and **Standard deviation** lines (one each side of the
  mean), each on or off with its colour.
- **Show Normal distribution**.
- **Transformation**: None, Logarithmic, Square root (a preview of the field
  made more normal).
- **Split by**: a category field gives one small histogram per category,
  several per row, with a larger preview of the one clicked.

**Box Plot**: spread and outliers.
- One or more number fields; with several, **Standardize values (z-score)**
  is on by default so they share one axis.
- **Split by**: a category field, one box per category for each field.
- **Show outliers**: points beyond 1.5 × the interquartile range from the box,
  or whiskers to the extremes when off.

### Round 3

**Scatter Plot Matrix**: every pair of several number fields.
- Number fields: two or more.
- **Lower left**: R² values with a colour gradient, scatter plots, or empty.
- **Upper right**: scatter plots, a **Preview plot** (a larger view of the
  cell clicked), or empty.
- **Diagonal**: histograms of each field, or the field names.
- Trend line in the small scatter plots.

**QQ Plot**: is a field normally distributed, or do two fields share a
distribution?
- X: a number field; Y: optional second number field. Without Y the field is
  compared with a normal distribution.
- **Reference line** on or off.
- **Transformation**: None, Logarithmic, Square root, Inverse, Box-Cox.

**Matrix Heat Chart**: a grid of two fields.
- Columns and rows: date, integer or text fields; a date field can be binned
  (interval, alignment, trim incomplete), separately for columns and rows.
- **Aggregation**: Count, or Sum, Mean, Median, Minimum, Maximum of a number
  field, which colours the cells.
- **Classification**: Equal interval, Geometric interval, Natural breaks,
  Quantile; number of classes; colour ramp (QGIS's colour ramps).

**Calendar Heat Chart**: values on a calendar.
- Date: a date field.
- **Calendar type**: Year by month and day of month, or Week by day of week
  and hour of day.
- **View**: Single (all years or weeks pooled into one grid) or Sequential
  (one grid per year or week).
- **Aggregation**: Count, or Sum, Mean, Median, Minimum, Maximum of a number
  field.
- **Include leap day**; classification and colour ramp as in Matrix Heat.

**Data Clock**: a year (or week, or day) as a clock.
- Date: a date field.
- **Rings and wedges**: Years and Months, Years and Weeks, Years and Days,
  Weeks and Days, Days and Hours.
- **Aggregation**: Count, Sum, Mean, Median, Minimum, Maximum.
- **Classification**: Equal interval, Geometric interval, Natural breaks,
  Quantile; number of classes; colour ramp.
- As in ArcGIS Pro, the last week of a year holds the extra day (two in a
  leap year).

## Round 4: Help and documents

- The Data Plot gets its own Help (its ? button), like the other tools:
  **Overview** (window, plot list, records, selection, export), one tab per
  plot type with a picture of that plot and its numbered properties, and
  **More details** with the full written help. Pictures use made-up data and
  are made by `tests/qgis/make_help_images.py`.
- README: a Data Plot section with screenshots (made-up data, light and
  dark).
- metadata.txt: version 1.4, the 1.4 changelog, the description and about
  text name four tools.

## How it is checked

- Unit tests for the numbers of every type (no QGIS needed): bins, periods,
  aggregations, trend lines and R², quartiles and outliers, QQ quantiles,
  classifications, clock and calendar binning, density shading.
- QGIS tests: the window, each type on memory and GeoPackage layers, live
  records, two-way selection, saving in the project and to a file, export,
  docking, on QGIS 4.4.0 and then QGIS 3.44.14.
- A big-layer test (made-up layer of millions of records) for read time,
  redraw time and memory, with the measured numbers written here.

## Not in this plan

- Charts on the map (pie or bar symbols per feature).
- Changing Visualize Statistics or the Pivot Table chart.
