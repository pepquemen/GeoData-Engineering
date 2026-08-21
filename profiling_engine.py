# -*- coding: utf-8 -*-
"""
profiling_engine.py
--------------------
Pure data-profiling logic: does NOT depend on Qt widgets, only on the
QGIS API (qgis.core) and numpy. This lets it be reused both from the
dock (UI) and from profiling_task.py's background QgsTask.

Performance note (this module targets large layers such as cadastral
datasets, which can have hundreds of thousands of features with heavy
geometries):

  - profile_all_fields() computes statistics for EVERY requested field in
    a SINGLE pass over the layer (one feature read = one update to every
    field's running statistics), instead of one pass per field. The
    feature request excludes geometry entirely, since none of this needs
    it. This is what makes it practical to run on very large layers.
  - Running statistics use streaming/online algorithms, so we never hold
    the full column of values in memory: Welford's algorithm for mean/
    variance (also numerically stable, unlike naive sum-of-squares, which
    matters for fields with large values like cadastral coordinates or
    areas), and reservoir sampling (bounded to HISTOGRAM_SAMPLE_LIMIT
    values) for percentiles and the histogram/frequency chart, since QGIS
    has no built-in "distribution shape" statistic. Distinct-value
    tracking is also capped (DISTINCT_TRACK_LIMIT) so a near-unique field
    (e.g. a cadastral reference) can't blow up memory.
  - We do NOT precompute or store feature id lists for nulls/min/max/
    outliers. The dock instead builds a small QGIS expression from the
    already-known statistic (e.g. "field IS NULL", or "field = <max>")
    and resolves it to feature ids lazily, only when the user actually
    clicks that cell. See selection_helper.py.
  - profiling_task.py wraps profile_all_fields() in a background QgsTask
    so the QGIS UI thread is never blocked, however large the layer is.
"""

import random

try:
    import numpy as np
except ImportError:  # numpy should always be available in QGIS, but just in case
    np = None

from qgis.core import NULL, QgsFeatureRequest, QgsField

# Note: we use QgsField.isNumeric(), which is stable across QGIS versions
# (this avoids depending on QVariant.Type vs QMetaType.Type, which differs
# between the Qt5 and Qt6 builds of QGIS 3.28 -> 3.4x).

# Percentiles/histogram/value_counts are computed from at most this many
# values per field (reservoir sample for numeric fields; first-seen cap
# for text categories), so a single huge field can't blow up memory or
# runtime on a very large layer.
HISTOGRAM_SAMPLE_LIMIT = 50_000

# A numeric field's set of distinct values is only tracked up to this
# size; beyond it we stop counting and report an approximate ">N".
DISTINCT_TRACK_LIMIT = 20_000

# A text field's category breakdown is only tracked up to this many
# distinct categories; beyond it we stop adding new ones and report an
# approximate ">N" (existing tracked categories keep accumulating counts).
MAX_CATEGORIES_TRACKED = 500

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


# ----------------------------------------------------------------------
# Streaming accumulators
# ----------------------------------------------------------------------

def _new_numeric_acc():
    return {
        'count': 0,          # non-null count
        'null_count': 0,
        'min': None,
        'max': None,
        'mean': 0.0,          # Welford running mean
        'm2': 0.0,            # Welford running sum of squared diffs
        'distinct': set(),
        'distinct_capped': False,
        'reservoir': [],      # up to HISTOGRAM_SAMPLE_LIMIT raw values
    }


def _new_text_acc():
    return {
        'count': 0,
        'null_count': 0,
        'value_counts': {},
        'categories_capped': False,
    }


def _update_numeric(acc, raw_value, rng):
    if acc['min'] is None or raw_value < acc['min']:
        acc['min'] = raw_value
    if acc['max'] is None or raw_value > acc['max']:
        acc['max'] = raw_value

    value = float(raw_value)
    acc['count'] += 1
    n = acc['count']
    delta = value - acc['mean']
    acc['mean'] += delta / n
    delta2 = value - acc['mean']
    acc['m2'] += delta * delta2

    if not acc['distinct_capped']:
        acc['distinct'].add(raw_value)
        if len(acc['distinct']) > DISTINCT_TRACK_LIMIT:
            acc['distinct_capped'] = True

    # Reservoir sampling (Algorithm R): keeps a uniform random sample of
    # size HISTOGRAM_SAMPLE_LIMIT without ever holding more than that many
    # values in memory, regardless of how many features the layer has.
    reservoir = acc['reservoir']
    if len(reservoir) < HISTOGRAM_SAMPLE_LIMIT:
        reservoir.append(value)
    else:
        j = rng.randint(0, n - 1)
        if j < HISTOGRAM_SAMPLE_LIMIT:
            reservoir[j] = value


def _update_text(acc, value_str):
    acc['count'] += 1
    vc = acc['value_counts']
    if value_str in vc:
        vc[value_str] += 1
    elif not acc['categories_capped']:
        vc[value_str] = 1
        if len(vc) > MAX_CATEGORIES_TRACKED:
            acc['categories_capped'] = True
    # else: category cap already reached and this is a brand-new category;
    # dropped rather than growing the dict without bound.


