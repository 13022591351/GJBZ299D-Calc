"""Domestic semiconductor discretes, clauses 5.3.2–5.3.10."""

from dataclasses import replace
from fractions import Fraction
from functools import partial

from ..model_spec import Input, ModelSpec, choice, quality, stress, temperature
from ..reference_data import below_value, matrix_value, temp_matrix_value, temp_paired_value
from ..semiconductor import temperature_correction
from ..stress import StressAdjustment, ceil_stress, trace_number
from ._active_tables import bands, grouped, headers, quality_choices, quality_value, rows
from .common import Factors

MODELS = {}
TEMP_FIELDS = (
    temperature(),
    choice('temperature_basis', '工作温度依据', ('工作环境温度', '带散热片功率器件的管壳温度'), '工作应力'),
    Input('case_temperature_C', '管壳温度 Tc（℃）', '工作应力', positive=False, allow_negative=True,
          when=('temperature_basis', ('带散热片功率器件的管壳温度',))),
    Input('rated_temperature_C', '额定温度 Ts（℃）', '额定条件', positive=False,
          min_value=25, max_value=200, max_inclusive=False,
          presets=(25, 50, 75, 100), constraint_hint='5.3.10.2 要求 Ts ≥ 25 ℃ 且 Ts < TM'),
    Input('max_junction_temperature_C', '最高允许结温 TM（℃）', '额定条件',
          min_value=25, min_inclusive=False, max_value=200,
          presets=(125, 150, 175, 200), constraint_hint='5.3.10.2 要求 Ts < TM ≤ 200 ℃'),
)
TEMP_EXAMPLE = {'temperature_basis': '工作环境温度', 'rated_temperature_C': 25,
                'max_junction_temperature_C': 175}


def _fraction(p, key):
    return Fraction(str(p[key]))


def _ratio(p, operating, rated):
    actual, limit = _fraction(p, operating), _fraction(p, rated)
    if actual <= 0 or limit <= 0 or actual > limit:
        raise ValueError('使用负荷须大于 0 且不超过额定值；高于 Ts 时须核查器件降额曲线')
    return actual / limit


def _load_fields(mode, **rated_kwargs):
    if mode == 'current':
        return (Input('operating_current_A', '使用平均正向电流 IOP（A）', '工作应力'),
                Input('rated_current_A', 'Ts 时额定平均正向电流 IM（A）', '额定条件', **rated_kwargs))
    if mode == 'energy':
        return (Input('operating_energy_J', '工作尖峰漏泄能量 J1（J）', '工作应力'),
                Input('rated_energy_J', '25 ℃额定烧毁能量 J2（J）', '额定条件', **rated_kwargs))
    return (Input('operating_power_W', '使用功耗 POP（W）', '工作应力'),
            Input('rated_power_W', 'Ts 时额定功耗 PM（W）', '额定条件', **rated_kwargs))


def _load_example(mode):
    if mode == 'current':
        return {'operating_current_A': 0.1, 'rated_current_A': 1}
    if mode == 'energy':
        return {'operating_energy_J': 0.1, 'rated_energy_J': 1}
    return {'operating_power_W': 0.1, 'rated_power_W': 1}


def _base(f, p, ambient, table, mode='power', *, uncorrected_stress=False, raw_stress=None):
    correction = temperature_correction(p, ambient)
    if raw_stress is None:
        keys = {'power': ('operating_power_W', 'rated_power_W'),
                'current': ('operating_current_A', 'rated_current_A'),
                'energy': ('operating_energy_J', 'rated_energy_J')}[mode]
        raw_stress = _ratio(p, *keys)
    raw_stress *= 1 if uncorrected_stress else correction.coefficient
    axis = ceil_stress(raw_stress, 1)
    value, source = temp_matrix_value(table, correction.temperature, axis, label_column=0)
    f.add('λb', value, f'5.3.10：C={float(correction.coefficient):g}，'
          f'校正 T={float(correction.temperature):g} ℃；{source}')
    return StressAdjustment('stress_ratio', '校正电应力比 S', trace_number(raw_stress), axis,
                            f'5.3.10，表 {table}')


