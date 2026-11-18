"""Recompute saved analysis inputs for reports without importing Qt."""

import copy
import math

from ..numeric import number_text
from ..standards import ENVIRONMENTS
from .bayes import bayesian_update
from .common import decimal
from .failure_modes import mode_contributions
from .mission import STATES, mission_profile
from .physics import physics_calculate
from .state import validate_analysis_state

_INCOMPLETE_ERROR_PREFIXES = (
    '待填写：',
    '尚未选择',
    '应力表达式待填写变量：',
    '组成工程的对应工况未完整计算：',
)
_INCOMPLETE_EXACT_ERRORS = frozenset({
    '元器件数量待填写',
    '当前环境类别和温度未录入适用失效率，请补充或重新确认',
    '请按工程工况录入已知单件失效率',
    '该工作环境未启用计算',
    '请先填写有效的环境类别和环境温度',
    '请先选择工程环境类别',
    '请在工程顶部统一选择环境温度',
    '组成工程没有对应环境、温度且已启用的工况',
})


def is_component_config_error(model, result, *, calculation_enabled=True):
    """Return True when an enabled component fails due to invalid inputs or limits rather than missing fields."""
    if not calculation_enabled or model == '待配置' or result.rate is not None or not result.error:
        return False
    error = result.error
    if (
        error in _INCOMPLETE_EXACT_ERRORS
        or error.startswith(_INCOMPLETE_ERROR_PREFIXES)
        or '：待填写：' in error
        or '组成工程没有对应环境、温度且已启用的工况' in error
        or '组成工程的对应工况未完整计算：' in error
        or '外贴微电路数量待填写' in error
    ):
        return False
    return True


def _matches_snapshot_number(phase_val, snap_val, label, *, nonnegative=True):
    entered = decimal(phase_val, label, nonnegative=nonnegative)
    exact = decimal(snap_val, '来源' + label, nonnegative=nonnegative)
    if entered == exact:
        return True
    if isinstance(snap_val, float):
        return entered == decimal(number_text(snap_val), '来源' + label, nonnegative=nonnegative)
    return False


def _model_for_state(component, project_state, state_key):
    if state_key == project_state:
        return component.model
    snap = component.profiles.get(state_key)
    if isinstance(snap, dict) and isinstance(snap.get('model'), str):
        return snap['model']
    return '待配置'


def _valid_condition_env_temp(condition):
    try:
        valid_temp = (
            type(condition.ambient_temperature_C) in (int, float)
            and math.isfinite(condition.ambient_temperature_C)
            and condition.ambient_temperature_C >= -273.15
        )
    except OverflowError:
        valid_temp = False
    return (
        isinstance(condition.environment, str)
        and condition.environment in ENVIRONMENTS
        and valid_temp
    )


def _condition_issue_summary(project, condition, state_key, context):
    if not condition.enabled:
        return '该工况已停用计算'
    if not project.components:
        return '工程暂无预计项'
    if not _valid_condition_env_temp(condition):
        return '工况环境或温度尚未配置有效值'
    if context.error:
        return f'合计错误：{context.error}'
    err_names = []
    inc_names = []
    for component in project.components:
        model_for_state = _model_for_state(component, project.prediction_state, state_key)
        res = context.evaluate(component)
        if res.rate is not None:
            continue
        if is_component_config_error(model_for_state, res, calculation_enabled=True):
            err_names.append(component.name)
        else:
            inc_names.append(component.name)
    parts = []
    if err_names:
        sample = '、'.join(err_names[:3]) + (' 等' if len(err_names) > 3 else '')
        parts.append(f'{len(err_names)} 项配置异常（{sample}）')
    if inc_names:
        sample = '、'.join(inc_names[:3]) + (' 等' if len(inc_names) > 3 else '')
        parts.append(f'{len(inc_names)} 项待配置（{sample}）')
    return '；'.join(parts) if parts else '计算未完成'


def _all_unconfigured_summary(components):
    names = [c.name for c in components[:3]]
    sample = '、'.join(names) + (' 等' if len(components) > 3 else '')
    return f'{len(components)} 项待配置（{sample}）'


