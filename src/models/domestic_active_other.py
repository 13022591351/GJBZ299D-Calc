"""Domestic optoelectronics and vacuum electronics, clauses 5.4 and 5.5."""

from functools import partial

from ..model_spec import Input, ModelSpec, choice, quality, temperature
from ..reference_data import (
    below_value,
    equal,
    fragments,
    matrix_value,
    resolve_temperature_tick,
    result,
)
from ._active_tables import (
    grouped,
    headers,
    horizontal,
    numeric_axes,
    quality_choices,
    quality_value,
    rows,
)
from .common import Factors

MODELS = {}
OPTO_KINDS = (*rows('108', columns=(0, 1)), '多位数码管')


def _opto(p, environment, ambient):
    f = Factors()
    kind = p['kind']
    if kind == '多位数码管':
        base = .0048 + .0021 * p['digit_count']
        f.add('λb', base, '表 108：多位数码管 λb = 0.0048 + 0.0021 × 位数')
    else:
        base = f.add('λb', *grouped('108', kind))
    if '数码管' in kind:
        base *= p['chip_count']
        f.add('芯片数', p['chip_count'], '表 108 注：数码管按内含芯片数计算')
    e = f.env('109', environment)
    q = f.add('πQ', *quality_value('110', p['quality']))
    offset = 3 if kind == '激光二极管' else 2 if ('耦合器' in kind or '数码管' in kind) else 1
    raw_labels = [
        table['grid'][row, col]['text']
        for table in fragments('111')
        for row in range(2, table['rows'])
        for col in (0, 4)
        if table['grid'].get((row, col)) and table['grid'][(row, col)]['kind'] == 'number'
    ]
    tick, note = resolve_temperature_tick('111', ambient, raw_labels)
    candidates = []
    for table in fragments('111'):
        for row in range(2, table['rows']):
            for col in (0, 4):
                cell = table['grid'].get((row, col))
                if cell and cell['kind'] == 'number' and equal(cell['text'], tick):
                    candidates.append((table, table['grid'][row, col + offset]))
    value, source = result('111', candidates)
    t = f.add('πT', value, source + note)
    return f.result(base * e * q * t)


MODELS['国产光电子器件'] = ModelSpec(
    (choice('kind', '器件种类', OPTO_KINDS), quality(quality_choices('110')), temperature(),
     Input('digit_count', '显示位数', integer=True, min_value=1, presets=(2, 4, 6, 8),
           when=('kind', ('多位数码管',)),
           constraint_hint='表 108 多位数码管显示位数须为正整数（≥ 1）'),
     Input('chip_count', '内含芯片数', integer=True, min_value=1, presets=(1, 2, 4, 8),
           when=('kind', ('单位数码管', '多位数码管')),
           constraint_hint='表 108 注：数码管按内含芯片数（≥ 1）累计')),
    'λp = λbπEπQπT；数码管按内含芯片数累计', '5.4，式（30），表 108～111',
    category='LED', section='5.4', calculator=_opto, quality_tables=('110',),
    example={'kind': '发光二极管', 'quality': 'B2'},
)


def _tube(p, environment, ambient):
    f = Factors()
    f.add('λb', *grouped('113', p['kind']))
    f.env('116', environment)
    f.add('πQ', *quality_value('112', p['quality']))
    f.add('πL', *below_value('117', p['maturity']))
    return f.result()


MODELS['国产普通电子管'] = ModelSpec(
    (choice('kind', '电子管类型', rows('113', columns=(0, 1))), quality(quality_choices('112')),
     choice('maturity', '成熟程度', headers('117'), '质量与工艺')),
    'λp = λbπEπQπL', '5.5.2，式（31），表 112、113、116、117',
    category='V', section='5.5.2', calculator=_tube, quality_tables=('112',),
    example={'kind': '整流管', 'quality': 'B', 'maturity': headers('117')[0]},
)


