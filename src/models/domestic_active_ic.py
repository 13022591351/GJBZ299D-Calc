"""Domestic integrated circuits and SAW devices, clauses 5.1 and 5.2."""

from dataclasses import replace
from functools import partial

from ..model_spec import Input, ModelSpec, choice, quality
from ..reference_data import (
    below_value,
    ceil_paired_value,
    ceil_row_value,
    fragments,
    resolve_numeric_tick,
    row_value,
    temp_paired_value,
)
from ..standard_lookups import (
    DIGITAL_IC_PACKAGES,
    SUPPLY_VOLTAGE_CHOICES,
    SUPPLY_VOLTAGE_HINT,
    SUPPLY_VOLTAGE_PRESETS,
    digital_ic_package,
    package_lead_choices_by,
    supply_voltage_factor,
)
from ._active_tables import (
    headers,
    int_or_float_options,
    numeric_axes,
    paired_axes,
    quality_choices,
    quality_value,
    rows,
    temp_triplet,
    triplet,
)
from .common import Factors

MODELS = {}
MATURITY = headers('29')
IC_QUALITY = quality_choices('3')


_LEAD_CHOICES_BY_27 = package_lead_choices_by('27', DIGITAL_IC_PACKAGES)
_PROM_TICKS_30 = int_or_float_options(sorted({
    float(t['grid'][(r, c)]['decimal_text'])
    for t in fragments('30')
    for r in range(1, t['rows'])
    for c in (0, 3)
    if t['grid'].get((r, c), {}).get('kind') == 'number'
}))


def _common_ic_fields(temp_table):
    return (
        quality(IC_QUALITY),
        choice('junction_temperature_C', '结温 Tj（℃）', int_or_float_options(paired_axes(temp_table)), '工作应力'),
        choice('supply_voltage_V', '工作电源电压 Vs（V）', SUPPLY_VOLTAGE_CHOICES, '工作应力',
               presets=SUPPLY_VOLTAGE_PRESETS, min_value=0, min_inclusive=False, max_value=15,
               ceil_choices=True, constraint_hint=SUPPLY_VOLTAGE_HINT),
        choice('package', '封装形式', DIGITAL_IC_PACKAGES),
        choice('lead_count', '引出端数', int_or_float_options(numeric_axes('27', first_row=2)),
               min_value=1, integer=True, ceil_choices=True, choices_by=_LEAD_CHOICES_BY_27),
        choice('maturity', '成熟程度', MATURITY, '质量与工艺'),
    )


def _ic_calculate(base_table, temp_table, cycle_kind, p, environment, ambient_temperature_C):
    f = Factors()
    axis_label = '门数' if base_table in ('4', '5') else '晶体管数' if base_table in ('6', '7', '8') else '位数'
    c1 = f.add('C1', *ceil_row_value(base_table, p['complexity'], 1, label_column=0, axis_label=axis_label))
    c2 = f.add('C2', *ceil_row_value(base_table, p['complexity'], 2, label_column=0, axis_label=axis_label))
    c3 = f.add('C3', *digital_ic_package(p['package'], p['lead_count']))
    t = f.add('πT', *temp_paired_value(temp_table, p['junction_temperature_C']))
    v = f.add('πV', *supply_voltage_factor(p['supply_voltage_V']))
    extra = 1
    if cycle_kind in ('PROM-双极', 'PROM-MOS'):
        if p['complexity'] <= 512:
            extra = f.add('πPT', *triplet('30', '≤ 512', 1 if cycle_kind == 'PROM-双极' else 2))
        else:
            pt_tick, pt_note = resolve_numeric_tick('30', p['complexity'], _PROM_TICKS_30, axis_label='位数')
            pt_val, pt_src = triplet('30', pt_tick, 1 if cycle_kind == 'PROM-双极' else 2)
            extra = f.add('πPT', pt_val, pt_src + pt_note)
    elif cycle_kind in ('EEPROM', 'FLASH'):
        extra = f.add('πCYC', *ceil_paired_value('31', p['programming_cycles'], axis_label='编程循环次数'))
    elif cycle_kind == 'UVEPROM':
        extra = f.add('πCYC', 1, '表 31 注：UVEPROM 的 πCYC = 1.0')
    e = f.env('28', environment)
    q = f.add('πQ', *quality_value('3', p['quality']))
    maturity = f.add('πL', *below_value('29', p['maturity']))
    return f.result(q * (c1 * t * v * extra + (c2 + c3) * e) * maturity)


