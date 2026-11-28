# Plot editing mode: plan

Status: **built** as Rounds 6 to 8 of version 1.4 (dev-v5); see *Built*
at the end.

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
  legend cannot be edited or dragged at all: showing it and its place stay
  in Appearance (Automatic, right, under, hidden).
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
- **Styles are plot files**: *Save plot to file* already keeps a whole plot
  (type, settings, look) and *Load plot from file* makes it again on a layer
  of your choice; the file now also holds the canvas look and the added
  objects, so a plot file is the style to reuse.
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
| Each type's own parts (boxes, heat cells, calendar days, clock rings, matrix cells) | Not edited (decided): a click on them selects the canvas |

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
3. Plot files with the look and the objects (the style to reuse).
4. Help (an Edit plot tab with a numbered picture), README, screenshots;
   tests on QGIS 4.4 then 3.44 in every round.

## Built

As planned, with these choices:

- **Top row** (asked before the build): the records line was removed; Plots,
  then the Edit plot group (Edit plot, Save edits, Undo, Redo), then Add in
  a group of its own, each after a divider, at the left; Change scope (its
  tooltip shows the records), Clear selection, Export, Help and Dock at the
  right.
- **Kept per part**: the canvas look is kept in `plot["canvas"]["style"]`,
  one entry per part (`grid_h`, `series:<name>`, `line:trend:<field>`,
  `guide:<n>` ...), the objects in `plot["canvas"]["objects"]`; a part keeps
  the plot's own look for every setting not changed.
- **Bar width** is one setting for every series (the share of each
  category's space the bars take); outline and transparency are per series.
- **Mean, median and standard deviation lines** keep their colour in the
  card (beside their ticks): Edit plot sets their width and dash only.
- **Each type's own parts** (boxes, heat cells, calendar days, clock rings,
  matrix cells) are not edited, as decided after the build: a click on them
  selects the canvas.
- **Pictures** are kept inside the plot as PNG, their longest side at most
  1,600 px.
- **Undo** treats quick changes of one setting (typing, a spin box) as one
  step.
- **Ctrl+Z, Ctrl+Y, Delete and the arrow keys** belong to the plot while it
  is edited, so QGIS's own shortcuts do not take them.
- **Help**: an Edit plot tab (after Overview) with a numbered picture.

## Answers

1. The legend cannot be dragged at all.
2. A style is a whole plot, as *Save plot to file* and *Load plot from file*
   already do it (type, settings, look), made again on a chosen layer.
3. Its objects (text boxes, lines, arrows, pictures) come with it.
