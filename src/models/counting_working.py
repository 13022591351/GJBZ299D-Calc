"""Working-state parts count: chapter 6 and appendix C.3.

Generic rates are per device in 10^-6/h. Project aggregation supplies quantity N.
Only domestic integrated circuits have the chapter-6 maturity correction.
"""

from .counting_common import QualityRule as Q
from .counting_common import counting_model

MODELS = {}


def _discrete(kind):
    if '普通二极管' in kind:
        table = '52'
    elif '电压调整' in kind:
        table = '58'
    elif '微波硅' in kind:
        table = '63'
    elif '微波二极管' in kind:
        table = '66'
    elif '微波双极' in kind:
        table = '87'
    elif '微波场效应' in kind:
        table = '95'
    elif '硅场效应' in kind:
        table = '80'
    elif '双极型' in kind:
        table = '72'
    elif '单结' in kind:
        table = '102'
    elif '闸流' in kind:
        table = '105'
    else:
        raise ValueError(f'半导体分立器件质量种类未定义：{kind}')
    return Q(table, horizontal=True)


def _resistor(kind):
    for label, table in (
        ('金属膜', '128'), ('碳膜', '132'), ('玻璃釉', '136'),
        ('功率非线绕', '154'), ('功率线绕', '145'), ('线绕', '140'),
        ('箔式', '149'), ('片式膜', '157'), ('电阻网络', '163'),
    ):
        if label in kind:
            return Q(table)
    raise ValueError(f'电阻器质量种类未定义：{kind}')


def _potentiometer(kind):
    tables = {'普通线绕电位器': '173', '精密线绕电位器': '177', '微调线绕电位器': '181',
              '有机实芯电位器': '185', '合成碳膜电位器': '191', '金属玻璃釉电位器': '197',
              '非线绕精密电位器': '202'}
    return Q(tables[kind])


def _capacitor(kind):
    for label, table in (
        ('纸和薄膜', '209'), ('云母', '215'), ('1类瓷介', '219'), ('2类瓷介', '224'),
        ('铝电解', '228'), ('非固体钽', '237'), ('固体钽', '232'), ('氧化铌', '241'), ('可变', '246'),
    ):
        if label in kind:
            return Q(table)
    raise ValueError(f'电容器质量种类未定义：{kind}')


def _inductive(kind, imported=False):
    if imported:
        return Q('C.73', 0) if '变压器' in kind else Q('C.69' if '片式' in kind else 'C.66')
    return Q('258' if '变压器' in kind else '254' if '片式' in kind else '251')


IC_NAMES = (
    '密封单片数字集成电路', '非密封单片数字集成电路', '密封只读及可编程只读存储器',
    '非密封只读及可编程只读存储器', 'MOS可擦可编程只读存储器', '单片双极、MOS模拟电路',
    '微波单片集成电路', '密封静态随机存储器', '非密封静态随机存储器', '密封先进先出存储器',
    'MOS动态随机存储器', '密封单片微处理器', '非密封单片微处理器',
)
for _table, _name in zip(range(356, 369), IC_NAMES, strict=True):
    MODELS[f'国产{_name}（工作计数）'] = counting_model(
        str(_table), Q('3'), category='IC', section='6', method='working_count', maturity=True,
        note='集成电路通用失效率以稳定生产产品为基础；其他成熟程度按表 29 修正。'
             '混合集成电路按 5.1.3 的详细模型预计。',
    )

