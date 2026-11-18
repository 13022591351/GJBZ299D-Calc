"""Tests for declarative Input constraints, CompositeComboBox, and sync_field_constraints."""

import math

import pytest
from PyQt6.QtWidgets import QComboBox, QFormLayout, QWidget

from src.batch_dialog import BatchDialog
from src.dialogs import ComponentDialog
from src.domain import Component
from src.field_editor import (
    CompositeComboBox,
    TemperatureComboBox,
    active_choices,
    add_field_row,
    composite_input_row,
    create_field,
    field_draft,
    is_choice_combo,
    is_field_blank,
    read_field_value,
    restore_field_draft,
    set_field_value,
    sync_field_constraints,
)
from src.model_spec import Input
from src.number_edit import NumberEdit
from src.standards import MODELS, _validate_fields, evaluate, validate_parameters
from src.theme import current_colors


def test_input_metadata_defaults_and_widget_selection(qapp):
    plain = Input('resistance_ohm', '阻值 R（Ω）')
    assert plain.min_value is None
    assert plain.max_value is None
    assert plain.min_inclusive is True
    assert plain.max_inclusive is True
    assert plain.presets == ()
    assert plain.choices_by == ()
    assert plain.limits_by == ()
    assert plain.constraint_hint == ''
    assert plain.ceil_choices is False
    assert plain.is_composite() is False

    assert TemperatureComboBox is CompositeComboBox
    assert isinstance(create_field(plain, '国产片式膜电阻器'), NumberEdit)

    temp_spec = next(f for f in MODELS['国产普通数字集成电路'].fields if f.key == 'junction_temperature_C')
    temp_widget = create_field(temp_spec, '国产普通数字集成电路')
    assert isinstance(temp_widget, CompositeComboBox)
    assert isinstance(temp_widget, TemperatureComboBox)
    assert not is_choice_combo(temp_widget)

    qual_spec = next(f for f in MODELS['国产电容器（非工作详细）'].fields if f.key == 'quality')
    qual_widget = create_field(qual_spec, '国产电容器（非工作详细）')
    assert isinstance(qual_widget, QComboBox)
    assert is_choice_combo(qual_widget)


