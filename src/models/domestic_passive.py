"""Domestic passive components: GJB/Z 299D-2024 sections 5.6–5.10."""

import re
from dataclasses import replace
from decimal import Decimal
from functools import partial

from ..model_spec import Evaluation, Input, ModelSpec, choice, quality, stress, temperature
from ..reference_data import (
    below_value,
    compact,
    fragments,
    paired_value,
    range_matches,
    result,
    row_value,
    stated_base_rate,
    temp_matrix_value,
    temp_paired_value,
)
from .common import Factors

MODELS: dict[str, ModelSpec] = {}
PAPER_SURFACE_LIMITATION = '表 204 未给出纸介、金属化纸介及纸-膜复合介质电容器的 πch，式（55）无法完整计算。'
PAPER_FILM_KINDS = (
    '聚苯乙烯，聚四氟乙烯，聚碳酸酯', '涤纶、聚丙烯等有机膜',
    '纸-膜复合介质', '纸介，金属化纸介',
)


def _row_labels(table, column=0):
    """Labels of data rows that have a numeric final coefficient."""
    return tuple(dict.fromkeys(
        compact(f['grid'][r, column]['text'])
        for f in fragments(str(table)) for r in range(1, f['rows'])
        if (r, column) in f['grid']
        and f['grid'].get((r, f['columns'] - 1), {}).get('kind') == 'number'
    ))


def _header_labels(table):
    return tuple(dict.fromkeys(
        c['text'] for f in fragments(str(table)) for c in f['cells']
        if c['row_no'] == 0 and c['column_no'] > 0
    ))


def _quality(table):
    return quality(_row_labels(table, 1))


def _range(factors, symbol, table, value, axis, key, column=0):
    """Look up a named range axis and retain its actual published interval."""
    candidates = []
    labels = []
    for fragment in fragments(str(table)):
        for row in range(1, fragment['rows']):
            label = fragment['grid'].get((row, column), {}).get('text', '')
            comparison = label
            if table in (229, 233, 238, 242, 234, 243):
                # These tables put units in the header, not beside each bound.
                # The R/V axis is already supplied in the table's Ω/V units;
                # use a unit-scale token solely to reuse the inequality parser.
                unit = 'μF' if axis == 'C' else 'Ω'
                comparison = ''.join(
                    term + unit if re.fullmatch(r'\d+(?:\.\d+)?', term) else term
                    for term in re.split(r'(≤|≥|<|>|=)', compact(label)))
            if range_matches(comparison, value, axis):
                cell = fragment['grid'].get((row, column + 1))
                if cell:
                    candidates.append((fragment, cell))
                    # Bare bounds inherit their unit from the table header.
                    # Public disclosures must keep that unit after extracting
                    # the row from its original table layout.
                    labels.append(label + (' [μF]' if axis == 'C' else ' [Ω/V]')
                                  if table in (229, 233, 238, 242, 234, 243) else label)
    answer = factors.add(symbol, *result(str(table), candidates))
    factors.ranges.append((key, labels[0]))
    return answer


def _base(factors, table, p):
    return factors.add('λb', *temp_matrix_value(str(table), p['temperature_C'], p['stress_ratio'], label_column=0))


def _kind(factors, table, value, symbol='πK'):
    if fragments(str(table))[0]['rows'] == 2:
        return factors.add(symbol, *below_value(str(table), value))
    return factors.add(symbol, *row_value(str(table), value, label_column=0))


def _resistor(p, environment, _ambient, *, base, env, q, resistance, kind=None, power_limit=None):
    if power_limit == 'small' and p['rated_power_W'] > 2:
        raise ValueError('本模型仅适用于额定功率 P≤2W 的电阻器')
    if power_limit == 'large' and p['rated_power_W'] <= 2:
        raise ValueError('本模型仅适用于额定功率 P>2W 的电阻器')
    if isinstance(base, dict):
        base = base[p['rated_temperature_C']]
    factors = Factors()
    _base(factors, base, p)
    factors.env(str(env), environment)
    factors.quality(str(q), p['quality'])
    _range(factors, 'πR', resistance, p['resistance_ohm'], 'R', 'resistance_ohm')
    if kind:
        _kind(factors, kind, p['kind'])
    return factors.result()


