"""Appendix D scalar tools, not a system PoF simulation or a constant library."""

import math
from dataclasses import dataclass

from .common import OUTPUT_LABELS, RATE_UNIT, AnalysisResult, decimal, finite, required_text, shown

K_EV = 1.380649e-23 / 1.602176634e-19


@dataclass(frozen=True)
class PhysicsField:
    key: str
    label: str
    unit: str
    default: float | None = None
    minimum: float | None = 0
    maximum: float | None = None
    strictly_positive: bool = True


@dataclass(frozen=True)
class PhysicsTool:
    name: str
    reference: str
    formula: str
    fields: tuple[PhysicsField, ...]
    calculator: object
    units: dict
    note: str = ''


def field(key, label, unit, **kwargs):
    return PhysicsField(key, label, unit, **kwargs)


def _em(p):
    return {'rate': p['a'] * p['current_density'] ** p['n'] * math.exp(-p['ea'] / (K_EV * p['temperature_K']))}


def _hci(p):
    return {'rate': p['a'] * (p['current_A'] / p['width_um']) ** p['m'] * math.exp(-p['ea'] / (K_EV * p['temperature_K']))}


def _tddb(p):
    t = p['temperature_K']
    return {'rate': p['a_tddb'] * p['material_a'] ** (1 / p['beta']) * p['voltage_V'] ** (p['a'] + p['b'] * t)
            * math.exp(p['c'] / t + p['d'] / t**2)}


def _corrosion(p):
    seconds = (p['k1'] * p['k2'] * p['k3'] / p['k4'] * p['width_cm']**2 * p['height_cm'] * p['valence']
               * p['density_g_cm3'] * 96500 / (4 * p['atomic_weight_g_mol'] * p['voltage_V'])
               * p['resistivity_ohm_cm'] / p['dielectric_cm'])
    return {'time_seconds': seconds, 'time_hours': seconds / 3600}


def _environment(p):
    return {'k4': (p['reference_rh'] / p['rh'])**p['n'] * math.exp(p['ea'] / K_EV * (
        1 / p['reference_temperature_K'] - 1 / p['temperature_K']))}


def _vibration_response(p):
    frequency = p['frequency_Hz']
    a = .5 if frequency <= 100 else .5 + .005 * (frequency - 100) if frequency < 200 else 1 if frequency <= 300 else 1 + .01 * (frequency - 300) if frequency < 400 else 2
    q = a * math.sqrt(frequency)
    rms = math.sqrt(math.pi / 2 * p['psd_G2_Hz'] * frequency * q)
    displacement = 9.80665 / (4 * math.pi**2) * rms / frequency**2
    return {'a': a, 'q': q, 'acceleration_rms_G': rms, 'displacement_rms_m': displacement,
            'displacement_max_m': 3 * displacement}


def _vibration_test(p):
    return {'cycles': p['test_cycles'] * (p['test_stress'] / p['use_stress'])**p['slope']}


def _vibration_displacement(p):
    return {'cycles': 20000000 * (p['component_displacement_m'] / p['board_displacement_m'])**6.4}


def _thermal(p):
    exponent = -.442 - 6e-4 * p['mean_joint_temperature_C'] + 1.74e-2 * math.log(1 + 360 / p['dwell_minutes'])
    if exponent >= 0:
        raise ValueError('疲劳延性指数 C 必须为负；当前温度/持续时间超出本疲劳模型的有效输入')
    return {'fatigue_exponent': exponent, 'cycles': .5 * (p['shear_strain'] / (2 * .325))**(1 / exponent)}


def _sac_strain(p):
    return {'shear_strain': .5 * p['half_length_um'] / p['joint_height_um'] * p['cte_difference'] * p['temperature_difference_K']}


def _humidity(p):
    return {'acceleration_factor': (p['test_rh'] / p['use_rh'])**-3 * math.exp(p['ea'] / K_EV * (
        1 / p['use_temperature_K'] - 1 / p['test_temperature_K']))}


