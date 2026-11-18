"""Appendix E: exposure uses component-hours; public rates use 10⁻⁶/h."""

from decimal import Decimal, localcontext

from .common import RATE_UNIT, AnalysisResult, decimal, finite, required_text, shown


def bayesian_update(initial_rate, observations, *, prior_failures=.5):
    if prior_failures is None or (isinstance(prior_failures, str) and not prior_failures.strip()):
        prior_failures = 0.5
    initial = decimal(initial_rate, '初始预计失效率 λ1', positive=True)
    prior = decimal(prior_failures, '初始有效失效数 a0')
    if not isinstance(observations, (list, tuple)) or not observations or len(observations) > 10000:
        raise ValueError('请填写至少一组测试或现场使用数据（最多 10000 组）')
    rows = []
    for index, row in enumerate(observations, 1):
        if not isinstance(row, dict):
            raise ValueError('测试或现场使用数据格式无效')
        source = required_text(row.get('source'), f'第 {index} 组数据来源')
        failures = decimal(row.get('failures'), f'第 {index} 组失效数')
        if failures != failures.to_integral_value():
            raise ValueError('观测失效数必须为非负整数；先验有效失效数可以为小数')
        hours = decimal(row.get('component_hours'), f'第 {index} 组累积元器件小时数', positive=True)
        raw_af = row.get('acceleration_factor', 1)
        if raw_af is None or (isinstance(raw_af, str) and not raw_af.strip()):
            raw_af = 1
        factor = decimal(raw_af, f'第 {index} 组加速因子', positive=True)
        rows.append((source, failures, hours, factor))
    with localcontext() as context:
        context.prec = 80
        initial_hourly = initial / Decimal(1000000)
        prior_hours = prior / initial_hourly
        failures = prior + sum(row[1] for row in rows)
        exposure = sum(row[2] * row[3] for row in rows)
        posterior = failures / (prior_hours + exposure) * Decimal(1000000)
        details = tuple({'source': source, 'failures': finite(count, '失效数'),
                         'component_hours': finite(hours, '元器件小时数'),
                         'acceleration_factor': finite(factor, '加速因子'),
                         'equivalent_component_hours': finite(hours * factor, '非加速等效小时数')}
                        for source, count, hours, factor in rows)
        steps = [f'λ1 = {shown(initial)} × 10⁻⁶/h',
                 f'初始等效元器件小时数 = a0 / (λ1 × 10⁻⁶) = {shown(prior_hours)} h']
        for row in details:
            steps.append(f'{row["source"]}：b = {shown(row["component_hours"])} × {shown(row["acceleration_factor"])} = {shown(row["equivalent_component_hours"])} h；失效数 {shown(row["failures"])}')
        steps.append(f'λ2 = ({shown(prior)} + {shown(failures - prior)}) / ({shown(prior_hours)} + {shown(exposure)}) × 10⁶ = {shown(posterior)}（{RATE_UNIT}）')
        notes = ['无法获知初始有效失效数时 a0 可取 0.5。加速因子及数据适用性需由试验或现场记录确定。']
        if any(row[3] < 1 for row in rows):
            notes.append('包含低于 1 的 AF，按所提供的适用因子换算；请确认因子定义及试验与使用条件。')
        return AnalysisResult('bayes', '预计失效率修正',
                              {'initial_rate': finite(initial, '初始率'), 'prior_failures': finite(prior, '先验有效失效数'),
                               'observations': list(details)}, tuple(steps),
                              {'rate': finite(posterior, '修正失效率'),
                               'prior_component_hours': finite(prior_hours, '初始等效元器件小时数'),
                               'observed_equivalent_hours': finite(exposure, '观测等效元器件小时数')},
                              {'rate': RATE_UNIT, 'prior_component_hours': 'h', 'observed_equivalent_hours': 'h'},
                              '附录 E，式（E.1）、（E.2）',
                              tuple(notes), details)
