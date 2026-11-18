"""Bidirectional intrinsic-parameter reuse between working and nonoperating models."""

import copy

from .prediction import model_state
from .standards import MODELS, validate_parameters

_SPECIAL_MODELS = frozenset(('待配置', '已知单件失效率', '工程模块'))

# Operating stress, duty/cycling, thermal and composed-rate fields never cross states.
_EXCLUDED_KEYS = frozenset({
    'junction_temperature_C',
    'channel_temperature_C',
    'analog_junction_temperature_C',
    'digital_junction_temperature_C',
    'hybrid_temperature_C',
    'hotspot_temperature_C',
    'case_temperature_C',
    'case_temperature_rise_C',
    'temperature_rise_C',
    'temperature_method',
    'supply_voltage_V',
    'analog_supply_voltage_V',
    'digital_supply_voltage_V',
    'operating_voltage_V',
    'operating_current_A',
    'operating_power_W',
    'operating_energy_J',
    'series_resistance_ohm',
    'cycle_rate',
    'programming_cycles',
    'operations_per_hour',
    'operations_per_day',
    'mating_per_1000h',
    'contact_current_A',
    'contact_voltage_mV',
    'utilization',
    'constituent_rate',
    'rear_circuit_rate',
    'mounted_ic_rate',
    'source_environment',
    'source_temperature_C',
    'tube_current_mA',
    'speed_rpm',
    'output_power_mW',
    'output_power_W',
})

_EXCLUDED_SECTIONS = frozenset(('工作应力', '非工作条件', '外贴微电路', '组成预计'))

_MATURITY_TIER_BY_TEXT = {
    '稳定生产': 0,
    '符合相应的标准或技术条件，已稳定生产的产品': 0,
    '符合相应的标准或技术 条件，已稳定生产的产品': 0,
    '符合生产标准已稳定生产的产品': 0,
    '符合相应标准或技术条件，已稳定生产的产品': 0,
    '质量尚未稳定': 1,
    '质量尚未稳定的产品': 1,
    '试制／新投产／重大工艺变化或复产': 2,
    '试制品或新投产的初批次产品；设计或工艺上有重大变更；长期中断生产或生产线有重大变化': 2,
    '试制品或新投产的初批次产品；设计或 工艺有重大变更；长期中断生产或 生产线有重大变化': 2,
    '试制品或设计、工艺有重大变更的产品': 2,
}

_WORKING_TO_NONOP_PACKAGE = {
    '密封-双列直插(DIP)封装': '密封 / 双列直插 (DIP) 封装',
    '密封-扁平(FP)封装': '密封 / 扁平 (FP) 封装',
    '密封-插接阵列(PGA/BGA和其他)封装': '密封 / 插接阵列 (含 PGA、 BGA、LGA 和其他)封装',
    '密封-表面安装(LCC/SO和其他)封装': '密封 / 表面安装 (PLCC、 LCC、SO 和其他) 封装',
    '密封-金属壳封装': '密封 / 金属壳 封装',
    '非密封-双列直插(DIP)封装': '非密封 / 双列直插 (DIP) 封装',
    '非密封-扁平(FP)封装': '非密封 / 扁平 (FP) 封装',
    '非密封-插接阵列(PGA/BGA和其他)封装': '非密封 / 插接阵列 (含 PGA、 BGA、LGA 和其他) 封装',
    '非密封-表面安装(LCC/SO和其他)封装': '非密封 / 表面安装 (PLCC、 LCC、SO 和 其他)封装',
}
_NONOP_TO_WORKING_PACKAGE = {v: k for k, v in _WORKING_TO_NONOP_PACKAGE.items()}

_TECHNOLOGY_PAIRS = {
    '双极型': '双极',
    '双极': '双极型',
    'MOS': 'MOS',
}

_IMPLIED_IC_TECHNOLOGY = {
    '国产双极微处理器': '双极',
    '国产MOS微处理器': 'MOS',
    '国产双极SRAM': '双极',
    '国产MOS SRAM及CCD器件': 'MOS',
    '国产双极ROM': '双极',
    '国产MOS ROM': 'MOS',
    '国产双极PROM': '双极',
    '国产MOS PROM': 'MOS',
    '国产MOS DRAM': 'MOS',
    '国产MOS FIFO': 'MOS',
    '国产MOS UVEPROM': 'MOS',
    '国产MOS EEPROM': 'MOS',
    '国产MOS FLASH': 'MOS',
}

_WORKING_MICROPROCESSOR_BY_TECH = {
    '双极': '国产双极微处理器',
    'MOS': '国产MOS微处理器',
}

_MMIC_PROCESS_PAIRS = {
    'MESFET': 'GaAs 微波单片集成电路 / MESFET',
    'HBT': 'GaAs 微波单片集成电路 / HBT',
    'pHEMT': 'GaAs 微波单片集成电路 / pHEMT',
    'HEMT': 'GaN 微波单片集成电路 / HEMT',
}
_MMIC_KIND_TO_PROCESS = {v: k for k, v in _MMIC_PROCESS_PAIRS.items()}

_RESISTOR_DIRECT_KIND = {
    '国产金属膜电阻器': '金属膜',
    '国产功率线绕电阻器': '功率线绕电阻器',
    '国产片式膜电阻器': '片式膜电阻器',
}
_RESISTOR_LIMITED_LOW_KIND = {
    '国产碳膜电阻器': '碳膜(功率 P ≤2W)',
    '国产玻璃釉电阻器': '玻璃釉(功率 P ≤2W)',
}
_RESISTOR_SUBTYPE_PAIR = {
    ('国产线绕电阻器', '普通线绕'): '线绕电阻器 / 普通',
    ('国产线绕电阻器', '精密线绕'): '线绕电阻器 / 精密',
    ('国产箔式电阻器', '普通箔式'): '箔式电阻器 / 普通金属箔',
    ('国产箔式电阻器', '精密箔式'): '箔式电阻器 / 精密金属箔',
}
_RESISTOR_NONOP_KIND_MAP = {
    '金属膜': ('国产金属膜电阻器', None),
    '碳膜(功率 P ≤2W)': ('国产碳膜电阻器', None),
    '玻璃釉(功率 P ≤2W)': ('国产玻璃釉电阻器', None),
    '线绕电阻器 / 普通': ('国产线绕电阻器', '普通线绕'),
    '线绕电阻器 / 精密': ('国产线绕电阻器', '精密线绕'),
    '箔式电阻器 / 普通金属箔': ('国产箔式电阻器', '普通箔式'),
    '箔式电阻器 / 精密金属箔': ('国产箔式电阻器', '精密箔式'),
    '功率线绕电阻器': ('国产功率线绕电阻器', None),
    '功率非线绕电阻器(功率 P ＞2W)': ('国产功率非线绕电阻器', None),
    '片式膜电阻器': ('国产片式膜电阻器', None),
}

_POTENTIOMETER_WORKING_TO_KIND = {
    '国产普通线绕电位器': '普通线绕',
    '国产精密线绕电位器': '精密线绕',
    '国产微调线绕电位器': '微调线绕',
    '国产有机实芯电位器': '有机实芯',
    '国产合成碳膜电位器': '合成碳膜',
    '国产玻璃釉电位器': '玻璃釉',
    '国产非线绕精密电位器': '非线绕精密电位器',
}
_POTENTIOMETER_KIND_TO_WORKING = {v: k for k, v in _POTENTIOMETER_WORKING_TO_KIND.items()}

_CAP_MODEL_TO_NONOP_KIND = {
    '国产1类瓷介电容器': '瓷介电容器 / 1 类瓷介',
    '国产2类瓷介电容器': '瓷介电容器 / 2 类瓷介',
    '国产云母电容器': '云母电容器',
    '国产铝电解电容器': '铝电解电容器',
    '国产固体钽电解电容器': '固体钽电解电容器',
    '国产非固体钽电解电容器': '非固体钽电解电容器',
    '国产氧化铌电解电容器': '氧化铌电解电容器',
}
_CAP_NONOP_KIND_TO_MODEL = {v: k for k, v in _CAP_MODEL_TO_NONOP_KIND.items()}

