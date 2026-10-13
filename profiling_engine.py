# -*- coding: utf-8 -*-
"""
profiling_engine.py
--------------------
QGIS-facing side of the profiler: decides how each field is treated
(its "kind"), reads the features in ONE pass and feeds plain Python
values to the streaming accumulators in stats_core.py.

No Qt widgets here, so it runs unchanged on the main thread, inside a
background QgsTask (profiling_task.py) or from a future Processing
algorithm.

Performance notes (large layers such as cadastral datasets):
  - one single pass for every field, geometry never read
    (NoGeometry + setSubsetOfAttributes);
  - attributes are read once per feature with feat.attributes() and then
    accessed by index (feat[name] resolves the name on every call);
  - cancel / progress callbacks are only polled every few hundred
    features;
  - the reported feature count is the number of features actually read,
    not layer.featureCount(), which can be -1 (unknown) or an estimate
    (e.g. PostGIS with "use estimated metadata").
"""

import datetime as _dt
import time
from dataclasses import dataclass, field as dc_field
from typing import Any, Dict, List, Optional

from qgis.core import QgsFeatureRequest

from . import stats_core
from .compat import NO_GEOMETRY_FLAG, QVariant, unwrap, variant_type_id

# ----------------------------------------------------------------------
# Field kinds
# ----------------------------------------------------------------------
INTEGER = 'integer'
DECIMAL = 'decimal'
TEXT = 'text'
DATE = 'date'
DATETIME = 'datetime'
TIME = 'time'
BOOLEAN = 'boolean'
OTHER = 'other'

NUMERIC_KINDS = (INTEGER, DECIMAL)
TEMPORAL_KINDS = (DATE, DATETIME, TIME)
CATEGORICAL_KINDS = (TEXT, BOOLEAN, OTHER)

_FLOAT_TYPE_IDS = {6, 38}       # Double, Float
_BOOL_TYPE_ID = 1
_DATE_TYPE_ID = 14
_TIME_TYPE_ID = 15
_DATETIME_TYPE_ID = 16
_STRING_TYPE_IDS = {7, 10}      # Char, String

# Julian day number of 0001-01-01 minus one: lets us convert Python
# dates (proleptic ordinal) to the same Julian day QDate uses.
_JD_OFFSET = 1721425

# Features processed between two accumulator flushes / cancel checks.
BATCH_SIZE = 8192


def field_kind(field):
    """Classifies a QgsField into one of the kinds above."""
    t = variant_type_id(field)
    if t == _BOOL_TYPE_ID:
        return BOOLEAN
    if field.isNumeric():
        return DECIMAL if t in _FLOAT_TYPE_IDS else INTEGER
    if t == _DATE_TYPE_ID:
        return DATE
    if t == _DATETIME_TYPE_ID:
        return DATETIME
    if t == _TIME_TYPE_ID:
        return TIME
    if t in _STRING_TYPE_IDS:
        return TEXT
    return OTHER