def test_validation_choices_by_limits_by_and_ceil_choices():
    # 1. ceil_choices: 进口电连接器 contacts + 国产光纤连接器（非工作详细） fiber_count
    imp_base = dict(MODELS['进口电连接器'].example)
    ev_exact = evaluate('进口电连接器', imp_base | {'contacts': 25}, 'GB', ambient_temperature_C=30)
    ev_ceil = evaluate('进口电连接器', imp_base | {'contacts': 22}, 'GB', ambient_temperature_C=30)
    assert ev_ceil.error == '' and ev_ceil.rate == ev_exact.rate
    assert any('接触件数向上取 25 档' in src for _s, _v, src in ev_ceil.factors)

    ev_over_num = evaluate('进口电连接器', imp_base | {'contacts': 120}, 'GB', ambient_temperature_C=30)
    ev_over_str = evaluate('进口电连接器', imp_base | {'contacts': '＞100'}, 'GB', ambient_temperature_C=30)
    assert ev_over_num.error == '' and ev_over_num.rate == ev_over_str.rate

    assert evaluate('进口电连接器', imp_base | {'contacts': 0}, 'GB', ambient_temperature_C=30).rate is None
    assert evaluate('进口电连接器', imp_base | {'contacts': 2.5}, 'GB', ambient_temperature_C=30).rate is None

    fib_base = dict(MODELS['国产光纤连接器（非工作详细）'].example)
    fib_3 = evaluate('国产光纤连接器（非工作详细）', fib_base | {'fiber_count': 3}, 'GB', ambient_temperature_C=25)
    fib_4 = evaluate('国产光纤连接器（非工作详细）', fib_base | {'fiber_count': 4}, 'GB', ambient_temperature_C=25)
    assert fib_3.error == '' and fib_3.rate == fib_4.rate

    fib_9 = evaluate('国产光纤连接器（非工作详细）', fib_base | {'fiber_count': 9}, 'GB', ambient_temperature_C=25)
    fib_12 = evaluate('国产光纤连接器（非工作详细）', fib_base | {'fiber_count': 12}, 'GB', ambient_temperature_C=25)
    fib_ge10 = evaluate('国产光纤连接器（非工作详细）', fib_base | {'fiber_count': 'N≥10'}, 'GB', ambient_temperature_C=25)
    assert fib_9.error == '' and fib_9.rate == fib_ge10.rate
    assert fib_12.error == '' and fib_12.rate == fib_ge10.rate

    # Synthetic ceil_choices without overflow label rejects above max numeric choice.
    bounded_ceil = Input('bins', '档位', choices=(10, 20, 50), integer=True, min_value=1, ceil_choices=True)
    _validate_fields((bounded_ceil,), {'bins': 25})
    with pytest.raises(ValueError, match='超出标准表上限'):
        _validate_fields((bounded_ceil,), {'bins': 51})

    # 2. choices_by & limits_by: 模数混合集成电路 digital_complexity & 非工作详细电容/电阻/继电器/微特电机 quality
    mixed_base = dict(MODELS['国产模数混合集成电路'].example)
    bad_mixed = evaluate('国产模数混合集成电路', mixed_base | {'digital_technology': '双极型', 'digital_complexity': 80000.0}, 'GB', ambient_temperature_C=25)
    assert bad_mixed.rate is None and '60000' in bad_mixed.error
    ok_mixed_mos = evaluate('国产模数混合集成电路', mixed_base | {'digital_technology': 'MOS', 'digital_complexity': 80000.0}, 'GB', ambient_temperature_C=25)
    assert ok_mixed_mos.error == '' and ok_mixed_mos.rate > 0

    cap_ex = dict(MODELS['国产电容器（非工作详细）'].example)
    bad_cap_c = evaluate('国产电容器（非工作详细）', cap_ex | {'quality': 'C'}, 'GB', ambient_temperature_C=25)
    assert bad_cap_c.rate is None and '仅可选' in bad_cap_c.error
    bad_nb = evaluate('国产电容器（非工作详细）', cap_ex | {'kind': '氧化铌电解电容器', 'quality': 'A1P'}, 'GB', ambient_temperature_C=25)
    assert bad_nb.rate is None and '仅可选' in bad_nb.error

    res_nonop = dict(MODELS['国产电阻器（非工作详细）'].example)
    assert evaluate('国产电阻器（非工作详细）', res_nonop | {'kind': '碳膜(功率 P ≤2W)', 'rated_power_W': 1, 'quality': 'A1P'}, 'GB', ambient_temperature_C=25).rate is None

    # 3. min_value / max_value / limits_by across models
    fet_ex = dict(MODELS['国产硅场效应晶体管'].example)
    assert evaluate('国产硅场效应晶体管', fet_ex | {'rated_temperature_C': 20}, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产硅场效应晶体管', fet_ex | {'max_junction_temperature_C': 210}, 'GB', ambient_temperature_C=25).rate is None

    bjt_ex = dict(MODELS['国产双极型晶体管'].example)
    assert evaluate('国产双极型晶体管', bjt_ex | {'kind': '小功率晶体管', 'rated_power_W': 1}, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产双极型晶体管', bjt_ex | {'kind': '小功率晶体管', 'rated_power_W': 0.5}, 'GB', ambient_temperature_C=25).rate > 0
    assert evaluate('国产双极型晶体管', bjt_ex | {'kind': '大功率晶体管', 'rated_power_W': 0.8}, 'GB', ambient_temperature_C=25).rate is None

    thy_ex = dict(MODELS['国产闸流晶体管'].example)
    assert evaluate('国产闸流晶体管', thy_ex | {'rated_current_A': 0.1}, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产闸流晶体管', thy_ex | {'rated_current_A': 0.2}, 'GB', ambient_temperature_C=25).rate > 0

    mag_ex = dict(MODELS['国产磁控管'].example)
    assert evaluate('国产磁控管', mag_ex | {'rated_power_kW': 5}, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产磁控管', mag_ex | {'rated_power_kW': 4}, 'GB', ambient_temperature_C=25).rate > 0

    carbon_ex = dict(MODELS['国产碳膜电阻器'].example)
    assert evaluate('国产碳膜电阻器', carbon_ex | {'rated_power_W': 2}, 'GB', ambient_temperature_C=25).rate > 0
    assert evaluate('国产碳膜电阻器', carbon_ex | {'rated_power_W': 2.5}, 'GB', ambient_temperature_C=25).rate is None

    power_nl_ex = dict(MODELS['国产功率非线绕电阻器'].example)
    assert evaluate('国产功率非线绕电阻器', power_nl_ex | {'rated_power_W': 2}, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产功率非线绕电阻器', power_nl_ex | {'rated_power_W': 3}, 'GB', ambient_temperature_C=25).rate > 0

    pot_ex = dict(MODELS['国产普通线绕电位器'].example)
    assert evaluate('国产普通线绕电位器', pot_ex | {'terminal_count': 2}, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产普通线绕电位器', pot_ex | {'terminal_count': 20}, 'GB', ambient_temperature_C=25).rate > 0

    net_ex = dict(MODELS['国产电阻网络'].example)
    assert evaluate('国产电阻网络', net_ex | {'case_temperature_C': -10}, 'GB', ambient_temperature_C=25).rate > 0
    assert evaluate('国产电阻网络', net_ex | {'case_temperature_C': 130}, 'GB', ambient_temperature_C=25).rate is None

    ind_ex = dict(MODELS['国产电感器'].example)
    assert evaluate('国产电感器', ind_ex | {'insulation_class': 'Y', 'hotspot_temperature_C': 90}, 'GB', ambient_temperature_C=25).rate > 0
    assert evaluate('国产电感器', ind_ex | {'insulation_class': 'Y', 'hotspot_temperature_C': 95}, 'GB', ambient_temperature_C=25).rate is None

    xfmr_ex = dict(MODELS['国产变压器'].example)
    assert evaluate('国产变压器', xfmr_ex | {'insulation_class': 'H', 'hotspot_temperature_C': 180}, 'GB', ambient_temperature_C=25).rate > 0
    assert evaluate('国产变压器', xfmr_ex | {'insulation_class': 'H', 'hotspot_temperature_C': 185}, 'GB', ambient_temperature_C=25).rate is None

    nonop_res_small = {'kind': '碳膜(功率 P ≤2W)', 'quality': 'B2', 'rated_power_W': 2, 'cycle_rate': 1}
    assert evaluate('国产电阻器（非工作详细）', nonop_res_small, 'GB', ambient_temperature_C=25).rate > 0
    assert evaluate('国产电阻器（非工作详细）', nonop_res_small | {'rated_power_W': 2.5}, 'GB', ambient_temperature_C=25).rate is None
    nonop_res_large = {'kind': '功率线绕电阻器', 'quality': 'B2', 'rated_power_W': 2, 'cycle_rate': 1}
    assert evaluate('国产电阻器（非工作详细）', nonop_res_large, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产电阻器（非工作详细）', nonop_res_large | {'rated_power_W': 5}, 'GB', ambient_temperature_C=25).rate > 0

    # Strict validation rejects out-of-range parameters, while allow_incomplete=True still loads legacy saved projects.
    with pytest.raises(ValueError):
        validate_parameters('国产碳膜电阻器', carbon_ex | {'rated_power_W': 5.0})
    Component('R_OLD', '国产碳膜电阻器', 1, carbon_ex | {'rated_power_W': 5.0}, domestic=True).validate()


def test_composite_combobox_presets_hints_warnings_and_ceil_bins(qapp):
    # 1. Presets with (value, label_text) pairs: 国产激光器（非工作详细） output_power_mW
    laser_spec = next(f for f in MODELS['国产激光器（非工作详细）'].fields if f.key == 'output_power_mW')
    laser_box = create_field(laser_spec, '国产激光器（非工作详细）')
    assert isinstance(laser_box, CompositeComboBox)
    assert is_field_blank(laser_box)
    assert '可选或输入' in laser_box.result_label.text()
    assert laser_box.result_label.styleSheet() == ''

    # Selecting labeled preset populates numeric value cleanly into lineEdit()
    laser_box.setCurrentIndex(1)
    assert laser_box.value() == 50
    assert laser_box.lineEdit().text() == '50'
    assert '当前取值 50' in laser_box.result_label.text()

    # Invalid negative input turns result_label into warning color
    laser_box.setEditText('-5')
    assert current_colors()['warning'] in laser_box.result_label.styleSheet()
    assert '非负数' in laser_box.result_label.text()
    laser_box.close()

    # 2. ceil_choices CompositeComboBox: 进口电连接器 contacts
    contacts_spec = next(f for f in MODELS['进口电连接器'].fields if f.key == 'contacts')
    cbox = create_field(contacts_spec, '进口电连接器')
    assert isinstance(cbox, CompositeComboBox)
    cbox.setEditText('22')
    assert cbox.value() == pytest.approx(22.0)
    assert '当前 22 → 向上取档 25' in cbox.result_label.text()
    assert cbox.result_label.styleSheet() == ''

    cbox.setEditText('120')
    assert '当前 120 → 归入 ＞100 档' in cbox.result_label.text()

    # Selecting string overflow choice from dropdown + draft/restore_draft + set_number
    overflow_idx = next(i for i in range(cbox.count()) if cbox.itemData(i) == '＞100')
    cbox.setCurrentIndex(overflow_idx)
    assert cbox.value() == '＞100'
    assert '当前档位：＞100' in cbox.result_label.text()
    saved_draft = field_draft(cbox)
    cbox.set_number(4)
    assert cbox.value() == 4
    restore_field_draft(cbox, saved_draft)
    assert cbox.value() == '＞100'
    assert read_field_value(contacts_spec, cbox) == '＞100'

    # Non-integer on integer=True triggers warning
    cbox.setEditText('17.5')
    assert current_colors()['warning'] in cbox.result_label.styleSheet()
    assert '整数' in cbox.result_label.text()
    cbox.close()

    # 3. TEMP_CHOICE_KEYS CompositeComboBox
    tj_spec = next(f for f in MODELS['国产普通数字集成电路'].fields if f.key == 'junction_temperature_C')
    tbox = create_field(tj_spec, '国产普通数字集成电路')
    tbox.setEditText('47.6')
    assert '当前 47.6 ℃ → 向上取档 49 ℃' in tbox.result_label.text()
    assert tbox.result_label.styleSheet() == ''
    tbox.setEditText('180')
    assert current_colors()['warning'] in tbox.result_label.styleSheet()
    tbox.close()

    # 4. add_field_row pairs CompositeComboBox + result_label via composite_input_row
    container = QWidget()
    form = QFormLayout(container)
    row = add_field_row(form, contacts_spec, create_field(contacts_spec, '进口电连接器'))
    assert row.layout().count() == 2
    assert isinstance(composite_input_row(create_field(contacts_spec, '进口电连接器')), QWidget)
    container.close()


def test_sync_field_constraints_pure_and_composite_combos_and_batch(qapp):
    # 1a. Pure QComboBox choices_by: 国产电容网络 quality & kind
    capnet_specs = {f.key: f for f in MODELS['国产电容网络'].fields if f.scope == 'device'}
    capnet_fields = {k: create_field(s, '国产电容网络') for k, s in capnet_specs.items()}

    cmodel = capnet_fields['capacitor_model']
    cqual = capnet_fields['quality']
    set_field_value(cmodel, '国产纸和薄膜电容器')
    sync_field_constraints(capnet_fields)
    film_quals = [cqual.itemData(i) for i in range(1, cqual.count())]
    assert 'C1' in film_quals and 'A1' not in film_quals
    set_field_value(cqual, 'C1')
    assert active_choices(capnet_fields)['quality'] == 'C1'

    # Switching to 国产可变电容器 drops incompatible 'C1' and exposes 'A1'
    set_field_value(cmodel, '国产可变电容器')
    sync_field_constraints(capnet_fields)
    var_quals = [cqual.itemData(i) for i in range(1, cqual.count())]
    assert var_quals == ['A1', 'A2', 'B1', 'B2', 'C']
    assert cqual.currentData() is None

    # Valid shared choice 'B2' is preserved across switch back to 国产纸和薄膜电容器
    set_field_value(cqual, 'B2')
    set_field_value(cmodel, '国产纸和薄膜电容器')
    sync_field_constraints(capnet_fields)
    assert cqual.currentData() == 'B2'
    for w in capnet_fields.values():
        w.close()

    # 1b. CompositeComboBox ceil_choices + choices_by + limits_by: 国产模数混合集成电路 digital_complexity
    mixed_specs = {f.key: f for f in MODELS['国产模数混合集成电路'].fields if f.scope == 'device'}
    mixed_fields = {k: create_field(s, '国产模数混合集成电路') for k, s in mixed_specs.items()}

    tech = mixed_fields['digital_technology']
    gates = mixed_fields['digital_complexity']
    assert isinstance(gates, CompositeComboBox)
    set_field_value(tech, 'MOS')
    sync_field_constraints(mixed_fields)
    mos_opts = [gates.itemData(i) for i in range(1, gates.count())]
    assert 80000 in mos_opts
    set_field_value(gates, 80000)
    assert gates.value() == 80000

    # Switching to 双极型 restricts dropdown <= 60000 and warns on preserved 80000 draft
    set_field_value(tech, '双极型')
    sync_field_constraints(mixed_fields)
    bipolar_opts = [gates.itemData(i) for i in range(1, gates.count())]
    assert max(bipolar_opts) == 60000
    assert current_colors()['warning'] in gates.result_label.styleSheet()
    for w in mixed_fields.values():
        w.close()

    # 2. CompositeComboBox limits_by + choices_by: 国产电感器 hotspot_temperature_C preserves text across insulation changes
    ind_specs = {f.key: f for f in MODELS['国产电感器'].fields if f.scope == 'device'}
    ind_fields = {k: create_field(s, '国产电感器') for k, s in ind_specs.items()}
    ins = ind_fields['insulation_class']
    hs = ind_fields['hotspot_temperature_C']

    set_field_value(ins, 'Y')
    sync_field_constraints(ind_fields)
    y_presets = [hs.itemData(i) for i in range(1, hs.count())]
    assert max(y_presets) == 90
    hs.setEditText('100')
    assert current_colors()['warning'] in hs.result_label.styleSheet()
    assert '90' in hs.result_label.text()

    set_field_value(ins, 'F')
    sync_field_constraints(ind_fields)
    f_presets = [hs.itemData(i) for i in range(1, hs.count())]
    assert max(f_presets) == 155
    assert hs.lineEdit().text() == '100'
    assert math.isclose(hs.value(), 100.0)
    assert hs.result_label.styleSheet() == ''
    for w in ind_fields.values():
        w.close()

    # 3. BatchDialog preserves '多种值 · 分别保留' empty item when parent field is shared via base_params
    items = [
        Component('R1', '国产电阻器（非工作详细）', 1,
                  {'kind': '碳膜(功率 P ≤2W)', 'quality': 'B1', 'rated_power_W': 0.5, 'cycle_rate': 1}, domestic=True),
        Component('R2', '国产电阻器（非工作详细）', 1,
                  {'kind': '碳膜(功率 P ≤2W)', 'quality': 'B2', 'rated_power_W': 1.0, 'cycle_rate': 1}, domestic=True),
    ]
    bdlg = BatchDialog(items, 'GB', ambient_temperature_C=25, prediction_state='nonoperating', calculation_enabled=True)
    qual_box = bdlg.fields['quality']
    pwr_box = bdlg.fields['rated_power_W']
    assert qual_box.itemText(0) == '多种值 · 分别保留'
    assert pwr_box.itemText(0) == '多种值 · 分别保留'
    sync_field_constraints(bdlg.fields, {'kind': '碳膜(功率 P ≤2W)'})
    assert qual_box.itemText(0) == '多种值 · 分别保留'
    assert [qual_box.itemData(i) for i in range(1, qual_box.count())] == ['A2', 'B1', 'B2', 'C']
    assert [pwr_box.itemData(i) for i in range(1, pwr_box.count())] == [0.125, 0.25, 0.5, 1, 2]
    bdlg.close()

    # 4. ComponentDialog conditional CompositeComboBox row visibility hides both combo and result_label
    comp = Component('L1', '国产激光器（非工作详细）', 1, {'kind': '氦氖激光器'}, domestic=True)
    cdlg = ComponentDialog(
        comp,
        environment='GB',
        ambient_temperature_C=25,
        prediction_state='nonoperating',
        calculation_enabled=True,
    )
    assert cdlg.field_rows['output_power_mW'].isHidden()
    cdlg.fields['kind'].setCurrentIndex(cdlg.fields['kind'].findData('半导体激光器'))
    assert not cdlg.field_rows['output_power_mW'].isHidden()
    cdlg.close()


def test_capacitor_network_and_relay_switch_hybrid_ic_constraints():
    # [P1-6]: 国产电容网络 kind, rated_temperature_C, quality, capacitance_uF, temperature_C
    capnet_fields = {f.key: f for f in MODELS['国产电容网络'].fields}
    assert capnet_fields['kind'].label == '介质种类 / 可变类型'

    # Cross-type invalid kind & quality rejected at validate_parameters stage
    with pytest.raises(ValueError, match='仅可选'):
        validate_parameters('国产电容网络', {
            'capacitor_model': '国产可变电容器',
            'capacitor_count': 4,
            'kind': '聚苯乙烯，聚四氟乙烯，聚碳酸酯',
            'quality': 'B2',
        })
    with pytest.raises(ValueError, match='仅可选'):
        validate_parameters('国产电容网络', {
            'capacitor_model': '国产可变电容器',
            'capacitor_count': 4,
            'kind': '瓷介微调',
            'quality': 'C1',
        })
    validate_parameters('国产电容网络', {
        'capacitor_model': '国产可变电容器',
        'capacitor_count': 4,
        'kind': '瓷介微调',
        'quality': 'A1',
    })

    # Wrong rated_temperature_C for mica capacitor rejected
    with pytest.raises(ValueError, match='仅可选'):
        validate_parameters('国产电容网络', {
            'capacitor_model': '国产云母电容器',
            'capacitor_count': 2,
            'capacitance_uF': 0.01,
            'rated_temperature_C': 125,
            'mounting': '有引线',
            'quality': 'B2',
            'stress_ratio': 0.5,
        })

    # Class 1 ceramic network enforces <= 85 ℃ ambient temperature
    c1_net = {
        'capacitor_model': '国产1类瓷介电容器',
        'capacitor_count': 4,
        'capacitance_uF': 0.01,
        'mounting': '片式',
        'quality': 'B2',
        'stress_ratio': 0.5,
    }
    assert evaluate('国产电容网络', c1_net, 'GB', ambient_temperature_C=85).rate > 0
    over_85 = evaluate('国产电容网络', c1_net, 'GB', ambient_temperature_C=90)
    assert over_85.rate is None and '85' in over_85.error

    # [P2-5]: 国产氧化铌电解电容器 (standalone & inside 国产电容网络) requires C > 3.3 μF
    nb_ex = dict(MODELS['国产氧化铌电解电容器'].example)
    with pytest.raises(ValueError, match='3.3'):
        validate_parameters('国产氧化铌电解电容器', nb_ex | {'capacitance_uF': 3.3})
    validate_parameters('国产氧化铌电解电容器', nb_ex | {'capacitance_uF': 4.7})
    with pytest.raises(ValueError, match='3.3'):
        validate_parameters('国产电容网络', {
            'capacitor_model': '国产氧化铌电解电容器',
            'capacitor_count': 2,
            'capacitance_uF': 2.2,
            'mounting': '片式',
            'quality': 'B2',
            'stress_ratio': 0.5,
            'series_resistance_ohm': 2.0,
            'operating_voltage_V': 10.0,
        })

    # [P2-6]: 国产功率线绕电阻器 stress_max == 0.9
    pw_ex = dict(MODELS['国产功率线绕电阻器'].example)
    with pytest.raises(ValueError, match='0.9'):
        validate_parameters('国产功率线绕电阻器', pw_ex | {'stress_ratio': 1.0})
    validate_parameters('国产功率线绕电阻器', pw_ex | {'stress_ratio': 0.9})

    # [P2-7]: 国产压电陶瓷介电石英晶体和机械滤波器 f <= 0.2 MHz preset & hint say 0.90 (not 1.0)
    flt_freq = next(f for f in MODELS['国产压电陶瓷介电石英晶体和机械滤波器'].fields if f.key == 'frequency_MHz')
    assert '0.90' in flt_freq.constraint_hint and '取 1.0' not in flt_freq.constraint_hint
    assert any('0.90' in str(item) for item in flt_freq.presets)

    # [P2-2]: 国产机电继电器 & 国产开关 inductive/lamp stress_ratio limits_by
    relay_ex = dict(MODELS['国产机电继电器'].example)
    with pytest.raises(ValueError, match='0.7'):
        validate_parameters('国产机电继电器', relay_ex | {'load_type': '感性', 'stress_ratio': 0.8})
    validate_parameters('国产机电继电器', relay_ex | {'load_type': '感性', 'stress_ratio': 0.7})
    validate_parameters('国产机电继电器', relay_ex | {'load_type': '阻性', 'stress_ratio': 1.0})

    sw_ex = dict(MODELS['国产开关'].example)
    with pytest.raises(ValueError, match='0.7'):
        validate_parameters('国产开关', sw_ex | {'load_type': '感性', 'stress_ratio': 0.75})
    with pytest.raises(ValueError, match='0.4'):
        validate_parameters('国产开关', sw_ex | {'load_type': '灯', 'stress_ratio': 0.5})
    validate_parameters('国产开关', sw_ex | {'load_type': '灯', 'stress_ratio': 0.4})

    # [P2-3]: 进口混合集成电路 hybrid_temperature_C <= 125 ℃ on case temp & negative temp support
    imp_hyb = dict(MODELS['进口混合集成电路'].example)
    with pytest.raises(ValueError, match='125'):
        validate_parameters('进口混合集成电路', imp_hyb | {'temperature_basis': '封装底座温度', 'hybrid_temperature_C': 130})
    validate_parameters('进口混合集成电路', imp_hyb | {'temperature_basis': '封装内最高温度', 'hybrid_temperature_C': 170})
    validate_parameters('进口混合集成电路', imp_hyb | {'temperature_basis': '封装底座温度', 'hybrid_temperature_C': -25})


def test_bipolar_ic_complexity_nonop_quality_uncalculable_and_record_validation():
    # [P2-4]: All 6 monolithic IC models enforce bipolar vs MOS complexity upper bounds
    bipolar_limits = [
        ('国产普通数字集成电路', 'technology', 'gate_count', 60000, 80000),
        ('进口通用数字集成电路', 'technology', 'complexity', 100000, 300000),
        ('进口模数混合集成电路', 'digital_technology', 'digital_complexity', 100000, 300000),
        ('进口微处理器', 'technology', 'transistor_count', 90000, 100000),
        ('进口静态随机存储器（SRAM）', 'technology', 'bit_count', 268435456, 536870912),
        ('进口ROM、PROM、UVEPROM、EEPROM、FLASH', 'technology', 'complexity', 16777216, 33554432),
    ]
    for model, tech_key, comp_key, ok_val, over_val in bipolar_limits:
        ex = dict(MODELS[model].example)
        validate_parameters(model, ex | {tech_key: '双极型', comp_key: ok_val})
        validate_parameters(model, ex | {tech_key: 'MOS', comp_key: over_val})
        with pytest.raises(ValueError, match=str(ok_val)):
            validate_parameters(model, ex | {tech_key: '双极型', comp_key: over_val})

    # [P2-8]: Nonoperating diode (B.21), transistor (B.26), potentiometer (B.49) quality choices_by
    diode_ex = dict(MODELS['国产二极管（非工作详细）'].example)
    with pytest.raises(ValueError, match='仅可选'):
        validate_parameters('国产二极管（非工作详细）', diode_ex | {'kind': '微波二极管 / 微波硅检波二极管', 'quality': 'A2'})
    validate_parameters('国产二极管（非工作详细）', diode_ex | {'kind': '微波二极管 / 微波硅检波二极管', 'quality': 'A3'})

    trans_ex = dict(MODELS['国产晶体管（非工作详细）'].example)
    with pytest.raises(ValueError, match='仅可选'):
        validate_parameters('国产晶体管（非工作详细）', trans_ex | {'kind': '硅场效应晶体管', 'quality': 'A1'})
    with pytest.raises(ValueError, match='仅可选'):
        validate_parameters('国产晶体管（非工作详细）', {'kind': '单结晶体管', 'quality': 'A2', 'cycle_rate': 1})
    validate_parameters('国产晶体管（非工作详细）', {'kind': '单结晶体管', 'quality': 'A3', 'cycle_rate': 1})

    pot_nonop = dict(MODELS['国产电位器（非工作详细）'].example)
    with pytest.raises(ValueError, match='仅可选'):
        validate_parameters('国产电位器（非工作详细）', pot_nonop | {'kind': '合成碳膜', 'quality': 'A1M'})
    validate_parameters('国产电位器（非工作详细）', pot_nonop | {'kind': '合成碳膜', 'quality': 'A2'})

    # [P2-9]: 国产感性元件（工作计数） restricted_kinds & uncalculable options rejected at validate_parameters
    with pytest.raises(ValueError, match='表 377'):
        validate_parameters('国产感性元件（工作计数）', {'kind': '变压器 / 小功率脉冲变压器'})
    # Quality field must be inactive on restricted kind
    ind_cnt_fields = [f for f in MODELS['国产感性元件（工作计数）'].fields if f.key != 'kind']
    assert all(not f.active({'kind': '变压器 / 小功率脉冲变压器'}) for f in ind_cnt_fields)

    with pytest.raises(ValueError, match='表 C.126'):
        validate_parameters('进口电阻器（工作计数）', {'kind': '电阻网络 / 每个分立元件（N=10）'})
    with pytest.raises(ValueError, match='表 C.63'):
        validate_parameters('进口电容器', dict(MODELS['进口电容器'].example, kind='纸介'))
    with pytest.raises(ValueError, match='表 C.63'):
        validate_parameters('进口电容网络', {'kind': '瓷介微调', 'capacitor_count': 4, 'quality': 'M', 'stress_ratio': 0.5})
    with pytest.raises(ValueError, match='表 204'):
        validate_parameters('国产纸和薄膜电容器', dict(MODELS['国产纸和薄膜电容器'].example, kind='纸介，金属化纸介'))
    with pytest.raises(ValueError, match='表 B.52'):
        validate_parameters('国产电容器（非工作详细）', dict(MODELS['国产电容器（非工作详细）'].example, kind='纸和薄膜电容器 / 纸-膜复合介质'))
    with pytest.raises(ValueError, match='维护'):
        validate_parameters('国产普通电动机和风机', dict(MODELS['国产普通电动机和风机'].example, maintenance='不满足维护要求'))
    # Legacy projects still load cleanly with allow_incomplete=True
    validate_parameters('国产纸和薄膜电容器', dict(MODELS['国产纸和薄膜电容器'].example, kind='纸介，金属化纸介'), allow_incomplete=True)

    # [P2-11] & [P2-40]: Inactive field type check, record_fields stress_variables & blank {} rejection
    with pytest.raises(ValueError):
        validate_parameters('国产光电子器件', {'kind': '发光二极管', 'quality': 'B2', 'digit_count': ['invalid']})
    # Valid inactive scalar value remains accepted
    validate_parameters('国产开关', {
        'switch_type': '微动式', 'quality': 'A1', 'active_contacts': 2,
        'load_type': '阻性', 'stress_ratio': 0.2, 'operations_per_day': 5,
    })

    # Blank {} record rejected in both strict and allow_incomplete modes
    with pytest.raises(ValueError, match='不能为空'):
        validate_parameters('国产SMT互连', {'connections': [{}]})
    with pytest.raises(ValueError, match='不能为空'):
        validate_parameters('国产SMT互连', {'connections': [{}]}, allow_incomplete=True)

    # Stress variables propagate into nested record_fields
    rec_with_stress = Input(
        'rows', '子记录',
        record_fields=(Input('s', '应力比', choices=(0.2, 0.5, 1.0), stress_max=1.0),),
    )
    _validate_fields((rec_with_stress,), {'rows': [{'s': 'U / 10'}]}, stress_variables={'U': 5})
    with pytest.raises(ValueError, match='待填写变量'):
        _validate_fields((rec_with_stress,), {'rows': [{'s': 'U / 10'}]}, stress_variables={})

    # [P3-1] & [P3-2]: Integer IC axes normalized to int, discrete counts marked integer=True, stress_source annotation
    for model_name in ('国产模拟集成电路', '国产MOS EEPROM', '进口通用数字集成电路', '进口微波单片集成电路'):
        lead_f = next(f for f in MODELS[model_name].fields if f.key == 'lead_count')
        assert lead_f.integer is True and lead_f.ceil_choices is True
        assert all(isinstance(c, int) for c in lead_f.choices)
    eeprom_nc = next(f for f in MODELS['国产MOS EEPROM'].fields if f.key == 'programming_cycles')
    assert eeprom_nc.integer is True and all(isinstance(c, int) for c in eeprom_nc.choices)
    dig_ng = next(f for f in MODELS['国产普通数字集成电路'].fields if f.key == 'gate_count')
    assert dig_ng.integer is False and dig_ng.ceil_choices is True
    assert 'tuple' in str(Input.__annotations__['stress_source'])
