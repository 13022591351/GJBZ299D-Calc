"""Qt-free project model. Rates are failures per million hours."""

import copy
import hashlib
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from functools import lru_cache
from uuid import uuid4

from .component_fields import (
    DEFAULT_GROUP_FIELDS,
    DEFAULT_VISIBLE_FIELDS,
    FIELD_KEYS,
    GROUP_FIELDS,
    SORT_MODES,
)
from .numeric import number_text
from .prediction import model_state, validate_state
from .standards import (
    ENVIRONMENTS,
    MODELS,
    Evaluation,
    evaluate,
    validate_parameters,
)

RATE_UNIT = '10⁻⁶/h'


def _scalar_parameter_key(model, parameters, variables):
    """Key only exact scalar types; structured records keep their full validation."""
    groups = []
    for values in (parameters, variables):
        if type(values) is not dict or any(type(key) is not str or type(value) not in
                (str, int, float, bool, type(None)) for key, value in values.items()):
            return None
        # Type tags keep True, 1 and 1.0 distinct for strict choice/integer checks.
        groups.append(tuple(sorted((key, type(value), value) for key, value in values.items())))
    return (model, *groups)


def _validate_bom_source(source):
    """Validate provenance before duplicate-import checks use its row and sheet."""
    required = {'row_number', 'sheet_name', 'cells', 'mapping', 'columns'}
    if not isinstance(source, dict) or set(source) != required:
        raise ValueError('BOM source 来源记录格式无效')
    if (type(source['row_number']) is not int or source['row_number'] < 1
            or not isinstance(source['sheet_name'], str)):
        raise ValueError('BOM source 的行号或工作表名称无效')
    if (not isinstance(source['cells'], list)
            or any(not isinstance(value, str) for value in source['cells'])):
        raise ValueError('BOM source.cells 须为原始单元格文本列表')
    columns = source['columns']
    if (not isinstance(columns, list) or not columns
            or any(type(column) is not int or column < 0 for column in columns)
            or len(set(columns)) != len(columns)):
        raise ValueError('BOM source.columns 须为不重复的有效列编号')
    mapping = source['mapping']
    if (not isinstance(mapping, dict)
            or any(not isinstance(key, str) or column is not None and (
                type(column) is not int or column not in columns) for key, column in mapping.items())):
        raise ValueError('BOM source.mapping 字段映射无效')


def quantity_text(components):
    known = sum(c.quantity for c in components if c.quantity is not None)
    missing = sum(c.quantity is None for c in components)
    return f'{known:,} + 待填写（{missing} 项）' if missing else f'{known:,}'


