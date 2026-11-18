"""Atomic batch configuration and explicit BOM nominal-value conversion."""

import copy
import re

from .model_registry import recommend
from .model_spec import TEMP_CHOICE_KEYS, normalize_option
from .nominal import parse_nominal
from .prediction import model_state
from .standards import MODELS, Evaluation, stress_inputs
from .stress import expression_variables

CALCULATION_DISABLED = '该工作环境未启用计算'


def _passive_kind_for_component(component):
    prefixes = set(recommend(component).prefixes)
    if prefixes & {'R', 'RN'}:
        return 'R'
    if 'C' in prefixes:
        return 'C'
    if 'L' in prefixes:
        return 'L'
    return None


def is_parameter_value_column(components) -> bool:
    """Return True when passive BOM Value cells represent component values rather than arbitrary text/indices."""
    passive_valid = []
    bare_index_pairs = []
    for comp in components:
        if not getattr(comp, 'bom', None):
            continue
        row = comp.bom.get('row', {})
        stripped = str(row.get('Value', '') or '').strip()
        if not stripped:
            continue
        kind = _passive_kind_for_component(comp)
        if kind is None:
            continue
        footprint = str(row.get('Footprint', '') or '').upper()
        bare_mark = re.fullmatch(r'(\d{3,4})[JKM]?', stripped)
        is_pkg = bool(
            bare_mark and re.search(rf'(?<!\d){re.escape(bare_mark.group(1))}(?!\d)', footprint)
        )
        is_valid = (not is_pkg) and (parse_nominal(stripped, kind) is not None)
        passive_valid.append(is_valid)
        if re.fullmatch(r'\d+', stripped):
            bare_index_pairs.append((stripped, str(row.get('Index', '') or '').strip()))
    if len(passive_valid) >= 3:
        valid_ratio = sum(1 for ok in passive_valid if ok) / len(passive_valid)
        all_bare_equal_index = bool(bare_index_pairs) and all(
            bool(idx) and val == idx for val, idx in bare_index_pairs
        )
        if valid_ratio < 0.5 or all_bare_equal_index:
            return False
    return True


def nominal_parameter(component, model, *, allow_bare_eia=True):
    """Only the BOM Value field is interpreted. MPN fields are never consulted."""
    row = component.bom.get('row', {}) if component.bom else {}
    text = str(row.get('Value', '') or '')
    specs = {field.key: field for field in MODELS[model]['fields']}
    if 'resistance_ohm' in specs:
        key, kind, unit = 'resistance_ohm', 'R', 'Ω'
    elif 'capacitance_uF' in specs:
        key, kind, unit = 'capacitance_uF', 'C', 'μF'
    elif 'inductance_H' in specs:
        key, kind, unit = 'inductance_H', 'L', 'H'
    else:
        return None
    stripped = text.strip()
    footprint = str(row.get('Footprint', '') or '').upper()
    bare_mark = re.fullmatch(r'(\d{3,4})[JKM]?', stripped)
    if bare_mark:
        if not allow_bare_eia:
            return None
        digits = bare_mark.group(1)
        if re.search(rf'(?<!\d){re.escape(digits)}(?!\d)', footprint):
            return None
    parsed = parse_nominal(text, kind)
    if parsed is None:
        return None
    try:
        value = parsed.in_unit(unit)
    except (OverflowError, ValueError):
        return None
    if specs[key].positive and value <= 0:
        return None
    return key, value


def _choice_value_compatible(spec, value, context):
    """Return False when choices_by restricts an enum/ceiling/temp field and value is incompatible."""
    if not spec.choices_by or spec.choices is None:
        return True
    resolved = spec.resolve_choices(context)
    if resolved is None:
        return True
    options = tuple(normalize_option(item)[0] for item in resolved)
    if spec.key in TEMP_CHOICE_KEYS:
        nums = [c for c in options if not isinstance(c, bool) and isinstance(c, (int, float))]
        if not nums:
            return True
        return not isinstance(value, bool) and isinstance(value, (int, float)) and value <= max(nums)
    if spec.ceil_choices:
        if isinstance(value, str):
            return value in options
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        nums = [c for c in options if not isinstance(c, bool) and isinstance(c, (int, float))]
        has_overflow = any(isinstance(c, str) and ('＞' in c or '>' in c or '≥' in c) for c in options)
        return has_overflow or not nums or value <= max(nums)
    return not isinstance(value, bool) and value in options


