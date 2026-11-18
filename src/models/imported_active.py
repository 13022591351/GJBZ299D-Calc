"""Remaining imported active-device models, appendix C.2.2–C.2.6."""

from dataclasses import replace
from functools import partial

from ..model_spec import Input, ModelSpec, choice, quality, stress, temperature
from ..reference_data import (
    below_value,
    ceil_row_value,
    compact,
    equal,
    fragments,
    matrix_value,
    row_value,
    temp_matrix_value,
    temp_paired_value,
)
from ..standard_lookups import column_numeric_ticks, package_lead_choices_by
from ._active_tables import (
    grouped,
    headers,
    int_or_float_options,
    numeric_axes,
    paired_axes,
    rows,
    temp_triplet,
)
from .common import Factors

MODELS = {}
_package_grid = fragments('C.13')[0]['grid']
PACKAGES = tuple(' / '.join(_package_grid[row, col]['text'] for row in (0, 1))
                 for col in range(1, 10))
_LEAD_CHOICES_BY_C13 = package_lead_choices_by('C.13', PACKAGES)
IC_QUALITY = rows('C.15')
JUNCTION_TEMPERATURES = tuple(range(25, 106, 2)) + tuple(range(110, 176, 5))


def _package(p):
    return ceil_row_value('C.13', p['lead_count'], PACKAGES.index(p['package']) + 1,
                          label_column=0, first_row=2, axis_label='引出端数')


def _ic_fields(base):
    has_tech_c1 = base in ('C.1', 'C.8')
    all_ticks = int_or_float_options(numeric_axes(base))
    comp_kwargs = {
        'min_value': 1,
        'integer': base != 'C.1',
        'ceil_choices': True,
    }
    if has_tech_c1:
        bjt_ticks = column_numeric_ticks(base, 1, label_column=0, first_row=1)
        mos_ticks = column_numeric_ticks(base, 2, label_column=0, first_row=1)
        comp_kwargs['choices_by'] = ('technology', (('双极型', bjt_ticks), ('MOS', mos_ticks)))
        comp_kwargs['limits_by'] = ('technology', (
            ('双极型', (1, bjt_ticks[-1], f'表 {base} 双极型上限 {bjt_ticks[-1]}')),
            ('MOS', (1, mos_ticks[-1], f'表 {base} MOS 上限 {mos_ticks[-1]}')),
        ))
    return ((choice('technology', '电路工艺', ('双极型', 'MOS')),) if base != 'C.2' else ()) + (
        choice('complexity', '门数 NG' if base == 'C.1' else '位数 NB（bit）', all_ticks, **comp_kwargs),
        choice('package', '封装形式', PACKAGES),
        choice('lead_count', '引出端数 NP', int_or_float_options(numeric_axes('C.13', first_row=2)),
               min_value=1, integer=True, ceil_choices=True, choices_by=_LEAD_CHOICES_BY_C13),
        quality(IC_QUALITY),
        choice('junction_temperature_C', '结温 Tj（℃）', JUNCTION_TEMPERATURES, '工作应力'),
    )


def _example(fields):
    p = {field.key: field.choices[0] if field.choices else field.default if field.default is not None else 1
         for field in fields if field.scope != 'project'}
    if 'lead_count' in p:
        p['lead_count'] = 4
    return p


def _ic(base, temp, tech, p, environment, ambient):
    f = Factors()
    column = 1 if not tech or p['technology'] == '双极型' else 2
    axis_label = '门数' if base == 'C.1' else '晶体管数' if base == 'C.2' else '位数'
    c1 = f.add('C1', *ceil_row_value(base, p['complexity'], column, label_column=0, axis_label=axis_label))
    c2 = f.add('C2', *_package(p))
    if temp == 'C.10':
        t = f.add('πT', *temp_paired_value(temp, p['junction_temperature_C']))
    else:
        t = f.add('πT', *temp_triplet(temp, p['junction_temperature_C'],
                                     1 if p['technology'] == '双极型' else 2))
    e = f.env('C.14', environment)
    q = f.add('πQ', *row_value('C.15', p['quality']))
    return f.result((c1 * t + c2 * e) * q)


