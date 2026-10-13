# -*- coding: utf-8 -*-
"""
ui/stats_model.py
-----------------
Model/view version of the statistics grid (one row per field, one
column per statistic). Replaces the old QTableWidget so that:

  - columns sort by their real value (SORT_ROLE), not by display text
    ("10" < "9" bugs, "N/A" in the middle...);
  - a QSortFilterProxyModel filters fields by name from the search box;
  - custom delegates paint type badges, null bars and sparklines;
  - fields appear instantly (name + type) while profiling still runs.
"""

from qgis.PyQt.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt

from .. import profiling_engine as pe
from ..translation import tr
from . import formatting as fmt

SORT_ROLE = Qt.ItemDataRole.UserRole + 1
PROFILE_ROLE = Qt.ItemDataRole.UserRole + 2
KEY_ROLE = Qt.ItemDataRole.UserRole + 3
CLICK_ROLE = Qt.ItemDataRole.UserRole + 4
ROW_ROLE = Qt.ItemDataRole.UserRole + 5

PENDING = '···'


def columns():
    """(key, header, header tooltip). Built at call time so headers are
    translated with the active QGIS locale."""
    return [
        ('field', tr('FIELD'), tr('Field name (alias in grey when different)')),
        ('type', tr('TYPE'), tr('Data type. KEY = every value is unique (candidate key)')),
        ('dist', tr('DISTRIBUTION'), tr('Histogram (numbers, dates) or top categories (text)')),
        ('nulls', tr('NULLS'), tr('NULL values. Click to select them')),
        ('distinct', tr('DISTINCT'), tr('Number of distinct non-null values')),
        ('min', tr('MIN'), tr('Minimum. Click to select the features with it')),
        ('max', tr('MAX'), tr('Maximum. Click to select the features with it')),
        ('mean', tr('MEAN'), ''),
        ('median', tr('MEDIAN'), ''),
        ('std', tr('STD DEV'), tr('Standard deviation')),
        ('p25', tr('P25'), tr('25th percentile')),
        ('p75', tr('P75'), tr('75th percentile')),
        ('sum', tr('SUM'), ''),
        ('range', tr('RANGE'), tr('Max - min')),
        ('outliers', tr('OUTLIERS'), tr('Values outside the Tukey fences (IQR). Click to select them')),
    ]


NUMERIC_KEYS = ('min', 'max', 'mean', 'median', 'std', 'p25', 'p75', 'sum', 'range')
SAMPLE_KEYS = ('median', 'p25', 'p75')


class FieldRow:
    __slots__ = ('name', 'alias', 'kind', 'type_name', 'profile')

    def __init__(self, name, alias, kind, type_name, profile=None):
        self.name = name
        self.alias = alias
        self.kind = kind
        self.type_name = type_name
        self.profile = profile