def _register_resistor(name, section, equation, base, env, q, resistance, kind=None, power_limit=None):
    stress_values = tuple(n / 10 for n in range(1, 10)) if base == 143 else None
    fields = [Input('resistance_ohm', '阻值 R（Ω）'), _quality(q), temperature(),
              stress(label='功率应力比 S', values=stress_values,
                     source=f'表 {base if not isinstance(base, dict) else "、".join(map(str, base.values()))}')]
    example = dict(resistance_ohm=1000, quality='B2', stress_ratio=0.5)
    if isinstance(base, dict):
        fields.insert(1, choice('rated_temperature_C', '额定温度（℃）', base))
        example['rated_temperature_C'] = next(iter(base))
    if kind:
        labels = _header_labels(kind) if fragments(str(kind))[0]['rows'] == 2 else _row_labels(kind)
        fields.insert(1, choice('kind', '种类', labels))
        example['kind'] = labels[0]
    if power_limit == 'small':
        fields.insert(1, Input(
            'rated_power_W', '额定功率 P（W）', '额定值',
            min_value=0, min_inclusive=False, max_value=2, max_inclusive=True,
            presets=(0.125, 0.25, 0.5, 1, 2),
            constraint_hint='本模型仅适用于额定功率 P ≤ 2 W 的电阻器',
        ))
        example['rated_power_W'] = 1
    elif power_limit == 'large':
        fields.insert(1, Input(
            'rated_power_W', '额定功率 P（W）', '额定值',
            min_value=2, min_inclusive=False,
            presets=(3, 5, 10, 25, 50),
            constraint_hint='本模型仅适用于额定功率 P > 2 W 的功率非线绕电阻器',
        ))
        example['rated_power_W'] = 5
    MODELS[name] = ModelSpec(
        fields, 'λp = λb × πE × πQ × πR' + (' × πK' if kind else ''),
        f'{section}，式（{equation}）', category='R', section=section,
        calculator=partial(_resistor, base=base, env=env, q=q, resistance=resistance, kind=kind, power_limit=power_limit),
        quality_tables=(str(q),), example=example,
    )


for _row in (
    ('国产金属膜电阻器', '5.6.2', 35, {70: 125, 125: 126}, 127, 128, 129, None, None),
    ('国产碳膜电阻器', '5.6.3', 36, 130, 131, 132, 133, None, 'small'),
    ('国产玻璃釉电阻器', '5.6.4', 37, 134, 135, 136, 137, None, 'small'),
    ('国产线绕电阻器', '5.6.5', 38, 138, 139, 140, 141, 142, None),
    ('国产功率线绕电阻器', '5.6.6', 39, 143, 144, 145, 146, None, None),
    ('国产箔式电阻器', '5.6.7', 40, 147, 148, 149, 150, 151, None),
    ('国产功率非线绕电阻器', '5.6.8', 41, 152, 153, 154, 155, None, 'large'),
):
    _register_resistor(*_row)


def _resistor_network(p, environment, _ambient):
    f = Factors()
    f.add('λb', *below_value('160', p['kind']))
    f.add('NR', p['resistor_count'], '5.6.10 式（43）：使用中的电阻数，不包括未使用电阻')
    f.add('πT', *temp_paired_value('161', p['case_temperature_C']))
    f.env('162', environment)
    f.quality('163', p['quality'])
    return f.result()


