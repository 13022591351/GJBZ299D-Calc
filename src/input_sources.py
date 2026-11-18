"""Explicit physical input derivation and auditable internal-circuit bindings."""

import copy
import hashlib
import json
import math
import re
from dataclasses import replace
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from .model_spec import Evaluation
from .numeric import number_text
from .prediction import model_state
from .reference_data import below_value, fragments

RATE_KEYS = {'constituent_rate', 'rear_circuit_rate', 'additional_component_rate'}
GATE_KEYS = {'gate_count', 'complexity', 'digital_complexity'}
TEMPERATURE_KEYS = {'junction_temperature_C', 'channel_temperature_C',
                    'analog_junction_temperature_C', 'digital_junction_temperature_C'}


def _number(value, name, *, minimum=0, integer=False):
    if type(value) not in (int, float):
        raise ValueError(f'{name}须为不小于 {minimum} 的有限数值')
    try:
        is_fin = math.isfinite(value)
    except OverflowError as error:
        raise ValueError(f'{name}须为不小于 {minimum} 的有限数值') from error
    if not is_fin or value < minimum:
        raise ValueError(f'{name}须为不小于 {minimum} 的有限数值')
    if integer and int(value) != value:
        raise ValueError(f'{name}须为整数')
    return Fraction(str(value))


def gate_count(inputs):
    """Clause 5.1.1.3; no guessing of the manufacturer's transistor count."""
    if not isinstance(inputs, dict):
        raise ValueError('门数换算输入格式无效')
    mode = inputs.get('mode')
    if mode == 'transistors':
        if set(inputs) != {'mode', 'technology', 'transistors'}:
            raise ValueError('晶体管数换算需填写工艺与晶体管数')
        if inputs['technology'] not in ('双极型', 'CMOS', '其他MOS'):
            raise ValueError('门数换算工艺无效')
        count = _number(inputs['transistors'], '晶体管数', minimum=1, integer=True)
        divisor = Fraction('3.75') if inputs['technology'] == 'CMOS' else Fraction(3)
        value = count / divisor
        trace = f'NG = {count} / {number_text(float(divisor))}'
    elif mode == 'flip_flops':
        if set(inputs) != {'mode', 'jk_rs', 't_d'}:
            raise ValueError('触发器换算需填写 J-K/R-S 和 T/D 数量')
        first = _number(inputs['jk_rs'], 'J-K/R-S 触发器数量', integer=True)
        second = _number(inputs['t_d'], 'T/D 触发器数量', integer=True)
        value = first * 8 + second * 6
        trace = f'NG = {first} × 8 + {second} × 6'
        if not value:
            raise ValueError('至少填写一个触发器')
    else:
        raise ValueError('请选择晶体管数或触发器数量换算')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError('等效门数超出数值范围')
    return result, f'{trace} = {number_text(result)}（5.1.1.3）'


@lru_cache(maxsize=1)
def thermal_packages():
    result = {}
    for table in fragments('25'):
        grid = table['grid']
        for row in range(1, table['rows']):
            cell = grid.get((row, 2))
            if cell and cell['kind'] == 'number':
                label = grid[row, 0]['text'] + ' / ' + grid[row, 1]['text']
                result[label] = (float(cell['decimal_text']), '表 25；' + cell['id'])
    return result


def junction_temperature(inputs, environment, *, forced_case=None):
    if not isinstance(inputs, dict) or set(inputs) - {
            'case_source', 'case_temperature_C', 'resistance_source', 'thermal_resistance_C_W',
            'package', 'power_W'}:
        raise ValueError('结温辅助输入格式无效')
    if inputs.get('case_source') == 'table24':
        case, case_source = below_value('24', environment)
    elif inputs.get('case_source') == 'specified':
        case = float(_number(inputs.get('case_temperature_C'), '管壳温度', minimum=-273.15))
        case_source = '用户确定的管壳温度'
    else:
        raise ValueError('请选择管壳温度来源')
    if forced_case is not None:
        case, case_source = forced_case, '外贴微电路规定 Tc=35 ℃'
    if inputs.get('resistance_source') == 'table25':
        if inputs.get('package') not in thermal_packages():
            raise ValueError('请选择表 25 的封装形式')
        resistance, resistance_source = thermal_packages()[inputs['package']]
    elif inputs.get('resistance_source') == 'specified':
        resistance = float(_number(inputs.get('thermal_resistance_C_W'), '结到管壳热阻'))
        resistance_source = '用户提供的结到管壳热阻'
    else:
        raise ValueError('请选择结到管壳热阻来源')
    power = _number(inputs.get('power_W'), '实际耗散功率')
    value = float(Fraction(str(case)) + Fraction(str(resistance)) * power)
    if not math.isfinite(value):
        raise ValueError('结温超出数值范围')
    return value, (f'Tj = {number_text(case)} + {number_text(resistance)} × {number_text(float(power))}'
                   f' = {number_text(value)} ℃；式（2）；{case_source}；{resistance_source}')