def _quality(f, table, p):
    return f.add('πQ', *quality_value(table, p['quality']))


ORDINARY = {'整流二极管': '47', '开关二极管': '48', '检波二极管': '49', '瞬态抑制二极管': '50'}


def _ordinary(p, environment, ambient):
    f = Factors()
    adjustment = _base(f, p, ambient, ORDINARY[p['kind']], 'current')
    f.env('51', environment)
    _quality(f, '52', p)
    f.add('πr', *bands('53', p['rated_current_A'], 'I'))
    f.add('πS2', *below_value('54', '≤ 0.3' if p['voltage_ratio'] <= 0.3 else p['voltage_ratio']))
    f.add('πC', *below_value('55', p['structure']))
    return replace(f.result(), stress_adjustments=(adjustment,))


MODELS['国产普通二极管'] = ModelSpec(
    (choice('kind', '二极管种类', ORDINARY), quality(quality_choices('52')), *TEMP_FIELDS,
     *_load_fields('current', presets=(0.5, 1, 3, 10, 20, 50),
                   constraint_hint='表 53 分档：≤1、1～3、3～10、10～20、＞20 A'),
     stress('voltage_ratio', '反向电压应力比 S2 = VR/VRM',
            values=(.3, .4, .5, .6, .7, .8, .9, 1), source='表 54'),
     choice('structure', '接触结构', headers('55'))),
    'λp = λbπEπQπrπS2πC', '5.3.2、5.3.10，式（7）、（19）～（25），表 47～55',
    category='D', section='5.3.2', calculator=_ordinary, quality_tables=('46',),
    example={**TEMP_EXAMPLE, **_load_example('current'), 'kind': '整流二极管', 'quality': 'B2',
             'voltage_ratio': .3, 'structure': '冶金键合'},
)


def _zener(p, environment, ambient):
    f = Factors()
    mode = 'current' if p['load_basis'] == '电流额定值' else 'power'
    adjustment = _base(f, p, ambient, '56', mode)
    f.env('57', environment)
    _quality(f, '58', p)
    f.add('πr', *bands('53', p['rated_current_A'], 'I'))
    f.add('πA', *below_value('59', p['application']))
    return replace(f.result(), stress_adjustments=(adjustment,))


MODELS['国产电压调整、电压基准及电流调整二极管'] = ModelSpec(
    (quality(quality_choices('58')), choice('application', '应用', headers('59')), *TEMP_FIELDS,
     choice('load_basis', '电应力计算依据', ('电流额定值', '功率额定值'), '工作应力'),
     Input('rated_current_A', '额定电流 IM（A）', '额定条件',
           presets=(0.5, 1, 3, 10, 20, 50),
           constraint_hint='表 53 分档：≤1、1～3、3～10、10～20、＞20 A'),
     Input('operating_current_A', '实际使用电流 IZ(OP)（A）', '工作应力',
           when=('load_basis', ('电流额定值',))),
     *(replace(field, when=('load_basis', ('功率额定值',))) for field in _load_fields('power'))),
    'λp = λbπEπQπrπA', '5.3.3、5.3.10，式（8）、（26），表 53、56～59',
    category='D', section='5.3.3', calculator=_zener, quality_tables=('46',),
    example={**TEMP_EXAMPLE, **_load_example('current'), 'quality': 'B2',
             'application': '电压调整', 'load_basis': '电流额定值'},
)


def _simple(table, etable, qtable, mode, uncorrected, p, environment, ambient):
    f = Factors()
    adjustment = _base(f, p, ambient, table, mode, uncorrected_stress=uncorrected)
    f.env(etable, environment)
    _quality(f, qtable, p)
    return replace(f.result(), stress_adjustments=(adjustment,))


