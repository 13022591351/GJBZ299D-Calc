"""Baseline working-state stress models migrated into declarative ModelSpecs."""

import math
from dataclasses import replace

from ..model_spec import Input, ModelSpec
from ..numeric import number_text
from ..quality_labels import DEFINITIONS as QUALITY_METADATA
from ..reference_data import (
    below_value,
    conditions_value,
    range_selection,
    resolve_numeric_tick,
    row_value,
    stated_base_rate,
    stated_constant,
    temp_matrix_value,
)
from ..semiconductor import fet_conditions_exact, rated_power_band
from ..standard_data import lookup as standard_lookup
from ..standard_lookups import (
    DIGITAL_IC_PACKAGES,
    SUPPLY_VOLTAGE_CHOICES,
    SUPPLY_VOLTAGE_HINT,
    SUPPLY_VOLTAGE_PRESETS,
    bjt_stress,
    capacitor_base_rate,
    capacitor_stress,
    capacitor_temperature,
    column_numeric_ticks,
    digital_ic_complexity,
    digital_ic_package,
    digital_ic_temperature,
    diode_stress,
    discrete_stress,
    discrete_temperature,
    package_lead_choices_by,
    resistor_stress,
    resistor_temperature,
    supply_voltage_factor,
)
from ..stress import StressAdjustment, ceil_stress, trace_number
from ._active_tables import int_or_float_options, numeric_axes, paired_axes
from .common import Factors

# Choice labels are stored here; coefficient values come only from the reference database.
QUALITY_IC = ('A3', 'A4', 'A5', 'A6', 'B1', 'B2', 'C')
QUALITY_RES = ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C')
QUALITY_CAP = ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C')
QUALITY_IMPORT = ('S', 'R', 'P', 'M', '军规无可靠性指标', '民用或未知筛选')
QUALITY_INDUCTOR = QUALITY_IMPORT
QUALITY_DISCRETE = ('JANS', 'JANJ', 'JANTXV', 'JANTX', 'JAN', 'B-1（经筛选）', 'B-2（受控工业级）', 'C')
MATURITY = {
    '稳定生产': '符合相应的标准或技术条件，已稳定生产的产品',
    '质量尚未稳定': '质量尚未稳定的产品',
    '试制／新投产／重大工艺变化或复产': '试制品或新投产的初批次产品；设计或工艺上有重大变更；长期中断生产或生产线有重大变化',
}
PACKAGES = DIGITAL_IC_PACKAGES
RES_TYPES = ['普通膜（≤1 W）', '功率膜（>1 W）', '普通线绕', '精密线绕', '功率线绕', '片式膜']
# Mounting categories in table C.63.
CAP_TYPES = {
    '纸介': None,
    '纸膜复合': None,
    '薄膜': '薄膜电容器',
    '云母': '云母电容器',
    '瓷介': '瓷介电容器',
    '铝电解': '铝电解电容器',
    '固体钽（密封）': '固体钽电解电容器',
    '固体钽（非密封）': '固体钽电解电容器',
    '非固体钽': None,
    '氧化铌': '氧化铌电解电容器',
}
DIODE_TYPES = ('整流二极管', '开关二极管', '检波二极管', '瞬态抑制二极管')
BJT_TYPES = {'功率放大晶体管': '功率晶体管', '开关晶体管': '开关晶体管', '达林顿晶体管': '达林顿晶体管'}
DISCRETE_TYPES = {
    '电压调整／电压基准／电流调整二极管': '电压调整、电压基准及电流调整二极管',
    '硅场效应晶体管': '硅场效应晶体管',
    '单结晶体管': '单结晶体管',
    '闸流晶体管': '闸流晶体管',
}
S_GRID = tuple(round(n / 10, 1) for n in range(1, 10))
T_GRID = tuple(range(30, 121, 5))
T_INDUCTOR = tuple(range(30, 101, 5)) + (110, 120)  # PDF C.67 has no 105/115 °C columns.
IMPORTED_IC_PACKAGES = {
    '密封－双列直插（DIP）封装': 'C2_密封DIP',
    '密封－扁平（FP）封装': 'C2_密封FP',
    '密封－插接阵列（PGA/BGA/LGA和其他）封装': 'C2_密封插接阵列',
    '密封－表面安装（PLCC/LCC/SO和其他）封装': 'C2_密封表面安装',
    '密封－金属壳封装': 'C2_金属壳',
    '非密封－双列直插（DIP）封装': 'C2_非密封DIP',
    '非密封－扁平（FP）封装': 'C2_非密封FP',
    '非密封－插接阵列（PGA/BGA/LGA和其他）封装': 'C2_非密封插接阵列',
    '非密封－表面安装（PLCC/LCC/SO和其他）封装': 'C2_非密封表面安装',
}
IMPORTED_IC_QUALITY = ('S', 'B', 'B-1', 'B-2', 'B-3', 'C-1', 'C-2')
JUNCTION_GRID = tuple(range(25, 106, 2)) + tuple(range(110, 176, 5))


