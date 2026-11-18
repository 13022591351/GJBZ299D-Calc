"""Context-aware R/C/L values and markings, never inferred from part numbers.

Marking references (no runtime network access):
https://www.koaglobal.com/product/library/resistor/marking?sc_lang=en
https://product.tdk.com/en/contact/faq/inductors-0032.html
TDK VLS5045EX part-number construction: μH code + J/K/M tolerance:
https://product.tdk.com/system/files/dam/doc/product/inductor/inductor/smd/catalog/inductor_commercial_power_vls5045ex_en.pdf
Yageo Product Selection Guide, p. 10 (2008), capacitor multipliers 8 and 9:
https://pccomponents.com/datasheets/Yageo-catalog.pdf
"""

import math
import re
from dataclasses import dataclass
from decimal import Decimal, DecimalException
from functools import lru_cache

NUMBER = r'(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?'
PREFIXES = {'': 0, 'f': -15, 'p': -12, 'P': -12, 'n': -9, 'N': -9, 'u': -6, 'U': -6,
            'm': -3, 'k': 3, 'K': 3, 'M': 6, 'G': 9, 'T': 12}
BASE_UNITS = {'R': 'Ω', 'C': 'F', 'L': 'H'}
DISPLAY_PREFIXES = {
    'R': [(-6, 'μ'), (-3, 'm'), (0, ''), (3, 'k'), (6, 'M'), (9, 'G'), (12, 'T')],
    'C': [(-15, 'f'), (-12, 'p'), (-9, 'n'), (-6, 'μ'), (-3, 'm'), (0, '')],
    'L': [(-12, 'p'), (-9, 'n'), (-6, 'μ'), (-3, 'm'), (0, '')],
}


def _shift(value, power):
    """Shift a Decimal exponent without applying the current context precision."""
    sign, digits, exponent = value.as_tuple()
    return Decimal((sign, digits, exponent + power))


def _float(value):
    result = float(value)
    if not math.isfinite(result) or result == 0 and value != 0:
        raise ValueError('标称值超出数值范围')
    return result


def _decimal_text(value):
    if value == 0:
        return '0'
    sign, digits, exponent = value.as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    exact = Decimal((sign, tuple(digits), exponent))
    # Scientific display keeps extreme values readable without rounding them.
    return str(exact).replace('E', 'e') if exact.adjusted() < -6 or exact.adjusted() > 12 else format(exact, 'f')


def _unit(text):
    """Return (kind, SI exponent) for an explicit unit, preserving prefix case."""
    text = text.replace('Ω', 'Ω').replace('µ', 'u').replace('μ', 'u')
    match = re.fullmatch(r'([fpPnNuUmkKMGT]?)(Ω|[oO][hH][mM][sS]?|[RrFfHh])', text)
    if not match:
        return None
    prefix, unit = match.groups()
    kind = 'C' if unit in ('F', 'f') else 'L' if unit in ('H', 'h') else 'R'
    return kind, PREFIXES[prefix]


@dataclass(frozen=True)
class NominalValue:
    """One physical value stored exactly in Ω, F or H, plus its original text."""

    kind: str
    decimal_value: Decimal
    original: str

    @classmethod
    def from_unit(cls, value, unit, *, original=''):
        """Convert a Decimal model input directly to its SI base unit."""
        resolved = _unit(unit)
        if resolved is None or not isinstance(value, Decimal) or not value.is_finite():
            raise ValueError('物理量需要有限 Decimal 数值和有效单位')
        kind, power = resolved
        return cls(kind, _shift(value, power), original)

    @property
    def unit(self):
        return BASE_UNITS[self.kind]

    @property
    def value(self):
        """The value in ohms, farads, or henries; display never feeds this value."""
        return _float(self.decimal_value)

    def decimal_in_unit(self, unit):
        """Convert the SI value to the requested unit without binary or Decimal rounding."""
        target = _unit(unit)
        if target is None or target[0] != self.kind:
            raise ValueError('目标单位与标称值类型不一致')
        return _shift(self.decimal_value, -target[1])

    def in_unit(self, unit):
        """Convert to a model unit, then to float once, avoiding chained float scaling."""
        return _float(self.decimal_in_unit(unit))

    @property
    def engineering_unit(self):
        """Choose a supported prefix giving 1 ≤ magnitude < 1000 when possible."""
        prefixes = DISPLAY_PREFIXES[self.kind]
        if self.decimal_value == 0:
            prefix = ''
        else:
            _power, prefix = next(((power, prefix) for power, prefix in reversed(prefixes)
                                   if self.decimal_value.adjusted() >= power), prefixes[0])
        return prefix + self.unit

    def text_in_unit(self, unit, *, significant_digits=None):
        """Format a converted copy; rounding, if requested, is display-only."""
        value = self.decimal_in_unit(unit)
        if significant_digits is not None:
            value = Decimal(format(value, f'.{significant_digits}g'))
        return f'{_decimal_text(value)} {unit}'

    def engineering_text(self, *, significant_digits=None):
        return self.text_in_unit(self.engineering_unit, significant_digits=significant_digits)

    @property
    def display(self):
        return self.engineering_text()