for name, base, etable, qtable, mode, uncorrected, section in (
    ('国产微波硅检波二极管', '60', '62', '63', 'power', True, '5.3.4.1'),
    ('国产微波硅混频二极管', '61', '62', '63', 'energy', True, '5.3.4.1'),
    ('国产单结晶体管', '100', '101', '102', 'power', False, '5.3.8'),
):
    MODELS[name] = ModelSpec(
        (quality(quality_choices(qtable)), *TEMP_FIELDS, *_load_fields(mode)),
        'λp = λbπEπQ', f'{section}、5.3.10，表 {base}、{etable}、{qtable}',
        category='Q' if section == '5.3.8' else 'D', section=section,
        calculator=partial(_simple, base, etable, qtable, mode, uncorrected), quality_tables=('46',),
        example={**TEMP_EXAMPLE, **_load_example(mode), 'quality': 'B2'},
    )


def _microwave_diode(p, environment, ambient):
    f = Factors()
    adjustment = _base(f, p, ambient, '64')
    f.env('65', environment)
    _quality(f, '66', p)
    f.add('πK', *grouped('67', p['kind']))
    return replace(f.result(), stress_adjustments=(adjustment,))


MODELS['国产其他微波二极管'] = ModelSpec(
    (choice('kind', '二极管种类及应用条件', rows('67', columns=(0, 1))),
     quality(quality_choices('66')), *TEMP_FIELDS, *_load_fields('power')),
    'λp = λbπEπQπK', '5.3.4.2、5.3.10，式（10）、（29），表 64～67',
    category='D', section='5.3.4.2', calculator=_microwave_diode, quality_tables=('46',),
    example={**TEMP_EXAMPLE, **_load_example('power'), 'quality': 'B2',
             'kind': rows('67', columns=(0, 1))[0]},
)


def _bjt(p, environment, ambient):
    f = Factors()
    base = {'小功率晶体管': '68', '大功率晶体管': '68', '开关晶体管': '69', '达林顿晶体管': '70'}[p['kind']]
    if (p['kind'] == '小功率晶体管' and p['rated_power_W'] >= 1
            or p['kind'] == '大功率晶体管' and p['rated_power_W'] < 1):
        raise ValueError('小功率晶体管额定功率 < 1 W；大功率晶体管额定功率 ≥ 1 W')
    adjustment = _base(f, p, ambient, base)
    f.env('71', environment)
    _quality(f, '72', p)
    f.add('πA', *below_value('73', p['application']))
    f.add('πS2', *below_value('74', p['voltage_ratio']))
    f.add('πr', *bands('75', p['rated_power_W'], 'P'))
    f.add('πC', *below_value('76', '达林顿对' if p['kind'] == '达林顿晶体管' else '单管'))
    f.add('πK', *below_value('77', p['polarity']))
    return replace(f.result(), stress_adjustments=(adjustment,))


_BJT_FIELDS = (
    choice('kind', '晶体管种类', ('小功率晶体管', '大功率晶体管', '开关晶体管', '达林顿晶体管')),
    quality(quality_choices('72')), *TEMP_FIELDS,
    *_load_fields(
        'power',
        presets=(0.5, 0.8, 1, 5, 20, 50, 200),
        choices_by=('kind', (
            ('小功率晶体管', (0.1, 0.3, 0.5, 0.8)),
            ('大功率晶体管', (1, 5, 20, 50, 200)),
        )),
        limits_by=('kind', (
            ('小功率晶体管', (0, 1, '小功率晶体管要求 Ts 时额定功耗 P < 1 W（表 75）', False, False)),
            ('大功率晶体管', (1, None, '大功率晶体管要求 Ts 时额定功耗 P ≥ 1 W（表 75）', True, True)),
        )),
        constraint_hint='表 75 分档：<0.7、0.7～1、1～5、5～20、20～50、50～200、≥200 W',
    ),
    choice('application', '应用', headers('73')),
    stress('voltage_ratio', '电压应力比 S2 = VCE/VCEO', values=(.25, .3, .4, .5, .6, .7, .8, .9, 1), source='表 74'),
    choice('polarity', '种类', headers('77')),
)
MODELS['国产双极型晶体管'] = ModelSpec(
    _BJT_FIELDS, 'λp = λbπEπQπAπS2πrπCπK', '5.3.5、5.3.10，式（11）、（19）～（24），表 68～77',
    category='Q', section='5.3.5', calculator=_bjt, quality_tables=('46',),
    example={**TEMP_EXAMPLE, **_load_example('power'), 'kind': '大功率晶体管', 'quality': 'B2',
             'application': '线性放大', 'voltage_ratio': .3, 'polarity': 'NPN 型'},
)


