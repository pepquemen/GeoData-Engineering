# -*- coding: utf-8 -*-
"""
data_engineering_dock.py
-------------------------
UI for the Data Engineering Toolkit panel, docked at the bottom of the
QGIS window (same area as the native Attribute Table), inspired by the
Data Engineering view in ArcGIS Pro.

Layout (two stacked pages):

  STATS PAGE (shown by default):
    [ layer selector                                                 ]
    [ field   |  one row per field, one column per statistic,        ]
    [ list    |  plus a small chart-preview thumbnail column.        ]
    [ (left)  |  Clicking a stat cell (nulls/min/max/outliers)       ]
    [         |  selects features on the map. Clicking a thumbnail   ]
    [         |  opens the full chart on the DETAIL PAGE.            ]

  DETAIL PAGE (opened on demand):
    [ < Back to statistics        Field: <name>                     ]
    [ large interactive chart (click a bar -> select features)       ]

This class only contains UI logic; statistics are computed in
profiling_engine.py and selections are performed in selection_helper.py.
"""

from qgis.core import QgsApplication, QgsMapLayerProxyModel
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QSize, pyqtSignal
from qgis.PyQt.QtGui import QImage, QPixmap, QIcon
from qgis.PyQt.QtWidgets import (
    QDockWidget,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QTableWidget,
    QTableWidgetItem,
    QLabel,
    QPushButton,
    QToolButton,
    QHeaderView,
    QAbstractItemView,
    QStackedWidget,
    QProgressBar,
)

from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg
try:
    # Unified backend (matplotlib >= 3.5), adapts to whichever Qt binding
    # is active (PyQt5/PyQt6), matching the QGIS build in use.
    from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
except ImportError:  # older matplotlib bundled with QGIS
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from . import profiling_engine
from . import profiling_task
from . import selection_helper
from . import symbology_helper

# Column layout of the "all fields" table: an explicit Field name column,
# every statistic from the numeric set (the superset; non-numeric fields
# just show N/A on the columns that don't apply to them), and a trailing
# thumbnail chart column.
FIELD_COLUMN = ('field', 'Field', False)
CHART_COLUMN = ('chart', 'Chart', False)
TABLE_COLUMNS = [FIELD_COLUMN] + profiling_engine.NUMERIC_STAT_COLUMNS + [CHART_COLUMN]

THUMBNAIL_SIZE = QSize(90, 36)


class FieldListRow(QWidget):
    """Row widget used inside the field list: shows the field name and,
    only while the mouse is hovering over the row, a small Symbology
    button that lets the user render the layer by that field."""

    symbology_clicked = pyqtSignal(str)  # emits the field name

    def __init__(self, field_name, display_text=None, parent=None):
        super().__init__(parent)
        self.field_name = field_name

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 2, 4, 2)

        self.label = QLabel(display_text if display_text is not None else field_name)
        layout.addWidget(self.label)
        layout.addStretch()

        self.symbology_button = QToolButton()
        icon = QgsApplication.getThemeIcon('propertyicons/symbology.svg')
        if icon.isNull():
            self.symbology_button.setText('...')
        else:
            self.symbology_button.setIcon(icon)
        self.symbology_button.setAutoRaise(True)
        self.symbology_button.setToolTip(f'Symbolize the layer by "{field_name}"')
        self.symbology_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.symbology_button.setVisible(False)
        self.symbology_button.clicked.connect(lambda: self.symbology_clicked.emit(self.field_name))
        layout.addWidget(self.symbology_button)

    def enterEvent(self, event):
        self.symbology_button.setVisible(True)
        super().enterEvent(event)

    def leaveEvent(self, event):
        self.symbology_button.setVisible(False)
        super().leaveEvent(event)


