"""Read-only ratio and table-bin annotations; entered expressions stay untouched."""

from .numeric import number_text
from .stress import ceil_stress, ceil_to_grid, parse_stress_ratio, trace_number


def stress_value_text(value, maximum, variables=None, *, grid=None):
    original = value if isinstance(value, str) else number_text(value)
    try:
        ratio = parse_stress_ratio(value, variables)
        selected = ceil_to_grid(ratio, grid) if grid is not None else ceil_stress(ratio, maximum)
        computed = trace_number(ratio)
    except (ValueError, OverflowError, ZeroDivisionError):
        return f'{original} [待配置]'
    return (f'{original} [比值 {number_text(computed)}，档位 {number_text(selected)}]'
            if isinstance(value, str) else f'{original} [档位 {number_text(selected)}]')


def adjustment_text(adjustment):
    """Render the calculator's values without evaluating an expression twice."""
    if adjustment.input_text:
        return (f'{adjustment.input_text} [比值 {number_text(adjustment.raw)}，'
                f'档位 {number_text(adjustment.selected)}]')
    return f'{number_text(adjustment.raw)} [档位 {number_text(adjustment.selected)}]'
