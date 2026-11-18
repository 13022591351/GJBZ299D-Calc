"""Concise report values and arithmetic traces from existing 299D evaluations."""

import ast
import math
import operator
import re
import unicodedata

from .nominal_display import standard_nominal_text
from .numeric import number_text
from .parameter_display import input_text, nominal_value
from .prediction import model_state
from .semiconductor import fet_conditions
from .standards import MODELS, model_name
from .stress_display import adjustment_text

NUMBER = re.compile(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?')


def _arithmetic_value(expression):
    """Evaluate only an already tokenized arithmetic expression, never Python code."""
    def visit(node):
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            return node.value
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand)
            return value if isinstance(node.op, ast.UAdd) else -value
        if isinstance(node, ast.BinOp):
            operations = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                          ast.Div: operator.truediv, ast.Pow: operator.pow}
            if type(node.op) in operations:
                left, right = visit(node.left), visit(node.right)
                if isinstance(node.op, ast.Pow) and (abs(right) > 20 or abs(left) > 1e30):
                    raise ValueError('报告算式的幂运算范围无效')
                return operations[type(node.op)](left, right)
        raise ValueError('报告算式不是完整的数值运算')

    return visit(ast.parse(expression, mode='eval').body)


def numerical_substitution(formula, factors, rate):
    """Tokenize symbols before substitution so C1πT cannot become two joined digits.

    Multi-branch, max/sum notation and formulas with incomplete factor traces
    deliberately return None. A numerical line is shown only if its independent
    arithmetic agrees with the recorded result.
    """
    if formula.count('=') != 1 or len({symbol for symbol, _, _ in factors}) != len(factors):
        return None
    expression = formula.split('=', 1)[1].strip()
    values = {symbol: value for symbol, value, _ in factors}
    symbols = sorted(values, key=len, reverse=True)
    tokens, position = [], 0
    while position < len(expression):
        character = expression[position]
        if character.isspace():
            position += 1
            continue
        symbol = next((symbol for symbol in symbols if expression.startswith(symbol, position)), None)
        if symbol:
            tokens.append(('value', number_text(values[symbol])))
            position += len(symbol)
        elif match := NUMBER.match(expression, position):
            tokens.append(('number', match.group()))
            position = match.end()
        elif character in '([)]×*+−-/^':
            tokens.append(('open' if character in '([' else 'close' if character in ')]' else 'operator', character))
            position += 1
        else:
            return None
    rendered, previous = [], None
    for kind, token in tokens:
        if previous in ('value', 'number', 'close') and kind in ('value', 'number', 'open'):
            # Adjacent numeric literals are not a defined implicit product.
            if previous == 'number' and kind == 'number':
                return None
            rendered.append('×')
        rendered.append(token)
        previous = kind
    shown = ' '.join(rendered)
    python_expression = shown.replace('×', '*').replace('−', '-').replace('^', '**').replace('[', '(').replace(']', ')')
    try:
        calculated = _arithmetic_value(python_expression)
        if not math.isfinite(calculated) or not math.isclose(calculated, rate, rel_tol=5e-13, abs_tol=0):
            return None
    except (ValueError, SyntaxError, TypeError, ZeroDivisionError, OverflowError):
        return None
    return shown


def _factor_lines(result, *, separate=False):
    values = [f'{symbol}={number_text(value)}' for symbol, value, _source in result.factors]
    return ['计算项：', *values] if separate and values else ['；'.join(values)] if values else []


def _smt_process(result, *, nonoperating=False):
    board_symbol = 'λNSMT' if nonoperating else 'λSMT'
    candidates = [(symbol, value) for symbol, value, _ in result.factors
                  if re.fullmatch(rf'连接类别 \d+ {board_symbol}', symbol)]
    if not candidates:
        return _factor_lines(result, separate=True)
    lines = [f'{symbol}={number_text(value)}' for symbol, value in candidates]
    maximum = next((int(value) for symbol, value, _ in result.factors if symbol == '最薄弱连接序号'), None)
    if maximum is not None:
        lines.append(f'最薄弱连接：[第 {maximum} 类]')
    lines.append('最薄弱连接计算项：')
    lines.extend(f'{symbol}={number_text(value)}' for symbol, value, _ in result.factors
                 if not symbol.startswith('连接类别 ') and symbol not in ('最薄弱连接序号', '连接类别数'))
    lines.append(f'{board_symbol}[整板] = max(' + ', '.join(number_text(value) for _, value in candidates)
                 + ') = ' + number_text(result.rate))
    return lines


