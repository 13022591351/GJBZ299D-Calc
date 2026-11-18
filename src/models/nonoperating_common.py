"""Read only the explicit points, intervals and source cells in Appendix B.2."""

import re
from decimal import Decimal, InvalidOperation
from functools import lru_cache

from ..model_spec import ModelSpec, choice
from ..reference_data import (
    below_value,
    compact,
    equal,
    fragments,
    resolve_temperature_tick,
    result,
    stated_constant,
)

METHOD = 'nonoperating_stress'
NOTE = '按附录 B.2 非工作详细预计模型计算。'
BLOCK_HEADERS = {'种类', '类别', '环境', '质量等级', 'T℃', 'NP'}
# Appendix B quality tables give coefficients; these chapter-5 tables supply
# the corresponding grade requirements only, never calculation coefficients.
QUALITY_DEFINITIONS = {
    7: ('3',), 14: ('35',), 17: ('44',), 21: ('46',), 26: ('46',),
    34: ('110',), 38: ('112',), 40: ('124',), 45: ('166',), 49: ('169',),
    53: ('209', '219', '224', '215', '228', '232', '237', '241', '246'),
    60: ('247',), 63: ('247',), 67: ('247',), 71: ('267',), 72: ('275',),
    75: ('284',), 78: ('291',), 81: ('297',), 82: ('300', '303'),
    84: ('306',), 94: ('319',), 96: ('321',), 98: ('324',), 101: ('327',),
    104: ('331',), 107: ('334',), 119: ('352',),
}


def number(table):
    return str(table) if str(table).startswith('B.') else f'B.{table}'


def path(table, row, columns):
    values = [table['grid'].get((row, col)) for col in columns]
    if not all(values):
        return ''
    return ' / '.join(dict.fromkeys(cell['text'] for cell in values))


@lru_cache(maxsize=None)
def row_options(table_number, columns=(0,), output=-1, *, expressions=False):
    labels = []
    for table in fragments(number(table_number)):
        column = table['columns'] - 1 if output == -1 else output
        for row in range(1, table['rows']):
            cell = table['grid'].get((row, column))
            label = path(table, row, columns)
            if cell and label and (cell['kind'] == 'number' or expressions and '×' in cell['text']):
                if label not in labels:
                    labels.append(label)
    return tuple(labels)


def row_lookup(table_number, label, output=-1, columns=(0,)):
    candidates = []
    for table in fragments(number(table_number)):
        column = table['columns'] - 1 if output == -1 else output
        for row in range(1, table['rows']):
            if equal(path(table, row, columns), label) and (row, column) in table['grid']:
                candidates.append((table, table['grid'][row, column]))
    return result(number(table_number), candidates)


@lru_cache(maxsize=None)
def header_options(table_number, header_rows=(0,), start=1):
    labels = []
    for table in fragments(number(table_number)):
        for col in range(start, table['columns']):
            cells = [table['grid'].get((row, col)) for row in header_rows]
            if all(cells):
                label = ' / '.join(dict.fromkeys(cell['text'] for cell in cells))
                if label not in labels:
                    labels.append(label)
    return tuple(labels)


def header_column(table, label, header_rows=(0,), start=1):
    columns = []
    for col in range(start, table['columns']):
        cells = [table['grid'].get((row, col)) for row in header_rows]
        if all(cells) and equal(' / '.join(dict.fromkeys(cell['text'] for cell in cells)), label):
            columns.append(col)
    return columns


def matrix(table_number, row_label, column_label, *, columns=(0,)):
    """Handle repeated header blocks and continued tables without crossing blocks."""
    candidates = []
    for table in fragments(number(table_number)):
        for header in table['cells']:
            if not equal(header['text'], column_label):
                continue
            start, col = header['row_no'] + header['row_span'], header['column_no']
            for row in range(start, table['rows']):
                first = table['grid'].get((row, 0))
                if first and compact(first['text']) in BLOCK_HEADERS:
                    break
                if equal(path(table, row, columns), row_label) and (row, col) in table['grid']:
                    candidates.append((table, table['grid'][row, col]))
    return result(number(table_number), candidates)


def scalar(table_number, label):
    return below_value(number(table_number), label)


@lru_cache(maxsize=None)
def quality_options(table_number, output=-1):
    table_number = number(table_number)
    tables = fragments(table_number)
    if len(tables) == 1 and tables[0]['rows'] == 2:
        return tuple(compact(label) for label in header_options(table_number))
    return tuple(compact(label) for label in row_options(table_number, (1,), output))


def quality_value(table_number, grade, output=-1):
    tables = fragments(number(table_number))
    if len(tables) == 1 and tables[0]['rows'] == 2:
        return scalar(table_number, grade)
    return row_lookup(table_number, grade, output, (1,))


def quality_field(table_number, output=-1, **kwargs):
    return choice('quality', '质量等级', quality_options(table_number, output), '质量与工艺', **kwargs)


def _parse_choice_token(text):
    value = compact(text)
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return value
    return int(parsed) if parsed == parsed.to_integral_value() else float(parsed)


@lru_cache(maxsize=None)
def cycle_options(table_number):
    return tuple(_parse_choice_token(option) for option in _axis_options(table_number))


