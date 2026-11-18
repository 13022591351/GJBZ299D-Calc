"""Imported working-state models C.2.12–C.2.21, sourced from Appendix C."""

from functools import partial

from ..model_spec import Input, ModelSpec, choice, quality, stress, temperature
from ..reference_data import equal, result, row_value, temp_below_value, temp_paired_value
from .common import Factors
from .table_choices import horizontal_choices, horizontal_value, row_paths, vertical_choices


def _quality(table, column=0):
    return quality(vertical_choices(table, column))


def _base_from_path(table, selected, *, columns=2):
    fragment, row, _ = row_paths(table, columns)[selected]
    return result(table, [(fragment, fragment['grid'][row, fragment['columns'] - 1])])


def _relay(p, environment, ambient_temperature_C):
    factors = Factors()
    factors.add('λb', *row_value('C.75', p['relay_type']))
    factors.env('C.76', environment)
    factors.quality('C.77', p['quality'])
    factors.add('πT', *temp_below_value('C.78', ambient_temperature_C))
    factors.add('πS', *horizontal_value('C.79', p['stress_ratio']))
    return factors.result()


def _switch(p, environment, ambient_temperature_C):
    factors = Factors()
    mechanism = factors.add('λb1', *row_value('C.80', p['switch_type'], label_column=0))
    count = p['active_contacts']
    if count <= 0 or int(count) != count:
        raise ValueError('有源接点数必须为正整数')
    contacts = factors.add('λb2', .0008 * count, '表 C.81：0.0008N，N 为实际接通电流的动触点数')
    environment_factor = factors.env('C.82', environment)
    quality_factor = factors.quality('C.83', p['quality'], label_column=0)
    temperature_factor = factors.add('πT', *temp_paired_value('C.84', ambient_temperature_C))
    stress_factor = factors.add('πS', *horizontal_value('C.85', p['stress_ratio']))
    return factors.result((mechanism + contacts) * environment_factor * quality_factor * temperature_factor * stress_factor)


_CONTACTS_C90 = (*range(1, 21), *range(25, 105, 5))


def _connector(p, environment, ambient_temperature_C):
    factors = Factors()
    factors.add('λb', *row_value('C.86', p['connector_type'], label_column=1))
    factors.env('C.87', environment)
    factors.quality('C.88', p['quality'], label_column=0)
    factors.add('πT', *temp_paired_value('C.89', ambient_temperature_C))
    raw = p['contacts']
    if raw == '＞100':
        factors.add('πP', 15, '表 C.90 注：n>100 时 πP=15')
    else:
        if raw <= 0 or int(raw) != raw:
            raise ValueError('实际使用接触件数必须为正整数')
        n = int(raw)
        if n > 100:
            factors.add('πP', 15, f'表 C.90 注：n={n}>100 时 πP=15')
        else:
            selected = next(tick for tick in _CONTACTS_C90 if tick >= n)
            value, source = horizontal_value('C.90', selected)
            if selected > n:
                source += f'；接触件数向上取 {selected} 档'
            factors.add('πP', value, source)
    return factors.result()


def _simple(p, environment, ambient_temperature_C, *, base, env, q, layout='vertical'):
    factors = Factors()
    if layout == 'paths':
        base_rate = _base_from_path(base, p['device_type'])
    elif layout == 'horizontal':
        base_rate = horizontal_value(base, p['device_type'])
    else:
        base_rate = row_value(base, p['device_type'], label_column=0)
    factors.add('λb', *base_rate)
    factors.env(env, environment)
    factors.quality(q, p['quality'], label_column=0)
    return factors.result()


OTHER_ROWS = row_paths('C.109', 3, start_row=2)
_BREAKERS = tuple(label for label in OTHER_ROWS if label.startswith('断路器'))


def _other(p, environment, ambient_temperature_C):
    table, row, first_numeric = OTHER_ROWS[p['device_type']]
    columns = [column for column in range(first_numeric, table['columns'])
               if equal(table['grid'][1, column]['text'], environment)]
    factors = Factors()
    rate = factors.add('λp', *result('C.109', [(table, table['grid'][row, column]) for column in columns]))
    if p['device_type'] in _BREAKERS:
        poles = p['poles']
        if poles <= 0 or int(poles) != poles:
            raise ValueError('断路器电极数必须为正整数')
        rate *= factors.add('电极数', poles, '表 C.109 断路器按每个电极计；数量填写断路器只数')
    return factors.result(rate)


