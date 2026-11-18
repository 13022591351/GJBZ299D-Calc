"""Readable model inputs shared by the component table and calculation reports.

Only declared, active Input fields are rendered. Nested records use their own
Chinese labels; source snapshots and unknown dictionary keys are never dumped.
"""

from collections import defaultdict

from .model_registry import category_for_model, recommend
from .nominal import parse_nominal
from .nominal_display import actual_value_text
from .numeric import number_text
from .parameter_disclosure import disclosure_stress_text
from .standards import MODELS
from .stress_display import adjustment_text, stress_value_text


def _is_stress_section(section):
    return section in ('工作应力', '非工作条件')


def input_label(field):
    return (field.label.split(' = ', 1)[0].replace('（标准表取值）', '')
            .replace('（须为标准表列值）', '').replace('，须为标准表列值', ''))


def parameter_lines(fields, parameters, *, stress=None, variables=None, detailed=False, adjustments=(),
                    include_missing=False, assignment='='):
    """Render scalar values or labeled record lists without changing their data."""
    fields = [field for field in fields if field.scope == 'device' and field.active(parameters)
              and (stress is None or _is_stress_section(field.section) == stress)]
    selected = {entry.key: entry for entry in adjustments}
    zero_groups = defaultdict(list)
    for field in fields:
        value = parameters.get(field.key, field.default)
        if field.integer and field.default == 0 and value == 0:
            zero_groups[field.section].append(field.key)
    compressed = {key for keys in zero_groups.values() if len(keys) >= 3 for key in keys}
    lines = []
    for field in fields:
        if field.key in compressed:
            continue
        label = input_label(field)
        value = parameters.get(field.key, field.default)
        if value is None:
            if include_missing:
                lines.append(f'{label}{assignment}未填写')
            continue
        if field.record_fields:
            if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
                lines.append(f'{label}{assignment}[记录格式无效]')
                continue
            lines.append(f'{label}{assignment}[{len(value)} 项]')
            if detailed:
                for index, row in enumerate(value, 1):
                    lines.append(f'[第 {index} 项]')
                    lines.extend('  ' + text for text in parameter_lines(
                        field.record_fields, row, variables=variables, detailed=True,
                        include_missing=include_missing, assignment=assignment))
            continue
        if isinstance(value, (dict, list, tuple)):
            shown = '[参数格式无效]'
        elif field.stress_max is not None:
            shown = (adjustment_text(selected[field.key]) if field.key in selected else
                     stress_value_text(value, field.stress_max, variables, grid=field.choices))
        else:
            shown = actual_value_text(field.key, value)
        lines.append(f'{label}{assignment}{shown}')
    for section, keys in zero_groups.items():
        if len(keys) >= 3:
            lines.append(f'{section}：[另有 {len(keys)} 项数量均为 0]')
    return lines


def input_text(component, *, stress=False, environment=None, ambient_temperature_C=None, detailed=False,
               result=None):
    if component.disclosure:
        if stress:
            return disclosure_stress_text(component.disclosure)
        return '；'.join(item['name'] + '：' + item['range'] for item in
                        component.disclosure['parameter_ranges']) or '内部'
    if stress and component.model == '工程模块':
        return '内部'
    if not stress and component.model == '工程模块':
        source = component.module_source or {}
        project = source.get('project', {})
        return f"{project.get('name', '来源未填写')} · {len(project.get('components', []))} 个内部预计项"
    if not stress and component.model == '已知单件失效率':
        if environment is not None and ambient_temperature_C is not None:
            value = component.condition_rate(environment, ambient_temperature_C)
            return '当前工况待填写' if value is None else f'λp={number_text(value)}（10⁻⁶/h）'
        entries = component.condition_rate_entries()
        return f'{len(entries)} 组工况已填写' if entries else '待填写'
    specs = [s for s in MODELS[component.model]['fields']
             if s.scope == 'device' and _is_stress_section(s.section) == stress and s.active(component.parameters)]
    if not specs:
        return '—'
    direct_specs = [spec for spec in specs if spec.key not in component.input_sources]
    values = parameter_lines(direct_specs, component.parameters, variables=component.stress_variables,
                             detailed=detailed, adjustments=result.stress_adjustments if result else ())
    for spec in specs:
        source = component.input_sources.get(spec.key)
        if source:
            calculated = next((value for symbol, value, _trace in result.factors if symbol == spec.label), None) if result else None
            origin = {'gate_count': '门数换算', 'junction_temperature': '结温辅助',
                      'project_rate': '组成工程', 'mounted_microcircuits': '外贴微电路工程'}.get(source['kind'], '参数来源')
            values.append(input_label(spec) + '=[' + origin +
                          (f'，计算值 {number_text(calculated)}' if calculated is not None else '，待计算') + ']')
    return ('\n' if detailed else '；').join(values) or '待填写'


def nominal_value(component):
    """Normalize the BOM value for display/grouping without changing source data."""
    category = category_for_model(component.model)
    explicit_kind = category if category in ('R', 'C', 'L') else None
    categories = recommend(component).categories if explicit_kind is None else ()
    kind = explicit_kind or (categories[0] if len(categories) == 1 and categories[0] in ('R', 'C', 'L') else None)
    return parse_nominal(component.bom.get('row', {}).get('Value', ''), kind)