def _choice(key, label, values, section='器件参数', **kwargs):
    return Input(key, label, section, tuple(values), **kwargs)


def _quality(values):
    return _choice('quality', '质量等级', values, '质量与工艺')


def _temperature(values=T_GRID):
    return Input('temperature_C', '工程环境温度 Ta（℃）', '总体条件', tuple(values), scope='project')


def _stress(key='stress_ratio', label='电应力比 S', values=S_GRID):
    return Input(key, label, '工作应力', tuple(values), stress_max=max(values))


def _spec(fields, formula, reference, calculator, note='', *,
          derived_stress=()):
    if any(field.stress_max is not None for field in fields):
        suffix = '应力输入向上选取间隔为 0.1 的表列档位。'
        note = (note + ' ' + suffix).strip() if note else suffix
    return ModelSpec(tuple(fields), formula, 'GJB/Z 299D—2024 ' + reference, note,
                     calculator=calculator, derived_stress=derived_stress)


def _grade_label(grade):
    return {
        '军规无可靠性指标': '执行军用规范但无可靠性指标的产品',
        '民用或未知筛选': '其他民用或未知筛选等级的产品',
        'B-1（经筛选）': 'B-1',
        'B-2（受控工业级）': 'B-2',
    }.get(grade, grade)


def _range_factor(f, symbol, table, value, axis, key, **kwargs):
    selected = range_selection(table, value, axis, **kwargs)
    f.ranges.append((key, selected.interval, selected.public_source))
    return f.add(symbol, selected.value, selected.source)


def _calc_solder(p, environment, _ambient):
    f = Factors()
    base = f.add('λb', *row_value('308', p['kind']))
    e = f.env('309', environment)
    grade = f.quality('310', _grade_label(p['quality']))
    return f.result(base * e * grade)


def _calc_pcb(p, environment, _ambient):
    f = Factors()
    b1 = f.add('λb1', *stated_constant('5.15.1', 'λb1'))
    b2 = f.add('λb2', *stated_constant('5.15.1', 'λb2'))
    holes = f.add('N', p['plated_hole_count'], '使用的金属化孔数；标准节 5.15.1')
    e = f.env('305', environment)
    grade = f.quality('306', _grade_label(p['quality']))
    complexity = f.add('πC', *(stated_constant('5.15.1', 'πC', qualifier='单面板的')
                                if p['layer_count'] == 1 else below_value('307', p['layer_count'])))
    rate = (b1 * holes + b2) * e * grade * complexity
    if not math.isfinite(rate):
        raise ValueError('计算结果溢出')
    return f.result(rate)


def _calc_fet(p, environment, ambient):
    f = Factors()
    exact_c, exact_t, raw_s = fet_conditions_exact(p, ambient)
    c, t = float(exact_c), float(exact_t)
    s = ceil_stress(raw_s, 1)
    adjustment = StressAdjustment('stress_ratio', '校正电应力比 S', trace_number(raw_s), s,
                                  '式（19）～（23）、表 78')
    derived = (f'式（19）～（23）：C={number_text(c)}，校正 T={number_text(t)} ℃，'
               f'S={number_text(float(raw_s))}，查表 S={number_text(s)}')
    try:
        value, source = temp_matrix_value('78', exact_t, s, label_column=0)
    except ValueError as error:
        raise ValueError(derived + '；' + str(error)) from error
    f.add('λb', value, derived + '；' + source)
    f.env('79', environment)
    f.add('πQ', *below_value('80', p['quality']))
    f.add('πA', *below_value('81', p['application']))
    f.add('πr', *below_value('82', rated_power_band(p['rated_power_W'])))
    f.add('πC', *below_value('83', p['structure']))
    f.add('πK', *below_value('84', p['kind']))
    return replace(f.result(), stress_adjustments=(adjustment,))


IMPORTED_CIRCUITS = {
    '进口模拟集成电路': ('C.2', 'C.10', 'transistor_count', '晶体管数 NT', False),
    '进口微处理器': ('C.4', 'C.11', 'transistor_count', '晶体管数 NT', True),
    '进口静态随机存储器（SRAM）': ('C.5', 'C.12', 'bit_count', '位数 NB（bit）', True),
}
_IMPORTED_PACKAGE_LIST = tuple(IMPORTED_IC_PACKAGES)


