# -*- coding: utf-8 -*-
"""Engine, expressions, symbology and background task against a real
QGIS memory layer."""

import pytest

pytest.importorskip('qgis.core')

from qgis.core import NULL, QgsApplication, QgsFeature, QgsVectorLayer  # noqa: E402
from qgis.PyQt.QtCore import QDate, QDateTime, QTime  # noqa: E402


@pytest.fixture()
def layer(qgis_app):
    lyr = QgsVectorLayer(
        'Point?crs=EPSG:25830'
        '&field=area:double&field=floors:integer&field=uso:string'
        '&field=alta:date&field=mod:datetime&field=hora:time&field=ok:boolean'
        '&field=' + 'a%C3%B1o%20%22x%22' + ':integer',
        'parcelas', 'memory')
    assert lyr.isValid()
    feats = []
    data = [
        (120.5, 0, 'Residencial', QDate(2001, 5, 1), QDateTime(QDate(2020, 1, 1), QTime(8, 0)), QTime(8, 0), True, 1),
        (80.0, 1, "Ind'ustrial", QDate(1999, 1, 1), QDateTime(QDate(2021, 6, 1), QTime(9, 30)), QTime(9, 30), False, 2),
        (95.25, 2, 'Residencial', QDate(2010, 7, 15), QDateTime(QDate(2022, 3, 3), QTime(12, 0)), QTime(12, 0), True, 3),
        (NULL, 0, NULL, NULL, NULL, NULL, NULL, NULL),
        (10000.0, 0, 'Comercial', QDate(2015, 2, 2), QDateTime(QDate(2023, 9, 9), QTime(18, 45)), QTime(18, 45), True, 4),
        (101.0, 1, 'Residencial', QDate(2018, 8, 8), QDateTime(QDate(2024, 1, 1), QTime(0, 0)), QTime(0, 0), False, 5),
    ]
    for row in data:
        f = QgsFeature(lyr.fields())
        f.setAttributes(list(row))
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    return lyr


def _ids(plugin, layer, expr):
    return sorted(plugin.selection_helper.ids_for_expression(layer, expr))


def test_kinds(plugin, layer):
    pe = plugin.profiling_engine
    kinds = {f['name']: f['kind'] for f in pe.list_fields(layer)}
    assert kinds == {
        'area': pe.DECIMAL, 'floors': pe.INTEGER, 'uso': pe.TEXT, 'alta': pe.DATE,
        'mod': pe.DATETIME, 'hora': pe.TIME, 'ok': pe.BOOLEAN, 'año "x"': pe.INTEGER,
    }


def test_profile_counts_and_types(plugin, layer):
    lp = plugin.profiling_engine.profile_layer(layer)
    assert lp.feature_count == 6
    area = lp.fields['area']
    assert area.total == 6 and area.null_count == 1 and area.count == 5
    assert area.min == 80.0 and area.max == 10000.0
    assert area.sum == pytest.approx(120.5 + 80 + 95.25 + 10000 + 101)
    uso = lp.fields['uso']
    assert uso.top[0][0] == 'Residencial' and uso.top[0][2] == 3
    ok = lp.fields['ok']
    assert {k for k, _r, _c in ok.top} == {'true', 'false'}
    alta = lp.fields['alta']
    assert plugin.selection_helper.jd_to_iso(alta.min) == '1999-01-01'
    assert plugin.selection_helper.jd_to_iso(alta.max) == '2018-08-08'
    assert alta.sum is None
    assert lp.fields['año "x"'].is_unique


def test_selected_only(plugin, layer):
    layer.selectByIds([1, 2])
    lp = plugin.profiling_engine.profile_layer(layer, selected_only=True)
    assert lp.feature_count == 2
    assert lp.fields['area'].max == 120.5


