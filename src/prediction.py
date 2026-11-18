"""Declared prediction methods and the physical state of each calculation."""

STATES = {'working': '工作状态', 'nonoperating': '非工作状态'}
METHODS = {
    'working_stress': '工作状态应力分析法',
    'working_count': '工作状态元器件计数法',
    'nonoperating_stress': '非工作状态详细预计法',
    'nonoperating_count': '非工作状态元器件计数法',
}


def validate_state(state):
    if not isinstance(state, str) or state not in STATES:
        raise ValueError('预计状态须为工作状态或非工作状态')
    return state


def method_state(method):
    if method not in METHODS:
        raise ValueError('预计方法无效')
    return 'nonoperating' if method.startswith('nonoperating_') else 'working'


def model_state(model):
    from .standards import MODELS
    return method_state(MODELS[model].method)