_IC_TYPES = (
    ('模拟集成电路', '6', '18', '', '晶体管数 NT'),
    ('双极微处理器', '7', '19', '', '晶体管数 NT'),
    ('MOS微处理器', '8', '20', '', '晶体管数 NT'),
    ('双极SRAM', '9', '21', '', '位数 NB'),
    ('MOS SRAM及CCD器件', '10', '22', '', '位数 NB'),
    ('双极ROM', '11', '21', '', '位数 NB'),
    ('MOS ROM', '12', '22', '', '位数 NB'),
    ('双极PROM', '11', '21', 'PROM-双极', '位数 NB'),
    ('MOS PROM', '12', '22', 'PROM-MOS', '位数 NB'),
    ('MOS DRAM', '13', '22', '', '位数 NB'),
    ('MOS FIFO', '14', '22', '', '位数 NB'),
    ('MOS UVEPROM', '15', '22', 'UVEPROM', '位数 NB'),
    ('MOS EEPROM', '15', '22', 'EEPROM', '位数 NB'),
    ('MOS FLASH', '15', '22', 'FLASH', '位数 NB'),
)
for name, base, temp, cycle, axis_name in _IC_TYPES:
    fields = (
        choice('complexity', axis_name, int_or_float_options(numeric_axes(base)),
               min_value=1, integer=True, ceil_choices=True),
        *_common_ic_fields(temp),
    )
    example = {field.key: field.choices[0] for field in fields}
    example['lead_count'] = 4
    if cycle in ('EEPROM', 'FLASH'):
        fields += (choice('programming_cycles', '全寿命期内编程循环次数 NC',
                          int_or_float_options(paired_axes('31')),
                          min_value=1, integer=True, ceil_choices=True),)
        example['programming_cycles'] = 100
    MODELS['国产' + name] = ModelSpec(
        fields, 'λp = πQ[C1πTπV' + ('πPT' if cycle.startswith('PROM') else 'πCYC' if cycle else '')
        + ' + (C2 + C3)πE]πL', '5.1.2，表 2～31',
        category='IC', section='5.1.2', calculator=partial(_ic_calculate, base, temp, cycle),
        quality_tables=('3',), example=example,
    )


def _mmic(p, environment, ambient_temperature_C):
    f = Factors()
    c1 = f.add('C1', 0.0844, '表 2 注：微波单片集成电路 C1 = 0.0844 × 10⁻⁶/h')
    t = f.add('πT', *temp_paired_value('23', p['channel_temperature_C']))
    a = f.add('πA', *below_value('32', p['application']))
    process = f.add('πP', *below_value('33', p['process']))
    c3 = f.add('C3', *digital_ic_package(p['package'], p['lead_count']))
    e = f.env('28', environment)
    q = f.add('πQ', *quality_value('3', p['quality']))
    maturity = f.add('πL', *below_value('29', p['maturity']))
    return f.result(q * (c1 * t * a * process + c3 * e) * maturity)


_mmic_fields = (
    quality(IC_QUALITY),
    choice('channel_temperature_C', '沟道温度 Tch（℃）', int_or_float_options(paired_axes('23')), '工作应力'),
    choice('application', '应用', headers('32')),
    choice('process', '晶体管结构与工艺', headers('33', row=1)),
    choice('package', '封装形式', DIGITAL_IC_PACKAGES),
    choice('lead_count', '引出端数', int_or_float_options(numeric_axes('27', first_row=2)),
           min_value=1, integer=True, ceil_choices=True, choices_by=_LEAD_CHOICES_BY_27),
    choice('maturity', '成熟程度', MATURITY, '质量与工艺'),
)
MODELS['国产微波单片集成电路'] = ModelSpec(
    _mmic_fields, 'λp = πQ[C1πTπAπP + C3πE]πL', '5.1.2，表 2、3、23、27～29、32、33',
    category='IC', section='5.1.2', calculator=_mmic, quality_tables=('3',),
    example={**{field.key: field.choices[0] for field in _mmic_fields}, 'lead_count': 4},
)


