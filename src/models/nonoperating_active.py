"""Appendix B.2.2–B.2.6: nonoperating microcircuits and active devices."""

from functools import partial

from ..model_spec import Input, choice, temperature
from ..reference_data import (
    ceil_paired_value,
    ceil_row_value,
    fragments,
    result,
    stated_base_rate,
    temp_paired_value,
    temp_row_value,
)
from ..standard_lookups import package_lead_choices_by
from ._active_tables import int_or_float_options, numeric_axes, paired_axes
from .common import Factors
from .nonoperating_common import (
    cell_source,
    compact,
    cycle_field,
    cycle_value,
    header_column,
    header_options,
    horizontal_axis,
    matrix,
    number,
    path,
    quality_field,
    quality_options,
    quality_value,
    row_lookup,
    row_options,
    scalar,
    spec,
)

MODELS = {}
PACKAGES = header_options(9, (0, 1))
_LEAD_CHOICES_BY_B9 = package_lead_choices_by('B.9', PACKAGES)
MMIC_PACKAGES = header_options(5, (1, 2), 2)
MATURITY = header_options(10)
IC_FORMULA = 'λNP = λNb × πNE × πNQ × (πNT + πNF) × πNL × πCYC'


def _ic_fields(family):
    fields = []
    if family != '模拟':
        fields.append(choice('technology', '制造工艺', ('双极', 'MOS')))
    base, key, label = {
        '数字': (1, 'gate_count', '门数 NG'),
        '微处理器': (2, 'transistor_count', '晶体管数 NT'),
        '模拟': (3, 'transistor_count', '晶体管数 NT'),
        '存储器': (4, 'bit_count', '位数 NB'),
    }[family]
    fields.extend([
        choice(key, label, int_or_float_options(paired_axes(f'B.{base}')),
               min_value=1, integer=(family != '数字'), ceil_choices=True),
        choice('package', '封装形式', PACKAGES),
        choice('lead_count', '引出端数 NP', int_or_float_options(numeric_axes('B.9', first_row=2)),
               min_value=1, integer=True, ceil_choices=True, choices_by=_LEAD_CHOICES_BY_B9),
        quality_field(7),
        choice('maturity', '产品成熟程度', MATURITY, '质量与工艺'),
        cycle_field(11),
    ])
    return tuple(fields)


def _ic(p, environment, ambient, *, family):
    f = Factors()
    base, key, temp_column, axis_label = {
        '数字': (1, 'gate_count', 1 if p.get('technology') == '双极' else 2, '门数'),
        '微处理器': (2, 'transistor_count', 4 if p.get('technology') == '双极' else 5, '晶体管数'),
        '模拟': (3, 'transistor_count', 3, '晶体管数'),
        '存储器': (4, 'bit_count', 6 if p.get('technology') == '双极' else 7, '位数'),
    }[family]
    b = f.add('λNb', *ceil_paired_value(number(base), p[key], axis_label=axis_label))
    package_columns = header_column(fragments('B.9')[0], p['package'], (0, 1))
    if len(package_columns) != 1:
        raise ValueError('表 B.9：封装形式须对应唯一表列')
    package_column = package_columns[0]
    e = f.add('πNE', *matrix(6, '密封' if package_column <= 5 else '非密封', environment))
    q = f.add('πNQ', *quality_value(7, p['quality']))
    t = f.add('πNT', *temp_row_value('B.8', ambient, temp_column, label_column=0))
    package = f.add('πNF', *ceil_row_value('B.9', p['lead_count'], package_column,
                                           label_column=0, first_row=2, axis_label='引出端数'))
    maturity = f.add('πNL', *scalar(10, p['maturity']))
    cycle = f.add('πCYC', *cycle_value(11, p['cycle_rate'], 3 if family == '模拟' else 2))
    return f.result(b * e * q * (t + package) * maturity * cycle)


