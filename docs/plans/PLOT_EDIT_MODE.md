# Plot editing mode: plan

Status: **plan, nothing built**. Decisions below are the user's answers to the
first draft; the questions at the end are still open.

## The idea

Data Plot gets an **Edit plot** mode for what is drawn inside the plot
canvas (the area inside the axes). While it is on, the plot and its settings
are frozen, things in the canvas can be clicked and their look changed in
the right panel, and objects can be added: text boxes, horizontal and
vertical lines, arrows and pictures. Everything else (fields, titles, axes,
legend, colours and shapes) stays where it is now, in the settings card,
and is not touched by the mode.

## Decisions

- **In the Data Plot window**, not a separate window.
- **Its own group in the top row**, after Export with a divider between
  them: **Edit plot** (pencil, stays pressed while on), **Save edits**,
  **Undo**, **Redo** and **Add ▾** (Text box, Horizontal line, Vertical line,
  Arrow, Picture…). Like the editing buttons of Attribute Explorer, the
  others are greyed out until Edit plot is on.
- **Frozen while editing**: the plot no longer follows its records, and the
  settings card's usual settings (fields, records, legend, titles, axes,
  colours) cannot change; the card shows only the settings of what is
  selected in the canvas. Leaving the mode unfreezes both.
- **Only the canvas is edited.** Titles, axis titles, axis labels and axis
  lines are not editable or movable in the mode: they stay in the card. The
  legend is neither edited nor dragged: showing it and its place stay in
  Appearance (Automatic, right, under, hidden).
- **No setting twice.** Anything the card already sets (series colours,
  point shapes, grid lines on or off, guides' values and labels, trend
  settings) stays in the card only; the mode adds what the card does not
  have.
- **Added objects stay inside the canvas** and can be dragged anywhere in
  it; their place is kept as a share of the canvas, so they stay put when the
  window is resized or the axes change. Horizontal and vertical lines are
  drawing objects, not tied to an axis value.
- **Drawing order**: the data, then the added objects, then the legend when
  it is inside the plot.
- **Styles**: a look can be saved as a style and used on other plots (see
  question 2).
- **Data Plot only**; Visualize Statistics and the Pivot Table chart keep
  their look.

## What the mode edits (canvas only)

| Part | Settings in the mode |
| --- | --- |
| Canvas | Background colour and transparency |
| Grid lines, horizontal and vertical apart | Colour, width, solid / dashed / dotted (on or off stays in Appearance) |
| Lines of a series (line, combo) | Width, dash, a marker on each point |
| Bars and slices | Outline colour and width, transparency, bar width |
| Points (scatter, QQ, matrix) | Size (when not Size by), transparency, outline |
| Trend, reference, mean, median and normal curve lines | Colour, width, dash |
| Guides | Colour, width, dash (value and label stay in the card) |
| Each type's own parts (boxes, cells, rings) | In a later round |

## Objects that can be added

| Object | How it is placed | Settings |
| --- | --- | --- |
| Text box | Add, then click in the canvas; drag to move, handles to set its width (the text wraps) | Text (several lines), font, size, colour, bold, italic, underline, alignment; box fill colour and transparency, border colour, width and dash, rounded corners, padding |
| Horizontal line | Across the whole canvas width; drag up or down | Colour (a default, changeable), width, dash, transparency |
| Vertical line | Across the whole canvas height; drag left or right | Colour, width, dash, transparency |
| Arrow | Add, then drag from where it starts to where it points; handles at both ends | Colour, width, dash, head at the end, the start or both, head size |
| Picture | Add, then choose a PNG, JPG or SVG file; drag to move, handles to resize (keeping its proportions) | Transparency, border; the picture is kept inside the plot (so the project needs no file beside it), up to a size limit |

Common to all: click to select (handles show), Delete removes, arrow keys
nudge (Shift: 10 px), right-click for Duplicate, Bring to front, Send to
back, Delete. Ctrl+Z and Ctrl+Y undo and redo.

## Saving

- **Save edits** keeps the changes made so far and stays in the mode.
  Turning Edit plot off with unsaved changes asks: Save, Discard or Cancel,
  as QGIS asks when a layer leaves editing.
- The look and the objects are kept in the plot's settings: saved in the
  project, in plot files, and drawn in PNG and SVG exports.

## Possible rounds

1. The mode: the edit group, freezing, selecting parts of the canvas, the
   card for a selected part, Save, Undo and Redo; canvas background, grid
   lines, series lines, bars, points, trend and other lines, guides.
2. Objects: text box, horizontal and vertical lines, arrows, pictures.
3. Styles: save a look as a style, apply it to other plots.
4. Help (an Edit plot tab with a numbered picture), README, screenshots;
   tests on QGIS 4.4 then 3.44 in every round.

## Open questions

1. **The legend**: neither edited nor dragged in the mode (its place stays in
   Appearance)? The answer said both "cannot be dragged" and "just drag".
2. **What a style holds**: (a) only the look of the canvas (grid, lines,
   backgrounds, series widths), applied to any plot of any layer; or (b) a
   whole plot made into a template (type, settings, look, objects), from
   which a new plot is made by choosing a layer and its fields? Today's
   *Load plot from file* is close to (b).
3. **Objects in a style**: when a style is applied, do its text boxes,
   lines, arrows and pictures come too, or only the look?