def _make_imported_ic(model):
    base_table, temp_table, axis, _, technology = IMPORTED_CIRCUITS[model]
    axis_short = '晶体管数' if axis == 'transistor_count' else '位数'

    def calculate(p, environment, _ambient):
        f = Factors()
        suffix = ('_双极' if p['technology'] == '双极型' else '_MOS') if technology else ''
        col = (1 if p['technology'] == '双极型' else 2) if technology else 1
        axis_ticks = column_numeric_ticks(base_table, col, label_column=0, first_row=1)
        axis_tick, axis_note = resolve_numeric_tick(base_table, p[axis], axis_ticks, axis_label=axis_short)
        c1_val, c1_src = standard_lookup(base_table, 'C1' + suffix, axis_tick)
        c1 = f.add('C1', c1_val, c1_src + axis_note)
        pkg_col = _IMPORTED_PACKAGE_LIST.index(p['package']) + 1
        pkg_ticks = column_numeric_ticks('C.13', pkg_col, label_column=0, first_row=2)
        pkg_tick, pkg_note = resolve_numeric_tick('C.13', p['lead_count'], pkg_ticks, axis_label='引出端数')
        c2_val, c2_src = standard_lookup('C.13', IMPORTED_IC_PACKAGES[p['package']], pkg_tick)
        c2 = f.add('C2', c2_val, c2_src + pkg_note)
        t = f.add('πT', *standard_lookup(temp_table, 'πT' + suffix, p['junction_temperature_C']))
        e = f.add('πE', *standard_lookup('C.14', 'πE', environment))
        quality_factor = f.add('πQ', *standard_lookup('C.15', 'πQ', p['quality']))
        return f.result((c1 * t + c2 * e) * quality_factor)

    return calculate


def _calc_chip_film_resistor(p, environment, _ambient):
    f = Factors()
    f.add('λb', *stated_base_rate('5.6.9'))
    f.env('156', environment)
    f.quality('157', _grade_label(p['quality']))
    _range_factor(f, 'πR', '158', p['resistance_ohm'], 'R', 'resistance_ohm')
    f.add('πK', *row_value('159', p['film']))
    return f.result()


def _make_ceramic_cap(first):
    def calculate(p, environment, _ambient):
        f = Factors()
        if not first and p['temperature_C'] > p['rated_temperature_C']:
            raise ValueError('工作温度超过所选额定温度表的范围')
        table = '217' if first else '221' if p['rated_temperature_C'] == 85 else '222'
        f.add('λb', *capacitor_base_rate(table, temperature_C=p['temperature_C'],
                                         stress_ratio=p['stress_ratio']))
        f.env('218' if first else '223', environment)
        f.quality('219' if first else '224', _grade_label(p['quality']))
        _range_factor(f, 'πCV', '220' if first else '225', p['capacitance_uF'], 'C', 'capacitance_uF')
        f.add('πch', *row_value('204', '1类瓷介电容器' if first else '2类瓷介电容器',
                                1 if p['mounting'] == '片式' else 2))
        return f.result()

    return calculate


def _calc_digital_ic(p, environment, _ambient):
    f = Factors()
    c1 = f.add('C1', *digital_ic_complexity(p['technology'], p['gate_count'], 'C1'))
    c2 = f.add('C2', *digital_ic_complexity(p['technology'], p['gate_count'], 'C2'))
    c3 = f.add('C3', *digital_ic_package(p['package'], p['lead_count']))
    t = f.add('πT', *digital_ic_temperature(p['technology'], p['junction_temperature_C']))
    v = f.add('πV', *supply_voltage_factor(p['supply_voltage_V']))
    e = f.env('28', environment)
    quality_factor = f.quality('3', _grade_label(p['quality']))
    life = f.add('πL', *below_value('29', MATURITY[p['maturity']]))
    return f.result(quality_factor * (c1 * t * v + (c2 + c3) * e) * life)


def _calc_imported_resistor(p, environment, _ambient):
    f = Factors()
    kind = p['kind']
    if kind in (RES_TYPES[0], RES_TYPES[1], RES_TYPES[3]):
        label = {
            RES_TYPES[0]: '碳膜、氧化物膜、金属膜电阻器（P≤1W）',
            RES_TYPES[1]: '功率膜电阻器（P>1W）',
            RES_TYPES[3]: '精密线绕电阻器',
        }[kind]
        _range_factor(f, 'λb', 'C.44', p['resistance_ohm'], 'R', 'resistance_ohm',
                      conditions={1: label}, range_column=2)
    else:
        label = {
            RES_TYPES[2]: '普通线绕电阻器',
            RES_TYPES[4]: '功率线绕电阻器',
            RES_TYPES[5]: '片式膜电阻器',
        }[kind]
        f.add('λb', *row_value('C.44', label))
        f.ranges.append(('resistance_ohm', '内部', 'GJB/Z 299D—2024 表 C.44'))
    f.env('C.45', environment)
    f.quality('C.46', _grade_label(p['quality']))
    f.add('πT', *resistor_temperature(kind, p['temperature_C']))
    f.add('πS', *resistor_stress(kind, p['stress_ratio']))
    return f.result()


