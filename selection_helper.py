# -*- coding: utf-8 -*-
"""
selection_helper.py
--------------------
Single place where we centralize how the plugin selects features on the
canvas from a user interaction (a click on a stats-table cell or on a
histogram bar). Keeping this separate avoids duplicating logic between
the table and the charts, and makes changing the behaviour (e.g. add vs.
replace selection) a one-place change.
"""

from qgis.core import QgsExpression, QgsFeatureRequest


def select_by_ids(layer, feature_ids, canvas=None, zoom=True, behavior='set'):
    """Selects a list of feature ids on the layer.

    :param behavior: 'set' replaces the current selection, 'add' extends
        it, 'remove' subtracts from it. This is meant to allow
        combinations such as "nulls in A" + Ctrl-click "outliers in B".
    """
    if not layer:
        return

    # We use the direct select()/deselect()/selectByIds() methods, which
    # are stable across the public API, instead of SelectBehavior (whose
    # exposure has varied between QGIS versions).
    if behavior == 'add':
        layer.select(list(feature_ids))
    elif behavior == 'remove':
        layer.deselect(list(feature_ids))
    else:
        layer.selectByIds(list(feature_ids))

    if zoom and canvas is not None and feature_ids:
        canvas.zoomToSelected(layer)


def select_by_expression(layer, expression, canvas=None, zoom=True, behavior='set'):
    """Selects features on the layer that match a QGIS expression (for
    example, a histogram range or "field IS NULL")."""
    if not layer:
        return

    expr = QgsExpression(expression)
    if expr.hasParserError():
        raise ValueError(f'Invalid expression: {expr.parserErrorString()}')

    request = QgsFeatureRequest(expr)
    ids = [feat.id() for feat in layer.getFeatures(request)]
    select_by_ids(layer, ids, canvas=canvas, zoom=zoom, behavior=behavior)
    return ids


def expression_for_null(field_name):
    return f'"{field_name}" IS NULL'


def expression_for_equal(field_name, value):
    if isinstance(value, str):
        escaped = value.replace("'", "''")
        return f"\"{field_name}\" = '{escaped}'"
    return f'"{field_name}" = {value}'


def expression_for_range(field_name, low, high, upper_inclusive=False):
    op = '<=' if upper_inclusive else '<'
    return f'"{field_name}" >= {low} AND "{field_name}" {op} {high}'
