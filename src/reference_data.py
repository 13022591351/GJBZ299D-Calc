"""Read normalized calculation tables and constants from database/standard_reference.sqlite."""

import math
import re
import sqlite3
import sys
import threading
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from .numeric import number_text


def _app_root() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _resolve_db_path() -> Path:
    primary = _app_root() / 'database/standard_reference.sqlite'
    if primary.is_file() or getattr(sys, 'frozen', False):
        return primary
    cwd_fallback = Path.cwd() / 'database/standard_reference.sqlite'
    return cwd_fallback if cwd_fallback.is_file() else primary


def _missing_db_error(path: Path) -> FileNotFoundError:
    if getattr(sys, 'frozen', False):
        return FileNotFoundError(
            f'未找到标准参考数据库文件：{path}。'
            '请完整解压独立发布压缩包，保持 database/standard_reference.sqlite 与可执行文件位于同一目录层级，切勿单独移动或复制可执行文件。'
        )
    return FileNotFoundError(
        f'未找到标准参考数据库文件：{path}。'
        '请确认工程根目录下存在 database/standard_reference.sqlite（若使用独立发布包，请完整解压并保留 database/ 目录结构）。'
    )


def _sqlite_ro_uri(path: Path, *, immutable: bool = False) -> str:
    uri = path.as_uri()
    if uri.startswith('file://') and not uri.startswith(('file:///', 'file://localhost/')):
        uri = 'file:////' + uri.removeprefix('file://')
    query = '?mode=ro&immutable=1' if immutable else '?mode=ro'
    return uri + query


def _connect_readonly(path: Path, *, immutable: bool = False) -> sqlite3.Connection:
    try:
        con = sqlite3.connect(_sqlite_ro_uri(path, immutable=immutable), uri=True)
    except sqlite3.OperationalError:
        con = sqlite3.connect(str(path))
        con.execute('PRAGMA query_only = ON')
    con.row_factory = sqlite3.Row
    return con


REFERENCE_PATH = _resolve_db_path()
_LOCAL = threading.local()


def _db():
    path = (REFERENCE_PATH if REFERENCE_PATH.is_file() else _resolve_db_path()).resolve()
    cached = getattr(_LOCAL, 'connection', None)
    if cached is not None and getattr(_LOCAL, 'path', None) == path:
        return cached
    if cached is not None:
        cached.close()
    if not path.is_file():
        raise _missing_db_error(path)
    con = _connect_readonly(path, immutable=True)
    _LOCAL.connection = con
    _LOCAL.path = path
    return con


def connection():
    path = (REFERENCE_PATH if REFERENCE_PATH.is_file() else _resolve_db_path()).resolve()
    if not path.is_file():
        raise _missing_db_error(path)
    return _connect_readonly(path, immutable=False)


@lru_cache(maxsize=1)
def catalog():
    return tuple(dict(r) for r in _db().execute(
        'SELECT id,number,caption,seq,sort_order FROM table_fragment ORDER BY sort_order'))


@lru_cache(maxsize=None)
def fragments(number):
    con = _db()
    result = []
    for row in con.execute('SELECT * FROM table_fragment WHERE number=? ORDER BY seq', (number,)):
        table = dict(row)
        table['cells'] = tuple(dict(r) for r in con.execute(
            'SELECT * FROM cell WHERE table_id=? ORDER BY row_no,column_no', (row['id'],)))
        table['grid'] = {(r, c): cell for cell in table['cells']
                         for r in range(cell['row_no'], cell['row_no'] + cell['row_span'])
                         for c in range(cell['column_no'], cell['column_no'] + cell['column_span'])}
        result.append(table)
    return tuple(result)


def compact(value):
    return re.sub(r'\s+', '', str(value)).replace('−', '-')


def equal(left, right):
    left, right = compact(left), compact(right)
    try:
        return Decimal(left) == Decimal(right)
    except InvalidOperation:
        return left == right


