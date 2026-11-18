"""Domestic working-state models, clauses 5.11–5.24.

Numerical table entries are read from the authoritative reference, including
continuations and merged cells. Missing entries and overlapping ranges fail
closed; the code does not interpolate those entries.
"""

import math
import re
from dataclasses import replace
from functools import partial

from ..model_spec import Input, ModelSpec, choice, quality, stress, temperature
from ..numeric import number_text
from ..reference_data import (
    compact,
    equal,
    fragments,
    matrix_value,
    paired_value,
    result,
    row_value,
    stated_base_rate,
    temp_matrix_value,
)
from ._active_tables import int_or_float_options
from .common import Factors
from .table_choices import horizontal_choices as _horizontal_choices
from .table_choices import horizontal_value as below_value
from .table_choices import row_paths
from .table_choices import vertical_choices as _vertical_choices


def _q(table):
    return quality(_vertical_choices(table, 1))


def _h(table, key, label, **kwargs):
    return choice(key, label, _horizontal_choices(table), **kwargs)


def _v(table, key, label, column=0, **kwargs):
    return choice(key, label, _vertical_choices(table, column), **kwargs)


def _number(key, label, *, zero=False, integer=False, when=None, when_all=(), **kwargs):
    return Input(key, label, positive=not zero, integer=integer, when=when, when_all=when_all, **kwargs)


_CONTACTS_292 = int_or_float_options(sorted({
    float(cell['decimal_text'])
    for table in fragments(292)
    for cell in table['cells']
    if cell['column_no'] > 0 and cell['row_no'] % 2 == 0 and cell['kind'] == 'number'
}))
_CURRENTS_288 = int_or_float_options(sorted({
    float(cell['decimal_text'])
    for table in fragments(288)
    for row in range(2, table['rows'])
    if (cell := table['grid'].get((row, 0))) and cell['kind'] == 'number'
}))


def _value(p, key, *, minimum=0, maximum=None, strict=False):
    value = float(p[key])
    if not math.isfinite(value) or (minimum is not None and (value < minimum or (strict and value == minimum))):
        raise ValueError(f'{key} 不在有效范围内')
    if maximum is not None and value > maximum:
        raise ValueError(f'{key} 超出标准允许范围 {maximum}')
    return value


def _spec(fields, formula, section, calculator, *, category='OTHER', quality_table=None,
          note='', example=None, example_temperature=40):
    return ModelSpec(
        fields, formula, f'GJB/Z 299D—2024 {section}',
        note,
        category, section, calculator,
        (quality_table,) if quality_table else (), example or {}, example_temperature,
    )


def _simple(p, environment, ambient_temperature_C, *, base, env, q, extras=()):
    factors = Factors()
    if isinstance(base, tuple):
        table, key = base
        factors.add('λb', *below_value(table, p[key]))
    else:
        factors.add('λb', *stated_base_rate(base))
    factors.env(env, environment)
    factors.quality(q, p['quality'])
    for symbol, table, key in extras:
        factors.add(symbol, *below_value(table, p[key]))
    return factors.result()


def _relay(p, environment, ambient_temperature_C):
    factors = Factors()
    table = {'阻性': 260, '感性': 261, '灯': 262}[p['load_type']]
    if p['rated_temperature_C'] == 125:
        table += 3
    if ambient_temperature_C > p['rated_temperature_C']:
        raise ValueError('工作环境温度不得超过继电器额定温度')
    stress_key = 'lamp_stress_ratio' if p['load_type'] == '灯' else 'stress_ratio'
    factors.add('λb', *temp_matrix_value(table, ambient_temperature_C, p[stress_key], label_column=0))
    factors.env(266, environment)
    factors.quality(267, p['quality'])
    factors.add('πC1', *row_value(268, p['contacts'], label_column=0))
    current = _value(p, 'rated_current_A', strict=True)
    band = 'I≤1' if current <= 1 else '1<I<10' if current < 10 else '10≤I<50' if current < 50 else 'I≥50'
    factors.add('πr', *below_value(269, band))
    frequency = _value(p, 'operations_per_hour')
    if frequency > _value(p, 'design_operations_per_hour', strict=True):
        raise ValueError('动作速率超过继电器设计极限，表 270 不适用')
    factors.add('πCYC', frequency / 10 if frequency > 1 else 0.1, '表 270：f>1 时 f/10，否则 0.1')
    factors.add('πA', *row_value(271, p['application']))
    factors.add('πC', *row_value(272, p['structure'], label_column=0))
    factors.add('πP', *below_value(273, p['enclosure']))
    return factors.result()


DETAILED = '组成元器件失效率求和'
APPROXIMATE = '信息不充分时近似预计'
_APPROX = ('method', (APPROXIMATE,))
_DETAIL = ('method', (DETAILED,))


def sum_component_rates(components):
    """Sum independently calculated constituent rates, with positive counts."""
    values = []
    for rate, count in components:
        if not math.isfinite(rate) or rate < 0 or not math.isfinite(count) or count <= 0 or int(count) != count:
            raise ValueError('组成元器件必须给出有限非负失效率及正整数数量')
        values.append(rate * count)
    if not values:
        raise ValueError('组成元器件不能为空')
    return math.fsum(values)


def _check_composition_condition(p, environment, ambient_temperature_C):
    if not equal(p['source_environment'], environment) or p['source_temperature_C'] != ambient_temperature_C:
        raise ValueError('组成元器件失效率的适用环境或温度不匹配；请在当前工况重新计算组成电路')


def _composition(p, section, environment, ambient_temperature_C):
    _check_composition_condition(p, environment, ambient_temperature_C)
    factors = Factors()
    factors.add('ΣNiλpi', _value(p, 'constituent_rate'), f'{section}：当前环境条件下所有组成元器件的失效率之和')
    return factors.result()


