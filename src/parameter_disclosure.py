"""Disclose only the actual standard lookup interval, never a component input."""

import math

from .numeric import number_text


def parameter_ranges(component, results=()):
    """Return safe {name, range, source} rows derived exclusively from standard cells.

    Resistance inputs are in ohms and capacitance inputs in microfarads, as in
    the calculation model. The standard's own unit-bearing inequalities remain
    intact. Existing disclosure metadata is preserved only when already validated.
    """
    if component.disclosure:
        validate_stress_bins(component.disclosure)
        raw_ranges = component.disclosure.get('parameter_ranges', [])
        if not isinstance(raw_ranges, list):
            raise ValueError('公开参数档位须为列表')
        rows = []
        for item in raw_ranges:
            if not isinstance(item, dict) or set(item) != {'name', 'range', 'source'}:
                raise ValueError('公开参数档位条目格式无效')
            rows.append({'name': str(item['name']), 'range': str(item['range']), 'source': str(item['source'])})
        return rows
    from .standards import MODELS
    spec = MODELS[component.model]
    disclosed = {}
    for result in results:
        for record in result.parameter_ranges:
            key, interval = record[:2]
            source = record[2] if len(record) > 2 else spec.reference
            name = next((field.label for field in spec.fields if field.key == key), key)
            name = {'resistance_ohm': '阻值 R', 'capacitance_uF': '电容量 C'}.get(key, name)
            item = {'name': name, 'range': interval, 'source': source}
            if key in disclosed and disclosed[key] != item:
                raise ValueError('不同工况的参数档位不一致，不能公开为同一器件档位')
            disclosed[key] = item
    return list(disclosed.values())


def stress_bin_specs(model):
    """Public stress keys, labels and standard references; no device inputs."""
    from .standards import MODELS

    spec = MODELS[model]
    fields = [field for field in spec.fields if field.stress_max is not None]
    fields.extend(spec.derived_stress)
    return {field.key: {'label': field.label.split(' = ', 1)[0], 'maximum': field.stress_max,
                        'values': field.choices,
                        'sources': field.stress_source if isinstance(field.stress_source, tuple) else (field.stress_source,),
                        'required': field.when is None}
            for field in fields}


def validate_stress_bins(disclosure):
    """Validate only real numeric table coordinates and canonical public labels."""
    bins = disclosure.get('stress_bins', [])
    if not isinstance(bins, list):
        raise ValueError('公开应力档位须为列表')
    specs, seen = stress_bin_specs(disclosure['model']), set()
    for item in bins:
        if not isinstance(item, dict) or set(item) != {'key', 'label', 'value', 'source'}:
            raise ValueError('公开应力档位条目格式无效')
        key = item['key']
        if not isinstance(key, str) or key not in specs or key in seen:
            raise ValueError('公开应力档位的参数不适用或重复')
        seen.add(key)
        spec, value = specs[key], item['value']
        if item['label'] != spec['label'] or item['source'] not in spec['sources']:
            raise ValueError('公开应力档位的名称或标准依据无效')
        if (type(value) not in (int, float) or not math.isfinite(value)
                or not 0 < value <= spec['maximum']
                or value not in spec['values']):
            raise ValueError('公开应力档位须为适用标准表的数值档位')
    if disclosure.get('stress') != ('标准档位' if bins else '内部'):
        raise ValueError('公开工作应力标记与应力档位不一致')
    if bins and not {key for key, spec in specs.items() if spec['required']} <= seen:
        raise ValueError('公开应力档位缺少该模型的应力参数')
    return bins


def stress_bins(component, results):
    """Publish selected bins only; raw ratios, expressions and powers never escape."""
    if component.disclosure:
        return [dict(item) for item in validate_stress_bins(component.disclosure)]
    specs = stress_bin_specs(component.model)
    selected = {}
    for result in results:
        if result.rate is None:
            continue
        for adjustment in result.stress_adjustments:
            if adjustment.key not in specs:
                continue
            item = {'key': adjustment.key, 'label': specs[adjustment.key]['label'],
                    'value': adjustment.selected, 'source': adjustment.source}
            if adjustment.key in selected and selected[adjustment.key] != item:
                raise ValueError('不同工况的应力档位不一致，不能公开为同一器件档位')
            selected[adjustment.key] = item
    bins = [selected[key] for key in specs if key in selected]
    validate_stress_bins({'model': component.model, 'stress_bins': bins,
                          'stress': '标准档位' if bins else '内部'})
    return bins


def disclosure_stress_text(disclosure):
    bins = validate_stress_bins(disclosure)
    return '；'.join(f'{item["label"]} [档位 {number_text(item["value"])}]' for item in bins) or '内部'
