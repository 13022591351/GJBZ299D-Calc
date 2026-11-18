"""Look up standard coefficients using named physical quantities and device types."""

import math
from decimal import Decimal

from .reference_data import (
    below_value,
    ceil_row_value,
    fragments,
    matrix_value,
    resolve_numeric_tick,
    temp_matrix_value,
    temp_paired_value,
)
from .standard_data import lookup

SUPPLY_VOLTAGE_CHOICES = ('≤3', 3, *range(4, 16))
SUPPLY_VOLTAGE_PRESETS = ('≤3', *range(4, 16))
SUPPLY_VOLTAGE_HINT = '表 26：0 < Vs ≤ 3 V 按 ≤3 档，3 < Vs ≤ 15 V 向上取整至下一整数电压档'

DIGITAL_IC_PACKAGES = [
    f'{seal}-{package}封装'
    for seal in ('密封', '非密封')
    for package in ('双列直插(DIP)', '扁平(FP)', '插接阵列(PGA/BGA和其他)', '表面安装(LCC/SO和其他)')
]
DIGITAL_IC_PACKAGES.insert(4, '密封-金属壳封装')

RESISTOR_TEMPERATURE_LABELS = {
    '普通膜（≤1 W）': '碳膜、氧化物膜、金属膜电阻器（P≤1W）',
    '功率膜（>1 W）': '功率膜电阻器（P>1W）',
    '普通线绕': '线绕电阻器',
    '精密线绕': '线绕电阻器',
    '功率线绕': '功率线绕电阻器',
    '片式膜': '片式膜电阻器',
}
RESISTOR_STRESS_LABELS = {
    '普通膜（≤1 W）': '膜电阻器（碳膜、氧化物膜、金属膜电阻器）、片式膜电阻器',
    '片式膜': '膜电阻器（碳膜、氧化物膜、金属膜电阻器）、片式膜电阻器',
    '功率膜（>1 W）': '功率膜电阻器',
    '普通线绕': '线绕电阻器、功率线绕电阻器',
    '精密线绕': '线绕电阻器、功率线绕电阻器',
    '功率线绕': '线绕电阻器、功率线绕电阻器',
}
CAPACITOR_TEMPERATURE_LABELS = {
    '纸介': '纸介、纸-膜复合介质',
    '纸膜复合': '纸介、纸-膜复合介质',
    '薄膜': '薄膜',
    '云母': '云母电容器',
    '瓷介': '瓷介电容器',
    '铝电解': '铝电解电容器',
    '固体钽（密封）': '固体钽电解电容器',
    '固体钽（非密封）': '固体钽电解电容器',
    '非固体钽': '非固体钽电解电容器',
    '氧化铌': '氧化铌电解电容器',
}
CAPACITOR_STRESS_LABELS = {
    **CAPACITOR_TEMPERATURE_LABELS,
    '纸介': '纸和薄膜电容器',
    '纸膜复合': '纸和薄膜电容器',
    '薄膜': '纸和薄膜电容器',
}


def capacitor_base_rate(table_number, *, temperature_C, stress_ratio):
    """Tables 217, 221 and 222: temperature rows and voltage-stress columns."""
    if table_number not in ('217', '221', '222'):
        raise ValueError('瓷介电容器基本失效率须使用表 217、221 或 222')
    return temp_matrix_value(table_number, temperature_C, stress_ratio, label_column=0)


def column_numeric_ticks(table_number, column, *, label_column=0, first_row=1):
    values = set()
    for table in fragments(str(table_number)):
        target = table['columns'] - 1 if column == -1 else column
        for row in range(first_row, table['rows']):
            axis_cell = table['grid'].get((row, label_column))
            val_cell = table['grid'].get((row, target))
            if (
                axis_cell
                and axis_cell['kind'] == 'number'
                and val_cell
                and val_cell['id'] != axis_cell['id']
                and val_cell['kind'] == 'number'
            ):
                num = float(axis_cell['decimal_text'])
                values.add(int(num) if num.is_integer() else num)
    return tuple(sorted(values))


def package_lead_choices_by(table_number, packages, *, dep_key='package'):
    return (
        dep_key,
        tuple(
            (pkg, column_numeric_ticks(table_number, idx, label_column=0, first_row=2))
            for idx, pkg in enumerate(packages, 1)
        ),
    )


def supply_voltage_factor(raw_vs):
    if raw_vs == '≤3':
        return below_value('26', '≤3')
    if isinstance(raw_vs, bool) or not isinstance(raw_vs, (int, float)) or not math.isfinite(raw_vs):
        raise ValueError('工作电源电压 Vs 须为有效数值或 ≤3')
    if raw_vs <= 0 or raw_vs > 15:
        raise ValueError('工作电源电压 Vs 须满足 0 < Vs ≤ 15 V（表 26）')
    if raw_vs <= 3:
        return below_value('26', '≤3')
    tick = math.ceil(raw_vs)
    value, source = below_value('26', tick)
    note = f'；电源电压向上取 {tick} V 档' if tick > raw_vs else ''
    return value, source + note


def digital_ic_complexity(technology, gate_count, coefficient):
    """C1 and C2 are the standard's circuit-complexity coefficients."""
    if coefficient not in ('C1', 'C2'):
        raise ValueError('通用数字集成电路复杂度系数须为 C1 或 C2')
    table = {'双极型': '4', 'MOS': '5'}[technology]
    col = 1 if coefficient == 'C1' else 2
    ticks = [Decimal(str(t)) for t in column_numeric_ticks(table, col, label_column=0, first_row=1)]
    tick, note = resolve_numeric_tick(table, gate_count, ticks, axis_label='门数')
    value, source = lookup(table, coefficient, tick)
    return value, source + note


def digital_ic_package(package, lead_count):
    return ceil_row_value('27', lead_count, DIGITAL_IC_PACKAGES.index(package) + 1,
                          label_column=0, first_row=2, axis_label='引出端数')


def digital_ic_temperature(technology, junction_temperature_C):
    return temp_paired_value({'双极型': '16', 'MOS': '17'}[technology], junction_temperature_C)


def resistor_temperature(kind, temperature_C):
    return temp_matrix_value('C.47', RESISTOR_TEMPERATURE_LABELS[kind], temperature_C, temp_axis='col')


def resistor_stress(kind, stress_ratio):
    return matrix_value('C.48', RESISTOR_STRESS_LABELS[kind], stress_ratio)


def capacitor_temperature(kind, temperature_C):
    return temp_matrix_value('C.61', CAPACITOR_TEMPERATURE_LABELS[kind], temperature_C, temp_axis='col')


def capacitor_stress(kind, stress_ratio):
    return matrix_value('C.62', CAPACITOR_STRESS_LABELS[kind], stress_ratio)


def discrete_temperature(kind, temperature_C):
    label = '场效应晶体管' if kind == '硅场效应晶体管' else kind
    return temp_matrix_value('C.33', label, temperature_C, label_column=0, temp_axis='col')


def diode_stress(*, current_ratio, voltage_ratio):
    """Table C.34: current-stress rows and voltage-stress columns."""
    return matrix_value('C.34', current_ratio, voltage_ratio, label_column=0)


def bjt_stress(*, voltage_ratio, power_ratio):
    """Table C.35: voltage-stress rows and power-stress columns."""
    return matrix_value('C.35', voltage_ratio, power_ratio, label_column=0)


def discrete_stress(kind, stress_ratio):
    return matrix_value('C.36', kind, stress_ratio, label_column=0)
