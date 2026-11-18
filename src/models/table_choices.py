"""Choice labels and horizontal-axis lookup derived from reference tables."""

from ..reference_data import compact, equal, fragments, result


def horizontal_value(number, label):
    """Read alternating header/value rows without matching numeric values as axes."""
    candidates = []
    for table in fragments(number):
        for cell in table['cells']:
            if not equal(cell['text'], label):
                continue
            if cell['kind'] == 'number' and cell['row_no'] % 2:
                continue
            value = table['grid'].get((cell['row_no'] + cell['row_span'], cell['column_no']))
            if value and value['kind'] == 'number':
                candidates.append((table, value))
    return result(number, candidates)


def horizontal_choices(number):
    labels = []
    for table in fragments(number):
        for cell in table['cells']:
            if cell['column_no'] == 0 or cell['column_span'] != 1:
                continue
            below = table['grid'].get((cell['row_no'] + cell['row_span'], cell['column_no']))
            if below and below['kind'] == 'number' and cell['kind'] != 'number':
                labels.append(compact(cell['text']))
    return tuple(dict.fromkeys(labels))


def vertical_choices(number, column=0):
    return tuple(dict.fromkeys(
        compact(table['grid'][row, column]['text'])
        for table in fragments(number) for row in range(1, table['rows'])
        if table['grid'][row, table['columns'] - 1]['kind'] == 'number'
    ))


def row_paths(number, label_columns, start_row=1):
    """Preserve full category paths, including labels inherited from row spans."""
    rows = {}
    for table in fragments(number):
        columns = label_columns(table) if callable(label_columns) else label_columns
        for row in range(start_row, table['rows']):
            labels = list(dict.fromkeys(compact(table['grid'][row, col]['text']) for col in range(columns)))
            path = ' / '.join(labels)
            if path in rows:
                raise ValueError(f'表 {number} 存在重复类型路径：{path}')
            rows[path] = (table, row, columns)
    return rows
