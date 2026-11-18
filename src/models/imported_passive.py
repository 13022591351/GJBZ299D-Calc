"""Remaining imported passive models from Appendix C."""

import re
from functools import partial

from ..model_spec import Evaluation, Input, ModelSpec, choice, quality, stress, temperature
from ..reference_data import (
    compact,
    conditions_value,
    equal,
    fragments,
    resolve_temperature_tick,
    result,
    row_value,
    temp_below_value,
    temp_matrix_value,
    temp_paired_value,
)
from .common import Factors


def _labels(table, column=0):
    return tuple(dict.fromkeys(
        f['grid'][row, column]['text'] for f in fragments(table) for row in range(1, f['rows'])
        if f['grid'].get((row, f['columns'] - 1), {}).get('kind') == 'number'))


def _quality(table):
    return quality(_labels(table, 1))


def _group_matrix(table, group, kind, axis, *, temp_col=False):
    """The first two columns form a composite row axis (e.g. wirewound/精密)."""
    note = ''
    if temp_col:
        raw_labels = [
            c['text']
            for fragment in fragments(table)
            for c in fragment['cells']
            if c['row_no'] == 1 and c['column_no'] > 0
        ]
        axis, note = resolve_temperature_tick(table, axis, raw_labels)
    candidates = []
    for fragment in fragments(table):
        columns = [c['column_no'] for c in fragment['cells'] if c['row_no'] == 1 and equal(c['text'], axis)]
        for row in range(2, fragment['rows']):
            if equal(fragment['grid'][row, 0]['text'], group) and equal(fragment['grid'][row, 1]['text'], kind):
                candidates.extend((fragment, fragment['grid'][row, col]) for col in columns)
    value, source = result(table, candidates)
    return value, source + note


def _network_resistor_stress():
    """Read the explicit πS=1 constant from C.48."""
    matches = []
    for fragment in fragments('C.48'):
        for cell in fragment['cells']:
            match = re.search(r'电阻网络πS=(\d+(?:\.\d+)?)', compact(cell['text']))
            if match:
                matches.append((float(match[1]), f'表 C.48 注；{cell["id"]}'))
    if len(matches) != 1:
        raise ValueError('表 C.48 电阻网络电应力系数常数不唯一或缺失')
    return matches[0]


def _resistor_network(p, environment, _ambient):
    f = Factors()
    f.add('λb', *conditions_value('C.44', {0: '电阻网络', 1: p['kind']}))
    f.add('N', p['resistor_count'], 'C.2.7 式（C.10）：网络电阻数')
    f.env('C.45', environment)
    f.quality('C.46', p['quality'])
    f.add('πT', *temp_matrix_value('C.47', '电阻网络', p['temperature_C'], label_column=0, temp_axis='col'))
    f.add('πS', *_network_resistor_stress())
    return f.result()


MODELS = {
    '进口电阻网络': ModelSpec(
        [choice('kind', '类型', ['每个分立元件', '厚薄膜']), Input('resistor_count', '网络电阻数 N', integer=True),
         _quality('C.46'), temperature()], 'λp = N × λb × πE × πQ × πT × πS', 'C.2.7，式（C.10）',
        category='R', section='C.2.7', calculator=_resistor_network, quality_tables=('C.46',),
        note='按表 C.48 注，电阻网络 πS=1，无需填写单体功率应力比。',
        example=dict(kind='每个分立元件', resistor_count=4, quality='M'),
    )
}


def _sensitive(p, environment, _ambient, *, kind):
    f = Factors()
    f.add('λb', *row_value('C.49', kind, label_column=0))
    f.env('C.50', environment)
    f.quality('C.51', p['quality'])
    f.add('πT', *temp_paired_value('C.52', p['temperature_C']))
    return f.result()


for _kind in _labels('C.49'):
    MODELS['进口' + _kind] = ModelSpec(
        [_quality('C.51'), temperature()], 'λp = λb × πE × πQ × πT', 'C.2.8，式（C.11）',
        category='RT', section='C.2.8', calculator=partial(_sensitive, kind=_kind),
        quality_tables=('C.51',), example=dict(quality='M'),
    )


POTENTIOMETERS = {
    '普通线绕': ('线绕电位器', '普通'),
    '精密线绕': ('线绕电位器', '精密'),
    '微调线绕': ('线绕电位器', '微调'),
    '有机实芯': ('非线绕电位器', '有机实芯'),
    '低精密碳膜': ('非线绕电位器', '低精密碳膜'),
    '玻璃釉': ('非线绕电位器', '玻璃釉'),
    '精密': ('非线绕电位器', '精密'),
    '微调': ('非线绕电位器', '微调'),
}


def _potentiometer(p, environment, _ambient):
    group, kind = POTENTIOMETERS[p['kind']]
    f = Factors()
    f.add('λb', *row_value('C.53', p['kind'], label_column=1))
    f.env('C.54', environment)
    f.quality('C.55', p['quality'])
    f.add('πT', *_group_matrix('C.56', group, kind, p['temperature_C'], temp_col=True))
    f.add('πS', *_group_matrix('C.57', group, kind, p['stress_ratio']))
    return f.result()


