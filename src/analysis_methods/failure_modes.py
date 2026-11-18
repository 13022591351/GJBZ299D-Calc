"""Read Appendix F's original percentages without silently normalizing them."""

import copy
import re
from collections import OrderedDict
from decimal import Decimal
from functools import lru_cache

from ..reference_data import fragments
from .common import RATE_UNIT, AnalysisResult, decimal, finite, shown

PERCENT = re.compile(r'^(\d+(?:\.\d+)?)\s*[%％]$')


@lru_cache(maxsize=1)
def _catalog():
    catalog = OrderedDict()
    for number in range(1, 21):
        table = f'F.{number}'
        categories = OrderedDict()
        for fragment in fragments(table):
            if fragment['columns'] != 3:
                raise ValueError(f'表 {table} 的失效模式列结构未建立')
            for row in range(fragment['rows']):
                cells = [fragment['grid'][row, col] for col in range(3)]
                match = PERCENT.fullmatch(cells[2]['text'].strip())
                if match is None:
                    continue  # Titles and merged section headings are not modes.
                category, mode = (cell['text'].strip() for cell in cells[:2])
                percent = Decimal(match[1])
                if not 0 <= percent <= 100:
                    raise ValueError(f'表 {table} 的原文比例超出 0～100%')
                entries = categories.setdefault(category, [])
                if any(entry['mode'] == mode for entry in entries):
                    raise ValueError(f'表 {table} 的 {category} 模式重复，不能自动合并')
                entries.append({'mode': mode, 'percent': str(percent),
                                'reference': f'表 {table}'})
        if not categories:
            raise ValueError(f'表 {table} 没有可读取的原文比例')
        catalog[table] = categories
    return catalog


def failure_mode_catalog():
    return copy.deepcopy(_catalog())


def failure_modes(table, category):
    try:
        return tuple(dict(row) for row in _catalog()[table][category])
    except (KeyError, TypeError):
        raise ValueError('请选择附录 F 已列明的表格及元器件类别') from None


def mode_contributions(rate, table, category):
    total = decimal(rate, '元器件失效率')
    modes = failure_modes(table, category)
    details = tuple({**row, 'percent': float(row['percent']),
                     'rate': finite(total * Decimal(row['percent']) / 100, '模式失效率贡献')}
                    for row in modes)
    percent_sum = sum(Decimal(row['percent']) for row in modes)
    contribution = total * percent_sum / 100
    notes = ['依据表列频数比计算 λ模式 = λ元器件 × 比例。']
    if percent_sum != 100:
        notes.append(f'原文比例合计 {shown(percent_sum)}%。')
    steps = tuple(f'{row["mode"]}：{shown(total)} × {row["percent"]}% = {shown(row["rate"])}（{RATE_UNIT}）'
                  for row in details)
    return AnalysisResult('failure_modes', '失效模式及频数比应用',
                          {'rate': finite(total, '元器件失效率'), 'table': table, 'category': category},
                          steps, {'original_rate': finite(total, '元器件失效率'),
                                  'percent_sum': finite(percent_sum, '比例合计'),
                                  'contribution_sum': finite(contribution, '模式贡献合计')},
                          {'original_rate': RATE_UNIT, 'percent_sum': '%', 'contribution_sum': RATE_UNIT},
                          f'附录 F.1、F.2，表 {table}', tuple(notes), details)