def calculation_process(component, result, ambient_temperature_C, *, prediction_state=None):
    """Show the evaluated formula and coefficients without copying source references."""
    model = component.model
    state = prediction_state or (component.rate_state if model in ('工程模块', '已知单件失效率') else model_state(model))
    symbol = 'λNP' if state == 'nonoperating' else 'λp'
    state_label = '非工作' if state == 'nonoperating' else '工作'
    if component.disclosure:
        lines = [f'{symbol} = 原工况预计结果', '公开参数档位仅用于展示']
    elif model == '工程模块':
        lines = ['λ模块 = Σ(N内部 × λ内部)']
    elif model == '已知单件失效率':
        lines = [f'{symbol} = 本工况提供的{state_label}失效率']
    else:
        lines = [MODELS[model]['formula']]
    if result.rate is None:
        return '\n'.join([*lines, *_factor_lines(result, separate=True), '尚未完成计算'])
    for adjustment in result.stress_adjustments:
        lines.append(f'{adjustment.label}：{adjustment_text(adjustment)}')
    if component.stress_variables:
        lines.append('变量：' + '；'.join(f'{key}={value}' for key, value in component.stress_variables.items()))
    if model == '国产硅场效应晶体管':
        parameters = component.parameters
        correction, temperature, stress = fet_conditions(parameters, ambient_temperature_C)
        derived_stress = next((item for item in result.stress_adjustments if item.key == 'stress_ratio'), None)
        if derived_stress is not None:
            stress = derived_stress.raw
        raw_temperature = (parameters['case_temperature_C']
                           if parameters['temperature_basis'] == '带散热片功率器件的管壳温度'
                           else ambient_temperature_C)
        lines.extend([
            f'C = ({number_text(min(parameters["max_junction_temperature_C"], 175))} − '
            f'{number_text(parameters["rated_temperature_C"])}) / 150 = {number_text(correction)}',
            f'T = {number_text(raw_temperature)} + (1 − {number_text(correction)}) × 150 = {number_text(temperature)} ℃',
            f'S = ({number_text(parameters["operating_power_W"])} / {number_text(parameters["rated_power_W"])})'
            f' × {number_text(correction)} = {number_text(stress)}',
        ])
    if model not in ('工程模块', '已知单件失效率'):
        substituted = numerical_substitution(MODELS[model].formula, result.factors, result.rate)
        if model in ('国产SMT互连', '国产SMT互连（非工作详细）'):
            lines.extend(_smt_process(result, nonoperating=model.endswith('（非工作详细）')))
        elif substituted is not None:
            lines.extend(_factor_lines(result))
            lines.append(f'代入：{symbol} = {substituted} = {number_text(result.rate)}')
        else:
            lines.extend(_factor_lines(result, separate=True))
            lines.append(f'计算结果：{symbol} = {number_text(result.rate)}')
    else:
        lines.append(f'{symbol} = {number_text(result.rate)}')
    weighted = result.weighted(component.quantity)
    if weighted is not None:
        lines.append(f'小计 = {number_text(component.quantity)} × {number_text(result.rate)} = {number_text(weighted)}')
    return '\n'.join(lines)


