"""Decimal unit conversion and presentation, independent of calculation precision."""

import math
import sys
from decimal import Decimal, DecimalException


def scale_decimal(text, power):
    """Scale the entered decimal exactly, then convert once to the stored float."""
    try:
        decimal = Decimal(text)
        if not decimal.is_finite():
            raise ValueError('数值必须有限')
        sign, digits, exponent = decimal.as_tuple()
        value = float(Decimal((sign, digits, exponent + power)))
    except (DecimalException, OverflowError) as error:
        raise ValueError('数值超出有效范围') from error
    if not math.isfinite(value) or (value == 0 and decimal != 0):
        raise ValueError('数值超出有效范围')
    return value


def number_text(value, *, grouping=False):
    """Format displayed numbers only. Never use this to round stored inputs."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return str(value)
    if value == 0:
        return '0'
    if isinstance(value, int):
        return format(value, ',d' if grouping else 'd')
    if abs(value) < sys.float_info.min:
        # Subnormal floats have fewer significant digits. Their shortest
        # round-tripping representation avoids presenting binary residue as
        # meaningful precision (for example 1e-320 as 9.99988867182683e-321).
        return repr(value)
    return format(value, ',.15g' if grouping else '.15g')


def formatted_number(value):
    return number_text(value, grouping=True) if value is not None else '无有限值'
