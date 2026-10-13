# -*- coding: utf-8 -*-
"""
stats_core.py
-------------
Pure streaming statistics: NO QGIS and NO Qt imports, only the standard
library and numpy. Everything that knows about QGIS (feature iteration,
field types, NULL handling, date conversions) lives in
profiling_engine.py; this module only ever sees plain Python numbers and
hashable keys, which keeps it fast and trivially unit-testable.

Two accumulators:

  NumericAccumulator
      For numeric fields, and for date/datetime/time fields once
      profiling_engine.py has converted them to numbers (Julian day,
      epoch seconds, seconds since midnight).
      - count / min / max / sum are EXACT (every value is seen).
      - mean / std use Welford's online algorithm (numerically stable
        for large magnitudes such as projected coordinates or areas).
      - percentiles, histogram and outliers come from a uniform
        reservoir sample (Algorithm L) of at most `sample_limit` values,
        so memory is bounded however large the layer is. When the
        sample holds every value they are exact.
      - the histogram always spans the TRUE [min, max] (not the sample's),
        its bin count is capped (MAX_BINS), and when the field is sampled
        bar heights are scaled back up to the full population.

  CategoryAccumulator
      For text / boolean / other fields.
      - exact value counts while the number of distinct values stays
        within `capacity`;
      - beyond that, switches to the Misra-Gries "frequent items"
        algorithm: memory stays at `capacity` counters and every value
        whose true frequency is above n / (capacity + 1) is guaranteed
        to be kept, wherever it appears in the layer. (The previous
        "first N categories seen" cap silently lost frequent categories
        that happened to appear late, e.g. in a layer sorted by another
        column.) Counts then become lower-bound estimates and the result
        says so (`top_exact = False`).
"""

import math
import random
from collections import Counter

import numpy as np

# Percentiles / histogram / outliers are computed from at most this many
# values per field.
SAMPLE_LIMIT = 50_000

# Exact distinct-value tracking is capped at this many values per field;
# beyond it the distinct count is reported as "> DISTINCT_LIMIT".
DISTINCT_LIMIT = 20_000

# Counters kept per categorical field (exact below, Misra-Gries above).
TOP_K_CAPACITY = 500

# Upper bound for histogram bins, so heavy-tailed data can't produce
# thousands of unreadable (and slow) bins.
MAX_BINS = 40

# Number of categories reported (the rest is folded into "other").
TOP_N = 15

DEFAULT_OUTLIER_K = 1.5


