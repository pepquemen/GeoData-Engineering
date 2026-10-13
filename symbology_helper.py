# -*- coding: utf-8 -*-
"""
symbology_helper.py
--------------------
"Explore by field" renderers, triggered from the field cell's
symbology button or the table's context menu.

Non-destructive: the renderer is applied in a NEW named style of the
layer's style manager ("DET · <field>"), never on top of the user's
style. The original style stays available in the layer's Styles menu
and restore_original_style() switches back to it.

  - numeric fields: graduated renderer (quantiles, 5 classes) on a
    light-blue -> dark-blue ramp. The light end is NOT white, so low
    classes stay visible on a white map background;
  - date / datetime fields: categorized by year("field");
  - text / boolean fields: categorized, one colour per value, hues
    spread with the golden angle so neighbouring categories never get
    similar colours (capped at MAX_CATEGORIES for a readable legend).
"""

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsClassificationQuantile,
    QgsExpression,
    QgsExpressionContext,
    QgsExpressionContextUtils,
    QgsFeatureRequest,
    QgsGradientColorRamp,
    QgsGraduatedSymbolRenderer,
    QgsRendererCategory,
    QgsSymbol,
)
from qgis.PyQt.QtGui import QColor

from . import profiling_engine as pe
from .compat import NO_GEOMETRY_FLAG
from .translation import tr

RAMP_LOW = QColor('#c6dbef')
RAMP_HIGH = QColor('#08306b')
DEFAULT_NUMERIC_CLASSES = 5
MAX_CATEGORIES = 40

STYLE_PREFIX = 'DET · '
_PROP_ORIGINAL = 'data_engineering_toolkit/original_style'


class SymbologyError(Exception):
    pass


def _switch_to_det_style(layer, field_name):
    """Makes a dedicated style current, remembering the user's one."""
    sm = layer.styleManager()
    current = sm.currentStyle()
    if not current.startswith(STYLE_PREFIX):
        layer.setCustomProperty(_PROP_ORIGINAL, current)
    name = STYLE_PREFIX + field_name
    if name not in sm.styles():
        # Copy of the current state, so labels, opacity, etc. are kept.
        sm.addStyleFromLayer(name)
    sm.setCurrentStyle(name)
    return name


def original_style_name(layer):
    name = layer.customProperty(_PROP_ORIGINAL)
    if name and name in layer.styleManager().styles():
        return name
    return None


def has_det_style_active(layer):
    return layer.styleManager().currentStyle().startswith(STYLE_PREFIX)


def restore_original_style(layer):
    name = original_style_name(layer)
    if name is None:
        return False
    layer.styleManager().setCurrentStyle(name)
    layer.triggerRepaint()
    return True


def _category_color(i):
    hue = int((i * 137.508) % 360)
    sat = 170 if i % 2 else 210
    val = 215 if (i // 2) % 2 else 185
    return QColor.fromHsv(hue, sat, val)


def _numeric_renderer(layer, field_name, classes):
    renderer = QgsGraduatedSymbolRenderer(field_name)
    renderer.setClassificationMethod(QgsClassificationQuantile())
    renderer.setSourceSymbol(QgsSymbol.defaultSymbol(layer.geometryType()))
    renderer.updateClasses(layer, classes)
    renderer.updateColorRamp(QgsGradientColorRamp(RAMP_LOW, RAMP_HIGH))
    if not renderer.ranges():
        raise SymbologyError(tr('The field has no values to classify.'))
    return renderer


def _unique_values(layer, attribute_or_expression, is_expression, limit):
    if not is_expression:
        idx = layer.fields().indexFromName(attribute_or_expression)
        return list(layer.uniqueValues(idx, limit + 1))
    expr = QgsExpression(attribute_or_expression)
    context = QgsExpressionContext(QgsExpressionContextUtils.globalProjectLayerScopes(layer))
    expr.prepare(context)
    request = QgsFeatureRequest()
    request.setFlags(NO_GEOMETRY_FLAG)
    values = set()
    for feat in layer.getFeatures(request):
        context.setFeature(feat)
        value = expr.evaluate(context)
        if value is not None:
            values.add(value)
        if len(values) > limit:
            break
    return list(values)


def _categorized_renderer(layer, attribute, is_expression=False):
    values = _unique_values(layer, attribute, is_expression, MAX_CATEGORIES)
    values = [v for v in values if v is not None and not (hasattr(v, 'isNull') and v.isNull())]
    if not values:
        raise SymbologyError(tr('The field has no values to classify.'))
    if len(values) > MAX_CATEGORIES:
        raise SymbologyError(
            tr('Too many distinct values (more than {}) for a readable legend.').format(MAX_CATEGORIES))
    values.sort(key=lambda v: (0, v, '') if isinstance(v, (int, float)) else (1, 0, str(v)))
    categories = []
    for i, value in enumerate(values):
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(_category_color(i))
        categories.append(QgsRendererCategory(value, symbol, str(value)))
    return QgsCategorizedSymbolRenderer(attribute, categories)


def apply_field_renderer(layer, field_name, kind):
    """Symbolizes `layer` by `field_name` in a dedicated style.
    Returns the name of the style created/updated. Raises SymbologyError
    with a user-facing message when it can't."""
    if kind in pe.NUMERIC_KINDS:
        renderer = _numeric_renderer(layer, field_name, DEFAULT_NUMERIC_CLASSES)
    elif kind in (pe.DATE, pe.DATETIME):
        renderer = _categorized_renderer(
            layer, 'year({})'.format(QgsExpression.quotedColumnRef(field_name)), is_expression=True)
    elif kind in (pe.TEXT, pe.BOOLEAN):
        renderer = _categorized_renderer(layer, field_name)
    else:
        raise SymbologyError(tr('This field type cannot be symbolized.'))

    style_name = _switch_to_det_style(layer, field_name)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return style_name
