"""Calculation traces and source-backed table helpers for device models."""

import math

from ..model_spec import Evaluation
from ..reference_data import below_value, row_value


class Factors:
    def __init__(self):
        self.values = []
        self.ranges = []

    def add(self, symbol, value, source):
        if not math.isfinite(value) or value < 0:
            raise ValueError(f'{symbol} 必须为有限非负数')
        self.values.append((symbol, value, source))
        return value

    def env(self, table, environment):
        return self.add('πE', *below_value(table, environment))

    def quality(self, table, grade, **kwargs):
        return self.add('πQ', *row_value(table, grade, **kwargs))

    def result(self, rate=None):
        if rate is None:
            rate = math.prod(item[1] for item in self.values)
        if not math.isfinite(rate) or rate < 0:
            raise ValueError('工作失效率必须为有限非负数')
        return Evaluation(rate, tuple(self.values), parameter_ranges=tuple(self.ranges))