class DataEngineeringDock(QDockWidget):

    def __init__(self, iface, parent=None):
        super().__init__('Data Engineering Toolkit', parent)
        self.setObjectName('DataEngineeringToolkitDock')

        self.iface = iface
        self.canvas = iface.mapCanvas()

        self.current_layer = None
        self.profiles = {}          # field_name -> profile dict
        self.field_order = []       # row index -> field_name, mirrors the table
        self.detail_field = None
        self._chart_mode = None
        self._active_task = None    # currently running ProfileLayerTask, if any

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)

        # --- Layer selector (vector layers only), always visible ---
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.Filter.VectorLayer)
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        root_layout.addWidget(self.layer_combo)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_stats_page())
        self.stack.addWidget(self._build_detail_page())
        root_layout.addWidget(self.stack)

        self.setWidget(root)

        # If a layer is already active when the panel opens, load it.
        if self.layer_combo.currentLayer() is not None:
            self._on_layer_changed(self.layer_combo.currentLayer())

    def _build_stats_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        self.status_label = QLabel('All fields')
        header_row.addWidget(self.status_label)
        header_row.addStretch()

        # Shown only while a ProfileLayerTask is running in the
        # background, so the user always sees that the plugin is working
        # instead of the panel looking frozen on a large layer.
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setFixedWidth(160)
        self.progress_bar.setVisible(False)
        header_row.addWidget(self.progress_bar)

        self.refresh_button = QPushButton('Refresh')
        self.refresh_button.clicked.connect(self._refresh_all_fields)
        header_row.addWidget(self.refresh_button)
        layout.addLayout(header_row)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # Left: field list, used to quickly jump to a field's row.
        self.field_list = QListWidget()
        self.field_list.currentItemChanged.connect(self._on_field_list_item_changed)
        splitter.addWidget(self.field_list)

        # Right: one row per field, one column per statistic + thumbnail.
        self.stats_table = QTableWidget(0, len(TABLE_COLUMNS))
        self.stats_table.setHorizontalHeaderLabels([label for _k, label, _c in TABLE_COLUMNS])
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.stats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.stats_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.stats_table.setIconSize(THUMBNAIL_SIZE)
        self.stats_table.cellClicked.connect(self._on_stats_table_cell_clicked)
        splitter.addWidget(self.stats_table)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([180, 800])

        layout.addWidget(splitter)
        return page

    def _build_detail_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        header_row = QHBoxLayout()
        self.back_button = QPushButton('< Back to statistics')
        self.back_button.clicked.connect(self._show_stats_page)
        self.detail_title_label = QLabel('')
        header_row.addWidget(self.back_button)
        header_row.addWidget(self.detail_title_label)
        header_row.addStretch()
        layout.addLayout(header_row)

        self.figure = Figure(figsize=(4, 3))
        self.canvas_plot = FigureCanvas(self.figure)
        self.ax = self.figure.add_subplot(111)
        self.canvas_plot.mpl_connect('button_press_event', self._on_chart_click)
        layout.addWidget(self.canvas_plot)

        return page

    # ------------------------------------------------------------------
    # Layer / profiling
    # ------------------------------------------------------------------
    def _on_layer_changed(self, layer):
        self._cancel_active_task()

        self.current_layer = layer
        self.profiles = {}
        self.field_order = []
        self.detail_field = None
        self.field_list.clear()
        self.stats_table.setRowCount(0)
        self._show_stats_page()

        if layer is None:
            return

        for field_info in profiling_engine.list_fields(layer):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, field_info['name'])
            item.setToolTip(field_info['type_name'])
            self.field_list.addItem(item)

            row_widget = FieldListRow(field_info['name'], display_text=field_info['alias'])
            row_widget.symbology_clicked.connect(self._on_symbology_button_clicked)
            item.setSizeHint(row_widget.sizeHint())
            self.field_list.setItemWidget(item, row_widget)

        self._refresh_all_fields()

    def _cancel_active_task(self):
        if self._active_task is not None:
            self._active_task.cancel()
            self._active_task = None
        self.progress_bar.setVisible(False)
        self.refresh_button.setEnabled(True)
        self.layer_combo.setEnabled(True)

    def _refresh_all_fields(self):
        """(Re)computes the profile of every field, in the background so
        the QGIS UI is never blocked regardless of layer size, and
        repopulates the table when done."""
        if self.current_layer is None:
            return

        self._cancel_active_task()

        layer = self.current_layer
        field_names = [f['name'] for f in profiling_engine.list_fields(layer)]

        self.status_label.setText(f'Profiling "{layer.name()}"...')
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(True)
        self.refresh_button.setEnabled(False)
        self.layer_combo.setEnabled(False)

        task = profiling_task.ProfileLayerTask(layer, field_names, self._on_profile_task_finished)
        task.progressChanged.connect(self._on_profile_task_progress)
        self._active_task = task
        QgsApplication.taskManager().addTask(task)

    def _on_profile_task_progress(self, progress):
        self.progress_bar.setValue(int(progress))

    def _on_profile_task_finished(self, task, success):
        # QGIS may still fire this for a task we've already superseded
        # (e.g. the user switched layers or hit Refresh again mid-run and
        # we canceled + replaced self._active_task): ignore anything that
        # isn't the task we're currently tracking.
        if self._active_task is not task:
            return
        self._active_task = None

        self.progress_bar.setVisible(False)
        self.refresh_button.setEnabled(True)
        self.layer_combo.setEnabled(True)
        self.status_label.setText('All fields')

        if not success:
            if task.error is not None:
                self.iface.messageBar().pushWarning(
                    'Data Engineering Toolkit',
                    f'Profiling failed: {task.error}',
                )
            return  # canceled, or failed with no exception: leave the table as-is

        self.profiles = task.results or {}
        self._populate_stats_table()

        if any(p.get('sampled') for p in self.profiles.values()):
            self.iface.messageBar().pushInfo(
                'Data Engineering Toolkit',
                f'Percentiles and charts for very large fields are estimated from a sample '
                f'of up to {profiling_engine.HISTOGRAM_SAMPLE_LIMIT:,} values; counts, '
                'nulls, min and max cover every feature.',
            )

    # ------------------------------------------------------------------
    # Stats table (all fields, one column per statistic + chart thumbnail)
    # ------------------------------------------------------------------
    def _populate_stats_table(self):
        self.field_order = list(self.profiles.keys())
        self.stats_table.setRowCount(len(self.field_order))

        for row, field_name in enumerate(self.field_order):
            profile = self.profiles[field_name]
            for col, (key, _label, clickable) in enumerate(TABLE_COLUMNS):
                if key == 'field':
                    item = QTableWidgetItem(field_name)
                elif key == 'chart':
                    item = QTableWidgetItem('')
                    pixmap = self._render_thumbnail(profile)
                    if pixmap is not None:
                        item.setIcon(QIcon(pixmap))
                        tooltip = 'Click to open the full chart'
                        if profile.get('sampled'):
                            tooltip += f' (based on a sample of {profiling_engine.HISTOGRAM_SAMPLE_LIMIT:,} features)'
                        item.setToolTip(tooltip)
                else:
                    value = profiling_engine.stat_value(profile, key)
                    item = QTableWidgetItem(self._format_value(value))
                    if clickable and value:
                        item.setForeground(Qt.GlobalColor.blue)
                        item.setToolTip('Click to select these features on the map')
                item.setData(Qt.ItemDataRole.UserRole, key)
                self.stats_table.setItem(row, col, item)

        self.stats_table.resizeRowsToContents()

    @staticmethod
    def _format_value(value):
        if value is None:
            return 'N/A'
        if isinstance(value, float):
            return f'{value:.4g}'
        return str(value)

    def _on_stats_table_cell_clicked(self, row, column):
        if row >= len(self.field_order):
            return
        field_name = self.field_order[row]
        profile = self.profiles.get(field_name)
        if profile is None:
            return

        item = self.stats_table.item(row, column)
        key = item.data(Qt.ItemDataRole.UserRole)

        if key == 'chart':
            self._open_detail_view(field_name)
            return

        if self.current_layer is None:
            return

        # Selections are resolved lazily from an expression at click time
        # instead of from precomputed id lists: profiling large layers
        # (e.g. cadastral data) no longer has to build and hold a feature
        # id list per statistic just in case the user clicks it.
        expr = None
        if key == 'null_count':
            expr = selection_helper.expression_for_null(field_name)
        elif key == 'min_value' and profile.get('min_value') is not None:
            expr = selection_helper.expression_for_equal(field_name, profile['min_value'])
        elif key == 'max_value' and profile.get('max_value') is not None:
            expr = selection_helper.expression_for_equal(field_name, profile['max_value'])
        elif key == 'outlier_count' and profile.get('outlier_bounds'):
            bounds = profile['outlier_bounds']
            expr = f'"{field_name}" < {bounds["lower"]} OR "{field_name}" > {bounds["upper"]}'

        if expr:
            selection_helper.select_by_expression(self.current_layer, expr, canvas=self.canvas)

    def _on_field_list_item_changed(self, current_item, _previous_item):
        """Clicking a field in the left list jumps to (and highlights)
        its row in the statistics table, rather than replacing the table
        contents, since the table now always shows every field."""
        if current_item is None:
            return
        field_name = current_item.data(Qt.ItemDataRole.UserRole)
        if field_name not in self.field_order:
            return
        row = self.field_order.index(field_name)
        self.stats_table.selectRow(row)
        self.stats_table.scrollToItem(self.stats_table.item(row, 0))

    def _on_symbology_button_clicked(self, field_name):
        """Renders the active layer by field_name: a white -> dark blue
        graduated renderer for numeric fields, or a multicolor
        categorized renderer for text/categorical fields."""
        if self.current_layer is None:
            return

        is_numeric = profiling_engine.is_numeric_field(self.current_layer, field_name)
        applied = symbology_helper.apply_field_renderer(self.current_layer, field_name, is_numeric)

        if applied:
            self.iface.mapCanvas().refresh()
        else:
            self.iface.messageBar().pushWarning(
                'Data Engineering Toolkit',
                f'Could not symbolize by "{field_name}" (no values, or too many '
                'distinct categories for a readable legend).',
            )

    # ------------------------------------------------------------------
    # Chart thumbnails (rendered off-screen with the Agg backend, so they
    # don't depend on the Qt canvas used for the big interactive chart)
    # ------------------------------------------------------------------
    def _render_thumbnail(self, profile, dpi=100):
        if not profile.get('histogram') and not profile.get('value_counts'):
            return None

        width_in = THUMBNAIL_SIZE.width() / dpi
        height_in = THUMBNAIL_SIZE.height() / dpi
        fig = Figure(figsize=(width_in, height_in), dpi=dpi)
        canvas = FigureCanvasAgg(fig)
        ax = fig.add_subplot(111)
        ax.axis('off')
        fig.subplots_adjust(left=0, right=1, top=1, bottom=0)

        if profile.get('is_numeric') and profile.get('histogram'):
            hist = profile['histogram']
            edges = hist['edges']
            counts = hist['counts']
            widths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
            ax.bar(edges[:-1], counts, width=widths, align='edge', color='#4C72B0')
        elif profile.get('value_counts'):
            items = sorted(profile['value_counts'].items(), key=lambda kv: kv[1], reverse=True)[:10]
            values = [v for _, v in items]
            ax.bar(range(len(values)), values, color='#4C72B0')
        else:
            return None

        canvas.draw()
        width_px, height_px = canvas.get_width_height()
        image = QImage(canvas.buffer_rgba(), width_px, height_px, QImage.Format.Format_RGBA8888)
        # .copy() detaches the QImage from matplotlib's buffer before the
        # Figure/canvas go out of scope and get garbage-collected.
        return QPixmap.fromImage(image.copy())

    # ------------------------------------------------------------------
    # Detail page: full-size interactive chart for a single field
    # ------------------------------------------------------------------
    def _open_detail_view(self, field_name):
        profile = self.profiles.get(field_name)
        if profile is None:
            return
        self.detail_field = field_name
        title = f'Field: {field_name}'
        if profile.get('sampled'):
            title += f'  (chart based on a sample of {profiling_engine.HISTOGRAM_SAMPLE_LIMIT:,} features)'
        self.detail_title_label.setText(title)
        self._draw_detail_chart(profile)
        self.stack.setCurrentIndex(1)

    def _show_stats_page(self):
        self.stack.setCurrentIndex(0)

    def _draw_detail_chart(self, profile):
        self.ax.clear()

        if profile.get('is_numeric') and profile.get('histogram'):
            hist = profile['histogram']
            edges = hist['edges']
            counts = hist['counts']
            widths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
            self.ax.bar(edges[:-1], counts, width=widths, align='edge', edgecolor='white')
            self.ax.set_title(f"Distribution of {profile['field']}")
            self._chart_edges = edges
            self._chart_mode = 'histogram'
        elif not profile.get('is_numeric') and profile.get('value_counts'):
            # Top 15 categories so the chart doesn't get overcrowded.
            items = sorted(profile['value_counts'].items(), key=lambda kv: kv[1], reverse=True)[:15]
            labels = [k for k, _ in items]
            values = [v for _, v in items]
            self.ax.bar(range(len(labels)), values, edgecolor='white')
            self.ax.set_xticks(range(len(labels)))
            self.ax.set_xticklabels(labels, rotation=45, ha='right')
            self.ax.set_title(f"Frequency of {profile['field']}")
            self._chart_categories = labels
            self._chart_mode = 'categorical'
        else:
            self._chart_mode = None

        self.figure.tight_layout()
        self.canvas_plot.draw_idle()

    def _on_chart_click(self, event):
        if self.current_layer is None or self.detail_field is None:
            return
        if event.xdata is None or self._chart_mode is None:
            return

        if self._chart_mode == 'histogram':
            edges = self._chart_edges
            if event.xdata < edges[0] or event.xdata > edges[-1]:
                return
            # numpy isn't imported here, so we do a simple linear scan
            # (the number of bins is small, this isn't a bottleneck).
            bin_index = None
            for i in range(len(edges) - 1):
                is_last = i == len(edges) - 2
                if edges[i] <= event.xdata < edges[i + 1] or (is_last and event.xdata == edges[-1]):
                    bin_index = i
                    break
            if bin_index is None:
                return
            expr = selection_helper.expression_for_range(
                self.detail_field, edges[bin_index], edges[bin_index + 1],
                upper_inclusive=(bin_index == len(edges) - 2),
            )
            selection_helper.select_by_expression(self.current_layer, expr, canvas=self.canvas)

        elif self._chart_mode == 'categorical':
            index = round(event.xdata)
            if index < 0 or index >= len(self._chart_categories):
                return
            category_value = self._chart_categories[index]
            expr = selection_helper.expression_for_equal(self.detail_field, category_value)
            selection_helper.select_by_expression(self.current_layer, expr, canvas=self.canvas)
