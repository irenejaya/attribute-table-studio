# Plots that keep their own layer: plan

Status: **being built** as Round 5 of version 1.4 (dev-v5); questions
answered.

## Now

Plots are kept per layer. The **Layer** box at the top of the window chooses
the layer, and the list shows only that layer's plots: picking another layer
does not delete the others, but they disappear from the list until their
layer is picked again. The records choice (*Change scope*) belongs to the
window, not to a plot.

## The idea

Data Plot becomes a manager of plots: the list holds every plot of the
project, each plot keeps the layer it was made from, and the layer is chosen
per plot in the settings card, like its fields.

## Proposal

- **The list**: every plot of the project, in the order you put them. Each
  row: the type icon, the plot's name and, dimmed after it, its layer
  ("Score by land use · Sample parcels"); the layer in the tooltip too.
- **The top row**: the Layer box goes. The row keeps Plots, the records
  line and *Change scope*, Clear selection, Export, the edit group, Help and
  Dock.
- **The card**: **Layer** becomes the first row of Data. Changing it keeps
  the fields that have the same names on the new layer; the others go back
  to *Choose a field…*, and the status bar names them.
- **Records per plot**: All, Explorer filtered, Selected or Visible map
  extent is kept with each plot; the top row shows and changes the records
  of the plot shown.
- **New plot**: on the layer of the plot shown (the active layer when the
  list is empty); the layer can then be changed in the card.
- **Attribute Explorer's Data Plot button**: adds a new plot of that layer
  with the Explorer's filtered records to the list and shows it (as now).
- **A layer leaves the project**: its plots stay in the list, marked *layer
  not in the project* (greyed, with a warning icon). Choosing a layer in the
  card brings one back (fields by name); or delete it.
- **Plot files**: *Save plot to file* records the layer's name; *Load plot
  from file* adds it to the list on the layer of the plot shown, and the
  layer can be changed in the card.
- **Windows**: the window is titled *Data Plot*; *Open in new window* shows
  any plot of the list.
- **Projects saved before**: their plots (kept per layer) are moved into the
  one list when the project opens, each on its layer.

## Effort and order

One round: the list and storage (with the move of older projects), the Layer
row in the card, records per plot, missing layers, the Explorer button, plot
files; tests on QGIS 4.4 then 3.44; Help pictures (Overview), README and
screenshots. It changes how every plot finds its layer, so it is best done
before the plot editing mode, which builds on it.

## Answers

1. Each plot in the list has its own layer, and plots can have different
   layers: one list, the layer after each name.
2. Records are kept per plot.
3. Built in 1.4, before its release.