class NumericAccumulator:
    __slots__ = (
        'n', 'min', 'max', 'total', 'mean', 'm2',
        'distinct', 'distinct_count', 'distinct_capped',
        'sample', 'sample_limit', 'distinct_limit', '_rng', '_w', '_next',
    )

    def __init__(self, sample_limit=SAMPLE_LIMIT, distinct_limit=DISTINCT_LIMIT, seed=42):
        self.n = 0
        self.min = None
        self.max = None
        self.total = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.distinct = set()
        self.distinct_count = 0
        self.distinct_capped = False
        self.sample = []
        self.sample_limit = sample_limit
        self.distinct_limit = distinct_limit
        # Fixed seed: profiling the same unchanged layer twice gives the
        # same sample, so charts don't jitter between refreshes.
        self._rng = random.Random(seed)
        self._w = 1.0
        self._next = None

    def add(self, x):
        self.add_many((x,))

    def add_many(self, values):
        """Adds a batch of numbers. The profiler buffers values per field
        and calls this every few thousand features, so all the heavy
        lifting runs in numpy instead of one Python call per value."""
        m = len(values)
        if not m:
            return
        try:
            arr = np.asarray(values)
            if arr.dtype.kind not in 'iuf':
                arr = arr.astype(float)
        except (OverflowError, TypeError, ValueError):
            arr = np.asarray([float(v) for v in values])
        farr = arr.astype(float, copy=False)

        # --- exact extremes and sum (keep Python ints for integer data) ---
        bmin = arr.min().item()
        bmax = arr.max().item()
        if self.min is None or bmin < self.min:
            self.min = bmin
        if self.max is None or bmax > self.max:
            self.max = bmax
        if arr.dtype.kind in 'iu':
            # int64 sums wrap around silently on overflow: only trust numpy
            # when the batch can't overflow, otherwise sum in Python.
            if max(abs(bmin), abs(bmax)) * m < 2 ** 62:
                self.total += int(arr.sum())
            else:
                self.total += sum(int(v) for v in values)
        else:
            self.total += float(farr.sum())   # pairwise summation

        # --- mean / variance: merge batch moments (Chan et al.) ---------
        n_a = self.n
        n = n_a + m
        mean_b = float(farr.mean())
        m2_b = float(((farr - mean_b) ** 2).sum())
        delta = mean_b - self.mean
        self.mean += delta * m / n
        self.m2 += m2_b + delta * delta * n_a * m / n
        self.n = n

        # --- distinct values (capped) -----------------------------------
        if not self.distinct_capped:
            d = self.distinct
            d.update(np.unique(arr).tolist())
            if len(d) > self.distinct_limit:
                self.distinct_capped = True
                self.distinct_count = len(d)
                self.distinct = None  # free the memory, we only report "> limit"

        # --- reservoir sample, Algorithm L (Li, 1994) ---------------------
        # Instead of a random draw per value (Algorithm R), it computes
        # how many values to SKIP before the next replacement, so a batch
        # only costs a few random draws once the reservoir is full.
        k = self.sample_limit
        sample = self.sample
        taken = 0
        if len(sample) < k:
            taken = min(k - len(sample), m)
            sample.extend(farr[:taken].tolist())
            if len(sample) == k:
                self._schedule_next(n_a + taken)
        if len(sample) == k:
            end = n_a + m
            while self._next <= end:
                sample[self._rng.randrange(k)] = float(farr[self._next - n_a - 1])
                self._schedule_next(self._next)

    def _schedule_next(self, n):
        rng = self._rng
        k = self.sample_limit
        self._w *= math.exp(math.log(rng.random() or 1e-300) / k)
        skip = math.floor(math.log(rng.random() or 1e-300) / math.log1p(-self._w))
        self._next = n + skip + 1

    # ------------------------------------------------------------------
    def summary(self, outlier_k=DEFAULT_OUTLIER_K, max_bins=MAX_BINS, integer=False):
        """Returns a dict with the final statistics.

        :param integer: True when the underlying values are integers
            (integer fields, or dates as Julian days). Enables one bin per
            integer value when the range is small enough, which gives a
            much more readable chart for codes such as "number of floors".
        """
        n = self.n
        sampled = n > len(self.sample)
        out = {
            'count': n,
            'distinct': None if self.distinct_capped else len(self.distinct),
            'distinct_capped': self.distinct_capped,
            'is_unique': (not self.distinct_capped) and n > 0 and len(self.distinct) == n,
            'min': self.min,
            'max': self.max,
            'sum': self.total if n else None,
            'mean': self.mean if n else None,
            'std': None,
            'range': (self.max - self.min) if n else None,
            'median': None,
            'p25': None,
            'p75': None,
            'outlier_lower': None,
            'outlier_upper': None,
            'outlier_count': None,
            'outlier_estimated': False,
            'outlier_note': None,
            'histogram': None,
            'sampled': sampled,
            'sample_size': len(self.sample),
        }
        if n == 0:
            return out

        out['std'] = math.sqrt(self.m2 / (n - 1)) if n > 1 else 0.0

        arr = np.asarray(self.sample, dtype=float)
        p25, median, p75 = (float(v) for v in np.percentile(arr, [25, 50, 75]))
        out.update({'p25': p25, 'median': median, 'p75': p75})

        # --- Outliers (Tukey fences on the IQR) -------------------------
        iqr = p75 - p25
        if n < 4:
            out['outlier_note'] = 'too_few'
        elif iqr <= 0:
            # More than half the values are identical (typical for fields
            # that are mostly 0): every other value would be flagged, which
            # is noise rather than insight, so we don't report outliers.
            out['outlier_note'] = 'iqr_zero'
        else:
            lower = p25 - outlier_k * iqr
            upper = p75 + outlier_k * iqr
            hits = int(np.count_nonzero((arr < lower) | (arr > upper)))
            if sampled:
                hits = int(round(hits * n / len(arr)))
            out.update({
                'outlier_lower': lower,
                'outlier_upper': upper,
                'outlier_count': hits,
                'outlier_estimated': sampled,
            })

        fences = (out['outlier_lower'], out['outlier_upper']) if out['outlier_lower'] is not None else None
        out['histogram'] = self._histogram(arr, n, sampled, iqr, max_bins, integer, fences)
        return out

    def _histogram(self, arr, n, sampled, iqr, max_bins, integer, fences=None):
        """Histogram over the true [min, max].

        Heavy tails (a few huge values, very common in cadastral values,
        areas, prices...) would squash every other value into the first
        bar. So when the Tukey fences cover less than half of the range,
        bins are computed over the "core" range inside the fences and the
        tails are reported as two overflow bins ('under' / 'over'), which
        remain selectable. Every value lands in exactly one bin.
        """
        lo, hi = float(self.min), float(self.max)
        if lo == hi:
            return {'edges': [lo - 0.5, hi + 0.5], 'counts': [float(n)], 'estimated': False,
                    'under': None, 'over': None}

        core_lo, core_hi = lo, hi
        if fences is not None:
            a, b = max(lo, fences[0]), min(hi, fences[1])
            if integer:
                a, b = float(math.ceil(a)), float(math.floor(b))
            if b > a and (b - a) < 0.5 * (hi - lo):
                core_lo, core_hi = a, b

        span = core_hi - core_lo
        if integer and span < max_bins:
            # One bin per integer value, centred on it.
            edges = [core_lo - 0.5 + i for i in range(int(span) + 2)]
        else:
            # numpy's "auto" rule (min of Freedman-Diaconis and Sturges
            # bin widths), computed by hand so we can cap the number of
            # bins BEFORE allocating them: numpy itself can try to build
            # millions of bins for heavy-tailed data.
            m = len(arr)
            sturges_w = span / (math.log2(m) + 1.0)
            fd_w = 2.0 * iqr / (m ** (1.0 / 3.0)) if iqr > 0 else 0.0
            width = min(sturges_w, fd_w) if fd_w > 0 else sturges_w
            bins = int(math.ceil(span / width)) if width > 0 else 1
            bins = max(1, min(bins, max_bins))
            edges = np.linspace(core_lo, core_hi, bins + 1).tolist()

        counts, _ = np.histogram(arr, bins=edges)   # values outside edges are ignored
        counts = counts.astype(float)
        # An overflow bin only exists on a side that was actually clipped.
        under = float(np.count_nonzero(arr < edges[0])) if core_lo > lo else None
        over = float(np.count_nonzero(arr > edges[-1])) if core_hi < hi else None
        if sampled and len(arr):
            factor = n / len(arr)
            counts *= factor
            under = under * factor if under is not None else None
            over = over * factor if over is not None else None
        return {'edges': list(edges), 'counts': counts.tolist(), 'estimated': sampled,
                'under': under, 'over': over}


