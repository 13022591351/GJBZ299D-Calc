"""Appendix A.3.1: explicitly weighted working and nonoperating phases."""

from decimal import Decimal, localcontext

from .common import RATE_UNIT, AnalysisResult, decimal, finite, required_text, shown

STATES = {'working': '工作', 'nonoperating': '非工作'}


def mission_profile(phases, *, basis='duration'):
    if basis not in ('duration', 'duty'):
        raise ValueError('任务剖面权重应选择阶段时长或占空比')
    if not isinstance(phases, (list, tuple)) or not phases or len(phases) > 10000:
        raise ValueError('任务剖面应包含 1～10000 个明确阶段')
    key = 'duration_hours' if basis == 'duration' else 'duty_cycle'
    rows = []
    for index, phase in enumerate(phases, 1):
        if not isinstance(phase, dict):
            raise ValueError(f'第 {index} 阶段格式无效')
        name = required_text(phase.get('name'), f'第 {index} 阶段名称')
        state = phase.get('state')
        if state not in STATES:
            raise ValueError(f'{name}必须明确工作或非工作状态')
        amount = decimal(phase.get(key), f'{name}的时长或占空比', positive=True)
        if basis == 'duty' and amount > 1:
            raise ValueError('各阶段占空比必须在 0～1 内，不使用百分数')
        rate = decimal(phase.get('rate'), f'{name}失效率')
        source = required_text(phase.get('source'), f'{name}失效率来源')
        environment = required_text(phase.get('environment'), f'{name}环境条件')
        temp = decimal(phase.get('ambient_temperature_C'), f'{name}温度', nonnegative=False)
        if temp < Decimal('-273.15'):
            raise ValueError('阶段温度不得低于绝对零度')
        # Source snapshots carry a state to prevent a working result being reused as storage loss.
        if phase.get('source_state', state) != state:
            raise ValueError(f'{name}的阶段状态与来源预计状态不一致')
        rows.append((name, state, amount, rate, source, environment, temp))
    with localcontext() as context:
        context.prec = 80
        total = sum(row[2] for row in rows)
        if basis == 'duty' and total != 1:
            raise ValueError(f'工作与非工作阶段占空比之和必须为 1，当前为 {total}')
        details, weighted, working = [], Decimal(0), Decimal(0)
        steps = ['λ全寿命 = Σ(DC工作 × λ工作) + Σ(DC非工作 × λ非工作)']
        for name, state, amount, rate, source, env, temp in rows:
            duty = amount / total
            contribution = duty * rate
            weighted += contribution
            working += duty if state == 'working' else 0
            details.append({'name': name, 'state': state, key: finite(amount, '权重'),
                            'duty_cycle': finite(duty, '占空比'), 'rate': finite(rate, '阶段失效率'),
                            'weighted_rate': finite(contribution, '阶段加权失效率'), 'source': source,
                            'environment': env, 'ambient_temperature_C': finite(temp, '阶段温度')})
            steps.append(f'{name} [{STATES[state]}；{env}；{shown(temp)}℃]：{shown(duty)} × {shown(rate)} = {shown(contribution)}（{RATE_UNIT}）')
        rate = finite(weighted, '全寿命失效率')
        outputs = {'rate': rate, 'working_duty_cycle': finite(sum(row[2] for row in rows if row[1] == 'working') / total, '工作占空比'),
                   'nonoperating_duty_cycle': finite(sum(row[2] for row in rows if row[1] == 'nonoperating') / total, '非工作占空比')}
        units = {'rate': RATE_UNIT, 'working_duty_cycle': '1', 'nonoperating_duty_cycle': '1'}
        if basis == 'duration':
            outputs['total_duration_hours'] = finite(total, '任务剖面总时长')
            units['total_duration_hours'] = 'h'
        steps.append(f'λ全寿命 = {shown(weighted)}（{RATE_UNIT}）')
        return AnalysisResult('mission', '全寿命状态可靠性预计', {'basis': basis, 'phases': details},
                              tuple(steps), outputs, units, '附录 A.3.1，式（A.1）',
                              ('阶段权重来自任务剖面各阶段时长或占空比。',), tuple(details))