class StatsTableModel(QAbstractTableModel):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cols = columns()
        self._keys = [c[0] for c in self._cols]
        self.rows = []
        self.layer_profile = None

    # -- structure -------------------------------------------------------
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._cols)

    def column_of(self, key):
        return self._keys.index(key)

    def key_of(self, column):
        return self._keys[column]

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._cols[section][1]
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._cols[section][2] or None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            key = self._keys[section]
            if key in ('field', 'type', 'dist'):
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    # -- content ---------------------------------------------------------
    def set_fields(self, field_infos):
        """Shows the fields immediately (statistics pending)."""
        self.beginResetModel()
        self.layer_profile = None
        self.rows = [FieldRow(f['name'], f['alias'], f['kind'], f['type_name']) for f in field_infos]
        self.endResetModel()

    def set_profile(self, layer_profile):
        self.beginResetModel()
        self.layer_profile = layer_profile
        rows = []
        for name, prof in layer_profile.fields.items():
            rows.append(FieldRow(name, prof.alias, prof.kind, prof.type_name, prof))
        self.rows = rows
        self.endResetModel()

    def clear(self):
        self.beginResetModel()
        self.rows = []
        self.layer_profile = None
        self.endResetModel()

    def row_for_field(self, name):
        for i, r in enumerate(self.rows):
            if r.name == name:
                return i
        return -1

    # -- data ------------------------------------------------------------
    @staticmethod
    def click_action(key, prof):
        """Which selection a click on this cell triggers (or None)."""
        if prof is None:
            return None
        if key == 'nulls' and prof.null_count > 0:
            return 'nulls'
        if key in ('min', 'max') and prof.is_continuous and getattr(prof, key) is not None:
            return key
        if key == 'outliers' and prof.outlier_count:
            return 'outliers'
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        key = self._keys[index.column()]
        prof = row.profile

        if role == ROW_ROLE:
            return row
        if role == PROFILE_ROLE:
            return prof
        if role == KEY_ROLE:
            return key
        if role == CLICK_ROLE:
            return self.click_action(key, prof)
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display(key, row, prof)
        if role == SORT_ROLE:
            return self._sort_value(key, row, prof)
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._tooltip(key, row, prof)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if key in ('field', 'type', 'dist'):
                return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def _display(self, key, row, prof):
        if key == 'field':
            return row.name
        if key == 'type':
            return fmt.KIND_BADGES.get(row.kind, '?')
        if key == 'dist':
            return ''
        if prof is None:
            return PENDING
        if key == 'nulls':
            return fmt.fmt_count(prof.null_count)
        if key == 'distinct':
            if prof.distinct_capped:
                return '>' + fmt.fmt_int(pe_distinct_limit())
            return fmt.fmt_count(prof.distinct)
        if key == 'outliers':
            if not prof.is_continuous:
                return fmt.DASH
            if prof.outlier_count is None:
                return fmt.DASH
            text = fmt.fmt_count(prof.outlier_count)
            return ('~' + text) if prof.outlier_estimated and prof.outlier_count else text
        if key in NUMERIC_KEYS:
            if not prof.is_continuous:
                return fmt.DASH
            value = getattr(prof, key)
            if key in ('std', 'range'):
                return fmt.fmt_spread(prof, value)
            if key == 'sum':
                return fmt.fmt_number(value, integer=prof.kind == pe.INTEGER) if prof.is_numeric else fmt.DASH
            if key == 'mean':
                return fmt.fmt_value(prof, value) if prof.is_temporal else fmt.fmt_decimal(value)
            text = fmt.fmt_value(prof, value)
            if key in SAMPLE_KEYS and prof.sampled and value is not None:
                return '~' + text
            return text
        return ''

    def _sort_value(self, key, row, prof):
        if key == 'field':
            return row.name.lower()
        if key == 'type':
            return fmt.KIND_BADGES.get(row.kind, '?')
        if prof is None:
            return -1e308
        if key == 'dist':
            return prof.null_ratio
        if key == 'nulls':
            return float(prof.null_count)
        if key == 'distinct':
            return float(pe_distinct_limit() + 1) if prof.distinct_capped else float(prof.distinct or 0)
        if key == 'outliers':
            return float(prof.outlier_count) if prof.outlier_count is not None else -1.0
        if key in NUMERIC_KEYS:
            value = getattr(prof, key, None)
            try:
                return float(value) if (value is not None and prof.is_continuous) else -1e308
            except (TypeError, ValueError):
                return -1e308
        return 0.0

    def _tooltip(self, key, row, prof):
        if key == 'field':
            lines = ['<b>{}</b>'.format(row.name)]
            if row.alias and row.alias != row.name:
                lines.append(tr('Alias: {}').format(row.alias))
            lines.append(tr('Provider type: {}').format(row.type_name))
            lines.append('<i>{}</i>'.format(tr('Hover and click the icon on the right to symbolize the layer by this field')))
            return '<br>'.join(lines)
        if prof is None:
            return tr('Profiling…')
        if key == 'type' and prof.is_unique:
            return tr('Every non-null value is unique: candidate key')
        if key == 'nulls':
            return tr('{} NULL values ({}) of {} features. Click to select them').format(
                fmt.fmt_count(prof.null_count), fmt.fmt_percent(prof.null_ratio), fmt.fmt_count(prof.total))
        if key == 'distinct' and prof.distinct_capped:
            return tr('More than {} distinct values: exact counting stops there to bound memory').format(
                fmt.fmt_int(pe_distinct_limit()))
        if key == 'outliers' and prof.is_continuous:
            if prof.outlier_note == 'iqr_zero':
                return tr('Not computed: more than half of the values are identical (IQR = 0), '
                          'so every other value would be flagged')
            if prof.outlier_note == 'too_few':
                return tr('Not computed: fewer than 4 values')
            if prof.outlier_count is not None:
                text = tr('Outside [{}, {}]').format(fmt.fmt_value(prof, prof.outlier_lower),
                                                     fmt.fmt_value(prof, prof.outlier_upper))
                if prof.outlier_estimated:
                    text += '<br>' + tr('Count estimated from a sample; the selection on click is exact')
                return text
        if key in SAMPLE_KEYS and prof.sampled:
            return tr('Estimated from a random sample of {} values').format(fmt.fmt_int(prof.sample_size))
        if key == 'dist' and prof.histogram and prof.histogram.get('estimated'):
            return tr('Bar heights estimated from a random sample of {} values').format(
                fmt.fmt_int(prof.sample_size))
        if key == 'dist' and prof.is_categorical and not prof.top_exact:
            return tr('Too many categories to count exactly: the most frequent ones are guaranteed, '
                      'their counts are lower bounds')
        if key in ('min', 'max') and self.click_action(key, prof):
            return tr('Click to select the features with this value')
        return None


def pe_distinct_limit():
    from .. import stats_core
    return stats_core.DISTINCT_LIMIT


class FieldFilterProxy(QSortFilterProxyModel):
    """Filters by field name or alias; sorts by SORT_ROLE."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSortRole(SORT_ROLE)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self._needle = ''

    def set_needle(self, text):
        self._needle = (text or '').strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, source_row, source_parent):
        if not self._needle:
            return True
        row = self.sourceModel().rows[source_row]
        return self._needle in row.name.lower() or self._needle in (row.alias or '').lower()