def project_condition_source_diagnostics(project, *, active_results=None):
    """Collect calculable condition sources and explicit failure reasons in a single pass."""
    from ..calculation import calculate_condition

    cache_key = None
    if active_results is not None:
        cache_key = (
            project.name,
            project.prediction_state,
            tuple(
                (c.identifier, c.name, c.environment, c.ambient_temperature_C, c.enabled)
                for c in project.conditions
            ),
            tuple(
                (
                    c.identifier,
                    c.name,
                    c.model,
                    c.quantity,
                    id(c.profiles),
                    len(c.profiles),
                )
                for c in project.components
            ),
            tuple((cid, id(res)) for cid, res in active_results.items()),
        )
        cached = project.__dict__.get('_condition_sources_cache')
        if cached is not None and cached[0] == cache_key:
            return copy.deepcopy(cached[1]), dict(cached[2])

    sources = []
    issues = {}
    for state_key in ('working', 'nonoperating'):
        is_active_state = state_key == project.prediction_state
        state_items = None
        all_unconfigured = False
        if not is_active_state and project.components:
            if all(_model_for_state(c, project.prediction_state, state_key) == '待配置' for c in project.components):
                all_unconfigured = True
            else:
                state_items = [c.component_for_state(state_key) for c in project.components]

        for condition in project.conditions:
            if not condition.enabled:
                issues[(condition.identifier, state_key)] = '该工况已停用计算'
                continue
            if not project.components:
                issues[(condition.identifier, state_key)] = '工程暂无预计项'
                continue
            if not _valid_condition_env_temp(condition):
                issues[(condition.identifier, state_key)] = '工况环境或温度尚未配置有效值'
                continue
            if all_unconfigured:
                issues[(condition.identifier, state_key)] = _all_unconfigured_summary(project.components)
                continue
            if is_active_state and active_results is not None and condition.identifier in active_results:
                context = active_results[condition.identifier]
            elif is_active_state:
                context = calculate_condition(project.components, condition, prediction_state=state_key)
            else:
                context = calculate_condition(state_items, condition, prediction_state=state_key)

            if not context.error and context.total_rate is not None:
                sources.append({
                    'id': condition.identifier,
                    'name': condition.name,
                    'project_name': project.name,
                    'environment': condition.environment,
                    'ambient_temperature_C': condition.ambient_temperature_C,
                    'prediction_state': state_key,
                    'rate': context.total_rate,
                    'scope': 'current',
                })
            else:
                issues[(condition.identifier, state_key)] = _condition_issue_summary(
                    project, condition, state_key, context
                )

    if cache_key is not None:
        project.__dict__['_condition_sources_cache'] = (
            cache_key,
            copy.deepcopy(sources),
            dict(issues),
        )
    return sources, issues


def project_condition_sources(project, *, active_results=None):
    """Collect calculable working and nonoperating condition rates without mutating project."""
    sources, _issues = project_condition_source_diagnostics(project, active_results=active_results)
    return sources