MODELS['国产电阻网络'] = ModelSpec(
    [choice('kind', '类型', _header_labels(160)),
     Input('resistor_count', '使用中的电阻数 NR', integer=True, min_value=1, presets=(2, 4, 8),
           constraint_hint='使用中的电阻数须为正整数（≥ 1）'),
     Input('case_temperature_C', '工作温度 Tp [壳温]（℃）', '工作应力', positive=False, allow_negative=True,
           min_value=-273.15, max_value=125, presets=tuple(range(25, 126, 5)),
           constraint_hint='表 161 壳温 Tp 上限 125 ℃（≤25 ℃ 按 25 ℃ 档，其余向上取档）'),
     _quality(163)],
    'λp = λb × NR × πT × πE × πQ', '5.6.10，式（43）', category='R', section='5.6.10',
    note='壳温按表 161 查表；使用中的电阻数不包含未使用的电阻。',
    calculator=_resistor_network, quality_tables=('163',),
    example=dict(kind='单个分立元件', resistor_count=4, case_temperature_C=40, quality='B2'),
)


def _sensitive(p, environment, _ambient, *, kind, temp_table):
    f = Factors()
    f.add('λb', *below_value('164', kind))
    f.env('165', environment)
    f.quality('166', p['quality'])
    f.add('πT', *temp_paired_value(str(temp_table), p['temperature_C']))
    return f.result()


for _kind_name, _temp_table in [('热敏电阻器', 167), ('压敏电阻器', 168), ('光敏电阻器', 168)]:
    MODELS['国产' + _kind_name] = ModelSpec(
        [_quality(166), temperature()], 'λp = λb × πE × πQ × πT', '5.7，式（44）',
        category='RT', section='5.7', calculator=partial(_sensitive, kind=_kind_name, temp_table=_temp_table),
        note='按表 167/168 查工作环境温度系数（表列不含 50℃，向上归入 55℃ 档）。',
        quality_tables=('166',), example=dict(quality='B2'),
    )


def _potentiometer(p, environment, _ambient, *, base, env, q, resistance, kind=None, construction=None):
    f = Factors()
    _base(f, base, p)
    f.env(str(env), environment)
    f.quality(str(q), p['quality'])
    _range(f, 'πR', resistance, p['resistance_ohm'], 'R', 'resistance_ohm')
    if kind:
        _kind(f, kind, p['kind'])
    if construction:
        _kind(f, construction, p['construction'], 'πC')
    n = p['terminal_count']
    if n < 3:
        raise ValueError('电位器引出端数至少为 3 [包括滑动引出端和固定引出端]')
    if n <= 16:
        f.add('πTAPS', *paired_value('170', n))
    else:
        f.add('πTAPS', n ** 1.5 / 25 + 0.792, '5.8.1 表 170 注 a，式（46）：N^(3/2)/25+0.792')
    return f.result()


for _name, _section, _equation, _base_table, _env, _q, _r, _kind_table, _construction in (
    ('普通线绕电位器', '5.8.2', 47, 171, 172, 173, 174, None, None),
    ('精密线绕电位器', '5.8.3', 48, 175, 176, 177, 178, None, None),
    ('微调线绕电位器', '5.8.4', 49, 179, 180, 181, 182, None, None),
    ('有机实芯电位器', '5.8.5', 50, 183, 184, 185, 186, 187, 188),
    ('合成碳膜电位器', '5.8.6', 51, 189, 190, 191, 192, 193, 194),
    ('玻璃釉电位器', '5.8.7', 52, 195, 196, 197, 198, 199, None),
    ('非线绕精密电位器', '5.8.8', 53, 200, 201, 202, 203, None, None),
):
    _fields = [Input('resistance_ohm', '阻值 R（Ω）'),
               Input('terminal_count', '引出端数 N', integer=True, min_value=3,
                     presets=(3, 4, 5, 6, 8, 10, 16),
                     constraint_hint='表 170 引出端数 N ≥ 3（含滑动与固定端；＞16 按式 46 计算）'),
               _quality(_q), temperature(), stress(label='功率应力比 S', source=f'表 {_base_table}')]
    _example = dict(resistance_ohm=1000, terminal_count=3, quality='B2', stress_ratio=0.5)
    if _kind_table:
        _fields.append(choice('kind', '种类', _row_labels(_kind_table)))
        _example['kind'] = _row_labels(_kind_table)[0]
    if _construction:
        _fields.append(choice('construction', '结构类型', _row_labels(_construction)))
        _example['construction'] = _row_labels(_construction)[0]
    MODELS['国产' + _name] = ModelSpec(
        _fields, 'λp = λb × πE × πQ × πR' + (' × πK' if _kind_table else '')
        + (' × πC' if _construction else '') + ' × πTAPS', f'{_section}，式（{_equation}）',
        category='RP', section=_section, calculator=partial(_potentiometer, base=_base_table, env=_env,
        q=_q, resistance=_r, kind=_kind_table, construction=_construction),
        quality_tables=(str(_q),), example=_example,
    )