def _mixed(p, environment, ambient_temperature_C):
    if p['digital_technology'] == '双极型' and p['digital_complexity'] > 60000:
        raise ValueError('表 4 双极型数字部分门数上限为 60000 门；超过 60000 门请核对工艺类型')
    analog = _ic_calculate('6', '18', '',
                           {key[7:]: value for key, value in p.items() if key.startswith('analog_')},
                           environment, ambient_temperature_C)
    digital = _ic_calculate('4' if p['digital_technology'] == '双极型' else '5',
                            '16' if p['digital_technology'] == '双极型' else '17', '',
                            {key[8:]: value for key, value in p.items() if key.startswith('digital_')},
                            environment, ambient_temperature_C)
    f = Factors()
    f.values = [('模拟部分 ' + symbol, value, source) for symbol, value, source in analog.factors]
    f.values += [('数字部分 ' + symbol, value, source) for symbol, value, source in digital.factors]
    return f.result(analog.rate + digital.rate)


_analog_fields = MODELS['国产模拟集成电路'].fields
_mixed_digital_gates = int_or_float_options(sorted(set(numeric_axes('4') + numeric_axes('5'))))
_digital_fields = (
    choice(
        'complexity',
        '门数 NG',
        _mixed_digital_gates,
        min_value=1,
        ceil_choices=True,
        choices_by=('digital_technology', (
            ('双极型', tuple(x for x in _mixed_digital_gates if x <= 60000)),
            ('MOS', int_or_float_options(numeric_axes('5'))),
        )),
        limits_by=('digital_technology', (
            ('双极型', (1, 60000, '表 4 双极型数字部分门数上限 60000 门')),
            ('MOS', (1, 10000000, '表 5 MOS 数字部分门数上限 10000000 门')),
        )),
        constraint_hint='表 4 双极型数字部分门数上限 60000 门；MOS 可至 10000000 门',
    ),
    *_common_ic_fields('16'),
)
_mixed_fields = (choice('digital_technology', '数字部分工艺', ('双极型', 'MOS')),)
for prefix, fields in (('analog', _analog_fields), ('digital', _digital_fields)):
    _local_keys = {f.key for f in fields}
    _mixed_fields += tuple(
        replace(
            field,
            key=f'{prefix}_{field.key}',
            section='模拟部分' if prefix == 'analog' else '数字部分',
            choices_by=(
                (f'{prefix}_{field.choices_by[0]}', field.choices_by[1])
                if field.choices_by and field.choices_by[0] in _local_keys
                else field.choices_by
            ),
            limits_by=(
                (f'{prefix}_{field.limits_by[0]}', field.limits_by[1])
                if field.limits_by and field.limits_by[0] in _local_keys
                else field.limits_by
            ),
        )
        for field in fields
    )
MODELS['国产模数混合集成电路'] = ModelSpec(
    _mixed_fields, 'λp = λp[模拟部分] + λp[数字部分]', '5.1.2，表 2～29',
    note='适用于 A/D、D/A、F/V、S/D、R/D、采样保持等电路；模拟与数字部分分别预计后求和。',
    category='IC', section='5.1.2', calculator=_mixed, quality_tables=('3',),
    example={**{field.key: field.choices[0] for field in _mixed_fields},
             'analog_lead_count': 4, 'digital_lead_count': 4},
)

EXTERNAL_TYPES = rows('40')


