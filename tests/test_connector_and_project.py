"""Clause 5.13 domestic connector verification, constraints, state transfer, and project check."""

import copy
import hashlib
from pathlib import Path

import pytest

from src.domain import Component
from src.field_editor import (
    CompositeComboBox,
    create_field,
    set_field_value,
    sync_field_constraints,
)
from src.standards import MODELS, evaluate, factor_text
from src.state_transfer import translate_state_parameters
from src.storage import load_project

_BASE_LF = {
    'connector_type': '低频连接器',
    'quality': 'A1',
    'counting_unit': '单个',
    'contacts': 100,
    'socket_structure': '绞线插针',
    'mating_per_1000h': 100.0,
    'material_class': 'Ⅱ类',
    'material_max_temperature_C': 120.0,
    'temperature_method': '实际温升',
    'temperature_rise_C': 10.0,
}

_BASE_RF = {
    'connector_type': '射频同轴连接器',
    'quality': 'B2',
    'counting_unit': '一对',
    'contacts': 1,
    'socket_structure': '针孔',
    'mating_per_1000h': 0.5,
}


def test_low_frequency_connector_actual_rise_and_table_289_287_limits():
    # Class II material rated 120 ℃ at 25 + 10 = 35 ℃ (ceils to 40 ℃ -> λb = 0.0124).
    j1_fixed = evaluate('国产电连接器', _BASE_LF, 'GB', ambient_temperature_C=25)
    assert j1_fixed.error == ''
    assert j1_fixed.rate == pytest.approx(0.0124 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)
    assert '温度向上取 40 ℃ 档' in factor_text(j1_fixed)

    # Paired vs single low-frequency connector share (1.0 vs 0.5).
    paired = evaluate('国产电连接器', _BASE_LF | {'counting_unit': '一对'}, 'GB', ambient_temperature_C=25)
    assert paired.error == ''
    assert paired.rate == pytest.approx(2 * j1_fixed.rate, rel=1e-12)

    # Table 289 material limits: Class II max 120 ℃, Class I max 250 ℃ (Table 287 operating max 150 ℃).
    class2_120 = evaluate(
        '国产电连接器',
        _BASE_LF | {'material_class': 'Ⅱ类', 'material_max_temperature_C': 120.0, 'temperature_rise_C': 0.0},
        'GB',
        ambient_temperature_C=120,
    )
    assert class2_120.error == ''
    assert class2_120.rate == pytest.approx(0.1230 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)

    class2_over_289 = evaluate(
        '国产电连接器',
        _BASE_LF | {'material_class': 'Ⅱ类', 'material_max_temperature_C': 125.0},
        'GB',
        ambient_temperature_C=25,
    )
    assert class2_over_289.rate is None
    assert '120' in class2_over_289.error and '表 289' in class2_over_289.error

    class1_150 = evaluate(
        '国产电连接器',
        _BASE_LF | {'material_class': 'Ⅰ类', 'material_max_temperature_C': 250.0, 'temperature_rise_C': 0.0},
        'GB',
        ambient_temperature_C=150,
    )
    assert class1_150.error == ''
    assert class1_150.rate == pytest.approx(0.1739 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)

    class1_over_289 = evaluate(
        '国产电连接器',
        _BASE_LF | {'material_class': 'Ⅰ类', 'material_max_temperature_C': 260.0},
        'GB',
        ambient_temperature_C=25,
    )
    assert class1_over_289.rate is None
    assert '250' in class1_over_289.error and '表 289' in class1_over_289.error

    # Lower bound -55 ℃ ceils to 10 ℃, while below -55 ℃ fails cleanly.
    cold_ok = evaluate('国产电连接器', _BASE_LF | {'temperature_rise_C': 0.0}, 'GB', ambient_temperature_C=-55)
    assert cold_ok.error == ''
    assert cold_ok.rate == pytest.approx(0.0053 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)

    too_cold = evaluate('国产电连接器', _BASE_LF | {'temperature_rise_C': 0.0}, 'GB', ambient_temperature_C=-60)
    assert too_cold.rate is None and '−55 ℃' in too_cold.error

    # Operating temperature exceeding material_max_temperature_C or Table 287 150 ℃ limit fails cleanly.
    over_mat = evaluate('国产电连接器', _BASE_LF, 'GB', ambient_temperature_C=115)
    assert over_mat.rate is None and '最高允许温度' in over_mat.error

    over_class1_287 = evaluate(
        '国产电连接器',
        _BASE_LF | {'material_class': 'Ⅰ类', 'material_max_temperature_C': 250.0, 'temperature_rise_C': 1.0},
        'GB',
        ambient_temperature_C=150,
    )
    assert over_class1_287.rate is None and '超出表列上限 150 ℃' in over_class1_287.error