def load_source(path):
    from .excel_limits import MAX_PACKAGE_BYTES
    from .shared_io import read_supplier_bytes

    path = Path(path)
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError('来源工程文件过大，无法读取')
    raw = path.read_bytes()
    project = read_supplier_bytes(raw, path.suffix)
    return {'file_name': path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'project': project.to_dict()}


def _source_digest_and_project(component, key, project_dict, *, use_cache=False):
    from .domain import (
        _shallow_project_signature,
        cached_project_from_snapshot,
        project_snapshot_digest,
    )

    sig = _shallow_project_signature(project_dict)
    cache = component.__dict__.setdefault('_source_digest_cache', {})
    digest = None
    if use_cache:
        entry = cache.get(key)
        if entry is not None and entry[0] is project_dict and entry[1] == sig:
            digest = entry[2]
    if digest is None:
        digest = project_snapshot_digest(project_dict)
    project, digest = cached_project_from_snapshot(project_dict, digest=digest)
    cache[key] = (project_dict, sig, digest)
    return digest, project


def validate_sources(component, *, _use_cache=False):
    from .domain import check_module_depth
    from .standards import MODELS
    sources = component.input_sources
    if not isinstance(sources, dict) or len(sources) > 20:
        raise ValueError('参数来源须为至多20项的对象')
    fields = {f.key: f for f in MODELS[component.model].fields}
    for key, source in sources.items():
        if not isinstance(key, str) or key not in fields or not isinstance(source, dict):
            raise ValueError('参数来源与所选模型不匹配')
        kind = source.get('kind')
        if kind in ('gate_count', 'junction_temperature'):
            if set(source) != {'kind', 'inputs'}:
                raise ValueError('辅助计算来源字段无效')
            if kind == 'gate_count':
                if key not in GATE_KEYS or '门数' not in fields[key].label:
                    raise ValueError('等效门换算只能用于门数参数')
                gate_count(source['inputs'])
                if source['inputs']['mode'] == 'transistors':
                    declared = component.parameters.get('digital_technology' if key == 'digital_complexity' else 'technology')
                    technology = source['inputs']['technology']
                    if declared in ('双极', '双极型', 'MOS') and ((declared in ('双极', '双极型')) != (technology == '双极型')):
                        raise ValueError('门数换算工艺与所选电路工艺不一致')
            else:
                if key not in TEMPERATURE_KEYS:
                    raise ValueError('结温计算只能用于结温或沟道温度参数')
                junction_temperature(source['inputs'], 'GB')
                if (component.model.startswith('进口') and
                        (source['inputs'].get('case_source') == 'table24'
                         or source['inputs'].get('resistance_source') == 'table25')):
                    raise ValueError('进口器件须填写实际管壳温度与热阻，不能套用国产表24、25')
        elif kind in ('project_rate', 'mounted_microcircuits'):
            if set(source) != {'kind', 'source'}:
                raise ValueError('组成工程来源字段无效')
            if kind == 'project_rate' and key not in RATE_KEYS:
                raise ValueError('此参数不是普通组成失效率')
            if kind == 'mounted_microcircuits' and (key != 'mounted_ic_rate' or
                    component.model not in ('国产混合集成电路', '进口混合集成电路')):
                raise ValueError('外贴微电路计算仅用于工作状态混合集成电路')
            document = source['source']
            if (not isinstance(document, dict) or set(document) != {'file_name', 'sha256', 'project'}
                    or not isinstance(document['file_name'], str) or not document['file_name']
                    or not isinstance(document['sha256'], str)
                    or re.fullmatch(r'[0-9a-fA-F]{64}', document['sha256']) is None):
                raise ValueError('组成工程来源记录无效')
            check_module_depth(document['project'])
            _digest, project = _source_digest_and_project(
                component, key, document['project'], use_cache=_use_cache
            )
            if project.prediction_state != model_state(component.model):
                raise ValueError('组成工程的工作／非工作状态与父模型不一致')
        else:
            raise ValueError('未知参数来源类型')


def _mounted_formula(factors, domestic):
    values = {symbol: value for symbol, value, _source in factors}
    if any(key.startswith('模拟部分 ') for key in values):
        return math.fsum(_mounted_formula(tuple((key.removeprefix(prefix), value, '')
                         for key, value in values.items() if key.startswith(prefix)), domestic)
                         for prefix in ('模拟部分 ', '数字部分 '))
    for required in ('C1', 'πT', 'πQ'):
        if required not in values:
            raise ValueError('外贴微电路计算缺少规定的系数')
    thermal = values['C1'] * values['πT']
    for key in ('πV', 'πPT', 'πCYC', 'πA', 'πP'):
        thermal *= values.get(key, 1)
    # Domestic C3 is removed by table 40. Imported C2 remains by table C.25.
    return (thermal + values.get('C2', 0)) * values['πQ']