def _marking(compact, kind):
    """Return decimal + base-unit exponent for a context-specific marking."""
    if kind == 'R' and compact in ('0', '000', '0000'):
        return '0', 0
    if kind == 'C':
        # Require non-zero leading digit so package codes (e.g. 0402, 0603) never parse as values.
        match = re.fullmatch(r'([1-9][0-9])([0-9])(?:[JKM])?', compact)
        scale = -12
    elif kind == 'L':
        decimal_mark = re.fullmatch(r'([0-9]*)[Rr]([0-9]+)[JKM]', compact)
        if decimal_mark:
            leading, trailing = decimal_mark.groups()
            return (leading or '0') + '.' + trailing, -6
        match = re.fullmatch(r'([1-9][0-9])([0-7])(?:[JKM])?', compact)
        scale = -6
    elif kind == 'R':
        match = re.fullmatch(r'([1-9][0-9]{1,2})([0-9])', compact)
        scale = 0
    else:
        return None
    if match:
        digits, exponent = match.groups()
        power = {8: -2, 9: -1}.get(int(exponent), int(exponent)) if kind == 'C' else int(exponent)
        return digits, power + scale
    return None


def _compact(text, kind):
    compact = re.sub(r'\s+', '', text).replace('Ω', 'Ω').replace('µ', 'u').replace('μ', 'u')
    # A digit-by-digit marking may contain spaces. Separate numeric tokens are
    # otherwise ambiguous: "4.7 100nF" must not become "4.7100nF".
    boundaries = list(re.finditer(r'(?<=[0-9])\s+(?=[0-9])', text))
    if boundaries:
        if re.fullmatch(r'[0-9]{3,4}[JKM]?', compact) is None or _marking(compact, kind) is None:
            return None
        for boundary in boundaries:
            left = re.search(r'[0-9.]+$', text[:boundary.start()])[0]
            right = re.match(r'[0-9.]+', text[boundary.end():])[0]
            if len(left) != 1 or len(right) != 1:
                return None
    return compact


def parse_nominal(text, kind=None):
    """Parse one explicit nominal value, returning None for ambiguous input.

    ``kind`` is R, C or L. It resolves prefix-only notations such as 4u7 or 10n,
    but never overrides a conflicting unit. Pure digits require a known kind:
    R uses three/four-digit ohm markings, C three-digit pF markings, and L
    three-digit μH markings. Explicit units always take precedence. Unsupported
    markings and text with multiple numeric values remain unparsed.
    """
    if kind not in (None, 'R', 'C', 'L'):
        raise ValueError('标称值类型须为 R、C 或 L')
    if not isinstance(text, str) or not text.strip():
        return None
    return _parse_nominal(str(text), str(kind) if kind is not None else None)


@lru_cache(maxsize=2048)
def _parse_nominal(text, kind):
    """Cache immutable results only after the public input checks succeed."""
    original = text
    compact = _compact(text, kind)
    if compact is None:
        return None
    if compact.startswith('+'):
        compact = compact[1:]
    match = re.fullmatch(f'({NUMBER})(.*)', compact)
    resolved = None
    if match:
        decimal, suffix = match.groups()
        resolved = _unit(suffix)
        if resolved is None:
            marking = _marking(compact, kind)
            if marking is not None:
                decimal, power = marking
                resolved = kind, power
            elif kind == 'L' and re.fullmatch(r'[0-9]{3}[JKM]', compact):
                return None  # An unsupported marking must not become kilo/megahenries.
            elif suffix in PREFIXES and suffix:
                prefix_kind = kind or ('R' if suffix in ('k', 'K', 'M', 'G', 'T') else None)
                if prefix_kind is not None:
                    resolved = prefix_kind, PREFIXES[suffix]
    if resolved is None:
        # R47M starts with a letter and therefore has no NUMBER match above.
        marking = _marking(compact, kind)
        if marking is not None:
            decimal, power = marking
            resolved = kind, power
    if resolved is None:
        match = re.fullmatch(r'([0-9]*)([RrLfnNpPuUmkKMGT])([0-9]+)(Ω|[oO][hH][mM][sS]?|[FfHh])?', compact)
        if not match:
            return None
        leading, marker, trailing, suffix = match.groups()
        decimal = (leading or '0') + '.' + trailing
        if marker in ('R', 'r'):
            # The explicit suffix defines the unit: 4R7H is 4.7 H. Without a
            # suffix TDK's inductor marking uses R as the μH decimal point.
            resolved = (_unit(suffix)[0], 0) if suffix else ('L', -6) if kind == 'L' else ('R', 0)
        elif marker == 'L':
            if kind != 'R' or suffix is not None and _unit(suffix)[0] != 'R':
                return None
            resolved = 'R', -3
        else:
            actual_kind = _unit(suffix)[0] if suffix else kind
            if actual_kind is None and marker in ('k', 'K', 'M', 'G', 'T'):
                actual_kind = 'R'
            if actual_kind is None:
                return None
            resolved = actual_kind, PREFIXES[marker]
    actual_kind, power = resolved
    if kind is not None and actual_kind != kind:
        return None
    try:
        value = _shift(Decimal(decimal), power)
        if not value.is_finite() or value < 0 or actual_kind != 'R' and value == 0:
            return None
        _float(value)
    except (DecimalException, OverflowError, ValueError):
        return None
    return NominalValue(actual_kind, value, original)


def normalize_nominal(text, kind=None):
    """Return a display label, preserving unrecognized source text as supplied."""
    result = parse_nominal(text, kind)
    return result.display if result is not None else text if isinstance(text, str) else '' if text is None else str(text)
