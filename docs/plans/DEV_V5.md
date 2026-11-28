# dev-v5 plan: Data Plot

A new tool, **Data Plot**, that makes plots from a layer's records, with 12
plot types and the options ArcGIS Pro gives each of its charts. It is a tool
of its own, apart from Visualize Statistics and the Pivot Table chart, which
stay as they are.

| Round | Content | Status |
| --- | --- | --- |
| 1 | The Data Plot window, plus Scatter Plot, Line Chart and Bar Chart | Built |
| 2 | Combo Chart, Pie Chart, Histogram, Box Plot | Built |
| 3 | Scatter Plot Matrix, QQ Plot, Matrix Heat Chart, Calendar Heat Chart, Data Clock | Built |
| 4 | Help, README, screenshots, 1.4 changelog | In progress: README text, changelog, metadata done; Help pictures and screenshots wait for the layout choice |

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
| Window | Plot list on the left, the plot in the middle, its properties on the right; Open in new window shows another plot beside it; dockable; the same look as Visualize Statistics and the Pivot Table (properties in one card with section headers, no tabs) |
| Help | Overview, one tab per plot type (12), More details |
| Version | 1.4 |

## The Data Plot window

```
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ Layer [Parcels ▾]  Records: All records (3,000)   [Change scope ▾] ◩ ⤓ ? ▣ │
 ├──────────────┬──────────────────────────────────────┬─────────────────────┤
 │ Plots        │                                      │ Data ────────────── │
 │ ▸ Value vs   │            (the plot)                │ X-axis [AREA_M2  ▾] │
 │   area       │                                      │ Y-axis [VALUE    ▾] │
 │   Sales per  │                                      │ [+ Add Y field]     │
 │   month      │                                      │ Aggregation [Sum ▾] │
 │              │                                      │ Axes ────────────── │
 │ [+ New plot] │                                      │ Summary ─────────── │
 │ ⧉ ✕ ↑ ↓ ▣ ☰  │                                      │ Appearance ──────── │
 ├──────────────┴──────────────────────────────────────┴─────────────────────┤
 │ Bar Chart  •  5 bars  •  699 of 3,000 records selected                    │
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
  layer's list; its fields are chosen in Data, as in ArcGIS Pro.
- **Plot list**: the plots made for this layer. Click to show one; rename,
  duplicate, delete, move up or down; **Open in new window** shows it in a
  second Data Plot window, so two plots can sit side by side.
- **Properties**: one card, as the Visualize Statistics and Pivot Table
  chart cards, with a section for each part of ArcGIS Pro's Chart
  Properties: **Data** (the type's own options, below; an **X-axis** box and
  a **Y-axis** box wherever the type has both), **Time binning** (a date
  along X), **Statistics on the chart**, **Axes**, **Guides**, **Summary**
  and **Appearance** (ArcGIS Pro's Format and General). Every change redraws
  at once; only a new field or a new set of records reads the layer again.
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

From ArcGIS Pro's Chart Properties tabs, shown as sections of the card.

- **Axes**: minimum and maximum of each axis (the data's range by default),
  **Log axis** for number axes.
- **Guides**: add a horizontal or vertical guide at a value; a **to** value
  makes it a range; an optional label.
- **Format**: series colours (the colour-blind checked colours of the current
  charts by default, each one changeable), grid lines on or off, axis lines.
  The background and text follow the QGIS light or dark look.
- **General** (in Appearance, with Format): title, a description shown under
  the plot, axis titles (named from the fields and type until changed),
  legend on or off and its place (Automatic places it as Visualize
  Statistics does: inside the plot, or beside it when it lists series).

## The 12 plot types

Options are from Esri's ArcGIS Pro help and ArcPy chart pages. Options I could
not confirm on those pages are left out.

### Round 1

**Scatter Plot**: the relationship between two number fields.
- **X-axis** and **Y-axis**: number fields.
- **Size by**: an optional third number field sizes each point (a bubble
  plot), with smallest and largest point sizes.
- **Colour**: the layer's own symbology colours (default, as in ArcGIS Pro,
  read from the layer's QGIS style), or one colour.
- **Trend line**: on or off; Linear, or the non-linear Exponential,
  Logarithmic, Power and Polynomial; shows R² and the equation, with a note
  that R² assumes a straight line for the non-linear fits.
- Many points: density shading as above.

**Line Chart**: values over time or along a number.
- **X-axis**: a date or number field.
- **Y-axis**: Count of records, or a numeric field; **Add Y field** adds more
  (one line each). **Aggregation** of the Y fields: Sum, Mean, Median,
  Minimum, Maximum. Or **Split by** a category field (one line per category,
  with one Y field).
- **Time binning**: interval size and unit (default from the data's time
  span), alignment to the first or last record or a reference time, **Trim
  incomplete interval**.
- **Empty bins**: Treat as zero, Connect line or Break line.

**Bar Chart**: a value per category.
- **X-axis**: a text, whole number or date field (dates binned as in Line
  Chart).
- **Y-axis**, **Add Y field**, **Aggregation** and **Split by** as in Line
  Chart.
- **Bars** (ArcGIS Pro's Display multiple series as): Side by side (default),
  Stacked, 100% stacked.
- **Sort**: A → Z, Z → A, Smallest value first, Largest value first (worded
  and drawn as in Visualize Statistics).

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

## Round 1 built

The Data Plot window with Scatter Plot, Line Chart and Bar Chart, as planned,
with these choices made while building:

- The window takes the look of the other tools: the properties are one card
  with section headers (Data, Time binning, Statistics on the chart, Axes,
  Guides, Summary, Appearance) and "Label [box]" rows, as the Visualize
  Statistics and Pivot Table chart cards, rather than ArcGIS Pro's tabs; the
  top row is the Pivot Table's (Layer, the records, Change scope, then the
  square buttons); the plot list sits in a card like Build your table, with
  New plot under it.
- Line and Bar Chart have an **X-axis** box and a **Y-axis** box; the first
  Y-axis box also offers Count of records, and **Add Y field** adds a series,
  each with a remove button, as the Pivot Table's values.
- A new plot starts with its fields empty and asks for them (as ArcGIS Pro
  does), so it never opens on an ID field with thousands of categories.
- Bars with a Mean, Median, Minimum or Maximum have no selected part; a strip
  in the selection colour at the bar's base marks a bar that holds selected
  records. Count and Sum bars show their selected part.
- A Bar Chart draws at most 1,000 bars (in the chosen order); the status bar
  says how many records the others hold.
- Split by keeps the rule of Visualize Statistics: the 8 categories with the
  most records get their own colour, the rest form an exact Other.

Measured on this container (headless QGIS 4.4.0, a 1,000 x 700 plot):

| Scatter points | Drawn one by one | Shaded |
| --- | --- | --- |
| 10,000 | 18 ms | 13 ms |
| 50,000 | 61 ms | 15 ms |
| 100,000 | 113 ms | 15 ms |
| 200,000 | 228 ms | 16 ms |
| 500,000 | 514 ms | 29 ms |
| 1,000,000 | | 55 ms |
| 5,000,000 | | 288 ms |

So a scatter plot draws every point up to 100,000 points (about 0.1 s) and
shades above that. Drawing each point from a ready-made image, rather than
painting each circle, took 100,000 points from 2.9 s to 0.11 s. Hovering
never redraws the plot: it is kept as an image.

On a made-up GeoPackage table of 1,000,000 records (5 fields), each plot was
ready in 2.8 to 4.7 s (Scatter Plot with a trend line 3.1 s, Line Chart of a
monthly mean 4.7 s, Bar Chart of medians split by a 51-value field 2.8 s);
QGIS never paused for more than 68 ms meanwhile. Changing the trend type took
0.2 s with no new read, and showing a selection of 100,000 records 0.3 s.

On the same table of 10,000,000 records:

| Plot | Ready in | Longest pause of QGIS | Peak memory of QGIS |
| --- | --- | --- | --- |
| Scatter Plot with a trend line | 44 s | 37 ms | 1.4 GB |
| Line Chart, mean per month | 68 s | 131 ms | 1.8 GB |
| Bar Chart, medians split by a 51-value field | 37 s | 57 ms | 1.8 GB |

Changing the trend type took 1.8 s with no new read; showing a selection of
1,000,000 records took 1.0 s. A plot reads two or three fields where
Visualize Statistics reads one (18 to 34 s at 10 million records), and the
Line Chart's dates cost the most to read; reading dates faster is left for a
later round.

## Round 2 built

Combo Chart, Pie Chart, Histogram and Box Plot, as planned, with these
choices made while building:

- Attribute Explorer has a **Data Plot** button in its top bar, between
  Visualize Statistics and Pivot Table: an icon with a menu (like Export, so
  the row keeps its labels) that lists the 12 plot types; picking one opens
  Data Plot on the layer, with the Explorer's filtered records and a new plot
  of that type. New plot in Data Plot lists the 12 types too; the types of
  round 3 are greyed out until they are built.
- **Combo Chart**: the X-axis box, then one row of boxes per series (Y-axis:
  Count of records or a number field, its aggregation, Bar or Line, Left or
  Right y-axis) and **Add series**. Categories come A → Z. "Treat as zero"
  makes an interval without records 0; a category whose records have no value
  (NULL) has no value either way.
- **Pie Chart**: slices largest first, clockwise from 12 o'clock; the share of
  each in the legend and the hover card (no text on the slices, as the
  unconfirmed slice labels were left out). Other is exact: a mean of Other is
  the mean of its records. A selection fills its share of a Count or Sum
  slice; for the other aggregations the slice's rim shows it.
- **Histogram**: Auto bins are the square root of the record count, at most
  64 (the limit Esri's Spanish help gives); a bin runs from its lower edge up
  to its upper edge, the last one including the maximum. The logarithmic
  transformation is the natural logarithm. With Split by, the small
  histograms share the bins and the scale; ArcPy's mini charts per row and
  preview chart are there, and a click on a small histogram's title shows it
  in the preview.
- **Box Plot**: the X-axis box is the category (None: one box per field), the
  Y-axis boxes the number fields (**Add Y field**). Quartiles use linear
  interpolation between the closest ranks. A box with selected records gets
  an outline in the selection colour; selected outliers turn that colour.

## Round 3 built

Scatter Plot Matrix, QQ Plot, Matrix Heat Chart, Calendar Heat Chart and Data
Clock, as planned; all 12 types are now in New plot and in Attribute
Explorer's Data Plot menu. Choices made while building:

- **Scatter Plot Matrix**: Field rows with **Add field**; R² of each pair on
  the records with both values, shaded by its strength. The preview plot
  fills the upper right corner; a click on a small plot or an R² cell shows
  that pair in it. Small plots draw up to 4,000 points one by one (the
  preview up to 20,000) and shade above that.
- **QQ Plot**: the field's sorted values against the standard normal
  quantiles at the proportions (i - 0.5) / n, or two fields' values at the same proportions
  (each point is then a record of the field with fewer values). The reference
  line runs through the first and third quartiles. Box-Cox's lambda is the
  one between -2 and 2 that makes the field most normal (largest
  log-likelihood, on at most 100,000 values). Both come from SciPy when QGIS
  has it (QGIS 3.44.16 and later ship it) and from the plugin's own
  calculation otherwise; the tests check that the two agree. The boxes are named **Field**
  and **Compare with**, as the plot has no X field and Y field of its own.
- **Matrix Heat Chart**, **Calendar Heat Chart**, **Data Clock**: the cell
  colours come from a **Classification** section: Equal interval, Geometric
  interval (class widths growing by a constant ratio), Natural breaks (Jenks,
  on a sample of at most 1,000 values) or Quantile, 2 to 10 classes, and any
  colour ramp of the QGIS default style (Viridis to start). Cells without
  records stay empty. A sequential calendar draws at most 60 grids (the
  latest), a data clock at most 200 rings.

## Polish after Round 3

- **One label column per card**: the labels of a settings card share one
  width (the widest label), so every box and dropdown starts and ends at the
  same place; done in Data Plot, Visualize Statistics and the Pivot Table
  chart. Long labels are shortened, the full wording in the tooltip:
  Aggregation → Aggregate, Transformation → Transform, Calendar type →
  Calendar, Rings and wedges → Rings, Grouping threshold → Threshold, Mini
  charts per row → Per row, Colour ramp → Ramp, X / Y axis title → X / Y
  title, Right Y min / max → Right min / max, Compare with → Compare. Colour
  buttons are as wide as the boxes above them.
- **Option icons**: trend line types, transformations and the Bars choices
  (side by side, stacked, 100% stacked) show a small painted picture of their
  shape, also in Visualize Statistics and the Pivot Table chart.
- **Layout of the plot list**: two mock-ups shown (A: a Plot box in the top
  row with the list's actions in a menu; B: a Plots button that hides the
  list card); the choice is open.

## Not in this plan

- Charts on the map (pie or bar symbols per feature).
- Changing Visualize Statistics or the Pivot Table chart.
