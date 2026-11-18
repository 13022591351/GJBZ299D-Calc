"""Explicit physical inputs and table mappings for supported 299D models.

The environment code belongs to the project. No component stores an environment
factor. Coefficients are read from the standard using named physical quantities.
"""

import html
import json
import math
import re
from dataclasses import replace
from decimal import Decimal
from functools import lru_cache

from .model_spec import TEMP_CHOICE_KEYS, Evaluation, Input, ModelSpec
from .numeric import number_text
from .stress import (
    MissingStressVariable,
    StressAdjustment,
    ceil_to_grid,
    trace_number,
    validate_stress,
    validate_stress_variables,
)

ENVIRONMENTS = dict(zip(
    'GB GMS GF1 GF2 GM1 GM2 MP NSB NS1 NS2 NU AIF AUF AIC AUC ARW SF ML MF'.split(),
    ['地面良好', '导弹发射井', '一般地面固定', '恶劣地面固定', '平稳地面移动',
     '剧烈地面移动', '背负', '潜艇', '舰船良好舱内', '舰船普通舱内', '舰船舱外',
     '战斗机座舱', '战斗机无人舱', '运输机座舱', '运输机无人舱', '直升机',
     '宇宙飞行', '导弹发射', '导弹飞行'], strict=True))

MODELS = {
    '已知单件失效率': ModelSpec(
        (Input('λp', '失效率 λp（10⁻⁶/h）', positive=False),),
        'λp = 输入的失效率',
        'GJB/Z 299D—2024 用户输入，须提供适用环境和来源',
        '按环境类别和环境温度分别保存单件失效率。',
    ),
    '工程模块': ModelSpec(
        (),
        'λ模块 = Σ(N内部 × λ内部)',
        '供应商工程快照；按相同环境类别和环境温度汇总',
        '将整份工程作为一个微模块、密封模块或其他组件引入。内部对象仅在模块内汇总一次；'
        '只使用源工程已启用且全部对象可计算的工况。源文件变动不影响已引入的快照。',
    ),
    '待配置': ModelSpec(
        (),
        '尚未配置器件类型与参数',
        'GJB/Z 299D—2024 待配置',
        '请选择器件类型并填写参数。',
    ),
}

# Device families register pure calculators and declarative physical inputs.
# Imports are deliberately late; family modules must not import standards at module scope.
from .models import MODEL_GROUPS  # noqa: E402

for _group in MODEL_GROUPS:
    if MODELS.keys() & _group.keys():
        raise RuntimeError('重复的标准计算模型')
    MODELS.update(_group)

# Keep saved model identifiers stable; presentation follows the standard's wording.
MODEL_NAMES = {'国产普通数字集成电路': '国产通用数字集成电路',
               '已知单件失效率': '失效率（已知值）'}

AUTOMATIC_MODELS = tuple(name for name in MODELS if name not in ('待配置', '已知单件失效率', '工程模块'))

FACTOR_TITLES = {
    'λb': '基本失效率', 'λb1': '基本失效率', 'λb2': '基本失效率',
    'λNb': '非工作基本失效率', 'λNb1': '非工作基本失效率', 'λNb2': '非工作基本失效率',
    'λG': '通用失效率', 'λNG': '非工作通用失效率',
    'λC': '网络复杂度失效率', 'λRT': '膜电阻失效率', 'λSF': '工艺和封装失效率',
    'N': '使用的金属化孔数', 'NR': '使用中的电阻数', 'NE': '网络电阻数',
    'λp': '工作失效率', 'λNP': '非工作失效率',
    'πE': '环境系数', 'πNE': '非工作环境系数',
    'πQ': '质量系数', 'πNQ': '非工作质量系数',
    'πV': '电压应力系数', 'πL': '成熟系数', 'πNL': '非工作成熟系数',
    'C1': '电路复杂度失效率', 'C2': '电路复杂度失效率', 'C3': '封装复杂度失效率',
    'πT': '温度应力系数', 'πNT': '非工作温度应力系数', 'πNF': '非工作封装系数',
    'πR': '阻值系数', 'πK': '种类系数', 'πCV': '电容量系数',
    'πch': '表面贴装系数', 'πNch': '非工作表面贴装系数',
    'πS': '应力系数', 'πS2': '电压应力系数', 'πSI': '输出电流系数',
    'πSV': '瞬态电压系数', 'πSR': '串联电阻系数',
    'πA': '应用系数', 'πr': '额定功率系数', 'πNr': '非工作额定参数系数',
    'πC': '结构系数', 'πNC': '非工作复杂度系数',
    'πC1': '触点形式系数', 'πC2': '输出电路数系数',
    'πCYC': '循环系数', 'πPT': '可编程工艺系数', 'πP': '封装系数',
    'πF': '电路功能系数', 'πM': '匹配网络系数', 'πB': '发射极镇流系数',
    'πTAPS': '引出端系数', 'πU': '利用率系数',
    'πf': '频率系数', 'πk': '结构系数', 'πm': '传输模式系数', 'πp': '端面形式系数',
    'A': '失效率水平调整参数', 'CR': '温度循环率系数',
}