def resolve_phase_sources(phases, current_sources=None, *, source_issues=None):
    result = copy.deepcopy(phases)
    by_state = {}
    by_id = {}
    for source in current_sources or []:
        by_state[(source.get('id'), source.get('prediction_state'))] = source
        by_id.setdefault(source.get('id'), []).append(source)
    for phase in result:
        snapshot = phase.get('source_snapshot')
        if not snapshot:
            continue
        phase_state = phase.get('state')
        if snapshot.get('scope') == 'current':
            try:
                env_temp_matches = (
                    phase.get('environment') == snapshot['environment']
                    and _matches_snapshot_number(
                        phase.get('ambient_temperature_C'),
                        snapshot['ambient_temperature_C'],
                        '温度',
                        nonnegative=False,
                    )
                )
                rate_val = phase.get('rate')
                rate_matches = (
                    rate_val in (None, '')
                    or phase_state != snapshot['prediction_state']
                    or _matches_snapshot_number(rate_val, snapshot['rate'], '失效率')
                )
            except ValueError:
                env_temp_matches = False
                rate_matches = False
            if not (env_temp_matches and rate_matches):
                raise ValueError('来源阶段的状态、环境或失效率已修改；请重新添加手工阶段或重新引入来源工况')
            source = by_state.get((snapshot.get('id'), phase_state))
            if source is None:
                cond_name = snapshot.get('name') or phase.get('name') or '对应工况'
                state_label = STATES.get(phase_state, phase_state)
                issue = (source_issues or {}).get((snapshot.get('id'), phase_state))
                if issue:
                    raise ValueError(
                        f'当前工程工况“{cond_name}”的{state_label}状态暂不可计算：{issue}'
                    )
                candidates = by_id.get(snapshot.get('id'), [])
                if candidates and all(c.get('prediction_state') != phase_state for c in candidates):
                    raise ValueError(
                        f'当前工程工况“{cond_name}”的{state_label}状态尚未完成计算'
                    )
                if (
                    source_issues is not None
                    and not candidates
                    and all((snapshot.get('id'), state_key) not in source_issues for state_key in STATES)
                ):
                    raise ValueError(f'任务阶段引用的当前工程工况“{cond_name}”已从工程中删除')
                raise ValueError(
                    f'任务阶段引用的当前工程工况“{cond_name}”（{state_label}）已删除、停用或计算未完成'
                )
            if source.get('rate') is None:
                raise ValueError('任务阶段引用的当前工程工况已删除、停用或计算未完成')
            phase.update(
                rate=source['rate'],
                environment=source['environment'],
                ambient_temperature_C=source['ambient_temperature_C'],
                source=source.get('project_name', '当前工程') + ' / ' + source['name'],
                source_state=source['prediction_state'],
            )
        else:
            matches = (
                phase_state == snapshot['prediction_state']
                and phase.get('environment') == snapshot['environment']
                and _matches_snapshot_number(phase.get('rate'), snapshot['rate'], '失效率')
                and _matches_snapshot_number(
                    phase.get('ambient_temperature_C'),
                    snapshot['ambient_temperature_C'],
                    '温度',
                    nonnegative=False,
                )
            )
            if not matches:
                raise ValueError('来源阶段的状态、环境或失效率已修改；请重新添加手工阶段或重新引入来源工况')
            phase['rate'] = snapshot['rate']
            phase['source_state'] = snapshot['prediction_state']
    return result



def configured_analyses(state):
    """Unused default tabs are drafts, not failed calculations to put in reports."""
    selected = set()
    mission = state.get('mission', {})
    if any(any(row.get(key) not in (None, '') for key in ('duration_hours', 'duty_cycle', 'rate', 'environment', 'ambient_temperature_C'))
           for row in mission.get('phases', [])):
        selected.add('mission')
    bayes = state.get('bayes', {})
    if bayes.get('initial_rate') not in (None, '') or any(
            any(row.get(key) not in (None, '') for key in ('source', 'failures', 'component_hours'))
            for row in bayes.get('observations', [])):
        selected.add('bayes')
    if state.get('failure_modes', {}).get('rate') not in (None, ''):
        selected.add('failure_modes')
    if any(value not in (None, '') for value in state.get('physics', {}).get('parameters', {}).values()):
        selected.add('physics')
    return selected

def recompute_analyses(state, *, current_sources=None, source_issues=None):
    """Return valid results and explicit pending reasons; ignore no saved outputs."""
    validate_analysis_state(state)
    results, errors = [], {}
    if not state:
        return results, errors
    methods = {
        'mission': lambda value: mission_profile(
            resolve_phase_sources(
                value.get('phases', []), current_sources, source_issues=source_issues
            ),
            basis=value.get('basis', 'duration'),
        ),
        'bayes': lambda value: bayesian_update(value.get('initial_rate'), value.get('observations', []),
                                               prior_failures=value.get('prior_failures', .5)),
        'failure_modes': lambda value: mode_contributions(value.get('rate'), value.get('table'), value.get('category')),
        'physics': lambda value: physics_calculate(value.get('model_id'), value.get('parameters', {})),
    }
    configured = configured_analyses(state)
    for key, calculator in methods.items():
        if key not in configured:
            continue
        try:
            results.append(calculator(state[key]))
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            errors[key] = str(error)
    return results, errors