def _dual(fet, p, environment, ambient):
    f = Factors()
    pt = _fraction(p, 'package_rated_power_W')
    ps = [_fraction(p, f'rated_power_{i}_W') for i in (1, 2)]
    powers = [_fraction(p, f'operating_power_{i}_W') for i in (1, 2)]
    if not max(ps) <= pt <= sum(ps) or sum(powers) > pt:
        raise ValueError('双管额定功耗须在最大单管额定值与两单管额定值之和之间，使用总功耗不得超过双管额定功耗')
    adjustments, rates = [], []
    for i in range(2):
        if powers[i] <= 0 or powers[i] > ps[i] or 2 * ps[i] < pt:
            raise ValueError('式（24）需有效的单管额定值和正使用功耗，且 2PS ≥ PT')
        raw = powers[i] / ps[i] + powers[1-i] * (2 * ps[i] - pt) / (pt * ps[i])
        component = Factors()
        base = '78' if fet else {'普通晶体管': '68', '开关晶体管': '69'}[p['kind']]
        adjustment = _base(component, p, ambient, base, raw_stress=raw)
        adjustments.append(replace(adjustment, key=f'stress_ratio_{i+1}',
                                   label=f'第 {i+1} 管校正电应力比 S{i+1}'))
        component.env('79' if fet else '71', environment)
        _quality(component, '80' if fet else '72', p)
        component.add('πA', *below_value('81' if fet else '73', p['application']))
        if not fet:
            component.add('πS2', *below_value('74', p[f'voltage_ratio_{i+1}']))
        component.add('πr', *bands('82' if fet else '75', float(ps[i]), 'P'))
        component.add('πC', *below_value('83' if fet else '76', p['structure']))
        component.add('πK', *below_value('84' if fet else '77', p['technology' if fet else 'polarity']))
        rates.append(component.result().rate)
        f.values += [(f'第 {i+1} 管 {symbol}', value, source) for symbol, value, source in component.values]
    return replace(f.result(sum(rates)), stress_adjustments=tuple(adjustments))


for fet in (False, True):
    _dual_presets = (0.5, 1, 2, 5, 10, 20, 50)
    fields = (quality(quality_choices('80' if fet else '72')), *TEMP_FIELDS,
              Input('package_rated_power_W', '两管同时工作额定功耗 PT（W）', '额定条件',
                    presets=_dual_presets,
                    constraint_hint='式（24）须满足 max(PS1, PS2) ≤ PT ≤ PS1 + PS2 且 2PSi ≥ PT'))
    for i in (1, 2):
        fields += (Input(f'operating_power_{i}_W', f'第 {i} 管使用功耗 P{i}（W）', '工作应力'),
                   Input(f'rated_power_{i}_W', f'第 {i} 管单独工作额定功耗 PS{i}（W）', '额定条件',
                         presets=_dual_presets,
                         constraint_hint='式（24）须满足 2PSi ≥ PT 且 Pi ≤ PSi'))
        if not fet:
            fields += (stress(f'voltage_ratio_{i}', f'第 {i} 管电压应力比 S2',
                              values=(.25, .3, .4, .5, .6, .7, .8, .9, 1), source='表 74'),)
    fields += (choice('application', '应用', headers('81' if fet else '73')),
               choice('structure', '双管结构', ('双管 (不匹配或补偿)', '双管 (匹配)') if fet
                      else ('双管 (不匹配对或补偿)', '双管 (匹配对)')),
               choice('technology' if fet else 'polarity', '种类', headers('84' if fet else '77')))
    if not fet:
        fields += (choice('kind', '晶体管类别', ('普通晶体管', '开关晶体管')),)
    example = {field.key: field.choices[0] for field in fields if field.choices}
    example.update(TEMP_EXAMPLE, quality='B2', package_rated_power_W=2,
                   operating_power_1_W=.1, operating_power_2_W=.1, rated_power_1_W=1, rated_power_2_W=1)
    MODELS['国产硅场效应双管' if fet else '国产双极型双管'] = ModelSpec(
        fields, 'Sᵢ = [Pᵢ/PSᵢ + Pⱼ(2PSᵢ − PT)/(PT·PSᵢ)]C；λp = λp1 + λp2',
        f'5.3.{6 if fet else 5}、5.3.10，式（24）',
        note='按式（24）分别计算两单管电应力；各单管应用双管结构系数后，按串联可靠性模型累计。',
        category='Q', section='5.3.6' if fet else '5.3.5', calculator=partial(_dual, fet),
        quality_tables=('46',), example=example,
    )


