# -*- coding: utf-8 -*-
"""
profiling_engine.py
--------------------
Pure data-profiling logic: does NOT depend on Qt widgets, only on the
QGIS API (qgis.core) and numpy. This lets it be reused both from the
dock (UI) and from a future Processing algorithm running in batch mode.

Every profiling function returns, in addition to the statistics, the
feature ids of interest (nulls, outliers, extremes) already computed,
so that selecting them on the map is instant and doesn't require
re-scanning the layer.
"""

from statistics import mean, median, pstdev, stdev

try:
    import numpy as np
except ImportError:  # numpy should always be available in QGIS, but just in case
    np = None

from qgis.core import QgsField, NULL

# Note: we use QgsField.isNumeric(), which is stable across QGIS versions
# (this avoids depending on QVariant.Type vs QMetaType.Type, which differs
# between the Qt5 and Qt6 builds of QGIS 3.28 -> 3.4x).

# Ordered list of statistic columns shown in the stats table, and whether
# a click on that column's cell should trigger a map selection.
NUMERIC_STAT_COLUMNS = [
    ('field_type', 'Field Type', False),
    ('count', 'Count', False),
    ('null_count', 'Null Count', True),
    ('distinct_count', 'Distinct', False),
    ('sum', 'Sum', False),
    ('min_value', 'Min', True),
    ('max_value', 'Max', True),
    ('range', 'Range', False),
    ('mean', 'Mean', False),
    ('median', 'Median', False),
    ('std_dev', 'Std Dev', False),
    ('p25', 'P25', False),
    ('p75', 'P75', False),
    ('outlier_count', 'Outliers (IQR)', True),
]

CATEGORICAL_STAT_COLUMNS = [
    ('field_type', 'Field Type', False),
    ('count', 'Count', False),
    ('null_count', 'Null Count', True),
    ('distinct_count', 'Distinct', False),
]

# Statistic keys that only make sense for numeric fields. Used by the
# "all fields at once" table to show "N/A" for non-numeric fields instead
# of a misleading 0 or dash.
_CATEGORICAL_KEYS = {key for key, _label, _clickable in CATEGORICAL_STAT_COLUMNS}
NUMERIC_ONLY_KEYS = {key for key, _label, _clickable in NUMERIC_STAT_COLUMNS} - _CATEGORICAL_KEYS


def stat_value(profile, key):
    """Returns the value for a given statistic key from a field profile,
    applying the numeric-only rule above. Centralized here so the "single
    field" and "all fields" table views in the dock stay consistent."""
    if key in NUMERIC_ONLY_KEYS and not profile.get('is_numeric'):
        return None
    if key == 'outlier_count':
        if not profile.get('is_numeric'):
            return None
        return len(profile.get('outlier_ids', []))
    return profile.get(key)


def is_numeric_field(layer, field_name):
    """Returns True if the given field of the layer is numeric."""
    idx = layer.fields().indexFromName(field_name)
    if idx < 0:
        return False
    field: QgsField = layer.fields().at(idx)
    return field.isNumeric()


def list_fields(layer):
    """Returns basic metadata for every field of the layer, used to
    populate the field selector on the left."""
    result = []
    for field in layer.fields():
        result.append({
            'name': field.name(),
            'alias': field.alias() or field.name(),
            'type_name': field.typeName(),
            'is_numeric': field.isNumeric(),
        })
    return result


def profile_field(layer, field_name):
    """Computes statistics for a field plus the feature ids of interest
    (nulls, outliers, minimum, maximum).

    Returns a dictionary with, among other keys:
      - field_type, count, null_count, distinct_count
      - null_ids: list[int]
      - min_value, max_value, min_ids, max_ids, range (numeric only)
      - sum, mean, median, std_dev, p25, p75 (numeric only)
      - outlier_ids: list[int] (IQR method, numeric only)
      - histogram: {'counts': [...], 'edges': [...]} (numeric only)
      - value_counts: {value: count} (non-numeric / categorical fields only)
    """
    numeric = is_numeric_field(layer, field_name)

    idx = layer.fields().indexFromName(field_name)
    field_type_name = layer.fields().at(idx).typeName() if idx >= 0 else '-'

    values = []          # non-null values
    value_to_ids = {}     # value -> list of feature ids (for categorical values and extremes)
    null_ids = []
    total = 0

    for feat in layer.getFeatures():
        total += 1
        val = feat[field_name]
        # QGIS represents null attribute values with the NULL sentinel
        # (not Python's None); comparing with `== NULL` is the way
        # recommended by PyQGIS. We also cover NaN in case the data comes
        # from an external numeric source (e.g. a join with pandas/numpy).
        if val == NULL or val is None or (
            isinstance(val, float) and np is not None and np.isnan(val)
        ):
            null_ids.append(feat.id())
            continue
        values.append(val)
        value_to_ids.setdefault(val, []).append(feat.id())

    profile = {
        'field': field_name,
        'field_type': field_type_name,
        'is_numeric': numeric,
        'count': total,
        'null_count': len(null_ids),
        'null_ids': null_ids,
        'distinct_count': len(value_to_ids),
    }

    if not values:
        return profile

    if numeric:
        arr = np.array(values, dtype=float) if np is not None else None
        v_min = min(values)
        v_max = max(values)
        profile.update({
            'min_value': v_min,
            'max_value': v_max,
            'min_ids': value_to_ids.get(v_min, []),
            'max_ids': value_to_ids.get(v_max, []),
            'range': v_max - v_min,
            'sum': sum(values),
            'mean': mean(values),
            'median': median(values),
            'std_dev': stdev(values) if len(values) >= 2 else pstdev(values),
        })

        if np is not None and len(values) >= 4:
            q1, q3 = np.percentile(arr, [25, 75])
            iqr = q3 - q1
            lower = q1 - 1.5 * iqr
            upper = q3 + 1.5 * iqr
            outlier_ids = [
                fid
                for val, ids in value_to_ids.items()
                for fid in ids
                if val < lower or val > upper
            ]
            profile['p25'] = q1
            profile['p75'] = q3
            profile['outlier_bounds'] = {'lower': lower, 'upper': upper}
            profile['outlier_ids'] = outlier_ids
        else:
            profile['p25'] = profile['p75'] = None
            profile['outlier_bounds'] = None
            profile['outlier_ids'] = []

        if np is not None:
            counts, edges = np.histogram(arr, bins='auto')
            profile['histogram'] = {
                'counts': counts.tolist(),
                'edges': edges.tolist(),
            }
    else:
        # Categorical / text field: use a value -> count breakdown.
        profile['value_counts'] = {
            str(val): len(ids) for val, ids in value_to_ids.items()
        }
        profile['value_to_ids'] = value_to_ids

    return profile
