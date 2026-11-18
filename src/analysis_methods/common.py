"""Pure, serializable analysis results; all rates use 10⁻⁶/h."""

import copy
import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from ..numeric import number_text

RATE_UNIT = '10⁻⁶/h'


def decimal(value, name, *, positive=False, nonnegative=True):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError(f'{name}必须为有限数值')
    try:
        result = Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f'{name}必须为有限数值') from None
    if not result.is_finite() or (positive and result <= 0) or (nonnegative and result < 0):
        raise ValueError(f'{name}必须为有限' + ('正数' if positive else '非负数' if nonnegative else '数值'))
    finite(result, name)
    return result


def finite(value, name):
    result = float(value)
    if not math.isfinite(result) or (value != 0 and result == 0):
        raise ValueError(f'{name}超出可表示数值范围')
    return result


def required_text(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise ValueError(f'请填写{name}（最多 1000 字符）')
    return value.strip()


def shown(value):
    return number_text(finite(value, '计算项'))


@dataclass(frozen=True)
class AnalysisResult:
    method: str
    title: str
    inputs: object
    steps: tuple[str, ...]
    outputs: dict
    units: dict
    reference: str
    notes: tuple[str, ...] = ()
    details: tuple[dict, ...] = field(default_factory=tuple)

    def to_dict(self):
        return copy.deepcopy({'method': self.method, 'title': self.title, 'inputs': self.inputs,
                              'steps': list(self.steps), 'outputs': self.outputs, 'units': self.units,
                              'reference': self.reference, 'notes': list(self.notes),
                              'details': list(self.details)})


OUTPUT_LABELS = {
    'rate': '预计失效率', 'working_duty_cycle': '工作占空比', 'nonoperating_duty_cycle': '非工作占空比',
    'total_duration_hours': '任务剖面总时长', 'prior_component_hours': '初始等效元器件小时数',
    'observed_equivalent_hours': '观测等效元器件小时数', 'original_rate': '元器件失效率',
    'percent_sum': '原文比例合计', 'contribution_sum': '模式贡献合计', 'time_seconds': '失效时间',
    'time_hours': '失效时间', 'k4': '环境修正因子 K4', 'a': '谐振系数 A', 'q': '可传送性 Q',
    'acceleration_rms_G': '加速度均方根', 'displacement_rms_m': '板位移均方根',
    'displacement_max_m': '板最大位移', 'cycles': '失效循环次数',
    'fatigue_exponent': '疲劳延性指数 C', 'shear_strain': '剪切应变 Δγ', 'acceleration_factor': '加速因子 AF',
}