def _prune_parameters_and_sources(model, parameters, input_sources, *, reused_from_opposite=False):
    fields = MODELS[model]['fields']
    defaults = {f.key: f.default for f in fields if f.default is not None}
    while True:
        changed = False
        active = {f.key for f in fields if f.active(parameters)}
        for key in list(parameters):
            if key not in active:
                parameters.pop(key, None)
                changed = True
        for key in list(input_sources):
            if key not in active:
                input_sources.pop(key, None)
                changed = True
        context = defaults | parameters
        for spec in fields:
            if spec.key in parameters and spec.key not in input_sources and spec.choices_by:
                if not _choice_value_compatible(spec, parameters[spec.key], context):
                    parameters.pop(spec.key, None)
                    context = defaults | parameters
                    changed = True
        if not changed:
            break
    if reused_from_opposite:
        from .state_transfer import _is_valid_subtype_quality
        q_key = next((k for k in parameters if k == 'quality' or k.endswith('_quality')), None)
        if q_key and not _is_valid_subtype_quality(model, parameters, parameters[q_key]):
            parameters.pop(q_key, None)


def configure_batch(components, model, overrides, *, from_bom=False, fill_missing_nominal=False,
                    environment=None, ambient_temperature_C=None, calculation_enabled=True,
                    stress_variable_overrides=None, prediction_state='working', input_source_overrides=None,
                    allow_bare_eia=None):
    """Return validated copies with optional evaluations; disabled results are None."""
    if model not in MODELS or model == '待配置':
        raise ValueError('请选择本次批量配置的器件类型')
    if stress_variable_overrides is not None and not isinstance(stress_variable_overrides, dict):
        raise ValueError('应力变量须为名称与数值或表达式的对应关系')
    if set(overrides) - {field.key for field in MODELS[model]['fields']}:
        raise ValueError('器件参数包含不适用的字段')
    effective_allow_eia = is_parameter_value_column(components) if allow_bare_eia is None else bool(allow_bare_eia)
    effective_state = (
        model_state(model)
        if model not in ('待配置', '已知单件失效率', '工程模块')
        else prediction_state
    )
    results = []
    for original in components:
        item = copy.deepcopy(original)
        reused_from_opposite = False
        if item.model != model:
            reused_params, reused_sources = ({}, {})
            if item.model == '待配置':
                from .state_transfer import reusable_from_component
                reused_params, reused_sources = reusable_from_component(
                    item, model, effective_state
                )
                reused_from_opposite = bool(reused_params or reused_sources)
            item.parameters = reused_params
            item.condition_rates = []
            if hasattr(item, 'module_source'):
                item.module_source = None
            item.disclosure = None
            item.stress_variables = {}
            item.input_sources = reused_sources
        item.model = model
        if model == '已知单件失效率' and item.rate_state != effective_state:
            item.condition_rates = []
        item.rate_state = effective_state
        for key in overrides:
            item.input_sources.pop(key, None)
        for key, value in (input_source_overrides or {}).items():
            if value is None:
                item.input_sources.pop(key, None)
            else:
                item.input_sources[key] = copy.deepcopy(value)
        if from_bom or fill_missing_nominal:
            nominal = nominal_parameter(item, model, allow_bare_eia=effective_allow_eia)
            if nominal and (from_bom or nominal[0] not in item.parameters):
                item.parameters[nominal[0]] = nominal[1]
        merged = item.parameters | overrides
        active = {field.key for field in MODELS[model]['fields'] if field.active(merged)}
        if model == '已知单件失效率':
            # Only an explicitly entered value updates this environment/temperature.
            # Existing values for every other condition remain independent.
            if 'λp' in overrides:
                item.set_condition_rate(environment, ambient_temperature_C, overrides['λp'])
        else:
            item.parameters.update({key: value for key, value in overrides.items() if key in active})
            _prune_parameters_and_sources(
                model,
                item.parameters,
                item.input_sources,
                reused_from_opposite=reused_from_opposite,
            )
        if stress_inputs(model):
            if stress_variable_overrides is not None:
                item.stress_variables.update(stress_variable_overrides)
            names, pending = set(), []
            for spec in stress_inputs(model):
                if spec.key in item.parameters and spec.active(item.parameters):
                    pending.extend(expression_variables(item.parameters[spec.key]))
            while pending:
                name = pending.pop()
                if name in names:
                    continue
                names.add(name)
                definition = item.stress_variables.get(name, '')
                if not (isinstance(definition, str) and not definition.strip()):
                    pending.extend(expression_variables(definition) - names)
            item.stress_variables = {name: value for name, value in item.stress_variables.items() if name in names}
        else:
            item.stress_variables = {}
        item.validate()
        result = None
        if calculation_enabled:
            from .calculation import evaluate_component
            result = evaluate_component(item, environment, ambient_temperature_C, prediction_state)
            if item.quantity is None:
                result = Evaluation(None, error='元器件数量待填写')
        results.append((item, result))
    return results