def _solid_relay(p, environment, ambient_temperature_C):
    if p['method'] == DETAILED:
        return _composition(p, '5.11.2', environment, ambient_temperature_C)
    factors = Factors()
    factors.add('λb', *stated_base_rate('5.11.2'))
    factors.env(274, environment)
    factors.quality(275, p['quality'])
    factors.add('πL', *below_value(276, p['maturity']))
    count = _value(p, 'output_circuits', strict=True)
    if int(count) != count:
        raise ValueError('输出电路数必须为正整数')
    factors.add('πC2', count * 1.1 ** (count - 1), '5.11.2：N×1.1^(N−1)')
    factors.add('πP', *below_value(277, p['enclosure']))
    current = p['current_ratio']
    derated = p['derated_current_ratio']
    if current > derated:
        raise ValueError('实际输出电流超过当前环境温度允许的最大输出电流')
    factors.add('πSI', *matrix_value(278, '≤0.1' if current <= 0.1 else current, derated, label_column=0))
    voltage = p['transient_voltage_ratio']
    factors.add('πSV', *below_value(279, '≤0.5' if voltage <= 0.5 else voltage))
    return factors.result()


def _switch(p, environment, ambient_temperature_C):
    factors = Factors()
    mechanism = factors.add('λb1', *below_value(280, p['switch_type']))
    if p['switch_type'] in ('微动式', '行程式'):
        contact_rate = factors.add('λb2', 0, '5.12：微动开关和行程开关 λb2=0')
    else:
        count = _value(p, 'active_contacts', strict=True)
        if int(count) != count:
            raise ValueError('有源接点数必须为正整数')
        contact_rate = factors.add('λb2', 0.002 * (count - 1), '表 281：0.002(N−1)；N 的定义见表 282')
    env = factors.env(283, environment)
    grade = factors.quality(284, p['quality'])
    load = factors.add('πL', *matrix_value(285, p['stress_ratio'], p['load_type'], label_column=0))
    frequency = _value(p, 'operations_per_day')
    cycles = factors.add('πCYC', frequency / 10 if frequency > 1 else 0.1, '表 286：f>1 时 f/10，否则 0.1')
    return factors.result((mechanism + contact_rate) * env * grade * load * cycles)


_LOW_FREQUENCY = ('connector_type', ('低频连接器',))


def _material_temperature_limit(material_class):
    limits = []
    for table in fragments(289):
        for row in range(1, table['rows']):
            if compact(table['grid'][row, 0]['text']) == material_class.removesuffix('类'):
                match = re.search(r'～(\d+)$', compact(table['grid'][row, 2]['text']))
                if match:
                    limits.append(float(match[1]))
    if not limits:
        raise ValueError('表 289 未列出当前绝缘安装板材料类别')
    return max(limits)


def _connector_temperature_rise(p, factors):
    if p['temperature_method'] == '实际温升':
        return factors.add('ΔT', _value(p, 'temperature_rise_C'), '绝缘安装板实际温升')
    if p['temperature_method'] != '按接触件电流查表':
        raise ValueError('未知的绝缘安装板温升确定方式')
    current = _value(p, 'contact_current_A')
    if current == 0:
        return factors.add('ΔT', 0.0, '表 288：接触件电流为 0 A，温升为 0 ℃')
    max_current = _CURRENTS_288[-1]
    if current > max_current:
        raise ValueError(f'每个接触件电流 {number_text(current)} A 超出表 288 上限 {number_text(max_current)} A')
    selected = next(tick for tick in _CURRENTS_288 if tick >= current)
    rise, source = matrix_value(288, selected, p['contact_diameter'], label_column=0)
    if selected > current:
        source += f'；电流向上取 {number_text(selected)} A 档'
    return factors.add('ΔT', rise, source)


def _connector_contacts(p, factors):
    count = _value(p, 'contacts', strict=True)
    if int(count) != count:
        raise ValueError('实际使用接触件数必须为正整数')
    n = int(count)
    max_contacts = _CONTACTS_292[-1]
    if n > max_contacts:
        raise ValueError(f'实际使用接触件数 {n} 超出表 292 上限 {max_contacts}')
    selected = next(tick for tick in _CONTACTS_292 if tick >= n)
    value, source = below_value(292, selected)
    if selected > n:
        source += f'；接触件数向上取 {selected} 档'
    return factors.add('πP', value, source)


def _connector(p, environment, ambient_temperature_C):
    factors = Factors()
    connector_type = p['connector_type']
    unit = p['counting_unit']
    if connector_type == '低频连接器':
        if unit not in ('一对', '单个'):
            raise ValueError(f'低频连接器计量对象仅支持“一对”或“单个”（当前为“{unit}”）')
        share = 1 if unit == '一对' else 0.5
        rise = _connector_temperature_rise(p, factors)
        operating = ambient_temperature_C + rise
        material_max = _value(p, 'material_max_temperature_C', strict=True)
        if material_max > _material_temperature_limit(p['material_class']):
            raise ValueError(
                '所用材料最高允许温度超出表 289 范围：'
                'Ⅰ类最高 250 ℃（表 289），Ⅱ类最高 120 ℃（表 289，常用 80/90/100/120 ℃）'
            )
        if operating < -55 or operating > material_max:
            raise ValueError('工作温度超出所用绝缘安装板材料的温度范围（表 289：−55 ℃～所用材料最高允许温度）')
        base = factors.add('λb', *temp_matrix_value(287, operating, p['material_class'], label_column=0))
    elif connector_type == '射频同轴连接器':
        if unit not in ('一对', '插头', '插座'):
            raise ValueError(f'射频同轴连接器计量对象仅支持“一对”、“插头”或“插座”（当前为“{unit}”）')
        share = {'一对': 1, '插头': 0.4, '插座': 0.6}[unit]
        base = factors.add('λb', 0.0026, '5.13：射频同轴连接器基本失效率为 0.0026')
    else:
        raise ValueError(f'未知的连接器类型：{connector_type}')
    env = factors.env(290, environment)
    grade = factors.quality(291, p['quality'])
    contacts = _connector_contacts(p, factors)
    frequency = _value(p, 'mating_per_1000h')
    band = 'f≤0.5' if frequency <= 0.5 else '0.5<f<5' if frequency < 5 else '5≤f<50' if frequency < 50 else 'f≥50'
    mating = factors.add('πK', *below_value(293, band))
    structure = factors.add('πC', *below_value(294, p['socket_structure']))
    factors.add('计量比例', share, '5.13：整对 1，低频单个 0.5，射频插头 0.4，射频插座 0.6')
    return factors.result(base * env * grade * contacts * mating * structure * share)