_PAPER_FILM_SUBTYPE_TO_NONOP = {
    '聚苯乙烯，聚四氟乙烯，聚碳酸酯': '纸和薄膜电容器 / 薄膜',
    '涤纶、聚丙烯等有机膜': '纸和薄膜电容器 / 薄膜',
    '纸介，金属化纸介': '纸和薄膜电容器 / 纸介',
    '纸-膜复合介质': '纸和薄膜电容器 / 纸-膜复合介质',
}
_VARIABLE_CAP_SUBTYPE_PAIRS = {
    '瓷介微调': '可变电容器 / 瓷介微调',
    '玻璃介质微调': '可变电容器 / 玻璃介质微调',
    '空气介质微调': '可变电容器 / 空气介质微调',
}
_VARIABLE_CAP_NONOP_TO_SUBTYPE = {v: k for k, v in _VARIABLE_CAP_SUBTYPE_PAIRS.items()}
_CAP_MOUNTING_PAIRS = {
    '片式': '片式',
    '有引线': '有引线(包含有引线 SMT，通孔式)',
    '有引线(包含有引线 SMT，通孔式)': '有引线',
}

_ORDINARY_DIODE_SUBTYPES = ('整流二极管', '开关二极管', '检波二极管', '瞬态抑制二极管')
_DIODE_NONOP_TO_ORDINARY = {f'普通二极管 / {name}': name for name in _ORDINARY_DIODE_SUBTYPES}

_BIPOLAR_TRANSISTOR_TO_NONOP = {
    '小功率晶体管': '双极型晶体管 / 功率晶体管',
    '大功率晶体管': '双极型晶体管 / 功率晶体管',
    '开关晶体管': '双极型晶体管 / 开关晶体管',
    '达林顿晶体管': '双极型晶体管 / 达林顿晶体管',
}
_NONOP_TRANSISTOR_TO_BIPOLAR = {
    '双极型晶体管 / 开关晶体管': '开关晶体管',
    '双极型晶体管 / 达林顿晶体管': '达林顿晶体管',
}

_OPTO_PAIRS = {
    '简单式光电耦合器': '光电耦合器 / 简单式',
    '复合式光电耦合器': '光电耦合器 / 复合式',
}
_OPTO_REVERSE_PAIRS = {v: k for k, v in _OPTO_PAIRS.items()}

_TRANSFORMER_W_TO_N = {
    '小功率脉冲变压器': '小功率脉冲变压器',
    '音频变压器 中频变压器': '中频变压器、音频变压器、电源变压器',
    '高压变压器 射频变压器': '高压变压器、射频变压器',
}
_TRANSFORMER_N_TO_W = {
    '小功率脉冲变压器': '小功率脉冲变压器',
    '高压变压器、射频变压器': '高压变压器 射频变压器',
}

_SWITCH_PAIRS = {
    '旋转式': '旋转开关',
    '微动式': '微动开关',
    '钮子式': '钮子开关',
    '按钮式': '按钮开关',
    '滑动式': '滑动开关',
    '行程式': '行程开关',
}
_SWITCH_REVERSE = {v: k for k, v in _SWITCH_PAIRS.items()}

_LASER_W_TO_N = {'密封二氧化碳': '密封二氧化碳激光器'}
_LASER_N_TO_W = {'密封二氧化碳激光器': '密封二氧化碳'}

_OTHER_W_TO_N = {
    '微波元器件 / 衰减器 / 固定': '微波元器件 / 衰减器(固定或可调)',
    '微波元器件 / 衰减器 / 可调': '微波元器件 / 衰减器(固定或可调)',
    '微波元器件 / 固定元件 / 定向耦合器': '微波元器件 / 固定元件 / 定向耦合器 固定短路线 谐振腔',
    '微波元器件 / 固定元件 / 固定短线谐振腔': '微波元器件 / 固定元件 / 定向耦合器 固定短路线 谐振腔',
    '微波元器件 / 可变元件 / 可调短线': '微波元器件 / 可变元件 / 可调短路线 可调谐振器',
    '微波元器件 / 可变元件 / 可调谐振腔': '微波元器件 / 可变元件 / 可调短路线 可调谐振器',
    '熔断器 / ≤30A': '熔断器',
    '熔断器 / ＞30A': '熔断器',
    '仿真负载 / ＜100W': '仿真负载',
    '仿真负载 / 100W～1000W': '仿真负载',
    '仿真负载 / ＞1000W': '仿真负载',
    '仿真终端': '仿真终端(厚、薄膜负载)',
    '断路器 / 保护用(每个电极)': '断路器(每个电极)',
    '断路器 / 电源开关用(每个电极)': '断路器(每个电极)',
}
_OTHER_N_TO_W = {
    '仿真终端(厚、薄膜负载)': '仿真终端',
}

_COUNT_MMIC_PAIRS = {
    'GaAsMMIC(、pHEMT) / 密封': 'GaAsMMIC(pHEMT) / 密封',
    'GaAsMMIC(、pHEMT) / 非密封': 'GaAsMMIC(pHEMT) / 非密封',
    'aNMMIC(HEMT) / 密封': 'GaNMMIC(HEMT) / 密封',
    'aNMMIC(HEMT) / 非密封': 'GaNMMIC(HEMT) / 非密封',
}
_COUNT_MMIC_REVERSE = {v: k for k, v in _COUNT_MMIC_PAIRS.items()}

_COUNT_DISCRETE_W_TO_N = {
    '二极管 / 普通二极管 / 整流': '普通二极管 / 整流',
    '二极管 / 普通二极管 / 开关': '普通二极管 / 开关',
    '二极管 / 普通二极管 / 检波': '普通二极管 / 检波',
    '二极管 / 普通二极管 / 瞬态抑制': '普通二极管 / 瞬态抑制',
    '二极管 / 电压调整、电流调整、电压基准': '电压调整、电流调整、电压基准二极管',
    '二极管 / 微波二极管 / 微波硅检波': '微波二极管 / 微波硅检波',
    '二极管 / 微波二极管 / 微波硅混频': '微波二极管 / 微波硅混频',
    '二极管 / 微波二极管 / 变容、阶跃、隧道、体效应、PIN、雪崩': '微波二极管 / 变容，阶跃，隧道，体效应，PIN，雪崩',
    '晶体管 / 双极型 / 功率': '双极型晶体管 / 功率',
    '晶体管 / 双极型 / 开关': '双极型晶体管 / 开关',
    '晶体管 / 双极型 / 达林顿': '双极型晶体管 / 达林顿',
    '晶体管 / 硅场效应': '硅场效应晶体管',
    '晶体管 / 微波双极型': '微波晶体管',
    '晶体管 / 微波场效应 / GaAsMESFET、HBT、pHEMT': '微波晶体管',
    '晶体管 / 微波场效应 / GaNHEMT': '微波晶体管',
    '晶体管 / 单结': '单结晶体管',
    '晶体管 / 闸流': '闸流晶体管',
}
_COUNT_DISCRETE_N_TO_W = {
    v: k for k, v in _COUNT_DISCRETE_W_TO_N.items() if v != '微波晶体管'
}

_COUNT_VACUUM_PAIRS = {
    '速调管 / 连续波': '微波电子管 / 连续波速调管',
    '速调管 / 脉冲': '微波电子管 / 脉冲速调管',
}
_COUNT_VACUUM_REVERSE = {v: k for k, v in _COUNT_VACUUM_PAIRS.items()}

_COUNT_RESISTOR_PAIRS = {
    '线绕电阻器 / 普通线绕': '绕线电阻器 / 普通线绕',
    '线绕电阻器 / 精密线绕': '绕线电阻器 / 精密线绕',
    '功率非线绕固定电阻器': '功率非线绕电阻器',
}
_COUNT_RESISTOR_REVERSE = {v: k for k, v in _COUNT_RESISTOR_PAIRS.items()}

_COUNT_CAP_EXACT_PAIRS = {
    '1类瓷介电容器': '瓷介电容器 / 1类瓷介',
    '2类瓷介电容器': '瓷介电容器 / 2类瓷介',
}
_COUNT_CAP_EXACT_REVERSE = {v: k for k, v in _COUNT_CAP_EXACT_PAIRS.items()}

_COUNT_INDUCTOR_W_TO_N = {
    '变压器 / 电源变压器': '变压器 / 中频、音频、电源变压器',
    '变压器 / 音频变压器': '变压器 / 中频、音频、电源变压器',
    '变压器 / 中频变压器': '变压器 / 中频、音频、电源变压器',
}