def _capacitor(p, environment, _ambient, *, base, env, q, capacitance, surface_kind=None, kind=None, series=None):
    if isinstance(base, dict):
        base = base[p['rated_temperature_C']]
    f = Factors()
    _base(f, base, p)
    f.env(str(env), environment)
    f.quality(str(q), p['quality'])
    # Table 210 has two independent range/value pairs, not one four-axis table.
    range_column = 0
    if capacitance == 210:
        range_column = 0 if p['kind'] == '纸介，金属化纸介' else 2
    _range(f, 'πCV', capacitance, p['capacitance_uF'], 'C', 'capacitance_uF', range_column)
    if kind:
        source_kind = ('涤纶、聚丙烯等有机膜，纸-膜复合介质'
                       if kind == 211 and p['kind'] in PAPER_FILM_KINDS[1:3] else p['kind'])
        _kind(f, kind, source_kind)
    if series:
        ratio = Decimal(str(p['series_resistance_ohm'])) / Decimal(str(p['operating_voltage_V']))
        _range(f, 'πSR', series, ratio, 'R', 'series_resistance_per_volt')
    if surface_kind:
        if kind == 211 and p['kind'] in PAPER_FILM_KINDS[2:]:
            return Evaluation(None, tuple(f.values), error=PAPER_SURFACE_LIMITATION,
                              parameter_ranges=tuple(f.ranges))
        f.add('πch', *row_value('204', surface_kind, 1 if p['mounting'] == '片式' else 2, label_column=0))
    return f.result()


for _name, _section, _equation, _base_table, _env, _q, _c, _surface, _kind_table, _series in (
    ('纸和薄膜电容器', '5.9.2', 55, {65: 205, 85: 206, 100: 207}, 208, 209, 210, '薄膜电容器', 211, None),
    ('云母电容器', '5.9.3', 56, {70: 212, 85: 213}, 214, 215, 216, '云母电容器', None, None),
    ('铝电解电容器', '5.9.6', 59, 226, 227, 228, 229, '铝电解电容器', None, None),
    ('固体钽电解电容器', '5.9.7', 60, 230, 231, 232, 233, '固体钽电解电容器', None, 234),
    ('非固体钽电解电容器', '5.9.8', 61, 235, 236, 237, 238, None, None, None),
    ('氧化铌电解电容器', '5.9.9', 62, 239, 240, 241, 242, '氧化铌电解电容器', None, 243),
):
    _cap_input = (
        Input('capacitance_uF', '电容量 C（μF）', min_value=3.3, min_inclusive=False,
              presets=(4.7, 10, 22, 47, 100), constraint_hint='表 242 要求电容量 C > 3.3 μF')
        if _c == 242 else Input('capacitance_uF', '电容量 C（μF）')
    )
    _fields = [_cap_input, _quality(_q), temperature(),
               stress(label='电压应力比 S', source=f'表 {_base_table if not isinstance(_base_table, dict) else "、".join(map(str, _base_table.values()))}')]
    _example = dict(capacitance_uF=10, quality='B2', stress_ratio=0.5)
    if isinstance(_base_table, dict):
        _fields.append(choice('rated_temperature_C', '额定温度（℃）', _base_table))
        _example['rated_temperature_C'] = next(iter(_base_table))
    if _surface:
        _fields.append(choice('mounting', '安装形式', ['片式', '有引线']))
        _example['mounting'] = '有引线'
    if _kind_table:
        labels = PAPER_FILM_KINDS if _kind_table == 211 else _row_labels(_kind_table)
        _fields.append(choice('kind', '介质种类', labels))
        _example['kind'] = labels[0]
    if _series:
        _fields.extend([Input('series_resistance_ohm', '电容器与电源之间的回路电阻（Ω）', '工作应力', positive=False),
                        Input('operating_voltage_V', '电容器工作电压（V）', '工作应力')])
        _example.update(series_resistance_ohm=2, operating_voltage_V=10)
    MODELS['国产' + _name] = ModelSpec(
        _fields, 'λp = λb × πE × πQ × πCV' + (' × πK' if _kind_table else '')
        + (' × πSR' if _series else '') + (' × πch' if _surface else ''),
        f'{_section}，式（{_equation}）', category='C', section=_section,
        calculator=partial(_capacitor, base=_base_table, env=_env, q=_q, capacitance=_c,
                           surface_kind=_surface, kind=_kind_table, series=_series),
        quality_tables=(str(_q),), example=_example,
    )