_CELL_ID_PATTERN = re.compile(r'；(p\d+-[a-z]\d+-r\d+-c\d+)(?=；|$)')
_SOURCE_LINK_PATTERN = re.compile(r'表\s*(?P<tnum>[A-Z]\.\d+|\d+)')


def environment_text(code):
    return f'{code} · {ENVIRONMENTS[code]}' if code in ENVIRONMENTS else '尚未选择工程环境'


def model_name(model):
    return MODEL_NAMES.get(model, model)


def _iter_stress_fields(fields):
    for field in fields:
        if field.stress_max is not None:
            yield field
        if field.record_fields:
            yield from _iter_stress_fields(field.record_fields)


def stress_inputs(model):
    return tuple(_iter_stress_fields(MODELS.get(model, {}).get('fields', ())))


def _top_stress_inputs(model):
    return tuple(field for field in MODELS.get(model, {}).get('fields', ()) if field.stress_max is not None)


def _stress_source(model, parameters, key):
    source = next(field.stress_source for field in stress_inputs(model) if field.key == key)
    if isinstance(source, tuple):
        if model == '国产2类瓷介电容器':
            return source[0 if parameters['rated_temperature_C'] == 85 else 1]
        raise ValueError('该应力参数缺少标准来源选择规则')
    return source


def _validate_calculable_options(model, parameters):
    kind = parameters.get('kind')
    if model == '国产感性元件（工作计数）' and kind == '变压器 / 小功率脉冲变压器':
        raise ValueError('表 377 续表两行均标为小功率脉冲变压器但通用率不同，该分类拒算；请使用详细模型')
    if model == '进口电阻器（工作计数）' and kind == '电阻网络 / 每个分立元件（N=10）':
        raise ValueError('表 C.126 分立电阻网络同时标注每个分立元件与 N=10，计量单位不明确；请使用进口电阻网络详细模型')
    if model == '进口电容器' and kind in ('纸介', '纸膜复合', '非固体钽'):
        raise ValueError('表 C.63 未给出该电容类型的表面贴装系数；请核对类型和安装形式')
    if model == '进口电容网络' and kind in ('纸介', '纸膜复合', '非固体钽', '瓷介微调', '玻璃介质', '空气介质', '真空'):
        raise ValueError('表 C.63 未给出所选单体电容类型的表面贴装系数 πch，式（C.14）不能完整计算')
    if model == '国产纸和薄膜电容器' and kind in ('纸-膜复合介质', '纸介，金属化纸介'):
        raise ValueError('表 204 未给出纸介、金属化纸介及纸-膜复合介质电容器的 πch，式（55）无法完整计算')
    if (
        model == '国产电容网络'
        and parameters.get('capacitor_model') == '国产纸和薄膜电容器'
        and kind in ('纸-膜复合介质', '纸介，金属化纸介')
    ):
        raise ValueError('表 204 未给出纸介、金属化纸介及纸-膜复合介质电容器的 πch，式（64）无法完整计算')
    if model in ('国产电容器（非工作详细）', '国产电容网络（非工作详细）'):
        if kind == '纸和薄膜电容器 / 纸-膜复合介质':
            raise ValueError('表 B.52 未给出纸-膜复合介质电容器的非工作基本失效率')
        if kind == '纸和薄膜电容器 / 纸介':
            raise ValueError('表 B.56 未给出纸介电容器的非工作表面贴装系数')
    if model in ('国产普通电动机和风机', '国产自整角机和旋转变压器') and parameters.get('maintenance') == '不满足维护要求':
        raise ValueError('电机模型要求在使用期内定期检修、维护和更换电刷')