def _calc_imported_capacitor(p, environment, _ambient):
    f = Factors()
    chip = CAP_TYPES[p['kind']]
    if chip is None:
        raise ValueError('表 C.63 未给出该电容类型的表面贴装系数；请核对类型和安装形式')
    if p['kind'] == '铝电解':
        selected = range_selection('C.58', p['capacitance_uF'], 'C',
                                   conditions={0: '铝电解电容器'}, range_column=1)
        f.ranges.append(('capacitance_uF', selected.interval, selected.public_source))
        base = selected.value, selected.source
    elif p['kind'].startswith('固体钽'):
        base = conditions_value('C.58', {0: '固体钽电解电容器',
                                1: '密封' if p['kind'] == '固体钽（密封）' else '非密封'})
        f.ranges.append(('capacitance_uF', '内部', 'GJB/Z 299D—2024 表 C.58'))
    else:
        label = {
            '纸介': '纸介',
            '纸膜复合': '纸-膜复合介质',
            '薄膜': '薄膜',
            '云母': '云母电容器',
            '瓷介': '瓷介电容器',
            '非固体钽': '非固体钽电解电容器',
            '氧化铌': '氧化铌电解电容器',
        }[p['kind']]
        base = row_value('C.58', label)
        f.ranges.append(('capacitance_uF', '内部', 'GJB/Z 299D—2024 表 C.58'))
    f.add('λb', *base)
    f.env('C.59', environment)
    f.quality('C.60', _grade_label(p['quality']))
    f.add('πT', *capacitor_temperature(p['kind'], p['temperature_C']))
    f.add('πS', *capacitor_stress(p['kind'], p['stress_ratio']))
    f.add('πch', *row_value('C.63', chip, 1 if p['mounting'] == '片式' else 2))
    return f.result()


def _calc_imported_inductor(p, environment, _ambient):
    f = Factors()
    chip = p['kind'] == '片式电感器'
    if chip:
        f.add('λb', *stated_base_rate('C.2.11.3'))
    else:
        f.add('λb', *standard_lookup('C.64', 'λb', p['kind']))
    f.add('πE', *standard_lookup('C.68' if chip else 'C.65', 'πE', environment))
    grade = {
        '军规无可靠性指标': '执行军用规范但无可靠性指标的产品',
        '民用或未知筛选': '其他民用或未知筛选等级的产品' if chip else '民品或未知筛选等级的产品',
    }.get(p['quality'], p['quality'])
    f.add('πQ', *standard_lookup('C.69' if chip else 'C.66', 'πQ', grade))
    f.add('πT', *standard_lookup('C.70' if chip else 'C.67', 'πT', p['temperature_C']))
    return f.result()


def _calc_imported_diode(p, environment, _ambient):
    f = Factors()
    f.add('λb', *conditions_value('C.30', {1: '普通二极管', 2: p['kind']}))
    f.env('C.31', environment)
    f.quality('C.32', _grade_label(p['quality']))
    f.add('πT', *discrete_temperature('普通二极管', p['temperature_C']))
    f.add('πS', *diode_stress(current_ratio=p['current_ratio'], voltage_ratio=p['voltage_ratio']))
    return f.result()


def _calc_imported_bjt(p, environment, _ambient):
    f = Factors()
    f.add('λb', *conditions_value('C.30', {1: '双极型晶体管', 2: BJT_TYPES[p['kind']]}))
    f.env('C.31', environment)
    f.quality('C.32', _grade_label(p['quality']))
    f.add('πT', *discrete_temperature('双极型晶体管', p['temperature_C']))
    f.add('πS', *bjt_stress(voltage_ratio=p['voltage_ratio'], power_ratio=p['power_ratio']))
    return f.result()


def _calc_imported_other_discrete(p, environment, _ambient):
    f = Factors()
    temp = DISCRETE_TYPES[p['kind']]
    f.add('λb', *row_value('C.30', temp))
    f.env('C.31', environment)
    f.quality('C.32', _grade_label(p['quality']))
    f.add('πT', *discrete_temperature(temp, p['temperature_C']))
    f.add('πS', *discrete_stress(temp, p['stress_ratio']))
    return f.result()