for name, base, temp, tech in (
    ('进口通用数字集成电路', 'C.1', 'C.9', True),
    ('进口动态随机存储器（DRAM）', 'C.6', 'C.12', False),
    ('进口先进先出存储器（FIFO）', 'C.7', 'C.12', False),
    ('进口ROM、PROM、UVEPROM、EEPROM、FLASH', 'C.8', 'C.12', True),
):
    fields = _ic_fields(base)
    MODELS[name] = ModelSpec(
        fields, 'λp = (C1πT + C2πE)πQ', f'C.2.2.2，式（C.1），表 {base}、{temp}、C.13～C.15',
        category='IC', section='C.2.2.2', calculator=partial(_ic, base, temp, tech),
        quality_tables=('C.15',), example=_example(fields),
    )


def _mixed(p, environment, ambient):
    analog = _ic('C.2', 'C.10', False,
                 {key[7:]: value for key, value in p.items() if key.startswith('analog_')}, environment, ambient)
    digital = _ic('C.1', 'C.9', True,
                  {key[8:]: value for key, value in p.items() if key.startswith('digital_')}, environment, ambient)
    f = Factors()
    f.values = [('模拟部分 ' + key, value, source) for key, value, source in analog.factors]
    f.values += [('数字部分 ' + key, value, source) for key, value, source in digital.factors]
    return f.result(analog.rate + digital.rate)


_mixed_fields = ()
_mixed_example = {}
for prefix, fields in (('analog', _ic_fields('C.2')), ('digital', _ic_fields('C.1'))):
    _local_keys = {f.key for f in fields}
    for field in fields:
        label = '晶体管数 NT' if prefix == 'analog' and field.key == 'complexity' else field.label
        _mixed_fields += (
            replace(
                field,
                key=f'{prefix}_{field.key}',
                label=label,
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
            ),
        )
    _mixed_example.update({prefix + '_' + key: value for key, value in _example(fields).items()})
MODELS['进口模数混合集成电路'] = ModelSpec(
    _mixed_fields, 'λp = λp[模拟部分] + λp[数字部分]', 'C.2.2.2，式（C.1）',
    category='IC', section='C.2.2.2', calculator=_mixed, quality_tables=('C.15',), example=_mixed_example,
)


def _mmic(p, environment, ambient):
    f = Factors()
    c1 = f.add('C1', *row_value('C.16', p['transistor_band']))
    t = f.add('πT', *temp_paired_value('C.17', p['channel_temperature_C']))
    process = f.add('πP', *below_value('C.18', p['process']))
    a = f.add('πA', *row_value('C.19', p['application']))
    c2 = f.add('C2', *_package(p))
    e = f.env('C.14', environment)
    q = f.add('πQ', *row_value('C.15', p['quality']))
    return f.result((c1 * t * process * a + c2 * e) * q)


_mmic_fields = (
    choice('transistor_band', '晶体管数 NT', rows('C.16')),
    choice('channel_temperature_C', '沟道温度 Tch（℃）', int_or_float_options(paired_axes('C.17')), '工作应力'),
    choice('process', '晶体管结构与工艺', headers('C.18', row=1)),
    choice('application', '应用状态', rows('C.19')),
    choice('package', '封装形式', PACKAGES),
    choice('lead_count', '引出端数 NP', int_or_float_options(numeric_axes('C.13', first_row=2)),
           min_value=1, integer=True, ceil_choices=True, choices_by=_LEAD_CHOICES_BY_C13),
    quality(IC_QUALITY),
)
MODELS['进口微波单片集成电路'] = ModelSpec(
    _mmic_fields, 'λp = (C1πTπPπA + C2πE)πQ', 'C.2.2.2，式（C.2），表 C.13～C.19',
    category='IC', section='C.2.2.2', calculator=_mmic, quality_tables=('C.15',), example=_example(_mmic_fields),
)

EXTERNAL_TYPES = rows('C.25')


