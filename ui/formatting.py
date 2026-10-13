# -*- coding: utf-8 -*-
"""
ui/formatting.py
----------------
Turns raw statistics into display text, kind-aware and locale-aware:

  - integers keep thousands separators: 12.345.678 (never 1.235e+07);
  - decimals get adaptive precision (more decimals for small numbers),
    scientific notation only for really tiny / huge magnitudes;
  - dates, datetimes and times (stored as numbers by the engine) are
    shown as ISO dates/times; their spread (std dev, range) as durations.

The number format follows the QGIS locale (QLocale default), so a
Spanish QGIS shows 1.234,56.
"""

import math

from qgis.PyQt.QtCore import QDate, QDateTime, QLocale, QTime

from .. import profiling_engine as pe

DASH = '—'


def _locale():
    return QLocale()


def fmt_int(value):
    try:
        return _locale().toString(int(value))
    except (OverflowError, ValueError, TypeError):
        return str(value)


def fmt_decimal(value):
    if value is None:
        return DASH
    x = float(value)
    if x != x:
        return DASH
    if math.isinf(x):
        return '∞' if x > 0 else '-∞'
    ax = abs(x)
    if ax == 0:
        return '0'
    if ax >= 1e15 or ax < 1e-4:
        return _locale().toString(x, 'e', 3)
    if ax >= 1000:
        decimals = 2
    elif ax >= 1:
        decimals = 3
    else:
        decimals = 4
    text = _locale().toString(x, 'f', decimals)
    point = _locale().decimalPoint()
    point = point if isinstance(point, str) else str(point)
    if point in text:
        text = text.rstrip('0').rstrip(point)
    return text


def fmt_number(value, integer=False):
    if value is None:
        return DASH
    if integer and (isinstance(value, int) or float(value).is_integer()):
        return fmt_int(value)
    return fmt_decimal(value)


def fmt_count(value):
    return DASH if value is None else fmt_int(value)


def fmt_percent(ratio):
    if ratio is None:
        return DASH
    pct = ratio * 100.0
    if pct == 0:
        return '0 %'
    if pct < 0.1:
        return '<0{}1 %'.format(_locale().decimalPoint())
    return '{} %'.format(_locale().toString(pct, 'f', 1))


def fmt_duration(seconds):
    if seconds is None:
        return DASH
    s = abs(float(seconds))
    if s >= 86400 * 365.25 * 2:
        return '{} a'.format(fmt_decimal(s / (86400 * 365.25)))
    if s >= 86400:
        return '{} d'.format(fmt_decimal(s / 86400))
    if s >= 3600:
        return '{} h'.format(fmt_decimal(s / 3600))
    if s >= 60:
        return '{} min'.format(fmt_decimal(s / 60))
    return '{} s'.format(fmt_decimal(s))


def fmt_temporal(kind, x):
    if x is None:
        return DASH
    if kind == pe.DATE:
        return QDate.fromJulianDay(int(round(x))).toString('yyyy-MM-dd')
    if kind == pe.DATETIME:
        return QDateTime.fromMSecsSinceEpoch(int(round(x * 1000))).toString('yyyy-MM-dd HH:mm')
    if kind == pe.TIME:
        return QTime(0, 0).addMSecs(int(round(x * 1000))).toString('HH:mm:ss')
    return fmt_decimal(x)


def spread_seconds(kind, x):
    """Converts a spread (std dev / range) to seconds for temporal kinds."""
    if x is None:
        return None
    return x * 86400.0 if kind == pe.DATE else x


def fmt_value(profile, x):
    """A value on the field's own scale (min, max, mean, median, P25...)."""
    kind = profile.kind
    if kind in pe.TEMPORAL_KINDS:
        return fmt_temporal(kind, x)
    return fmt_number(x, integer=(kind == pe.INTEGER))


def fmt_spread(profile, x):
    """A difference on the field's scale (std dev, range)."""
    kind = profile.kind
    if kind in pe.TEMPORAL_KINDS:
        return fmt_duration(spread_seconds(kind, x))
    return fmt_number(x, integer=(kind == pe.INTEGER))


KIND_BADGES = {
    pe.INTEGER: 'INT',
    pe.DECIMAL: 'DEC',
    pe.TEXT: 'TXT',
    pe.DATE: 'DATE',
    pe.DATETIME: 'DTM',
    pe.TIME: 'TIME',
    pe.BOOLEAN: 'BOOL',
    pe.OTHER: 'OBJ',
}