class CategoryAccumulator:
    __slots__ = (
        'n', 'counts', 'capacity', 'exact',
        'distinct', 'distinct_capped', 'distinct_limit',
    )

    def __init__(self, capacity=TOP_K_CAPACITY, distinct_limit=DISTINCT_LIMIT):
        self.n = 0
        self.counts = Counter()
        self.capacity = capacity
        self.exact = True
        self.distinct = set()
        self.distinct_capped = False
        self.distinct_limit = distinct_limit

    def add(self, key):
        self.add_many((key,))

    def add_many(self, keys):
        """Adds a batch of hashable keys (counted in C by Counter)."""
        if not keys:
            return
        batch = Counter(keys)
        self.n += len(keys)

        if not self.distinct_capped:
            d = self.distinct
            d.update(batch.keys())
            if len(d) > self.distinct_limit:
                self.distinct_capped = True
                self.distinct = None

        counts = self.counts
        counts.update(batch)
        if len(counts) > self.capacity:
            # Batched Misra-Gries: subtract the (capacity+1)-th largest
            # count from every counter and drop the non-positive ones.
            # Merged summaries keep the same guarantee: estimated counts
            # are lower bounds, off by at most n / (capacity + 1).
            self.exact = False
            threshold = sorted(counts.values(), reverse=True)[self.capacity]
            self.counts = Counter({k: v - threshold for k, v in counts.items() if v > threshold})

    def summary(self, top_n=TOP_N):
        n = self.n
        ranked = sorted(self.counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
        top = ranked[:top_n]
        shown = sum(c for _k, c in top)
        distinct = None if self.distinct_capped else len(self.distinct)
        return {
            'count': n,
            'distinct': distinct,
            'distinct_capped': self.distinct_capped,
            'is_unique': (not self.distinct_capped) and n > 0 and distinct == n,
            'top': top,                      # [(key, count)]
            'top_exact': self.exact,
            # With exact counts this is exact; with Misra-Gries it's an
            # upper bound (the shown counts are lower bounds).
            'other_count': max(0, n - shown),
        }