for family, title, key, count in (
    ('数字', '数字集成电路', 'gate_count', 1000),
    ('微处理器', '微处理器', 'transistor_count', 1000),
    ('模拟', '模拟集成电路', 'transistor_count', 100),
    ('存储器', '存储器', 'bit_count', 1024),
):
    example = {key: count, 'package': PACKAGES[0], 'lead_count': 16, 'quality': 'B2',
               'maturity': MATURITY[0], 'cycle_rate': 1}
    if family != '模拟':
        example['technology'] = 'MOS'
    MODELS[f'国产{title}（非工作详细）'] = spec(
        (*_ic_fields(family), temperature()), IC_FORMULA, 'B.2.2.2', range(1, 12),
        partial(_ic, family=family), example, category='IC', qualities=(7,),
        note='数字类包含以门数表示复杂度的可编程逻辑器件；存储器按位数计。')


def _mmic(p, environment, ambient):
    f = Factors()
    table = fragments('B.5')[0]
    columns = header_column(table, p['package'], (1, 2), 2)
    if len(columns) != 1:
        raise ValueError('表 B.5：微波单片封装形式须对应唯一表列')
    column = columns[0]
    b = f.add('λNb', *row_lookup(5, p['kind'], column, (0, 1)))
    e = f.add('πNE', *matrix(6, '非密封' if column == 6 else '密封', environment))
    q = f.add('πNQ', *quality_value(7, p['quality']))
    t = f.add('πNT', *temp_row_value('B.8', ambient, 8, label_column=0))
    package = f.add('πNF', 0, '第 B.2.2.2 节明确：微波单片集成电路 πNF=0')
    maturity = f.add('πNL', *scalar(10, p['maturity']))
    cycle = f.add('πCYC', *cycle_value(11, p['cycle_rate'], 3))
    return f.result(b * e * q * (t + package) * maturity * cycle)


MMIC_KINDS = row_options(5, (0, 1))
MODELS['国产微波单片集成电路（非工作详细）'] = spec(
    (choice('kind', '晶体管结构', MMIC_KINDS), choice('package', '封装形式', MMIC_PACKAGES),
     quality_field(7), choice('maturity', '产品成熟程度', MATURITY), cycle_field(11), temperature()),
    IC_FORMULA, 'B.2.2.2', (5, 6, 7, 8, 10, 11), _mmic,
    {'kind': MMIC_KINDS[0], 'package': MMIC_PACKAGES[0], 'quality': 'B2',
     'maturity': MATURITY[0], 'cycle_rate': 1}, category='IC', qualities=(7,))


def _mixed(p, environment, ambient):
    f = Factors()
    values = []
    for key, family in (('analog_sections', '模拟'), ('digital_sections', '数字')):
        for index, parameters in enumerate(p[key], 1):
            evaluated = _ic(parameters, environment, ambient, family=family)
            values.append(f.add(f'λNP[{family}{index}]', evaluated.rate,
                                '第 B.2.2.2 节：组成模拟电路与数字电路非工作失效率求和'))
            f.values.extend((f'{symbol}[{family}{index}]', value, source)
                            for symbol, value, source in evaluated.factors)
    return f.result(sum(values))


MODELS['国产模数混合集成电路（非工作详细）'] = spec(
    (Input('analog_sections', '组成模拟电路', record_fields=_ic_fields('模拟')),
     Input('digital_sections', '组成数字电路', record_fields=_ic_fields('数字')), temperature()),
    'λNP = ΣλNP[模拟] + ΣλNP[数字]', 'B.2.2.2', range(1, 12), _mixed,
    {'analog_sections': [dict(MODELS['国产模拟集成电路（非工作详细）'].example)],
     'digital_sections': [dict(MODELS['国产数字集成电路（非工作详细）'].example)]}, category='IC', qualities=(7,),
    note='每行表示组成电路；分别按式 B.1 计算后求和，不再乘一次整件系数。')


def _hybrid(p, environment, _ambient):
    f = Factors()
    counts = [p[key] for key in ('ic_count', 'transistor_count', 'diode_count', 'passive_count')]
    if not any(counts):
        raise ValueError('混合集成电路至少须包含一个组成元器件')
    complexity = 7 * counts[0] + 1.5 * counts[1] + counts[2] + counts[3]
    table = fragments('B.12')[0]
    row = 1 if complexity <= 31 else 2
    a = f.add('A', *result('B.12', [(table, table['grid'][row, 1])]))
    subtotal = 0
    for column, (symbol, count) in enumerate(zip(('IC', 'T', 'D', 'N'), counts, strict=True), 2):
        f.add('N' + symbol, count, '组成元器件数量；式 B.3')
        typical = f.add('λN' + symbol, *result('B.12', [(table, table['grid'][row, column])]))
        subtotal += count * typical
    base = f.add('λNb', a * subtotal, '式 B.3；复杂度 7NIC+1.5NT+ND+NN=' + str(complexity))
    e = f.add('πNE', *scalar(13, environment))
    q = f.add('πNQ', *quality_value(14, p['quality']))
    cycle = f.add('πCYC', *cycle_value(15, p['cycle_rate']))
    return f.result(base * e * q * cycle)