def _variable_capacitor(p, environment, _ambient):
    f = Factors()
    f.add('λb', *row_value('244', p['kind'], label_column=0))
    f.env('245', environment)
    f.quality('246', p['quality'])
    return f.result()


MODELS['国产可变电容器'] = ModelSpec(
    [choice('kind', '类型', _row_labels(244)), _quality(246)], 'λp = λb × πE × πQ', '5.9.10，式（63）',
    category='C', section='5.9.10', calculator=_variable_capacitor, quality_tables=('246',),
    example=dict(kind='瓷介微调', quality='B2'),
)


def _inductive(p, environment, _ambient, *, base, env, q, kind_table):
    f = Factors()
    f.add('λb', *temp_matrix_value(str(base), p['hotspot_temperature_C'], p['insulation_class'], label_column=0))
    f.env(str(env), environment)
    f.quality(str(q), p['quality'])
    _kind(f, kind_table, p['kind'], 'πC' if base == 249 else 'πK')
    return f.result()


def _hotspot_constraints(base_table):
    ins_temps = {}
    for fragment in fragments(str(base_table)):
        cols = {c['column_no']: c['text'] for c in fragment['cells'] if c['row_no'] == 1 and c['column_no'] > 0}
        for row in range(4, fragment['rows']):
            tcell = fragment['grid'].get((row, 0))
            if not tcell or tcell['kind'] != 'number':
                continue
            temp = int(float(tcell['decimal_text']))
            for col_no, ins in cols.items():
                val = fragment['grid'].get((row, col_no))
                if val and val['kind'] == 'number':
                    ins_temps.setdefault(ins, []).append(temp)
    choices_branches = tuple((ins, tuple(temps)) for ins, temps in ins_temps.items())
    limits_branches = tuple(
        (ins, (-273.15, max(temps), f'表 {base_table} {ins} 级绝缘热点温度上限 {max(temps)} ℃（≤25 ℃ 按 25 ℃ 档，其余向上取档）'))
        for ins, temps in ins_temps.items()
    )
    all_temps = tuple(sorted({t for temps in ins_temps.values() for t in temps}))
    return all_temps, choices_branches, limits_branches


for _name, _section, _equation, _base_table, _env, _q, _kind_table in (
    ('电感器', '5.10.2', 65, 249, 250, 251, 252),
    ('变压器', '5.10.4', 67, 256, 257, 258, 259),
):
    _insulation = tuple(c['text'] for c in fragments(str(_base_table))[0]['cells']
                        if c['row_no'] == 1 and c['column_no'] > 0)
    _all_hs, _hs_choices_by, _hs_limits_by = _hotspot_constraints(_base_table)
    MODELS['国产' + _name] = ModelSpec(
        [choice('kind', '结构' if _base_table == 249 else '种类', _header_labels(_kind_table)),
         choice('insulation_class', '绝缘等级', _insulation),
         Input('hotspot_temperature_C', '热点温度 THS（℃）', '工作应力', positive=False, allow_negative=True,
               min_value=-273.15, max_value=max(_all_hs), presets=_all_hs,
               choices_by=('insulation_class', _hs_choices_by),
               limits_by=('insulation_class', _hs_limits_by),
               constraint_hint=f'表 {_base_table} 按绝缘等级限定热点温度上限（≤25 ℃ 按 25 ℃ 档，其余向上取档）'),
         _quality(_q)],
        'λp = λb × πE × πQ × ' + ('πC' if _base_table == 249 else 'πK'),
        f'{_section}，式（{_equation}）', category='L', section=_section,
        note='热点温度按表列值查表。表 256 的温升估算符号存在歧义，变压器请填写已确定的热点温度。'
             if _base_table == 256 else '热点温度按表 249 查表；绝缘等级和温度组合未给出的条件不计算。',
        calculator=partial(_inductive, base=_base_table, env=_env, q=_q, kind_table=_kind_table),
        quality_tables=(str(_q),), example=dict(kind=_header_labels(_kind_table)[0], insulation_class='A',
        hotspot_temperature_C=40, quality='B2'),
    )