def component_report_values(component, result, condition, index, *, prediction_state=None):
    source = component.bom.get('row', {})
    state = prediction_state or (component.rate_state if component.model in ('工程模块', '已知单件失效率') else model_state(component.model))
    state_label, symbol = ('非工作', 'λNP') if state == 'nonoperating' else ('工作', 'λp')
    if component.model in ('已知单件失效率', '工程模块') and not component.disclosure:
        rate = component.condition_rate(condition.environment, condition.ambient_temperature_C)
        parameters = (f'本工况{state_label}失效率 {symbol}（10⁻⁶/h） = ' + number_text(rate)
                      if rate is not None else '本工况未录入适用失效率')
    else:
        parameters = input_text(component, environment=condition.environment,
                                ambient_temperature_C=condition.ambient_temperature_C, detailed=True, result=result)
    parameters = ('公开参数档位：\n' if component.disclosure else '计算参数：\n') + parameters.replace('；', '\n')
    if source.get('Value', '').strip():
        nominal = nominal_value(component)
        shown = standard_nominal_text(nominal) if nominal is not None else source['Value']
        parameters = f'BOM 标称值：{shown}\n' + parameters
    model = model_name(component.disclosure['model']) if component.disclosure else model_name(component.model)
    return [index + 1, ', '.join(component.bom.get('assigned_designators', [])) or component.name,
            source.get('Manufacturer Part Number', ''), component.quantity, model,
            parameters, input_text(component, stress=True, detailed=True, result=result).replace('；', '\n'),
            calculation_process(component, result, condition.ambient_temperature_C, prediction_state=state), result.rate,
            result.weighted(component.quantity), result.error if result.rate is None else '已计算']


def display_lines(value, width):
    """Estimate wrapped lines using double-width CJK characters and explicit breaks."""
    if value is None:
        return 1
    return sum(max(1, math.ceil(sum(2 if unicodedata.east_asian_width(char) in ('W', 'F') else 1
                                     for char in line) / max(1, width - 2)))
               for line in str(value).split('\n'))


def row_height(values, widths):
    lines = max(display_lines(value, width) for value, width in zip(values, widths, strict=True))
    return min(8190, max(420, lines * 300 + 120))


def analysis_report_rows(result):
    """Readable Chinese rows, never raw internal dictionaries or private source IDs."""
    from .analysis_methods.common import OUTPUT_LABELS
    from .analysis_methods.physics import PHYSICS_TOOLS

    rows = []

    def parameter(label, value, unit='', note=''):
        rows.append(['参数', label, value, unit, note])

    values = result.inputs
    if result.method == 'mission':
        parameter('任务剖面权重', '阶段时长' if values['basis'] == 'duration' else '占空比')
        for index, phase in enumerate(result.details, 1):
            prefix = f'阶段 {index} · {phase["name"]}'
            parameter(prefix + ' · 状态', '工作' if phase['state'] == 'working' else '非工作')
            parameter(prefix + ' · 环境', phase['environment'])
            parameter(prefix + ' · 环境温度', phase['ambient_temperature_C'], '℃')
            if 'duration_hours' in phase:
                parameter(prefix + ' · 时长', phase['duration_hours'], 'h')
            parameter(prefix + ' · 占空比', phase['duty_cycle'], '1')
            parameter(prefix + ' · 失效率', phase['rate'], '10⁻⁶/h', phase['source'])
    elif result.method == 'bayes':
        parameter('初始预计失效率 λ1', values['initial_rate'], '10⁻⁶/h')
        parameter('初始有效失效数 a0', values['prior_failures'], '1')
        for index, observation in enumerate(result.details, 1):
            prefix = f'第 {index} 组 · {observation["source"]}'
            parameter(prefix + ' · 失效数', observation['failures'], '次')
            parameter(prefix + ' · 累积元器件小时数', observation['component_hours'], 'h')
            parameter(prefix + ' · 加速因子 AF', observation['acceleration_factor'], '1')
            parameter(prefix + ' · 非加速等效元器件小时数', observation['equivalent_component_hours'], 'h')
    elif result.method == 'failure_modes':
        parameter('标准表格', '表 ' + values['table'])
        parameter('元器件或部件名称', values['category'])
        parameter('元器件失效率', values['rate'], '10⁻⁶/h')
        for mode in result.details:
            rows.append(['模式贡献', mode['mode'], mode['rate'], '10⁻⁶/h', f'原文比例 {number_text(mode["percent"])}%'])
    elif result.method.startswith('physics:'):
        model = PHYSICS_TOOLS[result.method.split(':', 1)[1]]
        parameter('材料/试验/设计参数依据', values['parameter_source'])
        for spec in model.fields:
            parameter(spec.label, values[spec.key], spec.unit)
    for step in result.steps:
        rows.append(['过程', step, '', '', ''])
    for key, value in result.outputs.items():
        rows.append(['结果', OUTPUT_LABELS.get(key, key), value, result.units[key], ''])
    for note in result.notes:
        rows.append(['说明', note, '', '', ''])
    return rows