def validate_parameters(model, parameters, *, allow_incomplete=False, include_project=False, stress_variables=None):
    if model not in MODELS or not isinstance(parameters, dict):
        raise ValueError('未知器件类型或参数格式无效')
    validate_stress_variables(stress_variables)
    if stress_variables and not stress_inputs(model):
        raise ValueError('当前器件类型没有可引用变量的电应力比输入')
    fields = [f for f in MODELS[model]['fields'] if include_project or f.scope == 'device']
    _validate_fields(fields, parameters, allow_incomplete=allow_incomplete, stress_variables=stress_variables)
    if not allow_incomplete:
        _validate_calculable_options(model, parameters)


def _require_finite_number(field, value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field.label}须为有限数值')
    try:
        finite_ok = math.isfinite(value)
    except OverflowError as error:
        raise ValueError(f'{field.label}超出有限数值范围') from error
    if not finite_ok:
        raise ValueError(f'{field.label}须为有限数值')


def _format_choice_sample(choices):
    items = [number_text(c) for c in choices]
    if len(items) > 10:
        return '、'.join(items[:6]) + '、…、' + '、'.join(items[-2:])
    return '、'.join(items)


def _check_numeric_limits(field, value, context):
    min_v, max_v, min_inc, max_inc, hint = field.active_limits(context)
    suffix = f'（{hint}）' if hint else ''
    if min_v is not None and (value < min_v if min_inc else value <= min_v):
        op = '不得小于' if min_inc else '须大于'
        raise ValueError(f'{field.label}{op} {number_text(min_v)}{suffix}')
    if max_v is not None and (value > max_v if max_inc else value >= max_v):
        op = '不得大于' if max_inc else '须小于'
        raise ValueError(f'{field.label}{op} {number_text(max_v)}{suffix}')


def _is_celsius_temperature_field(field):
    if field.key == 'temperature_C' or field.key.endswith('_temperature_C'):
        return True
    return '温度' in field.label and '温度/' not in field.label and '比' not in field.label


def _check_numeric_bounds(field, value, context, *, check_limits=True):
    if field.allow_negative and _is_celsius_temperature_field(field) and value < -273.15:
        raise ValueError(f'{field.label}不得低于绝对零度（-273.15 ℃）')
    if not field.allow_negative and (value < 0 or (field.positive and value == 0)):
        raise ValueError(f'{field.label}须为' + ('正数' if field.positive else '非负数'))
    if field.integer and int(value) != value:
        raise ValueError(f'{field.label}须为整数')
    if check_limits:
        _check_numeric_limits(field, value, context)


def _matches_inequality_choice(choice_item, value) -> bool:
    if not isinstance(choice_item, str):
        return False
    expr = re.sub(r'\s+', '', choice_item).replace('＜', '<').replace('＞', '>')
    match = re.fullmatch(r'(≤|≥|<|>)(\d+(?:\.\d+)?)', expr)
    if not match:
        return False
    op, bound_text = match.groups()
    x, bound = Decimal(str(value)), Decimal(bound_text)
    return {'≤': x <= bound, '≥': x >= bound, '<': x < bound, '>': x > bound}[op]


