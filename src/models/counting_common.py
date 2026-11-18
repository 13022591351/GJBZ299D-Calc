"""Source-bound parts-count rows and quality classifications.

Count tables contain 19 environment columns. Their left-hand columns form a
classification path, including values inherited from merged cells and continuations.
No operating-temperature or electrical-stress correction is applied to generic rates.
"""

import math
from dataclasses import dataclass
from functools import lru_cache

from ..model_spec import Evaluation, ModelSpec, choice
from ..reference_data import compact, fragments, result
from .common import Factors
from .table_choices import horizontal_choices, horizontal_value

TEMPERATURE_NOTE = '计数法采用标准通用条件；工程环境温度仅用于详细模型，不调整计数结果。'


@lru_cache(maxsize=None)
def count_rows(number):
    rows = {}
    for table in fragments(number):
        first = table['columns'] - 19
        for row in range(2, table['rows']):
            path = ' / '.join(dict.fromkeys(
                compact(table['grid'][row, col]['text']) for col in range(first)))
            rows.setdefault(path, []).append((table, row, first))
    if not rows:
        raise ValueError(f'表 {number} 缺少计数分类')
    for table, _row, first in (entry for entries in rows.values() for entry in entries):
        axes = tuple(compact(table['grid'][1, col]['text']) for col in range(first, table['columns']))
        if len(set(axes)) != 19 or axes[0] != 'GB' or axes[-1] != 'MF':
            raise ValueError(f'表 {number} 环境表头不完整')
    return rows


def generic_rate(number, kind, environment):
    try:
        rows = count_rows(number)[kind]
    except KeyError as exc:
        raise ValueError(f'表 {number} 未列出种类：{kind}') from exc
    if len(rows) != 1:
        raise ValueError(f'表 {number} 同一种类存在多行：{kind}；请使用详细模型')
    table, row, first = rows[0]
    cells = [(table, table['grid'][row, col]) for col in range(first, table['columns'])
             if compact(table['grid'][1, col]['text']) == compact(environment)]
    return result(number, cells)


@dataclass(frozen=True)
class QualityRule:
    table: str
    label_column: int = 1
    value_column: int = -1
    horizontal: bool = False
    row_label: str = ''
    header_row: int = 1
    row_label_column: int = 0

    @property
    def choices(self):
        return tuple(quality_values(self))

    def value(self, grade):
        try:
            return quality_values(self)[grade]
        except KeyError as exc:
            raise ValueError(f'表 {self.table} 未给出当前种类的质量等级 {grade}') from exc


@lru_cache(maxsize=None)
def quality_values(rule):
    """Return only grades whose selected source coefficient is numeric.

    For B.53, a merged coefficient across A1P/A1M or C1/C2 means the
    parent grade A1 or C. Do not invent duplicate grade subdivisions.
    """
    values = {}

    def add(grade, table, cell):
        if cell['kind'] != 'number':
            return
        grade = compact(grade)
        if grade in values:
            raise ValueError(f'表 {rule.table} 质量等级重复：{grade}')
        values[grade] = result(rule.table, [(table, cell)])

    if rule.horizontal:
        return {grade: horizontal_value(rule.table, grade) for grade in horizontal_choices(rule.table)}
    for table in fragments(rule.table):
        grid = table['grid']
        if rule.row_label:
            for row in range(rule.header_row + 1, table['rows']):
                if compact(grid[row, rule.row_label_column]['text']) != compact(rule.row_label):
                    continue
                for cell in table['cells']:
                    if cell['row_no'] != row or cell['column_no'] <= rule.row_label_column:
                        continue
                    header = rule.header_row
                    if rule.table == 'B.53' and cell['column_span'] > 1:
                        header = 1
                    add(grid[header, cell['column_no']]['text'], table, cell)
        else:
            column = table['columns'] - 1 if rule.value_column == -1 else rule.value_column
            for row in range(1, table['rows']):
                cell = grid[row, column]
                if cell['row_no'] != row:
                    continue
                add(grid[row, rule.label_column]['text'], table, cell)
    if not values:
        raise ValueError(f'表 {rule.table} 缺少可计算的质量等级')
    return values