def _thyristor(p, environment, ambient):
    f = Factors()
    adjustment = _base(f, p, ambient, '103', 'current')
    f.env('104', environment)
    _quality(f, '105', p)
    f.add('πr', *bands('106', p['rated_current_A'], 'I'))
    f.add('πS2', *bands('107', p['voltage_ratio'], 'S2'))
    return replace(f.result(), stress_adjustments=(adjustment,))


MODELS['国产闸流晶体管'] = ModelSpec(
    (quality(quality_choices('105')), *TEMP_FIELDS,
     *_load_fields('current', min_value=0.1, min_inclusive=False,
                   presets=(0.5, 1, 5, 20, 50, 100, 200),
                   constraint_hint='表 106 要求额定平均通态电流 I > 0.1 A'),
     stress('voltage_ratio', '阻塞电压应力比 S2', values=(.3, .4, .5, .6, .7, .8, .9, 1), source='表 107')),
    'λp = λbπEπQπrπS2', '5.3.9、5.3.10，式（18）、（25），表 103～107',
    category='Q', section='5.3.9', calculator=_thyristor, quality_tables=('46',),
    example={**TEMP_EXAMPLE, **_load_example('current'), 'quality': 'B2', 'voltage_ratio': .3},
)


def _rf_bjt(p, environment, ambient):
    f = Factors()
    f.add('λb', *matrix_value('85', p['frequency_GHz'], p['output_power_W'], label_column=0))
    f.env('86', environment)
    _quality(f, '87', p)
    f.add('πA', *grouped('88', p['application']))
    correction = temperature_correction(p, ambient)
    if correction.coefficient != 1:
        raise ValueError('5.3.10 在 C≠1 时的温度校正适用性未明确，暂不支持计算')
    table = '89' if p['metallization'] == '金金属化' else '90'
    f.add('πT', *temp_matrix_value(table, correction.temperature,
                                 '≤ 0.40' if p['voltage_ratio'] <= .4 else p['voltage_ratio'], label_column=0))
    f.add('πM', *below_value('91', p['matching']))
    f.add('πB', *below_value('92', p['ballast']))
    return f.result()


MODELS['国产微波双极型晶体管'] = ModelSpec(
    (quality(quality_choices('87')), *TEMP_FIELDS,
     choice('frequency_GHz', '工作频率 f（GHz）', (.5, *range(1, 11))),
     choice('output_power_W', '输出功率 P（W）', headers('85', row=1)),
     choice('application', '应用状态及占空系数', rows('88', columns=(0, 1))),
     choice('metallization', '金属化工艺', ('金金属化', '铝金属化')),
     stress('voltage_ratio', '电压应力比 VS = VCE/BVCES', values=(.4, .45, .5, .55), source='表 89、90'),
     choice('matching', '匹配形式', headers('91')), choice('ballast', '发射极镇流程度', headers('92'))),
    'λp = λbπEπQπAπTπMπB', '5.3.7.1、5.3.10，式（13）～（15），表 85～92',
    note='C=1 时按实际环境或带散热片管壳温度查表 89、90；C≠1 时暂不支持自动计算。',
    category='Q', section='5.3.7.1', calculator=_rf_bjt, quality_tables=('46',),
    example={**TEMP_EXAMPLE, 'quality': 'B2', 'frequency_GHz': 1, 'output_power_W': '1.0',
             'application': rows('88', columns=(0, 1))[0], 'metallization': '金金属化',
             'voltage_ratio': .4, 'matching': '输入、输出匹配', 'ballast': '无镇流'},
)