def _validate_fields(fields, parameters, *, allow_incomplete=False, stress_variables=None):
    if not isinstance(parameters, dict):
        raise ValueError('参数须为具名字段对象')
    keys = {f.key for f in fields}
    if set(parameters) - keys:
        raise ValueError('器件参数包含不适用的字段')
    context = {f.key: f.default for f in fields if f.default is not None}
    context.update(parameters)
    required = {f.key for f in fields if f.active(context) and f.default is None}
    if not allow_incomplete and required - set(parameters):
        missing = '、'.join(f.label for f in fields if f.key not in parameters and f.active(context))
        raise ValueError('待填写：' + missing)
    for field in fields:
        if field.key not in parameters:
            continue
        is_active = field.active(context)
        field_incomplete = allow_incomplete or not is_active
        value = parameters[field.key]
        if field.record_fields:
            min_records = 0 if field_incomplete else 1
            if not isinstance(value, list) or not min_records <= len(value) <= 1000:
                raise ValueError(f'{field.label}须包含 1～1000 条记录')
            for index, record in enumerate(value, 1):
                if not isinstance(record, dict) or not record:
                    raise ValueError(f'{field.label}第 {index} 条：记录不能为空')
                try:
                    _validate_fields(
                        field.record_fields,
                        record,
                        allow_incomplete=field_incomplete,
                        stress_variables=stress_variables,
                    )
                except (ValueError, TypeError, OverflowError) as error:
                    raise ValueError(f'{field.label}第 {index} 条：{error}') from error
        elif field.stress_max is not None:
            _min_v, limit_max, _min_inc, _max_inc, hint = (
                field.active_limits(context) if not field_incomplete else (None, None, True, True, '')
            )
            effective_max = limit_max if limit_max is not None else field.stress_max
            try:
                validate_stress(value, effective_max, field.label, stress_variables)
            except MissingStressVariable:
                if not field_incomplete:
                    raise
            except ValueError as error:
                if limit_max is not None and hint and '超出标准上限' in str(error) and hint not in str(error):
                    raise ValueError(f'{error}（{hint}）') from error
                raise
        elif field.choices is not None and field.key in TEMP_CHOICE_KEYS:
            _require_finite_number(field, value)
            if value < -273.15:
                raise ValueError(f'{field.label}不得低于绝对零度（-273.15 ℃）')
            active = field.resolve_choices(context) if not field_incomplete else None
            pool = active if active is not None else field.choices
            numeric_temps = [c for c in pool if not isinstance(c, bool) and isinstance(c, (int, float))]
            max_temp = max(numeric_temps)
            if value > max_temp:
                raise ValueError(f'{field.label}超出温度表上限（最高 {number_text(max_temp)} ℃）')
            if field.integer and int(value) != value:
                raise ValueError(f'{field.label}须为整数')
            if not field_incomplete:
                _check_numeric_limits(field, value, context)
        elif field.choices is not None and field.ceil_choices:
            active = field.resolve_choices(context) if not field_incomplete else None
            pool = active if active is not None else field.choices
            if isinstance(value, str):
                if value in pool:
                    continue
                if not field_incomplete and value in field.choices:
                    sample = _format_choice_sample(pool)
                    raise ValueError(f'{field.label}在当前条件下仅可选：{sample}')
                sample = _format_choice_sample(pool)
                raise ValueError(f'请选择有效的{field.label}（可选：{sample}）')
            _require_finite_number(field, value)
            _check_numeric_bounds(field, value, context, check_limits=not field_incomplete)
            if not field_incomplete:
                numeric_choices = [c for c in pool if not isinstance(c, bool) and isinstance(c, (int, float))]
                has_overflow = any(isinstance(c, str) and ('＞' in c or '>' in c or '≥' in c) for c in pool)
                if numeric_choices and not has_overflow:
                    table_max = max(numeric_choices)
                    if value > table_max:
                        raise ValueError(f'{field.label}超出标准表上限（最高 {number_text(table_max)}）')
        elif field.choices is not None:
            active = field.resolve_choices(context) if not field_incomplete else None
            pool = active if active is not None else field.choices
            if not isinstance(value, bool) and value in field.choices:
                if active is not None and value not in active:
                    sample = _format_choice_sample(active)
                    hint = f'（{field.constraint_hint}）' if field.constraint_hint else ''
                    raise ValueError(f'{field.label}在当前条件下仅可选：{sample}{hint}')
                if field.integer and not isinstance(value, str) and int(value) != value:
                    raise ValueError(f'{field.label}须为整数')
            elif (
                not isinstance(value, bool)
                and isinstance(value, (int, float))
                and any(isinstance(c, str) for c in pool)
            ):
                _require_finite_number(field, value)
                _check_numeric_bounds(field, value, context, check_limits=not field_incomplete)
                if not any(_matches_inequality_choice(c, value) for c in pool):
                    sample = _format_choice_sample(pool)
                    raise ValueError(f'请选择有效的{field.label}（可选：{sample}）')
            else:
                sample = _format_choice_sample(field.choices)
                raise ValueError(f'请选择有效的{field.label}（可选：{sample}）')
        else:
            _require_finite_number(field, value)
            _check_numeric_bounds(field, value, context, check_limits=not field_incomplete)


def _fill_field_defaults(fields, target):
    for field in fields:
        if field.default is not None and field.key not in target:
            target[field.key] = field.default
        elif field.record_fields and isinstance(target.get(field.key), list):
            for record in target[field.key]:
                if isinstance(record, dict):
                    _fill_field_defaults(field.record_fields, record)


