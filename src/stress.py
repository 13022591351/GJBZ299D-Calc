"""Bounded input arithmetic and upward selection of tabulated stress values."""

import ast
import keyword
import math
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, DecimalException, localcontext
from fractions import Fraction
from functools import lru_cache

MAX_EXPRESSION_LENGTH = 512
MAX_EXPRESSION_NODES = 128
MAX_EXPRESSION_DEPTH = 24
MAX_VARIABLES = 64
MAX_EVALUATION_NODES = 2048
MAX_DECIMAL_EXPONENT = 1000
MAX_POWER_EXPONENT = 32
MAX_INTEGER_BITS = 4096
ROOT_PRECISION = 80
FUNCTIONS = {'sqrt', 'abs', 'min', 'max'}
NUMBER_LITERAL = re.compile(r'(?:\d+(?:\.\d*)?|\.\d+)(?:[eE]([+-]?\d+))?', re.ASCII)
EXPRESSION_TRANSLATION = str.maketrans({'×': '*', '÷': '/', '（': '(', '）': ')', '−': '-'})


@dataclass(frozen=True)
class StressAdjustment:
    key: str
    label: str
    raw: float
    selected: float
    source: str
    input_text: str = ''


class MissingStressVariable(ValueError):
    def __init__(self, names):
        self.names = frozenset(names)
        super().__init__('应力表达式待填写变量：' + '、'.join(sorted(self.names)))


class _ApproximateFraction(Fraction):
    """A rational estimate with proven bounds, consumed by ceil_stress."""
    def __new__(cls, value, lower, upper):
        result = super().__new__(cls, value)
        result.lower_bound, result.upper_bound = lower, upper
        return result


def _bounded_fraction(value):
    if max(value.numerator.bit_length(), value.denominator.bit_length()) > MAX_INTEGER_BITS:
        raise ValueError('应力表达式中间数值过大，请简化计算')
    return value


def _decimal_fraction(text):
    match = NUMBER_LITERAL.fullmatch(text)
    if match is None:
        raise ValueError('应力表达式只支持十进制数字')
    if match.group(1) is not None and abs(int(match.group(1))) > MAX_DECIMAL_EXPONENT:
        raise ValueError(f'应力表达式的十进制指数须在 ±{MAX_DECIMAL_EXPONENT} 以内')
    try:
        return _bounded_fraction(Fraction(Decimal(text)))
    except (DecimalException, OverflowError) as error:
        raise ValueError('应力表达式数值超出有效范围') from error


def _numeric_fraction(value):
    if type(value) not in (int, float):
        raise ValueError('应力比及变量须填写数值或计算表达式')
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError('应力比及变量须为有限数值')
    if isinstance(value, int) and value.bit_length() > MAX_INTEGER_BITS:
        raise ValueError('应力比数值过大')
    return _bounded_fraction(Fraction(str(value)))


def _validate_name(name, *, function=False):
    if (not isinstance(name, str) or not name.isidentifier() or len(name) > 32
            or name.startswith('_') or '__' in name or keyword.iskeyword(name)
            or unicodedata.normalize('NFKC', name) != name
            or name.lower() in ('nan', 'inf', 'infinity')
            or not function and name in FUNCTIONS):
        raise ValueError('变量名须为不超过 32 字的普通名称，可含字母、汉字、数字和内部下划线；不能使用函数名')


def _normalized_expression(value):
    if not isinstance(value, str):
        raise ValueError('应力表达式须为文本')
    if len(value) > MAX_EXPRESSION_LENGTH:
        raise ValueError(f'应力表达式不能超过 {MAX_EXPRESSION_LENGTH} 个字符')
    expression = ''.join(' ' if character.isspace() else character
                         for character in value.translate(EXPRESSION_TRANSLATION)).strip().replace('^', '**')
    if not expression:
        raise ValueError('请填写应力比或计算表达式')
    if any(not (character.isalnum() or character in '._+-*/(), \t\r\n') for character in expression):
        raise ValueError('应力表达式包含不支持的字符；数值及变量不带单位')
    return expression


