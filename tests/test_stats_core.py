# -*- coding: utf-8 -*-
"""Pure statistics tests (no QGIS needed)."""

import random
import statistics

import numpy as np
import pytest

from conftest import load_stats_core

sc = load_stats_core()


def _num(values, batch=997, **kw):
    # Odd batch size so batch boundaries never line up with anything.
    acc = sc.NumericAccumulator(**kw)
    values = list(values)
    for i in range(0, len(values), batch):
        acc.add_many(values[i:i + batch])
    return acc


def test_single_add_matches_batches():
    a = sc.NumericAccumulator()
    for v in [5, 1, 9, 3]:
        a.add(v)
    b = _num([5, 1, 9, 3], batch=3)
    assert a.summary() == b.summary()


def test_exact_statistics_small_field():
    values = [3, 1, 4, 1, 5, 9, 2, 6, 5, 3, 5]
    s = _num(values).summary(integer=True)
    assert s['count'] == len(values)
    assert s['min'] == 1 and s['max'] == 9
    assert s['sum'] == sum(values) and isinstance(s['sum'], int)
    assert s['mean'] == pytest.approx(statistics.mean(values))
    assert s['std'] == pytest.approx(statistics.stdev(values))
    assert s['median'] == pytest.approx(statistics.median(values))
    assert s['distinct'] == len(set(values))
    assert not s['sampled']


def test_welford_is_stable_for_large_magnitudes():
    # Projected coordinates (UTM northing ~ 4.5 million) with tiny spread.
    base = 4_512_345.0
    values = [base + i * 0.001 for i in range(10_000)]
    s = _num(values).summary()
    assert s['std'] == pytest.approx(np.std(values, ddof=1), rel=1e-6)


def test_sampled_histogram_spans_true_range_and_is_scaled():
    rng = random.Random(1)
    values = [rng.gauss(100, 15) for _ in range(30_000)]
    values += [10_000.0]   # extreme maximum, very unlikely to be sampled
    acc = _num(values, sample_limit=2_000)
    s = acc.summary()
    h = s['histogram']
    assert s['sampled'] and h['estimated']
    # The extreme maximum is beyond the fences: it goes to the overflow
    # bin instead of stretching the axis.
    assert h['edges'][-1] < 10_000.0 and h['over'] is not None
    # bar heights add up to the population, not to the sample
    total = sum(h['counts']) + (h['under'] or 0) + (h['over'] or 0)
    assert total == pytest.approx(len(values), rel=1e-9)
    assert len(h['counts']) <= sc.MAX_BINS


def test_reservoir_is_uniform_on_sorted_input():
    # Sorted input is the worst case for a biased sampler.
    n, k = 200_000, 5_000
    acc = _num(range(n), sample_limit=k)
    assert len(acc.sample) == k
    assert np.mean(acc.sample) == pytest.approx((n - 1) / 2, rel=0.03)
    assert np.percentile(acc.sample, 90) == pytest.approx(0.9 * n, rel=0.03)


def test_bins_are_capped_for_heavy_tails():
    values = [0.0] * 1000 + [float(i) for i in range(1, 1000)] + [1e12]
    s = _num(values).summary()
    assert len(s['histogram']['counts']) <= sc.MAX_BINS


def test_heavy_tail_gets_overflow_bins():
    rng = random.Random(5)
    values = [rng.uniform(100, 200) for _ in range(5000)] + [1e6, 2e6, 5e7, -1e5]
    h = _num(values).summary()['histogram']
    assert h['edges'][0] > 0 and h['edges'][-1] < 1000          # core range only
    assert h['under'] == 1 and h['over'] == 3
    assert sum(h['counts']) + h['under'] + h['over'] == len(values)


def test_no_overflow_bins_without_heavy_tails():
    h = _num(range(1000)).summary()['histogram']
    assert h['under'] is None and h['over'] is None


def test_integer_field_gets_one_bin_per_value():
    values = [0, 1, 1, 2, 2, 2, 5]
    h = _num(values).summary(integer=True)['histogram']
    assert len(h['counts']) == 6          # 0..5
    assert h['counts'][2] == 3


def test_iqr_zero_reports_no_outliers():
    values = [0] * 900 + list(range(1, 101))
    s = _num(values).summary()
    assert s['outlier_note'] == 'iqr_zero'
    assert s['outlier_count'] is None


def test_outliers_exact_when_not_sampled():
    values = list(range(100)) + [1000, -1000]
    s = _num(values).summary()
    assert s['outlier_count'] == 2 and not s['outlier_estimated']


def test_distinct_cap():
    s = _num(range(50), distinct_limit=10).summary()
    assert s['distinct'] is None and s['distinct_capped']


def test_unique_flag():
    assert _num([1, 2, 3]).summary()['is_unique']
    assert not _num([1, 2, 2]).summary()['is_unique']


# ----------------------------------------------------------------------
def _cat(keys, capacity=sc.TOP_K_CAPACITY, batch=1000):
    acc = sc.CategoryAccumulator(capacity=capacity)
    for i in range(0, len(keys), batch):
        acc.add_many(keys[i:i + batch])
    return acc.summary()


def test_categories_exact_below_capacity():
    s = _cat(['a'] * 5 + ['b'] * 3 + ['c'])
    assert s['top_exact']
    assert s['top'] == [('a', 5), ('b', 3), ('c', 1)]
    assert s['other_count'] == 0


def test_frequent_late_category_is_not_lost():
    # 2,000 rare categories first (layer sorted by another column), then a
    # very frequent one: the old "first N seen" cap dropped it entirely.
    keys = ['rare{}'.format(i) for i in range(2000)] + ['FREQUENT'] * 3000
    s = _cat(keys, capacity=100)
    assert not s['top_exact']
    assert s['top'][0][0] == 'FREQUENT'
    # Misra-Gries guarantee: count >= true - n / (capacity + 1)
    assert s['top'][0][1] >= 3000 - len(keys) / 101


def test_category_unique_and_distinct():
    s = _cat(['x', 'y', 'z'])
    assert s['is_unique'] and s['distinct'] == 3
