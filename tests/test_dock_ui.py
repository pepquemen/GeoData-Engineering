# -*- coding: utf-8 -*-
"""End-to-end tests of the panel with a stand-in for QgisInterface."""

import time

import pytest

pytest.importorskip('qgis.gui')

from qgis.core import QgsFeature, QgsProject, QgsVectorLayer  # noqa: E402
from qgis.gui import QgsMapCanvas, QgsMessageBar  # noqa: E402
from qgis.PyQt.QtCore import QCoreApplication, QSettings  # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow  # noqa: E402


class _Status:
    def __init__(self):
        self.messages = []

    def showMessage(self, text, ms=0):
        self.messages.append(text)


class _Tree:
    def refreshLayerSymbology(self, layer_id):
        pass


class FakeIface:
    def __init__(self):
        self.win = QMainWindow()
        self.canvas = QgsMapCanvas()
        self.bar = QgsMessageBar()
        self.status = _Status()
        self.tables = []

    def mapCanvas(self):
        return self.canvas

    def messageBar(self):
        return self.bar

    def mainWindow(self):
        return self.win

    def statusBarIface(self):
        return self.status

    def layerTreeView(self):
        return _Tree()

    def showAttributeTable(self, layer, expression=''):
        self.tables.append(expression)


def pump(seconds=0.2):
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.005)


def wait_idle(dock, timeout=20):
    end = time.time() + timeout
    while dock._task is not None and time.time() < end:
        pump(0.05)
    pump(0.1)
    assert dock._task is None, 'profiling did not finish'


@pytest.fixture()
def layer(qgis_app):
    QSettings().setValue('data_engineering_toolkit/selected_only', False)
    QSettings().setValue('data_engineering_toolkit/auto_refresh', False)
    lyr = QgsVectorLayer('Point?crs=EPSG:25830&field=sup:double&field=uso:string', 'parcelas', 'memory')
    feats = []
    for i in range(200):
        f = QgsFeature(lyr.fields())
        f.setAttributes([None if i % 10 == 0 else float(i), 'R' if i % 3 else 'I'])
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    QgsProject.instance().addMapLayer(lyr)
    yield lyr
    QgsProject.instance().removeAllMapLayers()


@pytest.fixture()
def dock(plugin, layer):
    from data_engineering_toolkit.data_engineering_dock import DataEngineeringDock
    iface = FakeIface()
    d = DataEngineeringDock(iface)
    d.resize(1400, 420)
    d.show()
    d.layer_combo.setLayer(layer)
    wait_idle(d)
    yield d
    d.shutdown()
    d.deleteLater()
    pump(0.05)


def cell(dock, field, key):
    row = dock.model.row_for_field(field)
    return dock.proxy.mapFromSource(dock.model.index(row, dock.model.column_of(key)))


def test_profiles_and_fills_table(dock):
    lp = dock.model.layer_profile
    assert lp is not None and lp.feature_count == 200
    assert lp.fields['sup'].null_count == 20
    assert dock.chart.profile is not None


def test_click_on_nulls_selects_them(dock, layer):
    dock._on_cell_clicked(cell(dock, 'sup', 'nulls'))
    assert layer.selectedFeatureCount() == 20


def test_double_click_opens_attribute_table(dock):
    dock._on_cell_double_clicked(cell(dock, 'sup', 'nulls'))
    assert dock.iface.tables == ['"sup" IS NULL']


def test_edits_mark_profile_stale(dock, layer):
    layer.startEditing()
    fid = next(layer.getFeatures()).id()
    layer.changeAttributeValue(fid, 0, 12345.0)
    pump(0.3)
    assert layer.id() in dock._stale
    assert dock.stale_banner.isVisibleTo(dock)
    layer.rollBack()


def test_selected_only_scope(dock, layer):
    layer.selectByIds(list(range(1, 51)))
    dock.sel_button.setChecked(True)
    wait_idle(dock)
    assert dock.model.layer_profile.selected_only
    assert dock.model.layer_profile.feature_count == 50
    dock.sel_button.setChecked(False)
    wait_idle(dock)