def _finalize_numeric(acc, field_type_name, total):
    n = acc['count']
    distinct_count = len(acc['distinct'])
    if acc['distinct_capped']:
        distinct_count = f'>{distinct_count}'

    profile = {
        'field_type': field_type_name,
        'is_numeric': True,
        'count': total,
        'null_count': acc['null_count'],
        'distinct_count': distinct_count,
        'sampled': n > HISTOGRAM_SAMPLE_LIMIT,
    }

    if n == 0:
        profile.update({
            'min_value': None, 'max_value': None, 'range': None, 'sum': None,
            'mean': None, 'median': None, 'std_dev': None, 'p25': None, 'p75': None,
            'outlier_bounds': None, 'outlier_count': 0, 'histogram': None,
        })
        return profile

    variance = acc['m2'] / (n - 1) if n > 1 else 0.0
    std_dev = variance ** 0.5
    total_sum = acc['mean'] * n

    p25 = p75 = median_val = None
    histogram = None
    outlier_bounds = None
    outlier_count = 0

    sample = acc['reservoir']
    if sample and np is not None:
        arr = np.array(sample, dtype=float)
        p25, median_val, p75 = (float(v) for v in np.percentile(arr, [25, 50, 75]))
        counts, edges = np.histogram(arr, bins='auto')
        histogram = {'counts': counts.tolist(), 'edges': edges.tolist()}

        iqr = p75 - p25
        lower = p25 - 1.5 * iqr
        upper = p75 + 1.5 * iqr
        outlier_bounds = {'lower': lower, 'upper': upper}
        # Estimated from the sample and scaled up to the full population;
        # the actual selection on click is always exact (it re-evaluates
        # the expression against the whole layer), only this displayed
        # count is an estimate for very large fields.
        outlier_fraction = float(np.count_nonzero((arr < lower) | (arr > upper))) / len(sample)
        outlier_count = int(round(outlier_fraction * n))

    profile.update({
        'min_value': acc['min'],
        'max_value': acc['max'],
        'range': acc['max'] - acc['min'],
        'sum': total_sum,
        'mean': acc['mean'],
        'median': median_val,
        'std_dev': std_dev,
        'p25': p25,
        'p75': p75,
        'outlier_bounds': outlier_bounds,
        'outlier_count': outlier_count,
        'histogram': histogram,
    })
    return profile


def _finalize_text(acc, field_type_name, total):
    distinct_count = len(acc['value_counts'])
    if acc['categories_capped']:
        distinct_count = f'>{distinct_count}'
    return {
        'field_type': field_type_name,
        'is_numeric': False,
        'count': total,
        'null_count': acc['null_count'],
        'distinct_count': distinct_count,
        'value_counts': acc['value_counts'],
        'sampled': False,  # every feature was scanned; only category variety is capped
    }


# ----------------------------------------------------------------------
# Public entry points
# ----------------------------------------------------------------------

def profile_all_fields(get_features, fields, field_names, total_count,
                        on_progress=None, is_canceled=None):
    """Profiles every field in field_names with a SINGLE pass over the
    layer's features.

    :param get_features: callable(QgsFeatureRequest) -> iterable of
        QgsFeature. Pass layer.getFeatures for the main thread, or a
        QgsVectorLayerFeatureSource's getFeatures when running inside a
        background QgsTask (see profiling_task.py).
    :param fields: the layer's QgsFields (layer.fields()).
    :param field_names: list of field names to profile.
    :param total_count: layer.featureCount(), used for progress reporting.
    :param on_progress: optional callable(percent: int).
    :param is_canceled: optional callable() -> bool; checked periodically
        so a background task can be canceled cleanly. Returns None if
        canceled partway through.
    :returns: dict field_name -> profile dict, or None if canceled.
    """
    field_indexes = {}
    numeric_flags = {}
    type_names = {}
    for name in field_names:
        idx = fields.indexFromName(name)
        field_indexes[name] = idx
        if idx >= 0:
            field = fields.at(idx)
            numeric_flags[name] = field.isNumeric()
            type_names[name] = field.typeName()
        else:
            numeric_flags[name] = False
            type_names[name] = '-'

    accumulators = {
        name: (_new_numeric_acc() if numeric_flags[name] else _new_text_acc())
        for name in field_names
    }

    request = QgsFeatureRequest()
    valid_indexes = [idx for idx in field_indexes.values() if idx >= 0]
    if valid_indexes:
        request.setSubsetOfAttributes(valid_indexes)
    request.setFlags(QgsFeatureRequest.Flag.NoGeometry)

    # Fixed seed: profiling the same static layer twice in a row gives a
    # stable sample, so the chart doesn't jitter between successive
    # "Refresh" clicks on unchanged data.
    rng = random.Random(42)

    processed = 0
    report_every = max(1, total_count // 100) if total_count else 1

    for feat in get_features(request):
        if is_canceled is not None and is_canceled():
            return None

        for name in field_names:
            if field_indexes[name] < 0:
                continue
            val = feat[name]
            acc = accumulators[name]
            if val == NULL or val is None:
                acc['null_count'] += 1
                continue
            if numeric_flags[name]:
                _update_numeric(acc, val, rng)
            else:
                _update_text(acc, str(val))

        processed += 1
        if on_progress is not None and processed % report_every == 0:
            pct = int(processed / total_count * 100) if total_count else 0
            on_progress(min(pct, 99))

    if on_progress is not None:
        on_progress(100)

    profiles = {}
    for name in field_names:
        acc = accumulators[name]
        if numeric_flags[name]:
            profile = _finalize_numeric(acc, type_names[name], total_count)
        else:
            profile = _finalize_text(acc, type_names[name], total_count)
        profile['field'] = name
        profiles[name] = profile

    return profiles


def profile_field(layer, field_name):
    """Convenience wrapper for profiling a single field synchronously on
    the main thread (used where a background task would be overkill)."""
    result = profile_all_fields(
        layer.getFeatures, layer.fields(), [field_name], layer.featureCount(),
    )
    return result[field_name] if result else None
