"""Domestic nonoperating-state parts count, appendix B.3, tables B.121–B.149."""

from dataclasses import replace

from ..model_spec import Input, choice
from .counting_common import QualityRule as Q
from .counting_common import counting_model

MODELS = {}


def _discrete(kind):
    if '二极管' in kind:
        column = 2 if '普通' in kind else 3 if '调整' in kind else 4 if '微波硅' in kind else 5
        return Q('B.21', value_column=column)
    for label, column in (('双极型', 2), ('硅场效应', 3), ('单结', 4), ('微波', 5), ('闸流', 6)):
        if label in kind:
            return Q('B.26', value_column=column)
    raise ValueError(f'半导体分立器件质量种类未定义：{kind}')


def _resistor(kind):
    for label, row in (
        ('金属膜', '金属膜'), ('碳膜', '碳膜(功率P≤2W)'), ('玻璃釉', '玻璃釉(功率P≤2W)'),
        ('功率非线绕', '功率非线绕'), ('功率线绕', '功率线绕'), ('线绕', '线绕'),
        ('箔式', '箔式'), ('片式膜', '片式膜'), ('电阻网络', '电阻网络'),
    ):
        if label in kind:
            return Q('B.40', row_label=row)
    raise ValueError(f'电阻器质量种类未定义：{kind}')


def _capacitor(kind):
    for label, row in (
        ('有机介质', '纸和薄膜'), ('1类瓷介', '1类瓷介'), ('2类瓷介', '2类瓷介'),
        ('云母', '云母'), ('铝电解', '铝电解'), ('非固体钽', '非固体钽电解'),
        ('固体钽', '固体钽电解'), ('氧化铌', '氧化铌电解'), ('可变', '可变'),
    ):
        if label in kind:
            return Q('B.53', row_label=row, header_row=2, row_label_column=1)
    raise ValueError(f'电容器质量种类未定义：{kind}')


_IC_NAMES = (
    '密封单片数字集成电路', '非密封单片数字集成电路', '密封单片微处理器',
    '非密封单片微处理器', '单片双极、MOS模拟电路', '密封存储器', '非密封存储器',
    '微波单片集成电路',
)
for _table, _name in zip(range(121, 129), _IC_NAMES, strict=True):
    MODELS[f'国产{_name}（非工作计数）'] = counting_model(
        f'B.{_table}', Q('B.7'), category='IC', section='B.3', method='nonoperating_count',
        note='混合集成电路按 B.2.2.3 详细模型预计。', definitions=('3',),
        quality_summaries=(('C1', '表 B.7 未给出质量要求说明'), ('C2', '表 B.7 未给出质量要求说明')),
    )

_FAMILIES = (
    ('129', '声表面波器件', 'SAW', Q('B.17'), ('44',)),
    ('130', '半导体分立器件', 'Q', _discrete, ('46',)),
    ('131', '光电子器件', 'LED', Q('B.34'), ('110',)),
    ('132', '真空电子器件', 'V', Q('B.38'), ('112',)),
    ('133', '电阻器', 'R', _resistor, ('124',)),
    ('134', '敏感电阻器', 'RT', Q('B.45'), ('166',)),
    ('135', '电位器', 'RP', lambda kind: Q('B.49', row_label=kind.removesuffix('电位器')), ('169',)),
    ('136', '电容器', 'C', _capacitor, ('209', '219', '224', '215', '228', '232', '237', '241', '246')),
    ('137', '感性元件', 'L', lambda kind: Q('B.67' if '变压器' in kind else
                                            'B.63' if '片式' in kind else 'B.60'), ('247',)),
    ('138', '继电器', 'K', lambda kind: Q('B.72' if '固体' in kind else 'B.71'), ('267', '275')),
    ('139', '开关', 'S', Q('B.75'), ('284',)),
    ('140', '电连接器', 'J', Q('B.78'), ('291',)),
    ('141', '微特电机', 'M', lambda kind: Q('B.82' if '时间累加器' in kind or '自整角机' in kind
                                            else 'B.81'), ('297', '300', '303')),
    ('142', '磁性器件', 'MAG', Q('B.94'), ('319',)),
    ('143', '振荡器和谐振器', 'Y', lambda kind: Q('B.98' if '谐振器' in kind else 'B.96'), ('321', '324')),
    ('144', '滤波器', 'FL', lambda kind: Q('B.104' if 'LC' in kind else 'B.101'), ('327', '331')),
    ('145', '电池', 'BT', Q('B.107'), ('334',)),
    ('146', '灯', 'HL', None, ()),
    ('147', '激光器', 'LASER', None, ()),
    ('148', '压电陀螺', 'GYRO', None, ()),
    ('149', '光纤连接器', 'OPT', Q('B.119'), ('352',)),
)
for _table, _name, _category, _rule, _definitions in _FAMILIES:
    _limit = (f'B.3 计数通式要求非工作质量系数，但表 B.{_table} 及相应详细章节未明确本类计数质量系数；'
              '请使用本类非工作状态详细模型。') if _rule is None else ''
    MODELS[f'国产{_name}（非工作计数）'] = counting_model(
        f'B.{_table}', _rule, category=_category, section='B.3', method='nonoperating_count',
        definitions=_definitions, limitation=_limit,
        quality_summaries=((
            'A1', '振荡器表 B.96 未细分 A1S/A1B；谐振器按表 324'), (
            'B', '振荡器表 B.96 未细分 B1/B2；谐振器按表 324')) if _table == '143' else (),
        note='碳膜、玻璃釉电阻限定功率 P≤2W；功率线绕及功率非线绕电阻按 P>2W 分类。' if _table == '133' else '',
    )

# Table B.40 only supplies carbon/glaze quality coefficients for P≤2W.
_name = '国产电阻器（非工作计数）'
_spec = MODELS[_name]
_restricted_kinds = tuple(kind for kind in _spec.fields[0].choices if '碳膜' in kind or '玻璃釉' in kind)
MODELS[_name] = replace(_spec, fields=(*_spec.fields, choice(
    'power_class', '功率范围', ('P≤2W',), when=('kind', _restricted_kinds)),
    choice('high_power_class', '功率范围', ('P>2W',), when=('kind', tuple(
        kind for kind in _spec.fields[0].choices if kind.startswith(('功率线绕', '功率非线绕')))))))


_NETWORK_KINDS = tuple(kind for kind in _spec.fields[0].choices if kind.startswith('电阻网络'))
_BASE_RESISTOR = MODELS[_name]


def _network_units(p, environment, ambient):
    evaluated = _BASE_RESISTOR.calculator(p, environment, ambient)
    if p['kind'] not in _NETWORK_KINDS:
        return evaluated
    count = p['element_count']
    if isinstance(count, bool) or not isinstance(count, (int, float)) or count < 1 or count != int(count):
        raise ValueError('网络电阻数须为正整数')
    return replace(evaluated, rate=evaluated.rate * count, factors=evaluated.factors + (
        ('NE', count, '表 B.133 以单个网络内电阻列通用率；网络电阻数 NE，工程数量另计网络件数'),))


MODELS[_name] = replace(_BASE_RESISTOR, fields=(*_BASE_RESISTOR.fields, Input(
    'element_count', '网络电阻数 NE', integer=True, when=('kind', _NETWORK_KINDS))),
    calculator=_network_units, formula='λ = λNGπNQ [网络：NEλNGπNQ]',
    note=_BASE_RESISTOR.note + '表 B.133 网络按内部单个电阻列率，乘网络电阻数 NE；工程数量为网络件数。')
