"""Display standard input units, with an optional engineering-unit reading aid."""

import math
from decimal import Decimal
from functools import lru_cache

from .nominal import NominalValue
from .numeric import number_text

# The inductor BOM uses the common μH presentation; no calculation field is added.
PRIMARY_UNITS = {'R': 'Ω', 'C': 'μF', 'L': 'μH'}
ACTUAL_FIELDS = {'resistance_ohm': 'Ω', 'capacitance_uF': 'μF'}


def _reading_aid(parsed, primary_unit, *, significant_digits=None):
    if parsed.decimal_value == 0 or parsed.engineering_unit == primary_unit:
        return ''
    return f' [{parsed.engineering_text(significant_digits=significant_digits)}]'


@lru_cache(maxsize=2048)
def standard_nominal_text(parsed):
    """Return Ω/μF/μH as the main BOM unit; never convert stored parameters."""
    if parsed is None:
        return ''
    unit = PRIMARY_UNITS[parsed.kind]
    return parsed.text_in_unit(unit) + _reading_aid(parsed, unit)


def actual_value_text(key, value):
    """Format an actual model input; its field label already supplies the unit."""
    text = number_text(value)
    if key not in ACTUAL_FIELDS or isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return text
    # Convert from the stored input, never from its rounded display label.
    # Both labels use the existing 15-significant-digit presentation policy.
    unit = ACTUAL_FIELDS[key]
    parsed = NominalValue.from_unit(Decimal(str(value)), unit)
    return text + _reading_aid(parsed, unit, significant_digits=15)