MODELS['进口电位器'] = ModelSpec(
    [choice('kind', '类别', POTENTIOMETERS), _quality('C.55'), temperature(),
     stress(label='功率应力比 S', values=tuple(n / 10 for n in range(1, 10)), source='表 C.57')],
    'λp = λb × πE × πQ × πT × πS', 'C.2.9，式（C.12）', category='RP', section='C.2.9',
    calculator=_potentiometer, quality_tables=('C.55',), example=dict(kind='普通线绕', quality='M', stress_ratio=0.5),
)


VARIABLE_CAPACITORS = ('瓷介微调', '玻璃介质', '空气介质', '真空')
VARIABLE_LIMITATION = '表 C.63 未给出可变电容器的表面贴装系数 πch；式（C.13）不能完整计算，不将缺项假定为 1。'


def _variable_capacitor(p, environment, _ambient):
    f = Factors()
    f.add('λb', *conditions_value('C.58', {0: '可变电容器', 1: p['kind']}))
    f.env('C.59', environment)
    f.quality('C.60', p['quality'])
    kind = {'瓷介微调': '瓷介', '玻璃介质': '玻璃介质、空气介质',
            '空气介质': '玻璃介质、空气介质', '真空': '真空'}[p['kind']]
    f.add('πT', *_group_matrix('C.61', '可变电容器', kind, p['temperature_C'], temp_col=True))
    f.add('πS', *_group_matrix('C.62', '可变电容器', p['kind'], p['stress_ratio']))
    return Evaluation(None, tuple(f.values), error=VARIABLE_LIMITATION)


MODELS['进口可变电容器'] = ModelSpec(
    [choice('kind', '类型', VARIABLE_CAPACITORS), _quality('C.60'), temperature(),
     stress(label='电压应力比 S', values=tuple(n / 10 for n in range(1, 10)), source='表 C.62')],
    'λp = λb × πE × πQ × πT × πS × πch', 'C.2.10，式（C.13）', category='C', section='C.2.10',
    note=VARIABLE_LIMITATION, limitation=VARIABLE_LIMITATION, calculator=_variable_capacitor,
    quality_tables=('C.60',), example=dict(kind='瓷介微调', quality='M', stress_ratio=0.5),
)


# These public labels are the fixed-capacitor model's physical type vocabulary.
_FIXED_CAPACITORS = ('纸介', '纸膜复合', '薄膜', '云母', '瓷介', '铝电解',
                     '固体钽（密封）', '固体钽（非密封）', '非固体钽', '氧化铌')


def _capacitor_network(p, environment, ambient):
    from ..standards import MODELS as registry
    from ..standards import evaluate

    variable = p['kind'] in VARIABLE_CAPACITORS
    parameters = dict(p)
    if not variable:
        parameters['quality'] = {
            '执行军用规范但无可靠性指标的产品': '军规无可靠性指标',
            '其他民用或未知筛选等级的产品': '民用或未知筛选',
        }.get(p['quality'], p['quality'])
    model_name = '进口可变电容器' if variable else '进口电容器'
    parameters = {field.key: parameters[field.key] for field in registry[model_name].fields
                  if field.scope == 'device' and field.active(parameters) and field.key in parameters}
    single = evaluate(model_name, parameters,
                      environment, ambient_temperature_C=ambient)
    if single.error:
        return Evaluation(None, single.factors, error=f'电容网络单体：{single.error}')
    f = Factors()
    f.values.extend(single.factors)
    f.ranges.extend(item for item in single.parameter_ranges if item[1] != '内部')
    f.add('N', p['capacitor_count'], 'C.2.10 式（C.14）：电容器个数')
    return f.result(single.rate * p['capacitor_count'])


MODELS['进口电容网络'] = ModelSpec(
    [choice('kind', '单体电容器类型', _FIXED_CAPACITORS + VARIABLE_CAPACITORS,
            constraint_hint='仅表 C.63 提供 πch 的固定电容器类型（薄膜、云母、瓷介、铝电解、固体钽、氧化铌）可完整计算'),
     Input('capacitor_count', '电容器个数 N', integer=True),
     Input('capacitance_uF', '电容量 C（μF）', when=('kind', _FIXED_CAPACITORS)),
     choice('mounting', '安装形式', ['片式', '有引线'], when=('kind', _FIXED_CAPACITORS)),
     _quality('C.60'), temperature(), stress(label='电压应力比 S', values=tuple(n / 10 for n in range(1, 10)), source='表 C.62')],
    'λp = N × λp[单]', 'C.2.10，式（C.14）', category='C', section='C.2.10',
    note='按单体完整模型计算再乘数量；同类型、同参数和同应力。单体若缺 πch 则网络也不计算。',
    calculator=_capacitor_network, quality_tables=('C.60',),
    example=dict(kind='瓷介', capacitor_count=4, capacitance_uF=0.1, mounting='片式', quality='M', stress_ratio=0.5),
)


def _transformer(p, environment, _ambient):
    f = Factors()
    f.add('λb', *row_value('C.71', p['kind'], label_column=0))
    f.env('C.72', environment)
    f.quality('C.73', p['quality'])
    f.add('πT', *temp_below_value('C.74', p['temperature_C']))
    return f.result()


MODELS['进口变压器'] = ModelSpec(
    [choice('kind', '类别', _labels('C.71')), quality(_labels('C.73')), temperature()],
    'λp = λb × πE × πQ × πT', 'C.2.11.4，式（C.17）', category='L', section='C.2.11.4',
    calculator=_transformer, quality_tables=('C.73',),
    example=dict(kind='电源变压器', quality='完全按军用规范采购的产品'),
)