_COUNT_MOTOR_W_TO_N = {
    '电动机 / 高速＞10000r/min': '电动机',
    '电动机 / 1000r/min≤中速≤10000r/min': '电动机',
    '电动机 / 低速＜1000r/min': '电动机',
    '电动机 / 整流式': '电动机',
    '风机 / 高速＞10000r/min': '风机',
    '风机 / 1000r/min≤中速≤10000r/min': '风机',
    '风机 / 低速＜1000r/min': '风机',
    '时间累加器 / 普通(交直流)': '时间累加器',
    '时间累加器 / 可逆驱动': '时间累加器',
    '时间累加器 / 直流子式直流': '时间累加器',
}

_OSCILLATOR_QUALITY_W_TO_N = {
    'A1S': 'A1',
    'A1B': 'A1',
    'A2': 'A2',
    'B1': 'B',
    'B2': 'B',
    'C': 'C',
}


def _match_choice(value, choices):
    """Return the exact option object from choices matching value (preserving int/float type)."""
    if choices is None or isinstance(value, bool):
        return value
    if value in choices and (not isinstance(value, (int, float)) or isinstance(choices[0], type(value))):
        return value
    if isinstance(value, (int, float)):
        for candidate in choices:
            if not isinstance(candidate, bool) and isinstance(candidate, (int, float)) and candidate == value:
                return candidate
    if value in choices:
        return value
    return None


def _is_eligible_scalar(field, value):
    """Validate that value conforms to a single non-excluded Input definition."""
    if field.key in _EXCLUDED_KEYS or field.scope != 'device' or field.section in _EXCLUDED_SECTIONS:
        return False
    if field.stress_max is not None or field.record_fields:
        return False
    if field.choices is not None:
        if _match_choice(value, field.choices) is not None:
            return True
        if not field.ceil_choices or isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if field.integer and int(value) != value:
        return False
    if not field.allow_negative and (value < 0 or (field.positive and value == 0)):
        return False
    return True


def _map_maturity(value, target_field):
    tier = _MATURITY_TIER_BY_TEXT.get(value)
    if tier is None or target_field.choices is None or tier >= len(target_field.choices):
        return None
    return target_field.choices[tier]


def _translate_ic_section(source_params, target_fields, *, src_prefix='', dst_prefix=''):
    """Translate intrinsic IC fields between Chapter 5 and B.2.2.2 sections."""
    out = {}
    tech_key = src_prefix + 'technology'
    if tech_key in source_params and (dst_prefix + 'technology') in target_fields:
        mapped_tech = _TECHNOLOGY_PAIRS.get(source_params[tech_key])
        if mapped_tech and mapped_tech in target_fields[dst_prefix + 'technology'].choices:
            out[dst_prefix + 'technology'] = mapped_tech

    for skey, dkey in (
        ('gate_count', 'gate_count'),
        ('gate_count', 'complexity'),
        ('complexity', 'gate_count'),
        ('complexity', 'transistor_count'),
        ('transistor_count', 'complexity'),
        ('complexity', 'bit_count'),
        ('bit_count', 'complexity'),
        ('lead_count', 'lead_count'),
    ):
        full_src = src_prefix + skey
        full_dst = dst_prefix + dkey
        if full_src in source_params and full_dst in target_fields:
            dst_field = target_fields[full_dst]
            matched = _match_choice(source_params[full_src], dst_field.choices)
            if matched is not None:
                out[full_dst] = matched
            elif dst_field.ceil_choices and _is_eligible_scalar(dst_field, source_params[full_src]):
                out[full_dst] = source_params[full_src]

    pkg_src = src_prefix + 'package'
    pkg_dst = dst_prefix + 'package'
    if pkg_src in source_params and pkg_dst in target_fields:
        raw_pkg = source_params[pkg_src]
        mapped_pkg = _WORKING_TO_NONOP_PACKAGE.get(raw_pkg) or _NONOP_TO_WORKING_PACKAGE.get(raw_pkg)
        if mapped_pkg and mapped_pkg in target_fields[pkg_dst].choices:
            out[pkg_dst] = mapped_pkg

    qual_src = src_prefix + 'quality'
    qual_dst = dst_prefix + 'quality'
    if qual_src in source_params and qual_dst in target_fields:
        q = source_params[qual_src]
        if q in target_fields[qual_dst].choices:
            out[qual_dst] = q

    mat_src = src_prefix + 'maturity'
    mat_dst = dst_prefix + 'maturity'
    if mat_src in source_params and mat_dst in target_fields:
        mapped_mat = _map_maturity(source_params[mat_src], target_fields[mat_dst])
        if mapped_mat is not None:
            out[mat_dst] = mapped_mat
    return out


def _extract_quality(source_model, source_parameters):
    """Extract the single effective quality grade from either plain or grouped-quality models."""
    src_fields = [f for f in MODELS[source_model].fields if f.scope == 'device']
    if 'quality' in source_parameters:
        q_spec = next((f for f in src_fields if f.key == 'quality'), None)
        if q_spec is not None and q_spec.active(source_parameters):
            return source_parameters['quality']
    for f in src_fields:
        if f.key.endswith('_quality') and f.key in source_parameters and f.active(source_parameters):
            return source_parameters[f.key]
    return None


def _is_valid_subtype_quality(target_model, target_params, grade):
    """Reject grades forbidden by a specific subtype in consolidated non-operating models."""
    kind = target_params.get('kind')
    if target_model == '国产电阻器（非工作详细）':
        if kind is None:
            return grade in ('A2', 'B1', 'B2', 'C')
        if kind.startswith(('碳膜', '玻璃釉')):
            return grade in ('A2', 'B1', 'B2', 'C')
        if kind.startswith(('线绕电阻器', '箔式电阻器', '功率线绕', '功率非线绕')):
            return grade in ('A1M', 'A2', 'B1', 'B2', 'C')
        return grade in ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C')
    if target_model == '国产电位器（非工作详细）':
        if kind is None:
            return grade in ('A2', 'B1', 'B2', 'C')
        return grade != 'A1M' if kind == '合成碳膜' else True
    if target_model in ('国产电容器（非工作详细）', '国产电容网络（非工作详细）'):
        if kind is None:
            return grade in ('A2', 'B1', 'B2')
        if kind.startswith('纸和薄膜'):
            return grade in ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C1', 'C2')
        if kind.startswith('可变电容器'):
            return grade in ('A1', 'A2', 'B1', 'B2', 'C')
        if kind == '氧化铌电解电容器':
            return grade in ('A2', 'B1', 'B2', 'C')
        return grade in ('A1P', 'A1M', 'A2', 'B1', 'B2', 'C')
    if target_model == '国产电容网络':
        sub_model = target_params.get('capacitor_model')
        if not sub_model or sub_model not in MODELS:
            return False
        q_spec = next((f for f in MODELS[sub_model].fields if f.key == 'quality'), None)
        return q_spec is not None and grade in q_spec.choices
    if target_model == '国产二极管（非工作详细）':
        if kind is None:
            return grade in ('A3', 'A4', 'A5', 'A6', 'B1', 'B2', 'C')
        if kind.startswith(('微波二极管 / 微波硅检波', '微波二极管 / 微波硅混频')):
            return grade != 'A2'
        return True
    if target_model == '国产晶体管（非工作详细）':
        if kind is None:
            return grade in ('A3', 'A4', 'A5', 'A6', 'B1', 'B2', 'C')
        if kind in ('单结晶体管', '闸流晶体管'):
            return grade not in ('A1', 'A2')
        if kind.startswith(('硅场效应', '微波晶体管')):
            return grade != 'A1'
        return True
    if target_model == '国产继电器（非工作详细）':
        if kind is None:
            return grade in ('A2', 'B1', 'B2', 'C')
        if kind == '固体继电器':
            return grade in ('A1Y', 'A1W', 'A2', 'B1', 'B2', 'C')
        return grade in ('A1P', 'A1M', 'A1L', 'A2', 'B1', 'B2', 'C')
    if target_model == '国产微特电机（非工作详细）':
        if kind is None:
            return grade in ('A1', 'A2', 'C')
        if kind in ('电动机', '风机'):
            return grade in ('A1', 'A2', 'B1', 'B2', 'C')
        return grade in ('A1', 'A2', 'B', 'C')
    return True


