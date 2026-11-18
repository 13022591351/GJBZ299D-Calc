"""Section 5.3.10 calibration. Decimal inputs retain exact table coordinates."""

import math
from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class TemperatureCorrection:
    coefficient: Fraction
    temperature: Fraction


def temperature_correction(parameters, ambient_temperature_C):
    def value(key):
        return Fraction(str(parameters[key]))

    ts, tm = value('rated_temperature_C'), value('max_junction_temperature_C')
    if ts < 25 or tm > 200 or tm <= ts:
        raise ValueError('5.3.10.2 未给出该额定温度组合的校正方法；须满足 Ts ≥ 25 ℃、Ts < TM ≤ 200 ℃')
    c = (min(tm, 175) - ts) / 150
    if c <= 0:
        raise ValueError('电应力调整系数 C 必须大于 0')
    raw_t = (value('case_temperature_C') if parameters['temperature_basis'] == '带散热片功率器件的管壳温度'
             else Fraction(str(ambient_temperature_C)))
    if raw_t < Fraction('-273.15') or raw_t >= tm:
        raise ValueError('工作环境温度或管壳温度须低于最高允许结温 TM，且不得低于绝对零度')
    return TemperatureCorrection(c, raw_t + (1 - c) * 150)


def fet_conditions_exact(parameters, ambient_temperature_C):
    correction = temperature_correction(parameters, ambient_temperature_C)
    power = Fraction(str(parameters['operating_power_W']))
    rated = Fraction(str(parameters['rated_power_W']))
    if power <= 0 or rated <= 0 or power > rated:
        raise ValueError('使用功耗须大于 0 且不超过 Ts 时的额定功耗；高于 Ts 时还须按器件降额曲线核查')
    return correction.coefficient, correction.temperature, power / rated * correction.coefficient


def fet_conditions(parameters, ambient_temperature_C):
    values = tuple(float(v) for v in fet_conditions_exact(parameters, ambient_temperature_C))
    if not all(math.isfinite(v) for v in values):
        raise ValueError('校正结果超出数值范围')
    return values


def rated_power_band(power):
    for upper, label in [(0.5, 'P ≤ 0.5'), (1, '0.5 < P ≤ 1'), (5, '1 < P ≤ 5'),
                         (20, '5 < P ≤ 20'), (50, '20 < P ≤ 50'), (200, '50 < P ≤ 200')]:
        if power <= upper:
            return label
    return 'P > 200'
