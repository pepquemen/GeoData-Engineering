# -*- coding: utf-8 -*-
"""
symbology_helper.py
--------------------
Applies quick "explore by field" renderers to a vector layer, triggered
from the Symbology button that appears next to a field in the dock's
field list:

  - Numeric fields: a graduated (choropleth) renderer, white -> dark
    blue, so higher values read as visually "heavier".
  - Text / categorical fields: a categorized renderer with one distinct
    hue per unique value, evenly spaced around the color wheel so
    categories stay visually distinct from one another.

Kept separate from the dock (UI) so it can be reused later from a
Processing algorithm or a right-click context menu.
"""

from qgis.core import (
    QgsCategorizedSymbolRenderer,
    QgsGradientColorRamp,
    QgsGraduatedSymbolRenderer,
    QgsRendererCategory,
    QgsSymbol,
)
from qgis.PyQt.QtGui import QColor

NUMERIC_RAMP_LOW = QColor(255, 255, 255)   # white
NUMERIC_RAMP_HIGH = QColor(8, 48, 107)     # dark blue

DEFAULT_NUMERIC_CLASSES = 5

# Guard rail: a near-unique text field (e.g. an ID column) would produce
# hundreds of categories and an unreadable legend, so we cap it and let
# the dock warn the user instead of applying the renderer.
MAX_CATEGORIES = 40


def apply_numeric_renderer(layer, field_name, classes=DEFAULT_NUMERIC_CLASSES):
    """Applies a white -> dark blue graduated (quantile) renderer based
    on field_name. Returns True if the renderer was applied."""
    symbol = QgsSymbol.defaultSymbol(layer.geometryType())
    ramp = QgsGradientColorRamp(NUMERIC_RAMP_LOW, NUMERIC_RAMP_HIGH)

    renderer = QgsGraduatedSymbolRenderer.createRenderer(
        layer,
        field_name,
        classes,
        QgsGraduatedSymbolRenderer.Mode.Quantile,
        symbol,
        ramp,
    )
    if renderer is None:
        return False

    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return True


def apply_categorical_renderer(layer, field_name):
    """Applies a categorized renderer to field_name, one distinct hue per
    unique value. Returns True if the renderer was applied, False if
    there are no values or too many distinct categories to be readable
    (see MAX_CATEGORIES)."""
    idx = layer.fields().indexFromName(field_name)
    if idx < 0:
        return False

    unique_values = sorted(layer.uniqueValues(idx), key=lambda v: str(v))
    if not unique_values or len(unique_values) > MAX_CATEGORIES:
        return False

    categories = []
    total = len(unique_values)
    for i, value in enumerate(unique_values):
        hue = int(360 * i / total)
        color = QColor.fromHsv(hue, 200, 230)
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setColor(color)
        categories.append(QgsRendererCategory(value, symbol, str(value)))

    renderer = QgsCategorizedSymbolRenderer(field_name, categories)
    layer.setRenderer(renderer)
    layer.triggerRepaint()
    return True


def apply_field_renderer(layer, field_name, is_numeric):
    """Convenience dispatcher used by the dock's Symbology button."""
    if is_numeric:
        return apply_numeric_renderer(layer, field_name)
    return apply_categorical_renderer(layer, field_name)
