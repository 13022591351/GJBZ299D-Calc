"""Export anonymous condition rates without retaining private engineering inputs."""

from .domain import Component, OperatingCondition, Project
from .parameter_disclosure import parameter_ranges, stress_bins


def build_redacted_project(project):
    """Preserve calculated rates exactly; public ranges are descriptive, never recalculated."""
    project.validate()
    if not project.components:
        raise ValueError('工程没有预计对象，无法导出脱敏工程')
    enabled = project.enabled_conditions
    if not enabled:
        raise ValueError('请至少勾选一种工作环境参与计算')
    contexts = [project.calculate(condition) for condition in enabled]
    for context in contexts:
        if context.error:
            raise ValueError(f'工况“{context.condition_name}”合计错误，不能导出脱敏工程：{context.error}')
    totals = [context.total_rate for context in contexts]
    if all(total is None for total in totals):
        raise ValueError('至少有一种已启用工作环境完整计算后，才能导出脱敏工程')
    original_results = [[context.evaluate(component) for component in project.components]
                        for context in contexts]
    # Anonymize condition labels to prevent leaking project/mission designations.
    public_conditions = [OperatingCondition(f'工况{index:03d}', condition.environment,
                                             condition.ambient_temperature_C)
                         for index, condition in enumerate(enabled, 1)]
    public = Project('GJB299D-001', notes='', conditions=public_conditions,
                     prediction_state=project.prediction_state)
    for index, source in enumerate(project.components):
        model = source.disclosure['model'] if source.disclosure else source.model
        bins = stress_bins(source, [results[index] for results in original_results])
        disclosure = {'model': model, 'parameter_ranges': parameter_ranges(source, [results[index] for results in original_results]),
                      'stress': '标准档位' if bins else '内部', 'stress_bins': bins}
        # Build from an explicit allow-list. Never copy a component or source snapshot here.
        component = Component(f'{index + 1:06d}', '已知单件失效率', source.quantity, {},
                              domestic=source.is_domestic, disclosure=disclosure,
                              rate_state=project.prediction_state)
        for condition, results in zip(public_conditions, original_results, strict=True):
            rate = results[index].rate
            if rate is not None:
                component.set_condition_rate(condition.environment, condition.ambient_temperature_C, rate)
        public.components.append(component)
    public.validate()
    for condition, expected_results, expected_total in zip(public_conditions, original_results, totals, strict=True):
        context = public.calculate(condition)
        actual = [context.evaluate(component) for component in public.components]
        for source, target, component in zip(expected_results, actual, public.components, strict=True):
            if source.rate != target.rate or source.weighted(component.quantity) != target.weighted(component.quantity):
                raise ValueError('脱敏前后预计项失效率不一致，本次未导出')
        if context.total_rate != expected_total:
            raise ValueError('脱敏前后工程失效率不一致，本次未导出')
    return public