def _apply_quality(source_model, source_parameters, target_model, target_params):
    """Map quality grade into the active quality/groupN_quality slot of target_model."""
    grade = _extract_quality(source_model, source_parameters)
    if grade is None:
        return
    if source_model in ('国产振荡器', '国产振荡器和谐振器（工作计数）') and target_model in (
        '国产振荡器（非工作详细）',
        '国产振荡器和谐振器（非工作计数）',
    ):
        if source_model == '国产振荡器' or str(source_parameters.get('kind', '')).endswith('振荡器'):
            grade = _OSCILLATOR_QUALITY_W_TO_N.get(grade, grade)
    target_fields = [f for f in MODELS[target_model].fields if f.scope == 'device']
    for tf in target_fields:
        if (tf.key == 'quality' or tf.key.endswith('_quality')) and tf.active(target_params):
            if tf.choices is None or grade in tf.choices:
                if _is_valid_subtype_quality(target_model, target_params, grade):
                    target_params[tf.key] = grade
            break


def _translate_smt_records(records, target_record_fields):
    """Convert intrinsic SMT connection attributes while stripping thermal/cycle fields."""
    if not isinstance(records, list) or not records:
        return None
    rf_map = {rf.key: rf for rf in target_record_fields}
    converted = []
    for rec in records:
        if not isinstance(rec, dict):
            return None
        new_rec = {}
        raw_cte = rec.get('cte_method')
        if raw_cte in ('实际值', '实测值') and 'cte_method' in rf_map:
            target_cte = '实测值' if '实测值' in rf_map['cte_method'].choices else '实际值'
            new_rec['cte_method'] = target_cte
        elif raw_cte == '表列缺省值':
            new_rec['cte_method'] = '表列缺省值'
        raw_lead = rec.get('lead_structure')
        if raw_lead in ('J或S型引线', 'J 或 S 型引线') and 'lead_structure' in rf_map:
            new_rec['lead_structure'] = (
                'J 或 S 型引线' if 'J 或 S 型引线' in rf_map['lead_structure'].choices else 'J或S型引线'
            )
        elif isinstance(raw_lead, str) and 'lead_structure' in rf_map and raw_lead in rf_map['lead_structure'].choices:
            new_rec['lead_structure'] = raw_lead
        for key in ('board_material', 'package_material', 'board_cte', 'package_cte',
                    'joint_height_mm', 'distance_mm', 'design_life_h'):
            if key in rec and key in rf_map and rf_map[key].active(new_rec):
                val = rec[key]
                if _is_eligible_scalar(rf_map[key], val):
                    matched = _match_choice(val, rf_map[key].choices)
                    if matched is not None:
                        new_rec[key] = matched
        converted.append(new_rec)
    return converted


def _translate_ic_family(source_model, source_params, source_sources, target_model):
    """Handle working <-> nonoperating IC models including microcircuits, MMIC, and mixed-signal."""
    target_fields = {f.key: f for f in MODELS[target_model].fields if f.scope == 'device'}
    target_params = {}
    target_sources = {}

    if source_model == '国产模数混合集成电路' and target_model == '国产模数混合集成电路（非工作详细）':
        a_fields = {f.key: f for f in target_fields['analog_sections'].record_fields}
        d_fields = {f.key: f for f in target_fields['digital_sections'].record_fields}
        analog_sec = _translate_ic_section(source_params, a_fields, src_prefix='analog_', dst_prefix='')
        digital_sec = _translate_ic_section(source_params, d_fields, src_prefix='digital_', dst_prefix='')
        if analog_sec:
            target_params['analog_sections'] = [analog_sec]
        if digital_sec:
            target_params['digital_sections'] = [digital_sec]
        return target_params, target_sources

    if source_model == '国产模数混合集成电路（非工作详细）' and target_model == '国产模数混合集成电路':
        analog_list = source_params.get('analog_sections')
        if isinstance(analog_list, list) and len(analog_list) == 1 and isinstance(analog_list[0], dict):
            target_params.update(_translate_ic_section(analog_list[0], target_fields, dst_prefix='analog_'))
        digital_list = source_params.get('digital_sections')
        if isinstance(digital_list, list) and len(digital_list) == 1 and isinstance(digital_list[0], dict):
            target_params.update(_translate_ic_section(digital_list[0], target_fields, dst_prefix='digital_'))
        return target_params, target_sources

    if source_model == '国产微波单片集成电路' and target_model == '国产微波单片集成电路（非工作详细）':
        if 'process' in source_params and source_params['process'] in _MMIC_PROCESS_PAIRS:
            target_params['kind'] = _MMIC_PROCESS_PAIRS[source_params['process']]
        pkg = source_params.get('package', '')
        if pkg == '密封-扁平(FP)封装':
            target_params['package'] = '密封的扁平封装'
        elif pkg == '密封-金属壳封装':
            target_params['package'] = '密封的金属壳封装'
        elif pkg.startswith('非密封-'):
            target_params['package'] = '非密封器件'
        target_params.update(_translate_ic_section(source_params, {'quality': target_fields['quality'], 'maturity': target_fields['maturity']}))
        return target_params, target_sources

    if source_model == '国产微波单片集成电路（非工作详细）' and target_model == '国产微波单片集成电路':
        if source_params.get('kind') in _MMIC_KIND_TO_PROCESS:
            target_params['process'] = _MMIC_KIND_TO_PROCESS[source_params['kind']]
        pkg = source_params.get('package', '')
        if pkg.startswith('密封的双列直插(DIP)封装'):
            target_params['package'] = '密封-双列直插(DIP)封装'
        elif pkg == '密封的扁平封装':
            target_params['package'] = '密封-扁平(FP)封装'
        elif pkg == '密封的金属壳封装':
            target_params['package'] = '密封-金属壳封装'
        target_params.update(_translate_ic_section(source_params, {'quality': target_fields['quality'], 'maturity': target_fields['maturity']}))
        return target_params, target_sources

    if source_model in _IMPLIED_IC_TECHNOLOGY and 'technology' in target_fields:
        target_params['technology'] = _IMPLIED_IC_TECHNOLOGY[source_model]

    target_params.update(_translate_ic_section(source_params, target_fields))

    if isinstance(source_sources, dict):
        gc_src = source_sources.get('gate_count') or source_sources.get('complexity')
        if isinstance(gc_src, dict) and gc_src.get('kind') == 'gate_count':
            if 'gate_count' in target_fields:
                target_sources['gate_count'] = copy.deepcopy(gc_src)
            elif 'complexity' in target_fields and '门数' in target_fields['complexity'].label:
                target_sources['complexity'] = copy.deepcopy(gc_src)

    return target_params, target_sources


