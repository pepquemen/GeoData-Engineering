# -*- coding: utf-8 -*-
"""
data_engineering_dock.py
-------------------------
The Data Engineering panel, docked at the bottom of the QGIS window.

Layout
  ┌ header ───────────────────────────────────────────────────────────┐
  │ ›› DATA ENGINEERING [layer ▾]  FEATURES · FIELDS · SCOPE   [filter] │
  │                                   [SEL] [AUTO] [REFRESH] [■] [≡]   │
  ├ status (only while profiling / when data changed) ─────────────────┤
  ├ table (all fields × statistics)          │ detail: figures + chart │
  └──────────────────────────────────────────┴─────────────────────────┘

Behaviour
  - profiling runs in a background QgsTask (cancelable from the panel);
  - results are cached per layer and marked stale when the layer's data
    changes (edits, commits, filters); stale results show a banner, or
    are refreshed automatically if AUTO is on;
  - SEL profiles only the selected features and follows the selection;
  - clicking a statistic selects the matching features (Ctrl: add,
    Ctrl+Shift: remove); right click for more (attribute table, flash,
    symbology, copy);
  - the map selection is overlaid on the detail chart.

The panel only holds UI logic: statistics come from profiling_engine /
stats_core, selections from selection_helper, styles from
symbology_helper.
"""