def _rf_fet(p, environment, ambient):
    f = Factors()
    gan = p['material'] == 'GaN'
    if gan:
        f.add('λb', .0178, '5.3.7.2：GaN 微波场效应晶体管基本失效率取 0.0178')
    else:
        f.add('λb', *matrix_value('93', p['frequency_GHz'], p['output_power_W'], label_column=0))
    f.env('94', environment)
    _quality(f, '95', p)
    f.add('πA', *matrix_value('96', p['material'] + ' 微波场效应晶体管', p['application'], label_column=0))
    t = p['channel_temperature_C']
    if t < -273.15:
        raise ValueError('沟道温度不得低于绝对零度')
    f.add('πT', *temp_paired_value('98' if gan else '97', t))
    f.add('πM', *below_value('99', p['matching']))
    return f.result()


MODELS['国产微波场效应晶体管'] = ModelSpec(
    (quality(quality_choices('95')), choice('material', '材料', ('GaAs', 'GaN')),
     choice('frequency_GHz', '工作频率 f（GHz）', (1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 18), when=('material', ('GaAs',))),
     choice('output_power_W', '平均输出功率 P（W）', headers('93', row=1), when=('material', ('GaAs',))),
     Input('channel_temperature_C', '沟道温度 T（℃）', '工作应力', positive=False, allow_negative=True,
           min_value=-273.15, max_value=200,
           choices_by=('material', (
               ('GaAs', (30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95,
                         100, 105, 110, 120, 125, 135, 145, 155, 165, 175)),
               ('GaN', tuple(range(65, 201, 5))),
           )),
           limits_by=('material', (
               ('GaAs', (-273.15, 175, '表 97 GaAs 沟道温度上限 175 ℃（≤30 ℃ 按 30 ℃ 档，其余向上取档）')),
               ('GaN', (-273.15, 200, '表 98 GaN 沟道温度上限 200 ℃（≤65 ℃ 按 65 ℃ 档，其余向上取档）')),
           ))),
     choice('application', '应用', ('低功耗或脉冲', '连续')),
     choice('matching', '匹配形式', headers('99'))),
    'λp = λbπEπQπAπTπM', '5.3.7.2，式（16），表 93～99',
    category='Q', section='5.3.7.2', calculator=_rf_fet, quality_tables=('46',),
    example={'quality': 'B2', 'material': 'GaAs', 'frequency_GHz': 1, 'output_power_W': '0.1',
             'channel_temperature_C': 40, 'application': '连续', 'matching': '输入、输出匹配'},
)

# Derived stress is exposed to trace/redaction, without a duplicate editor field.
for _name, _tables in {
    '国产普通二极管': ('47', '48', '49', '50'),
    '国产电压调整、电压基准及电流调整二极管': ('56',),
    '国产微波硅检波二极管': ('60',), '国产微波硅混频二极管': ('61',),
    '国产单结晶体管': ('100',), '国产其他微波二极管': ('64',),
    '国产双极型晶体管': ('68', '69', '70'), '国产闸流晶体管': ('103',),
    '国产双极型双管': ('68', '69'), '国产硅场效应双管': ('78',),
}.items():
    _sources = tuple(f'5.3.10，表 {table}' for table in _tables)
    _keys = ('stress_ratio_1', 'stress_ratio_2') if '双管' in _name else ('stress_ratio',)
    MODELS[_name] = replace(MODELS[_name], derived_stress=tuple(
        stress(key, f'第 {index} 管校正电应力比 S{index}' if len(_keys) == 2 else '校正电应力比 S',
               source=_sources) for index, key in enumerate(_keys, 1)))