def _translate_passive_detailed(source_model, source_params, target_model):
    """Handle R, RP, RT, C, L detailed working <-> nonoperating mappings."""
    target_params = {}
    if target_model == '国产电阻器（非工作详细）':
        if source_model in _RESISTOR_DIRECT_KIND:
            target_params['kind'] = _RESISTOR_DIRECT_KIND[source_model]
        elif source_model in _RESISTOR_LIMITED_LOW_KIND:
            power = source_params.get('rated_power_W')
            if power is None or (isinstance(power, (int, float)) and not isinstance(power, bool) and 0 < power <= 2):
                target_params['kind'] = _RESISTOR_LIMITED_LOW_KIND[source_model]
                if power is not None:
                    target_params['rated_power_W'] = power
        elif source_model == '国产功率非线绕电阻器':
            target_params['kind'] = '功率非线绕电阻器(功率 P ＞2W)'
            power = source_params.get('rated_power_W')
            if isinstance(power, (int, float)) and not isinstance(power, bool) and power > 2:
                target_params['rated_power_W'] = power
        elif (source_model, source_params.get('kind')) in _RESISTOR_SUBTYPE_PAIR:
            target_params['kind'] = _RESISTOR_SUBTYPE_PAIR[(source_model, source_params['kind'])]
    elif source_model == '国产电阻器（非工作详细）':
        entry = _RESISTOR_NONOP_KIND_MAP.get(source_params.get('kind'))
        if entry and entry[0] == target_model:
            if entry[1] is not None:
                target_params['kind'] = entry[1]
            if 'rated_power_W' in source_params and 'rated_power_W' in {f.key for f in MODELS[target_model].fields}:
                power = source_params['rated_power_W']
                if isinstance(power, (int, float)) and not isinstance(power, bool) and power > 0:
                    target_params['rated_power_W'] = power
    elif source_model == '国产电阻网络' and target_model == '国产电阻网络（非工作详细）':
        kmap = {'单个分立元件': '电阻网络 / (单个分立电阻)', '厚薄膜': '电阻网络 / (单个膜电阻)'}
        if source_params.get('kind') in kmap:
            target_params['kind'] = kmap[source_params['kind']]
        if 'resistor_count' in source_params:
            target_params['element_count'] = source_params['resistor_count']
    elif source_model == '国产电阻网络（非工作详细）' and target_model == '国产电阻网络':
        kmap = {'电阻网络 / (单个分立电阻)': '单个分立元件', '电阻网络 / (单个膜电阻)': '厚薄膜'}
        if source_params.get('kind') in kmap:
            target_params['kind'] = kmap[source_params['kind']]
        if 'element_count' in source_params:
            target_params['resistor_count'] = source_params['element_count']
    elif target_model == '国产敏感电阻器（非工作详细）' and source_model in ('国产热敏电阻器', '国产压敏电阻器', '国产光敏电阻器'):
        target_params['kind'] = source_model.removeprefix('国产').removesuffix('电阻器')
    elif source_model == '国产敏感电阻器（非工作详细）' and target_model in ('国产热敏电阻器', '国产压敏电阻器', '国产光敏电阻器'):
        pass
    elif target_model == '国产电位器（非工作详细）' and source_model in _POTENTIOMETER_WORKING_TO_KIND:
        target_params['kind'] = _POTENTIOMETER_WORKING_TO_KIND[source_model]
    elif source_model == '国产电位器（非工作详细）' and target_model in _POTENTIOMETER_WORKING_TO_KIND:
        pass
    elif target_model == '国产电容器（非工作详细）':
        if source_model in _CAP_MODEL_TO_NONOP_KIND:
            target_params['kind'] = _CAP_MODEL_TO_NONOP_KIND[source_model]
        elif source_model == '国产纸和薄膜电容器' and source_params.get('kind') in _PAPER_FILM_SUBTYPE_TO_NONOP:
            target_params['kind'] = _PAPER_FILM_SUBTYPE_TO_NONOP[source_params['kind']]
        elif source_model == '国产可变电容器' and source_params.get('kind') in _VARIABLE_CAP_SUBTYPE_PAIRS:
            target_params['kind'] = _VARIABLE_CAP_SUBTYPE_PAIRS[source_params['kind']]
        if source_params.get('mounting') in _CAP_MOUNTING_PAIRS:
            target_params['mounting'] = _CAP_MOUNTING_PAIRS[source_params['mounting']]
    elif source_model == '国产电容器（非工作详细）':
        nkind = source_params.get('kind', '')
        if target_model == '国产纸和薄膜电容器':
            if nkind == '纸和薄膜电容器 / 纸介':
                target_params['kind'] = '纸介，金属化纸介'
            elif nkind == '纸和薄膜电容器 / 纸-膜复合介质':
                target_params['kind'] = '纸-膜复合介质'
        elif target_model == '国产可变电容器' and nkind in _VARIABLE_CAP_NONOP_TO_SUBTYPE:
            target_params['kind'] = _VARIABLE_CAP_NONOP_TO_SUBTYPE[nkind]
        if source_params.get('mounting') in _CAP_MOUNTING_PAIRS and target_model in (_CAP_MODEL_TO_NONOP_KIND.keys() | {'国产纸和薄膜电容器'}):
            if target_model != '国产非固体钽电解电容器':
                target_params['mounting'] = _CAP_MOUNTING_PAIRS[source_params['mounting']]
    elif source_model == '国产电容网络' and target_model == '国产电容网络（非工作详细）':
        cap_model = source_params.get('capacitor_model')
        if cap_model in _CAP_MODEL_TO_NONOP_KIND:
            target_params['kind'] = _CAP_MODEL_TO_NONOP_KIND[cap_model]
        elif cap_model == '国产纸和薄膜电容器' and source_params.get('kind') in _PAPER_FILM_SUBTYPE_TO_NONOP:
            target_params['kind'] = _PAPER_FILM_SUBTYPE_TO_NONOP[source_params['kind']]
        elif cap_model == '国产可变电容器' and source_params.get('kind') in _VARIABLE_CAP_SUBTYPE_PAIRS:
            target_params['kind'] = _VARIABLE_CAP_SUBTYPE_PAIRS[source_params['kind']]
        if source_params.get('mounting') in _CAP_MOUNTING_PAIRS:
            target_params['mounting'] = _CAP_MOUNTING_PAIRS[source_params['mounting']]
        if 'capacitor_count' in source_params:
            target_params['element_count'] = source_params['capacitor_count']
    elif source_model == '国产电容网络（非工作详细）' and target_model == '国产电容网络':
        nkind = source_params.get('kind', '')
        if nkind in _CAP_NONOP_KIND_TO_MODEL:
            target_params['capacitor_model'] = _CAP_NONOP_KIND_TO_MODEL[nkind]
        elif nkind.startswith('纸和薄膜电容器'):
            target_params['capacitor_model'] = '国产纸和薄膜电容器'
            if nkind == '纸和薄膜电容器 / 纸介':
                target_params['kind'] = '纸介，金属化纸介'
            elif nkind == '纸和薄膜电容器 / 纸-膜复合介质':
                target_params['kind'] = '纸-膜复合介质'
        elif nkind in _VARIABLE_CAP_NONOP_TO_SUBTYPE:
            target_params['capacitor_model'] = '国产可变电容器'
            target_params['kind'] = _VARIABLE_CAP_NONOP_TO_SUBTYPE[nkind]
        if source_params.get('mounting') in _CAP_MOUNTING_PAIRS and target_params.get('capacitor_model') != '国产非固体钽电解电容器':
            target_params['mounting'] = _CAP_MOUNTING_PAIRS[source_params['mounting']]
        if 'element_count' in source_params:
            target_params['capacitor_count'] = source_params['element_count']
    return target_params


def _translate_discrete_detailed(source_model, source_params, target_model):
    """Handle D, Q, LED, V detailed working <-> nonoperating mappings."""
    target_params = {}
    if target_model == '国产二极管（非工作详细）':
        if source_model == '国产普通二极管' and source_params.get('kind') in _ORDINARY_DIODE_SUBTYPES:
            target_params['kind'] = f'普通二极管 / {source_params["kind"]}'
        elif source_model == '国产电压调整、电压基准及电流调整二极管':
            target_params['kind'] = '电压调整、电压基准及电流调整二极管'
        elif source_model == '国产微波硅检波二极管':
            target_params['kind'] = '微波二极管 / 微波硅检波二极管'
        elif source_model == '国产微波硅混频二极管':
            target_params['kind'] = '微波二极管 / 微波硅混频二极管'
        elif source_model == '国产其他微波二极管':
            target_params['kind'] = '微波二极管 / 变容，阶跃，隧道，体效应，PIN，雪崩二极管'
        if source_params.get('temperature_basis') == '工作环境温度':
            target_params['temperature_basis'] = '非工作环境温度'
    elif source_model == '国产二极管（非工作详细）':
        nkind = source_params.get('kind', '')
        if target_model == '国产普通二极管' and nkind in _DIODE_NONOP_TO_ORDINARY:
            target_params['kind'] = _DIODE_NONOP_TO_ORDINARY[nkind]
        if source_params.get('temperature_basis') == '非工作环境温度':
            target_params['temperature_basis'] = '工作环境温度'
    elif target_model == '国产晶体管（非工作详细）':
        if source_model == '国产双极型晶体管':
            if source_params.get('kind') in _BIPOLAR_TRANSISTOR_TO_NONOP:
                target_params['kind'] = _BIPOLAR_TRANSISTOR_TO_NONOP[source_params['kind']]
            if 'rated_power_W' in source_params:
                target_params['rated_power_W'] = source_params['rated_power_W']
        elif source_model == '国产硅场效应晶体管':
            target_params['kind'] = '硅场效应晶体管'
            if 'rated_power_W' in source_params:
                target_params['rated_power_W'] = source_params['rated_power_W']
        elif source_model == '国产单结晶体管':
            target_params['kind'] = '单结晶体管'
        elif source_model == '国产闸流晶体管':
            target_params['kind'] = '闸流晶体管'
            if 'rated_current_A' in source_params:
                target_params['rated_current_A'] = source_params['rated_current_A']
        elif source_model in ('国产微波双极型晶体管', '国产微波场效应晶体管'):
            target_params['kind'] = '微波晶体管(双极型晶体管、场效应晶体管)'
    elif source_model == '国产晶体管（非工作详细）':
        nkind = source_params.get('kind', '')
        if target_model == '国产双极型晶体管':
            if nkind in _NONOP_TRANSISTOR_TO_BIPOLAR:
                target_params['kind'] = _NONOP_TRANSISTOR_TO_BIPOLAR[nkind]
            if 'rated_power_W' in source_params:
                target_params['rated_power_W'] = source_params['rated_power_W']
        elif target_model == '国产硅场效应晶体管' and 'rated_power_W' in source_params:
            target_params['rated_power_W'] = source_params['rated_power_W']
        elif target_model == '国产闸流晶体管' and 'rated_current_A' in source_params:
            target_params['rated_current_A'] = source_params['rated_current_A']
    elif source_model == '国产光电子器件' and target_model == '国产光电子器件（非工作详细）':
        kind = source_params.get('kind')
        if kind in _OPTO_PAIRS:
            target_params['kind'] = _OPTO_PAIRS[kind]
        elif kind is not None:
            target_params['kind'] = kind
        if 'digit_count' in source_params:
            target_params['digit_count'] = source_params['digit_count']
    elif source_model == '国产光电子器件（非工作详细）' and target_model == '国产光电子器件':
        kind = source_params.get('kind')
        if kind in _OPTO_REVERSE_PAIRS:
            target_params['kind'] = _OPTO_REVERSE_PAIRS[kind]
        elif kind is not None:
            target_params['kind'] = kind
        if 'digit_count' in source_params:
            target_params['digit_count'] = source_params['digit_count']
    elif target_model == '国产真空电子器件（非工作详细）':
        if source_model == '国产普通电子管' and 'kind' in source_params:
            target_params['kind'] = source_params['kind']
        elif source_model == '国产连续波速调管':
            target_params['kind'] = '速调管 / 连续波'
        elif source_model == '国产脉冲速调管':
            target_params['kind'] = '速调管 / 脉冲速调管'
        elif source_model == '国产行波管':
            target_params['kind'] = '行波管'
        elif source_model == '国产磁控管':
            target_params['kind'] = '磁控管'
    elif source_model == '国产真空电子器件（非工作详细）':
        if target_model == '国产普通电子管' and 'kind' in source_params:
            target_params['kind'] = source_params['kind']
    return target_params