def mounted_microcircuits(project, *, domestic):
    from .calculation import sum_rates
    from .standards import MODELS, evaluate
    if project.prediction_state != 'working' or not project.components:
        raise ValueError('外贴微电路需要包含元器件的工作状态工程')
    factors, rates = [], []
    for item in project.components:
        spec = MODELS[item.model]
        if (spec.section != ('5.1.2' if domestic else 'C.2.2.2')
                or spec.method != 'working_stress' or item.is_domestic != domestic):
            raise ValueError(f'{item.name}：不属于本外贴微电路条款支持的半导体集成电路')
        parameters, derived = resolve_sources(item, 'GB', 35, forced_case=35)
        temperature_keys = [field.key for field in spec.fields if field.key in TEMPERATURE_KEYS]
        if not temperature_keys or any(item.input_sources.get(key, {}).get('kind') != 'junction_temperature'
                                       for key in temperature_keys):
            raise ValueError(f'{item.name}：外贴条件须通过热阻与实际功耗从 Tc=35 ℃ 求结温，不能把35℃直接当结温')
        result = evaluate(item.model, parameters, 'GB', ambient_temperature_C=35,
                          stress_variables=item.stress_variables)
        if result.rate is None:
            raise ValueError(f'{item.name}：{result.error}')
        rate = _mounted_formula(result.factors, domestic)
        if item.quantity is None:
            raise ValueError(f'{item.name}：外贴微电路数量待填写')
        rates.append(rate * item.quantity)
        factors.extend((f'{item.name} {key}', value, source) for key, value, source in derived)
        overrides = {'πE': 1, **({'C3': 0, 'πL': 1} if domestic else {})}
        for key, value, trace in result.factors:
            symbol = key.split(' ')[-1]
            if symbol in overrides:
                value = overrides[symbol]
                trace = '外贴微电路规定条件；' + ('表 40' if domestic else '表 C.25')
            factors.append((f'{item.name} {key}', value, trace))
        factors.append((f'{item.name} NDC', item.quantity, '组成工程的外贴芯片数量'))
        factors.append((f'{item.name} NλDC', rates[-1],
                        '表 40：Tc=35℃、πE=1、C3=0、πL=1' if domestic else '表 C.25：Tc=35℃、πE=1'))
    total, error = sum_rates(rates)
    if error or total is None:
        raise ValueError(error or '外贴微电路没有完整结果')
    return Evaluation(total, tuple(factors))


@lru_cache(maxsize=128)
def _project_rate(encoded_or_digest, environment, temperature, mounted, domestic):
    from .domain import _ENCODED_PROJECTS_BY_DIGEST, Project, _cached_project_by_digest
    if (
        isinstance(encoded_or_digest, str)
        and len(encoded_or_digest) == 64
        and re.fullmatch(r'[0-9a-f]{64}', encoded_or_digest)
        and encoded_or_digest in _ENCODED_PROJECTS_BY_DIGEST
    ):
        project = _cached_project_by_digest(encoded_or_digest)
    else:
        project = Project.from_dict(json.loads(encoded_or_digest))
    if mounted:
        return mounted_microcircuits(project, domestic=domestic)
    matches = [c for c in project.enabled_conditions
               if c.environment == environment and c.ambient_temperature_C == temperature]
    if not matches:
        raise ValueError('组成工程没有对应环境、温度且已启用的工况')
    result = project.calculate(matches[0])
    if result.error or result.total_rate is None:
        raise ValueError('组成工程的对应工况未完整计算：' + (result.error or f'待配置 {result.pending_count} 项'))
    factors = tuple((item.name + ' Nλp', result.subtotals[index], '来源工程对应工况')
                    for index, item in enumerate(project.components))
    return Evaluation(result.total_rate, factors)


def resolve_sources(component, environment, temperature, *, forced_case=None, _use_cache=False):
    from .standards import MODELS
    parameters = {field.key: field.default for field in MODELS[component.model].fields if field.default is not None}
    parameters.update(copy.deepcopy(component.parameters))
    factors = []
    fields = {field.key: field for field in MODELS[component.model].fields}
    for key, source in component.input_sources.items():
        if key not in fields or not fields[key].active(parameters):
            continue
        kind = source['kind']
        if kind == 'gate_count':
            value, trace = gate_count(source['inputs'])
        elif kind == 'junction_temperature':
            value, trace = junction_temperature(source['inputs'], environment, forced_case=forced_case)
        else:
            document = source['source']['project']
            if document.get('prediction_state', 'working') != model_state(component.model):
                raise ValueError('组成工程的预计状态不匹配')
            digest, _project = _source_digest_and_project(
                component, key, document, use_cache=_use_cache
            )
            result = _project_rate(
                digest,
                environment,
                temperature,
                kind == 'mounted_microcircuits',
                component.is_domestic,
            )
            value, trace = result.rate, '关联组成工程：' + source['source']['file_name']
            factors.extend(result.factors)
            if kind == 'project_rate':
                parameters['source_environment'] = environment
                parameters['source_temperature_C'] = temperature
        parameters[key] = value
        factors.append((fields[key].label, value, trace))
    return parameters, tuple(factors)


def evaluate_with_sources(component, environment, temperature):
    from .standards import evaluate
    validate_sources(component, _use_cache=True)
    parameters, factors = resolve_sources(component, environment, temperature, _use_cache=True)
    result = evaluate(component.model, parameters, environment, ambient_temperature_C=temperature,
                      stress_variables=component.stress_variables)
    return replace(result, factors=factors + result.factors)