def _hybrid(p, environment, ambient):
    f = Factors()
    network = f.add('λC', *below_value('C.24', p['network_process']))
    resistor = f.add('λRT', .00015, 'C.2.2.3：单个膜电阻器失效率取值 0.00015')
    assembly = f.add('λSF', *below_value('C.26', p['package_process']))
    external = p['mounted_ic_rate']
    f.add('ΣNDCλDC[外贴微电路]', external, '表 C.25；Tc=35 ℃、πE=1 的已预计外贴微电路合计')
    for index, kind in enumerate(EXTERNAL_TYPES):
        count = p[f'external_count_{index}']
        if count:
            value, source = row_value('C.25', kind)
            external += count * value
            f.add(f'NDCλDC[{kind}]', count * value, f'{source}；数量 {count:g}')
    base = p['substrate_area_cm2'] * network + p['film_resistor_count'] * resistor + external + assembly
    f.add('λb', base, '式（C.4）')
    e = f.env('C.20', environment)
    q = f.add('πQ', *row_value('C.21', p['quality']))
    t = f.add('πT', *temp_triplet('C.22', p['hybrid_temperature_C'],
                                1 if p['temperature_basis'] == '封装底座温度' else 2))
    function = f.add('πF', *row_value('C.23', p['function']))
    return f.result(base * e * q * t * function)


_hybrid_fields = (
    quality(rows('C.21')), Input('substrate_area_cm2', '衬底面积 AS（cm²）'),
    choice('network_process', '网络工艺', headers('C.24')),
    Input('film_resistor_count', '膜电阻数量 NRT', integer=True, positive=False, default=0),
    choice('package_process', '工艺和封装', headers('C.26')),
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
            ('封装底座温度', (-273.15, 125, '表 C.22 封装底座温度上限 125 ℃（≤35 ℃ 按 35 ℃ 档）')),
            ('封装内最高温度', (-273.15, 175, '表 C.22 封装内最高温度上限 175 ℃（≤35 ℃ 按 35 ℃ 档）')),
        )),
    ),
    choice('function', '电路功能类型', rows('C.23')),
    Input('mounted_ic_rate', '已预计外贴微电路合计 ΣNDCλDC（10⁻⁶/h）', '外贴微电路', positive=False, default=0),
) + tuple(Input(f'external_count_{i}', f'{kind}数量', '外贴元器件', integer=True, positive=False, default=0)
          for i, kind in enumerate(EXTERNAL_TYPES))
MODELS['进口混合集成电路'] = ModelSpec(
    _hybrid_fields, 'λp = (ASλC + λRTNRT + ΣλDCNDC + λSF)πEπQπTπF', 'C.2.2.3，式（C.3）、（C.4），表 C.20～C.26',
    note='外贴微电路按表 C.25 的 Tc=35 ℃、πE=1 单独预计后输入合计；连接器须作为附加元件单独计算。',
    category='IC', section='C.2.2.3', calculator=_hybrid, quality_tables=('C.21',), example=_example(_hybrid_fields),
)


def _saw(p, environment, ambient):
    f = Factors()
    f.add('λb', *row_value('C.27', '声表面波器件(滤波器、振荡器、延迟线)'))
    f.env('C.28', environment)
    f.add('πQ', *row_value('C.29', p['quality']))
    return f.result()


MODELS['进口声表面波器件'] = ModelSpec(
    (quality(rows('C.29')),), 'λp = λbπEπQ', 'C.2.3，式（C.5），表 C.27～C.29',
    category='SAW', section='C.2.3', calculator=_saw, quality_tables=('C.29',),
    example={'quality': rows('C.29')[0]},
)


def _rf_discrete(p, environment, ambient):
    f = Factors()
    f.add('λb', *grouped('C.30', p['kind'], columns=(1, 2)))
    f.env('C.31', environment)
    f.add('πQ', *row_value('C.32', p['quality']))
    label = '微波二极管' if p['kind'].startswith('微波二极管') else '微波晶体管'
    f.add('πT', *temp_matrix_value('C.33', label, ambient, label_column=0, temp_axis='col'))
    f.add('πS', *matrix_value('C.36', label, p['stress_ratio'], label_column=0))
    return f.result()


MODELS['进口微波半导体分立器件'] = ModelSpec(
    (choice('kind', '器件种类', ('微波二极管 / 检波二极管', '微波二极管 / 混频二极管',
                              '微波晶体管 / 双极型晶体管', '微波晶体管 / 场效应晶体管')),
     quality(rows('C.32')), temperature(), stress(values=tuple(n/10 for n in range(1, 10)), source='表 C.36')),
    'λp = λbπEπQπTπS', 'C.2.4，式（C.6），表 C.30～C.33、C.36',
    note='表 C.36 给出无量纲电应力比 S 的系数，须输入供应商确认的电应力定义。',
    category='Q', section='C.2.4', calculator=_rf_discrete, quality_tables=('C.32',),
    example={'kind': '微波二极管 / 检波二极管', 'quality': 'JANTX', 'stress_ratio': .5},
)