def test_expressions_select_the_right_features(plugin, layer):
    sh = plugin.selection_helper
    pe = plugin.profiling_engine
    lp = pe.profile_layer(layer)
    f = lp.fields

    assert _ids(plugin, layer, sh.expression_for_null('area')) == [4]
    assert _ids(plugin, layer, sh.expression_for_min('area', pe.DECIMAL, f['area'].min)) == [2]
    assert _ids(plugin, layer, sh.expression_for_max('area', pe.DECIMAL, f['area'].max)) == [5]
    assert _ids(plugin, layer, sh.expression_for_min('alta', pe.DATE, f['alta'].min)) == [2]
    assert _ids(plugin, layer, sh.expression_for_max('mod', pe.DATETIME, f['mod'].max)) == [6]
    assert _ids(plugin, layer, sh.expression_for_max('hora', pe.TIME, f['hora'].max)) == [5]
    assert _ids(plugin, layer, sh.expression_for_category('uso', pe.TEXT, "Ind'ustrial")) == [2]
    assert _ids(plugin, layer, sh.expression_for_category('ok', pe.BOOLEAN, True)) == [1, 3, 5]
    assert _ids(plugin, layer, sh.expression_for_other_categories('uso', pe.TEXT, ['Residencial'])) == [2, 5]
    assert _ids(plugin, layer, sh.expression_for_min('año "x"', pe.INTEGER, 1)) == [1]


def test_histogram_bins_cover_every_feature(plugin, layer):
    sh = plugin.selection_helper
    pe = plugin.profiling_engine
    for name in ('area', 'floors', 'alta', 'mod', 'hora'):
        prof = pe.profile_layer(layer).fields[name]
        edges = prof.histogram['edges']
        selected = set()
        for i in range(len(edges) - 1):
            expr = sh.expression_for_range(name, prof.kind, edges[i], edges[i + 1],
                                           upper_inclusive=(i == len(edges) - 2))
            ids = set(_ids(plugin, layer, expr))
            assert not (ids & selected), name      # bins don't overlap
            selected |= ids
        if prof.histogram.get('under') is not None:
            ids = set(_ids(plugin, layer, sh.expression_for_below(name, prof.kind, edges[0])))
            assert not (ids & selected), name
            selected |= ids
        if prof.histogram.get('over') is not None:
            ids = set(_ids(plugin, layer, sh.expression_for_above(name, prof.kind, edges[-1])))
            assert not (ids & selected), name
            selected |= ids
        non_null = {f.id() for f in layer.getFeatures() if f[name] != NULL}
        assert selected == non_null, name          # every non-null feature


def test_outlier_expression(plugin, layer):
    sh = plugin.selection_helper
    pe = plugin.profiling_engine
    prof = pe.profile_layer(layer).fields['area']
    assert prof.outlier_count == 1
    expr = sh.expression_for_outliers('area', prof.kind, prof.outlier_lower, prof.outlier_upper)
    assert _ids(plugin, layer, expr) == [5]


def test_symbology_is_reversible(plugin, layer):
    sym = plugin.symbology_helper
    pe = plugin.profiling_engine
    sm = layer.styleManager()
    original_renderer = layer.renderer().type()
    style = sym.apply_field_renderer(layer, 'area', pe.DECIMAL)
    assert sm.currentStyle() == style and layer.renderer().type() == 'graduatedSymbol'
    sym.apply_field_renderer(layer, 'uso', pe.TEXT)
    assert layer.renderer().type() == 'categorizedSymbol'
    sym.apply_field_renderer(layer, 'alta', pe.DATE)
    assert layer.renderer().classAttribute() == 'year("alta")'
    assert sym.restore_original_style(layer)
    assert layer.renderer().type() == original_renderer


def test_symbology_refuses_unique_text(plugin, qgis_app):
    sym = plugin.symbology_helper
    lyr = QgsVectorLayer('Point?field=ref:string', 'refs', 'memory')
    feats = []
    for i in range(100):
        f = QgsFeature(lyr.fields())
        f.setAttributes(['R{:04d}'.format(i)])
        feats.append(f)
    lyr.dataProvider().addFeatures(feats)
    with pytest.raises(sym.SymbologyError):
        sym.apply_field_renderer(lyr, 'ref', plugin.profiling_engine.TEXT)


def test_background_task(plugin, layer):
    from qgis.PyQt.QtCore import QCoreApplication
    import time

    results = {}

    def done(task, success):
        results['success'] = success
        results['profile'] = task.result

    task = plugin.profiling_task.ProfileLayerTask(layer, ['area', 'uso'], done)
    QgsApplication.taskManager().addTask(task)
    deadline = time.time() + 20
    while 'success' not in results and time.time() < deadline:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    assert results.get('success') is True
    assert results['profile'].fields['area'].count == 5
    assert task not in plugin.profiling_task._RUNNING_TASKS
