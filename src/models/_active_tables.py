"""Small source-backed selectors for tables in sections 5.1–5.5."""

import re

from ..reference_data import (
    below_value,
    compact,
    equal,
    fragments,
    resolve_temperature_tick,
    result,
    row_value,
)


def rows(number, *, columns=(0,), numeric_column=-1):
    """Unique labels whose corresponding coefficient is a printed number."""
    values = []
    for table in fragments(str(number)):
        column = table['columns'] - 1 if numeric_column == -1 else numeric_column
        for row in range(1, table['rows']):
            cell = table['grid'].get((row, column))
            labels = [table['grid'].get((row, col)) for col in columns]
            if cell and cell['kind'] == 'number' and all(labels):
                label = ' / '.join(dict.fromkeys(item['text'] for item in labels))
                if label not in values:
                    values.append(label)
    return tuple(values)


def headers(number, row=0, start=1):
    values = []
    for table in fragments(str(number)):
        for col in range(start, table['columns']):
            cell = table['grid'].get((row, col))
            target = table['grid'].get((row + 1, col))
            if cell and target and target['kind'] == 'number':
                if cell['text'] not in values:
                    values.append(cell['text'])
    return tuple(values)


def grouped(number, label, *, columns=(0, 1), numeric_column=-1):
    candidates = []
    for table in fragments(str(number)):
        col = table['columns'] - 1 if numeric_column == -1 else numeric_column
        for row in range(1, table['rows']):
            labels = [table['grid'].get((row, c)) for c in columns]
            if all(labels):
                key = ' / '.join(dict.fromkeys(item['text'] for item in labels))
                if equal(key, label) and (row, col) in table['grid']:
                    candidates.append((table, table['grid'][row, col]))
    return result(str(number), candidates)


def bands(number, value, symbol):
    """Horizontal physical-parameter bands; no interpolation/extrapolation."""
    candidates = []
    for table in fragments(str(number)):
        for cell in table['cells']:
            if cell['kind'] == 'number' or cell['column_no'] == 0:
                continue
            label = compact(cell['text']).replace(symbol, '')
            label = label.replace('W', '').replace('A', '')
            tests = re.fullmatch(r'(?:(\d+(?:\.\d+)?)(≤|<))?(≤|<|≥|>)?(\d+(?:\.\d+)?)?', label)
            if not tests:
                continue
            low, lop, hop, high = tests.groups()
            match = True
            if low is not None:
                match &= value >= float(low) if lop == '≤' else value > float(low)
            if high is not None:
                if hop == '≤':
                    match &= value <= float(high)
                elif hop == '<':
                    match &= value < float(high)
                elif hop == '≥':
                    match &= value >= float(high)
                elif hop == '>':
                    match &= value > float(high)
                else:
                    match &= value == float(high)
            if match:
                target = table['grid'].get((cell['row_no'] + cell['row_span'], cell['column_no']))
                if target:
                    candidates.append((table, target))
    return result(str(number), candidates)


def triplet(number, axis, offset):
    """Repeated [axis, coefficient1, coefficient2] blocks (tables 30, 36)."""
    candidates = []
    for table in fragments(str(number)):
        for row in range(1, table['rows']):
            for col in range(0, table['columns'], 3):
                cell = table['grid'].get((row, col))
                if cell and equal(cell['text'], axis):
                    candidates.append((table, table['grid'][row, col + offset]))
    return result(str(number), candidates)


def temp_triplet(number, temp_C, offset):
    number_str = str(number)
    raw_labels = [
        table['grid'][row, col]['text']
        for table in fragments(number_str)
        for row in range(1, table['rows'])
        for col in range(0, table['columns'], 3)
        if (row, col) in table['grid']
    ]
    tick, note = resolve_temperature_tick(number_str, temp_C, raw_labels)
    value, source = triplet(number_str, tick, offset)
    return value, source + note


def quality_value(number, grade):
    if fragments(str(number))[0]['rows'] == 2:
        return below_value(str(number), grade)
    return row_value(str(number), grade)


def horizontal(number, axis, header_rows=(0,)):
    candidates = []
    for table in fragments(str(number)):
        for row in header_rows:
            for col in range(1, table['columns']):
                cell = table['grid'].get((row, col))
                target = table['grid'].get((row + 1, col))
                if cell and target and equal(cell['text'], axis):
                    candidates.append((table, target))
    return result(str(number), candidates)


def quality_choices(number):
    if fragments(str(number))[0]['rows'] == 2:
        return tuple(compact(value) for value in headers(number))
    return tuple(compact(value) for value in rows(number, columns=(1,)))


def numeric_axes(number, column=0, first_row=1):
    values = set()
    for table in fragments(str(number)):
        for row in range(first_row, table['rows']):
            cell = table['grid'].get((row, column))
            if cell and cell['kind'] == 'number':
                values.add(float(cell['decimal_text']))
    return tuple(sorted(values))


def paired_axes(number):
    values = set()
    for table in fragments(str(number)):
        for cell in table['cells']:
            if cell['row_no'] > 0 and cell['column_no'] % 2 == 0 and cell['kind'] == 'number':
                values.add(float(cell['decimal_text']))
    return tuple(sorted(values))


def int_or_float_options(values):
    """Convert integer-valued floats to int for clean dropdown display."""
    return tuple(int(value) if float(value).is_integer() else value for value in values)
