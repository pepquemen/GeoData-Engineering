# -*- coding: utf-8 -*-
"""
compat.py
---------
Small shims for APIs that moved between QGIS 3.28 -> 3.4x and between
the Qt5 and Qt6 (QGIS 4) builds, so the rest of the plugin can use one
spelling everywhere.
"""

from qgis.core import Qgis, QgsFeatureRequest, QgsMapLayerProxyModel, QgsTask

try:
    from qgis.PyQt.QtCore import QVariant
except ImportError:  # pragma: no cover - a Qt6 build without QVariant
    class QVariant:  # noqa: D401 - placeholder that never matches a value
        pass

# --- Layer filter for QgsMapLayerComboBox -----------------------------
# QGIS >= 3.34 exposes Qgis.LayerFilter; older versions only have
# QgsMapLayerProxyModel.Filter (removed in QGIS 4).
if hasattr(Qgis, 'LayerFilter'):
    VECTOR_LAYER_FILTER = Qgis.LayerFilter.VectorLayer
else:  # pragma: no cover - QGIS < 3.34
    VECTOR_LAYER_FILTER = QgsMapLayerProxyModel.Filter.VectorLayer

# --- Feature request "no geometry" flag --------------------------------
if hasattr(Qgis, 'FeatureRequestFlag'):
    NO_GEOMETRY_FLAG = Qgis.FeatureRequestFlag.NoGeometry
else:  # pragma: no cover - QGIS < 3.36
    NO_GEOMETRY_FLAG = QgsFeatureRequest.Flag.NoGeometry


# --- QgsTask "can cancel" flag -----------------------------------------
# Several QGIS enums moved from class scope to the Qgis namespace over
# time; accept either location.
if hasattr(QgsTask, 'Flag') and hasattr(QgsTask.Flag, 'CanCancel'):
    TASK_CAN_CANCEL = QgsTask.Flag.CanCancel
else:  # pragma: no cover
    TASK_CAN_CANCEL = Qgis.TaskFlag.CanCancel


def variant_type_id(field):
    """Returns the field's type as a plain int.

    QgsField.type() is a QVariant.Type on Qt5 and a QMetaType.Type on Qt6,
    but the numeric ids we care about are identical in both
    (Bool=1, Int=2, UInt=3, LongLong=4, ULongLong=5, Double=6,
    String=10, Date=14, Time=15, DateTime=16, Float=38).
    """
    t = field.type()
    t = getattr(t, 'value', t)
    try:
        return int(t)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return -1


_QVARIANT = QVariant


def unwrap(value):
    """Normalizes an attribute value: returns None for every flavour of
    NULL (QVariant NULL on QGIS 3, None on QGIS 4, float NaN), the plain
    Python value otherwise."""
    if value is None:
        return None
    if value.__class__ is _QVARIANT:
        if value.isNull():
            return None
        value = value.value()
        if value is None:
            return None
    if value.__class__ is float and value != value:  # NaN
        return None
    return value
