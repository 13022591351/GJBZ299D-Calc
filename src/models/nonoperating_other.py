"""Appendix B.2.12–B.2.25: nonoperating assemblies, carriers and other parts."""

import math
from dataclasses import replace
from functools import partial

from ..model_spec import Input, choice
from ..numeric import number_text
from ..reference_data import equal, fragments, paired_value, result, stated_base_rate
from .common import Factors
from .nonoperating_common import (
    axis_value,
    cell_source,
    constant,
    header_options,
    horizontal_axis,
    interval_matches,
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
SIMPLE_FORMULA = 'λNP = λNb × πNE × πNQ'


def _simple(p, environment, _ambient, *, base, env, q, section, horizontal=False):
    f = Factors()
    value = ((scalar(base, p['kind']) if horizontal else row_lookup(base, p['kind']))
             if base else stated_base_rate(section))
    f.add('λNb', *value)
    f.add('πNE', *scalar(env, environment))
    if q:
        f.add('πNQ', *quality_value(q, p['quality']))
    return f.result()


for title, section, base, env, q, horizontal, category in (
    ('开关', 'B.2.13', 73, 74, 75, False, 'S'),
    ('电连接器', 'B.2.14', 76, 77, 78, False, 'J'),
    ('焊接点', 'B.2.16.2', 85, 86, None, False, 'PCB'),
    ('磁性器件', 'B.2.17', 92, 93, 94, True, 'MAG'),
    ('振荡器', 'B.2.18.1', None, 95, 96, False, 'Y'),
    ('谐振器', 'B.2.18.2', None, 97, 98, False, 'Y'),
    ('滤波器', 'B.2.19.2', 99, 100, 101, True, 'FL'),
    ('电池', 'B.2.20', 105, 106, 107, True, 'BT'),
    ('灯', 'B.2.21', 108, 109, None, True, 'HL'),
):
    kinds = (header_options(base) if horizontal else row_options(base)) if base else ()
    fields = ((choice('kind', '器件类型', kinds),) if kinds else ()) + ((quality_field(q),) if q else ())
    example = {'kind': kinds[0]} if kinds else {}
    if q:
        example['quality'] = 'B2' if 'B2' in quality_options(q) else 'B'
    MODELS[f'国产{title}（非工作详细）'] = spec(
        fields, SIMPLE_FORMULA if q else 'λNP = λNb × πNE', section,
        tuple(value for value in (base, env, q) if value),
        partial(_simple, base=base, env=env, q=q, section=section, horizontal=horizontal),
        example, category=category, qualities=(q,) if q else ())


RELAY_KINDS = row_options(69, (0, 1, 2))
NONHERMETIC_RELAY = next(kind for kind in RELAY_KINDS if '非密封' in kind)


def _relay(p, environment, _ambient):
    f = Factors()
    candidates = []
    for table in fragments('B.69'):
        for row in range(1, table['rows']):
            if not equal(path(table, row, (0, 1, 2)), p['kind']):
                continue
            if p['kind'] != NONHERMETIC_RELAY or interval_matches(table['grid'][row, 3]['text'], p['contact_voltage_mV']):
                candidates.append((table, table['grid'][row, 4]))
    f.add('λNb', *result('B.69', candidates))
    f.add('πNE', *scalar(70, environment))
    f.add('πNQ', *quality_value(72 if p['kind'] == '固体继电器' else 71, p['quality']))
    return f.result()


_MECH_RELAYS = tuple(kind for kind in RELAY_KINDS if kind != '固体继电器')
MODELS['国产继电器（非工作详细）'] = spec(
    (choice('kind', '继电器类型与密封工艺', RELAY_KINDS),
     choice('quality', '质量等级', tuple(dict.fromkeys(quality_options(71) + quality_options(72))),
            choices_by=('kind', (
                (_MECH_RELAYS, quality_options(71)),
                ('固体继电器', quality_options(72)),
            )),
            constraint_hint='表 B.71（机电）与表 B.72（固体）质量等级不同'),
     choice('contact_voltage_mV', '触点电压 [工作值，mV]', ('＞50', '≤50'), '非工作条件',
            positive=False, when=('kind', (NONHERMETIC_RELAY,)))),
    SIMPLE_FORMULA, 'B.2.12', (69, 70, 71, 72), _relay,
    {'kind': RELAY_KINDS[0], 'quality': 'B2'}, category='K', qualities=(71, 72),
    note='表 B.69 的非密封机电继电器按触点电压工作值区分；表 B.71 的 A1R 无表值。')


def _motor(p, environment, _ambient):
    f = Factors()
    f.add('λNb', *row_lookup(79, p['kind']))
    f.add('πNE', *scalar(80, environment))
    f.add('πNQ', *quality_value(81 if p['kind'] in ('电动机', '风机') else 82, p['quality']))
    return f.result()


MODELS['国产微特电机（非工作详细）'] = spec(
    (choice('kind', '微特电机类别', row_options(79)),
     choice('quality', '质量等级', tuple(dict.fromkeys(quality_options(81) + quality_options(82))),
            choices_by=('kind', (
                (('电动机', '风机'), quality_options(81)),
                (('旋转变压器、自整角机', '时间累加器'), quality_options(82)),
            )),
            constraint_hint='表 B.81（电动机/风机）与表 B.82 质量等级不同')),
    SIMPLE_FORMULA, 'B.2.15', (79, 80, 81, 82), _motor,
    {'kind': '电动机', 'quality': 'B2'}, category='M', qualities=(81, 82))


def _pcb(p, environment, _ambient):
    f = Factors()
    b1 = f.add('λNb1', *constant('B.2.16.1', 'λNb1'))
    b2 = f.add('λNb2', *constant('B.2.16.1', 'λNb2'))
    holes = f.add('N', p['plated_hole_count'], '金属化孔数；式 B.22')
    e = f.add('πNE', *scalar(83, environment))
    q = f.add('πNQ', *quality_value(84, p['quality']))
    return f.result((b1 * holes + b2) * e * q)


MODELS['国产印制板（非工作详细）'] = spec(
    (Input('plated_hole_count', '金属化孔数 N', positive=False, integer=True), quality_field(84)),
    'λNP = (λNb1 × N + λNb2) × πNE × πNQ', 'B.2.16.1', (83, 84), _pcb,
    {'plated_hole_count': 100, 'quality': 'B'}, category='PCB', qualities=(84,),
    note='式 B.22 无 πC 系数。')


BOARD_MATERIALS = tuple(dict.fromkeys(
    cell['text'] for table in fragments('B.88') for cell in table['cells']
    if cell['row_no'] > 0 and cell['column_no'] % 2 == 0))
SMT_LEADS = row_options(91)
LEADED = ('J 或 S 型引线', '鸥翼型引线')
SMT_CONNECTION_FIELDS = (
    choice('cte_method', '热膨胀系数取值', ('表列缺省值', '实测值')),
    choice('board_material', '电路板底板材料', BOARD_MATERIALS, when=('cte_method', ('表列缺省值',))),
    choice('package_material', '封装材料', row_options(89), when=('cte_method', ('表列缺省值',))),
    Input('board_cte', '底板热膨胀系数 αS [ppm/℃]', positive=False, when=('cte_method', ('实测值',))),
    Input('package_cte', '封装热膨胀系数 αCC [ppm/℃]', positive=False, when=('cte_method', ('实测值',))),
    choice('lead_structure', '元器件引线结构', SMT_LEADS),
    Input('joint_height_mm', '无管脚焊接点高度 h [mm]', when=('lead_structure', ('无引线', 'BGA'))),
    Input('distance_mm', '器件中心到最远焊接点距离 d [mm]'),
    Input('temperature_span_C', '使用环境极值温差 ΔT [℃]', positive=False),
    Input('cycles_per_hour', '温度循环率 f [次/h]', positive=False),
    Input('design_life_h', '设备设计寿命周期 LC [h]'),
)
SMT_EXAMPLE = {'cte_method': '表列缺省值', 'board_material': BOARD_MATERIALS[0],
               'package_material': '陶瓷', 'lead_structure': '无引线', 'joint_height_mm': .2,
               'distance_mm': 10, 'temperature_span_C': 50, 'cycles_per_hour': .1, 'design_life_h': 10000}


def _smt_nonop_adjustment(product):
    if not math.isfinite(product) or product < 0:
        raise ValueError('λNbLC 不在表 B.87 区间内')
    if product <= .30:
        return axis_value(87, '0～0.30')
    for printed_low, high, label in ((.31, .6, '0.31～0.6'), (.61, .8, '0.61～0.8'), (.81, .9, '0.81～0.90')):
        if product <= high:
            value, source = axis_value(87, label)
            if product < printed_low:
                source += f'；λNbLC={number_text(product)} 向上归入 {label} 档'
            return value, source
    return axis_value(87, '>0.90')


def _smt(p, _environment, _ambient):
    f = Factors()
    if p['cte_method'] == '表列缺省值':
        board = f.add('αS', *paired_value('B.88', p['board_material']))
        package = f.add('αCC', *row_lookup(89, p['package_material']))
    else:
        board = f.add('αS', p['board_cte'], '电路板底板实测热膨胀系数 [ppm/℃]；式 B.25')
        package = f.add('αCC', p['package_cte'], '封装材料实测热膨胀系数 [ppm/℃]；式 B.25')
    delta = f.add('ΔT', p['temperature_span_C'], '使用环境温度极值之间的温差；式 B.25')
    if p['lead_structure'] in LEADED:
        height = f.add('h', *constant('B.2.16.3', 'h'))
    else:
        height = f.add('h', p['joint_height_mm'], '无管脚器件的实际焊接点高度 [mm]；式 B.25')
    distance = f.add('d', p['distance_mm'], '器件中心到最远焊接点距离 [mm]；式 B.25')
    cycles = p['cycles_per_hour']
    if cycles <= .2:
        table = fragments('B.90')[0]
        cr = f.add('CR', cycles, cell_source(table, table['grid'][1, 1]))
    else:
        cr = f.add('CR', *axis_value(90, cycles, symbol='f'))
    leads = f.add('πC', *row_lookup(91, p['lead_structure']))
    base = f.add('λNb', (distance / height * abs(board * delta - package * delta) * 1e-6) ** 2.26
                 / 7.5 * cr * leads, '式 B.25')
    adjustment = f.add('A', *_smt_nonop_adjustment(base * p['design_life_h']))
    return f.result(adjustment * base)


def _smt_board(p, environment, ambient):
    evaluated = [_smt(record, environment, ambient) for record in p['connections']]
    if not evaluated:
        raise ValueError('必须给出电路板上的全部 SMT 连接类别')
    index, weakest = max(enumerate(evaluated), key=lambda pair: pair[1].rate)
    trace = tuple((f'连接类别 {i + 1} λNSMT', value.rate, '式 B.24～B.25：本连接类别独立预计')
                  for i, value in enumerate(evaluated))
    return replace(weakest, factors=trace + weakest.factors + (
        ('最薄弱连接序号', index + 1, '第 B.2.16.3 节：整板取最大的连接失效率，不按连接类别累加'),
        ('连接类别数', len(evaluated), '电路板全部表面安装元器件连接类别数'),))


MODELS['国产SMT互连（非工作详细）'] = spec(
    (Input('connections', '电路板全部 SMT 连接类别', record_fields=SMT_CONNECTION_FIELDS),),
    'λNSMT[整板] = max(A × λNb)', 'B.2.16.3', (87, 88, 89, 90, 91), _smt_board,
    {'connections': [SMT_EXAMPLE.copy()]}, category='PCB',
    note='每个预计项表示整块板，数量为板数；全部连接逐类预计后取最大值。'
         'B.25 不含工作态功耗温升，直接使用环境极值温差；表 B.87 按连续半开区间向上归档。')


DETAILED = '组成元器件非工作失效率求和'
APPROXIMATE = '式 B.30 近似模型'


def _lc(p, environment, ambient):
    if p['method'] == DETAILED:
        if p['source_environment'] != environment or p['source_temperature_C'] != ambient:
            raise ValueError('LC 组成元器件非工作预计结果须对应当前环境类别和环境温度')
        f = Factors()
        f.add('ΣλNP[组成元器件]', p['constituent_rate'],
              '第 B.2.19.3 节：使用非工作详细预计法或非工作计数预计法逐项相加')
        return f.result(p['constituent_rate'])
    return _simple(p, environment, ambient, base=102, env=103, q=104,
                   section='B.2.19.3', horizontal=True)


MODELS['国产LC滤波器（非工作详细）'] = spec(
    (choice('method', '预计方法', (DETAILED, APPROXIMATE)),
     choice('kind', '滤波器组成', header_options(102), when=('method', (APPROXIMATE,))),
     quality_field(104, when=('method', (APPROXIMATE,))),
     Input('constituent_rate', '组成元器件非工作失效率之和 [10⁻⁶/h]', positive=False, when=('method', (DETAILED,))),
     choice('source_environment', '组成结果适用环境类别', (
         'GB', 'GMS', 'GF1', 'GF2', 'GM1', 'GM2', 'MP', 'NSB', 'NS1', 'NS2', 'NU',
         'AIF', 'AUF', 'AIC', 'AUC', 'ARW', 'SF', 'ML', 'MF'), when=('method', (DETAILED,))),
     Input('source_temperature_C', '组成结果适用环境温度 [℃]', allow_negative=True, when=('method', (DETAILED,)))),
    '组成法 λNP = ΣλNP[组成元器件]；近似法 λNP = λNb × πNE × πNQ', 'B.2.19.3', (102, 103, 104), _lc,
    {'method': APPROXIMATE, 'kind': header_options(102)[0], 'quality': 'B'}, category='FL', qualities=(104,),
    note='组成求和须使用同工况的非工作预计值。')


LASER_KINDS = tuple(kind for kind in row_options(110, (0, 1)) if not kind.startswith('半导体')) + ('半导体激光器',)


def _laser(p, environment, _ambient):
    f = Factors()
    if p['kind'] == '半导体激光器':
        power = p['output_power_mW']
        candidates = []
        for table in fragments('B.110'):
            for row in range(1, table['rows']):
                if table['grid'][row, 0]['text'] != '半导体激光器':
                    continue
                band = table['grid'][row, 1]['text'].replace('输出光功率', 'P').replace('mW', '')
                if interval_matches(band, power, 'P'):
                    candidates.append((table, table['grid'][row, 2]))
        f.add('λNb', *result('B.110', candidates))
    else:
        f.add('λNb', *row_lookup(110, p['kind'], columns=(0, 1)))
    f.add('πNE', *scalar(111, environment))
    f.add('πNL', *scalar(112, p['maturity']))
    return f.result()


MODELS['国产激光器（非工作详细）'] = spec(
    (choice('kind', '激光器类型', LASER_KINDS), choice('maturity', '成熟程度', header_options(112)),
     Input('output_power_mW', '输出光功率 [mW]', positive=False, when=('kind', ('半导体激光器',)),
           min_value=0,
           presets=(
               (50, '50 mW（≤ 90 mW 档）'),
               (90, '90 mW（≤ 90 mW 上限）'),
               (150, '150 mW（90～150 mW 上限）'),
               (200, '200 mW（＞ 150 mW 档）'),
           ),
           constraint_hint='表 B.110 分档：≤90、90＜P≤150、＞150 mW')),
    'λNP = λNb × πNE × πNL', 'B.2.22', (110, 111, 112), _laser,
    {'kind': '半导体激光器', 'output_power_mW': 150, 'maturity': header_options(112)[0]}, category='LASER')


def _gyro(p, environment, _ambient):
    f = Factors()
    f.add('λNb', *stated_base_rate('B.2.23'))
    f.add('πNC', *scalar(113, p['axes']))
    f.add('πNE', *scalar(114, environment))
    f.add('πNL', *scalar(115, p['maturity']))
    return f.result()


MODELS['国产压电陀螺（非工作详细）'] = spec(
    (choice('axes', '类别', header_options(113)), choice('maturity', '成熟程度', header_options(115))),
    'λNP = λNb × πNC × πNE × πNL', 'B.2.23', (113, 114, 115), _gyro,
    {'axes': '单轴', 'maturity': header_options(115)[0]}, category='GYRO')


_FIBER_COUNTS_B116 = (1, 2, 4, 6, 8)


def _fiber(p, environment, _ambient):
    f = Factors()
    raw = p['fiber_count']
    note = ''
    if raw == 'N≥10':
        axis = 'N≥10'
    else:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw <= 0 or int(raw) != raw:
            raise ValueError('芯数必须为正整数')
        n = int(raw)
        if n > _FIBER_COUNTS_B116[-1]:
            axis = 'N≥10'
            note = f'；芯数 {n} 归入 N≥10 档' if n >= 10 else '；芯数向上取 N≥10 档'
        else:
            axis = next(tick for tick in _FIBER_COUNTS_B116 if tick >= n)
            if axis > n:
                note = f'；芯数向上取 {axis} 档'
    val, src = horizontal_axis(116, axis, 'N')
    f.add('λNb', val, src + note)
    f.add('πNL', *scalar(117, p['connection_mode']))
    f.add('πNE', *scalar(118, environment))
    f.add('πNQ', *quality_value(119, p['quality']))
    return f.result()


MODELS['国产光纤连接器（非工作详细）'] = spec(
    (choice('fiber_count', '芯数 N', (1, 2, 4, 6, 8, 'N≥10'),
            integer=True, min_value=1, ceil_choices=True,
            constraint_hint='表 B.116 芯数：1、2、4、6、8、N≥10（中间整数向上取档，≥10 取 N≥10）'),
     choice('connection_mode', '连接模式', header_options(117)), quality_field(119)),
    'λNP = λNb × πNL × πNE × πNQ', 'B.2.24', (116, 117, 118, 119), _fiber,
    {'fiber_count': 4, 'connection_mode': 'FC', 'quality': 'B'}, category='OPT', qualities=(119,))


def _other_rows():
    rows = {}
    for table in fragments('B.120'):
        start = next(cell['column_no'] for cell in table['cells'] if cell['row_no'] == 0 and equal(cell['text'], 'GB'))
        for row in range(1, table['rows']):
            if table['grid'][row, start]['kind'] == 'number':
                key = path(table, row, tuple(range(start)))
                if key in rows:
                    raise ValueError('表 B.120 存在重复种类路径')
                rows[key] = (table, row)
    return rows


OTHER_ROWS = _other_rows()
CABLE = next(kind for kind in OTHER_ROWS if kind.startswith('电缆'))
BREAKER = next(kind for kind in OTHER_ROWS if kind.startswith('断路器'))


def _other(p, environment, _ambient):
    table, row = OTHER_ROWS[p['kind']]
    candidates = [(table, table['grid'][row, cell['column_no']]) for cell in table['cells']
                  if cell['row_no'] == 0 and equal(cell['text'], environment)]
    f = Factors()
    rate = f.add('λNP[表列单位]', *result('B.120', candidates))
    if p['kind'] == CABLE:
        rate *= f.add('L[km/根]', p['length_km'], '表 B.120：电缆按每根每千米列率，数量另计根数')
    elif p['kind'] == BREAKER:
        rate *= f.add('电极数', p['pole_count'], '表 B.120：断路器每个电极列率，数量另计断路器数')
    return f.result(rate)


MODELS['国产其他电子元器件（非工作详细）'] = spec(
    (choice('kind', '元器件种类', tuple(OTHER_ROWS)),
     Input('length_km', '每根电缆长度 [km]', when=('kind', (CABLE,))),
     Input('pole_count', '每只断路器电极数', integer=True, min_value=1, presets=(1, 2, 3, 4),
           when=('kind', (BREAKER,)), constraint_hint='表 B.120 断路器电极数须为正整数（≥ 1）')),
    'λNP = 表 B.120 对应环境表值 [电缆按千米、断路器按电极换算]', 'B.2.25', (120,), _other,
    {'kind': next(iter(OTHER_ROWS))}, category='OTHER',
    note='表 B.120 已给出对应环境的非工作失效率，不再附加环境、质量或成熟系数。')
