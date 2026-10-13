# Changelog

## 0.7.0

### Interface
- New panel design: monospaced type, hairlines, chamfered panels, one
  signal colour for data-quality issues; light and dark themes that follow
  QGIS (or forced from the options menu).
- Table and detail chart side by side (master–detail) instead of two
  pages; the redundant field list was merged into the table.
- Sortable columns (by real value), field filter, type badges, `KEY`
  badge for candidate keys, striped null bars and sparklines painted
  natively (no more one matplotlib figure per field).
- Header readout: features, fields, scope and scan time.
- Right-click menu on cells: select / add / remove, flash, open in the
  attribute table, symbolize, copy value, copy the whole table.
- Ctrl+click adds to the selection, Ctrl+Shift+click removes.
- Options: theme, zoom / pan / don't move the map, flash, outlier rule
  (k = 1.5 or 3), restore the original layer style.
- Spanish translation.

### Profiling
- About 2× faster: batched processing with numpy / Counter, attributes
  read by index.
- Cancelable from the panel (`STOP`); fields appear instantly while the
  statistics are computed.
- Results cached per layer and marked stale when the layer is edited,
  committed or filtered; banner to refresh, or `AUTO` refresh.
- `SEL`: profile only the selected features, following the selection.
- Current map selection overlaid on the detail chart.
- Dates, datetimes and times are profiled as such (min, max, mean,
  median, histogram with dates on the axis) and selectable; booleans too.
- Heavy-tailed fields: histogram focused on the bulk of the data with
  selectable `<` / `>` overflow bars; at most 40 bins.
- Integer fields with a small range get one bar per value.
- Sampled histograms span the true min/max and are scaled to the full
  population.
- Frequent categories are never lost on very high-cardinality fields
  (Misra–Gries), whatever their position in the layer.
- Outliers are not reported when IQR = 0 (more than half the values
  identical), with an explanation in the tooltip.

### Fixes
- Feature count is the number of features actually read (it was
  `featureCount()`, which can be -1 or an estimate).
- Possible crash when a running profile was cancelled or the layer
  changed (the task could be garbage-collected while running).
- Possible crash / error after reloading the plugin when layers were
  added or removed.
- Ghost toolbar left behind after each plugin reload; translator not
  removed on unload.
- Large numbers no longer shown in scientific notation; numbers follow
  the QGIS locale.
- Field names and values are quoted properly in selection expressions.
- Min / max selection uses `<=` / `>=`, robust for decimal columns.
- A minimum of 0 is now shown as clickable.
- Symbolize-by-field no longer overwrites your style: it is applied in a
  separate layer style and can be undone; the light end of the ramp is
  no longer white.

### Project
- Tests (pytest) for the statistics, the engine against real QGIS layers
  and the panel; GitHub Actions on QGIS LTR and QGIS 4.
- `metadata.txt`: repository and tracker URLs, QGIS 4 support
  (`supportsQt6`), no Processing provider declared until there is one.
- GPL-2.0-or-later `LICENSE`.

## 0.6.0
- Single-pass streaming profiler in a background `QgsTask`.

## 0.4.0
- Statistics grid for all fields, thumbnails, detail chart, symbology button.