def _translate_electromech_and_other_detailed(source_model, source_params, target_model):
    """Handle relays, switches, connectors, motors, filters, lasers, gyros, fiber, PCB, SMT and OTHER."""
    target_params = {}
    if source_model == '国产变压器' and target_model == '国产变压器（非工作详细）':
        if source_params.get('kind') in _TRANSFORMER_W_TO_N:
            target_params['kind'] = _TRANSFORMER_W_TO_N[source_params['kind']]
    elif source_model == '国产变压器（非工作详细）' and target_model == '国产变压器':
        if source_params.get('kind') in _TRANSFORMER_N_TO_W:
            target_params['kind'] = _TRANSFORMER_N_TO_W[source_params['kind']]
    elif target_model == '国产继电器（非工作详细）':
        if source_model == '国产固体继电器':
            target_params['kind'] = '固体继电器'
        elif source_model == '国产机电继电器' and source_params.get('enclosure') == '塑料和环氧树脂封装':
            target_params['kind'] = '机电继电器 / 非密封'
    elif source_model == '国产开关' and target_model == '国产开关（非工作详细）':
        if source_params.get('switch_type') in _SWITCH_PAIRS:
            target_params['kind'] = _SWITCH_PAIRS[source_params['switch_type']]
    elif source_model == '国产开关（非工作详细）' and target_model == '国产开关':
        if source_params.get('kind') in _SWITCH_REVERSE:
            target_params['switch_type'] = _SWITCH_REVERSE[source_params['kind']]
    elif source_model == '国产电连接器' and target_model == '国产电连接器（非工作详细）':
        if source_params.get('connector_type') == '射频同轴连接器':
            target_params['kind'] = '射频同轴连接器、圆形连接器'
    elif source_model == '国产电连接器（非工作详细）' and target_model == '国产电连接器':
        if source_params.get('kind') == '矩形连接器、印制电路板用连接器':
            target_params['connector_type'] = '低频连接器'
    elif target_model == '国产微特电机（非工作详细）':
        if source_model == '国产普通电动机和风机':
            mmap = {'普通电动机': '电动机', '风机': '风机'}
            if source_params.get('motor_type') in mmap:
                target_params['kind'] = mmap[source_params['motor_type']]
        elif source_model == '国产自整角机和旋转变压器':
            target_params['kind'] = '旋转变压器、自整角机'
        elif source_model == '国产时间累计器':
            target_params['kind'] = '时间累加器'
    elif source_model == '国产微特电机（非工作详细）' and target_model == '国产普通电动机和风机':
        mmap = {'电动机': '普通电动机', '风机': '风机'}
        if source_params.get('kind') in mmap:
            target_params['motor_type'] = mmap[source_params['kind']]
    elif source_model == '国产SMT互连' and target_model == '国产SMT互连（非工作详细）':
        rf = MODELS[target_model].fields[0].record_fields
        recs = _translate_smt_records(source_params.get('connections'), rf)
        if recs:
            target_params['connections'] = recs
    elif source_model == '国产SMT互连（非工作详细）' and target_model == '国产SMT互连':
        rf = MODELS[target_model].fields[0].record_fields
        recs = _translate_smt_records(source_params.get('connections'), rf)
        if recs:
            target_params['connections'] = recs
    elif source_model == '国产磁性器件' and target_model == '国产磁性器件（非工作详细）':
        m_opts = next(f.choices for f in MODELS[target_model].fields if f.key == 'kind')
        raw = source_params.get('magnetic_type', '')
        if raw.startswith('大功率'):
            target_params['kind'] = m_opts[0]
        elif raw.startswith('小功率'):
            target_params['kind'] = m_opts[1]
    elif source_model == '国产磁性器件（非工作详细）' and target_model == '国产磁性器件':
        m_opts = next(f.choices for f in MODELS[target_model].fields if f.key == 'magnetic_type')
        raw = source_params.get('kind', '')
        if raw.startswith('大功率'):
            target_params['magnetic_type'] = m_opts[0]
        elif raw.startswith('小功率'):
            target_params['magnetic_type'] = m_opts[1]
    elif source_model == '国产压电陶瓷介电石英晶体和机械滤波器' and target_model == '国产滤波器（非工作详细）':
        if 'filter_type' in source_params:
            target_params['kind'] = source_params['filter_type']
    elif source_model == '国产滤波器（非工作详细）' and target_model == '国产压电陶瓷介电石英晶体和机械滤波器':
        if 'kind' in source_params:
            target_params['filter_type'] = source_params['kind']
    elif source_model == '国产LC滤波器' and target_model == '国产LC滤波器（非工作详细）':
        mmap = {'组成元器件失效率求和': '组成元器件非工作失效率求和', '信息不充分时近似预计': '式 B.30 近似模型'}
        if source_params.get('method') in mmap:
            target_params['method'] = mmap[source_params['method']]
        if 'filter_type' in source_params:
            target_params['kind'] = source_params['filter_type']
    elif source_model == '国产LC滤波器（非工作详细）' and target_model == '国产LC滤波器':
        mmap = {'组成元器件非工作失效率求和': '组成元器件失效率求和', '式 B.30 近似模型': '信息不充分时近似预计'}
        if source_params.get('method') in mmap:
            target_params['method'] = mmap[source_params['method']]
        if 'kind' in source_params:
            target_params['filter_type'] = source_params['kind']
    elif source_model == '国产电池' and target_model == '国产电池（非工作详细）':
        if 'battery_type' in source_params:
            target_params['kind'] = source_params['battery_type']
    elif source_model == '国产电池（非工作详细）' and target_model == '国产电池':
        if 'kind' in source_params:
            target_params['battery_type'] = source_params['kind']
    elif source_model == '国产灯' and target_model == '国产灯（非工作详细）':
        if 'lamp_type' in source_params:
            target_params['kind'] = source_params['lamp_type']
    elif source_model == '国产灯（非工作详细）' and target_model == '国产灯':
        if 'kind' in source_params:
            target_params['lamp_type'] = source_params['kind']
    elif source_model == '国产激光器' and target_model == '国产激光器（非工作详细）':
        lt = source_params.get('laser_type')
        if lt is not None:
            target_params['kind'] = _LASER_W_TO_N.get(lt, lt)
    elif source_model == '国产激光器（非工作详细）' and target_model == '国产激光器':
        lk = source_params.get('kind')
        if lk is not None:
            target_params['laser_type'] = _LASER_N_TO_W.get(lk, lk)
    elif source_model == '国产光纤连接器' and target_model == '国产光纤连接器（非工作详细）':
        if 'cores' in source_params:
            target_params['fiber_count'] = source_params['cores']
        if 'coupling' in source_params:
            target_params['connection_mode'] = source_params['coupling']
    elif source_model == '国产光纤连接器（非工作详细）' and target_model == '国产光纤连接器':
        if 'fiber_count' in source_params:
            target_params['cores'] = source_params['fiber_count']
        if 'connection_mode' in source_params:
            target_params['coupling'] = source_params['connection_mode']
    elif source_model == '国产其他电子元器件' and target_model == '国产其他电子元器件（非工作详细）':
        dt = source_params.get('device_type')
        if dt is not None:
            target_params['kind'] = _OTHER_W_TO_N.get(dt, dt)
        if 'poles' in source_params:
            target_params['pole_count'] = source_params['poles']
    elif source_model == '国产其他电子元器件（非工作详细）' and target_model == '国产其他电子元器件':
        nk = source_params.get('kind')
        if nk is not None:
            target_params['device_type'] = _OTHER_N_TO_W.get(nk, nk)
        if 'pole_count' in source_params:
            target_params['poles'] = source_params['pole_count']
    return target_params