T = field('temperature_K', '绝对温度 T', 'K')
EA = field('ea', '激活能 Ea', 'eV', strictly_positive=False)
SIGNED = {'minimum': None, 'strictly_positive': False}
CALIBRATION_NOTE = '常数必须由材料、工艺或试验标定提供，并与本工具输入单位及输出 10⁻⁶/h 一致。'
PHYSICS_TOOLS = {
    'em': PhysicsTool('电迁移', 'D.2.1，式（D.1）', 'λEM = AEM × Jⁿ × exp[−Ea/(kT)]', (
        field('a', '材料与工艺常量 AEM', '(10⁻⁶/h)/(A/cm²)ⁿ'),
        field('current_density', '电流密度 J', 'A/cm²'), field('n', '电流密度指数 n', '1'), EA, T),
        _em, {'rate': RATE_UNIT}, CALIBRATION_NOTE),
    'hci': PhysicsTool('热载流子注入', 'D.2.2，式（D.2）', 'λHCI = AHCI × (Isub/W)ᵐ × exp[−Ea/(kT)]', (
        field('a', '特性相关因子 AHCI', '(10⁻⁶/h)/(A/μm)ᵐ'), field('current_A', '峰值衬底/栅极电流 Isub', 'A'),
        field('width_um', '沟道宽度 W', 'μm'), field('m', '指数 m', '1', minimum=2, maximum=4),
        field('ea', '激活能 Ea', 'eV', minimum=-.2, maximum=-.1, strictly_positive=False), T),
        _hci, {'rate': RATE_UNIT}, CALIBRATION_NOTE),
    'tddb': PhysicsTool('与时间相关的介质击穿', 'D.2.3，式（D.3）', 'λTDDB = ATDDB × A^(1/β) × Vgs^(a+bT) × exp(c/T+d/T²)', (
        field('a_tddb', '材料常数 ATDDB', '按标定单位'), field('material_a', '材料常数 A', '按标定单位'),
        field('beta', '形状参数 β', '1'), field('voltage_V', '栅源电压 Vgs', 'V'),
        field('a', '拟合参数 a', '按标定单位', **SIGNED), field('b', '拟合参数 b', '按标定单位', **SIGNED),
        field('c', '拟合参数 c', '按标定单位', **SIGNED), field('d', '拟合参数 d', '按标定单位', **SIGNED), T),
        _tddb, {'rate': RATE_UNIT}, CALIBRATION_NOTE),
    'corrosion': PhysicsTool('腐蚀失效时间', 'D.2.4，式（D.4）', 'tC = K1K2K3/K4 × w²hndF/(4MV) × ρ/z', (
        field('k1', '物理特性指数 K1', '1'), field('k2', '涂覆度指数 K2', '1'),
        field('k3', '已确定任务剖面修正因子 K3', '1'), field('k4', '环境修正因子 K4', '1'),
        field('width_cm', '导体宽度 w', 'cm'), field('height_cm', '导体高度 h', 'cm'),
        field('valence', '化合价 n', '1'), field('density_g_cm3', '金属导体密度 d', 'g/cm³'),
        field('atomic_weight_g_mol', '金属导体原子重量 M', 'g/mol'), field('voltage_V', '导线间电压 V', 'V'),
        field('resistivity_ohm_cm', '介质电阻率 ρ', 'Ω·cm'), field('dielectric_cm', '介质厚度 z', 'cm')),
        _corrosion, {'time_seconds': 's', 'time_hours': 'h'},
        '按源式 F=96500 C/mol；本工具统一采用 cm、g、Ω·cm，量纲得到秒后换算小时。源文 ρ 的 10⁶Ω·cm 数值须乘 10⁶ 后输入。式 D.5 的 DC 标注与表达式存在单位歧义，K3 需提供已确定值。'),
    'corrosion_environment': PhysicsTool('腐蚀环境修正因子', 'D.2.4，式（D.6）', 'K4 = (RHR/RH)ⁿ × exp[Ea/k × (1/TR−1/T)]', (
        field('reference_rh', '参考相对湿度 RHR', '0～1', maximum=1), field('rh', '相对湿度 RH', '0～1', maximum=1),
        field('n', '指数 n', '1'), EA, field('reference_temperature_K', '参考绝对温度 TR', 'K'), T), _environment, {'k4': '1'}),
    'vibration_response': PhysicsTool('随机振动响应与板位移', 'D.2.5，式（D.8）～（D.11）', 'Q=A√fn；GRMS=√(πPfnQ/2)；ZRMS=gGRMS/(4π²fn²)；ZMAX=3ZRMS', (
        field('psd_G2_Hz', '谐振频率下输入功率谱密度 P', 'G²/Hz'), field('frequency_Hz', '谐振频率 fn', 'Hz')),
        _vibration_response, {'a': '1', 'q': '1', 'acceleration_rms_G': 'G', 'displacement_rms_m': 'm', 'displacement_max_m': 'm'},
        'A 按 D.9 分段及线性过渡确定；g=9.80665 m/s²。输入应为相应谐振频率的谱密度，不是总均方根加速度。'),
    'vibration_test': PhysicsTool('振动疲劳试验换算', 'D.2.5，式（D.12）', 'Nu = Nt × (St/Su)ᵇ', (
        field('test_cycles', '试验失效循环数 Nt', '次'), field('test_stress', '试验应力等级 St', '与 Su 同单位'),
        field('use_stress', '使用应力等级 Su', '与 St 同单位'), field('slope', 'S-N 曲线斜率 b', '1')),
        _vibration_test, {'cycles': '次'}, 'b 需依据材料；原文多种材料为 6.4，SAC305 为 7.8，不统一默认。'),
    'vibration_displacement': PhysicsTool('板位移振动疲劳寿命', 'D.2.5，式（D.13）', 'Nf = 20000000 × (ZC/ZMAX)^6.4', (
        field('component_displacement_m', '已确定元器件最大位移 ZC', 'm'), field('board_displacement_m', '板最大位移 ZMAX', 'm')),
        _vibration_displacement, {'cycles': '次'},
        '使用 SnPb 典型疲劳常量 6.4，适用垂直板面振动；ZC 单位为 m。'),
    'thermal': PhysicsTool('共晶焊料热循环寿命', 'D.2.6，式（D.15）、（D.16）', 'C=−0.442−0.0006TSj+0.0174ln(1+360/tD)；Nf=0.5[Δγ/(2εf)]^(1/C)', (
        field('shear_strain', '焊点周期剪切应变 Δγ', '1'),
        field('mean_joint_temperature_C', '循环周期焊点平均温度 TSj', '℃', minimum=-273.15, strictly_positive=False),
        field('dwell_minutes', '最高温度持续时间 tD', 'min')),
        _thermal, {'fatigue_exponent': '1', 'cycles': '次'},
        '共晶焊料 εf=0.325，平均温度取四个稳态温度平均值。'),
    'sac_strain': PhysicsTool('SAC 无铅焊点剪切应变', 'D.2.6，式（D.19）', 'Δγ = 0.5 × LD/hs × Δα × ΔT', (
        field('half_length_um', '组件长度的一半 LD', 'μm'), field('joint_height_um', '焊点高度 hs', 'μm'),
        field('cte_difference', '热膨胀系数差 Δα', '1/K'), field('temperature_difference_K', '温度差 ΔT', 'K')),
        _sac_strain, {'shear_strain': '1'}, '输出无铅焊点剪切应变 Δγ。'),
    'humidity': PhysicsTool('温湿度加速因子', 'D.2.7，式（D.20）', 'AF = (RHtest/RHuse)^−3 × exp[Ea/k × (1/Tuse−1/Ttest)]', (
        field('test_rh', '测试相对湿度 RHtest', '0～1', maximum=.85),
        field('use_rh', '使用相对湿度 RHuse', '0～1', maximum=.85), EA,
        field('use_temperature_K', '使用绝对温度 Tuse', 'K'), field('test_temperature_K', '测试绝对温度 Ttest', 'K')),
        _humidity, {'acceleration_factor': '1'},
        '按原文式 D.20 湿度指数 −3 计算，适用湿度 ≤ 85%。注：该式源自 ANSI/VITA 51.2—2011 印刷形式，RHtest > RHuse 时指数 −3 会使湿度比值项小于 1，使用前请结合标准原文与试验定义核对。'),
}