MODELS = {
    '国产焊接点': _spec([
        _choice('kind', '连接工艺', ['再流焊', '浸焊', '有绕接烙铁焊', '无绕接烙铁焊',
                                    '印制板的烙铁焊', '熔接', '绕接', '压接', '夹接']),
        _quality(('A', 'B', 'C'))],
        'λp = λb × πE × πQ',
        '5.15.2，式（76），表 308～310，标准第 162～163 页',
        _calc_solder,
        '每个连接点单独计算，数量填写相应工艺的连接点数，不是板数或器件数。'
        'SMT 热循环疲劳须按 5.15.3 另行评估并取每块板的最大值；不要按焊点数累加该热循环项。'
        '已有整件失效率包含的连接部分不得重复计入。'),
    '国产印制板': _spec([
        Input('plated_hole_count', '使用的金属化孔数 N', positive=False, integer=True),
        Input('layer_count', '电路板层数 L', choices=tuple(range(1, 17)), integer=True),
        _quality(('A1', 'A2', 'B', 'C'))],
        'λp = (λb1 × N + λb2) × πE × πQ × πC',
        '5.15.1，式（75），表 305～307，标准第 161～162 页',
        _calc_pcb,
        '计算印制板本体，不包含板上元器件、焊接点和 SMT 互连。N 为实际使用的金属化孔数；'
        '单面板复杂度系数按正文取值，2～16 层按表 307。无独立温度或电应力系数。'
        '数量按设备实际装入的印制板计；拆分后不装入设备的拼板工艺边不另计。'),
    '国产硅场效应晶体管': _spec([
        _choice('kind', '种类', ['MOS 型', '结型']),
        _choice('structure', '结构', ['单管', '四极管']),
        _choice('application', '应用', ['线性放大', '开关', '高频 f > 400MHz']),
        _quality(('A2', 'A3', 'A4', 'A5', 'A6', 'B1', 'B2', 'C')),
        Input('rated_power_W', 'Ts 时的额定功耗 PM（W）', '额定值',
              presets=(0.5, 1, 5, 10, 20, 50, 200),
              constraint_hint='表 82 功率分档：≤0.5、0.5～1、1～5、5～20、20～50、50～200、＞200 W'),
        Input('rated_temperature_C', '额定温度点 Ts（℃）', '额定值', positive=False,
              min_value=25, max_value=200, max_inclusive=False,
              presets=(25, 50, 75, 100, 125),
              constraint_hint='5.3.10.2 要求 Ts ≥ 25 ℃ 且 Ts < TM'),
        Input('max_junction_temperature_C', '最高允许结温 TM（℃）', '额定值',
              min_value=25, min_inclusive=False, max_value=200,
              presets=(125, 150, 175, 200),
              constraint_hint='5.3.10.2 要求 Ts < TM ≤ 200 ℃（高于 175 ℃ 按 175 ℃ 计算 C）'),
        _choice('temperature_basis', '温度取用', ['工程环境温度', '带散热片功率器件的管壳温度'], '工作应力'),
        Input('case_temperature_C', '管壳温度 TC（℃）', '工作应力', positive=False,
              when=('temperature_basis', ('带散热片功率器件的管壳温度',)), allow_negative=True),
        Input('operating_power_W', '使用功耗 POP（W）', '工作应力')],
        'λp = λb × πE × πQ × πA × πr × πC × πK',
        '式（12）、（19）～（23），表 78～84，标准第 58～60、68～69 页',
        _calc_fet,
        '适用于单管和四极管；双管封装请选择硅场效应双管模型。按额定温度自动校正 C、T、S；'
        'S = (POP / PM) × C，使用耗散功率，不是漏极电流比。高于 Ts 时须按器件降额曲线核查功耗。'
        '校正温度须有表 78 对应数值；校正应力 S 向上选取表列档位。',
        derived_stress=(Input('stress_ratio', '校正电应力比 S', '工作应力',
                              tuple(n / 10 for n in range(1, 11)), stress_max=1.0,
                              stress_source='式（19）～（23）、表 78'),)),
    '进口模拟集成电路': _spec([
        _choice('transistor_count', '晶体管数 NT', int_or_float_options(numeric_axes('C.2')),
                min_value=1, integer=True, ceil_choices=True),
        _choice('package', '封装形式', IMPORTED_IC_PACKAGES),
        _choice('lead_count', '引出端数 NP', int_or_float_options(numeric_axes('C.13', first_row=2)),
                min_value=1, integer=True, ceil_choices=True,
                choices_by=package_lead_choices_by('C.13', _IMPORTED_PACKAGE_LIST)),
        _quality(IMPORTED_IC_QUALITY),
        _choice('junction_temperature_C', '结温 Tj（℃）', JUNCTION_GRID, '工作应力')],
        'λp = (C1 × πT + C2 × πE) × πQ',
        '式（C.1），表 C.2、C.10、C.13～C.15，标准第 287、289、294～297 页',
        _make_imported_ic('进口模拟集成电路'),
        '按标准查表。C1 为电路复杂度失效率，C2 为封装复杂度失效率。结温与工程环境温度分别填写。'),
    '国产片式膜电阻器': _spec([
        Input('resistance_ohm', '阻值 R（Ω）', positive=False), _choice('film', '膜类型', ['薄膜', '厚膜']),
        _quality(QUALITY_RES)], 'λp = 0.006 × πE × πQ × πR × πK', '式（42），表 156～159，标准第 94～95 页',
        _calc_chip_film_resistor,
        '本模型无独立的温度、功率应力项。阻值按标准范围选取 πR。'),
    '国产1类瓷介电容器': _spec([
        Input('capacitance_uF', '电容量 C（μF）'), _choice('mounting', '安装形式', ['片式', '有引线']),
        _quality(QUALITY_CAP), _temperature(range(0, 86, 5)),
        _stress(label='电压应力比 S = 工作电压 / 额定电压', values=S_GRID + (1.0,))],
        'λp = λb × πE × πQ × πCV × πch', '式（57），表 204、217～220，标准第 113、120～122 页',
        _make_ceramic_cap(True)),
    '国产2类瓷介电容器': _spec([
        Input('capacitance_uF', '电容量 C（μF）'), _choice('rated_temperature_C', '额定温度（℃）', [85, 125]),
        _choice('mounting', '安装形式', ['片式', '有引线']), _quality(QUALITY_CAP), _temperature(range(0, 126, 5)),
        _stress(label='电压应力比 S = 工作电压 / 额定电压', values=S_GRID + (1.0,))],
        'λp = λb × πE × πQ × πCV × πch', '式（58），表 204、221～225，标准第 113、122～126 页',
        _make_ceramic_cap(False)),
    '国产普通数字集成电路': _spec([
        _choice('technology', '电路工艺', ['双极型', 'MOS']),
        _choice('gate_count', '等效门数 NG', int_or_float_options(numeric_axes('5')),
                min_value=1, ceil_choices=True,
                choices_by=('technology', (
                    ('双极型', int_or_float_options(numeric_axes('4'))),
                    ('MOS', int_or_float_options(numeric_axes('5'))),
                )),
                limits_by=('technology', (
                    ('双极型', (1, 60000, '表 4 双极型等效门数上限 60000 门')),
                    ('MOS', (1, 10000000, '表 5 MOS 等效门数上限 10000000 门')),
                )),
                constraint_hint='表 4 双极型上限 60000 门，表 5 MOS 上限 10000000 门；非表列门数向上取档'),
        _choice('package', '封装形式', PACKAGES),
        _choice('lead_count', '引出端数 NP', int_or_float_options(numeric_axes('27', first_row=2)),
                min_value=1, integer=True, ceil_choices=True,
                choices_by=package_lead_choices_by('27', PACKAGES)),
        _quality(QUALITY_IC), _choice('maturity', '成熟程度', MATURITY, '质量与工艺'),
        _choice('junction_temperature_C', '结温 Tj（℃）', int_or_float_options(paired_axes('17')), '工作应力'),
        _choice('supply_voltage_V', '工作电源电压 Vs（V）', SUPPLY_VOLTAGE_CHOICES, '工作应力',
                presets=SUPPLY_VOLTAGE_PRESETS, min_value=0, min_inclusive=False, max_value=15,
                ceil_choices=True, constraint_hint=SUPPLY_VOLTAGE_HINT)],
        'λp = πQ × [C1 × πT × πV + (C2 + C3) × πE] × πL',
        '表 2～5、16～17、26～29，标准第 18～33 页',
        _calc_digital_ic,
        '输入结温，不以环境温度代替结温。Vs ≤ 3 V 按 ≤3 档；3 V 以上向上归入下一表列整数电压档。'),
    '进口电阻器': _spec([
        _choice('kind', '电阻类型', RES_TYPES), Input('resistance_ohm', '阻值 R（Ω）', positive=False),
        _quality(QUALITY_IMPORT), _temperature(), _stress()],
        'λp = λb × πE × πQ × πT × πS', '式（C.9），表 C.44～C.48，标准第 310～311 页',
        _calc_imported_resistor),
    '进口电容器': _spec([
        _choice('kind', '电容类型', CAP_TYPES), Input('capacitance_uF', '电容量 C（μF）'),
        _choice('mounting', '安装形式', ['片式', '有引线']), _quality(QUALITY_IMPORT), _temperature(),
        _stress(label='电压应力比 S = 工作电压 / 额定电压')],
        'λp = λb × πE × πQ × πT × πS × πch', '式（C.13），表 C.58～C.63，标准第 315～317 页',
        _calc_imported_capacitor),
    '进口电感器': _spec([
        _choice('kind', '电感类型', ['固定电感器', '可变电感器', '扼流圈', '片式电感器']),
        _quality(QUALITY_INDUCTOR), _temperature(T_INDUCTOR)],
        'λp = λb × πE × πQ × πT', '式（C.15）～（C.16），表 C.64～C.70，标准第 317～319 页',
        _calc_imported_inductor),
    '进口普通二极管': _spec([
        _choice('kind', '二极管类型', DIODE_TYPES), _quality(QUALITY_DISCRETE), _temperature(),
        _stress('voltage_ratio', '电压负荷率 SV = 工作电压 / 额定电压'),
        _stress('current_ratio', '电流负荷率 SI = 工作电流 / 额定电流')],
        'λp = λb × πE × πQ × πT × πS', '式（C.6），表 C.30～C.34，标准第 303～305 页',
        _calc_imported_diode),
    '进口双极型晶体管': _spec([
        _choice('kind', '晶体管类型', BJT_TYPES), _quality(QUALITY_DISCRETE), _temperature(),
        _stress('voltage_ratio', '电压负荷率 SV = 工作电压 / 额定电压'),
        _stress('power_ratio', '功率负荷率 SP = 工作功率 / 额定功率')],
        'λp = λb × πE × πQ × πT × πS', '式（C.6），表 C.30～C.33、C.35，标准第 303～306 页',
        _calc_imported_bjt),
    '进口其他分立半导体': _spec([
        _choice('kind', '器件类型', DISCRETE_TYPES), _quality(QUALITY_DISCRETE), _temperature(), _stress()],
        'λp = λb × πE × πQ × πT × πS', '式（C.6），表 C.30～C.33、C.36，标准第 303～306 页',
        _calc_imported_other_discrete,
        '表 C.36 未单独定义各类器件的 S 换算方法，须输入已确认适用的应力比；'
        '不能把硅场效应晶体管的 S 直接解释为漏极电流比。'),
}