def _power_tube(table, etable, p, environment, ambient):
    power, frequency = float(p['power']), float(p['frequency_GHz'])
    if table == '114':
        if not (.1 <= power <= 200 and .3 <= frequency <= 20
                and power <= 8e6 * (frequency * 1000) ** -1.4):
            raise ValueError('连续波速调管须满足表 113 的功率、频率及 P ≤ 8×10⁶f⁻¹·⁴ 条件（f 单位 MHz）')
    if table == '115':
        if not (.01 <= power <= 100 and .2 <= frequency <= 20 and power <= 490 * frequency ** -1.95):
            raise ValueError('脉冲速调管须满足表 113 的功率、频率及 P ≤ 490f⁻¹·⁹⁵ 条件（f 单位 GHz）')
    f = Factors()
    f.add('λb', *matrix_value(table, power, frequency, label_column=0))
    f.env(etable, environment)
    f.add('πQ', *quality_value('112', p['quality']))
    if table in ('114', '115'):
        f.add('πL', *below_value('117', p['maturity']))
    return f.result()


for name, table, etable, unit, description in (
    ('国产连续波速调管', '114', '116', 'kW', '平均输出功率'),
    ('国产脉冲速调管', '115', '116', 'MW', '峰值输出功率'),
    ('国产行波管', '118', '119', 'W', '额定功率[脉冲式取峰值功率]'),
):
    fields = (quality(quality_choices('112')), choice('power', f'{description} P（{unit}）', numeric_axes(table, first_row=2)),
              choice('frequency_GHz', '工作频率 f（GHz）', headers(table, row=1)))
    if table != '118':
        fields += (choice('maturity', '成熟程度', headers('117'), '质量与工艺'),)
    MODELS[name] = ModelSpec(
        fields, 'λp = λbπEπQ' + ('πL' if table != '118' else ''),
        f'5.5.{3 if table == "118" else 2}，表 112～119',
        note=('行波管工作频率为频段时填两端算术平均值，为两个不同频率时填几何平均值；按表列坐标计算。'
              if table == '118' else '除表列功率频率外，同时检查表 113 的适用条件。'),
        category='V', section='5.5.3' if table == '118' else '5.5.2',
        calculator=partial(_power_tube, table, etable), quality_tables=('112',),
        example={field.key: field.choices[0] for field in fields},
    )


def _magnetron(p, environment, ambient):
    f = Factors()
    if p['kind'].startswith('连续波'):
        if not 0 < p['rated_power_kW'] < 5:
            raise ValueError('连续波磁控管模型限额定功率小于 5 kW')
        f.add('λb', 26.2, '5.5.4：额定功率小于 5 kW 连续波磁控管 λb = 26.2')
    else:
        f.add('λb', *matrix_value('120', p['peak_power_MW'], p['frequency_GHz'], label_column=0))
    f.env('121', environment)
    f.add('πQ', *quality_value('112', p['quality']))
    f.add('πU', *horizontal('122', p['utilization'], (0, 2)))
    f.add('πC', *below_value('123', p['kind']))
    return f.result()


_MAGNETRON_KINDS = headers('123')
MODELS['国产磁控管'] = ModelSpec(
    (quality(quality_choices('112')), choice('kind', '磁控管类型', _MAGNETRON_KINDS),
     Input('rated_power_kW', '额定功率（kW）', min_value=0, min_inclusive=False,
           max_value=5, max_inclusive=False, presets=(0.5, 1, 2, 4),
           when=('kind', (_MAGNETRON_KINDS[0],)),
           constraint_hint='5.5.4 限定连续波磁控管额定功率 0 < P < 5 kW'),
     choice('peak_power_MW', '额定峰值功率 P（MW）', numeric_axes('120', first_row=2), when=('kind', _MAGNETRON_KINDS[1:])),
     choice('frequency_GHz', '工作频率 f（GHz）', headers('120', row=1), when=('kind', _MAGNETRON_KINDS[1:])),
     choice('utilization', '利用率 U[发射时间/灯丝工作时间]', tuple(n / 10 for n in range(11)), '工作应力', positive=False)),
    'λp = λbπEπQπUπC', '5.5.4，式（33），表 112、120～123',
    category='V', section='5.5.4', calculator=_magnetron, quality_tables=('112',),
    example={'quality': 'B', 'kind': _MAGNETRON_KINDS[0], 'rated_power_kW': 1, 'utilization': 1},
)