def test_symbology_from_panel(dock, layer):
    dock._symbolize('uso')
    assert layer.renderer().type() == 'categorizedSymbol'
    dock._restore_style()
    assert layer.renderer().type() == 'singleSymbol'


def test_search_filters_rows(dock):
    dock.search.setText('us')
    assert dock.proxy.rowCount() == 1
    dock.search.setText('')
    assert dock.proxy.rowCount() == 2


def test_copy_table(dock):
    from qgis.PyQt.QtGui import QGuiApplication
    dock._copy_table()
    text = QGuiApplication.clipboard().text()
    assert text.splitlines()[0].startswith('FIELD')
    assert len(text.splitlines()) == 3


def test_theme_switch(dock):
    dock._set_theme_pref('dark')
    assert dock.theme.is_dark
    dock._set_theme_pref('light')
    assert not dock.theme.is_dark
    QSettings().setValue('data_engineering_toolkit/theme', 'auto')


def test_shutdown_while_profiling_is_safe(plugin, layer):
    from data_engineering_toolkit.data_engineering_dock import DataEngineeringDock
    from data_engineering_toolkit import profiling_task
    d = DataEngineeringDock(FakeIface())
    d.layer_combo.setLayer(layer)
    d.refresh(force=True)
    d.shutdown()
    d.deleteLater()
    end = time.time() + 10
    while profiling_task._RUNNING_TASKS and time.time() < end:
        pump(0.05)
    assert not profiling_task._RUNNING_TASKS


class LifecycleIface(FakeIface):
    """Adds the toolbar / menu / dock methods initGui and unload use."""

    def __init__(self):
        super().__init__()
        self.menu_actions = []

    def addToolBar(self, name):
        return self.win.addToolBar(name)

    def addPluginToMenu(self, menu, action):
        self.menu_actions.append(action)

    def removePluginMenu(self, menu, action):
        self.menu_actions.remove(action)

    def removeToolBarIcon(self, action):
        pass

    def addDockWidget(self, area, dock):
        self.win.addDockWidget(area, dock)

    def removeDockWidget(self, dock):
        self.win.removeDockWidget(dock)


def test_plugin_load_toggle_unload(plugin, layer):
    from qgis.PyQt.QtWidgets import QToolBar
    from data_engineering_toolkit import classFactory, profiling_task

    iface = LifecycleIface()
    p = classFactory(iface)
    p.initGui()
    assert len(iface.win.findChildren(QToolBar)) == 1
    p.action.setChecked(True)                    # opens the dock and starts profiling
    assert p.dock_widget is not None
    p.unload()                                   # unload while the task may still run
    pump(0.3)
    end = time.time() + 10
    while profiling_task._RUNNING_TASKS and time.time() < end:
        pump(0.05)
    assert not profiling_task._RUNNING_TASKS
    # deleteLater() runs on the next event-loop turn; flush it explicitly.
    from qgis.PyQt.QtCore import QEvent
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert iface.win.findChildren(QToolBar) == []    # no ghost toolbar after reload
    assert iface.menu_actions == []


def test_closed_dock_ignores_project_changes(plugin, layer):
    """Regression: a shut-down dock (deleteLater pending) used to react to
    layers being added/removed, start orphan tasks and crash."""
    from data_engineering_toolkit.data_engineering_dock import DataEngineeringDock
    d = DataEngineeringDock(FakeIface())
    d.layer_combo.setLayer(layer)
    wait_idle(d)
    d.shutdown()
    other = QgsVectorLayer('Point?field=a:integer', 'other', 'memory')
    QgsProject.instance().addMapLayer(other)
    QgsProject.instance().removeMapLayer(layer.id())
    pump(0.2)
    assert d._task is None
    QgsProject.instance().removeMapLayer(other.id())