def physics_calculate(model_id, parameters):
    if model_id not in PHYSICS_TOOLS or not isinstance(parameters, dict):
        raise ValueError('请选择已支持的失效物理单项模型')
    model = PHYSICS_TOOLS[model_id]
    source = required_text(parameters.get('parameter_source'), '材料/试验/设计参数依据')
    values = {}
    for spec in model.fields:
        raw = parameters.get(spec.key, spec.default)
        value = decimal(raw, spec.label, positive=spec.strictly_positive, nonnegative=False)
        if (spec.minimum is not None and value < decimal(spec.minimum, '下限', nonnegative=False)
                or spec.maximum is not None and value > decimal(spec.maximum, '上限', nonnegative=False)):
            if spec.minimum is not None and spec.maximum is not None:
                bound_text = f'[{shown(spec.minimum)}, {shown(spec.maximum)}]'
            elif spec.minimum is not None:
                op = '>' if spec.strictly_positive and spec.minimum == 0 else '≥'
                bound_text = f'{op} {shown(spec.minimum)}'
            else:
                bound_text = f'≤ {shown(spec.maximum)}'
            raise ValueError(f'{spec.label}不在源式适用范围 {bound_text}')
        values[spec.key] = finite(value, spec.label)
    try:
        outputs = model.calculator(values)
        for key, value in outputs.items():
            finite(value, key)
            if key != 'fatigue_exponent' and value <= 0:
                raise ValueError('结果为零、负值或发生下溢，不能作为有效失效机理结果')
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError('失效物理计算超出有限数值范围，请核对参数与单位') from error
    steps = [model.formula]
    steps.extend(f'{spec.label} = {shown(values[spec.key])} [{spec.unit}]' for spec in model.fields)
    steps.extend(f'{OUTPUT_LABELS.get(key, key)} = {shown(value)} [{model.units[key]}]' for key, value in outputs.items())
    return AnalysisResult('physics:' + model_id, model.name, {**values, 'parameter_source': source}, tuple(steps),
                          outputs, model.units, model.reference,
                          tuple(filter(None, (model.note, '单一失效机理计算，不代表完整设备失效物理仿真或总失效率。'))))