def test_table_288_current_lookup_and_table_292_contacts_ceiling():
    base = {
        key: val
        for key, val in _BASE_LF.items()
        if key != 'temperature_rise_C'
    } | {'temperature_method': '按接触件电流查表', 'contact_diameter': '1'}

    # Zero current produces zero rise.
    zero_i = evaluate('国产电连接器', base | {'contact_current_A': 0}, 'GB', ambient_temperature_C=25)
    assert zero_i.error == ''
    assert zero_i.rate == pytest.approx(0.0094 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)
    assert '电流为 0 A' in factor_text(zero_i)

    # Exact current row (5 A, 1 mm -> ΔT = 12.6 ℃ -> operating 37.6 ℃ -> ceils to 40 ℃).
    exact_i = evaluate('国产电连接器', base | {'contact_current_A': 5}, 'GB', ambient_temperature_C=25)
    assert exact_i.error == ''
    assert exact_i.rate == pytest.approx(0.0124 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)
    assert '电流向上取' not in factor_text(exact_i)

    # Intermediate currents ceil up to next row in Table 288:
    # 1.5 A -> 2 A (ΔT = 2.3 ℃, 25+2.3=27.3 -> 30 ℃ -> 0.0094).
    ceil_low = evaluate('国产电连接器', base | {'contact_current_A': 1.5}, 'GB', ambient_temperature_C=25)
    assert ceil_low.error == ''
    assert ceil_low.rate == pytest.approx(0.0094 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)
    assert '电流向上取 2 A 档' in factor_text(ceil_low)

    # 12 A -> 15 A for 1.5 mm (ΔT = 41.1 ℃, 25+41.1=66.1 -> 70 ℃ -> 0.0285).
    ceil_mid = evaluate(
        '国产电连接器',
        base | {'contact_current_A': 12, 'contact_diameter': '1.5'},
        'GB',
        ambient_temperature_C=25,
    )
    assert ceil_mid.error == ''
    assert ceil_mid.rate == pytest.approx(0.0285 * 1.0 * 0.2 * 4.86 * 3.0 * 0.1 * 0.5, rel=1e-12)
    assert '电流向上取 15 A 档' in factor_text(ceil_mid)

    # Reject > 80 A and '—' cells (both direct and ceiled).
    over_i = evaluate(
        '国产电连接器',
        base | {'contact_current_A': 85, 'contact_diameter': '≥6.5'},
        'GB',
        ambient_temperature_C=25,
    )
    assert over_i.rate is None and '80' in over_i.error

    dash_exact = evaluate(
        '国产电连接器',
        base | {'contact_current_A': 10, 'contact_diameter': '≤0.6'},
        'GB',
        ambient_temperature_C=25,
    )
    assert dash_exact.rate is None and '—' in dash_exact.error

    dash_ceil = evaluate(
        '国产电连接器',
        base | {'contact_current_A': 12, 'contact_diameter': '0.8'},
        'GB',
        ambient_temperature_C=25,
    )
    assert dash_ceil.rate is None and '—' in dash_ceil.error

    # Table 292 non-tabulated contact count ceils upward (24 -> 25 -> πP = 3.29).
    contacts_24 = evaluate('国产电连接器', _BASE_LF | {'contacts': 24}, 'GB', ambient_temperature_C=25)
    contacts_25 = evaluate('国产电连接器', _BASE_LF | {'contacts': 25}, 'GB', ambient_temperature_C=25)
    assert contacts_24.error == ''
    assert contacts_24.rate == pytest.approx(contacts_25.rate, rel=1e-12)
    assert '接触件数向上取 25 档' in factor_text(contacts_24)

    over_contacts = evaluate('国产电连接器', _BASE_LF | {'contacts': 4001}, 'GB', ambient_temperature_C=25)
    assert over_contacts.rate is None and '4000' in over_contacts.error


