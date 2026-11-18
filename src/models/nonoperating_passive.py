"""Appendix B.2.7–B.2.11: nonoperating passive components and networks."""

from functools import partial

from ..model_spec import Input, choice, temperature
from ..reference_data import stated_base_rate
from .common import Factors
from .nonoperating_common import (
    compact,
    cycle_field,
    cycle_value,
    header_options,
    matrix,
    quality_field,
    quality_value,
    row_lookup,
    row_options,
    scalar,
    spec,
    temp_axis_value,
)

MODELS = {}
RESISTOR_KINDS = tuple(kind for kind in row_options(39, (0, 1)) if not kind.startswith('电阻网络'))
RESISTOR_GRADES = tuple(compact(value) for value in header_options(40, (1,)))
NETWORK_RESISTORS = tuple(kind for kind in row_options(39, (0, 1)) if kind.startswith('电阻网络'))


def _resistor_family(kind):
    if kind.startswith(('金属膜', '碳膜', '玻璃釉')):
        return kind, '金属膜、碳膜、 玻璃釉'
    for prefix, label in (('线绕电阻器', '线绕'), ('箔式电阻器', '箔式'),
                          ('功率线绕', '功率线绕'), ('功率非线绕', '功率非线绕'), ('片式膜', '片式膜')):
        if kind.startswith(prefix):
            return label, label
    return '电阻网络', '电阻网络'


SMALL_POWER_RESISTORS = tuple(kind for kind in RESISTOR_KINDS if kind.startswith(('碳膜', '玻璃釉')))
LARGE_POWER_RESISTORS = tuple(kind for kind in RESISTOR_KINDS if kind.startswith(('功率线绕', '功率非线绕')))
LIMITED_RESISTORS = SMALL_POWER_RESISTORS + LARGE_POWER_RESISTORS
_RES_FULL_QUALITY_KINDS = tuple(kind for kind in RESISTOR_KINDS if kind.startswith(('金属膜', '片式膜')))
_RES_MID_QUALITY_KINDS = tuple(
    kind for kind in RESISTOR_KINDS if kind.startswith(('线绕电阻器', '箔式电阻器', '功率线绕', '功率非线绕'))
)


def _resistor(p, environment, ambient, *, network=False):
    kind = p['kind']
    if not network and kind in LIMITED_RESISTORS:
        power = p['rated_power_W']
        if kind.startswith(('碳膜', '玻璃釉')) and power > 2:
            raise ValueError('第 B.2.7 节该电阻类型限定功率 P≤2 W')
        if kind.startswith(('功率线绕', '功率非线绕')) and power <= 2:
            raise ValueError('第 B.2.7 节该功率电阻类型限定功率 P>2 W')
    quality_kind, environment_kind = _resistor_family(kind)
    f = Factors()
    f.add('λNb', *row_lookup(39, kind, columns=(0, 1)))
    f.add('πNQ', *matrix(40, quality_kind, p['quality']))
    f.add('πNE', *matrix(41, environment_kind, environment))
    if network:
        f.add('NE', p['element_count'], '网络电阻数；式 B.10')
        f.add('πNT', *temp_axis_value(42, ambient, paired=True))
    f.add('πCYC', *cycle_value(43, p['cycle_rate']))
    return f.result()