def _translate_counting(source_model, source_params, target_model):
    """Handle working_count <-> nonoperating_count mappings."""
    target_params = {}
    kind = source_params.get('kind')
    target_kind_field = next((f for f in MODELS[target_model].fields if f.key == 'kind'), None)
    target_choices = target_kind_field.choices if target_kind_field else ()

    if kind is not None and target_kind_field is not None:
        if kind in target_choices:
            target_params['kind'] = kind
        elif source_model in ('国产MOS可擦可编程只读存储器（工作计数）', '国产MOS动态随机存储器（工作计数）') and target_model in (
            '国产密封存储器（非工作计数）',
            '国产非密封存储器（非工作计数）',
        ):
            band = kind.split(' / ', 1)[0]
            candidate = f'{band} / MOS'
            if candidate in target_choices:
                target_params['kind'] = candidate
        elif source_model == '国产微波单片集成电路（工作计数）' and target_model == '国产微波单片集成电路（非工作计数）':
            mapped = _COUNT_MMIC_PAIRS.get(kind, kind)
            if mapped in target_choices:
                target_params['kind'] = mapped
        elif source_model == '国产微波单片集成电路（非工作计数）' and target_model == '国产微波单片集成电路（工作计数）':
            mapped = _COUNT_MMIC_REVERSE.get(kind, kind)
            if mapped in target_choices:
                target_params['kind'] = mapped
        elif source_model == '国产声表面波器件（工作计数）' and target_model == '国产声表面波器件（非工作计数）':
            target_params['kind'] = '声表面波器件'
        elif source_model == '国产半导体分立器件（工作计数）' and target_model == '国产半导体分立器件（非工作计数）':
            mapped = _COUNT_DISCRETE_W_TO_N.get(kind)
            if mapped in target_choices:
                target_params['kind'] = mapped
        elif source_model == '国产半导体分立器件（非工作计数）' and target_model == '国产半导体分立器件（工作计数）':
            mapped = _COUNT_DISCRETE_N_TO_W.get(kind)
            if mapped in target_choices:
                target_params['kind'] = mapped
        elif source_model == '国产真空电子器件（工作计数）' and target_model == '国产真空电子器件（非工作计数）':
            if kind.startswith('磁控管'):
                target_params['kind'] = '磁控管'
            elif kind in _COUNT_VACUUM_PAIRS:
                target_params['kind'] = _COUNT_VACUUM_PAIRS[kind]
        elif source_model == '国产真空电子器件（非工作计数）' and target_model == '国产真空电子器件（工作计数）':
            if kind in _COUNT_VACUUM_REVERSE:
                target_params['kind'] = _COUNT_VACUUM_REVERSE[kind]
        elif source_model == '国产电阻器（工作计数）' and target_model == '国产电阻器（非工作计数）':
            if kind == '电阻网络(分立电阻NR=10)':
                target_params['kind'] = '电阻网络(单个分立电阻)'
                target_params['element_count'] = 10
            elif kind == '电阻网络(膜电阻，NR=10)':
                target_params['kind'] = '电阻网络(单个膜电阻)'
                target_params['element_count'] = 10
            else:
                mapped = _COUNT_RESISTOR_PAIRS.get(kind, kind)
                if mapped in target_choices:
                    target_params['kind'] = mapped
                    if mapped in ('功率线绕电阻器', '功率非线绕电阻器'):
                        target_params['high_power_class'] = 'P>2W'
        elif source_model == '国产电阻器（非工作计数）' and target_model == '国产电阻器（工作计数）':
            if kind == '电阻网络(单个分立电阻)' and source_params.get('element_count') == 10:
                target_params['kind'] = '电阻网络(分立电阻NR=10)'
            elif kind == '电阻网络(单个膜电阻)' and source_params.get('element_count') == 10:
                target_params['kind'] = '电阻网络(膜电阻，NR=10)'
            else:
                mapped = _COUNT_RESISTOR_REVERSE.get(kind, kind)
                if mapped in target_choices:
                    target_params['kind'] = mapped
        elif source_model == '国产电位器（工作计数）' and target_model == '国产电位器（非工作计数）':
            if kind == '金属玻璃釉电位器':
                target_params['kind'] = '玻璃釉电位器'
        elif source_model == '国产电位器（非工作计数）' and target_model == '国产电位器（工作计数）':
            if kind == '玻璃釉电位器':
                target_params['kind'] = '金属玻璃釉电位器'
        elif source_model == '国产电容器（工作计数）' and target_model == '国产电容器（非工作计数）':
            mapped = _COUNT_CAP_EXACT_PAIRS.get(kind, kind)
            if mapped in target_choices:
                target_params['kind'] = mapped
        elif source_model == '国产电容器（非工作计数）' and target_model == '国产电容器（工作计数）':
            if kind.startswith('有机介质电容器'):
                target_params['kind'] = '纸和薄膜电容器'
            else:
                mapped = _COUNT_CAP_EXACT_REVERSE.get(kind, kind)
                if mapped in target_choices:
                    target_params['kind'] = mapped
        elif source_model == '国产感性元件（工作计数）' and target_model == '国产感性元件（非工作计数）':
            mapped = _COUNT_INDUCTOR_W_TO_N.get(kind, kind)
            if mapped in target_choices:
                target_params['kind'] = mapped
        elif source_model == '国产继电器（工作计数）' and target_model == '国产继电器（非工作计数）':
            if kind == '固体电器继电器':
                target_params['kind'] = '固体继电器'
        elif source_model == '国产继电器（非工作计数）' and target_model == '国产继电器（工作计数）':
            if kind == '固体继电器':
                target_params['kind'] = '固体电器继电器'
        elif source_model == '国产电连接器（工作计数）' and target_model == '国产电连接器（非工作计数）':
            if kind == '射频同轴连接器':
                target_params['kind'] = '射频同轴连接器、圆形连接器'
            elif kind.startswith('低频连接器 / 印制电路板'):
                target_params['kind'] = '矩形连接器、印制电路板用连接器'
        elif source_model == '国产微特电机（工作计数）' and target_model == '国产微特电机（非工作计数）':
            mapped = _COUNT_MOTOR_W_TO_N.get(kind, kind)
            if mapped in target_choices:
                target_params['kind'] = mapped
        elif source_model == '国产振荡器和谐振器（工作计数）' and target_model == '国产振荡器和谐振器（非工作计数）':
            if kind.endswith('振荡器'):
                target_params['kind'] = '振荡器'
        elif source_model == '国产激光器（工作计数）' and target_model == '国产激光器（非工作计数）':
            if kind == '钝化钕掺杂的钇铝石榴石(ND：YAG)棒激光器':
                target_params['kind'] = '氧化钕掺杂的钇铝石榴石(ND:YAG)棒激光器'
        elif source_model == '国产激光器（非工作计数）' and target_model == '国产激光器（工作计数）':
            if kind == '氧化钕掺杂的钇铝石榴石(ND:YAG)棒激光器':
                target_params['kind'] = '钝化钕掺杂的钇铝石榴石(ND：YAG)棒激光器'
    return target_params


