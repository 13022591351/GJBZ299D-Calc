"""Current plain JSON project protocol, with strict validation and no migration."""

import copy
from dataclasses import MISSING, asdict, fields

from .domain import Component, OperatingCondition, Project
from .standards import MODELS


def _record_fields(value, record_type, path):
    """Check a JSON record before its constructor uses any field."""
    if not isinstance(value, dict):
        raise ValueError(f'{path} 须为对象')
    definitions = fields(record_type)
    allowed = {item.name for item in definitions}
    required = {item.name for item in definitions
                if item.default is MISSING and item.default_factory is MISSING}
    missing, unknown = required - value.keys(), value.keys() - allowed
    if missing or unknown:
        detail = ('缺少字段 ' + '、'.join(sorted(missing)) if missing else
                  '包含不支持的字段 ' + '、'.join(sorted(map(str, unknown))))
        raise ValueError(f'{path} {detail}')


def encode_project(project):
    """Serialize the current project without changing grouping or stored inputs."""
    project.validate()
    value = asdict(project)
    from .domain import check_module_depth
    check_module_depth(value)
    for component, record in zip(project.components, value['components'], strict=True):
        record['domestic'] = component.is_domestic
    return value


def decode_project(value):
    """Decode one current-protocol JSON object; old and unknown fields are errors."""
    required = {'name', 'notes', 'components', 'conditions'}
    optional = {'sort_mode', 'grouped', 'group_fields', 'visible_fields', 'prediction_state', 'analysis'}
    if not isinstance(value, dict) or not required <= set(value) or set(value) - required - optional:
        raise ValueError('工程内容须包含标准字段 [name、notes、components、conditions]，且不含未知字段')
    if not isinstance(value['components'], list):
        raise ValueError('components 须为元器件列表')
    if not isinstance(value['conditions'], list):
        raise ValueError('conditions 须为工作环境列表')
    from .domain import check_module_depth
    check_module_depth(value)
    for index, item in enumerate(value['components']):
        path = f'components[{index}]'
        _record_fields(item, Component, path)
        if not isinstance(item['model'], str) or item['model'] not in MODELS:
            raise ValueError(f'{path}.model 须为已支持的计算模型名称')
        for key, kind in (('parameters', dict), ('reference', str)):
            if key in item and not isinstance(item[key], kind):
                raise ValueError(f'{path}.{key} 字段类型无效')
        if 'profiles' in item:
            if not isinstance(item['profiles'], dict):
                raise ValueError(f'{path}.profiles 字段类型无效')
            for state_key, snapshot in item['profiles'].items():
                if not isinstance(snapshot, dict) or not isinstance(snapshot.get('model'), str) or snapshot['model'] not in MODELS:
                    raise ValueError(f'{path}.profiles[{state_key!r}].model 须为已支持的计算模型名称')
    for key in ('visible_fields', 'group_fields'):
        if key in value and (not isinstance(value[key], list)
                             or any(not isinstance(item, str) for item in value[key])):
            raise ValueError(f'{key} 须为字段名称列表')
    for index, item in enumerate(value['conditions']):
        _record_fields(item, OperatingCondition, f'conditions[{index}]')
    payload = copy.deepcopy(value)
    payload['components'] = [Component(**item) for item in payload['components']]
    conditions = []
    for index, item in enumerate(payload['conditions']):
        condition = OperatingCondition(**item)
        try:
            condition.validate()
        except ValueError as error:
            raise ValueError(f'conditions[{index}]：{error}') from error
        conditions.append(condition)
    payload['conditions'] = conditions
    return Project(**payload).validate()