def _chip_inductor(p, environment, _ambient):
    f = Factors()
    f.add('λb', *stated_base_rate('5.10.3'))
    f.env('253', environment)
    f.quality('254', p['quality'])
    f.add('πT', *temp_paired_value('255', p['hotspot_temperature_C']))
    return f.result()


MODELS['国产片式电感器'] = ModelSpec(
    [Input('hotspot_temperature_C', '热点温度 THS（℃）', '工作应力', positive=False, allow_negative=True,
           min_value=-273.15, max_value=190, presets=tuple(range(20, 191, 10)),
           constraint_hint='表 255 热点温度 THS 上限 190 ℃（≤20 ℃ 按 20 ℃ 档，其余向上取档）'),
     _quality(254)],
    'λp = λb × πE × πQ × πT', '5.10.3，式（66）', category='L', section='5.10.3',
    calculator=_chip_inductor, quality_tables=('254',), example=dict(hotspot_temperature_C=40, quality='B2'),
)


def _capacitor_network(p, environment, ambient):
    # Lazy import avoids a registry import cycle.  The choices exclude networks.
    from ..standards import MODELS as registry
    from ..standards import evaluate

    parameters = {field.key: p[field.key] for field in registry[p['capacitor_model']].fields
                  if field.scope == 'device' and field.active(p) and field.key in p}
    single = evaluate(p['capacitor_model'], parameters, environment, ambient_temperature_C=ambient)
    if single.error:
        raise ValueError(f'电容网络单体：{single.error}')
    f = Factors()
    f.values.extend(single.factors)
    f.ranges.extend(single.parameter_ranges)
    f.add('N', p['capacitor_count'], '5.9.11 式（64）：电容器个数')
    return f.result(single.rate * p['capacitor_count'])


# The single-capacitor parameters remain physical inputs.  Sharing a field across
# child models is safe because the selected model performs its own validation.
_cap_specs = {name: model for name, model in MODELS.items() if model.category == 'C'}
_cap_specs['国产1类瓷介电容器'] = ModelSpec([
    Input('capacitance_uF', '电容量 C（μF）'), choice('mounting', '安装形式', ['片式', '有引线']),
    _quality(219), temperature(range(0, 86, 5)), stress(label='电压应力比 S', source='表 217'),
], '', '')
_cap_specs['国产2类瓷介电容器'] = ModelSpec([
    Input('capacitance_uF', '电容量 C（μF）'), choice('mounting', '安装形式', ['片式', '有引线']),
    choice('rated_temperature_C', '额定温度（℃）', [85, 125]),
    _quality(224), temperature(range(0, 126, 5)), stress(label='电压应力比 S', source='表 221、222'),
], '', '')
_CAP_NETWORK_TEMP_LIMITS = (
    ('国产1类瓷介电容器', (-273.15, 85, '表 217 1类瓷介电容器环境温度上限 85 ℃')),
    ('国产2类瓷介电容器', (-273.15, 125, '表 221/222 2类瓷介电容器环境温度上限 125 ℃')),
    ('国产纸和薄膜电容器', (-273.15, 100, '表 205～207 纸和薄膜电容器环境温度上限 100 ℃')),
    ('国产云母电容器', (-273.15, 85, '表 212～213 云母电容器环境温度上限 85 ℃')),
    ('国产铝电解电容器', (-273.15, 85, '表 226 铝电解电容器环境温度上限 85 ℃')),
    ('国产固体钽电解电容器', (-273.15, 125, '表 230 固体钽电解电容器环境温度上限 125 ℃')),
    ('国产非固体钽电解电容器', (-273.15, 85, '表 235 非固体钽电解电容器环境温度上限 85 ℃')),
    ('国产氧化铌电解电容器', (-273.15, 125, '表 239 氧化铌电解电容器环境温度上限 125 ℃')),
)
_network_fields = [choice('capacitor_model', '单体电容器类型', _cap_specs),
                   Input('capacitor_count', '电容器个数 N', integer=True, min_value=1, presets=(2, 4, 8),
                         constraint_hint='表 64 电容器个数须为正整数（≥ 1）')]