def _motor(p, environment, ambient_temperature_C, *, synchro=False):
    if _value(p, 'insulation_temperature_C') < 105 or _value(p, 'case_temperature_rise_C') > 40:
        raise ValueError('模型要求绝缘等级不低于 105℃、金属外壳温升不大于 40℃')
    if p['maintenance'] != '定期检修维护和更换电刷':
        raise ValueError('电机模型要求在使用期内定期检修、维护和更换电刷')
    factors = Factors()
    speed = _value(p, 'speed_rpm')
    if synchro:
        factors.add('λb', 0.15 if speed <= 10 else 0.15 * speed / 10, '表 298：f≤10 时 0.15；否则 0.15f/10')
        env, grade = 299, 300
    else:
        state = ('整流式' if p['drive_type'] == '整流式' else
                 '高速>10000r/min' if speed > 10000 else
                 '中速≥1000r/min≤10000r/min' if speed >= 1000 else '低速<1000r/min')
        factors.add('λb', *matrix_value(295, p['motor_type'], state, label_column=0))
        env, grade = 296, 297
    factors.env(env, environment)
    factors.quality(grade, p['quality'])
    return factors.result()


def _totalizer(p, environment, ambient_temperature_C):
    factors = Factors()
    factors.add('λb', *below_value(301, p['totalizer_type']))
    factors.env(302, environment)
    factors.quality(303, p['quality'])
    ratio = _value(p, 'temperature_ratio', minimum=None, maximum=1)
    band = 'S≤0.5' if ratio <= .5 else '0.5<S≤0.6' if ratio <= .6 else '0.6<S≤0.8' if ratio <= .8 else '0.8<S≤1.0'
    factors.add('πT', *below_value(304, band))
    return factors.result()


def _smt_adjustment(product):
    if not math.isfinite(product) or product < 0:
        raise ValueError('λbLC 不在表 311 区间内')
    if product <= .30:
        return row_value(311, '0～0.30', label_column=0)
    for printed_low, high, label in ((.31, .6, '0.31～0.6'), (.61, .8, '0.61～0.8'), (.81, .9, '0.81～0.90')):
        if product <= high:
            value, source = row_value(311, label, label_column=0)
            if product < printed_low:
                source += f'；λbLC={number_text(product)} 向上归入 {label} 档'
            return value, source
    return row_value(311, '＞0.90', label_column=0)


def _smt(p, environment, ambient_temperature_C):
    factors = Factors()
    if p['cte_method'] == '表列缺省值':
        board_cte, board_source = paired_value(312, p['board_material'])
        package_cte, package_source = row_value(314, p['package_material'], label_column=0)
    else:
        board_cte, board_source = _value(p, 'board_cte'), '电路板底板实测 CTE，ppm/℃'
        package_cte, package_source = _value(p, 'package_cte'), '封装材料实测 CTE，ppm/℃'
    factors.add('αS', board_cte, board_source)
    factors.add('αCC', package_cte, package_source)
    if p['temperature_span_method'] == '表列缺省值':
        delta_t, source = below_value(313, environment)
    else:
        delta_t, source = _value(p, 'temperature_span_C'), '使用环境实际极值温差'
    factors.add('ΔT', delta_t, source)
    height = .2032 if p['lead_structure'] in ('J或S型引线', '鸥翼型引线') else _value(p, 'joint_height_mm', strict=True)
    distance = _value(p, 'distance_mm', strict=True)
    rise = _value(p, 'thermal_resistance_C_per_W') * _value(p, 'power_W')
    factors.add('TRISE', rise, '式(79)：Rth(j-c)×P')
    cycles = _value(p, 'cycles_per_hour')
    cycle_factor = cycles if cycles <= .2 else .25 if cycles <= .6 else .4 if cycles <= 1 else 1.5 if cycles <= 5 else 5
    factors.add('CR', cycle_factor, '表 315：按实际温度循环次数/小时分段计算')
    leads = factors.add('πC', *row_value(316, p['lead_structure'], label_column=0))
    strain = distance / height * abs(board_cte * delta_t - package_cte * (delta_t + rise)) * 1e-6
    base = strain ** 2.26 / 27.7 * cycle_factor * leads
    factors.add('λb', base, '式(78)')
    adjustment = factors.add('A', *_smt_adjustment(base * _value(p, 'design_life_h', strict=True)))
    return factors.result(adjustment * base)


def evaluate_smt_board(candidates, environment, ambient_temperature_C):
    """Evaluate every connection type and return the weakest one, never a sum."""
    results = [_smt(p, environment, ambient_temperature_C) for p in candidates]
    if not results:
        raise ValueError('必须给出电路板上全部 SMT 连接类别')
    index, weakest = max(enumerate(results), key=lambda item: item[1].rate)
    candidate_rates = tuple((f'连接类别 {i + 1} λSMT', evaluation.rate,
                             '5.15.3：按式（77）～（79）独立计算本类连接')
                            for i, evaluation in enumerate(results))
    return replace(weakest, factors=candidate_rates + weakest.factors + (
        ('最薄弱连接序号', index + 1, '5.15.3：全部连接类别中失效率最大者；并列时取首项'),
    ))


def _smt_board(p, environment, ambient_temperature_C):
    candidates = p['connections']
    calculation = evaluate_smt_board(candidates, environment, ambient_temperature_C)
    return replace(calculation, factors=calculation.factors + (
        ('连接类别数', len(candidates), '5.15.3：计算全部连接类别，仅取最大失效率，不按类别数累加'),
    ))


def _filter(p, environment, ambient_temperature_C):
    factors = Factors()
    factors.add('λb', *below_value(325, p['filter_type']))
    factors.env(326, environment)
    factors.quality(327, p['quality'])
    frequency = _value(p, 'frequency_MHz', strict=True)
    if frequency <= .2:
        factors.add('πf', *below_value(328, '≤0.2'))
    else:
        factors.add('πf', 1.3 * frequency ** .23, '表 328 注 a、式(84)：1.3f^0.23，f 单位 MHz')
    return factors.result()


def _lc_filter(p, environment, ambient_temperature_C):
    if p['method'] == DETAILED:
        return _composition(p, '5.18.3', environment, ambient_temperature_C)
    return _simple(p, environment, ambient_temperature_C, base=(329, 'filter_type'), env=330, q=331)


