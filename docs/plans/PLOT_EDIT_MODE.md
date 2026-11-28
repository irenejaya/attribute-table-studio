# Plot editing mode: discussion draft

Status: **draft for discussion, nothing built**. The open questions at the end
need answers before a build plan with rounds is written.

## The idea

Data Plot gets an **Edit plot** mode for the look of a plot. While it is on,
the data stop changing under you, and every part of the plot can be clicked:
a grid line, an axis, the title, the legend, a series. The settings card on
the right then shows the settings of the clicked part (for example the
width, colour and dash of the horizontal grid lines only). Free text can be
added and dragged anywhere on the plot. What is set in the mode is kept with
the plot.

## How it would work

1. **Turning it on.** An **Edit plot** button (pencil icon) beside Export in
   the top row. While it is on:
   - the button stays pressed;
   - a strip above the plot says *Editing the look · the data are frozen*,
     with **Add text** and **Done** in it;
   - hovering a part of the plot outlines it, and a click selects it (an
     outline with handles where the part can be moved);
   - clicks no longer select records.
2. **The settings card.** With a part selected, the card shows that part's
   settings only, under a header naming it ("Horizontal grid lines"). With
   nothing selected, it shows the usual settings. Esc or a click on an empty
   spot clears the selection.
3. **Moving.** The title, the legend and added text can be dragged. Arrow
   keys nudge a selected one by 1 px (Shift: 10 px).
4. **Free text.** **Add text**, then a click on the plot, places a text box
   there, ready for typing. Its settings: text (several lines), font, size,
   colour, bold, italic, alignment, an optional box behind it (fill and
   border) and rotation. Double-click edits the text in place; Delete removes
   it.
5. **Undo.** Ctrl+Z and Ctrl+Y undo and redo every change made in the mode.
6. **Leaving.** **Done** (or the Edit plot button again) ends the mode, and
   the plot follows its records again, keeping the look.

## What can be edited (proposal)

| Part | Settings |
| --- | --- |
| Whole picture | Background colour, outer margin |
| Plot area | Background colour, border (colour, width) |
| Title, description | Text, font, size, colour, bold, alignment; position by dragging |
| Axis titles (X, Y, right Y) | Text, font, size, colour |
| Axis labels (X, Y) | Size, colour, decimals, thousands separator; X label angle |
| Axis lines (X, Y) | Shown, colour, width |
| Grid lines, horizontal and vertical apart | Shown, colour, width, solid / dashed / dotted |
| Legend | Position by dragging, size of text, background, border, shown |
| A series (bar, line, slice, points) | Colour, transparency; line width and dash (lines), outline (bars, slices), point size and shape (scatter) |
| Trend line, reference line, mean and median lines | Colour, width, dash |
| Guides | Colour, width, dash, label size |
| Added text | As in step 4 |

Each type has some parts of its own (the boxes of a Box Plot, the cells of a
heat chart, the rings of a Data Clock), which would follow in a later round.

## Where it is kept

- In the plot's settings, so it is saved in the project, in a plot file
  (Save plot to file), and in PNG and SVG exports: a **style** part (one entry
  per part) and an **annotations** list (the added text).
- Added text and moved parts keep their place as a share of the plot area,
  so they stay in place when the window is resized. Text could instead be
  pinned to a value on the axes (it then moves with the data); see question 4.
- Settings that already exist in the card (titles, colours, grid on or off,
  legend position) stay one setting: Appearance and Edit plot change the
  same value, never two copies.

## Possible rounds (after the questions are settled)

1. The mode itself: hit testing of the parts, selecting, the card for a
   selected part, freezing, undo; whole picture, plot area, title,
   description, axes, grid lines, legend.
2. Added text: place, type, move, edit, delete; saved and exported.
3. Series, trend and reference lines, guides; the type's own parts.
4. Help (an Edit plot tab with a numbered picture), README, tests on QGIS 4.4
   then 3.44.

## Open questions

1. **What "frozen" means.** (a) The data freeze only while the mode is on and
   follow the records again after Done; or (b) a plot can stay frozen as a
   snapshot (Freeze / Unfreeze) whether or not you are editing?
2. **Where the mode lives.** A pressed Edit plot button in the top row, with a
   strip above the plot (proposed), or a separate editing window?
3. **Added items.** Only text to start with, or also arrows, lines and boxes
   (to point at or frame a part of the plot)? Images such as a logo?
4. **Placing text.** Kept as a share of the plot area (stays in place when the
   window or the axes change; proposed), or pinned to an X and Y value (moves
   with the data)? Or a choice per text box?
5. **Scope.** All 12 plot types from the start for the common parts (title,
   axes, grid, legend, text), with each type's own parts later (proposed)?
6. **A default look.** Should a look be savable as a style and applied to
   other plots (for example a house style for reports), or is a look per plot
   enough for now?
7. **Visualize Statistics and the Pivot Table chart.** Data Plot only, as you
   said, or should the other charts get the same mode later?
