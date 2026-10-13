# -*- coding: utf-8 -*-
"""
selection_helper.py
--------------------
Everything about turning a click (stats cell, chart bar, context menu)
into a selection on the map:

  * expression builders that are kind-aware (numbers, dates, datetimes,
    times, booleans, text) and always quote field names and values with
    QgsExpression.quotedColumnRef / quotedValue, so names such as
    'año "2024"' or values with apostrophes can't break the expression;
  * select(): set / add / remove behaviour, then zoom, pan or nothing,
    and an optional flash of the selected features;
  * open_attribute_table(): the native attribute table, filtered to the
    same expression.

Min / max use <= / >= instead of "=": comparing floats for equality is
fragile (decimal columns in PostGIS, rounding in some providers), while
"<= min" selects exactly the same features and never misses one.
"""

import datetime as _dt
import math

from qgis.core import QgsExpression, QgsFeatureRequest

from . import profiling_engine as pe
from .compat import NO_GEOMETRY_FLAG

SET, ADD, REMOVE = 'set', 'add', 'remove'
ZOOM, PAN, NONE = 'zoom', 'pan', 'none'

_JD_OFFSET = 1721425


# ----------------------------------------------------------------------
# Literals per kind. Temporal kinds are stored as numbers by the engine
# (Julian day / epoch seconds / seconds since midnight), so they are
# compared through an equivalent numeric expression on the column.
# ----------------------------------------------------------------------
def col(field_name):
    return QgsExpression.quotedColumnRef(field_name)


def _num(x):
    if isinstance(x, bool):
        return 'TRUE' if x else 'FALSE'
    if isinstance(x, int):
        return str(x)
    return repr(float(x))


def jd_to_iso(jd):
    return _dt.date.fromordinal(int(jd) - _JD_OFFSET).isoformat()


def _column_and_value(field_name, kind, x, rounding=None):
    """Returns (column_expression, literal) comparing in the same domain."""
    c = col(field_name)
    if kind == pe.DATE:
        if rounding == 'ceil':
            x = math.ceil(x)
        elif rounding == 'floor':
            x = math.floor(x)
        else:
            x = round(x)
        return c, "to_date('{}')".format(jd_to_iso(x))
    if kind == pe.DATETIME:
        return 'epoch({})'.format(c), _num(x * 1000.0)
    if kind == pe.TIME:
        # second() truncates to whole seconds, so compare whole seconds.
        return ('(hour({0})*3600 + minute({0})*60 + second({0}))'.format(c),
                _num(int(math.floor(x))))
    return c, _num(x)


def expression_for_null(field_name):
    return '{} IS NULL'.format(col(field_name))


def expression_for_min(field_name, kind, value):
    c, v = _column_and_value(field_name, kind, value, 'floor')
    return '{} <= {}'.format(c, v)


def expression_for_max(field_name, kind, value):
    c, v = _column_and_value(field_name, kind, value, 'ceil')
    return '{} >= {}'.format(c, v)


def expression_for_range(field_name, kind, low, high, upper_inclusive=False):
    c, lo = _column_and_value(field_name, kind, low, 'ceil')
    if upper_inclusive:
        _c, hi = _column_and_value(field_name, kind, high, 'floor')
        op = '<='
    else:
        _c, hi = _column_and_value(field_name, kind, high, 'ceil')
        op = '<'
    return '{0} >= {1} AND {0} {2} {3}'.format(c, lo, op, hi)


def expression_for_below(field_name, kind, value):
    """Histogram 'under' overflow bin: everything strictly below value."""
    c, v = _column_and_value(field_name, kind, value, 'ceil')
    return '{} < {}'.format(c, v)


def expression_for_above(field_name, kind, value):
    """Histogram 'over' overflow bin: everything strictly above value."""
    c, v = _column_and_value(field_name, kind, value, 'floor')
    return '{} > {}'.format(c, v)


def expression_for_outliers(field_name, kind, lower, upper):
    c, lo = _column_and_value(field_name, kind, lower, 'ceil')
    _c, hi = _column_and_value(field_name, kind, upper, 'floor')
    return '{0} < {1} OR {0} > {2}'.format(c, lo, hi)


def expression_for_category(field_name, kind, raw):
    """`raw` is the original value kept by the engine for that category
    (None for kinds that can't be selected by value)."""
    if kind == pe.BOOLEAN:
        return '{} = {}'.format(col(field_name), 'TRUE' if raw else 'FALSE')
    if raw is None:
        return None
    return '{} = {}'.format(col(field_name), QgsExpression.quotedValue(raw))


def expression_for_other_categories(field_name, kind, raws):
    """Every non-null value that is NOT one of the shown categories."""
    raws = [r for r in raws if r is not None]
    c = col(field_name)
    if not raws:
        return '{} IS NOT NULL'.format(c)
    if kind == pe.BOOLEAN:
        values = ', '.join('TRUE' if r else 'FALSE' for r in raws)
    else:
        values = ', '.join(QgsExpression.quotedValue(r) for r in raws)
    return '{0} IS NOT NULL AND {0} NOT IN ({1})'.format(c, values)


# ----------------------------------------------------------------------
# Selection
# ----------------------------------------------------------------------
def ids_for_expression(layer, expression, limit_to_ids=None):
    expr = QgsExpression(expression)
    if expr.hasParserError():
        raise ValueError('Invalid expression: {}'.format(expr.parserErrorString()))
    request = QgsFeatureRequest(expr)
    # Our expressions never use the geometry, so don't fetch it.
    request.setFlags(NO_GEOMETRY_FLAG)
    ids = [f.id() for f in layer.getFeatures(request)]
    if limit_to_ids is not None:
        # "Selected features only" scope: a fid filter and an expression
        # filter can't be combined in one request on every QGIS version,
        # so intersect here.
        allowed = set(limit_to_ids)
        ids = [i for i in ids if i in allowed]
    return ids


def apply_selection(layer, ids, behavior=SET):
    if behavior == ADD:
        layer.select(ids)
    elif behavior == REMOVE:
        layer.deselect(ids)
    else:
        layer.selectByIds(ids)


def frame(canvas, layer, ids, zoom_mode=ZOOM, flash=True):
    if canvas is None or not ids:
        return
    if zoom_mode == ZOOM:
        canvas.zoomToFeatureIds(layer, ids)
    elif zoom_mode == PAN:
        canvas.panToFeatureIds(layer, ids)
    if flash:
        canvas.flashFeatureIds(layer, ids)


def select(layer, expression, canvas=None, behavior=SET, zoom_mode=ZOOM,
           flash=True, limit_to_ids=None):
    """Selects the features matching `expression`. Returns the ids."""
    if layer is None or not expression:
        return []
    ids = ids_for_expression(layer, expression, limit_to_ids)
    apply_selection(layer, ids, behavior)
    if behavior != REMOVE:
        frame(canvas, layer, ids, zoom_mode, flash)
    return ids


def flash(layer, expression, canvas, limit_to_ids=None):
    ids = ids_for_expression(layer, expression, limit_to_ids)
    if canvas is not None and ids:
        canvas.flashFeatureIds(layer, ids)
    return ids


def open_attribute_table(iface, layer, expression):
    """Opens QGIS' own attribute table filtered with the expression."""
    iface.showAttributeTable(layer, expression)