def cycle_field(table_number, **kwargs):
    kwargs.setdefault('positive', False)
    return choice('cycle_rate', '设备电源通-断循环率 Nc [次/10³ h]', cycle_options(table_number), '非工作条件', **kwargs)


def interval_matches(label, value, symbol=''):
    """Source points, closed a～b intervals, or explicit chained inequalities."""
    expression = compact(label).replace('＜', '<').replace('＞', '>')
    expression = expression.replace(symbol, '') if symbol else expression
    if isinstance(value, str):
        candidate = compact(value).replace('＜', '<').replace('＞', '>')
        candidate = candidate.replace(symbol, '') if symbol else candidate
        if expression == candidate:
            return True
        try:
            x = Decimal(candidate)
        except InvalidOperation:
            return False
    else:
        x = Decimal(str(value))
    try:
        return x == Decimal(expression)
    except InvalidOperation:
        pass
    match = re.fullmatch(r'([\d.]+)[～~]([\d.]+)', expression)
    if match:
        return Decimal(match[1]) <= x <= Decimal(match[2])
    match = re.fullmatch(r'([\d.]+)?(≤|≥|<|>)([\d.]+)?(?:(≤|≥|<|>)([\d.]+))?', expression)
    if not match:
        return False
    left, first, right, second, end = match.groups()

    def compare(a, op, b):
        return {'≤': a <= b, '≥': a >= b, '<': a < b, '>': a > b}[op]

    if left is None:
        return compare(x, first, Decimal(right)) if right is not None and second is None else False
    if right is None and second is None:
        return compare(Decimal(left), first, x)
    if right is None and second is not None:
        return compare(Decimal(left), first, x) and compare(x, second, Decimal(end))
    return False


def _axis_options(table_number, *, axis_column=0, paired=False):
    seen = []
    for table in fragments(number(table_number)):
        axes = range(0, table['columns'], 2) if paired else (axis_column,)
        for row in range(1, table['rows']):
            for col in axes:
                cell = table['grid'].get((row, col))
                if cell and cell['text'].strip():
                    text = compact(cell['text'])
                    if text not in seen:
                        seen.append(text)
    return seen


def axis_value(table_number, value, *, axis_column=0, output=-1, symbol='', paired=False):
    candidates = []
    for table in fragments(number(table_number)):
        axes = range(0, table['columns'], 2) if paired else (axis_column,)
        for row in range(1, table['rows']):
            for col in axes:
                axis = table['grid'].get((row, col))
                target = col + 1 if paired else table['columns'] - 1 if output == -1 else output
                if axis and interval_matches(axis['text'], value, symbol) and (row, target) in table['grid']:
                    candidates.append((table, table['grid'][row, target]))
    if not candidates:
        options = _axis_options(table_number, axis_column=axis_column, paired=paired)
        hint = f'（可选：{"、".join(options)}）' if options else ''
        raise ValueError(f'表 {number(table_number)} 无匹配取值{hint}')
    return result(number(table_number), candidates)


def temp_axis_value(table_number, temp_C, *, axis_column=0, output=-1, paired=False):
    raw_labels = _axis_options(table_number, axis_column=axis_column, paired=paired)
    tick, note = resolve_temperature_tick(number(table_number), temp_C, raw_labels)
    value, source = axis_value(table_number, tick, axis_column=axis_column, output=output, paired=paired)
    return value, source + note


def horizontal_axis(table_number, value, symbol):
    candidates, options = [], []
    for table in fragments(number(table_number)):
        for cell in table['cells']:
            if cell['row_no'] != 0 or cell['column_no'] == 0:
                continue
            text = compact(cell['text'])
            if text and text not in options:
                options.append(text)
            if interval_matches(cell['text'], value, symbol):
                candidates.append((table, table['grid'][1, cell['column_no']]))
    if not candidates:
        hint = f'（可选：{"、".join(options)}）' if options else ''
        raise ValueError(f'表 {number(table_number)} 无匹配取值{hint}')
    return result(number(table_number), candidates)


def cycle_value(table_number, value, output=-1):
    return axis_value(table_number, value, output=output)


def constant(section, symbol, _pattern=None):
    return stated_constant(section, symbol)


def cell_source(table, cell):
    return f'表 {table["number"]}；{cell["id"]}'


def spec(fields, formula, section, tables, calculator, example, *, category='OTHER',
         note='', qualities=(), limitation=''):
    references = '、'.join('表 ' + number(table) for table in tables)
    quality_tables = tuple(dict.fromkeys(
        [definition for table in qualities for definition in QUALITY_DEFINITIONS.get(table, ())]
        + [number(table) for table in qualities]))
    summaries = (tuple((grade, '表 B.7 未给出质量要求说明') for grade in ('C1', 'C2'))
                 if 7 in qualities else ())
    if 96 in qualities:
        summaries += (('A1', '表 B.96 未细分 A1S/A1B 质量等级'),
                      ('B', '表 B.96 未细分 B1/B2 质量等级'))
    return ModelSpec(tuple(fields), formula, f'GJB/Z 299D—2024 第 {section} 节；{references}',
                     NOTE + note, category, section, calculator,
                     quality_tables, example, quality_summaries=summaries,
                     limitation=limitation, method=METHOD)