def test_rf_coaxial_connector_and_filtered_counting_unit():
    pair = evaluate('国产电连接器', _BASE_RF, 'GB', ambient_temperature_C=25)
    plug = evaluate('国产电连接器', _BASE_RF | {'counting_unit': '插头'}, 'GB', ambient_temperature_C=25)
    socket = evaluate('国产电连接器', _BASE_RF | {'counting_unit': '插座'}, 'GB', ambient_temperature_C=25)

    assert pair.error == ''
    assert pair.rate == pytest.approx(0.00052, rel=1e-12)
    assert plug.error == ''
    assert plug.rate == pytest.approx(0.4 * pair.rate, rel=1e-12)
    assert socket.error == ''
    assert socket.rate == pytest.approx(0.6 * pair.rate, rel=1e-12)
    assert plug.rate + socket.rate == pytest.approx(pair.rate, rel=1e-12)

    bad_rf = evaluate('国产电连接器', _BASE_RF | {'counting_unit': '单个'}, 'GB', ambient_temperature_C=25)
    assert bad_rf.rate is None and '仅可选' in bad_rf.error and '插头' in bad_rf.error

    bad_lf = evaluate('国产电连接器', _BASE_LF | {'counting_unit': '插头'}, 'GB', ambient_temperature_C=25)
    assert bad_lf.rate is None and '仅可选' in bad_lf.error and '一对、单个' in bad_lf.error


def test_domestic_other_constraints_and_composite_widgets(qapp):
    # Optical connector cores ceiling: 3 -> 4 (0.072), 9 -> N≥10 (0.15), 12 -> N≥10 (0.15).
    opt_base = {'transmission_mode': '单模', 'end_face': 'PC', 'coupling': 'SC', 'quality': 'B'}
    opt_3 = evaluate('国产光纤连接器', opt_base | {'cores': 3}, 'GB', ambient_temperature_C=25)
    opt_4 = evaluate('国产光纤连接器', opt_base | {'cores': 4}, 'GB', ambient_temperature_C=25)
    opt_9 = evaluate('国产光纤连接器', opt_base | {'cores': 9}, 'GB', ambient_temperature_C=25)
    opt_12 = evaluate('国产光纤连接器', opt_base | {'cores': 12}, 'GB', ambient_temperature_C=25)
    assert opt_3.error == '' and opt_3.rate == pytest.approx(opt_4.rate, rel=1e-12)
    assert '芯数向上取 4 档' in factor_text(opt_3)
    assert opt_9.error == '' and opt_9.rate == pytest.approx(0.756, rel=1e-12)
    assert '芯数向上取 N≥10 档' in factor_text(opt_9)
    assert opt_12.error == '' and opt_12.rate == pytest.approx(opt_9.rate, rel=1e-12)

    # Motor / lamp / laser declarative constraints.
    fan_rect = evaluate(
        '国产普通电动机和风机',
        dict(MODELS['国产普通电动机和风机'].example, motor_type='风机', drive_type='整流式'),
        'GB',
        ambient_temperature_C=25,
    )
    assert fan_rect.rate is None and '仅可选：按转速' in fan_rect.error

    motor_low_ins = evaluate(
        '国产普通电动机和风机',
        dict(MODELS['国产普通电动机和风机'].example, insulation_temperature_C=100),
        'GB',
        ambient_temperature_C=25,
    )
    assert motor_low_ins.rate is None and '105' in motor_low_ins.error

    lamp_bad = evaluate(
        '国产灯',
        dict(MODELS['国产灯'].example, utilization=1.2),
        'GB',
        ambient_temperature_C=25,
    )
    assert lamp_bad.rate is None and '1' in lamp_bad.error

    laser_bad = evaluate(
        '国产激光器',
        dict(MODELS['国产激光器'].example, tube_current_mA=5),
        'GB',
        ambient_temperature_C=25,
    )
    assert laser_bad.rate is None and '10' in laser_bad.error

    # CompositeComboBox + sync_field_constraints live filtering and warning feedback.
    fields = {f.key: create_field(f, '国产电连接器') for f in MODELS['国产电连接器'].fields if f.scope == 'device'}
    assert isinstance(fields['material_max_temperature_C'], CompositeComboBox)
    assert isinstance(fields['contacts'], CompositeComboBox)
    assert isinstance(fields['contact_current_A'], CompositeComboBox)
    assert isinstance(fields['mating_per_1000h'], CompositeComboBox)

    set_field_value(fields['connector_type'], '低频连接器')
    set_field_value(fields['material_class'], 'Ⅱ类')
    set_field_value(fields['material_max_temperature_C'], 125.0)
    set_field_value(fields['contacts'], 24)
    set_field_value(fields['contact_current_A'], 1.5)
    sync_field_constraints(fields)

    assert [fields['counting_unit'].itemData(i) for i in range(1, fields['counting_unit'].count())] == ['一对', '单个']
    assert [fields['material_max_temperature_C'].itemData(i) for i in range(1, fields['material_max_temperature_C'].count())] == [120, 100, 90, 80]
    assert '120' in fields['material_max_temperature_C'].result_label.text()
    assert '向上取档 25' in fields['contacts'].result_label.text()
    assert '向上取档 2' in fields['contact_current_A'].result_label.text()

    set_field_value(fields['connector_type'], '射频同轴连接器')
    sync_field_constraints(fields)
    assert [fields['counting_unit'].itemData(i) for i in range(1, fields['counting_unit'].count())] == ['一对', '插头', '插座']