from qgis.core import Qgis, QgsApplication, QgsProject
from qgis.gui import QgsMapLayerComboBox
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtGui import QGuiApplication
from qgis.PyQt.QtWidgets import (
    QAbstractItemView,
    QDockWidget,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

try:  # Qt5: QtWidgets / Qt6: QtGui
    from qgis.PyQt.QtWidgets import QActionGroup
except ImportError:  # pragma: no cover
    from qgis.PyQt.QtGui import QActionGroup

from . import profiling_engine as pe
from . import selection_helper as sh
from . import symbology_helper
from .compat import VECTOR_LAYER_FILTER
from .translation import tr
from .profiling_task import ProfileLayerTask
from .ui import formatting as fmt
from .ui import icons, settings
from .ui.chart_panel import ChartPanel, modifiers_behavior
from .ui.delegates import ROW_HEIGHT, StatsDelegate, StatsView
from .ui.stats_model import CLICK_ROLE, KEY_ROLE, PROFILE_ROLE, ROW_ROLE, FieldFilterProxy, StatsTableModel
from .ui.theme import Theme, detect_mode
from .ui.widgets import (Banner, BrandMark, ChamferFrame, StripedProgress, menu_section, muted_label,
                         readout_html, tool_button)

MESSAGE_TITLE = 'Data Engineering'


class DataEngineeringDock(QDockWidget):

    def __init__(self, iface, parent=None):
        super().__init__(tr('Data Engineering Toolkit'), parent)
        self.setObjectName('DataEngineeringToolkitDock')
        self.iface = iface
        self.canvas = iface.mapCanvas()
        self.theme = Theme(detect_mode(settings.get('theme')))

        self.current_layer = None
        self._layer_connections = []
        self._task = None
        self._dirty_during_run = False
        self._cache = {}             # layer id -> LayerProfile (full scope only)
        self._stale = set()          # layer ids whose cached profile is outdated
        self._scope_fids = None      # feature ids profiled in "selected only" mode
        self._own_selection = False  # True while WE change the selection
        self._detail_field = None
        self._closed = False         # set by shutdown(): every slot becomes a no-op

        self._refresh_timer = self._single_shot(1500, self._auto_refresh)
        self._selection_timer = self._single_shot(450, self._on_selection_settled)
        self._overlay_timer = self._single_shot(200, self._update_overlay)

        self._build_ui()
        self._apply_theme()

        project = QgsProject.instance()
        project.layersWillBeRemoved.connect(self._on_layers_will_be_removed)

        if self.layer_combo.currentLayer() is not None:
            self._on_layer_changed(self.layer_combo.currentLayer())
        else:
            self._show_empty()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._adapt_header(self.width())
        if not self._splitter_initialized and self.isVisible() and self.splitter.width() > 600:
            # Table ~60 % / detail ~40 % once the dock has its real size;
            # the user's adjustments are kept afterwards.
            total = self.splitter.width()
            self.splitter.setSizes([int(total * 0.6), total - int(total * 0.6)])
            self._splitter_initialized = True

    def _adapt_header(self, width):
        """Responsive header: a bottom dock can be narrow (laptops, side
        by side with the attribute table), so secondary items give way
        instead of forcing a minimum width on the QGIS window."""
        compact = width < 1100
        style = (Qt.ToolButtonStyle.ToolButtonIconOnly if compact
                 else Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        for b in (self.sel_button, self.auto_button, self.refresh_button, self.stop_button):
            b.setToolButtonStyle(style)
        self.meta_label.setVisible(width >= 1280)
        self.title_label.setVisible(width >= 960)

    def _single_shot(self, ms, slot):
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(ms)
        timer.timeout.connect(slot)
        return timer

    # ==================================================================
    # UI construction
    # ==================================================================
    def _build_ui(self):
        t = self.theme
        root = QWidget()
        root.setObjectName('DETRoot')
        outer = QVBoxLayout(root)
        outer.setContentsMargins(8, 6, 8, 8)
        outer.setSpacing(6)

        # --- header ---------------------------------------------------
        header = QHBoxLayout()
        header.setSpacing(8)
        self.brand = BrandMark(t)
        header.addWidget(self.brand)
        self.title_label = QLabel(tr('DATA ENGINEERING'))
        header.addWidget(self.title_label)

        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(VECTOR_LAYER_FILTER)
        self.layer_combo.setMinimumWidth(150)
        self.layer_combo.setMaximumWidth(320)
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        header.addWidget(self.layer_combo)

        self.meta_label = QLabel()
        self.meta_label.setTextFormat(Qt.TextFormat.RichText)
        header.addWidget(self.meta_label)
        header.addStretch(1)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr('FILTER FIELDS…'))
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(80)
        self.search.setMaximumWidth(170)
        self._search_action = self.search.addAction(icons.icon('search', t), QLineEdit.ActionPosition.LeadingPosition)
        self.search.textChanged.connect(self._on_search)
        header.addWidget(self.search)

        self.sel_button = tool_button(tr('SEL'), 'selection', t, checkable=True,
                                      tooltip=tr('Profile only the selected features (follows the map selection)'))
        self.sel_button.setChecked(settings.get('selected_only'))
        self.sel_button.toggled.connect(self._on_scope_toggled)
        header.addWidget(self.sel_button)

        self.auto_button = tool_button(tr('AUTO'), 'auto', t, checkable=True,
                                       tooltip=tr('Refresh automatically when the layer data changes'))
        self.auto_button.setChecked(settings.get('auto_refresh'))
        self.auto_button.toggled.connect(lambda on: settings.set('auto_refresh', on))
        header.addWidget(self.auto_button)

        self.refresh_button = tool_button(tr('REFRESH'), 'refresh', t, tooltip=tr('Profile the layer again'))
        self.refresh_button.clicked.connect(lambda: self.refresh(force=True))
        header.addWidget(self.refresh_button)

        self.stop_button = tool_button(tr('STOP'), 'stop', t, tooltip=tr('Cancel profiling'))
        self.stop_button.setProperty('det', 'signal')
        self.stop_button.clicked.connect(self._cancel_task)
        self.stop_button.setVisible(False)
        header.addWidget(self.stop_button)

        self.menu_button = tool_button('', 'settings', t, tooltip=tr('Options'))
        self.menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.menu_button.setMenu(self._build_options_menu())
        header.addWidget(self.menu_button)
        outer.addLayout(header)

        # Small explicit minimums: the layout may then shrink these items
        # and _adapt_header() switches to the compact header (icon-only
        # buttons, no title/readout) instead of the dock imposing its full
        # width on the QGIS window.
        for b in (self.sel_button, self.auto_button, self.refresh_button, self.stop_button):
            b.setMinimumWidth(28)
        for lab in (self.title_label, self.meta_label):
            lab.setMinimumWidth(1)

        # --- status strip ---------------------------------------------
        self.status_row = QWidget()
        status = QHBoxLayout(self.status_row)
        status.setContentsMargins(0, 0, 0, 0)
        status.setSpacing(8)
        self.status_label = muted_label()
        self.progress = StripedProgress(t)
        status.addWidget(self.status_label)
        status.addWidget(self.progress, 1)
        self.status_row.setVisible(False)
        outer.addWidget(self.status_row)

        self.stale_banner = Banner(t, 'signal')
        self.stale_banner.setText(tr('The layer data changed since this profile — click to refresh'))
        self.stale_banner.clicked.connect(lambda: self.refresh(force=True))
        self.stale_banner.setVisible(False)
        outer.addWidget(self.stale_banner)

        # --- body: empty state | table + detail -----------------------
        self.body = QStackedWidget()

        self.empty_frame = ChamferFrame(t, title=tr('NO LAYER'))
        empty_layout = QVBoxLayout(self.empty_frame)
        self.empty_label = QLabel(tr('SELECT A VECTOR LAYER TO PROFILE ITS FIELDS'))
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_label)
        self.body.addWidget(self.empty_frame)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setHandleWidth(6)
        self.splitter.setChildrenCollapsible(False)

        self.model = StatsTableModel(self)
        self.proxy = FieldFilterProxy(self)
        self.proxy.setSourceModel(self.model)
        self.view = StatsView()
        self.view.setObjectName('DETTable')
        self.view.setModel(self.proxy)
        self.delegate = StatsDelegate(t, self.view, self.view)
        self.delegate.symbology_requested.connect(self._symbolize)
        self.view.setItemDelegate(self.delegate)
        self.view.setSortingEnabled(True)
        # Start in the layer's own field order (no sort column); clicking
        # a header sorts by that statistic.
        self.view.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.proxy.sort(-1)
        self.view.setShowGrid(False)
        self.view.setWordWrap(False)
        self.view.setAlternatingRowColors(False)
        self.view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.view.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh = self.view.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        hh.setStretchLastSection(False)
        hh.setSortIndicatorShown(True)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._table_context_menu)
        self.view.clicked.connect(self._on_cell_clicked)
        self.view.doubleClicked.connect(self._on_cell_double_clicked)
        self.view.selectionModel().currentRowChanged.connect(self._on_current_row_changed)
        self.splitter.addWidget(self.view)

        self.view.setMinimumWidth(320)
        self.chart = ChartPanel(t)
        self.chart.setMinimumWidth(280)
        self.chart.select_requested.connect(self._select)
        self.chart.table_requested.connect(self._open_table)
        self.splitter.addWidget(self.chart)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self._splitter_initialized = False
        self.body.addWidget(self.splitter)
        outer.addWidget(self.body, 1)

        self.setWidget(root)
        self.root = root

    def _build_options_menu(self):
        menu = QMenu(self)
        menu.setProperty('det', 'menu')

        menu_section(menu, tr('THEME'))
        group = QActionGroup(menu)
        for value, label in (('auto', tr('Follow QGIS')), ('light', tr('Light')), ('dark', tr('Dark'))):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(settings.get('theme') == value)
            a.triggered.connect(lambda _c=False, v=value: self._set_theme_pref(v))
            group.addAction(a)

        menu_section(menu, tr('WHEN SELECTING'))
        group = QActionGroup(menu)
        for value, label in (('zoom', tr('Zoom to the features')), ('pan', tr('Pan to the features')),
                             ('none', tr("Don't move the map"))):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(settings.get('zoom_mode') == value)
            a.triggered.connect(lambda _c=False, v=value: settings.set('zoom_mode', v))
            group.addAction(a)
        flash = menu.addAction(tr('Flash selected features'))
        flash.setCheckable(True)
        flash.setChecked(settings.get('flash'))
        flash.toggled.connect(lambda on: settings.set('flash', on))

        menu_section(menu, tr('OUTLIERS'))
        group = QActionGroup(menu)
        for value, label in ((1.5, tr('Tukey fences, k = 1.5 (standard)')),
                             (3.0, tr('Tukey fences, k = 3 (extreme only)'))):
            a = menu.addAction(label)
            a.setCheckable(True)
            a.setChecked(abs(settings.get('outlier_k') - value) < 1e-9)
            a.triggered.connect(lambda _c=False, v=value: self._set_outlier_k(v))
            group.addAction(a)

        menu.addSeparator()
        menu.addAction(tr('Copy statistics table'), self._copy_table)
        self.restore_style_action = menu.addAction(tr('Restore original layer style'), self._restore_style)
        menu.aboutToShow.connect(self._update_menu_state)
        self.options_menu = menu
        return menu

    # ==================================================================
    # Theme
    # ==================================================================
    def _apply_theme(self):
        t = self.theme
        self.root.setStyleSheet(t.stylesheet())
        self.options_menu.setStyleSheet(t.stylesheet())
        self.title_label.setFont(t.font(8.2, 'bold', 1.4))
        self.title_label.setStyleSheet('color: {};'.format(t.ink))
        self.empty_label.setFont(t.font(8.5, 'bold', 1.0))
        self.empty_label.setStyleSheet('color: {};'.format(t.muted))
        hh = self.view.horizontalHeader()
        hh.setFont(t.font(7.0, 'bold', 0.8))
        for w in (self.brand, self.progress, self.stale_banner, self.empty_frame):
            w.set_theme(t)
        self.chart.set_theme(t)
        self.delegate.set_theme(t)
        for button, name in ((self.sel_button, 'selection'), (self.auto_button, 'auto'),
                             (self.refresh_button, 'refresh'), (self.menu_button, 'settings')):
            button.setIcon(icons.icon(name, t))
        self.stop_button.setIcon(icons.icon('stop', t, color=t.surface))
        self._search_action.setIcon(icons.icon('search', t))
        self._update_meta()
        self.view.viewport().update()

    def _set_theme_pref(self, value):
        settings.set('theme', value)
        self.theme = Theme(detect_mode(value))
        self._apply_theme()

    # ==================================================================
    # Layer handling
    # ==================================================================
    def _on_layer_changed(self, layer):
        if self._closed:
            return
        self._cancel_task()
        self._disconnect_layer()
        self.current_layer = layer
        self._scope_fids = None
        self.stale_banner.setVisible(False)
        self.chart.set_profile(None)
        self._detail_field = None

        if layer is None or not layer.isValid():
            self.model.clear()
            self._show_empty()
            return

        self._connect_layer(layer)
        self.body.setCurrentIndex(1)
        cached = self._cache.get(layer.id())
        if cached is not None and not self.sel_button.isChecked():
            self._show_profile(cached)
            if layer.id() in self._stale:
                if self.auto_button.isChecked():
                    self.refresh(force=True)
                else:
                    self.stale_banner.setVisible(True)
        else:
            self.refresh(force=True)

    def _show_empty(self):
        self.body.setCurrentIndex(0)
        self._update_meta()

    def _connect_layer(self, layer):
        pairs = [
            (layer.dataChanged, self._on_layer_data_changed),
            (layer.layerModified, self._on_layer_data_changed),
            (layer.subsetStringChanged, self._on_layer_data_changed),
            (layer.updatedFields, self._on_layer_fields_changed),
            (layer.selectionChanged, self._on_selection_changed),
        ]
        for signal, slot in pairs:
            signal.connect(slot)
        self._layer_connections = pairs

    def _disconnect_layer(self):
        for signal, slot in self._layer_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._layer_connections = []

    def _on_layers_will_be_removed(self, layer_ids):
        if self._closed:
            return
        for lid in layer_ids:
            self._cache.pop(lid, None)
            self._stale.discard(lid)
        if self.current_layer is not None:
            try:
                current_id = self.current_layer.id()
            except RuntimeError:
                current_id = None
            if current_id is None or current_id in layer_ids:
                self._cancel_task()
                self._disconnect_layer()
                self.current_layer = None

    def _on_layer_data_changed(self, *args):
        if self._closed:
            return
        layer = self.current_layer
        if layer is None:
            return
        self._stale.add(layer.id())
        if self._task is not None:
            self._dirty_during_run = True
            return
        if self.auto_button.isChecked():
            self._refresh_timer.start()
        else:
            self.stale_banner.setVisible(True)

    def _on_layer_fields_changed(self):
        self._on_layer_data_changed()
        if self.current_layer is not None and self._task is None:
            # Columns were added/removed: the cached rows no longer match.
            self.refresh(force=True)

    def _auto_refresh(self):
        if self._closed:
            return
        if self.current_layer is not None and self.current_layer.id() in self._stale:
            self.refresh(force=True)

    # ==================================================================
    # Profiling
    # ==================================================================
    def refresh(self, force=False):
        layer = self.current_layer
        if layer is None:
            return
        self._cancel_task()
        self.stale_banner.setVisible(False)
        self._dirty_during_run = False

        selected_only = self.sel_button.isChecked()
        if not self.model.rows or force:
            # Show the fields right away; statistics fill in when ready.
            keep = self._detail_field
            if self.model.layer_profile is None or self.model.layer_profile.layer_id != layer.id():
                self.model.set_fields(pe.list_fields(layer))
                self._fit_columns()
                self.chart.set_profile(None, placeholder=tr('PROFILING…'))
            self._detail_field = keep

        task = ProfileLayerTask(layer, [f.name() for f in layer.fields()], self._on_task_finished,
                                selected_only=selected_only, outlier_k=settings.get('outlier_k'))
        task.progressChanged.connect(self._on_task_progress)
        self._task = task
        self._scope_fids = task.filter_fids
        self._set_running(True, task)
        QgsApplication.taskManager().addTask(task)

    def _set_running(self, running, task=None):
        self.status_row.setVisible(running)
        self.stop_button.setVisible(running)
        self.refresh_button.setVisible(not running)
        if running:
            known = task is not None and task.progress_known
            self.progress.setValue(0 if known else None)
            what = tr('SELECTION') if task is not None and task.selected_only else tr('LAYER')
            self.status_label.setText('{} · {}'.format(tr('PROFILING'), what))
        self._update_meta()

    def _on_task_progress(self, value):
        task = self._task
        if task is not None and task.progress_known:
            self.progress.setValue(value)
            self.status_label.setText('{} · {} %'.format(tr('PROFILING'), int(value)))

    def _cancel_task(self):
        if self._task is not None:
            task = self._task
            self._task = None
            task.detach()
            try:
                task.cancel()     # it stays referenced in profiling_task until finished
            except RuntimeError:
                pass              # already finished and deleted by the task manager
        self._set_running(False)

    def _on_task_finished(self, task, success):
        if task is not self._task:
            return            # superseded by a newer task
        self._task = None
        self._set_running(False)
        if not success:
            if task.error is not None:
                self.iface.messageBar().pushMessage(
                    MESSAGE_TITLE, tr('Profiling failed: {}').format(task.error), Qgis.MessageLevel.Warning, 8)
            return
        result = task.result
        layer = self.current_layer
        if layer is None or layer.id() != result.layer_id:
            return
        if not result.selected_only:
            self._cache[result.layer_id] = result
            if not self._dirty_during_run:
                self._stale.discard(result.layer_id)
        self._show_profile(result)
        if self._dirty_during_run:
            self._dirty_during_run = False
            self._on_layer_data_changed()

    def shutdown(self):
        """Called by the plugin on unload: nothing may call back into this
        widget afterwards (deleteLater() only runs on the next event-loop
        turn, and until then the layer combo would still react to layers
        being added or removed)."""
        self._closed = True
        try:
            self.layer_combo.layerChanged.disconnect(self._on_layer_changed)
        except (TypeError, RuntimeError):
            pass
        self._cancel_task()
        for timer in (self._refresh_timer, self._selection_timer, self._overlay_timer):
            timer.stop()
        self._disconnect_layer()
        try:
            QgsProject.instance().layersWillBeRemoved.disconnect(self._on_layers_will_be_removed)
        except (TypeError, RuntimeError):
            pass

    # ==================================================================
    # Showing results
    # ==================================================================
    def _show_profile(self, layer_profile):
        keep = self._detail_field
        # The proxy keeps the user's sort column across model resets.
        self.model.set_profile(layer_profile)
        self._fit_columns()
        self.body.setCurrentIndex(1)
        self._update_meta()
        target = keep if keep in layer_profile.fields else self._first_chartable(layer_profile)
        if target:
            self._select_row(target)
        else:
            self.chart.set_profile(None)

    def _fit_columns(self):
        self.view.resizeColumnsToContents()
        hh = self.view.horizontalHeader()
        field_col = self.model.column_of('field')
        hh.resizeSection(field_col, min(max(hh.sectionSize(field_col), 140), 240))

    @staticmethod
    def _first_chartable(layer_profile):
        for name, prof in layer_profile.fields.items():
            if (prof.is_continuous and prof.histogram) or (prof.is_categorical and prof.top and not prof.is_unique):
                return name
        return next(iter(layer_profile.fields), None)

    def _select_row(self, field_name):
        row = self.model.row_for_field(field_name)
        if row < 0:
            return
        index = self.proxy.mapFromSource(self.model.index(row, 0))
        if index.isValid():
            self.view.setCurrentIndex(index)
            self.view.scrollTo(index)

    def _update_meta(self):
        t = self.theme
        layer = self.current_layer
        if layer is None:
            self.meta_label.setText('')
            return
        lp = self.model.layer_profile
        if lp is not None and lp.layer_id == layer.id():
            features = fmt.fmt_count(lp.feature_count)
            scope = tr('SELECTION') if lp.selected_only else tr('ALL')
            pairs = [(tr('FEATURES'), features), (tr('FIELDS'), str(len(lp.fields))), (tr('SCOPE'), scope),
                     (tr('SCAN'), '{} s'.format(fmt.fmt_decimal(round(lp.elapsed, 1))))]
        else:
            count = layer.featureCount()
            pairs = [(tr('FEATURES'), fmt.fmt_count(count) if count >= 0 else '?'),
                     (tr('FIELDS'), str(layer.fields().count()))]
        self.meta_label.setText(readout_html(t, pairs))

    def _on_search(self, text):
        self.proxy.set_needle(text)

    def _on_current_row_changed(self, current, _previous):
        if not current.isValid():
            return
        row = current.data(ROW_ROLE)
        self._detail_field = row.name if row is not None else None
        self.chart.set_profile(row.profile if row is not None else None)
        self._overlay_timer.start()

    # ==================================================================
    # Interaction: table
    # ==================================================================
    def _expression_for_cell(self, index):
        action = index.data(CLICK_ROLE)
        prof = index.data(PROFILE_ROLE)
        if not action or prof is None:
            return None
        if action == 'nulls':
            return sh.expression_for_null(prof.name)
        if action == 'min':
            return sh.expression_for_min(prof.name, prof.kind, prof.min)
        if action == 'max':
            return sh.expression_for_max(prof.name, prof.kind, prof.max)
        if action == 'outliers':
            return sh.expression_for_outliers(prof.name, prof.kind, prof.outlier_lower, prof.outlier_upper)
        return None

    def _on_cell_clicked(self, index):
        expr = self._expression_for_cell(index)
        if expr:
            self._select(expr, modifiers_behavior())

    def _on_cell_double_clicked(self, index):
        expr = self._expression_for_cell(index)
        if expr:
            self._open_table(expr)

    def _table_context_menu(self, pos):
        index = self.view.indexAt(pos)
        if not index.isValid():
            return
        menu = self._build_table_menu(index)
        menu.exec(self.view.viewport().mapToGlobal(pos))

    def _build_table_menu(self, index):
        row = index.data(ROW_ROLE)
        expr = self._expression_for_cell(index)
        menu = QMenu(self)
        menu.setProperty('det', 'menu')
        menu.setStyleSheet(self.theme.stylesheet())
        if expr:
            menu_section(menu, self._cell_caption(index))
            menu.addAction(tr('Select'), lambda: self._select(expr, sh.SET))
            menu.addAction(tr('Add to selection'), lambda: self._select(expr, sh.ADD))
            menu.addAction(tr('Remove from selection'), lambda: self._select(expr, sh.REMOVE))
            menu.addAction(tr('Flash on the map'), lambda: self._flash(expr))
            menu.addAction(icons.icon('table', self.theme), tr('Open in attribute table'),
                           lambda: self._open_table(expr))
        menu_section(menu, row.name.upper())
        menu.addAction(icons.icon('symbology', self.theme), tr('Symbolize layer by this field'),
                       lambda: self._symbolize(row.name))
        if self.current_layer is not None and symbology_helper.original_style_name(self.current_layer):
            menu.addAction(icons.icon('restore', self.theme), tr('Restore original layer style'),
                           self._restore_style)
        menu.addSeparator()
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if text:
            menu.addAction(icons.icon('copy', self.theme), tr('Copy value'),
                           lambda: QGuiApplication.clipboard().setText(str(text)))
        menu.addAction(icons.icon('copy', self.theme), tr('Copy statistics table'), self._copy_table)
        return menu

    def _cell_caption(self, index):
        key = index.data(KEY_ROLE)
        row = index.data(ROW_ROLE)
        labels = {'nulls': tr('NULLS'), 'min': tr('MIN'), 'max': tr('MAX'), 'outliers': tr('OUTLIERS')}
        return '{} · {}'.format(row.name.upper(), labels.get(key, key.upper()))

    # ==================================================================
    # Selection, attribute table, symbology
    # ==================================================================
    def _select(self, expression, behavior):
        layer = self.current_layer
        if layer is None:
            return
        self._own_selection = True
        try:
            ids = sh.select(layer, expression, canvas=self.canvas, behavior=behavior,
                            zoom_mode=settings.get('zoom_mode'), flash=settings.get('flash'),
                            limit_to_ids=self._scope_fids)
        except ValueError as exc:
            self.iface.messageBar().pushMessage(MESSAGE_TITLE, str(exc), Qgis.MessageLevel.Warning, 6)
            return
        finally:
            self._own_selection = False
        verb = {sh.SET: tr('selected'), sh.ADD: tr('added to the selection'),
                sh.REMOVE: tr('removed from the selection')}[behavior]
        self.iface.statusBarIface().showMessage(
            '{}: {} {}'.format(MESSAGE_TITLE, fmt.fmt_count(len(ids)), verb), 4000)

    def _flash(self, expression):
        if self.current_layer is not None:
            sh.flash(self.current_layer, expression, self.canvas, self._scope_fids)

    def _open_table(self, expression):
        if self.current_layer is not None:
            sh.open_attribute_table(self.iface, self.current_layer, expression)

    def _symbolize(self, field_name):
        layer = self.current_layer
        if layer is None:
            return
        idx = layer.fields().indexFromName(field_name)
        if idx < 0:
            return
        kind = pe.field_kind(layer.fields().at(idx))
        try:
            style = symbology_helper.apply_field_renderer(layer, field_name, kind)
        except symbology_helper.SymbologyError as exc:
            self.iface.messageBar().pushMessage(
                MESSAGE_TITLE, tr('Cannot symbolize by "{}": {}').format(field_name, exc),
                Qgis.MessageLevel.Warning, 6)
            return
        self.iface.layerTreeView().refreshLayerSymbology(layer.id())
        self.iface.messageBar().pushMessage(
            MESSAGE_TITLE,
            tr('Style "{}" applied. Your original style is kept: switch back from the options menu '
               'or the layer\'s Styles menu.').format(style),
            Qgis.MessageLevel.Info, 6)

    def _restore_style(self):
        layer = self.current_layer
        if layer is not None and symbology_helper.restore_original_style(layer):
            self.iface.layerTreeView().refreshLayerSymbology(layer.id())

    def _update_menu_state(self):
        layer = self.current_layer
        self.restore_style_action.setEnabled(
            layer is not None and symbology_helper.original_style_name(layer) is not None
            and symbology_helper.has_det_style_active(layer))

    def _copy_table(self):
        model = self.proxy
        cols = range(model.columnCount())
        lines = ['\t'.join(str(model.headerData(c, Qt.Orientation.Horizontal)) for c in cols
                           if self.model.key_of(c) != 'dist')]
        for r in range(model.rowCount()):
            cells = []
            for c in cols:
                key = self.model.key_of(c)
                if key == 'dist':
                    continue
                index = model.index(r, c)
                prof = index.data(PROFILE_ROLE)
                if key == 'nulls' and prof is not None:
                    cells.append('{} ({})'.format(prof.null_count, fmt.fmt_percent(prof.null_ratio)))
                else:
                    cells.append(str(index.data(Qt.ItemDataRole.DisplayRole) or ''))
            lines.append('\t'.join(cells))
        QGuiApplication.clipboard().setText('\n'.join(lines))
        self.iface.statusBarIface().showMessage(tr('Statistics table copied to the clipboard'), 3000)

    # ==================================================================
    # Options
    # ==================================================================
    def _on_scope_toggled(self, on):
        settings.set('selected_only', on)
        self.refresh(force=True)

    def _set_outlier_k(self, value):
        settings.set('outlier_k', value)
        self._cache.clear()
        self.refresh(force=True)

    # ==================================================================
    # Map selection -> profile scope / chart overlay
    # ==================================================================
    def _on_selection_changed(self, *args):
        if self._closed:
            return
        if self.sel_button.isChecked() and not self._own_selection:
            self._selection_timer.start()
        self._overlay_timer.start()

    def _on_selection_settled(self):
        if self._closed:
            return
        if self.sel_button.isChecked():
            self.refresh(force=True)

    def _update_overlay(self):
        if self._closed:
            return
        layer = self.current_layer
        prof = self.chart.profile
        if layer is None or prof is None:
            return
        fids = layer.selectedFeatureIds()
        if not fids or (self.sel_button.isChecked() and self._scope_fids is not None
                        and set(fids) == set(self._scope_fids)):
            # Nothing selected, or the selection IS the profile: no overlay.
            self.chart.set_overlay(None, 1.0, 0)
            return
        values, scale = pe.read_values(layer, prof.name, prof.kind, fids)
        self.chart.set_overlay(values, scale, len(fids))