def result(number, candidates):
    # Repeated span coordinates refer to one source cell, not multiple values.
    unique = {cell['id']: (table, cell) for table, cell in candidates}
    if not unique:
        raise ValueError(f'表 {number} 无匹配表值')
    if len(unique) > 1:
        raise ValueError(f'表 {number} 匹配到多条表值')
    table, cell = next(iter(unique.values()))
    if cell['kind'] != 'number':
        raise ValueError(f'表 {number} 当前条件无可用数值（表中标注为“{cell["text"]}”）')
    return float(cell['decimal_text']), f'表 {number}；{cell["id"]}'


def _parse_temperature_tick(text):
    normalized = compact(text).replace('＜', '<').replace('＞', '>')
    if match := re.fullmatch(r'(-?\d+(?:\.\d+)?)', normalized):
        bound = Decimal(match[1])
        return bound, bound, text
    if match := re.fullmatch(r'≤(-?\d+(?:\.\d+)?)', normalized):
        return Decimal('-Infinity'), Decimal(match[1]), text
    if match := re.fullmatch(r'(-?\d+(?:\.\d+)?)[～~](-?\d+(?:\.\d+)?)', normalized):
        return Decimal(match[1]), Decimal(match[2]), text
    return None


def _to_exact_fraction(value, *, label='数值'):
    if isinstance(value, bool):
        raise ValueError(f'{label}必须是有效的有限数值')
    if isinstance(value, Fraction):
        return value
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f'{label}必须是有效的有限数值')
        return Fraction(value)
    if isinstance(value, (int, float)):
        try:
            finite_ok = math.isfinite(value)
        except OverflowError as error:
            raise ValueError(f'{label}超出有限数值范围') from error
        if not finite_ok:
            raise ValueError(f'{label}必须是有效的有限数值')
        return Fraction(Decimal(str(value)))
    raise ValueError(f'{label}必须是有效的有限数值')


def _ge_bound_fraction(bound: Decimal, x: Fraction) -> bool:
    if bound == Decimal('-Infinity'):
        return False
    return Fraction(bound) >= x


def _lt_fraction_bound(x: Fraction, bound: Decimal) -> bool:
    if bound == Decimal('-Infinity'):
        return False
    return x < Fraction(bound)


def _fraction_display(x: Fraction, fallback) -> str:
    try:
        val = float(x)
        if math.isfinite(val):
            return number_text(val)
    except OverflowError:
        pass
    return str(fallback)


def resolve_temperature_tick(number, value, raw_labels):
    """Select exact matching tick or ceil up to the next higher printed temperature bin."""
    x = _to_exact_fraction(value, label='温度')
    if x < Fraction(-27315, 100):
        raise ValueError('温度不得低于绝对零度（-273.15 ℃）')
    ticks = {}
    for label in raw_labels:
        parsed = _parse_temperature_tick(label)
        if parsed is not None:
            key = (parsed[0], parsed[1])
            ticks.setdefault(key, parsed)
    if not ticks:
        raise ValueError(f'表 {number} 未定义温度坐标')
    eligible = [tick for tick in ticks.values() if _ge_bound_fraction(tick[1], x)]
    if not eligible:
        max_high = max(tick[1] for tick in ticks.values())
        raise ValueError(
            f'表 {number} 无匹配表值（温度 {_fraction_display(x, value)} ℃ 超出表列上限 {number_text(float(max_high))} ℃）'
        )
    low, _high, selected_label = min(eligible, key=lambda tick: (tick[1], tick[0]))
    note = f'；温度向上取 {compact(selected_label)} ℃ 档' if _lt_fraction_bound(x, low) else ''
    return selected_label, note