for _name, (_base, _temp, _axis, _label, _technology) in IMPORTED_CIRCUITS.items():
    if _name == '进口模拟集成电路':
        continue
    _bjt_ticks = column_numeric_ticks(_base, 1, label_column=0, first_row=1)
    _mos_ticks = column_numeric_ticks(_base, 2, label_column=0, first_row=1)
    MODELS[_name] = _spec([
        _choice('technology', '电路工艺', ['双极型', 'MOS']),
        _choice(_axis, _label, int_or_float_options(numeric_axes(_base)),
                min_value=1, integer=True, ceil_choices=True,
                choices_by=('technology', (('双极型', _bjt_ticks), ('MOS', _mos_ticks))),
                limits_by=('technology', (
                    ('双极型', (1, _bjt_ticks[-1], f'表 {_base} 双极型上限 {_bjt_ticks[-1]}')),
                    ('MOS', (1, _mos_ticks[-1], f'表 {_base} MOS 上限 {_mos_ticks[-1]}')),
                ))),
        _choice('package', '封装形式', IMPORTED_IC_PACKAGES),
        _choice('lead_count', '引出端数 NP', int_or_float_options(numeric_axes('C.13', first_row=2)),
                min_value=1, integer=True, ceil_choices=True,
                choices_by=package_lead_choices_by('C.13', _IMPORTED_PACKAGE_LIST)),
        _quality(IMPORTED_IC_QUALITY),
        _choice('junction_temperature_C', '结温 Tj（℃）', JUNCTION_GRID, '工作应力')],
        'λp = (C1 × πT + C2 × πE) × πQ',
        f'式（C.1），表 {_base}、{_temp}、C.13～C.15',
        _make_imported_ic(_name),
        '按标准查表。结温独立于工程环境温度；位数用 bit，晶体管数用实际数量。')

