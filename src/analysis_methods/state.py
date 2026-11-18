"""Strict JSON shape for editable analysis drafts; results are always recomputed."""

import math

from .failure_modes import _catalog
from .mission import STATES
from .physics import PHYSICS_TOOLS


def _finite(value):
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _object(value, allowed, name):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(name + '结构或字段无效')


def _text(value, name):
    if not isinstance(value, str) or len(value) > 1000:
        raise ValueError(name + '必须为最多 1000 字符的文字')


def _number_draft(value, name):
    if isinstance(value, str):
        _text(value, name)
    elif type(value) not in (int, float) or not _finite(value):
        raise ValueError(name + '必须为有限数值或待编辑文字')


def _rows(rows, name):
    if not isinstance(rows, list) or len(rows) > 10000 or any(not isinstance(row, dict) for row in rows):
        raise ValueError(name + '应为最多 10000 项的记录列表')
    return rows


def _source(source):
    _object(source, ('id', 'name', 'project_name', 'environment', 'ambient_temperature_C', 'prediction_state', 'rate', 'scope'), '来源工况')
    if source.get('scope', 'external') not in ('current', 'external'):
        raise ValueError('来源工况范围无效')
    for key in ('name', 'environment'):
        _text(source.get(key), '来源工况' + key)
    for key in ('id', 'project_name'):
        if key in source:
            _text(source[key], '来源工况' + key)
    if source.get('prediction_state') not in STATES:
        raise ValueError('来源工况预计状态无效')
    for key in ('rate', 'ambient_temperature_C'):
        value = source.get(key)
        if type(value) not in (int, float) or not _finite(value):
            raise ValueError('来源工况必须包含有限失效率和温度')
    if source['rate'] < 0 or source['ambient_temperature_C'] < -273.15:
        raise ValueError('来源工况失效率或温度范围无效')


def validate_analysis_state(state):
    if state == {}:
        return
    _object(state, ('version', 'mission', 'bayes', 'failure_modes', 'physics'), '分析页面')
    if type(state.get('version')) is not int or state['version'] != 1:
        raise ValueError('分析页面数据版本无效')
    mission = state.get('mission', {})
    _object(mission, ('basis', 'phases', 'external_sources'), '任务剖面')
    if mission.get('basis', 'duration') not in ('duration', 'duty'):
        raise ValueError('任务剖面权重方式无效')
    for row in _rows(mission.get('phases', []), '任务阶段'):
        _object(row, ('name', 'state', 'duration_hours', 'duty_cycle', 'rate', 'environment',
                      'ambient_temperature_C', 'source', 'source_snapshot'), '任务阶段')
        if row.get('state', 'working') not in STATES:
            raise ValueError('任务阶段状态无效')
        for key in ('name', 'environment', 'source'):
            if key in row:
                _text(row[key], '任务阶段' + key)
        for key in ('duration_hours', 'duty_cycle', 'rate', 'ambient_temperature_C'):
            if key in row:
                _number_draft(row[key], '任务阶段' + key)
        if row.get('source_snapshot') is not None:
            _source(row['source_snapshot'])
    for row in _rows(mission.get('external_sources', []), '来源工况'):
        _source(row)
    bayes = state.get('bayes', {})
    _object(bayes, ('initial_rate', 'prior_failures', 'observations'), '失效率修正')
    for key in ('initial_rate', 'prior_failures'):
        if key in bayes:
            _number_draft(bayes[key], key)
    for row in _rows(bayes.get('observations', []), '观测数据'):
        _object(row, ('source', 'failures', 'component_hours', 'acceleration_factor'), '观测数据')
        if 'source' in row:
            _text(row['source'], '观测数据来源')
        for key in ('failures', 'component_hours', 'acceleration_factor'):
            if key in row:
                _number_draft(row[key], key)
    modes = state.get('failure_modes', {})
    _object(modes, ('table', 'category', 'rate'), '失效模式')
    catalog = _catalog()
    table = modes.get('table', 'F.1')
    if not isinstance(table, str) or table not in catalog:
        raise ValueError('失效模式标准表无效')
    category = modes.get('category')
    if category is not None and (not isinstance(category, str) or category not in catalog[table]):
        raise ValueError('失效模式元器件类别无效')
    if 'rate' in modes:
        _number_draft(modes['rate'], '失效率')
    physics = state.get('physics', {})
    _object(physics, ('model_id', 'parameters'), '失效物理')
    model_id = physics.get('model_id', 'em')
    if not isinstance(model_id, str) or model_id not in PHYSICS_TOOLS:
        raise ValueError('失效物理模型无效')
    parameters = physics.get('parameters', {})
    _object(parameters, ('parameter_source', *(field.key for field in PHYSICS_TOOLS[model_id].fields)), '失效物理参数')
    for key, value in parameters.items():
        if key == 'parameter_source':
            _text(value, '参数依据')
        else:
            _number_draft(value, key)