@lru_cache(maxsize=4096)
def _evaluate(model, encoded, environment, ambient_temperature_C, encoded_variables):
    if model not in MODELS:
        raise ValueError('未知器件类型')
    p = json.loads(encoded)
    _fill_field_defaults(MODELS[model].fields, p)
    variables = json.loads(encoded_variables)
    validate_parameters(model, p, stress_variables=variables)
    if model == '待配置':
        return Evaluation(None, error='尚未选择器件类型并填写参数')
    if model == '工程模块':
        return Evaluation(None, error='请从工程引入模块，按来源工程快照计算')
    if environment not in ENVIRONMENTS:
        return Evaluation(None, error='请先选择工程环境类别')
    if ambient_temperature_C is None:
        return Evaluation(None, error='请在工程顶部统一选择环境温度')
    if isinstance(ambient_temperature_C, bool) or not isinstance(ambient_temperature_C, (int, float)):
        raise ValueError('工程环境温度必须是有效的摄氏温度')
    try:
        ambient_finite = math.isfinite(ambient_temperature_C)
    except OverflowError as error:
        raise ValueError('工程环境温度必须是有效的摄氏温度') from error
    if not ambient_finite or ambient_temperature_C < -273.15:
        raise ValueError('工程环境温度必须是有效的摄氏温度')
    if any(f.scope == 'project' for f in MODELS[model]['fields']):
        p['temperature_C'] = ambient_temperature_C
        validate_parameters(model, p, include_project=True, stress_variables=variables)
    if model == '已知单件失效率':
        return Evaluation(None, error='请按工程工况录入已知单件失效率')
    adjustments = []
    for field in _top_stress_inputs(model):
        if not field.active(p):
            continue
        supplied = p[field.key]
        _min_v, limit_max, _min_inc, _max_inc, _hint = field.active_limits(p)
        effective_max = limit_max if limit_max is not None else field.stress_max
        active_grid = field.resolve_choices(p) or field.choices
        exact = validate_stress(supplied, effective_max, field.label, variables)
        selected = ceil_to_grid(exact, active_grid, field.label)
        adjustments.append(StressAdjustment(field.key, field.label, trace_number(exact), selected,
                                            _stress_source(model, p, field.key),
                                            supplied if isinstance(supplied, str) else ''))
        p[field.key] = selected
    calculator = MODELS[model].calculator
    if calculator is None:
        raise ValueError(f'模型 {model} 缺少计算器定义')
    result = calculator(p, environment, ambient_temperature_C)
    if result.rate is not None and (not math.isfinite(result.rate) or result.rate < 0):
        raise ValueError('工作失效率必须为有限非负数')
    return replace(result, stress_adjustments=tuple(adjustments) + result.stress_adjustments)


def evaluate(model, parameters, environment, *, ambient_temperature_C=None, stress_variables=None):
    try:
        return _evaluate(model, json.dumps(parameters, sort_keys=True, allow_nan=False), environment,
                         ambient_temperature_C,
                         json.dumps({} if stress_variables is None else stress_variables,
                                    sort_keys=True, allow_nan=False))
    except OverflowError:
        return Evaluation(None, error='计算结果超出有限数值范围，请核对输入参数')
    except (ValueError, TypeError) as error:
        return Evaluation(None, error=str(error))


def parameter_text(model, parameters):
    from .parameter_display import parameter_lines

    return '；'.join(parameter_lines(MODELS[model].fields, parameters, detailed=True,
                                    include_missing=True, assignment=' = '))


def _has_table(source: str, *table_numbers: str) -> bool:
    pattern = '|'.join(re.escape(num) for num in table_numbers)
    return bool(re.search(rf'表\s*(?:{pattern})(?!\d)', source))


def _base_factor_symbol(symbol: str) -> str:
    base = symbol.removeprefix('模拟部分 ').removeprefix('数字部分 ')
    base = re.sub(r'^第\s*\d+\s*管\s+', '', base)
    base = re.sub(r'\[(?:模拟|数字)\d+\]$', '', base)
    return base