_CATEGORIES = {
    '国产焊接点': 'PCB', '国产印制板': 'PCB', '国产硅场效应晶体管': 'Q',
    '国产片式膜电阻器': 'R', '国产1类瓷介电容器': 'C', '国产2类瓷介电容器': 'C',
    '国产普通数字集成电路': 'IC', '进口模拟集成电路': 'IC', '进口微处理器': 'IC',
    '进口静态随机存储器（SRAM）': 'IC', '进口电阻器': 'R', '进口电容器': 'C',
    '进口电感器': 'L', '进口普通二极管': 'D', '进口双极型晶体管': 'Q', '进口其他分立半导体': 'Q',
}
_STRESS_SOURCES = {
    '国产1类瓷介电容器': {'stress_ratio': '表 217'},
    '国产2类瓷介电容器': {'stress_ratio': ('表 221', '表 222')},
    '进口普通二极管': {'current_ratio': '表 C.34，SI 行', 'voltage_ratio': '表 C.34，SV 列'},
    '进口双极型晶体管': {'voltage_ratio': '表 C.35，SV 行', 'power_ratio': '表 C.35，SP 列'},
    '进口电阻器': {'stress_ratio': '表 C.48'}, '进口电容器': {'stress_ratio': '表 C.62'},
    '进口其他分立半导体': {'stress_ratio': '表 C.36'},
}
_SECTIONS = {
    '国产焊接点': '5.15.2', '国产印制板': '5.15.1', '国产硅场效应晶体管': '5.3.6',
    '国产片式膜电阻器': '5.6.9', '国产1类瓷介电容器': '5.9.4', '国产2类瓷介电容器': '5.9.5',
    '国产普通数字集成电路': '5.1.2', '进口模拟集成电路': 'C.2.2.2', '进口微处理器': 'C.2.2.2',
    '进口静态随机存储器（SRAM）': 'C.2.2.2', '进口电阻器': 'C.2.7', '进口电容器': 'C.2.10',
    '进口电感器': 'C.2.11.2', '进口普通二极管': 'C.2.4', '进口双极型晶体管': 'C.2.4',
    '进口其他分立半导体': 'C.2.4',
}
_EXAMPLES = {
    '国产焊接点': {'kind': '再流焊', 'quality': 'B'},
    '国产印制板': {'plated_hole_count': 100, 'layer_count': 4, 'quality': 'B'},
    '国产硅场效应晶体管': {
        'kind': 'MOS 型', 'structure': '单管', 'application': '开关', 'quality': 'B2',
        'rated_power_W': 10, 'rated_temperature_C': 25, 'max_junction_temperature_C': 175,
        'temperature_basis': '工程环境温度', 'operating_power_W': 2,
    },
    '进口模拟集成电路': {
        'transistor_count': 100, 'package': '密封－双列直插（DIP）封装',
        'lead_count': 16, 'quality': 'B', 'junction_temperature_C': 55,
    },
    '国产片式膜电阻器': {'resistance_ohm': 1000, 'film': '厚膜', 'quality': 'B2'},
    '国产1类瓷介电容器': {'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'B2', 'stress_ratio': 0.5},
    '国产2类瓷介电容器': {
        'capacitance_uF': 0.1, 'rated_temperature_C': 85,
        'mounting': '片式', 'quality': 'B2', 'stress_ratio': 0.5,
    },
    '国产普通数字集成电路': {
        'technology': 'MOS', 'gate_count': 100, 'package': '密封-双列直插(DIP)封装',
        'lead_count': 16, 'quality': 'B2', 'maturity': '稳定生产',
        'junction_temperature_C': 55, 'supply_voltage_V': 5,
    },
    '进口电阻器': {'kind': '普通膜（≤1 W）', 'resistance_ohm': 1000, 'quality': 'M', 'stress_ratio': 0.5},
    '进口电容器': {'kind': '瓷介', 'capacitance_uF': 0.1, 'mounting': '片式', 'quality': 'M', 'stress_ratio': 0.5},
    '进口电感器': {'kind': '固定电感器', 'quality': 'M'},
    '进口普通二极管': {'kind': '整流二极管', 'quality': 'JANTX', 'voltage_ratio': 0.5, 'current_ratio': 0.5},
    '进口双极型晶体管': {'kind': '开关晶体管', 'quality': 'JANTX', 'voltage_ratio': 0.5, 'power_ratio': 0.5},
    '进口其他分立半导体': {'kind': '硅场效应晶体管', 'quality': 'JANTX', 'stress_ratio': 0.5},
    '进口微处理器': {
        'technology': 'MOS', 'transistor_count': 1000, 'package': '密封－双列直插（DIP）封装',
        'lead_count': 16, 'quality': 'B', 'junction_temperature_C': 55,
    },
    '进口静态随机存储器（SRAM）': {
        'technology': 'MOS', 'bit_count': 16384, 'package': '密封－双列直插（DIP）封装',
        'lead_count': 16, 'quality': 'B', 'junction_temperature_C': 55,
    },
}

for _name, _definition in tuple(MODELS.items()):
    _quality_tables, _quality_summaries = QUALITY_METADATA.get(_name, ((), {}))
    MODELS[_name] = replace(
        _definition,
        category=_CATEGORIES[_name],
        section=_SECTIONS[_name],
        quality_tables=_quality_tables,
        quality_summaries=tuple(_quality_summaries.items()),
        example=_EXAMPLES[_name],
        fields=tuple(replace(f, stress_source=_STRESS_SOURCES.get(_name, {}).get(f.key, ''))
                     for f in _definition.fields),
    )