_network_keys = dict.fromkeys(field.key for model in _cap_specs.values() for field in model.fields)
for _key in _network_keys:
    _uses = [(name, field) for name, model in _cap_specs.items() for field in model.fields if field.key == _key]
    _template = _uses[0][1]
    _choices = None if any(field.choices is None for _, field in _uses) else tuple(dict.fromkeys(
        value for _, field in _uses for value in field.choices))
    _extra = {}
    if _key == 'kind':
        _extra = {
            'label': '介质种类 / 可变类型',
            'choices_by': ('capacitor_model', (
                ('国产纸和薄膜电容器', PAPER_FILM_KINDS[:2]),
                ('国产可变电容器', _row_labels(244)),
            )),
            'constraint_hint': '纸和薄膜电容器选薄膜介质种类；可变电容器选微调类型',
        }
    elif _key == 'rated_temperature_C':
        _extra = {
            'choices_by': ('capacitor_model', tuple((name, f.choices) for name, f in _uses)),
            'constraint_hint': '纸和薄膜可选 65/85/100 ℃；云母可选 70/85 ℃；2 类瓷介可选 85/125 ℃',
        }
    elif _key == 'quality':
        _extra = {
            'choices_by': ('capacitor_model', tuple((name, f.choices) for name, f in _uses)),
            'constraint_hint': '按所选单体电容器类型限定可选质量等级',
        }
    elif _key == 'capacitance_uF':
        _extra = {
            'min_value': None,
            'min_inclusive': True,
            'presets': (),
            'limits_by': ('capacitor_model', (
                ('国产氧化铌电解电容器', (3.3, None, '表 242 要求氧化铌电解电容器电容量 C > 3.3 μF', False, True)),
            )),
            'constraint_hint': '',
        }
    elif _key == 'temperature_C':
        _extra = {
            'limits_by': ('capacitor_model', _CAP_NETWORK_TEMP_LIMITS),
        }
    _network_fields.append(replace(_template, choices=_choices,
                                   when=('capacitor_model', tuple(name for name, _ in _uses)),
                                   stress_source='按所选单体电容器的电压应力比表'
                                   if _template.stress_max is not None else _template.stress_source,
                                   **_extra))
MODELS['国产电容网络'] = ModelSpec(
    _network_fields, 'λp = N × λp[单]', '5.9.11，式（64）', category='C', section='5.9.11',
    note='按所选单体电容器的完整工作失效率模型计算，再乘电容器个数；单体类型、参数和应力相同。',
    calculator=_capacitor_network, quality_tables=tuple(str(n) for n in (209, 215, 219, 224, 228, 232, 237, 241, 246)),
    example=dict(capacitor_model='国产非固体钽电解电容器', capacitor_count=4,
                 capacitance_uF=10, quality='B2', stress_ratio=0.5),
)

# Coefficient-only tables defer quality meanings to their section's definitions.
for _name, _model in tuple(MODELS.items()):
    _definition_table = {'R': '124', 'RP': '169', 'L': '247'}.get(_model.category)
    if _definition_table:
        MODELS[_name] = replace(_model, quality_tables=_model.quality_tables + (_definition_table,))