def _lamp(p, environment, ambient_temperature_C):
    factors = Factors()
    factors.add('λb', *below_value(335, p['lamp_type']))
    factors.env(336, environment)
    utilization = _value(p, 'utilization', maximum=1)
    label = '＜0.1' if utilization < .1 else '0.1～0.8' if utilization <= .8 else '＞0.8～1'
    factors.add('πU', *below_value(337, label))
    voltage = _value(p, 'rated_voltage_V', strict=True)
    if voltage <= 5:
        factors.add('πV', *below_value(338, '≤5'))
    else:
        factors.add('πV', .125 * voltage ** 1.29, '表 338 注 a、式(88)：0.125V^1.29')
    return factors.result()


def _laser(p, environment, ambient_temperature_C):
    factors = Factors()
    if p['laser_type'] == '密封二氧化碳':
        current = _value(p, 'tube_current_mA', minimum=10, maximum=150)
        factors.add('λb', 10 * current - 90, '表 339：10I−90，10≤I≤150 mA')
    else:
        factors.add('λb', *below_value(339, p['laser_type']))
    factors.env(340, environment)
    if p['operation'] == '连续':
        label = '连续'
    else:
        power = _value(p, 'output_power_mW')
        if power == 150:
            raise ValueError('表 341 在 150 mW 同时标注两档，请选择明确功率档位')
        label = '输出功率≤90mW' if power <= 90 else '90＜输出功率≤150mW' if power < 150 else '输出功率≥150mW'
    factors.add('πA', *below_value(341, label))
    factors.add('πL', *below_value(342, p['maturity']))
    return factors.result()


def _gyro(p, environment, ambient_temperature_C):
    _check_composition_condition(p, environment, ambient_temperature_C)
    factors = Factors()
    sensor = factors.add('λb1', .05, '5.22：敏感器件基本失效率 0.05')
    rear = factors.add('λb2', _value(p, 'rear_circuit_rate'), '式(91)：后部电路 ΣNiλpi，须对应当前环境条件')
    complexity = factors.add('πC', *below_value(343, p['axes']))
    structure = factors.add('πk', *below_value(344, p['beam_length_mm']))
    application = factors.add('πA', *below_value(345, p['application']))
    env = factors.env(346, environment)
    maturity = factors.add('πL', *below_value(347, p['maturity']))
    return factors.result((sensor + rear) * complexity * structure * application * env * maturity)


_OPTICAL_CORES_348 = (1, 2, 4, 6, 8)


def _optical_connector(p, environment, ambient_temperature_C):
    factors = Factors()
    raw = p['cores']
    note = ''
    if raw == 'N≥10':
        axis = 'N≥10'
    else:
        cores = _value(p, 'cores', strict=True)
        if int(cores) != cores:
            raise ValueError('芯数必须为正整数')
        n = int(cores)
        if n >= 10:
            axis = 'N≥10'
        elif n in _OPTICAL_CORES_348:
            axis = n
        else:
            axis = next((tick for tick in _OPTICAL_CORES_348 if tick >= n), 'N≥10')
            note = f'；芯数向上取 {axis} 档'
    base_val, base_source = below_value(348, axis)
    factors.add('λb', base_val, base_source + note)
    factors.add('πm', *below_value(349, p['transmission_mode']))
    factors.add('πp', *below_value(350, p['end_face']))
    factors.add('πL', *below_value(351, p['coupling']))
    factors.quality(352, p['quality'])
    factors.env(353, environment)
    return factors.result()


OTHER_ROWS = row_paths(354, lambda table: 4 if table['columns'] == 23 else 2)
_CABLE = next(label for label in OTHER_ROWS if label.startswith('电缆'))
_BREAKERS = tuple(label for label in OTHER_ROWS if label.startswith('断路器'))


def _other(p, environment, ambient_temperature_C):
    table, row, label_columns = OTHER_ROWS[p['device_type']]
    columns = [col for col in range(label_columns, table['columns'])
               if equal(table['grid'][0, col]['text'], environment)]
    factors = Factors()
    rate = factors.add('λp', *result(354, [(table, table['grid'][row, col]) for col in columns]))
    if p['device_type'] == _CABLE:
        multiplier = factors.add('长度/km', _value(p, 'length_km', strict=True), '表 354 电缆按千米/根计；数量填写根数')
    elif p['device_type'] in _BREAKERS:
        multiplier = _value(p, 'poles', strict=True)
        if int(multiplier) != multiplier:
            raise ValueError('断路器电极数必须为正整数')
        factors.add('电极数', multiplier, '表 354 断路器按每个电极计；数量填写断路器只数')
    else:
        multiplier = 1
    return factors.result(rate * multiplier)


_MOTOR_COMMON = (
    _number('insulation_temperature_C', '绝缘等级温度（℃）', min_value=105,
            presets=((105, '105 ℃（A 级）'), (120, '120 ℃（E 级）'), (130, '130 ℃（B 级）'),
                     (155, '155 ℃（F 级）'), (180, '180 ℃（H 级）')),
            constraint_hint='表 295/298 注：电机绝缘等级不得低于 105 ℃'),
    _number('case_temperature_rise_C', '金属外壳温升（℃）', zero=True, min_value=0, max_value=40,
            presets=(10, 20, 30, 40), constraint_hint='表 295/298 注：金属外壳温升不得大于 40 ℃'),
    choice('maintenance', '使用期内维护', ('定期检修维护和更换电刷', '不满足维护要求')),
)
_MOTOR_EXAMPLE = dict(speed_rpm=1000, insulation_temperature_C=105,
                      case_temperature_rise_C=40, maintenance='定期检修维护和更换电刷', quality='B2')