def counting_model(number, rules, *, category, section, method, maturity=False,
                   definitions=(), limitation='', note='', restricted_kinds=(), quality_summaries=()):
    """Make one table-backed entry; quality fields depend on the selected kind."""
    kinds = tuple(count_rows(number))
    restrictions = dict(restricted_kinds)
    if set(restrictions) - set(kinds):
        raise ValueError(f'表 {number} 限制类别未见于源表')
    resolved = {kind: rules(kind) if callable(rules) else rules for kind in kinds}
    groups = {}
    for kind, rule in resolved.items():
        if rule is not None and kind not in restrictions:
            groups.setdefault(rule, []).append(kind)
    first_table, _row, first_column = next(iter(count_rows(number).values()))[0]
    axes_label = ' / '.join(dict.fromkeys(
        compact(first_table['grid'][0, col]['text']) for col in range(first_column)))
    fields = [choice('kind', axes_label + ' [标准分类]', kinds)]
    quality_keys = {}
    for index, (rule, selected) in enumerate(groups.items(), 1):
        key = 'quality' if len(groups) == 1 else f'group{index}_quality'
        quality_keys[rule] = key
        fields.append(choice(key, '质量等级', rule.choices, '质量与工艺',
                             when=None if len(selected) == len(kinds) else ('kind', tuple(selected))))
    if maturity:
        fields.append(choice('maturity', '成熟程度', horizontal_choices('29'), '质量与工艺'))
    nonoperating = method == 'nonoperating_count'
    domestic_work = section == '6'
    base_symbol, quality_symbol = ('λNG', 'πNQ') if nonoperating else ('λG', 'πQ')

    def calculate(p, environment, _ambient):
        f = Factors()
        kind = p['kind']
        base_value, base_source = generic_rate(number, kind, environment)
        f.add(base_symbol, base_value, f'{base_source}；分类：{kind}；环境：{environment}；{TEMPERATURE_NOTE}')
        f.ranges.append(('kind', kind))
        if kind in restrictions:
            return Evaluation(None, tuple(f.values), error=restrictions[kind])
        rule = resolved[kind]
        if rule is None:
            return Evaluation(None, tuple(f.values), error=limitation)
        f.add(quality_symbol, *rule.value(p[quality_keys[rule]]))
        if maturity:
            f.add('πL', *horizontal_value('29', p['maturity']))
        rate = math.prod(value for _name, value, _source in f.values)
        if domestic_work:
            f.add('通用工作环境温度 Ta（℃）', *horizontal_value('355', environment))
        return f.result(rate)

    example = {'kind': kinds[0]}
    first_rule = resolved[kinds[0]]
    if first_rule is not None:
        grades = first_rule.choices
        example[quality_keys[first_rule]] = 'B2' if 'B2' in grades else grades[0]
    if maturity:
        example['maturity'] = fields[-1].choices[0]
    formula = f'λ = {base_symbol}{quality_symbol}' + ('πL' if maturity else '')
    quality_tables = tuple(dict.fromkeys(list(definitions) + [rule.table for rule in groups]))
    reference = f'{section}，表 {number}'
    if quality_tables:
        reference += '；质量系数表 ' + '、'.join(quality_tables)
    if maturity:
        reference += '；成熟系数表 29'
    if domestic_work:
        reference += '；通用工作环境温度表 355'
    return ModelSpec(
        fields, formula, reference, note=TEMPERATURE_NOTE + note,
        category=category, section=section, calculator=calculate,
        quality_tables=quality_tables, example=example,
        limitation=limitation or '；'.join(restrictions.values()), method=method,
        quality_summaries=quality_summaries,
    )