_DOMESTIC = (
    ('369', '声表面波器件', 'SAW', Q('44'), ()),
    ('370', '半导体分立器件', 'Q', _discrete, ('46',)),
    ('371', '光电子器件', 'LED', Q('110'), ()),
    ('372', '真空电子器件', 'V', Q('112'), ()),
    ('373', '电阻器', 'R', _resistor, ('124',)),
    ('374', '敏感电阻器', 'RT', Q('166'), ()),
    ('375', '电位器', 'RP', _potentiometer, ('169',)),
    ('376', '电容器', 'C', _capacitor, ()),
    ('377', '感性元件', 'L', _inductive, ('247',)),
    ('378', '继电器', 'K', lambda kind: Q('275' if '固体' in kind else '267'), ()),
    ('379', '开关', 'S', Q('284'), ()),
    ('380', '电连接器', 'J', Q('291'), ()),
    ('381', '微特电机', 'M', lambda kind: Q('303' if '时间累加器' in kind else
                                             '300' if '自整角机' in kind else '297'), ()),
    ('382', '磁性器件', 'MAG', Q('319'), ()),
    ('383', '振荡器和谐振器', 'Y', lambda kind: Q('324' if '谐振器' in kind else '321'), ()),
    ('384', '滤波器', 'FL', lambda kind: Q('331' if 'LC' in kind else '327'), ()),
    ('385', '电池', 'BT', Q('334'), ()),
    ('386', '灯', 'HL', None, ()),
    ('387', '激光器', 'LASER', None, ()),
    ('388', '压电陀螺', 'GYRO', None, ()),
    ('389', '光纤连接器', 'OPT', Q('352'), ()),
)
for _table, _name, _category, _rule, _definitions in _DOMESTIC:
    _limit = (f'第 6 章计数通式要求质量系数，但表 {_table} 及相应详细章节未明确本类计数质量系数；'
              '请使用本类工作状态应力分析法详细模型。') if _rule is None else ''
    _note = '表列电阻网络为 NR=10 的分类，其他元件数采用详细模型。' if _table == '373' else ''
    if _table == '377':
        _note = '表 377 续表两行均标为小功率脉冲变压器但通用率不同，该分类拒算并使用详细模型。'
        _limit = _note
    MODELS[f'国产{_name}（工作计数）'] = counting_model(
        _table, _rule, category=_category, section='6', method='working_count',
        definitions=_definitions, limitation=_limit, note=_note,
        restricted_kinds=(('变压器 / 小功率脉冲变压器', _note),) if _table == '377' else (),
    )

_IMPORTED_IC = (
    '密封单片数字集成电路', '非密封单片数字集成电路', '密封只读及可擦可编程只读存储器',
    '非密封只读及可擦可编程只读存储器', '单片双极、MOS模拟电路', '微波单片集成电路',
    '密封静态随机存储器', '非密封静态随机存储器', '密封先进先出存储器', 'MOS动态随机存储器',
    '密封单片微处理器', '非密封单片微处理器',
)
for _table, _name in zip(range(110, 122), _IMPORTED_IC, strict=True):
    MODELS[f'进口{_name}（工作计数）'] = counting_model(
        f'C.{_table}', Q('C.15', 0), category='IC', section='C.3', method='working_count',
        note='混合集成电路按 C.2.2.3 详细模型或制造厂商数据预计。',
    )

_IMPORTED = (
    ('122', '声表面波器件', 'SAW', Q('C.29', 0)),
    ('123', '半导体分立器件', 'Q', Q('C.32', 0)),
    ('124', '光电子器件', 'LED', Q('C.39', 0)),
    ('125', '真空电子器件', 'V', Q('C.43', 0)),
    ('126', '电阻器', 'R', Q('C.46')),
    ('127', '敏感电阻器', 'RT', Q('C.51')),
    ('128', '电位器', 'RP', Q('C.55')),
    ('129', '电容器', 'C', Q('C.60')),
    ('130', '感性元件', 'L', lambda kind: _inductive(kind, imported=True)),
    ('131', '继电器', 'K', Q('C.77')),
    ('132', '开关', 'S', Q('C.83', 0)),
    ('133', '电连接器', 'J', Q('C.88', 0)),
    ('134', '微特电机', 'M', Q('C.93', 0)),
    ('135', '磁性器件', 'MAG', Q('C.96', 0)),
    ('136', '振荡器和谐振器', 'Y', Q('C.99', 0)),
    ('137', '滤波器', 'FL', Q('C.102', 0)),
    ('138', '电池', 'BT', Q('C.105', 0)),
    ('139', '灯', 'HL', Q('C.108', 0)),
)
for _table, _name, _category, _rule in _IMPORTED:
    MODELS[f'进口{_name}（工作计数）'] = counting_model(
        f'C.{_table}', _rule, category=_category, section='C.3', method='working_count',
        note='厚薄膜网络仅使用表列 N=10 分类；其他元件数采用详细模型。' if _table == '126' else '',
        restricted_kinds=((
            '电阻网络 / 每个分立元件（N=10）',
            '表 C.126 分立电阻网络同时标注每个分立元件与 N=10，计量单位不明确；请使用进口电阻网络详细模型。',
        ),) if _table == '126' else (),
    )
