"""One explicit operating condition, one set of results for every consumer."""

import math
from dataclasses import dataclass
from types import MappingProxyType

from .model_spec import Evaluation
from .prediction import STATES, model_state, validate_state
from .standards import ENVIRONMENTS


@dataclass(frozen=True)
class ConditionResult:
    condition_id: str
    condition_name: str
    environment: str | None
    ambient_temperature_C: float | None
    enabled: bool
    evaluations: object
    subtotals: tuple
    pending_count: int
    known_rate: float | None
    total_rate: float | None
    mtbf_hours: float | None
    error: str = ''
    prediction_state: str = 'working'
    mtbf_status: str = 'incomplete'

    @property
    def is_zero_rate(self):
        return self.total_rate == 0 and self.mtbf_status == 'infinite'

    @property
    def mtbf_text(self):
        if self.mtbf_hours is not None:
            from .numeric import formatted_number
            return formatted_number(self.mtbf_hours)
        if self.mtbf_status == 'infinite':
            return '∞'
        if self.mtbf_status == 'overflow':
            return '超出数值范围'
        return '待计算'

    def evaluate(self, component):
        try:
            return self.evaluations[component.identifier]
        except KeyError as error:
            raise ValueError('预计对象不属于本次计算结果') from error


def _is_finite(value):
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def sum_rates(values):
    """Retain a failed aggregate as an explicit result, never zero or infinity."""
    values = tuple(values)
    if not values:
        return None, ''
    if any(not _is_finite(value) or value < 0 for value in values):
        return None, '失效率合计必须为有限非负数，请核对各项失效率和数量'
    try:
        total = math.fsum(values)
    except OverflowError:
        return None, '失效率合计超出数值范围，请核对各项失效率和数量'
    return total, ''


def evaluate_component(component, environment, temperature, prediction_state):
    validate_state(prediction_state)
    state = (component.rate_state if component.model in ('已知单件失效率', '工程模块')
             else prediction_state if component.model == '待配置' else model_state(component.model))
    if state != prediction_state:
        return Evaluation(None, error=f'此项为{STATES[state]}预计结果；当前工程为{STATES[prediction_state]}，请配置相应模型或数据')
    return component.evaluate(environment, temperature)


def calculate_condition(components, condition, *, prediction_state='working'):
    """Capture input order and quantities; no Project copy or implicit environment."""
    components = tuple(components)
    validate_state(prediction_state)
    valid = (isinstance(condition.environment, str) and condition.environment in ENVIRONMENTS
             and type(condition.ambient_temperature_C) in (int, float))
    valid = valid and _is_finite(condition.ambient_temperature_C) and condition.ambient_temperature_C >= -273.15
    results, weighted = {}, []
    for component in components:
        if component.identifier in results:
            raise ValueError('存在重复元器件标识')
        result = (evaluate_component(component, condition.environment, condition.ambient_temperature_C, prediction_state)
                  if condition.enabled and valid else Evaluation(None, error=(
                      '该工作环境未启用计算' if not condition.enabled else '请先填写有效的环境类别和环境温度')))
        results[component.identifier] = result
        weighted.append(result.weighted(component.quantity))
    pending = sum(value is None for value in weighted)
    values = [value for value in weighted if value is not None]
    known, error = sum_rates(values)
    total = known if (condition.enabled and components and not pending
                     and condition.environment is not None and condition.ambient_temperature_C is not None) else None
    if total is None:
        mtbf = None
        mtbf_status = 'incomplete'
    elif total == 0:
        mtbf = None
        mtbf_status = 'infinite'
    else:
        try:
            candidate = 1e6 / total
        except OverflowError:
            candidate = math.inf
        if _is_finite(candidate):
            mtbf = candidate
            mtbf_status = 'finite'
        else:
            mtbf = None
            mtbf_status = 'overflow'
    return ConditionResult(condition.identifier, condition.name, condition.environment,
                           condition.ambient_temperature_C, condition.enabled,
                           MappingProxyType(results), tuple(weighted), pending, known, total, mtbf, error,
                           prediction_state, mtbf_status)