def _spec(fields, formula, section, calculator, *, category='OTHER', quality_table=None, example=None, note=''):
    return ModelSpec(fields, formula, f'GJB/Z 299D—2024 {section}',
                     note,
                     category, section, calculator, (quality_table,) if quality_table else (), example or {})


def _simple_spec(section, base, env, q, *, layout='vertical', category='OTHER'):
    labels = (tuple(row_paths(base, 2)) if layout == 'paths' else
              horizontal_choices(base) if layout == 'horizontal' else vertical_choices(base))
    quality_field = _quality(q)
    return _spec([choice('device_type', '种类', labels), quality_field], 'λp=λbπEπQ', section,
                 partial(_simple, base=base, env=env, q=q, layout=layout), category=category,
                 quality_table=q, example={'device_type': labels[0], 'quality': quality_field.choices[0]})


MODELS = {
    '进口继电器': _spec([
        choice('relay_type', '继电器类型', vertical_choices('C.75', 1)), _quality('C.77', 1),
        temperature((30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100, 110, 120)),
        stress(values=tuple(i / 10 for i in range(1, 9)), source='表 C.79'),
    ], 'λp=λbπEπQπTπS', 'C.2.12', _relay, category='K', quality_table='C.77',
        example={'relay_type': '通用(电磁)继电器', 'quality': 'M', 'stress_ratio': .5}),
    '进口开关': _spec([
        choice('switch_type', '开关类型', vertical_choices('C.80')), _quality('C.83'),
        Input('active_contacts', '有源接点数 N', integer=True, min_value=1,
              presets=(1, 2, 4, 8, 12), constraint_hint='表 C.81 有源接点数须为正整数（≥ 1）'), temperature(tuple(range(30, 126, 5))),
        stress(values=tuple(i / 10 for i in range(1, 10)), source='表 C.85'),
    ], 'λp=(λb1+λb2)πEπQπTπS', 'C.2.13', _switch, category='S', quality_table='C.83',
        example={'switch_type': '旋转开关', 'quality': '完全按军用规范采购的产品', 'active_contacts': 4, 'stress_ratio': .5}),
    '进口电连接器': _spec([
        choice('connector_type', '连接器类型', vertical_choices('C.86', 1)), _quality('C.88'),
        temperature(tuple(range(30, 151, 5))),
        choice('contacts', '实际使用接触件数 n', (*_CONTACTS_C90, '＞100'),
               integer=True, min_value=1, ceil_choices=True,
               constraint_hint='表 C.90：1～20 逐档，25～100 按 5 向上取档，＞100 取 πP=15'),
    ], 'λp=λbπEπQπTπP', 'C.2.14', _connector, category='J', quality_table='C.88',
        example={'connector_type': '圆形及矩形', 'quality': '完全按照军用规范采购的产品', 'contacts': 4}),
    '进口微特电机': _simple_spec('C.2.15', 'C.91', 'C.92', 'C.93', layout='paths', category='M'),
    '进口磁性器件': _simple_spec('C.2.16', 'C.94', 'C.95', 'C.96', category='MAG'),
    '进口振荡器和谐振器': _simple_spec('C.2.17', 'C.97', 'C.98', 'C.99', layout='paths', category='Y'),
    '进口滤波器': _simple_spec('C.2.18', 'C.100', 'C.101', 'C.102', category='FL'),
    '进口电池': _simple_spec('C.2.19', 'C.103', 'C.104', 'C.105', layout='horizontal', category='BT'),
    '进口灯': _simple_spec('C.2.20', 'C.106', 'C.107', 'C.108', category='HL'),
    '进口其他电子元器件': _spec([
        choice('device_type', '元器件种类', tuple(OTHER_ROWS)),
        Input('poles', '每只断路器电极数', integer=True, min_value=1, presets=(1, 2, 3, 4),
              when=('device_type', _BREAKERS),
              constraint_hint='表 C.109 断路器电极数须为正整数（≥ 1）'),
    ], 'λp=表 C.109 工作失效率 [断路器按电极数计量]', 'C.2.21', _other,
        note='表 C.109 已给出环境对应工作失效率，不再额外乘环境或质量系数；断路器按每只电极数换算。',
        example={'device_type': '陀螺仪'}),
}