MODELS['国产混合集成电路（非工作详细）'] = spec(
    tuple(Input(key, label, '组成元器件', positive=False, integer=True, default=0)
          for key, label in (('ic_count', '单片集成电路数 NIC'), ('transistor_count', '晶体管数 NT'),
                             ('diode_count', '二极管数 ND'), ('passive_count', '无源元件数 NN')))
    + (quality_field(14), cycle_field(15)),
    'λNP = A × (NIC × λNIC + NT × λNT + ND × λND + NN × λNN) × πNE × πNQ × πCYC',
    'B.2.2.3', (12, 13, 14, 15), _hybrid,
    {'ic_count': 2, 'transistor_count': 3, 'diode_count': 4, 'passive_count': 8,
     'quality': 'B2', 'cycle_rate': 1}, category='IC', qualities=(14,))


def _saw(p, environment, _ambient):
    f = Factors()
    f.add('λNb', *stated_base_rate('B.2.3'))
    f.add('πNE', *scalar(16, environment))
    f.add('πNQ', *quality_value(17, p['quality']))
    return f.result()


MODELS['国产声表面波器件（非工作详细）'] = spec(
    (quality_field(17),), 'λNP = λNb × πNE × πNQ', 'B.2.3', (16, 17), _saw,
    {'quality': 'B2'}, category='SAW', qualities=(17,))


DIODES = row_options(19, (0, 1))
DIODE_FAMILY = {kind: (1 if kind.startswith('普通二极管') else 2 if kind.startswith('电压调整')
                      else 3 if '微波硅' in kind else 4) for kind in DIODES}


def _diode(p, environment, ambient):
    f = Factors()
    family = DIODE_FAMILY[p['kind']]
    temp = p['case_temperature_C'] if p['temperature_basis'] == '带散热片功率器件的管壳温度' else ambient
    f.add('λNb', *row_lookup(19, p['kind'], columns=(0, 1)))
    f.add('πNE', *scalar(20, environment))
    f.add('πNQ', *quality_value(21, p['quality'], family + 1))
    f.add('πNT', *temp_row_value('B.22', temp, family, label_column=0))
    f.add('πCYC', *cycle_value(23, p['cycle_rate']))
    return f.result()


_DIODE_QUALITY_BY_KIND = tuple(
    (kind, quality_options(21, DIODE_FAMILY[kind] + 1)) for kind in DIODES
)
MODELS['国产二极管（非工作详细）'] = spec(
    (choice('kind', '器件种类', DIODES),
     quality_field(21, 2, choices_by=('kind', _DIODE_QUALITY_BY_KIND),
                   constraint_hint='表 B.21 微波硅检波/混频二极管无 A2 质量等级'),
     cycle_field(23), temperature(),
     choice('temperature_basis', '温度取值', ('非工作环境温度', '带散热片功率器件的管壳温度')),
     Input('case_temperature_C', '管壳温度 T [℃]', allow_negative=True,
           when=('temperature_basis', ('带散热片功率器件的管壳温度',)))),
    'λNP = λNb × πNE × πNQ × πNT × πCYC', 'B.2.4.2', (19, 20, 21, 22, 23), _diode,
    {'kind': DIODES[0], 'quality': 'B2', 'cycle_rate': 1, 'temperature_basis': '非工作环境温度'},
    category='D', qualities=(21,), note='表 B.22 允许带散热片功率器件用管壳温度；不附加工作态温度校正。')


TRANSISTORS = row_options(24, (0, 1))
TRANSISTOR_FAMILY = {kind: (1 if kind.startswith('双极型') else 2 if kind == '硅场效应晶体管'
                          else 3 if kind == '单结晶体管' else 4 if kind.startswith('微波') else 5)
                     for kind in TRANSISTORS}