def resolve_numeric_tick(number, value, ticks, *, axis_label='参数'):
    """Select exact matching numeric tick or ceil up to the next higher printed axis tick."""
    x = _to_exact_fraction(value, label=axis_label)
    if x <= 0:
        raise ValueError(f'{axis_label}须为正数')
    ordered = sorted(
        {(Fraction(Decimal(str(t))), int(t) if float(t).is_integer() else float(t)) for t in ticks},
        key=lambda item: item[0],
    )
    if not ordered:
        raise ValueError(f'表 {number} 未定义{axis_label}坐标')
    eligible = [item for item in ordered if item[0] >= x]
    if not eligible:
        max_tick = ordered[-1][1]
        raise ValueError(
            f'表 {number} 无匹配表值（{axis_label} {_fraction_display(x, value)} 超出表列上限 {number_text(max_tick)}）'
        )
    tick_frac, selected = eligible[0]
    note = f'；{axis_label}向上取 {number_text(selected)} 档' if tick_frac > x else ''
    return selected, note


def row_value(number, label, column=-1, *, label_column=None):
    candidates = []
    for table in fragments(number):
        target = table['columns'] - 1 if column == -1 else column
        for cell in table['cells']:
            if label_column is not None and cell['column_no'] != label_column:
                continue
            if equal(cell['text'], label):
                value = table['grid'].get((cell['row_no'], target))
                if value and value['id'] != cell['id']:
                    candidates.append((table, value))
    return result(number, candidates)


def ceil_row_value(number, axis_value, column=-1, *, label_column=0, first_row=1, axis_label='参数'):
    number_str = str(number)
    ticks = []
    for table in fragments(number_str):
        target = table['columns'] - 1 if column == -1 else column
        for row in range(first_row, table['rows']):
            axis_cell = table['grid'].get((row, label_column))
            val_cell = table['grid'].get((row, target))
            if (
                axis_cell
                and axis_cell['kind'] == 'number'
                and val_cell
                and val_cell['id'] != axis_cell['id']
                and val_cell['kind'] == 'number'
            ):
                ticks.append(Decimal(axis_cell['decimal_text']))
    tick, note = resolve_numeric_tick(number_str, axis_value, ticks, axis_label=axis_label)
    value, source = row_value(number_str, tick, column, label_column=label_column)
    return value, source + note


def temp_row_value(number, temp_C, column=-1, *, label_column=0):
    number_str = str(number)
    raw_labels = [
        cell['text']
        for table in fragments(number_str)
        for cell in table['cells']
        if cell['row_no'] > 0 and (label_column is None or cell['column_no'] == label_column)
    ]
    tick, note = resolve_temperature_tick(number_str, temp_C, raw_labels)
    value, source = row_value(number_str, tick, column, label_column=label_column)
    return value, source + note


def below_value(number, label):
    candidates = []
    for table in fragments(number):
        for cell in table['cells']:
            if equal(cell['text'], label):
                value = table['grid'].get((cell['row_no'] + cell['row_span'], cell['column_no']))
                if value:
                    candidates.append((table, value))
    return result(number, candidates)


def temp_below_value(number, temp_C):
    number_str = str(number)
    raw_labels = [
        cell['text']
        for table in fragments(number_str)
        for cell in table['cells']
        if cell['row_no'] % 2 == 0
        and cell['column_no'] > 0
        and table['grid'].get((cell['row_no'] + cell['row_span'], cell['column_no'])) is not None
    ]
    tick, note = resolve_temperature_tick(number_str, temp_C, raw_labels)
    candidates = []
    for table in fragments(number_str):
        for cell in table['cells']:
            if cell['row_no'] % 2 == 0 and equal(cell['text'], tick):
                value = table['grid'].get((cell['row_no'] + cell['row_span'], cell['column_no']))
                if value:
                    candidates.append((table, value))
    value, source = result(number_str, candidates)
    return value, source + note


def paired_value(number, axis):
    candidates = []
    for table in fragments(number):
        for cell in table['cells']:
            if cell['column_no'] % 2 == 0 and cell['row_no'] > 0 and equal(cell['text'], axis):
                candidates.append((table, table['grid'][cell['row_no'], cell['column_no'] + 1]))
    return result(number, candidates)