def _factor_title(symbol, source):
    base = _base_factor_symbol(symbol)
    if base == 'C2' and _has_table(source, 'C.13'):
        return '封装复杂度失效率'
    if base == 'πS' and _has_table(source, 'C.48', 'C.79', 'C.85'):
        return '电应力系数'
    if base == 'πL':
        if _has_table(source, '285'):
            return '触点负载系数'
        if _has_table(source, '351'):
            return '连接系数'
    if base == 'πNL' and _has_table(source, 'B.117'):
        return '非工作连接系数'
    if base == 'πr':
        if _has_table(source, '53', '106'):
            return '额定电流系数'
        if _has_table(source, '269'):
            return '额定负载系数'
    if base == 'πC':
        if _has_table(source, '307', '343') or '单面板的' in source:
            return '复杂度系数'
        if _has_table(source, '322'):
            return '控制系数'
        if _has_table(source, '294'):
            return '插孔结构系数'
        if _has_table(source, '316', 'B.91'):
            return '元器件引线结构系数'
    if base == 'πCYC':
        if _has_table(source, '270'):
            return '动作速率系数'
        if _has_table(source, '286'):
            return '开关速率系数'
    if base == 'πP':
        if _has_table(source, '33', 'C.18'):
            return '工艺系数'
        if _has_table(source, '292', 'C.90'):
            return '接触件系数'
        if _has_table(source, '273', '277'):
            return '封装系数'
    if base == 'πV' and (_has_table(source, '338') or '式(88)' in source):
        return '额定电压系数'
    if base == 'A' and _has_table(source, 'B.12'):
        return '修正系数'
    if base == 'N':
        if '电容' in source:
            return '电容器个数'
        if '电阻' in source:
            return '网络电阻数'
    return FACTOR_TITLES.get(base, FACTOR_TITLES.get(symbol, ''))


def strip_cell_ids(text):
    return _CELL_ID_PATTERN.sub('', text)


def factor_text(result):
    lines = []
    for adjustment in result.stress_adjustments:
        raw, selected = number_text(adjustment.raw), number_text(adjustment.selected)
        line = (f'{adjustment.label}：{adjustment.input_text} [比值 {raw}，档位 {selected}]'
                if adjustment.input_text else f'{adjustment.label}：{raw} [档位 {selected}]')
        lines.append(line + '；' + adjustment.source)
    for symbol, value, raw_source in result.factors:
        title = _factor_title(symbol, raw_source)
        display_source = strip_cell_ids(raw_source)
        lines.append(f'{title} {symbol} = {number_text(value)}  ←  {display_source}'.strip())
    return '\n'.join(lines)


def _linkify_source(raw_text):
    all_cells = _CELL_ID_PATTERN.findall(raw_text)
    single_fallback = all_cells[0] if len(all_cells) == 1 else ''
    parts, cursor = [], 0
    for match in _SOURCE_LINK_PATTERN.finditer(raw_text):
        parts.append(html.escape(strip_cell_ids(raw_text[cursor:match.start()])))
        number = match.group('tnum')
        adj = re.match(r'；(p\d+-[a-z]\d+-r\d+-c\d+)(?=；|$)', raw_text[match.end():])
        cell_id = adj.group(1) if adj else single_fallback
        suffix = f'/{cell_id}' if cell_id else ''
        parts.append(f'<a href="standard://table/{html.escape(number + suffix, quote=True)}">'
                     f'{html.escape(match.group(0))}</a>')
        cursor = match.end() + (adj.end() if adj else 0)
    parts.append(html.escape(strip_cell_ids(raw_text[cursor:])))
    return ''.join(parts)


def factor_html(result, *, reference=''):
    """Format calculation factors and reference with clickable table links."""
    blocks = []
    for adjustment in result.stress_adjustments:
        raw, selected = number_text(adjustment.raw), number_text(adjustment.selected)
        line = (f'{adjustment.label}：{adjustment.input_text} [比值 {raw}，档位 {selected}]'
                if adjustment.input_text else f'{adjustment.label}：{raw} [档位 {selected}]')
        blocks.append('<p>' + html.escape(line + '；') + _linkify_source(adjustment.source) + '</p>')
    for symbol, value, raw_source in result.factors:
        title = _factor_title(symbol, raw_source)
        prefix = f'{title} {symbol} = {number_text(value)}'.strip()
        blocks.append(f'<p><b>{html.escape(prefix)}</b> &nbsp;←&nbsp; {_linkify_source(raw_source)}</p>')
    if not blocks and result.error:
        blocks.append(f'<p>{html.escape(result.error)}</p>')
    if reference:
        blocks.append(f'<hr><p>{_linkify_source(reference)}</p>')
    return ''.join(blocks)


def conditions_text(environment, ambient_temperature_C):
    temperature = f'{number_text(ambient_temperature_C)} ℃' if ambient_temperature_C is not None else '尚未选择'
    return environment_text(environment) + '　｜　环境温度：' + temperature