MODELS['国产电阻器（非工作详细）'] = spec(
    (choice('kind', '电阻器种类', RESISTOR_KINDS),
     choice('quality', '质量等级', RESISTOR_GRADES,
            choices_by=('kind', (
                (_RES_FULL_QUALITY_KINDS, ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C')),
                (_RES_MID_QUALITY_KINDS, ('A1M', 'A2', 'B1', 'B2', 'C')),
                (SMALL_POWER_RESISTORS, ('A2', 'B1', 'B2', 'C')),
            )),
            constraint_hint='表 B.40 按电阻器种类限定可选质量等级'),
     Input('rated_power_W', '额定功率 P [W]', when=('kind', LIMITED_RESISTORS),
           min_value=0, min_inclusive=False,
           choices_by=('kind', (
               (SMALL_POWER_RESISTORS, (0.125, 0.25, 0.5, 1, 2)),
               (LARGE_POWER_RESISTORS, (3, 5, 10, 25, 50)),
           )),
           limits_by=('kind', (
               (SMALL_POWER_RESISTORS, (0, 2, '表 B.39 碳膜/玻璃釉电阻器限定功率 0 < P ≤ 2 W', False, True)),
               (LARGE_POWER_RESISTORS, (2, None, '表 B.39 功率线绕/功率非线绕电阻器限定功率 P > 2 W', False, True)),
           )),
           constraint_hint='表 B.39：碳膜/玻璃釉限定 P ≤ 2 W；功率线绕/功率非线绕限定 P > 2 W'),
     cycle_field(43)),
    'λNP = λNb × πNQ × πNE × πCYC', 'B.2.7', (39, 40, 41, 43), _resistor,
    {'kind': RESISTOR_KINDS[0], 'quality': 'B2', 'cycle_rate': 1}, category='R', qualities=(40,))
MODELS['国产电阻网络（非工作详细）'] = spec(
    (choice('kind', '网络电阻种类', NETWORK_RESISTORS),
     Input('element_count', '网络电阻数 NE', integer=True, min_value=1, presets=(2, 4, 8),
           constraint_hint='式 B.10 网络电阻数 NE ≥ 1（正整数）'),
     choice('quality', '质量等级', RESISTOR_GRADES), cycle_field(43), temperature()),
    'λNP = NE × λNb × πNQ × πNE × πNT × πCYC', 'B.2.7', (39, 40, 41, 42, 43),
    partial(_resistor, network=True), {'kind': NETWORK_RESISTORS[0], 'element_count': 4,
                                     'quality': 'B2', 'cycle_rate': 1}, category='R', qualities=(40,))


def _sensitive(p, environment, _ambient):
    f = Factors()
    f.add('λNb', *row_lookup(44, p['kind']))
    f.add('πNQ', *quality_value(45, p['quality']))
    f.add('πNE', *scalar(46, environment))
    f.add('πCYC', *cycle_value(47, p['cycle_rate']))
    return f.result()


MODELS['国产敏感电阻器（非工作详细）'] = spec(
    (choice('kind', '敏感电阻器种类', row_options(44)), quality_field(45), cycle_field(47)),
    'λNP = λNb × πNQ × πNE × πCYC', 'B.2.8', (44, 45, 46, 47), _sensitive,
    {'kind': '热敏', 'quality': 'B2', 'cycle_rate': 1}, category='RT', qualities=(45,))


def _potentiometer(p, environment, _ambient):
    f = Factors()
    kind = '非线绕精密' if p['kind'] == '非线绕精密电位器' else p['kind']
    f.add('λNb', *row_lookup(48, p['kind']))
    f.add('πNQ', *matrix(49, kind, p['quality']))
    f.add('πNE', *matrix(50, kind, environment))
    f.add('πCYC', *cycle_value(51, p['cycle_rate']))
    return f.result()


_POT_KINDS = row_options(48)
_POT_GRADES = tuple(compact(value) for value in header_options(49, (1,)))
_POT_STD_KINDS = tuple(kind for kind in _POT_KINDS if kind != '合成碳膜')
MODELS['国产电位器（非工作详细）'] = spec(
    (choice('kind', '电位器种类', _POT_KINDS),
     choice('quality', '质量等级', _POT_GRADES,
            choices_by=('kind', (
                (_POT_STD_KINDS, _POT_GRADES),
                ('合成碳膜', tuple(g for g in _POT_GRADES if g != 'A1M')),
            )),
            constraint_hint='表 B.49 合成碳膜电位器无 A1M 质量等级'),
     cycle_field(51)),
    'λNP = λNb × πNQ × πNE × πCYC', 'B.2.9', (48, 49, 50, 51), _potentiometer,
    {'kind': '普通线绕', 'quality': 'B2', 'cycle_rate': 1}, category='RP', qualities=(49,),
    note='表 B.50 的 SF、ML、MF 环境无表值。')


PAPER_COMPOSITE = '纸和薄膜电容器 / 纸-膜复合介质'
CAPACITORS = row_options(52, (0, 1)) + (PAPER_COMPOSITE,)
PAPER = next(kind for kind in CAPACITORS if compact(kind).endswith('/纸介'))
FILM = next(kind for kind in CAPACITORS if compact(kind).endswith('/薄膜'))
TANTALUM = ('固体钽电解电容器', '非固体钽电解电容器')
VARIABLE = tuple(kind for kind in CAPACITORS if kind.startswith('可变电容器'))
CAP_QUALITY_COLUMNS = {'A1P': 2, 'A1M': 3, 'A1': 2, 'A2': 4, 'B1': 5, 'B2': 6,
                       'C': 7, 'C1': 7, 'C2': 8}
CAP_MOUNTING = ('片式', '有引线(包含有引线 SMT，通孔式)')
CAP_LIMITATION = '表 B.52 未列纸-膜复合介质基本率；表 B.56 未列纸介表面贴装系数，这两种类型不能自动完成计算。'


def _capacitor_type(kind):
    if kind.startswith('纸和薄膜'):
        return '纸和薄膜', 1, '薄膜' if kind == FILM else None
    if kind.startswith('瓷介电容器'):
        label = '1 类瓷介' if '1 类' in kind else '2 类瓷介'
        return '瓷介 / ' + label, 2 if '1 类' in kind else 3, '瓷介 / ' + label
    if kind.startswith('可变'):
        return '可变', None, None
    label = kind.removesuffix('电容器')
    return label, 4 if label == '云母' else 6 if kind in TANTALUM else 5, label


def _capacitor(p, environment, ambient, *, network=False):
    kind, grade = p['kind'], p['quality']
    if kind == PAPER_COMPOSITE:
        raise ValueError('表 B.52 未给出纸-膜复合介质电容器的非工作基本失效率')
    if kind == PAPER:
        raise ValueError('表 B.56 未给出纸介电容器的非工作表面贴装系数')
    quality_kind, temp_column, surface_kind = _capacitor_type(kind)
    if grade in ('C1', 'C2') and quality_kind != '纸和薄膜' or grade == 'C' and quality_kind == '纸和薄膜':
        raise ValueError('表 B.53 纸和薄膜电容器须选择 C1/C2，其他类型使用 C 级')
    if grade == 'A1' and kind not in VARIABLE or grade in ('A1P', 'A1M') and kind in VARIABLE:
        raise ValueError('表 B.53 可变电容器使用 A1，其他类型按 A1P/A1M 分级')
    f = Factors()
    f.add('λNb', *row_lookup(52, kind, columns=(0, 1)))
    f.add('πNQ', *row_lookup(53, quality_kind, CAP_QUALITY_COLUMNS[grade], (0, 1)))
    f.add('πNE', *matrix(54, quality_kind, environment, columns=(0, 1)))
    if kind in VARIABLE:
        f.add('πNT', 1, '第 B.2.10 节明确：可变电容器 πNT=1')
    else:
        f.add('πNT', *temp_axis_value(55, ambient, output=temp_column))
    if kind in VARIABLE or kind == '非固体钽电解电容器':
        f.add('πNch', 1, '第 B.2.10 节明确：非固体钽、可变电容器 πNch=1')
    else:
        f.add('πNch', *row_lookup(56, surface_kind, 2 if p['mounting'] == CAP_MOUNTING[0] else 3, (0, 1)))
    if kind in TANTALUM:
        f.add('πCYC', 1, '第 B.2.10 节明确：钽电容器 πCYC=1')
    else:
        f.add('πCYC', *cycle_value(57, p['cycle_rate']))
    if network:
        f.add('N', p['element_count'], '网络电容个数；式 B.14')
    return f.result()


_PAPER_FILM_CAPS = tuple(kind for kind in CAPACITORS if kind.startswith('纸和薄膜'))
_NIOBIUM_CAPS = ('氧化铌电解电容器',)
_STD_CAPS = tuple(
    kind for kind in CAPACITORS
    if kind not in _PAPER_FILM_CAPS and kind not in VARIABLE and kind not in _NIOBIUM_CAPS
)
CAP_FIELDS = (
    choice('kind', '电容器种类', CAPACITORS),
    choice('quality', '质量等级', tuple(CAP_QUALITY_COLUMNS),
           choices_by=('kind', (
               (_PAPER_FILM_CAPS, ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C1', 'C2')),
               (_STD_CAPS, ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C')),
               (_NIOBIUM_CAPS, ('A2', 'B1', 'B2', 'C')),
               (VARIABLE, ('A1', 'A2', 'B1', 'B2', 'C')),
           )),
           constraint_hint='表 B.53 按电容器种类限定可选质量等级'),
    choice('mounting', '安装形式', CAP_MOUNTING,
           when=('kind', tuple(kind for kind in CAPACITORS if kind not in VARIABLE and kind != '非固体钽电解电容器'))),
    cycle_field(57, when=('kind', tuple(kind for kind in CAPACITORS if kind not in TANTALUM))), temperature(),
)
CAP_EXAMPLE = {'kind': FILM, 'quality': 'B2', 'mounting': CAP_MOUNTING[0], 'cycle_rate': 1}
for network in (False, True):
    MODELS['国产电容网络（非工作详细）' if network else '国产电容器（非工作详细）'] = spec(
        CAP_FIELDS + ((Input('element_count', '网络电容个数 N', integer=True, min_value=1, presets=(2, 4, 8),
                             constraint_hint='式 B.14 网络电容个数 N ≥ 1（正整数）'),) if network else ()),
        'λNP = N × λNP[单]' if network else 'λNP = λNb × πNQ × πNE × πNT × πNch × πCYC',
        'B.2.10', (52, 53, 54, 55, 56, 57), partial(_capacitor, network=network),
        CAP_EXAMPLE | ({'element_count': 4} if network else {}), category='C', qualities=(53,),
        note=CAP_LIMITATION, limitation=CAP_LIMITATION)


def _inductive(p, environment, _ambient, *, base, env, quality, cycle, section):
    f = Factors()
    f.add('λNb', *(row_lookup(base, p['kind']) if base else stated_base_rate(section)))
    f.add('πNE', *scalar(env, environment))
    f.add('πNQ', *quality_value(quality, p['quality']))
    f.add('πCYC', *cycle_value(cycle, p['cycle_rate']))
    return f.result()


for title, section, base, env, q, cycle in (
    ('电感器', 'B.2.11.2', 58, 59, 60, 61),
    ('片式电感器', 'B.2.11.3', None, 62, 63, 64),
    ('变压器', 'B.2.11.4', 65, 66, 67, 68),
):
    fields = ((choice('kind', '器件类型', row_options(base)),) if base else ()) + (quality_field(q), cycle_field(cycle))
    example = {'quality': 'B2', 'cycle_rate': 1}
    if base:
        example['kind'] = row_options(base)[0]
    MODELS[f'国产{title}（非工作详细）'] = spec(
        fields, 'λNP = λNb × πNE × πNQ × πCYC', section,
        tuple(value for value in (base, env, q, cycle) if value),
        partial(_inductive, base=base, env=env, quality=q, cycle=cycle, section=section),
        example, category='L', qualities=(q,))