def ceil_paired_value(number, axis_value, *, axis_label='参数'):
    number_str = str(number)
    ticks = []
    for table in fragments(number_str):
        for cell in table['cells']:
            if cell['column_no'] % 2 == 0 and cell['row_no'] > 0 and cell['kind'] == 'number':
                partner = table['grid'].get((cell['row_no'], cell['column_no'] + 1))
                if partner is not None and partner['id'] != cell['id'] and partner['kind'] == 'number':
                    ticks.append(Decimal(cell['decimal_text']))
    tick, note = resolve_numeric_tick(number_str, axis_value, ticks, axis_label=axis_label)
    value, source = paired_value(number_str, tick)
    return value, source + note


def temp_paired_value(number, temp_C):
    number_str = str(number)
    raw_labels = []
    for table in fragments(number_str):
        for cell in table['cells']:
            if cell['column_no'] % 2 == 0 and cell['row_no'] > 0:
                partner = table['grid'].get((cell['row_no'], cell['column_no'] + 1))
                if partner is not None and partner['id'] != cell['id']:
                    raw_labels.append(cell['text'])
    tick, note = resolve_temperature_tick(number_str, temp_C, raw_labels)
    value, source = paired_value(number_str, tick)
    return value, source + note


def matrix_value(number, row_label, column_axis, *, label_column=None):
    candidates = []
    for table in fragments(number):
        columns = [c['column_no'] for c in table['cells']
                   if c['row_no'] == 1 and equal(c['text'], column_axis)]
        rows = [c['row_no'] for c in table['cells'] if c['row_no'] > 1
                and (label_column is None or c['column_no'] == label_column)
                and equal(c['text'], row_label)]
        for row in rows:
            for col in columns:
                candidates.append((table, table['grid'][row, col]))
    return result(number, candidates)


def temp_matrix_value(number, row_label, column_axis, *, label_column=None, temp_axis='row'):
    number_str = str(number)
    if temp_axis == 'row':
        raw_labels = [
            cell['text']
            for table in fragments(number_str)
            for cell in table['cells']
            if cell['row_no'] > 1 and (label_column is None or cell['column_no'] == label_column)
        ]
        row_label, note = resolve_temperature_tick(number_str, row_label, raw_labels)
    elif temp_axis == 'col':
        raw_labels = [
            cell['text']
            for table in fragments(number_str)
            for cell in table['cells']
            if cell['row_no'] == 1 and cell['column_no'] > 0
        ]
        column_axis, note = resolve_temperature_tick(number_str, column_axis, raw_labels)
    else:
        raise ValueError(f'未知温度轴方向：{temp_axis}')
    value, source = matrix_value(number_str, row_label, column_axis, label_column=label_column)
    return value, source + note


def conditions_value(number, conditions, column=-1):
    candidates = []
    for table in fragments(number):
        target = table['columns'] - 1 if column == -1 else column
        for row in range(1, table['rows']):
            if all(equal(table['grid'][row, col]['text'], label)
                   for col, label in conditions.items()):
                candidates.append((table, table['grid'][row, target]))
    return result(number, candidates)


def range_matches(label, value, symbol):
    """Compare the standard's explicit inequalities in ohms or microfarads."""
    expression = compact(label)
    terms = re.split(r'(≤|≥|<|>|=)', expression)
    if len(terms) not in (3, 5):
        return False
    if not terms[0]:
        terms[0] = symbol

    def quantity(term):
        if term == symbol:
            return Decimal(str(value))
        match = re.fullmatch(r'(\d+(?:\.\d+)?)(MΩ|kΩ|Ω|μF|µF|nF|pF)', term)
        if not match:
            raise ValueError(f'未建立范围单位对应：{label}')
        scale = {'MΩ': '1000000', 'kΩ': '1000', 'Ω': '1', 'μF': '1', 'µF': '1',
                 'nF': '.001', 'pF': '.000001'}[match[2]]
        return Decimal(match[1]) * Decimal(scale)

    numbers = [quantity(t) for t in terms[::2]]
    for left, operator, right in zip(numbers, terms[1::2], numbers[1:]):
        matches = {'≤': left <= right, '≥': left >= right, '<': left < right,
                   '>': left > right, '=': left == right}[operator]
        if not matches:
            return False
    return True