# ----------------------------------------------------------------------
# Value converters: attribute value -> number (numeric/temporal kinds)
# or -> (key, raw) (categorical kinds). Return None to count as NULL.
# ----------------------------------------------------------------------
def _to_number(v):
    if v.__class__ in (int, float):
        return v
    if isinstance(v, bool):
        return int(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _date_to_jd(v):
    if hasattr(v, 'toJulianDay'):
        return v.toJulianDay() if v.isValid() else None
    if isinstance(v, _dt.datetime):
        return v.date().toordinal() + _JD_OFFSET
    if isinstance(v, _dt.date):
        return v.toordinal() + _JD_OFFSET
    return None


def _datetime_to_epoch(v):
    if hasattr(v, 'toMSecsSinceEpoch'):
        return v.toMSecsSinceEpoch() / 1000.0 if v.isValid() else None
    if isinstance(v, _dt.datetime):
        return v.timestamp()
    if hasattr(v, 'toJulianDay'):  # a QDate in a datetime column
        return None
    return None


def _time_to_seconds(v):
    if hasattr(v, 'msecsSinceStartOfDay'):
        return v.msecsSinceStartOfDay() / 1000.0 if v.isValid() else None
    if isinstance(v, _dt.time):
        return v.hour * 3600 + v.minute * 60 + v.second + v.microsecond / 1e6
    return None


NUMBER_CONVERTERS = {
    INTEGER: _to_number,
    DECIMAL: _to_number,
    DATE: _date_to_jd,
    DATETIME: _datetime_to_epoch,
    TIME: _time_to_seconds,
}


def category_raw(kind, key):
    """Original value for a category key (used in selection expressions)."""
    if kind == BOOLEAN:
        return key == 'true'
    if kind == TEXT:
        return key
    return None


def category_key(kind, v):
    """Returns (key, raw) for a categorical value."""
    if kind == BOOLEAN:
        b = bool(v)
        return ('true' if b else 'false'), b
    if kind == TEXT:
        s = v if v.__class__ is str else str(v)
        return s, s
    # OTHER: arrays, maps, binary... only countable, never selectable.
    return str(v), None


# ----------------------------------------------------------------------
# Results
# ----------------------------------------------------------------------
@dataclass
class FieldProfile:
    name: str
    alias: str
    type_name: str
    kind: str
    total: int = 0
    null_count: int = 0
    count: int = 0                      # non-null values
    distinct: Optional[int] = None
    distinct_capped: bool = False
    is_unique: bool = False
    # numeric / temporal (temporal values are Julian days, epoch seconds
    # or seconds since midnight; the UI formats them back)
    min: Any = None
    max: Any = None
    sum: Any = None
    mean: Optional[float] = None
    std: Optional[float] = None
    range: Any = None
    median: Optional[float] = None
    p25: Optional[float] = None
    p75: Optional[float] = None
    outlier_lower: Optional[float] = None
    outlier_upper: Optional[float] = None
    outlier_count: Optional[int] = None
    outlier_estimated: bool = False
    outlier_note: Optional[str] = None
    histogram: Optional[dict] = None
    sampled: bool = False
    sample_size: int = 0
    # categorical
    top: List[tuple] = dc_field(default_factory=list)   # (label, raw, count)
    top_exact: bool = True
    other_count: int = 0

    @property
    def is_numeric(self):
        return self.kind in NUMERIC_KINDS

    @property
    def is_temporal(self):
        return self.kind in TEMPORAL_KINDS

    @property
    def is_categorical(self):
        return self.kind in CATEGORICAL_KINDS

    @property
    def is_continuous(self):
        return self.kind in NUMERIC_KINDS or self.kind in TEMPORAL_KINDS

    @property
    def null_ratio(self):
        return (self.null_count / self.total) if self.total else 0.0


@dataclass
class LayerProfile:
    layer_id: str
    layer_name: str
    feature_count: int
    selected_only: bool
    fields: Dict[str, FieldProfile]
    elapsed: float
    sample_limit: int


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------
def list_fields(layer_or_fields):
    """Basic metadata for every field (used to show the table instantly,
    before profiling finishes)."""
    fields = layer_or_fields.fields() if hasattr(layer_or_fields, 'fields') else layer_or_fields
    return [
        {
            'name': f.name(),
            'alias': f.alias() or f.name(),
            'type_name': f.typeName(),
            'kind': field_kind(f),
        }
        for f in fields
    ]


def profile_features(get_features, fields, field_names=None, total_hint=None,
                     filter_fids=None, on_progress=None, is_canceled=None,
                     outlier_k=stats_core.DEFAULT_OUTLIER_K,
                     sample_limit=stats_core.SAMPLE_LIMIT):
    """Profiles the requested fields in a SINGLE pass.

    :param get_features: callable(QgsFeatureRequest) -> feature iterator
        (layer.getFeatures, or a QgsVectorLayerFeatureSource's
        getFeatures when running in a background task).
    :param fields: QgsFields of the layer.
    :param field_names: names to profile (default: all fields).
    :param total_hint: expected number of features, only used for the
        progress percentage (may be None / -1 when unknown).
    :param filter_fids: optional iterable of feature ids ("selected
        features only" mode).
    :returns: (feature_count, {name: FieldProfile}) or None if canceled.
    """
    if field_names is None:
        field_names = [f.name() for f in fields]

    plan = []          # (name, index, kind, accumulator)
    for name in field_names:
        idx = fields.indexFromName(name)
        if idx < 0:
            continue
        kind = field_kind(fields.at(idx))
        if kind in CATEGORICAL_KINDS:
            acc = stats_core.CategoryAccumulator()
        else:
            acc = stats_core.NumericAccumulator(sample_limit=sample_limit)
        plan.append((name, idx, kind, acc))

    request = QgsFeatureRequest()
    request.setFlags(NO_GEOMETRY_FLAG)
    if plan:
        request.setSubsetOfAttributes([p[1] for p in plan])
    else:
        request.setNoAttributes()
    if filter_fids is not None:
        request.setFilterFids(list(filter_fids))

    # Values are buffered per field and handed to the accumulators in
    # batches (numpy / Counter do the maths in C). The per-value Python
    # work is reduced to a type check and a list.append.
    buffers = [[] for _ in plan]
    handlers = [(buffers[i].append, idx, None if kind in CATEGORICAL_KINDS else NUMBER_CONVERTERS[kind], kind)
                for i, (_name, idx, kind, _acc) in enumerate(plan)]

    def flush():
        for (_name, _idx, _kind, acc), buf in zip(plan, buffers):
            if buf:
                acc.add_many(buf)
                buf.clear()

    known_total = total_hint if (total_hint is not None and total_hint > 0) else None
    batch = BATCH_SIZE

    qvariant = QVariant
    processed = 0
    for feat in get_features(request):
        attrs = feat.attributes()
        for append, idx, conv, kind in handlers:
            v = attrs[idx]
            if v is None:
                continue
            cls = v.__class__
            if cls is qvariant:          # NULL on QGIS 3 (or a wrapped value)
                v = unwrap(v)
                if v is None:
                    continue
                cls = v.__class__
            if conv is None:             # categorical
                if cls is str:
                    append(v)
                else:
                    append(category_key(kind, v)[0])
            elif cls is float:
                if v == v:               # skip NaN (counted as null)
                    append(v)
            elif cls is int:
                append(v)
            else:
                x = conv(v)
                if x is not None and x == x:
                    append(x)

        processed += 1
        if processed % batch == 0:
            flush()
            if is_canceled is not None and is_canceled():
                return None
            if on_progress is not None:
                on_progress(min(99.0, processed * 100.0 / known_total) if known_total else -1.0)

    flush()
    if on_progress is not None:
        on_progress(100.0)

    profiles = {}
    for i, (name, idx, kind, acc) in enumerate(plan):
        qfield = fields.at(idx)
        prof = FieldProfile(
            name=name,
            alias=qfield.alias() or name,
            type_name=qfield.typeName(),
            kind=kind,
            total=processed,
            null_count=processed - acc.n,
        )
        if kind in CATEGORICAL_KINDS:
            s = acc.summary()
            prof.count = s['count']
            prof.distinct = s['distinct']
            prof.distinct_capped = s['distinct_capped']
            prof.is_unique = s['is_unique']
            prof.top = [(key, category_raw(kind, key), count) for key, count in s['top']]
            prof.top_exact = s['top_exact']
            prof.other_count = s['other_count']
        else:
            s = acc.summary(outlier_k=outlier_k,
                            integer=kind in (INTEGER, DATE))
            prof.count = s['count']
            prof.distinct = s['distinct']
            prof.distinct_capped = s['distinct_capped']
            prof.is_unique = s['is_unique']
            for key in ('min', 'max', 'mean', 'std', 'range', 'median', 'p25', 'p75',
                        'outlier_lower', 'outlier_upper', 'outlier_count',
                        'outlier_estimated', 'outlier_note', 'histogram',
                        'sampled', 'sample_size'):
                setattr(prof, key, s[key])
            # A sum of dates or times means nothing.
            prof.sum = s['sum'] if kind in NUMERIC_KINDS else None
        profiles[name] = prof

    return processed, profiles


def profile_layer(layer, field_names=None, selected_only=False, **kwargs):
    """Synchronous convenience wrapper (main thread, tests, Processing)."""
    started = time.perf_counter()
    fids = layer.selectedFeatureIds() if selected_only else None
    total = len(fids) if fids is not None else layer.featureCount()
    result = profile_features(layer.getFeatures, layer.fields(), field_names,
                              total_hint=total, filter_fids=fids, **kwargs)
    if result is None:
        return None
    count, profiles = result
    return LayerProfile(
        layer_id=layer.id(),
        layer_name=layer.name(),
        feature_count=count,
        selected_only=selected_only,
        fields=profiles,
        elapsed=time.perf_counter() - started,
        sample_limit=kwargs.get('sample_limit', stats_core.SAMPLE_LIMIT),
    )


def read_values(layer, field_name, kind, fids, limit=20_000, seed=7):
    """Reads (converted) values of one field for a set of feature ids,
    used to overlay the current map selection on the detail chart.

    If there are more ids than `limit`, a random subset is read and the
    returned scale factor tells the caller how to scale counts up.
    Returns (values, scale).
    """
    import random

    fids = list(fids)
    if not fids:
        return [], 1.0
    scale = 1.0
    if len(fids) > limit:
        scale = len(fids) / float(limit)
        fids = random.Random(seed).sample(fids, limit)

    idx = layer.fields().indexFromName(field_name)
    if idx < 0:
        return [], 1.0
    request = QgsFeatureRequest()
    request.setFlags(NO_GEOMETRY_FLAG)
    request.setSubsetOfAttributes([idx])
    request.setFilterFids(fids)

    out = []
    conv = NUMBER_CONVERTERS.get(kind)
    for feat in layer.getFeatures(request):
        v = unwrap(feat.attributes()[idx])
        if v is None:
            continue
        if conv is None:
            out.append(category_key(kind, v)[0])
        else:
            x = conv(v)
            if x is not None:
                out.append(x)
    return out, scale