def _hybrid(p, environment, ambient_temperature_C):
    f = Factors()
    network = f.add('λC', *below_value('38', p['network_process']))
    resistor = f.add('λRT', *below_value('39', p['resistor_process']))
    assembly = f.add('λSF', *below_value('41', p['package_process']))
    external = p['mounted_ic_rate']
    f.add('ΣNDCλDC[外贴微电路]', external, '表 40；Tc=35 ℃、πE=1、C3=0、πL=1 的已预计外贴微电路合计')
    for index, kind in enumerate(EXTERNAL_TYPES):
        count = p[f'external_count_{index}']
        if count:
            rate, source = row_value('40', kind)
            external += count * rate
            f.add(f'NDCλDC[{kind}]', count * rate, f'{source}；数量 {count:g}')
    base = p['substrate_area_cm2'] * network + p['film_resistor_count'] * resistor + external + assembly
    f.add('λb', base, '式（4）：ASλC + λRTNRT + ΣλDCNDC + λSF')
    e = f.env('34', environment)
    q = f.add('πQ', *quality_value('35', p['quality']))
    maturity = f.add('πL', *below_value('29', p['maturity']))
    t = f.add('πT', *temp_triplet('36', p['hybrid_temperature_C'],
                                1 if p['temperature_basis'] == '封装底座温度' else 2))
    function = f.add('πF', *below_value('37', p['function']))
    return f.result(base * e * q * maturity * t * function)


_hybrid_fields = (
    quality(quality_choices('35')), Input('substrate_area_cm2', '基片面积 AS（cm²）'),
    choice('network_process', '网络工艺', headers('38')),
    choice('resistor_process', '膜电阻工艺', headers('39')),
    Input('film_resistor_count', '膜电阻数量 NRT', positive=False, integer=True, default=0),
    choice('package_process', '工艺和封装', headers('41')),
    choice('maturity', '成熟程度', MATURITY, '质量与工艺'),
    choice('temperature_basis', '温度依据', ('封装底座温度', '封装内最高温度'), '工作应力'),
    choice(
        'hybrid_temperature_C',
        '封装温度 T（℃）',
        range(35, 176, 5),
        '工作应力',
        positive=False,
        allow_negative=True,
        choices_by=('temperature_basis', (
            ('封装底座温度', tuple(range(35, 126, 5))),
            ('封装内最高温度', tuple(range(35, 176, 5))),
        )),
        limits_by=('temperature_basis', (
            ('封装底座温度', (-273.15, 125, '表 36 封装底座温度上限 125 ℃（≤35 ℃ 按 35 ℃ 档）')),
            ('封装内最高温度', (-273.15, 175, '表 36 封装内最高温度上限 175 ℃（≤35 ℃ 按 35 ℃ 档）')),
        )),
    ),
    choice('function', '电路功能类型', headers('37')),
    Input('mounted_ic_rate', '已预计外贴微电路合计 ΣNDCλDC（10⁻⁶/h）', '外贴微电路', positive=False, default=0),
) + tuple(Input(f'external_count_{i}', f'{kind}数量', '外贴元器件', positive=False, integer=True, default=0)
          for i, kind in enumerate(EXTERNAL_TYPES))
MODELS['国产混合集成电路'] = ModelSpec(
    _hybrid_fields, 'λp = (ASλC + λRTNRT + ΣλDCNDC + λSF)πEπQπLπTπF', '5.1.3，式（3）、（4），表 29、34～41',
    note='外贴无源和分立器件按表 40 数量自动累计；外贴微电路需按表 40 指定 Tc=35 ℃、πE=1、C3=0、πL=1 先分别预计并输入合计。',
    category='IC', section='5.1.3', calculator=_hybrid, quality_tables=('35',),
    example={field.key: (field.choices[0] if field.choices else 1 if field.positive else 0)
             for field in _hybrid_fields},
)


def _saw(p, environment, ambient_temperature_C):
    f = Factors()
    f.add('λb', *below_value('42', p['kind']))
    f.env('43', environment)
    f.add('πQ', *quality_value('44', p['quality']))
    return f.result()


MODELS['国产声表面波器件'] = ModelSpec(
    (choice('kind', '声表面波器件种类', headers('42')), quality(quality_choices('44'))),
    'λp = λbπEπQ', '5.2，式（5），表 42～44', category='SAW', section='5.2',
    calculator=_saw, quality_tables=('44',), example={'kind': '滤波器', 'quality': 'B2'},
)