def test_connector_state_transfer_and_parameter_reuse():
    # Working RF coax -> Nonoperating detailed maps unambiguously and evaluates immediately.
    rf = Component('J_RF', '国产电连接器', 1, dict(_BASE_RF, quality='A2', mating_per_1000h=5), domestic=True)
    orig_rf_rate = rf.evaluate('GB', 25).rate
    rf.switch_state('nonoperating', auto_suggest=True)
    assert rf.model == '国产电连接器（非工作详细）'
    assert rf.parameters == {'kind': '射频同轴连接器、圆形连接器', 'quality': 'A2'}
    nonop_ev = rf.evaluate('GB', 25)
    assert nonop_ev.error == '' and nonop_ev.rate == pytest.approx(0.00035 * 1.0 * 0.45, rel=1e-12)
    rf.switch_state('working', auto_suggest=True)
    assert rf.model == '国产电连接器'
    assert rf.evaluate('GB', 25).rate == pytest.approx(orig_rf_rate, rel=1e-12)

    # Working low-freq (with current lookup) -> Nonoperating keeps quality only without guessing circular vs rect/PCB.
    lf_params = {
        k: v for k, v in _BASE_LF.items() if k != 'temperature_rise_C'
    } | {'temperature_method': '按接触件电流查表', 'contact_current_A': 5, 'contact_diameter': '1'}
    lf = Component('J_LF', '国产电连接器', 1, lf_params, domestic=True)
    lf.switch_state('nonoperating', auto_suggest=True)
    assert lf.model == '国产电连接器（非工作详细）'
    assert lf.parameters == {'quality': 'A1'}
    lf.switch_state('working', auto_suggest=True)
    assert lf.parameters == lf_params

    # Nonoperating -> Working: rectangular/PCB is unambiguously low-freq; RF/circular stays unset.
    rect_params, rect_sources = translate_state_parameters(
        '国产电连接器（非工作详细）',
        {'kind': '矩形连接器、印制电路板用连接器', 'quality': 'B1'},
        {},
        '国产电连接器',
    )
    assert rect_params == {'connector_type': '低频连接器', 'quality': 'B1'}
    assert rect_sources == {}

    ambig_params, ambig_sources = translate_state_parameters(
        '国产电连接器（非工作详细）',
        {'kind': '射频同轴连接器、圆形连接器', 'quality': 'B1'},
        {},
        '国产电连接器',
    )
    assert ambig_params == {'quality': 'B1'}
    assert ambig_sources == {}


def test_real_project_json_j1_table_289_error_and_fixed_copy_calculation():
    project_path = Path('/home/wang/Downloads/预计工程.json')
    assert project_path.is_file()
    digest_before = hashlib.sha256(project_path.read_bytes()).hexdigest()

    project, _revision = load_project(project_path)
    project.validate()
    assert len(project.components) == 20
    condition = project.conditions[0]
    assert condition.environment == 'GB' and condition.ambient_temperature_C == 25

    # As saved, J1 has Class II with 125.0 ℃, which triggers the clear Table 289 120 ℃ error.
    context_raw = project.calculate(condition)
    assert context_raw.pending_count == 1
    assert context_raw.total_rate is None
    j1_raw = next(comp for comp in project.components if comp.name == 'J1')
    j1_res = context_raw.evaluate(j1_raw)
    assert j1_res.rate is None
    assert '120' in j1_res.error and '表 289' in j1_res.error

    # Fixing a copy of J1 to a valid Class II preset (120 ℃) lets all 20 components compute cleanly.
    fixed_project = copy.deepcopy(project)
    j1_fixed = next(comp for comp in fixed_project.components if comp.name == 'J1')
    j1_fixed.parameters['material_max_temperature_C'] = 120.0
    context_fixed = fixed_project.calculate(condition)
    assert context_fixed.error == ''
    assert context_fixed.pending_count == 0
    assert context_fixed.total_rate == pytest.approx(0.08649432, rel=1e-12)
    for component in fixed_project.components:
        result = context_fixed.evaluate(component)
        assert result.error == '', (component.name, result.error)
        assert result.rate is not None and result.rate > 0, component.name
    assert context_fixed.evaluate(j1_fixed).rate == pytest.approx(0.00180792, rel=1e-12)

    assert hashlib.sha256(project_path.read_bytes()).hexdigest() == digest_before
