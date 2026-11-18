"""Typed model declarations shared by calculation, editors and disclosure."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable

TEMP_CHOICE_KEYS = frozenset({
    'temperature_C',
    'junction_temperature_C',
    'channel_temperature_C',
    'analog_junction_temperature_C',
    'digital_junction_temperature_C',
    'hybrid_temperature_C',
})


def _scalar_equal(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _matches_dep_value(actual, expected):
    if actual is None:
        return False
    if isinstance(expected, (tuple, list, set, frozenset)):
        return any(_scalar_equal(actual, item) for item in expected)
    return _scalar_equal(actual, expected)


def normalize_option(item):
    """Split a preset/choice entry into (stored_value, custom_display_text_or_None)."""
    if isinstance(item, tuple) and len(item) == 2:
        return item[0], str(item[1])
    return item, None


@dataclass(frozen=True)
class Input:
    key: str
    label: str
    section: str = '器件参数'
    choices: tuple | None = None
    positive: bool = True
    scope: str = 'device'
    when: tuple | None = None
    allow_negative: bool = False
    integer: bool = False
    stress_max: float | None = None
    stress_source: str | tuple[str, ...] = ''
    default: int | float | str | None = None
    when_all: tuple = ()
    record_fields: tuple = ()
    min_value: float | None = None
    max_value: float | None = None
    min_inclusive: bool = True
    max_inclusive: bool = True
    presets: tuple = ()
    choices_by: tuple = ()
    limits_by: tuple = ()
    constraint_hint: str = ''
    ceil_choices: bool = False

    def active(self, parameters):
        conditions = (() if self.when is None else (self.when,)) + self.when_all
        return all(parameters.get(key) in values for key, values in conditions)

    def resolve_choices(self, parameters=None):
        """Return conditionally filtered choices/presets when choices_by matches parameters."""
        if not self.choices_by or not parameters:
            return None
        dep_key, branches = self.choices_by
        actual = parameters.get(dep_key)
        if actual is None:
            return None
        for dep_val, options in branches:
            if _matches_dep_value(actual, dep_val):
                return tuple(options)
        return None

    def active_limits(self, parameters=None):
        """Return (min_v, max_v, min_inc, max_inc, hint) merged with active limits_by."""
        min_v, max_v = self.min_value, self.max_value
        min_inc, max_inc = self.min_inclusive, self.max_inclusive
        hint = self.constraint_hint
        if self.limits_by and parameters:
            dep_key, branches = self.limits_by
            actual = parameters.get(dep_key)
            if actual is not None:
                for dep_val, spec in branches:
                    if _matches_dep_value(actual, dep_val):
                        if len(spec) == 3:
                            b_min, b_max, b_hint = spec
                            b_min_inc, b_max_inc = min_inc, max_inc
                        else:
                            b_min, b_max, b_hint, b_min_inc, b_max_inc = spec
                        min_v, max_v = b_min, b_max
                        min_inc, max_inc = b_min_inc, b_max_inc
                        if b_hint:
                            hint = b_hint
                        break
        return min_v, max_v, min_inc, max_inc, hint

    def is_composite(self):
        """True when rendered as CompositeComboBox (editable combo + hint/warning label)."""
        if self.record_fields or self.stress_max is not None:
            return False
        if self.choices is not None:
            return self.key in TEMP_CHOICE_KEYS or self.ceil_choices
        return bool(
            self.presets
            or self.limits_by
            or self.min_value is not None
            or self.max_value is not None
            or self.constraint_hint
        )

    def composite_options(self, parameters=None):
        """Options/presets shown in CompositeComboBox dropdown for the current context."""
        resolved = self.resolve_choices(parameters)
        if resolved is not None:
            return resolved
        if self.presets:
            return self.presets
        return self.choices if self.choices is not None else ()


@dataclass(frozen=True)
class Evaluation:
    rate: float | None
    factors: tuple = ()
    error: str = ''
    stress_adjustments: tuple = ()
    parameter_ranges: tuple = ()

    def weighted(self, quantity):
        return self.rate * quantity if self.rate is not None and quantity is not None else None


@dataclass(frozen=True)
class ModelSpec(Mapping):
    fields: tuple
    formula: str
    reference: str
    note: str = ''
    category: str = 'OTHER'
    section: str = ''
    calculator: Callable | None = None
    quality_tables: tuple = ()
    example: dict = field(default_factory=dict)
    example_temperature: float = 40
    derived_stress: tuple = ()
    limitation: str = ''
    quality_summaries: tuple = ()
    method: str = 'working_stress'

    def __post_init__(self):
        object.__setattr__(self, 'fields', tuple(self.fields))

    def __getitem__(self, key):
        if key not in self.__dataclass_fields__:
            raise KeyError(key)
        return getattr(self, key)

    def __iter__(self):
        return iter(self.__dataclass_fields__)

    def __len__(self):
        return len(self.__dataclass_fields__)


def choice(key, label, values, section='器件参数', **kwargs):
    return Input(key, label, section, tuple(values), **kwargs)


def quality(values):
    return choice('quality', '质量等级', values, '质量与工艺')


def temperature(values=None):
    return Input('temperature_C', '工程环境温度 Ta（℃）', '总体条件',
                 None if values is None else tuple(values), positive=False,
                 scope='project', allow_negative=True)


def stress(key='stress_ratio', label='电应力比 S', values=None, *, source='', when=None, **kwargs):
    values = tuple(values) if values is not None else tuple(n / 10 for n in range(1, 11))
    return Input(key, label, '工作应力', values, when=when,
                 stress_max=max(values), stress_source=source, **kwargs)