@dataclass(frozen=True)
class RangeSelection:
    value: float
    source: str
    interval: str
    axis: str
    cell_id: str
    public_source: str


def range_selection(number, value, symbol, *, conditions=None, range_column=0, result_column=-1):
    """Resolve a named range axis once and retain the exact source interval and cell."""
    candidates, labels = [], {}
    for table in fragments(number):
        target = table['columns'] - 1 if result_column == -1 else result_column
        for row in range(1, table['rows']):
            if conditions and not all(equal(table['grid'][row, col]['text'], label)
                                      for col, label in conditions.items()):
                continue
            cell = table['grid'][row, range_column]
            if range_matches(cell['text'], value, symbol):
                output = table['grid'][row, target]
                candidates.append((table, output))
                expression = compact(cell['text'])
                if expression[0] in '<>≤≥':
                    expression = symbol + expression
                interval = re.sub(r'([<>≤≥])', r' \1 ', expression)
                labels[output['id']] = interval
    selected, source = result(number, candidates)
    cell_id = candidates[0][1]['id']
    interval = labels[cell_id]
    return RangeSelection(selected, source, interval, symbol, cell_id,
                          f'GJB/Z 299D—2024 表 {number}')


def range_value(number, value, symbol, *, conditions=None, range_column=0, result_column=-1):
    selected = range_selection(number, value, symbol, conditions=conditions,
                               range_column=range_column, result_column=result_column)
    return selected.value, selected.source


@lru_cache(maxsize=None)
def stated_constant(section_number, symbol, *, qualifier=''):
    """Read a named scalar constant from the database standard_constant table."""
    row = _db().execute(
        'SELECT decimal_text, label FROM standard_constant WHERE section=? AND symbol=? AND qualifier=?',
        (section_number, symbol, qualifier),
    ).fetchone()
    if row is None:
        raise ValueError(f'未配置常数：{section_number} {qualifier}{symbol}')
    return float(row['decimal_text']), f'{row["label"]}（{section_number}）'


def stated_base_rate(section_number):
    symbol = 'λNb' if section_number.startswith('B.') else 'λb'
    return stated_constant(section_number, symbol)


@lru_cache(maxsize=1)
def parameter_tables():
    con = _db()
    tables = {}
    for definition in con.execute('SELECT * FROM parameter_definition ORDER BY id'):
        number = definition['table_number']
        if number not in tables:
            caption = con.execute('SELECT caption FROM table_fragment WHERE number=? '
                                  'ORDER BY seq LIMIT 1', (number,)).fetchone()[0]
            tables[number] = {'number': number, 'title': caption,
                              'axis': {'key': definition['axis_key'],
                                       'label': definition['axis_label'],
                                       'unit': definition['axis_unit']},
                              'outputs': [], 'unit': definition['unit'],
                              'rows': {}, 'source_cells': {}}
        table = tables[number]
        symbol = definition['symbol']
        table['outputs'].append(symbol)
        for point in con.execute('SELECT p.axis,p.cell_id,c.kind,c.decimal_text '
                                 'FROM parameter_point p JOIN cell c ON c.id=p.cell_id '
                                 'WHERE p.definition_id=? ORDER BY p.id', (definition['id'],)):
            axis = point['axis']
            table['rows'].setdefault(axis, {})[symbol] = (
                point['decimal_text'] if point['kind'] == 'number' else '—')
            table['source_cells'][axis + ':' + symbol] = point['cell_id']
    for table in tables.values():
        table['rows'] = [[axis] + [values[symbol] for symbol in table['outputs']]
                         for axis, values in table['rows'].items()]
    return list(tables.values())