def _transistor(p, environment, ambient):
    f = Factors()
    family = TRANSISTOR_FAMILY[p['kind']]
    f.add('λNb', *row_lookup(24, p['kind'], columns=(0, 1)))
    f.add('πNE', *scalar(25, environment))
    f.add('πNQ', *quality_value(26, p['quality'], family + 1))
    f.add('πNT', *temp_row_value('B.27', ambient, family, label_column=0))
    if family == 3:
        f.add('πNr', 1, '第 B.2.4.3 节明确：单结晶体管 πNr=1')
    else:
        f.add('πNr', *horizontal_axis(30 if family == 5 else 29 if family == 4 else 28,
                                      p['rated_current_A'] if family == 5 else p['rated_power_W'],
                                      'I' if family == 5 else 'P'))
    f.add('πCYC', *cycle_value(31, p['cycle_rate']))
    return f.result()


_TRANSISTOR_QUALITY_BY_KIND = tuple(
    (kind, quality_options(26, TRANSISTOR_FAMILY[kind] + 1)) for kind in TRANSISTORS
)
MODELS['国产晶体管（非工作详细）'] = spec(
    (choice('kind', '器件种类', TRANSISTORS),
     quality_field(26, 2, choices_by=('kind', _TRANSISTOR_QUALITY_BY_KIND),
                   constraint_hint='表 B.26 按晶体管种类限定可选质量等级'),
     cycle_field(31), temperature(),
     Input('rated_power_W', '额定功率 P [W]', when=('kind', tuple(k for k in TRANSISTORS if TRANSISTOR_FAMILY[k] in (1, 2, 4)))),
     Input('rated_current_A', '额定平均通态电流 I [A]', when=('kind', ('闸流晶体管',)))),
    'λNP = λNb × πNE × πNQ × πNT × πNr × πCYC', 'B.2.4.3', range(24, 32), _transistor,
    {'kind': TRANSISTORS[0], 'quality': 'B2', 'cycle_rate': 1, 'rated_power_W': 1},
    category='Q', qualities=(26,))


OPTO_KINDS = row_options(32, (1, 2), expressions=True)


def _opto(p, environment, ambient):
    f = Factors()
    if p['kind'] == '多位数码管':
        table = fragments('B.32')[0]
        cell = next(table['grid'][row, 3] for row in range(table['rows'])
                    if compact(path(table, row, (1, 2))) == '多位数码管')
        # The base is an affine formula printed in the source cell, not a lookup number.
        import re
        match = re.fullmatch(r'([\d.]+)\+([\d.]+)×位数', compact(cell['text']))
        if not match:
            raise ValueError('表 B.32 多位数码管公式无法唯一识别')
        base = float(match[1]) + float(match[2]) * p['digit_count']
        f.add('λNb', base, cell_source(table, cell))
    else:
        f.add('λNb', *row_lookup(32, p['kind'], columns=(1, 2)))
    f.add('πNE', *scalar(33, environment))
    f.add('πNQ', *quality_value(34, p['quality']))
    f.add('πNT', *temp_paired_value('B.35', ambient))
    return f.result()


MODELS['国产光电子器件（非工作详细）'] = spec(
    (choice('kind', '器件种类', OPTO_KINDS), quality_field(34), temperature(),
     Input('digit_count', '数码位数', integer=True, when=('kind', ('多位数码管',)))),
    'λNP = λNb × πNE × πNQ × πNT', 'B.2.5', (32, 33, 34, 35), _opto,
    {'kind': OPTO_KINDS[0], 'quality': 'B2'}, category='LED', qualities=(34,))


def _vacuum(p, environment, _ambient):
    f = Factors()
    f.add('λNb', *row_lookup(36, p['kind'], columns=(0, 1)))
    f.add('πNE', *scalar(37, environment))
    f.add('πNQ', *quality_value(38, p['quality']))
    return f.result()


VACUUM_KINDS = row_options(36, (0, 1))
MODELS['国产真空电子器件（非工作详细）'] = spec(
    (choice('kind', '器件种类', VACUUM_KINDS), quality_field(38)),
    'λNP = λNb × πNE × πNQ', 'B.2.6', (36, 37, 38), _vacuum,
    {'kind': VACUUM_KINDS[0], 'quality': 'B'}, category='V', qualities=(38,))
