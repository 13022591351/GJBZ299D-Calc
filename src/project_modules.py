"""Import a complete, auditable supplier project as one physical module."""

import copy
import hashlib
import json
from pathlib import Path

from .domain import Component, Project, rate_condition_key
from .excel_limits import MAX_PACKAGE_BYTES
from .shared_io import read_supplier_bytes


def _strip_identifiers(value):
    if isinstance(value, dict):
        return {k: _strip_identifiers(v) for k, v in value.items() if k != 'identifier'}
    if isinstance(value, list):
        return [_strip_identifiers(item) for item in value]
    return value


def module_project(component):
    if component.model != '工程模块' or not isinstance(component.module_source, dict):
        raise ValueError('该预计对象没有来源工程快照')
    return Project.from_dict(copy.deepcopy(component.module_source['project']))


def source_condition_rates(project):
    """Only complete, enabled source conditions become usable module rates."""
    values = {}
    for condition in project.enabled_conditions:
        context = project.calculate(condition)
        if context.error:
            raise ValueError(f'来源工程工况“{condition.name}”合计错误：{context.error}')
        rate = context.total_rate
        if rate is None:
            continue
        key = rate_condition_key(condition.environment, condition.ambient_temperature_C)
        if key in values and values[key]['rate'] != rate:
            raise ValueError('来源工程同一环境类别和温度存在不同计算结果')
        values[key] = {'environment': condition.environment,
                       'ambient_temperature_C': condition.ambient_temperature_C,
                       'rate': rate}
    return list(values.values())


def create_project_module(source_project, *, name, quantity=1, source_path=None, expected_sha256=None):
    snapshot = source_project.to_dict()
    source = Project.from_dict(snapshot)
    rates = source_condition_rates(source)
    if not rates:
        raise ValueError('来源工程没有已启用且计算完整的工况，无法作为模块引入')
    path = Path(source_path) if source_path is not None else None
    if path is not None:
        if path.stat().st_size > MAX_PACKAGE_BYTES:
            raise ValueError('来源工程文件过大，无法读取')
        encoded = path.read_bytes()
        digest = hashlib.sha256(encoded).hexdigest()
        try:
            current_project = read_supplier_bytes(encoded, path.suffix)
        except (TypeError, KeyError, OverflowError, RecursionError) as error:
            raise ValueError('来源工程的内容格式无效，请重新打开后引入') from error
        if expected_sha256 is not None:
            if digest.lower() != str(expected_sha256).lower():
                raise ValueError('来源工程在选择后已修改，请重新打开后引入')
        elif _strip_identifiers(current_project.to_dict()) != _strip_identifiers(snapshot):
            raise ValueError('来源工程在选择后已修改，请重新打开后引入')
    else:
        encoded = json.dumps(snapshot, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')
        digest = hashlib.sha256(encoded).hexdigest()
    provenance = {'file_name': path.name if path else source.name + '.json',
                  'sha256': digest, 'project': snapshot}
    return Component(name.strip(), '工程模块', quantity, {},
                     reference=f'来源工程：{source.name}；内部对象按对应工况汇总，作为一个模块计入。',
                     condition_rates=rates, module_source=provenance,
                     rate_state=source.prediction_state).validate()