def finite(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{name} 必须是数值')
    try:
        is_fin = math.isfinite(value)
    except OverflowError as error:
        raise ValueError(f'{name} 必须是有限的' + ('正数' if positive else '非负数')) from error
    if not is_fin or value < 0 or (positive and value == 0):
        raise ValueError(f'{name} 必须是有限的' + ('正数' if positive else '非负数'))
    return value


def validate_temperature(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError('环境温度必须是大于等于 -273.15 ℃ 的有限数值')
    try:
        is_fin = math.isfinite(value)
    except OverflowError as error:
        raise ValueError('环境温度必须是大于等于 -273.15 ℃ 的有限数值') from error
    if not is_fin or value < -273.15:
        raise ValueError('环境温度必须是大于等于 -273.15 ℃ 的有限数值')


def rate_condition_key(environment, ambient_temperature_C):
    if not isinstance(environment, str) or environment not in ENVIRONMENTS:
        raise ValueError('请先填写有效的工作环境类别')
    validate_temperature(ambient_temperature_C)
    return environment, ambient_temperature_C


def check_module_depth(project, ancestors=(), depth=0):
    if not isinstance(project, dict):
        raise ValueError('工程模块的来源工程格式无效')
    if id(project) in ancestors or depth >= 16:
        raise ValueError('工程模块来源存在循环引用或超过16层嵌套')
    components = project.get('components', [])
    if not isinstance(components, list):
        raise ValueError('工程模块的来源工程元器件格式无效')
    for component in components:
        if not isinstance(component, dict):
            continue
        _check_component_depth(component, project, ancestors, depth)


def _check_component_depth(component, project, ancestors, depth):
    if isinstance(component.get('module_source'), dict):
        check_module_depth(component['module_source'].get('project'), ancestors + (id(project),), depth + 1)
    if isinstance(component.get('input_sources'), dict):
        for source in component['input_sources'].values():
            if isinstance(source, dict) and isinstance(source.get('source'), dict):
                check_module_depth(source['source'].get('project'), ancestors + (id(project),), depth + 1)
    if isinstance(component.get('profiles'), dict):
        for snapshot in component['profiles'].values():
            if isinstance(snapshot, dict):
                _check_component_depth(snapshot, project, ancestors, depth)


_ENCODED_PROJECTS_BY_DIGEST = OrderedDict()
_MAX_ENCODED_PROJECTS = 128


def _shallow_project_signature(project_dict):
    comps = project_dict.get('components')
    conds = project_dict.get('conditions')
    return (
        len(project_dict),
        project_dict.get('name'),
        project_dict.get('prediction_state'),
        len(comps) if isinstance(comps, list) else None,
        len(conds) if isinstance(conds, list) else None,
    )


def project_snapshot_digest(project_dict):
    """Canonicalize nested project dict and return its 64-hex SHA-256 digest."""
    encoded = json.dumps(project_dict, ensure_ascii=False, sort_keys=True, allow_nan=False)
    digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    _ENCODED_PROJECTS_BY_DIGEST[digest] = encoded
    _ENCODED_PROJECTS_BY_DIGEST.move_to_end(digest)
    while len(_ENCODED_PROJECTS_BY_DIGEST) > _MAX_ENCODED_PROJECTS:
        _ENCODED_PROJECTS_BY_DIGEST.popitem(last=False)
    return digest


@lru_cache(maxsize=128)
def _cached_project_by_digest(digest):
    encoded = _ENCODED_PROJECTS_BY_DIGEST.get(digest)
    if encoded is None:
        raise KeyError(digest)
    return Project.from_dict(json.loads(encoded))


def cached_project_from_snapshot(project_dict, *, digest=None):
    """Decode and validate a nested Project snapshot once per canonical SHA-256 digest."""
    if digest is None:
        digest = project_snapshot_digest(project_dict)
    try:
        return _cached_project_by_digest(digest), digest
    except KeyError:
        digest = project_snapshot_digest(project_dict)
        return _cached_project_by_digest(digest), digest


@lru_cache(maxsize=128)
def verified_module_rates(encoded_or_digest):
    if (
        isinstance(encoded_or_digest, str)
        and len(encoded_or_digest) == 64
        and re.fullmatch(r'[0-9a-f]{64}', encoded_or_digest)
        and encoded_or_digest in _ENCODED_PROJECTS_BY_DIGEST
    ):
        project = _cached_project_by_digest(encoded_or_digest)
    else:
        project = Project.from_dict(json.loads(encoded_or_digest))
    rates = {}
    for condition in project.enabled_conditions:
        context = project.calculate(condition)
        if context.error:
            raise ValueError(f'来源工程工况“{condition.name}”合计错误：{context.error}')
        total = context.total_rate
        if total is None:
            continue
        key = rate_condition_key(condition.environment, condition.ambient_temperature_C)
        if key in rates and rates[key] != total:
            raise ValueError('来源工程存在同工况不同失效率')
        rates[key] = total
    return tuple((*key, rate) for key, rate in rates.items())


_STATE_FIELDS = (
    'model',
    'parameters',
    'condition_rates',
    'module_source',
    'disclosure',
    'stress_variables',
    'rate_state',
    'input_sources',
)
_PROFILE_KEYS = frozenset(_STATE_FIELDS)

_NONOPERATING_STRESS_BY_MODEL = {
    '国产普通数字集成电路': '国产数字集成电路（非工作详细）',
    '国产模拟集成电路': '国产模拟集成电路（非工作详细）',
    '国产双极微处理器': '国产微处理器（非工作详细）',
    '国产MOS微处理器': '国产微处理器（非工作详细）',
    '国产双极SRAM': '国产存储器（非工作详细）',
    '国产MOS SRAM及CCD器件': '国产存储器（非工作详细）',
    '国产双极ROM': '国产存储器（非工作详细）',
    '国产MOS ROM': '国产存储器（非工作详细）',
    '国产双极PROM': '国产存储器（非工作详细）',
    '国产MOS PROM': '国产存储器（非工作详细）',
    '国产MOS DRAM': '国产存储器（非工作详细）',
    '国产MOS FIFO': '国产存储器（非工作详细）',
    '国产MOS UVEPROM': '国产存储器（非工作详细）',
    '国产MOS EEPROM': '国产存储器（非工作详细）',
    '国产MOS FLASH': '国产存储器（非工作详细）',
    '国产电阻网络': '国产电阻网络（非工作详细）',
    '国产电容网络': '国产电容网络（非工作详细）',
    '国产LC滤波器': '国产LC滤波器（非工作详细）',
}

_NONOPERATING_STRESS_BY_CATEGORY = {
    'D': '国产二极管（非工作详细）',
    'Q': '国产晶体管（非工作详细）',
    'V': '国产真空电子器件（非工作详细）',
    'R': '国产电阻器（非工作详细）',
    'RT': '国产敏感电阻器（非工作详细）',
    'RP': '国产电位器（非工作详细）',
    'C': '国产电容器（非工作详细）',
    'K': '国产继电器（非工作详细）',
    'M': '国产微特电机（非工作详细）',
    'FL': '国产滤波器（非工作详细）',
}

_NONOPERATING_COUNT_EXTRA = {
    '国产密封只读及可编程只读存储器（工作计数）': '国产密封存储器（非工作计数）',
    '国产非密封只读及可编程只读存储器（工作计数）': '国产非密封存储器（非工作计数）',
    '国产密封静态随机存储器（工作计数）': '国产密封存储器（非工作计数）',
    '国产非密封静态随机存储器（工作计数）': '国产非密封存储器（非工作计数）',
    '国产密封先进先出存储器（工作计数）': '国产密封存储器（非工作计数）',
}

_WORKING_EXACT_PAIR = {
    **{
        f'{name}（非工作详细）': name
        for name in (
            '国产微波单片集成电路', '国产模数混合集成电路', '国产混合集成电路', '国产声表面波器件',
            '国产光电子器件', '国产电连接器', '国产开关', '国产电感器', '国产片式电感器', '国产变压器',
            '国产振荡器', '国产谐振器', '国产磁性器件', '国产电池', '国产灯', '国产激光器',
            '国产压电陀螺', '国产光纤连接器', '国产其他电子元器件', '国产印制板', '国产焊接点', '国产SMT互连',
            '国产电阻网络', '国产电容网络', '国产LC滤波器',
        )
    },
    '国产模拟集成电路（非工作详细）': '国产模拟集成电路',
    '国产数字集成电路（非工作详细）': '国产普通数字集成电路',
    '国产滤波器（非工作详细）': '国产压电陶瓷介电石英晶体和机械滤波器',
}

_WORKING_NO_NONOP_PAIR = frozenset({'国产双极型双管', '国产硅场效应双管'})

_WORKING_BY_NONOP_PARAM = {
    '国产微处理器（非工作详细）': ('technology', {
        '双极': '国产双极微处理器',
        'MOS': '国产MOS微处理器',
    }),
    '国产电阻器（非工作详细）': ('kind', {
        '金属膜': '国产金属膜电阻器',
        '碳膜(功率 P ≤2W)': '国产碳膜电阻器',
        '玻璃釉(功率 P ≤2W)': '国产玻璃釉电阻器',
        '线绕电阻器 / 普通': '国产线绕电阻器',
        '线绕电阻器 / 精密': '国产线绕电阻器',
        '箔式电阻器 / 普通金属箔': '国产箔式电阻器',
        '箔式电阻器 / 精密金属箔': '国产箔式电阻器',
        '功率线绕电阻器': '国产功率线绕电阻器',
        '功率非线绕电阻器(功率 P ＞2W)': '国产功率非线绕电阻器',
        '片式膜电阻器': '国产片式膜电阻器',
    }),
    '国产敏感电阻器（非工作详细）': ('kind', {
        '热敏': '国产热敏电阻器',
        '压敏': '国产压敏电阻器',
        '光敏': '国产光敏电阻器',
    }),
    '国产电位器（非工作详细）': ('kind', {
        '普通线绕': '国产普通线绕电位器',
        '精密线绕': '国产精密线绕电位器',
        '微调线绕': '国产微调线绕电位器',
        '有机实芯': '国产有机实芯电位器',
        '合成碳膜': '国产合成碳膜电位器',
        '玻璃釉': '国产玻璃釉电位器',
        '非线绕精密电位器': '国产非线绕精密电位器',
    }),
    '国产电容器（非工作详细）': ('kind', {
        '纸和薄膜电容器 / 纸介': '国产纸和薄膜电容器',
        '纸和薄膜电容器 / 薄膜': '国产纸和薄膜电容器',
        '纸和薄膜电容器 / 纸-膜复合介质': '国产纸和薄膜电容器',
        '瓷介电容器 / 1 类瓷介': '国产1类瓷介电容器',
        '瓷介电容器 / 2 类瓷介': '国产2类瓷介电容器',
        '云母电容器': '国产云母电容器',
        '铝电解电容器': '国产铝电解电容器',
        '固体钽电解电容器': '国产固体钽电解电容器',
        '非固体钽电解电容器': '国产非固体钽电解电容器',
        '氧化铌电解电容器': '国产氧化铌电解电容器',
        '可变电容器 / 瓷介微调': '国产可变电容器',
        '可变电容器 / 玻璃介质微调': '国产可变电容器',
        '可变电容器 / 空气介质微调': '国产可变电容器',
    }),
    '国产二极管（非工作详细）': ('kind', {
        '普通二极管 / 整流二极管': '国产普通二极管',
        '普通二极管 / 开关二极管': '国产普通二极管',
        '普通二极管 / 检波二极管': '国产普通二极管',
        '普通二极管 / 瞬态抑制二极管': '国产普通二极管',
        '电压调整、电压基准及电流调整二极管': '国产电压调整、电压基准及电流调整二极管',
        '微波二极管 / 微波硅检波二极管': '国产微波硅检波二极管',
        '微波二极管 / 微波硅混频二极管': '国产微波硅混频二极管',
        '微波二极管 / 变容，阶跃，隧道，体效应，PIN，雪崩二极管': '国产其他微波二极管',
    }),
    '国产晶体管（非工作详细）': ('kind', {
        '双极型晶体管 / 功率晶体管': '国产双极型晶体管',
        '双极型晶体管 / 开关晶体管': '国产双极型晶体管',
        '双极型晶体管 / 达林顿晶体管': '国产双极型晶体管',
        '硅场效应晶体管': '国产硅场效应晶体管',
        '单结晶体管': '国产单结晶体管',
        '闸流晶体管': '国产闸流晶体管',
    }),
    '国产继电器（非工作详细）': ('kind', {
        '机电继电器 / 密封 / 熔焊工艺': '国产机电继电器',
        '机电继电器 / 密封 / 铅焊工艺': '国产机电继电器',
        '机电继电器 / 非密封': '国产机电继电器',
        '固体继电器': '国产固体继电器',
    }),
    '国产微特电机（非工作详细）': ('kind', {
        '电动机': '国产普通电动机和风机',
        '风机': '国产普通电动机和风机',
        '旋转变压器、自整角机': '国产自整角机和旋转变压器',
        '时间累加器': '国产时间累计器',
    }),
    '国产真空电子器件（非工作详细）': ('kind', {
        '磁控管': '国产磁控管',
        '行波管': '国产行波管',
        '速调管 / 连续波': '国产连续波速调管',
        '速调管 / 脉冲速调管': '国产脉冲速调管',
        **{kind: '国产普通电子管'
           for kind in next(f.choices for f in MODELS['国产普通电子管'].fields if f.key == 'kind')},
    }),
}

_VACUUM_COUNT_COMPATIBLE_KINDS = frozenset({
    '磁控管', '行波管', '微波电子管 / 连续波速调管', '微波电子管 / 脉冲速调管',
})


def _is_unconfigured_snapshot(snapshot):
    """True when a saved state snapshot has no model, parameters, rates or external sources."""
    return (
        isinstance(snapshot, dict)
        and snapshot.get('model') == '待配置'
        and not snapshot.get('parameters')
        and not snapshot.get('condition_rates')
        and snapshot.get('module_source') is None
        and snapshot.get('disclosure') is None
        and not snapshot.get('stress_variables')
        and not snapshot.get('input_sources')
    )


def _suggest_state_model(previous_model, domestic, target_state, previous_parameters=None):
    """Suggest an unambiguous paired model across working/nonoperating without guessing stress or quality."""
    if not previous_model or previous_model in ('待配置', '已知单件失效率', '工程模块') or previous_model not in MODELS:
        return '待配置'
    params = previous_parameters or {}
    if target_state == 'nonoperating':
        if not domestic or not previous_model.startswith('国产') or previous_model in _WORKING_NO_NONOP_PAIR:
            return '待配置'
        spec = MODELS[previous_model]
        method = spec['method']
        if method == 'working_stress':
            candidate = _NONOPERATING_STRESS_BY_MODEL.get(previous_model)
            if not candidate:
                exact = f'{previous_model}（非工作详细）'
                candidate = exact if exact in MODELS else _NONOPERATING_STRESS_BY_CATEGORY.get(spec.get('category'), '待配置')
        elif method == 'working_count' and previous_model.endswith('（工作计数）'):
            if previous_model in ('国产MOS可擦可编程只读存储器（工作计数）', '国产MOS动态随机存储器（工作计数）'):
                kind = str(params.get('kind', ''))
                if kind.endswith('/ 密封'):
                    candidate = '国产密封存储器（非工作计数）'
                elif kind.endswith('/ 非密封'):
                    candidate = '国产非密封存储器（非工作计数）'
                else:
                    return '待配置'
            else:
                exact = f'{previous_model[:-6]}（非工作计数）'
                candidate = exact if exact in MODELS else _NONOPERATING_COUNT_EXTRA.get(previous_model, '待配置')
        else:
            return '待配置'
        return candidate if candidate in MODELS and model_state(candidate) == 'nonoperating' else '待配置'

    if target_state == 'working':
        if previous_model in _WORKING_EXACT_PAIR:
            candidate = _WORKING_EXACT_PAIR[previous_model]
            return candidate if candidate in MODELS and model_state(candidate) == 'working' else '待配置'
        if previous_model in _WORKING_BY_NONOP_PARAM:
            param_key, mapping = _WORKING_BY_NONOP_PARAM[previous_model]
            candidate = mapping.get(params.get(param_key), '待配置')
            return candidate if candidate in MODELS and model_state(candidate) == 'working' else '待配置'
        if previous_model.endswith('（非工作计数）'):
            if previous_model == '国产真空电子器件（非工作计数）' and 'kind' in params:
                if params['kind'] not in _VACUUM_COUNT_COMPATIBLE_KINDS:
                    return '待配置'
            exact = f'{previous_model[:-7]}（工作计数）'
            return exact if exact in MODELS and model_state(exact) == 'working' else '待配置'
    return '待配置'


@dataclass
class Component:
    name: str
    model: str
    quantity: int | None
    parameters: dict
    reference: str = ''
    identifier: str = field(default_factory=lambda: str(uuid4()))
    bom: dict = field(default_factory=dict)
    domestic: bool | None = None
    condition_rates: list[dict] = field(default_factory=list)
    module_source: dict | None = None
    disclosure: dict | None = None
    stress_variables: dict = field(default_factory=dict)
    rate_state: str | None = None
    input_sources: dict = field(default_factory=dict)
    profiles: dict = field(default_factory=dict)

    def __setattr__(self, name, value):
        object.__setattr__(self, name, value)
        if (
            name == 'model'
            and isinstance(value, str)
            and value in MODELS
            and value not in ('待配置', '已知单件失效率', '工程模块')
        ):
            object.__setattr__(self, 'rate_state', model_state(value))

    def __post_init__(self):
        if not isinstance(self.model, str):
            raise ValueError('元器件计算模型须为名称文本')
        if self.domestic is None or (type(self.domestic) is bool
                                     and self.model.startswith(('国产', '进口'))):
            object.__setattr__(self, 'domestic', self.is_domestic)
        if self.rate_state is None:
            default_state = (
                model_state(self.model)
                if self.model in MODELS and self.model not in ('待配置', '已知单件失效率', '工程模块')
                else 'working'
            )
            object.__setattr__(self, 'rate_state', default_state)

    def state_for_active(self):
        if self.model in ('待配置', '已知单件失效率', '工程模块') or self.model not in MODELS:
            return self.rate_state
        return model_state(self.model)

    def _profile_snapshot(self):
        return {
            'model': self.model,
            'parameters': copy.deepcopy(self.parameters),
            'condition_rates': copy.deepcopy(self.condition_rates),
            'module_source': copy.deepcopy(self.module_source),
            'disclosure': copy.deepcopy(self.disclosure),
            'stress_variables': copy.deepcopy(self.stress_variables),
            'rate_state': self.state_for_active(),
            'input_sources': copy.deepcopy(self.input_sources),
        }

    def _apply_snapshot(self, snapshot):
        self.model = snapshot['model']
        self.parameters = copy.deepcopy(snapshot['parameters'])
        self.condition_rates = copy.deepcopy(snapshot['condition_rates'])
        self.module_source = copy.deepcopy(snapshot['module_source'])
        self.disclosure = copy.deepcopy(snapshot['disclosure'])
        self.stress_variables = copy.deepcopy(snapshot['stress_variables'])
        self.rate_state = snapshot['rate_state']
        self.input_sources = copy.deepcopy(snapshot['input_sources'])

    def switch_state(self, target_state, *, auto_suggest=False):
        """Switch active working/nonoperating profile while keeping the previous state intact."""
        validate_state(target_state)
        current_state = self.state_for_active()
        if current_state == target_state:
            return False
        prev_snapshot = self._profile_snapshot()
        self.profiles[current_state] = prev_snapshot
        saved = self.profiles.pop(target_state, None)
        if saved is None or (auto_suggest and _is_unconfigured_snapshot(saved)):
            suggested = (_suggest_state_model(prev_snapshot['model'], self.is_domestic, target_state,
                                              prev_snapshot.get('parameters'))
                         if auto_suggest else '待配置')
            reused_params, reused_sources = ({}, {})
            if auto_suggest and suggested not in ('待配置', '已知单件失效率', '工程模块'):
                from .state_transfer import translate_state_parameters
                reused_params, reused_sources = translate_state_parameters(
                    prev_snapshot['model'],
                    prev_snapshot.get('parameters'),
                    prev_snapshot.get('input_sources'),
                    suggested,
                )
            self._apply_snapshot({
                'model': suggested,
                'parameters': reused_params,
                'condition_rates': [],
                'module_source': None,
                'disclosure': None,
                'stress_variables': {},
                'rate_state': target_state,
                'input_sources': reused_sources,
            })
        else:
            if auto_suggest and saved['model'] not in ('待配置', '已知单件失效率', '工程模块'):
                from .state_transfer import backfill_state_snapshot
                merged_params, merged_sources = backfill_state_snapshot(prev_snapshot, saved)
                saved = dict(saved, parameters=merged_params, input_sources=merged_sources)
            self._apply_snapshot(saved)
        return True

    def component_for_state(self, target_state):
        """Return an independent view of this component in target_state without mutating self."""
        validate_state(target_state)
        if self.state_for_active() == target_state:
            return Component(
                name=self.name,
                model=self.model,
                quantity=self.quantity,
                parameters=copy.deepcopy(self.parameters),
                reference=self.reference,
                identifier=self.identifier,
                bom=copy.deepcopy(self.bom),
                domestic=self.domestic,
                condition_rates=copy.deepcopy(self.condition_rates),
                module_source=copy.deepcopy(self.module_source),
                disclosure=copy.deepcopy(self.disclosure),
                stress_variables=copy.deepcopy(self.stress_variables),
                rate_state=self.rate_state,
                input_sources=copy.deepcopy(self.input_sources),
            )
        clone = Component(
            name=self.name, model='待配置', quantity=self.quantity, parameters={},
            reference=self.reference, identifier=self.identifier, bom=copy.deepcopy(self.bom),
            domestic=self.domestic, rate_state=target_state,
        )
        if target_state in self.profiles:
            clone._apply_snapshot(self.profiles[target_state])
        return clone

    @property
    def is_domestic(self):
        """A selected model determines origin; otherwise use the table preference."""
        if self.model.startswith('国产'):
            return True
        if self.model.startswith('进口'):
            return False
        return True if self.domestic is None else self.domestic

    def set_domestic(self, domestic):
        """Change model scope without reusing incompatible parameters or rates."""
        if type(domestic) is not bool:
            raise ValueError('国产选项必须为勾选或未勾选')
        if self.model == '工程模块':
            self.domestic = domestic
            return False
        changed = self.is_domestic != domestic
        self.domestic = domestic
        if changed:
            self.model = '待配置'
            self.parameters = {}
            self.condition_rates = []
            self.module_source = None
            self.disclosure = None
            self.stress_variables = {}
            self.input_sources = {}
            self.profiles = {}
        return changed

    def condition_rate_entries(self):
        """Validate and copy the rates explicitly assigned to each operating condition."""
        if not isinstance(self.condition_rates, list):
            raise ValueError('按工作环境保存的失效率格式无效')
        entries, keys = [], set()
        for entry in self.condition_rates:
            if not isinstance(entry, dict) or set(entry) != {'environment', 'ambient_temperature_C', 'rate'}:
                raise ValueError('工作环境失效率条目格式无效')
            key = rate_condition_key(entry['environment'], entry['ambient_temperature_C'])
            finite(entry['rate'], '工作失效率')
            if key in keys:
                raise ValueError('同一环境类别和环境温度的失效率重复')
            keys.add(key)
            entries.append(dict(entry))
        return entries

    def condition_rate(self, environment, ambient_temperature_C):
        if environment is None or ambient_temperature_C is None:
            return None
        key = rate_condition_key(environment, ambient_temperature_C)
        return next((entry['rate'] for entry in self.condition_rate_entries()
                     if (entry['environment'], entry['ambient_temperature_C']) == key), None)

    def set_condition_rate(self, environment, ambient_temperature_C, rate):
        if self.model != '已知单件失效率':
            raise ValueError('只有直接输入的失效率可编辑；工程模块须重新引入来源工程')
        key = rate_condition_key(environment, ambient_temperature_C)
        finite(rate, '工作失效率')
        if self.quantity is not None:
            finite(rate * self.quantity, '数量与工作失效率的乘积')
        entries = [entry for entry in self.condition_rate_entries()
                   if (entry['environment'], entry['ambient_temperature_C']) != key]
        entries.append({'environment': key[0], 'ambient_temperature_C': key[1], 'rate': rate})
        self.condition_rates = entries

    def remove_condition_rate(self, environment, ambient_temperature_C):
        if self.model != '已知单件失效率':
            raise ValueError('只有直接输入的失效率可编辑；工程模块须重新引入来源工程')
        key = rate_condition_key(environment, ambient_temperature_C)
        self.condition_rates = [entry for entry in self.condition_rate_entries()
                                if (entry['environment'], entry['ambient_temperature_C']) != key]

    def validate_module_source(self, *, _use_cache=False):
        source = self.module_source
        if (not isinstance(source, dict) or set(source) != {'file_name', 'sha256', 'project'}
                or not isinstance(source['file_name'], str) or not source['file_name']
                or not isinstance(source['sha256'], str)
                or re.fullmatch(r'[0-9a-fA-F]{64}', source['sha256']) is None):
            raise ValueError('工程模块缺少有效的来源工程与文件摘要')
        check_module_depth(source['project'])
        if source['project'].get('prediction_state', 'working') != self.rate_state:
            raise ValueError('工程模块的预计状态与来源工程不一致')
        proj_dict = source['project']
        sig = _shallow_project_signature(proj_dict)
        cached = self.__dict__.get('_module_digest_cache')
        if _use_cache and cached is not None and cached[0] is proj_dict and cached[1] == sig and cached[2] in _ENCODED_PROJECTS_BY_DIGEST:
            digest = cached[2]
        else:
            digest = project_snapshot_digest(proj_dict)
            self.__dict__['_module_digest_cache'] = (proj_dict, sig, digest)
        expected = verified_module_rates(digest)
        actual = {(entry['environment'], entry['ambient_temperature_C']): entry['rate']
                  for entry in self.condition_rate_entries()}
        if not expected:
            raise ValueError('来源工程没有已启用且完整计算的工作环境')
        if actual != {(environment, temperature): rate for environment, temperature, rate in expected}:
            raise ValueError('工程模块的工况失效率与来源工程计算结果不一致')

    def evaluate(self, environment, ambient_temperature_C):
        if self.quantity is None:
            return Evaluation(None, error='元器件数量待填写')
        try:
            if self.model in ('已知单件失效率', '工程模块'):
                if self.stress_variables:
                    return Evaluation(None, error='外部失效率不保存内部应力变量')
                if self.model == '工程模块':
                    self.validate_module_source(_use_cache=True)
                rate = self.condition_rate(environment, ambient_temperature_C)
                if rate is None:
                    return Evaluation(None, error='当前环境类别和温度未录入适用失效率，请补充或重新确认')
                finite(rate * self.quantity, '数量与工作失效率的乘积')
                result = Evaluation(rate, (('λp', rate,
                    f'用户输入；适用环境 {environment}，{number_text(ambient_temperature_C)} ℃'),))
                if self.model == '工程模块' and result.rate is not None:
                    return Evaluation(result.rate, (('λp', result.rate,
                        f'来源工程 {self.module_source["project"]["name"]}；'
                        f'已校验适用环境 {environment}，{number_text(ambient_temperature_C)} ℃'),))
                if self.disclosure is not None and result.rate is not None:
                    return Evaluation(result.rate, (('λp', result.rate,
                        f'脱敏工程保留的原工况预计结果；适用环境 {environment}，'
                        f'{number_text(ambient_temperature_C)} ℃'),))
                return result
            if self.condition_rates or self.module_source is not None:
                return Evaluation(None, error='自动模型不保存外部工况失效率或工程模块来源')
            if self.input_sources:
                from .input_sources import evaluate_with_sources
                return evaluate_with_sources(self, environment, ambient_temperature_C)
            return evaluate(self.model, self.parameters, environment,
                            ambient_temperature_C=ambient_temperature_C,
                            stress_variables=self.stress_variables)
        except (ValueError, OverflowError, TypeError, RecursionError) as error:
            return Evaluation(None, error=str(error))

    def validate(self, *, _parameter_cache=None, _validate_profiles=True):
        validate_state(self.rate_state)
        if not isinstance(self.model, str) or self.model not in MODELS:
            raise ValueError('元器件计算模型无效')
        if self.model not in ('待配置', '已知单件失效率', '工程模块') and self.rate_state != model_state(self.model):
            raise ValueError('元器件计算模型的预计状态与记录状态不一致')
        if self.domestic is not None and type(self.domestic) is not bool:
            raise ValueError('国产选项格式无效')
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('元器件名称不能为空')
        if self.quantity is not None and (type(self.quantity) is not int or not 1 <= self.quantity <= 1_000_000):
            raise ValueError('数量须为 1～1,000,000 的整数')
        if not isinstance(self.reference, str) or not isinstance(self.identifier, str) or not self.identifier:
            raise ValueError('元器件说明或标识格式无效')
        if self.model in ('已知单件失效率', '工程模块') and self.parameters:
            raise ValueError('外部失效率只按工况保存在 condition_rates，不保存单值参数')
        if not isinstance(self.parameters, dict):
            raise ValueError('器件参数须为对象')
        from .input_sources import validate_sources
        if not isinstance(self.input_sources, dict) or self.input_sources:
            validate_sources(self)
        parameters = {key: value for key, value in self.parameters.items() if key not in self.input_sources}
        parameter_key = (_scalar_parameter_key(self.model, parameters, self.stress_variables)
                         if _parameter_cache is not None else None)
        if parameter_key is None or parameter_key not in _parameter_cache:
            try:
                validate_parameters(self.model, parameters, allow_incomplete=True,
                                    stress_variables=self.stress_variables)
            except OverflowError as error:
                raise ValueError('器件参数数值超出有效范围') from error
            if parameter_key is not None:
                _parameter_cache.add(parameter_key)
        entries = self.condition_rate_entries()
        if self.model not in ('已知单件失效率', '工程模块') and entries:
            raise ValueError('自动模型不保存外部工况失效率')
        if self.model == '工程模块':
            self.validate_module_source()
        elif self.module_source is not None:
            raise ValueError('非工程模块不保存来源工程快照')
        if self.disclosure is not None:
            if self.model != '已知单件失效率':
                raise ValueError('只有脱敏的已知失效率项保存公开参数档位')
            disclosure = self.disclosure
            if (not isinstance(disclosure, dict)
                    or not {'model', 'parameter_ranges', 'stress'} <= set(disclosure)
                    or set(disclosure) - {'model', 'parameter_ranges', 'stress', 'stress_bins'}
                    or not isinstance(disclosure['model'], str) or disclosure['model'] not in MODELS
                    or disclosure['stress'] not in ('内部', '标准档位')
                    or not isinstance(disclosure['parameter_ranges'], list)):
                raise ValueError('公开参数档位格式无效')
            if disclosure['model'] not in ('待配置', '已知单件失效率', '工程模块') and model_state(disclosure['model']) != self.rate_state:
                raise ValueError('公开参数所声明模型的预计状态与工况失效率状态不一致')
            from .parameter_disclosure import validate_stress_bins
            validate_stress_bins(disclosure)
            for parameter in disclosure['parameter_ranges']:
                if (not isinstance(parameter, dict) or set(parameter) != {'name', 'range', 'source'}
                        or not all(isinstance(value, str) and value.strip() for value in parameter.values())):
                    raise ValueError('公开参数档位条目格式无效')
        if self.quantity is not None:
            for entry in entries:
                finite(entry['rate'] * self.quantity, '数量与工作失效率的乘积')
        if not isinstance(self.profiles, dict):
            raise ValueError('元器件双状态配置记录格式无效')
        if _validate_profiles and self.profiles:
            active_state = self.state_for_active()
            for state_key, snapshot in self.profiles.items():
                validate_state(state_key)
                if state_key == active_state:
                    raise ValueError('元器件备用状态配置不能与当前活动状态重复')
                if not isinstance(snapshot, dict) or set(snapshot) != _PROFILE_KEYS:
                    raise ValueError('元器件备用状态配置字段无效')
                if not isinstance(snapshot['model'], str) or snapshot['model'] not in MODELS:
                    raise ValueError('元器件备用状态配置模型无效')
                probe = Component(
                    name=self.name, model=snapshot['model'], quantity=self.quantity,
                    parameters=copy.deepcopy(snapshot['parameters']),
                    reference=self.reference, identifier=self.identifier,
                    domestic=self.domestic,
                    condition_rates=copy.deepcopy(snapshot['condition_rates']),
                    module_source=copy.deepcopy(snapshot['module_source']),
                    disclosure=copy.deepcopy(snapshot['disclosure']),
                    stress_variables=copy.deepcopy(snapshot['stress_variables']),
                    rate_state=snapshot['rate_state'],
                    input_sources=copy.deepcopy(snapshot['input_sources']),
                )
                probe.validate(_parameter_cache=_parameter_cache, _validate_profiles=False)
                if probe.state_for_active() != state_key:
                    raise ValueError('元器件备用状态配置与对应预计状态不一致')
        if not isinstance(self.bom, dict):
            raise ValueError('BOM 来源格式无效')
        if self.bom:
            allowed_bom_keys = {'row', 'file_name', 'sha256', 'assigned_designators', 'source'}
            if set(self.bom) - allowed_bom_keys:
                raise ValueError('BOM 来源包含未知字段')
            if not isinstance(self.bom.get('row'), dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in self.bom['row'].items()):
                raise ValueError('BOM 原始行格式无效')
            for k in ('file_name', 'sha256'):
                if k in self.bom and not isinstance(self.bom[k], str):
                    raise ValueError('BOM 文件来源无效')
            if 'assigned_designators' in self.bom:
                refs = self.bom['assigned_designators']
                if not isinstance(refs, list) or any(not isinstance(r, str) or not r for r in refs):
                    raise ValueError('BOM 当前计算位号无效')
            if 'source' in self.bom:
                if any(not isinstance(self.bom.get(k), str) for k in ('file_name', 'sha256')):
                    raise ValueError('BOM 文件来源无效')
                _validate_bom_source(self.bom['source'])
        return self


@dataclass
class OperatingCondition:
    name: str = '工况 1'
    environment: str | None = 'GB'
    ambient_temperature_C: float | None = 25
    identifier: str = field(default_factory=lambda: str(uuid4()))
    enabled: bool = True

    def validate(self):
        if type(self.enabled) is not bool:
            raise ValueError('工作环境计算选项格式无效')
        if not isinstance(self.name, str) or not isinstance(self.identifier, str) or not self.identifier:
            raise ValueError('工作环境条目格式无效')
        if self.environment is not None and (not isinstance(self.environment, str)
                                             or self.environment not in ENVIRONMENTS):
            raise ValueError('工程环境类别无效')
        if self.ambient_temperature_C is not None:
            validate_temperature(self.ambient_temperature_C)


@dataclass(init=False)
class Project:
    name: str
    notes: str
    components: list[Component]
    conditions: list[OperatingCondition]
    sort_mode: str
    grouped: bool
    group_fields: list[str]
    visible_fields: list[str]
    prediction_state: str
    analysis: dict

    def __init__(self, name='未命名工程', notes='', components=None, *, conditions=None,
                 sort_mode='bom', grouped=True, group_fields=None, visible_fields=None,
                 prediction_state='working', analysis=None):
        self.name, self.notes = name, notes
        self.components = components if components is not None else []
        self.conditions = conditions if conditions is not None else [OperatingCondition()]
        self.sort_mode, self.grouped = sort_mode, grouped
        self.group_fields = list(DEFAULT_GROUP_FIELDS) if group_fields is None else group_fields
        self.visible_fields = list(DEFAULT_VISIBLE_FIELDS) if visible_fields is None else visible_fields
        self.prediction_state = prediction_state
        self.analysis = {} if analysis is None else analysis

    @property
    def enabled_conditions(self):
        return [condition for condition in self.conditions if condition.enabled]

    def condition(self, condition):
        identifier = condition.identifier if isinstance(condition, OperatingCondition) else condition
        return next((item for item in self.conditions if item.identifier == identifier), None)

    def set_prediction_state(self, state, *, auto_suggest=False):
        """Switch project state while isolating each state's configured parameters and rates."""
        validate_state(state)
        if state == self.prediction_state:
            return False
        for component in self.components:
            component.switch_state(state, auto_suggest=auto_suggest)
        self.prediction_state = state
        return True

    def calculate(self, condition, *, prediction_state=None):
        from .calculation import calculate_condition
        selected = self.condition(condition)
        if selected is None:
            raise ValueError('工作环境不属于当前工程')
        state = self.prediction_state if prediction_state is None else prediction_state
        validate_state(state)
        items = (self.components if state == self.prediction_state
                 else [component.component_for_state(state) for component in self.components])
        return calculate_condition(items, selected, prediction_state=state)

    def evaluate(self, component, *, condition):
        selected = self.condition(condition)
        if selected is None:
            raise ValueError('工作环境不属于当前工程')
        if not selected.enabled:
            return Evaluation(None, error='该工作环境未启用计算')
        from .calculation import evaluate_component
        return evaluate_component(component, selected.environment, selected.ambient_temperature_C,
                                  self.prediction_state)

    def validate(self):
        validate_state(self.prediction_state)
        from .analysis_methods import validate_analysis_state
        validate_analysis_state(self.analysis)
        if not isinstance(self.analysis, dict):
            raise ValueError('分析记录须为对象')
        try:
            json.dumps(self.analysis, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError, OverflowError, RecursionError) as error:
            raise ValueError('分析记录必须为有限数值和可读 JSON 数据') from error
        if not isinstance(self.conditions, list) or not self.conditions:
            raise ValueError('至少保留一行工作环境')
        condition_ids = set()
        for condition in self.conditions:
            if not isinstance(condition, OperatingCondition):
                raise ValueError('工作环境条目格式无效')
            condition.validate()
            if condition.identifier in condition_ids:
                raise ValueError('存在重复工作环境标识')
            condition_ids.add(condition.identifier)
        if type(self.grouped) is not bool:
            raise ValueError('分组显示选项无效')
        for values, allowed in ((self.group_fields, GROUP_FIELDS), (self.visible_fields, FIELD_KEYS)):
            if (not isinstance(values, list) or not all(isinstance(v, str) for v in values)
                    or len(set(values)) != len(values) or not set(values) <= allowed):
                raise ValueError('表格字段选项无效')
        if not self.group_fields or not {'index', 'designators', 'quantity'} <= set(self.visible_fields):
            raise ValueError('至少选择一个分组字段；位号和数量列须保留')
        if self.sort_mode not in SORT_MODES:
            raise ValueError('工程排序选项无效')
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError('工程名称不能为空')
        if not isinstance(self.notes, str) or not isinstance(self.components, list):
            raise ValueError('工程格式无效')
        seen, parameter_cache = set(), set()
        for component in self.components:
            # Repeated BOM models often share all scalar inputs. Reuse only
            # their parameter check within this one validation pass; every
            # component's quantity, provenance, rates and ID are still checked.
            component.validate(_parameter_cache=parameter_cache)
            if component.identifier in seen:
                raise ValueError('存在重复元器件标识')
            seen.add(component.identifier)
        return self

    def to_dict(self):
        from .project_codec import encode_project
        return encode_project(self)

    @classmethod
    def from_dict(cls, value):
        from .project_codec import decode_project
        return decode_project(value)


def dual_transistor(p1, ps, p2, pt, c=1):
    for name, value in (('P1', p1), ('P2', p2), ('C', c)):
        finite(value, name)
    finite(ps, 'Ps', positive=True)
    finite(pt, 'PT', positive=True)
    result = (p1 / ps + p2 * (2 * ps - pt) / (pt * ps)) * c
    return finite(result, '应力比 S（请检查功率条件）')