def _opto(p, environment, ambient):
    f = Factors()
    kind = p['kind']
    if kind == '多位数码管':
        base = f.add('λb', .0001 + .00027 * p['digit_count'], '表 C.37：λb = 0.0001 + 0.00027 × 位数')
    else:
        base = f.add('λb', *grouped('C.37', kind))
    if '数码管' in kind:
        base *= p['chip_count']
        f.add('芯片数', p['chip_count'], '表 C.37 注：数码管按内含芯片数累计')
    e = f.env('C.38', environment)
    q = f.add('πQ', *row_value('C.39', p['quality']))
    if '耦合器' in kind or '探测器' in kind:
        label = '光电耦合器、光电探测器、光纤探测器模块'
    elif kind == '激光二极管':
        label = '激光二极管'
    elif kind in ('发光二极管', '红外发光二极管', '光纤发光二极管模块'):
        label = '发光二极管、红外发光二极管、光纤发光二极管模块'
    else:
        label = '其他'
    t = f.add('πT', *temp_matrix_value('C.40', ambient, label, label_column=0))
    additional = 0
    if '模块' in kind and p['module_content'] == '含其他电子器件或混合电路':
        if not equal(p['source_environment'], environment) or p['source_temperature_C'] != ambient:
            raise ValueError('内部附加器件失效率的适用环境或温度不匹配')
        additional = p['additional_component_rate']
    if additional:
        f.add('附加器件工作失效率', additional, '表 C.37 注：模块内其他电子器件和混合电路按本工况单独预计后累计')
    return f.result(base * e * q * t + additional)


_OPTO_KINDS = (*rows('C.37', columns=(0, 1)), '多位数码管')
_MODULE_KINDS = ('光纤发光二极管模块', '光纤激光模块', '光纤探测器模块')
_MODULE_EXTRA = (('kind', _MODULE_KINDS), ('module_content', ('含其他电子器件或混合电路',)))
_ENVIRONMENT_CODES = tuple(
    compact(table['grid'][row, col]['text'])
    for table in fragments('C.38') for row in (0, 2) for col in range(1, table['columns'])
    if table['grid'][row + 1, col]['kind'] == 'number')
MODELS['进口光电子器件'] = ModelSpec(
    (choice('kind', '器件种类', _OPTO_KINDS), quality(rows('C.39')), temperature(),
     Input('digit_count', '显示位数', integer=True, when=('kind', ('多位数码管',))),
     Input('chip_count', '内含芯片数', integer=True, when=('kind', ('单位数码管', '多位数码管'))),
     choice('module_content', '模块组成', ('仅光组件', '含其他电子器件或混合电路'), when=('kind', _MODULE_KINDS)),
     Input('additional_component_rate', '其他内部器件工作失效率合计（10⁻⁶/h）', '模块组成',
           positive=False, when_all=_MODULE_EXTRA),
     choice('source_environment', '内部附加器件计算适用环境', _ENVIRONMENT_CODES, when_all=_MODULE_EXTRA),
     Input('source_temperature_C', '内部附加器件计算适用温度（℃）', positive=False, allow_negative=True,
           when_all=_MODULE_EXTRA)),
    'λp = λbπEπQπT；模块另加内部附加器件工作失效率', 'C.2.5，式（C.7），表 C.37～C.40',
    category='LED', section='C.2.5', calculator=_opto, quality_tables=('C.39',),
    example={'kind': '发光二极管', 'quality': 'JANTX'},
)


def _vacuum(p, environment, ambient):
    f = Factors()
    f.add('λb', *grouped('C.41', p['kind']))
    f.env('C.42', environment)
    f.add('πQ', *row_value('C.43', p['quality']))
    return f.result()


MODELS['进口真空电子器件'] = ModelSpec(
    (choice('kind', '器件类别', rows('C.41', columns=(0, 1))), quality(rows('C.43'))),
    'λp = λbπEπQ', 'C.2.6，式（C.8），表 C.41～C.43',
    category='V', section='C.2.6', calculator=_vacuum, quality_tables=('C.43',),
    example={'kind': '速调管 / 连续波', 'quality': rows('C.43')[0]},
)