def translate_state_parameters(source_model, source_parameters, source_input_sources, target_model):
    """Translate compatible intrinsic parameters and gate_count source across working/nonoperating models."""
    if (source_model in _SPECIAL_MODELS or target_model in _SPECIAL_MODELS
            or source_model not in MODELS or target_model not in MODELS):
        return {}, {}
    if not (source_model.startswith('国产') and target_model.startswith('国产')):
        return {}, {}
    if model_state(source_model) == model_state(target_model):
        return {}, {}
    if MODELS[source_model].category != MODELS[target_model].category:
        return {}, {}

    src_params = source_parameters if isinstance(source_parameters, dict) else {}
    src_sources = source_input_sources if isinstance(source_input_sources, dict) else {}
    src_spec = MODELS[source_model]
    dst_spec = MODELS[target_model]
    target_fields = {f.key: f for f in dst_spec.fields if f.scope == 'device'}
    candidate_params = {}
    target_sources = {}

    if src_spec.method in ('working_count', 'nonoperating_count') and dst_spec.method in ('working_count', 'nonoperating_count'):
        candidate_params.update(_translate_counting(source_model, src_params, target_model))
    elif src_spec.category == 'IC' and src_spec.method in ('working_stress', 'nonoperating_stress') and dst_spec.method in ('working_stress', 'nonoperating_stress'):
        ic_params, ic_sources = _translate_ic_family(source_model, src_params, src_sources, target_model)
        candidate_params.update(ic_params)
        target_sources.update(ic_sources)
    else:
        for translator in (
            _translate_passive_detailed,
            _translate_discrete_detailed,
            _translate_electromech_and_other_detailed,
        ):
            candidate_params.update(translator(source_model, src_params, target_model))

    # Fill same-key non-excluded fields that were not already mapped by family-specific logic.
    src_field_map = {f.key: f for f in src_spec.fields if f.scope == 'device'}
    for key, value in src_params.items():
        if key in candidate_params or key == 'kind' or key.endswith('_quality') or key == 'quality':
            continue
        if key not in target_fields or key not in src_field_map:
            continue
        sf = src_field_map[key]
        tf = target_fields[key]
        if key in _EXCLUDED_KEYS or sf.section in _EXCLUDED_SECTIONS or tf.section in _EXCLUDED_SECTIONS:
            continue
        if key == 'maturity':
            mapped_mat = _map_maturity(value, tf)
            if mapped_mat is not None:
                candidate_params[key] = mapped_mat
            continue
        if tf.choices is not None:
            matched = _match_choice(value, tf.choices)
            if matched is not None and _is_eligible_scalar(tf, matched):
                candidate_params[key] = matched
        elif _is_eligible_scalar(tf, value):
            candidate_params[key] = value

    # Handle same-model/direct 'kind' copies (e.g. 国产焊接点, 国产电感器) if not already translated.
    if 'kind' in target_fields and 'kind' not in candidate_params and 'kind' in src_params:
        if source_model == target_model or f'{source_model}（非工作详细）' == target_model or f'{target_model}（非工作详细）' == source_model:
            matched = _match_choice(src_params['kind'], target_fields['kind'].choices)
            if matched is not None:
                candidate_params['kind'] = matched

    # Apply quality in target slot only after target kind/method is known.
    if source_model != '国产模数混合集成电路' and target_model != '国产模数混合集成电路':
        _apply_quality(source_model, src_params, target_model, candidate_params)

    # Prune inactive or invalid fields so Component.validate() always succeeds.
    cleaned = {}
    for field in dst_spec.fields:
        if field.scope != 'device' or field.key not in candidate_params:
            continue
        if not field.active(candidate_params):
            continue
        val = candidate_params[field.key]
        if field.record_fields:
            cleaned[field.key] = val
        elif field.choices is not None:
            matched = _match_choice(val, field.choices)
            if matched is not None and _is_eligible_scalar(field, matched):
                cleaned[field.key] = matched
            elif field.ceil_choices and _is_eligible_scalar(field, val):
                cleaned[field.key] = (
                    int(val)
                    if field.integer and isinstance(val, float) and val.is_integer()
                    else val
                )
        elif _is_eligible_scalar(field, val):
            cleaned[field.key] = val

    scalar_only = {k: v for k, v in cleaned.items() if k not in target_sources}
    try:
        validate_parameters(target_model, scalar_only, allow_incomplete=True)
    except ValueError:
        return {}, {}

    if target_sources:
        from .domain import Component
        try:
            Component(
                name='check',
                model=target_model,
                quantity=1,
                parameters=copy.deepcopy(scalar_only),
                domestic=True,
                input_sources=copy.deepcopy(target_sources),
            ).validate(_validate_profiles=False)
            cleaned = scalar_only
        except ValueError:
            rejected_keys = set(target_sources)
            target_sources = {}
            try:
                validate_parameters(target_model, cleaned, allow_incomplete=True)
            except ValueError:
                cleaned = {k: v for k, v in cleaned.items() if k not in rejected_keys}
                try:
                    validate_parameters(target_model, cleaned, allow_incomplete=True)
                except ValueError:
                    return {}, {}

    return cleaned, target_sources


def backfill_state_snapshot(source_snapshot, target_snapshot):
    """Fill missing reusable keys into an existing target snapshot without overwriting user edits."""
    src_model = source_snapshot.get('model')
    dst_model = target_snapshot.get('model')
    translated_params, translated_sources = translate_state_parameters(
        src_model,
        source_snapshot.get('parameters'),
        source_snapshot.get('input_sources'),
        dst_model,
    )
    existing_params = copy.deepcopy(target_snapshot.get('parameters') or {})
    existing_sources = copy.deepcopy(target_snapshot.get('input_sources') or {})
    if not translated_params and not translated_sources:
        return existing_params, existing_sources

    dst_spec = MODELS[dst_model]
    candidate_params = copy.deepcopy(existing_params)
    for field in dst_spec.fields:
        key = field.key
        if field.scope != 'device' or key not in translated_params:
            continue
        if key in existing_params or key in existing_sources or key in translated_sources:
            continue
        if not field.active(candidate_params):
            continue
        value = translated_params[key]
        if (key == 'quality' or key.endswith('_quality')) and not _is_valid_subtype_quality(
            dst_model, candidate_params, value
        ):
            continue
        candidate_params[key] = copy.deepcopy(value)

    candidate_sources = copy.deepcopy(existing_sources)
    dst_fields = {f.key: f for f in dst_spec.fields if f.scope == 'device'}
    for key, src in translated_sources.items():
        if key in candidate_params or key in candidate_sources or key not in dst_fields:
            continue
        if dst_fields[key].active(candidate_params):
            candidate_sources[key] = copy.deepcopy(src)

    scalar_only = {k: v for k, v in candidate_params.items() if k not in candidate_sources}
    try:
        validate_parameters(dst_model, scalar_only, allow_incomplete=True)
        if candidate_sources != existing_sources:
            from .domain import Component
            Component(
                name='check',
                model=dst_model,
                quantity=1,
                parameters=copy.deepcopy(candidate_params),
                domestic=True,
                input_sources=copy.deepcopy(candidate_sources),
            ).validate(_validate_profiles=False)
    except ValueError:
        return existing_params, existing_sources
    return candidate_params, candidate_sources


def reusable_from_component(component, target_model, target_state):
    """Extract reusable parameters/sources from a pending component's opposite state profile."""
    if component is None or not target_model or target_model in _SPECIAL_MODELS:
        return {}, {}
    other_state = 'working' if target_state == 'nonoperating' else 'nonoperating'
    snap = component.profiles.get(other_state)
    if isinstance(snap, dict) and snap.get('model') not in _SPECIAL_MODELS:
        params, sources = translate_state_parameters(
            snap['model'], snap.get('parameters', {}), snap.get('input_sources', {}), target_model
        )
        if params or sources:
            return params, sources
    if component.model not in _SPECIAL_MODELS:
        return translate_state_parameters(
            component.model, component.parameters, component.input_sources, target_model
        )
    return {}, {}