_METHOD_FIELDS = (
    choice('method', '预计方法', (DETAILED, APPROXIMATE)),
    Input('constituent_rate', '组成元器件 ΣNiλpi（10^-6/h）', '组成预计', positive=False, when=_DETAIL),
    choice('source_environment', '组成计算适用环境', _horizontal_choices(274), when=_DETAIL),
    Input('source_temperature_C', '组成计算适用温度（℃）', positive=False, allow_negative=True, when=_DETAIL),
)
_CLASS_1_TEMP_PRESETS = (
    (250, '250 ℃ · 聚酰亚胺/PTFE/PEEK 等'),
    (200, '200 ℃ · 硅橡胶/氟橡胶/PPS/环氧等'),
    (180, '180 ℃ · 酚醛玻纤塑料'),
    (170, '170 ℃ · 聚砜/聚醚砜/聚醚酰亚胺'),
)
_CLASS_2_TEMP_PRESETS = (
    (120, '120 ℃ · 增强尼龙/PC/聚酯'),
    (100, '100 ℃ · 普通酚醛/PPO/PP'),
    (90, '90 ℃ · 聚氯丁烯'),
    (80, '80 ℃ · PVC'),
)


MODELS = {
    '国产机电继电器': _spec([
        temperature(tuple(range(0, 126, 5))), choice('rated_temperature_C', '额定温度（℃）', (85, 125)),
        choice('load_type', '触点负载类型', ('阻性', '感性', '灯')),
        stress(
            values=(.05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1),
            source='表 260～265',
            when=('load_type', ('阻性', '感性')),
            choices_by=('load_type', (
                ('阻性', (.05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1)),
                ('感性', (.05, .1, .2, .3, .4, .5, .6, .7)),
            )),
            limits_by=('load_type', (
                ('阻性', (0, 1, '表 260/263 阻性负载电应力上限 1.0', False, True)),
                ('感性', (0, .7, '表 261/264 感性负载电应力上限 0.7', False, True)),
            )),
            constraint_hint='表 260～264：阻性负载上限 1.0，感性负载上限 0.7',
        ),
        stress('lamp_stress_ratio', '灯负载电应力比 S', values=(.05, .1, .15, .2, .3, .4), source='表 262、265', when=('load_type', ('灯',))),
        _q(267), choice('contacts', '有效传导触点数', (1, 2, 3, 4, 6, 8, 12)),
        _number('rated_current_A', '触点负载额定电流（A）', min_value=0, min_inclusive=False,
                presets=((0.5, '0.5 A（I≤1）'), (5, '5 A（1<I<10）'), (10, '10 A（10≤I<50）'), (50, '50 A（I≥50）')),
                constraint_hint='表 269：按 I≤1、1<I<10、10≤I<50、I≥50 A 分档'),
        _number('operations_per_hour', '动作速率（次/时）', zero=True, min_value=0,
                presets=((0.5, '0.5（≤1，πCYC=0.1）'), (1, '1（≤1，πCYC=0.1）'),
                         (10, '10（f/10=1）'), (60, '60（f/10=6）')),
                constraint_hint='表 270：f≤1 取 0.1，f>1 取 f/10，且不得超过继电器设计极限'),
        _number('design_operations_per_hour', '设计极限动作速率（次/时）', min_value=0, min_inclusive=False,
                presets=(10, 100, 1000, 10000), constraint_hint='须 > 0，且实际动作速率不得超过此极限'),
        _v(271, 'application', '应用类别', 1), _v(272, 'structure', '结构形式'), _h(273, 'enclosure', '封装结构'),
    ], 'λp=λbπEπQπC1πrπCYCπAπCπP', '5.11.1', _relay, category='K', quality_table=267,
        example=dict(rated_temperature_C=85, load_type='阻性', stress_ratio=.2, quality='B2', contacts=2,
                     rated_current_A=5, operations_per_hour=1, design_operations_per_hour=100,
                     application='弱功率', structure='衔铁式', enclosure=_horizontal_choices(273)[1])),
    '国产固体继电器': _spec([
        *_METHOD_FIELDS, choice('quality', '质量等级', _vertical_choices(275, 1), when=_APPROX),
        _h(276, 'maturity', '成熟程度', when=_APPROX),
        _number('output_circuits', '输出电路数 N', integer=True, when=_APPROX, min_value=1,
                presets=(1, 2, 4, 8), constraint_hint='正整数 N≥1（5.11.2：N×1.1^(N−1)）'),
        _h(277, 'enclosure', '封装结构', when=_APPROX),
        stress('current_ratio', '实际输出电流/规格最大输出电流', source='表 278', when=_APPROX),
        choice('derated_current_ratio', '当前温度允许最大电流/规格最大电流', (2, 1.5, 1.2, 1, .9, .8, .7, .6, .5, .4, .3, .2, .1), when=_APPROX),
        stress('transient_voltage_ratio', '瞬态峰值电压/规格瞬态最大电压', (.5, .6, .8, 1), source='表 279', when=_APPROX),
    ], 'λp = ΣNiλpi [详细]；λp = λbπEπQπLπC2πPπSIπSV [近似]', '5.11.2', _solid_relay, category='K', quality_table=275,
        note='优先导入含组成元器件的工程模块详细计算。仅无元器件清单和电路图时使用近似模型；详细求和值必须对应当前工况。',
        example=dict(method=APPROXIMATE, quality='B2', maturity=_horizontal_choices(276)[0], output_circuits=2,
                     enclosure=_horizontal_choices(277)[1], current_ratio=.2, derated_current_ratio=1, transient_voltage_ratio=.5)),
    '国产开关': _spec([
        _h(280, 'switch_type', '开关类型'), _q(284),
        _number('active_contacts', '有源接点数 N', integer=True,
                when=('switch_type', ('旋转式', '钮子式', '按钮式', '滑动式')),
                min_value=1, presets=(1, 2, 4, 6, 8, 12), constraint_hint='表 281：正整数 N≥1（0.002(N−1)）'),
        choice('load_type', '触点负载', ('阻性', '感性', '灯')),
        stress(
            values=(.05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1),
            source='表 285',
            choices_by=('load_type', (
                ('阻性', (.05, .1, .2, .3, .4, .5, .6, .7, .8, .9, 1)),
                ('感性', (.05, .1, .2, .3, .4, .5, .6, .7)),
                ('灯', (.05, .1, .2, .3, .4)),
            )),
            limits_by=('load_type', (
                ('阻性', (0, 1, '表 285 阻性负载电应力上限 1.0', False, True)),
                ('感性', (0, .7, '表 285 感性负载电应力上限 0.7', False, True)),
                ('灯', (0, .4, '表 285 灯负载电应力上限 0.4', False, True)),
            )),
            constraint_hint='表 285：阻性上限 1.0，感性上限 0.7，灯负载上限 0.4',
        ),
        _number('operations_per_day', '开关速率（次/天）', zero=True, min_value=0,
                presets=((1, '1（≤1，πCYC=0.1）'), (10, '10（f/10=1）'), (100, '100（f/10=10）')),
                constraint_hint='表 286：f≤1 取 0.1，f>1 取 f/10（次/天）'),
    ], 'λp=(λb1+λb2)πEπQπLπCYC', '5.12', _switch, category='S', quality_table=284,
        example=dict(switch_type='旋转式', quality='B2', active_contacts=4, load_type='阻性', stress_ratio=.2, operations_per_day=10)),
    '国产电连接器': _spec([
        choice('connector_type', '连接器类型', ('低频连接器', '射频同轴连接器')), _q(291),
        choice('counting_unit', '计量对象', ('一对', '单个', '插头', '插座'),
               choices_by=('connector_type', (('低频连接器', ('一对', '单个')), ('射频同轴连接器', ('一对', '插头', '插座')))),
               constraint_hint='低频连接器选一对/单个；射频同轴选一对/插头/插座'),
        choice('contacts', '实际使用接触件数', _CONTACTS_292, integer=True, min_value=1, max_value=4000,
               ceil_choices=True, constraint_hint='表 292：1～4000 正整数，非表列值向上取整至下一高档'),
        _h(294, 'socket_structure', '插孔结构'),
        _number('mating_per_1000h', '插拔速率（次/1000时）', zero=True, min_value=0,
                presets=((0, '0（f≤0.5，πK=1.0）'), (0.5, '0.5（f≤0.5，πK=1.0）'), (2, '2（0.5<f<5，πK=1.5）'),
                         (10, '10（5≤f<50，πK=2.0）'), (50, '50（f≥50，πK=3.0）')),
                constraint_hint='表 293：f≤0.5 取 1.0，0.5<f<5 取 1.5，5≤f<50 取 2.0，f≥50 取 3.0'),
        choice('material_class', '绝缘安装板材料类别', ('Ⅰ类', 'Ⅱ类'), when=_LOW_FREQUENCY),
        _number('material_max_temperature_C', '所用材料最高允许温度（℃）', when=_LOW_FREQUENCY,
                max_value=250,
                presets=(*_CLASS_1_TEMP_PRESETS, *_CLASS_2_TEMP_PRESETS),
                choices_by=('material_class', (('Ⅰ类', _CLASS_1_TEMP_PRESETS), ('Ⅱ类', _CLASS_2_TEMP_PRESETS))),
                limits_by=('material_class', (
                    ('Ⅰ类', (None, 250, '表 289 Ⅰ类常用材料最高允许温度 ≤ 250 ℃（表 287 工作温度上限 150 ℃）')),
                    ('Ⅱ类', (None, 120, '表 289 Ⅱ类常用材料最高允许温度 ≤ 120 ℃（常用 80/90/100/120 ℃）')),
                )),
                constraint_hint='表 289：Ⅰ类最高 250 ℃，Ⅱ类最高 120 ℃（常用 80/90/100/120 ℃）'),
        choice('temperature_method', '绝缘安装板温升', ('实际温升', '按接触件电流查表'), when=_LOW_FREQUENCY),
        _number('temperature_rise_C', '实际温升（℃）', zero=True,
                when=('temperature_method', ('实际温升',)), when_all=(_LOW_FREQUENCY,),
                min_value=0, presets=(0, 5, 10, 20, 30, 40),
                constraint_hint='实际温升 ΔT ≥ 0 ℃；环境温度 + ΔT 须在 −55 ℃～所用材料最高允许温度内'),
        choice('contact_current_A', '每个接触件电流（A）', (0, *_CURRENTS_288), '工作应力', positive=False,
               when=('temperature_method', ('按接触件电流查表',)), when_all=(_LOW_FREQUENCY,),
               min_value=0, max_value=80, ceil_choices=True,
               constraint_hint='表 288：0 A 温升为 0 ℃；0<I≤80 A 向上取整至下一电流档位'),
        choice('contact_diameter', '接触件直径（mm）', ('≤0.6', '0.8', '1', '1.5', '2', '2.5', '3.3', '4.1', '5.2', '≥6.5'), when=('temperature_method', ('按接触件电流查表',)), when_all=(_LOW_FREQUENCY,)),
    ], 'λp=λbπEπQπPπKπC [按计量对象分配]', '5.13', _connector, category='J', quality_table=291,
        note='表 287 温度为环境温度与绝缘安装板温升之和；绝缘材料最高允许温度按表 289 核查（Ⅰ类最高 250 ℃，Ⅱ类最高 120 ℃），混合结构请分别计算各连接器。',
        example=dict(connector_type='低频连接器', quality='B2', counting_unit='一对', contacts=4, socket_structure='簧片',
                     mating_per_1000h=0, material_class='Ⅰ类', material_max_temperature_C=200, temperature_method='实际温升', temperature_rise_C=0)),
    '国产普通电动机和风机': _spec([
        choice('motor_type', '种类', ('普通电动机', '风机')),
        choice('drive_type', '使用状态', ('按转速', '整流式'),
               choices_by=('motor_type', (('普通电动机', ('按转速', '整流式')), ('风机', ('按转速',)))),
               constraint_hint='表 295：风机仅支持按转速查表（整流式为“—”）'),
        _number('speed_rpm', '转速（r/min）', zero=True, min_value=0,
                presets=((500, '500 r/min（低速 <1000）'), (3000, '3000 r/min（中速 1000～10000）'),
                         (12000, '12000 r/min（高速 >10000）')),
                constraint_hint='表 295：低速 <1000、中速 1000～10000、高速 >10000 r/min'),
        *_MOTOR_COMMON, _q(297),
    ], 'λp=λbπEπQ', '5.14.1', _motor, category='M', quality_table=297,
        example=dict(_MOTOR_EXAMPLE, motor_type='普通电动机', drive_type='按转速')),
    '国产自整角机和旋转变压器': _spec([
        _number('speed_rpm', '转速（r/min）', zero=True, min_value=0,
                presets=((5, '5（f≤10，λb=0.15）'), (10, '10（f≤10，λb=0.15）'), (20, '20（f>10，0.15f/10）')),
                constraint_hint='表 298：f≤10 取 0.15，f>10 按 0.15×f/10 计算'),
        *_MOTOR_COMMON, _q(300),
    ], 'λp=λbπEπQ', '5.14.2', partial(_motor, synchro=True),
        category='M', quality_table=300, note='适用于低速、轻载条件；绝缘等级≥105℃，外壳温升≤40℃，定期维护。',
        example=dict(_MOTOR_EXAMPLE, speed_rpm=10, quality='B')),
    '国产时间累计器': _spec([
        _h(301, 'totalizer_type', '种类'), _q(303),
        Input(
            'temperature_ratio',
            '工作温度/额定温度',
            '工作应力',
            positive=False,
            allow_negative=True,
            max_value=1.0,
            presets=(
                (-0.5, '-0.5（S≤0.5，πT=0.8）'),
                (0, '0（0 ℃，S≤0.5，πT=0.8）'),
                (0.5, '0.5（S≤0.5，πT=0.8）'),
                (0.6, '0.6（0.5<S≤0.6，πT=1.0）'),
                (0.8, '0.8（0.6<S≤0.8，πT=1.2）'),
                (1.0, '1.0（0.8<S≤1.0，πT=1.7）'),
            ),
            constraint_hint='表 304：温度比 S = 工作温度/额定温度 ≤ 1.0（0 ℃ 及负温均满足 S≤0.5，取 πT=0.8）',
        ),
    ], 'λp=λbπEπQπT', '5.14.3', _totalizer, category='M', quality_table=303,
        example=dict(totalizer_type='普通(交直流)', quality='B', temperature_ratio=.5)),
    '国产SMT互连': _spec([
        _number('distance_mm', '中心至最远焊接点距离 d（mm）'), _v(316, 'lead_structure', '引线结构'),
        _number('joint_height_mm', '无管脚焊接点高度 h（mm）', when=('lead_structure', ('无引线', 'BGA'))),
        choice('cte_method', '热膨胀系数来源', ('表列缺省值', '实际值')),
        choice('board_material', '电路板底板材料', tuple(dict.fromkeys(compact(c['text']) for t in fragments(312) for c in t['cells'] if c['column_no'] % 2 == 0 and c['row_no'] > 0)), when=('cte_method', ('表列缺省值',))),
        _v(314, 'package_material', '封装材料', when=('cte_method', ('表列缺省值',))),
        _number('board_cte', '底板 CTE（ppm/℃）', zero=True, when=('cte_method', ('实际值',))),
        _number('package_cte', '封装 CTE（ppm/℃）', zero=True, when=('cte_method', ('实际值',))),
        choice('temperature_span_method', '使用环境温差来源', ('表列缺省值', '实际值')),
        _number('temperature_span_C', '环境极值温差 ΔT（℃）', zero=True, when=('temperature_span_method', ('实际值',))),
        _number('thermal_resistance_C_per_W', '热阻 Rth(j-c)（℃/W）', zero=True), _number('power_W', '功耗 P（W）', zero=True),
        _number('cycles_per_hour', '温度循环次数（次/时）', zero=True), _number('design_life_h', '设备设计寿命周期 LC（h）'),
    ], 'λSMT=Aλb；λb=[(d/h)|αSΔT−αCC(ΔT+TRISE)|×10^-6]^2.26 CRπC/27.7', '5.15.3', _smt,
        category='PCB', note='每条代表一块板的最薄弱 SMT 连接，数量为电路板数。必须比较板上所有连接类别并取最大值；不得按焊点数累加。未考虑焊接缺陷。',
        example=dict(distance_mm=10, lead_structure='鸥翼型引线', cte_method='表列缺省值',
                     board_material='环氧树脂覆铜箔板', package_material='塑料', temperature_span_method='表列缺省值',
                     thermal_resistance_C_per_W=0, power_W=0, cycles_per_hour=.1, design_life_h=10000)),
    '国产磁性器件': _spec([_h(317, 'magnetic_type', '种类'), _q(319)], 'λp=λbπEπQ', '5.16',
        partial(_simple, base=(317, 'magnetic_type'), env=318, q=319), category='MAG', quality_table=319,
        example=dict(magnetic_type=_horizontal_choices(317)[0], quality='B2')),
    '国产振荡器': _spec([_q(321), _h(322, 'control', '控制方式')], 'λp=λbπEπQπC', '5.17.1',
        partial(_simple, base='5.17.1', env=320, q=321, extras=(('πC', 322, 'control'),)), category='Y', quality_table=321,
        example=dict(quality='B2', control='压控')),
    '国产谐振器': _spec([_q(324)], 'λp=λbπEπQ', '5.17.2', partial(_simple, base='5.17.2', env=323, q=324),
        category='Y', quality_table=324, example=dict(quality='B')),
    '国产压电陶瓷介电石英晶体和机械滤波器': _spec([
        _h(325, 'filter_type', '滤波器类型'), _q(327),
        _number('frequency_MHz', '频率 f（MHz）', min_value=0, min_inclusive=False,
                presets=((0.1, '0.1 MHz（≤0.2，πf=0.90）'), (1, '1 MHz（πf=1.3）'),
                         (10, '10 MHz'), (50, '50 MHz')),
                constraint_hint='表 328：f≤0.2 MHz 取 0.90，f>0.2 MHz 按 1.3×f^0.23 计算'),
    ], 'λp=λbπEπQπf；πf=1.3f^0.23', '5.18.2', _filter, category='FL', quality_table=327,
        example=dict(filter_type='石英晶体', quality='B', frequency_MHz=1)),
    '国产LC滤波器': _spec([
        *_METHOD_FIELDS, _h(329, 'filter_type', '组成类型', when=_APPROX),
        choice('quality', '质量等级', _vertical_choices(331, 1), when=_APPROX),
    ], 'λp = ΣNiλpi [详细]；λp = λbπEπQ [近似]', '5.18.3', _lc_filter, category='FL', quality_table=331,
        note='优先导入包含组成元器件的工程模块详细求和；仅信息不充分时使用近似模型。详细求和值必须对应当前工况。',
        example=dict(method=APPROXIMATE, filter_type='分立电感电容', quality='B')),
    '国产电池': _spec([_h(332, 'battery_type', '种类'), _q(334)], 'λp=λbπEπQ', '5.19',
        partial(_simple, base=(332, 'battery_type'), env=333, q=334), category='BT', quality_table=334,
        example=dict(battery_type='锂离子蓄电池', quality='B2')),
    '国产灯': _spec([
        _h(335, 'lamp_type', '种类'),
        _number('utilization', '照明小时/设备工作小时', zero=True, min_value=0, max_value=1,
                presets=((0.05, '0.05（＜0.1，πU=0.10）'), (0.5, '0.5（0.1～0.8，πU=0.72）'),
                         (0.9, '0.9（＞0.8～1，πU=1.0）')),
                constraint_hint='表 337：利用率 U 须满足 0 ≤ U ≤ 1'),
        _number('rated_voltage_V', '额定电压（V）', min_value=0, min_inclusive=False,
                presets=((3.3, '3.3 V（≤5，πV=1.0）'), (5, '5 V（≤5，πV=1.0）'),
                         (12, '12 V'), (24, '24 V'), (28, '28 V')),
                constraint_hint='表 338：V≤5 V 取 1.0，V>5 V 按 0.125×V^1.29 计算'),
    ], 'λp=λbπEπUπV', '5.20', _lamp, category='HL',
        example=dict(lamp_type='白炽灯', utilization=.5, rated_voltage_V=5)),
    '国产激光器': _spec([
        choice('laser_type', '激光器类型', (*_horizontal_choices(339), '密封二氧化碳')),
        _number('tube_current_mA', '管子电流 I（mA）', when=('laser_type', ('密封二氧化碳',)),
                min_value=10, max_value=150, presets=(10, 30, 50, 100, 150),
                constraint_hint='表 339 注：密封二氧化碳管子电流须满足 10 ≤ I ≤ 150 mA'),
        choice('operation', '应用方式', ('连续', '脉冲')),
        _number('output_power_mW', '脉冲输出功率（mW）', zero=True, when=('operation', ('脉冲',)),
                min_value=0,
                presets=((50, '50 mW（≤90 mW）'), (120, '120 mW（90＜P＜150 mW）'), (200, '200 mW（＞150 mW）')),
                constraint_hint='表 341 在 150 mW 同时标注两档，请避免填 150 mW'),
        _h(342, 'maturity', '成熟程度'),
    ], 'λp=λbπEπAπL', '5.21', _laser, category='LASER',
        example=dict(laser_type='密封二氧化碳', tube_current_mA=10, operation='连续', maturity=_horizontal_choices(342)[0])),
    '国产压电陀螺': _spec([
        Input('rear_circuit_rate', '后部电路 ΣNiλpi（10^-6/h）', '组成预计', positive=False),
        choice('source_environment', '后部电路计算适用环境', _horizontal_choices(274)),
        Input('source_temperature_C', '后部电路计算适用温度（℃）', positive=False, allow_negative=True),
        _h(343, 'axes', '轴数'), choice('beam_length_mm', '振梁长度（mm）', (30, 40, 60)),
        _h(345, 'application', '应用模式'), _h(347, 'maturity', '成熟程度'),
    ], 'λp=(0.05+ΣNiλpi)πCπkπAπEπL', '5.22', _gyro, category='GYRO',
        note='后部电路应依据第 5 章逐元器件计算并求和，输入当前工况下的组成计算结果，不能省略后部电路。',
        example=dict(rear_circuit_rate=.1, source_environment='GB', source_temperature_C=40, axes='双轴', beam_length_mm=40, application='角速度', maturity=_horizontal_choices(347)[0])),
    '国产光纤连接器': _spec([
        choice('cores', '芯数 N', (1, 2, 4, 6, 8, 'N≥10'), integer=True, min_value=1, ceil_choices=True,
               constraint_hint='表 348：1、2、4、6、8 或 N≥10（其他正整数向上取整，≥10 归入 N≥10 档）'),
        _h(349, 'transmission_mode', '传输模式'),
        _h(350, 'end_face', '端面形式'), _h(351, 'coupling', '连接模式'), _q(352),
    ], 'λp=λbπmπpπLπQπE', '5.23', _optical_connector, category='OPT', quality_table=352,
        example=dict(cores=2, transmission_mode='单模', end_face='PC', coupling='SC', quality='B')),
    '国产其他电子元器件': _spec([
        choice('device_type', '元器件种类', tuple(OTHER_ROWS)),
        _number('length_km', '每根电缆长度（km）', when=('device_type', (_CABLE,)),
                min_value=0, min_inclusive=False, presets=(0.01, 0.05, 0.1, 0.5, 1),
                constraint_hint='表 354：每根电缆长度（km），须 > 0'),
        _number('poles', '每只断路器电极数', integer=True, when=('device_type', _BREAKERS),
                min_value=1, presets=(1, 2, 3, 4), constraint_hint='表 354：每只断路器电极数，正整数 ≥ 1'),
    ], 'λp=表354工作失效率 [按长度或电极数计量]', '5.24', _other,
        note='表 354 已给出环境对应工作失效率，不再额外乘环境或质量系数。电缆按每根千米数，断路器按每只电极数换算。',
        example=dict(device_type='陶瓷共鸣器')),
}

# The public object is one complete board. Connection classes are candidates for
# its weakest interconnect, rather than separately summable project components.
SMT_CONNECTION_FIELDS = MODELS['国产SMT互连'].fields
SMT_CONNECTION_EXAMPLE = MODELS['国产SMT互连'].example
MODELS['国产SMT互连'] = replace(
    MODELS['国产SMT互连'],
    fields=(Input('connections', '电路板全部 SMT 连接类别', record_fields=SMT_CONNECTION_FIELDS),),
    calculator=_smt_board,
    formula='λSMT[整板] = maxᵢ(Aᵢλbᵢ)；λbᵢ 按式（78）计算',
    note='每条预计对象代表一块电路板。填写板上全部 SMT 连接类别，自动分别计算并取最大失效率；数量填写电路板数。',
    example={'connections': [SMT_CONNECTION_EXAMPLE]},
)