def test_context_menu_actions(dock):
    menu = dock._build_table_menu(cell(dock, 'sup', 'nulls'))
    texts = [a.text() for a in menu.actions() if a.text()]
    assert 'Open in attribute table' in texts and 'Symbolize layer by this field' in texts
    # A non-clickable cell has no selection actions.
    menu = dock._build_table_menu(cell(dock, 'sup', 'mean'))
    assert 'Select' not in [a.text() for a in menu.actions()]


@pytest.mark.parametrize('uri, rows', [
    ('Point?field=a:double&field=b:string', []),                       # empty layer
    ('Point', [[]] * 3),                                                # no fields
    ('None?field=a:integer&field=b:string', [[1, 'x'], [2, 'y']]),     # table without geometry
    ('Point?field=a:double&field=b:string&field=d:date', [[None, None, None]] * 5),  # all NULL
])
def test_unusual_layers(plugin, qgis_app, uri, rows):
    from data_engineering_toolkit.data_engineering_dock import DataEngineeringDock
    lyr = QgsVectorLayer(uri, 'odd', 'memory')
    feats = []
    for r in rows:
        f = QgsFeature(lyr.fields())
        f.setAttributes(list(r))
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    QgsProject.instance().addMapLayer(lyr)
    d = DataEngineeringDock(FakeIface())
    d.layer_combo.setLayer(lyr)
    wait_idle(d)
    lp = d.model.layer_profile
    assert lp is not None and lp.feature_count == len(rows)
    for r in range(d.proxy.rowCount()):            # every cell renders
        for c in range(d.proxy.columnCount()):
            d.proxy.index(r, c).data()
    d.grab()                                        # paints delegates and chart
    d.shutdown()
    QgsProject.instance().removeMapLayer(lyr.id())


def test_profiles_uncommitted_edits(dock, layer):
    layer.startEditing()
    fid = next(layer.getFeatures()).id()
    layer.changeAttributeValue(fid, 0, 999999.0)
    dock.refresh(force=True)
    wait_idle(dock)
    assert dock.model.layer_profile.fields['sup'].max == 999999.0
    layer.rollBack()


def _click_chart_bar(dock, index, button=1):
    from matplotlib.backend_bases import MouseEvent
    chart = dock.chart
    chart.canvas.draw()
    patch = chart._bars[index][0]
    bbox = patch.get_window_extent()
    x, y = (bbox.x0 + bbox.x1) / 2, (bbox.y0 + bbox.y1) / 2
    event = MouseEvent('button_press_event', chart.canvas, x, y, button=button)
    chart._on_press(event)


def test_chart_bar_click_selects_its_features(dock, layer):
    dock._select_row('sup')
    pump(0.2)
    prof = dock.chart.profile
    _click_chart_bar(dock, 0)
    lo, hi = prof.histogram['edges'][0], prof.histogram['edges'][1]
    expected = {f.id() for f in layer.getFeatures() if f['sup'] is not None and f['sup'] == f['sup']
                and lo <= f['sup'] < hi}
    assert set(layer.selectedFeatureIds()) == expected and expected


def test_chart_category_click(dock, layer):
    dock._select_row('uso')
    pump(0.2)
    _click_chart_bar(dock, 0)                      # most frequent category: 'R'
    assert layer.selectedFeatureCount() == sum(1 for f in layer.getFeatures() if f['uso'] == 'R')


def test_symbology_icon_click_in_table(dock, layer):
    from qgis.PyQt.QtCore import QEvent, QPointF, Qt
    from qgis.PyQt.QtGui import QMouseEvent
    index = cell(dock, 'sup', 'field')
    dock.view.hover_index = index
    dock.view.viewport().repaint()                 # paints the hover icon, records its rect
    rect = dock.delegate._sym_rects['sup']
    pos = rect.center()
    event = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(pos), Qt.MouseButton.LeftButton,
                        Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    handled = dock.delegate.editorEvent(event, dock.proxy, None, index)
    assert handled and layer.renderer().type() == 'graduatedSymbol'
    dock._restore_style()
