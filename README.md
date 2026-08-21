# Data Engineering Toolkit (QGIS)

A QGIS plugin inspired by the **Data Engineering** view in ArcGIS Pro: a
dockable panel with a field list, a statistics table and interactive
charts, with the twist that you can click a statistic (nulls, min, max,
outliers) or a chart bar to **select the corresponding features on the
map**.

## Structure

```
data_engineering_toolkit/
├── __init__.py                 # classFactory(), QGIS entry point
├── metadata.txt                 # plugin metadata (name, version...)
├── data_engineering_plugin.py   # plugin lifecycle (initGui/unload)
├── data_engineering_dock.py     # UI: splitter (field list | stats table + chart)
├── profiling_engine.py          # pure logic: single-pass streaming statistics, IQR outliers, histogram
├── profiling_task.py            # runs profiling_engine on a background QgsTask (progress bar, non-blocking UI)
├── selection_helper.py          # reusable feature selection (table and chart)
├── symbology_helper.py          # "explore by field" graduated / categorized renderers
└── icons/icon.svg
```

## Installing for development

1. Locate your QGIS profile's plugins folder:
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
   - Linux/macOS: `~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/`
2. Copy (or symlink, better for development) the whole
   `data_engineering_toolkit` folder into that `plugins` path.
3. Open QGIS → **Plugins → Manage and Install Plugins** → "Installed"
   tab → tick the checkbox for "Data Engineering Toolkit".
4. A toolbar icon and a Plugins-menu entry will appear that toggle the
   panel, which docks at the bottom of the window (same area as the
   native Attribute Table).

For iterative development, install the
[Plugin Reloader](https://plugins.qgis.org/plugins/pluginreloader/)
plugin, which lets you reload this plugin without restarting QGIS after
each change.

## Dependencies

- `numpy` (for histograms and outlier detection) — normally already
  bundled with QGIS's Python.
- `matplotlib` — also usually bundled with QGIS, but if your install is
  missing it: `python3 -m pip install matplotlib` using the Python
  interpreter QGIS uses (on OSGeo4W: `OSGeo4W Shell` → `py3_env` →
  `pip install matplotlib`).

## Current status (v0.6.0)

### Performance on large layers (e.g. cadastral data)

Profiling runs on a background `QgsTask` (`profiling_task.py`), so the
QGIS UI is never blocked, however large the layer is — a progress bar in
the panel header shows it's working. Reading features happens through a
`QgsVectorLayerFeatureSource` cloned on the main thread before the task
starts, which is the QGIS-documented safe way to read a layer's features
from a worker thread.

`profiling_engine.profile_all_fields()` computes every field's
statistics in a **single pass** over the layer (previously it was one
pass per field, and before that even one pass per statistic). Geometry
is never read. Running statistics use streaming/online algorithms so
memory stays bounded regardless of layer size: Welford's algorithm for
mean/standard deviation (numerically stable, unlike a naive sum-of-
squares — matters for fields with large values like cadastral
coordinates), reservoir sampling (`HISTOGRAM_SAMPLE_LIMIT`, 50,000
values by default) for percentiles/histogram/outlier estimates, and a
capped set (`DISTINCT_TRACK_LIMIT`) / capped category dict
(`MAX_CATEGORIES_TRACKED`) for distinct-value tracking. Feature selection
(nulls/min/max/outliers) is resolved lazily from a QGIS expression at
click time, so profiling never has to build a feature-id list per value.

For very large fields, percentiles/outlier counts/charts are estimated
from the sample rather than exact — the UI says so via a message-bar
notice. Counts, nulls, min and max are always exact (computed from every
feature, not the sample). Clicking a stat cell to select features is
always exact too, since it re-evaluates the expression against the whole
layer at that moment rather than reusing the sample.


Implemented:
- Vector layer selector + field list, docked at the bottom of the QGIS
  window (same area as the native Attribute Table).
- Statistics grid showing every field of the active layer at once: one
  row per field, one column per statistic (Field Type, Count, Null
  Count, Distinct, Sum, Min, Max, Range, Mean, Median, Std Dev, P25,
  P75, Outliers), plus a small chart-preview thumbnail column. Clicking
  a stat cell (nulls/min/max/outliers) selects the matching features on
  the map.
- Clicking a thumbnail opens a full-size interactive chart on a second
  page (histogram for numeric fields, bar chart for categorical ones),
  with a "Back to statistics" button to return to the grid. Clicking a
  bar there also selects the matching features on the map.
- Profiling logic decoupled from the UI (`profiling_engine.py`), ready
  to be reused by a future Processing algorithm (batch mode across
  several layers).
- Hovering a field in the left field list reveals a Symbology button.
  Clicking it renders the active layer by that field: a white -> dark
  blue graduated (quantile) renderer for numeric fields, or a
  categorized renderer with one distinct hue per value for text fields
  (capped at 40 categories for readability; see `symbology_helper.py`).

Suggested next steps:
- Color indicator in the field list based on % of null values.
- Profiling cache with invalidation on layer edits (`committedAttributeValuesChanges`,
  `featuresAdded`, `featuresDeleted` signals).
- Data-cleaning tools (fill nulls, remove duplicates, fix geometries) as
  Processing algorithms, launchable from the table.
- Export the profiling report to HTML/PDF.
- Packaging with `pb_tool` or `qgis-plugin-ci` for publishing to the
  official plugin repository.
