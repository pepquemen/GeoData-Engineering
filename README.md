# Data Engineering Toolkit (QGIS)

A data-profiling panel for QGIS vector layers, inspired by the **Data
Engineering** view in ArcGIS Pro — with everything connected to the map:
click a statistic or a chart bar and the matching features are selected.

- **Every field at once**: type, nulls, distinct values (and candidate
  keys), min, max, mean, median, standard deviation, P25/P75, sum, range
  and outliers, plus a distribution sparkline per field.
- **One click to the map**: nulls, minimum, maximum, outliers and any
  histogram or category bar select their features (Ctrl adds, Ctrl+Shift
  removes). Double-click or right-click to open them in the attribute
  table, flash them, or copy values.
- **Brushing & linking**: your current map selection is drawn over the
  detail chart, so you can see where it falls in the distribution.
- **Built for big layers** (e.g. cadastral data): background, cancelable,
  single-pass profiling with bounded memory; results cached per layer and
  flagged when the layer is edited (optional auto-refresh).
- **Every common type**: integers, decimals, text, booleans, dates,
  datetimes and times.
- **Light and dark themes** that follow QGIS, English and Spanish.

## Using it

Open the panel from the toolbar icon or *Plugins → Data Engineering
Toolkit*. It docks at the bottom, next to the attribute table.

| Control | What it does |
|---|---|
| Layer selector | Layer to profile (vector layers and tables) |
| Filter fields | Filters the table by field name or alias |
| `SEL` | Profile only the selected features; follows the map selection |
| `AUTO` | Re-profile automatically when the layer data changes |
| `REFRESH` / `STOP` | Profile again / cancel a running profile |
| Options (≡) | Theme, what the map does when selecting (zoom / pan / nothing, flash), outlier rule (k = 1.5 or 3), copy the table, restore the layer style |

In the table, cells with a small corner tick are clickable. Click a
column header to sort by that statistic. Hover a field name and click
the bars icon to symbolize the layer by that field; it is applied as a
new layer style (`DET · <field>`), so your original style is kept and can
be restored from the options menu or the layer's *Styles* menu.

A `~` in front of a value means it was estimated from a random sample
(see *How statistics are computed*). Selections are always exact.

## How statistics are computed

- Features are read **once**, without geometry, in a background
  `QgsTask`. Values are buffered and processed in batches with numpy.
- **Exact**: feature count, nulls, min, max, sum, mean and standard
  deviation (Welford / Chan merge, numerically stable for large
  magnitudes such as projected coordinates).
- **Distinct values**: exact up to 20 000, then reported as `>20 000`.
- **Median, percentiles, histogram and outlier count**: from a uniform
  random sample of up to 50 000 values per field (reservoir sampling,
  Algorithm L); exact when the field has fewer values. Histogram heights
  are scaled back to the full population.
- **Outliers**: Tukey fences (`Q1 − k·IQR`, `Q3 + k·IQR`). When more than
  half of the values are identical (IQR = 0) they are not reported, since
  every other value would be flagged.
- **Heavy tails**: when the fences cover less than half of the range, the
  histogram shows the bulk of the data plus `<` / `>` overflow bars,
  which are selectable too.
- **Categories**: exact counts up to 500 categories; beyond that the
  Misra–Gries algorithm keeps every frequent category whatever its
  position in the layer (counts become lower bounds, shown with `≥`).

## Structure

```
data_engineering_toolkit/
├── __init__.py                 # classFactory(), QGIS entry point
├── metadata.txt
├── data_engineering_plugin.py  # lifecycle: toolbar action, dock, translator
├── data_engineering_dock.py    # the panel (UI logic only)
├── profiling_engine.py         # QGIS side: field kinds, single-pass read
├── profiling_task.py           # background QgsTask wrapper
├── stats_core.py               # pure streaming statistics (no QGIS / Qt)
├── selection_helper.py         # kind-aware expressions, selection, zoom
├── symbology_helper.py         # non-destructive "explore by field" styles
├── compat.py                   # QGIS 3.28+ / QGIS 4 (Qt6) shims
├── translation.py              # tr()
├── ui/
│   ├── theme.py                # colour tokens, fonts, stylesheet, matplotlib style
│   ├── widgets.py              # chamfered frame, progress, banner, menus
│   ├── stats_model.py          # table model + filter proxy
│   ├── delegates.py            # badges, null bars, sparklines
│   ├── chart_panel.py          # detail panel and interactive chart
│   ├── formatting.py           # locale-aware numbers, dates, durations
│   ├── icons.py                # theme-coloured line icons
│   └── settings.py             # persisted preferences
├── fonts/                      # JetBrains Mono (SIL OFL 1.1)
├── i18n/                       # .ts sources and compiled .qm
├── icons/icon.svg
├── scripts/build_translations.py
└── tests/                      # pytest: statistics, engine, panel
```

## Development

1. Clone into your QGIS profile's plugins folder **with the folder name
   `data_engineering_toolkit`** (a name with dashes is not a valid Python
   module, so QGIS would not load it):

   ```bash
   cd "%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins"   # Windows
   git clone https://github.com/pepquemen/GeoData-Engineering.git data_engineering_toolkit
   ```

2. Enable it in *Plugins → Manage and Install Plugins → Installed*.
   [Plugin Reloader](https://plugins.qgis.org/plugins/pluginreloader/)
   reloads it after each change without restarting QGIS.

### Tests

Run with the Python interpreter that has the QGIS bindings (OSGeo4W
Shell on Windows, or the system Python on Linux):

```bash
python -m pip install pytest
QT_QPA_PLATFORM=offscreen python -m pytest
```

`tests/test_stats_core.py` needs only numpy; the other tests use a real
QGIS and run headless. GitHub Actions runs them against the QGIS LTR
(Qt5) and the current stable release (QGIS 4, Qt6).

### Translations

Source strings are English, wrapped in `tr()`. After changing UI text:

```bash
python scripts/build_translations.py
```

It adds new strings to `i18n/data_engineering_toolkit_es.ts` (translate
them there, e.g. with Qt Linguist) and compiles the `.qm` files with
`lrelease`.

## Dependencies

`numpy` and `matplotlib`, both bundled with QGIS on Windows and macOS.
On Linux, install your distribution's `python3-matplotlib` if missing.

## License

GPL-2.0-or-later (see `LICENSE`). The bundled JetBrains Mono font is
licensed under the SIL Open Font License 1.1 (`fonts/OFL.txt`).