@lru_cache(maxsize=4096)
def _expression_tree(expression):
    depth = 0
    for character in expression:
        if character == '(':
            depth += 1
            if depth > MAX_EXPRESSION_DEPTH:
                raise ValueError('应力表达式括号嵌套过深，请简化计算')
        elif character == ')':
            depth -= 1
    try:
        tree = ast.parse(expression, mode='eval')
    except (SyntaxError, ValueError, RecursionError) as error:
        raise ValueError('应力表达式格式无效，请检查数字、运算符及圆括号') from error
    if sum(1 for _ in ast.walk(tree)) > MAX_EXPRESSION_NODES:
        raise ValueError('应力表达式运算过多，请简化计算')
    names = set()

    def inspect(node, level=0):
        if level > MAX_EXPRESSION_DEPTH:
            raise ValueError('应力表达式运算层数过多，请简化计算')
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            _decimal_fraction(ast.get_source_segment(expression, node))
        elif isinstance(node, ast.Name):
            _validate_name(node.id)
            names.add(node.id)
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            inspect(node.operand, level + 1)
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)):
            inspect(node.left, level + 1)
            inspect(node.right, level + 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in FUNCTIONS:
            if node.keywords or (len(node.args) != 1 if node.func.id in ('sqrt', 'abs') else not 2 <= len(node.args) <= 16):
                raise ValueError('sqrt 和 abs 需要一个参数；min 和 max 需要 2～16 个参数')
            for argument in node.args:
                inspect(argument, level + 1)
        else:
            raise ValueError('应力表达式只支持数值、变量、加减乘除、整数幂及 sqrt/abs/min/max')

    inspect(tree.body)
    return tree.body, frozenset(names)


def validate_expression_syntax(value):
    """Validate structure independently of whether variable values are available."""
    if isinstance(value, str):
        _expression_tree(_normalized_expression(value))
    else:
        _numeric_fraction(value)


def expression_variables(value):
    if not isinstance(value, str):
        _numeric_fraction(value)
        return set()
    return set(_expression_tree(_normalized_expression(value))[1])


def _variable_order(variables):
    """Validate definitions and return dependencies before their consumers."""
    if variables is None:
        return ()
    if not isinstance(variables, dict) or len(variables) > MAX_VARIABLES:
        raise ValueError(f'应力变量须为名称与数值的对应表，最多 {MAX_VARIABLES} 项')
    graph = {}
    for name, value in variables.items():
        _validate_name(name)
        if isinstance(value, str) and not value.strip():
            graph[name] = set()
            continue
        validate_expression_syntax(value)
        graph[name] = expression_variables(value)
    depths, ordered = {}, []

    def inspect(name, chain):
        if name in chain:
            raise ValueError('应力变量存在循环引用：' + ' → '.join((*chain, name)))
        if name not in graph:
            return 0
        if name in depths:
            return depths[name]
        # Memoize the longest remaining path, not just whether a node was seen.
        # Shared dependencies must contribute their full depth at every root.
        depth = 1 + max((inspect(dependency, (*chain, name))
                         for dependency in sorted(graph[name])), default=0)
        if depth > MAX_EXPRESSION_DEPTH:
            raise ValueError('应力变量引用层数过多，请简化计算')
        depths[name] = depth
        ordered.append(name)
        return depth

    for name in sorted(graph):
        inspect(name, ())
    return tuple(ordered)


def validate_stress_variables(variables):
    """Reject malformed definitions and cycles; blank or missing values remain pending."""
    _variable_order(variables)


@dataclass(frozen=True)
class _Value:
    value: Fraction
    lower: Fraction
    upper: Fraction

    @classmethod
    def make(cls, value, lower=None, upper=None):
        lower = value if lower is None else lower
        upper = value if upper is None else upper
        for number in (value, lower, upper):
            _bounded_fraction(number)
        return cls(value, lower, upper)

    @property
    def exact(self):
        return self.lower == self.upper


def _perfect_root(value):
    numerator, denominator = math.isqrt(value.numerator), math.isqrt(value.denominator)
    return Fraction(numerator, denominator) if numerator * numerator == value.numerator and denominator * denominator == value.denominator else None


def _sqrt_bound(value, lower):
    root = _perfect_root(value)
    if root is not None:
        return root
    with localcontext() as context:
        context.prec = ROOT_PRECISION
        context.rounding = ROUND_FLOOR if lower else ROUND_CEILING
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        # Decimal.sqrt is correctly rounded; adjacent decimals enclose its exact root.
        root = decimal.sqrt()
        return Fraction(root.next_minus(context) if lower else root.next_plus(context))


def _sqrt(value):
    if value.lower < 0:
        raise ValueError('平方根内的数值须为非负数')
    root = _perfect_root(value.value)
    if root is not None and value.exact:
        return _Value.make(root)
    with localcontext() as context:
        context.prec = ROOT_PRECISION
        estimate = Fraction((Decimal(value.value.numerator) / Decimal(value.value.denominator)).sqrt())
    return _Value.make(estimate, _sqrt_bound(value.lower, True), _sqrt_bound(value.upper, False))


def _binary(left, right, operation):
    if isinstance(operation, ast.Add):
        return _Value.make(left.value + right.value, left.lower + right.lower, left.upper + right.upper)
    if isinstance(operation, ast.Sub):
        return _Value.make(left.value - right.value, left.lower - right.upper, left.upper - right.lower)
    if isinstance(operation, ast.Mult):
        endpoints = [a * b for a in (left.lower, left.upper) for b in (right.lower, right.upper)]
        return _Value.make(left.value * right.value, min(endpoints), max(endpoints))
    if isinstance(operation, ast.Div):
        if right.lower <= 0 <= right.upper:
            raise ValueError('应力表达式的除数不能为 0，也不能包含无法排除 0 的近似区间')
        endpoints = [a / b for a in (left.lower, left.upper) for b in (right.lower, right.upper)]
        return _Value.make(left.value / right.value, min(endpoints), max(endpoints))
    if not right.exact or right.value.denominator != 1 or abs(right.value) > MAX_POWER_EXPONENT:
        raise ValueError(f'应力表达式的幂指数须为 -{MAX_POWER_EXPONENT}～{MAX_POWER_EXPONENT} 的整数')
    exponent = int(right.value)
    if exponent == 0:
        if left.lower <= 0 <= left.upper:
            raise ValueError('应力表达式不支持 0 的 0 次幂')
        return _Value.make(Fraction(1))
    if max(max(x.numerator.bit_length(), x.denominator.bit_length()) for x in (left.value, left.lower, left.upper)) * abs(exponent) > MAX_INTEGER_BITS:
        raise ValueError('应力表达式幂运算的中间数值过大，请简化计算')
    if exponent < 0:
        return _binary(_Value.make(Fraction(1)), _binary(left, _Value.make(Fraction(-exponent)), ast.Pow()), ast.Div())
    endpoints = [left.lower ** exponent, left.upper ** exponent]
    lower = Fraction(0) if exponent % 2 == 0 and left.lower <= 0 <= left.upper else min(endpoints)
    return _Value.make(left.value ** exponent, lower, max(endpoints))


def parse_stress_ratio(value, variables=None):
    """Evaluate a bounded arithmetic tree without eval, attributes, or executable calls."""
    order = _variable_order(variables)
    if not isinstance(value, str):
        # A numeric literal has neither syntax nor dependencies. Validate and
        # convert it once, with exactly the same bounds used by expression leaves.
        return _numeric_fraction(value)
    variables = {} if variables is None else variables
    validate_expression_syntax(value)
    missing, inspected = set(), set()

    def inspect_dependencies(source):
        for name in expression_variables(source):
            if name in inspected:
                continue
            inspected.add(name)
            if name not in variables or isinstance(variables[name], str) and not variables[name].strip():
                missing.add(name)
            else:
                inspect_dependencies(variables[name])

    inspect_dependencies(value)
    if missing:
        raise MissingStressVariable(missing)
    evaluated, budget = {}, [MAX_EVALUATION_NODES]

    def evaluate_source(source):
        if not isinstance(source, str):
            return _Value.make(_numeric_fraction(source))
        expression = _normalized_expression(source)
        return visit(_expression_tree(expression)[0], expression)

    def visit(node, expression):
        budget[0] -= 1
        if budget[0] < 0:
            raise ValueError('应力表达式及变量的总运算量过大，请简化计算')
        if isinstance(node, ast.Constant):
            return _Value.make(_decimal_fraction(ast.get_source_segment(expression, node)))
        if isinstance(node, ast.Name):
            return evaluated[node.id]
        if isinstance(node, ast.UnaryOp):
            operand = visit(node.operand, expression)
            return operand if isinstance(node.op, ast.UAdd) else _Value.make(-operand.value, -operand.upper, -operand.lower)
        if isinstance(node, ast.BinOp):
            return _binary(visit(node.left, expression), visit(node.right, expression), node.op)
        arguments = [visit(argument, expression) for argument in node.args]
        if node.func.id == 'sqrt':
            return _sqrt(arguments[0])
        if node.func.id == 'abs':
            argument = arguments[0]
            lower = Fraction(0) if argument.lower <= 0 <= argument.upper else min(abs(argument.lower), abs(argument.upper))
            return _Value.make(abs(argument.value), lower, max(abs(argument.lower), abs(argument.upper)))
        function = min if node.func.id == 'min' else max
        return _Value.make(function(argument.value for argument in arguments),
                           function(argument.lower for argument in arguments),
                           function(argument.upper for argument in arguments))

    # Each referenced variable is evaluated once in dependency order. A Name
    # lookup cannot recursively enter another expression, so the per-expression
    # depth bound also bounds the runtime stack. Unused definitions stay unused.
    for name in order:
        if name in inspected:
            evaluated[name] = evaluate_source(variables[name])
    result = evaluate_source(value)
    return result.value if result.exact else _ApproximateFraction(result.value, result.lower, result.upper)


def validate_stress(value, maximum, label, variables=None):
    number = parse_stress_ratio(value, variables)
    lower, upper = getattr(number, 'lower_bound', number), getattr(number, 'upper_bound', number)
    if lower <= 0:
        raise ValueError(f'{label}须为可确定的正数')
    if upper > Fraction(str(maximum)):
        raise ValueError(f'{label}超出标准上限 {maximum:g}')
    trace_number(number)
    return number


def trace_number(value):
    result = float(value)
    if not math.isfinite(result) or result == 0 and value != 0:
        raise ValueError('电应力比超出数值表示范围')
    return result


def ceil_stress(value, maximum, label='电应力比 S'):
    """Use exact ratios or an enclosing interval; never guess across a grid boundary."""
    exact = value if isinstance(value, Fraction) else Fraction(str(value))
    lower, upper = getattr(exact, 'lower_bound', exact), getattr(exact, 'upper_bound', exact)
    if lower <= 0:
        raise ValueError(f'{label}须为可确定的正数')
    if upper > Fraction(Decimal(str(maximum))):
        raise ValueError(f'{label}超出标准上限 {maximum:g}')
    def tenths(number):
        return (number.numerator * 10 + number.denominator - 1) // number.denominator
    first, last = tenths(lower), tenths(upper)
    if first != last:
        raise ValueError('应力表达式近似值跨越档位边界，请化简表达式或填写确定值')
    return first / 10


def ceil_to_grid(value, choices, label='电应力比 S'):
    """Resolve an exact value or enclosing interval to an explicit standard axis."""
    exact = value if isinstance(value, Fraction) else Fraction(str(value))
    lower, upper = getattr(exact, 'lower_bound', exact), getattr(exact, 'upper_bound', exact)
    axis = sorted({Fraction(str(point)) for point in choices})
    if lower <= 0 or not axis or upper > axis[-1]:
        raise ValueError(f'{label}超出标准表范围')
    first = next(point for point in axis if point >= lower)
    last = next(point for point in axis if point >= upper)
    if first != last:
        raise ValueError('应力表达式近似值跨越档位边界，请化简表达式')
    return float(first)


