"""Verification across the 16 migrated baseline models and representative standard families."""

import math

import pytest

from src.standards import evaluate

BASELINE_CASES = [
    ('国产焊接点', {'kind': '再流焊', 'quality': 'A'}, 'GB', 25),
    ('国产印制板', {'plated_hole_count': 100, 'layer_count': 2, 'quality': 'A1'}, 'GB', 25),
    (
        '国产硅场效应晶体管',
        {
            'kind': 'MOS 型',
            'structure': '单管',
            'application': '线性放大',
            'quality': 'A2',
            'rated_power_W': 1.0,
            'rated_temperature_C': 25,
            'max_junction_temperature_C': 150,
            'temperature_basis': '工程环境温度',
            'operating_power_W': 0.2,
        },
        'GB',
        25,
    ),
    (
        '进口模拟集成电路',
        {
            'transistor_count': 50,
            'package': '密封－双列直插（DIP）封装',
            'lead_count': 14,
            'quality': 'B',
            'junction_temperature_C': 25,
        },
        'GB',
        25,
    ),
    ('国产片式膜电阻器', {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'}, 'GB', 25),
    (
        '国产1类瓷介电容器',
        {'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
        'GB',
        25,
    ),
    (
        '国产2类瓷介电容器',
        {'capacitance_uF': 0.1, 'rated_temperature_C': 85, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
        'GB',
        25,
    ),
    (
        '国产普通数字集成电路',
        {
            'technology': 'MOS',
            'gate_count': 200,
            'package': '密封-双列直插(DIP)封装',
            'lead_count': 16,
            'quality': 'A3',
            'maturity': '稳定生产',
            'junction_temperature_C': 45,
            'supply_voltage_V': 5.0,
        },
        'GB',
        25,
    ),
    (
        '进口电阻器',
        {'kind': '普通膜（≤1 W）', 'resistance_ohm': 1000.0, 'quality': 'S', 'stress_ratio': 0.3},
        'GB',
        30,
    ),
    (
        '进口电容器',
        {'kind': '瓷介', 'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'S', 'stress_ratio': 0.4},
        'GB',
        30,
    ),
    ('进口电感器', {'kind': '固定电感器', 'quality': 'S'}, 'GB', 30),
    (
        '进口普通二极管',
        {'kind': '整流二极管', 'quality': 'JANS', 'voltage_ratio': 0.4, 'current_ratio': 0.4},
        'GB',
        30,
    ),
    (
        '进口双极型晶体管',
        {'kind': '开关晶体管', 'quality': 'JANS', 'voltage_ratio': 0.4, 'power_ratio': 0.3},
        'GB',
        30,
    ),
    (
        '进口其他分立半导体',
        {'kind': '电压调整／电压基准／电流调整二极管', 'quality': 'JANS', 'stress_ratio': 0.4},
        'GB',
        30,
    ),
    (
        '进口微处理器',
        {
            'technology': 'MOS',
            'transistor_count': 10000,
            'package': '密封－双列直插（DIP）封装',
            'lead_count': 40,
            'quality': 'B',
            'junction_temperature_C': 25,
        },
        'GB',
        25,
    ),
    (
        '进口静态随机存储器（SRAM）',
        {
            'technology': 'MOS',
            'bit_count': 65536,
            'package': '密封－双列直插（DIP）封装',
            'lead_count': 24,
            'quality': 'B',
            'junction_temperature_C': 25,
        },
        'GB',
        25,
    ),
]


@pytest.mark.parametrize(('model', 'params', 'env', 'temp'), BASELINE_CASES, ids=[c[0] for c in BASELINE_CASES])
def test_migrated_baseline_models_evaluate_deterministically(model, params, env, temp):
    first = evaluate(model, params, env, ambient_temperature_C=temp)
    second = evaluate(model, params, env, ambient_temperature_C=temp)
    assert first.error == ''
    assert first.rate is not None and math.isfinite(first.rate) and first.rate > 0
    assert first.factors
    assert first == second


def test_dual_transistor_stress_and_fet_derived_stress():
    result = evaluate(
        '国产双极型双管',
        {
            'kind': '开关晶体管',
            'polarity': 'NPN 型',
            'structure': '双管 (匹配对)',
            'application': '线性放大',
            'quality': 'A2',
            'temperature_basis': '工作环境温度',
            'package_rated_power_W': 1.5,
            'rated_power_1_W': 1.0,
            'rated_power_2_W': 1.0,
            'rated_temperature_C': 25,
            'max_junction_temperature_C': 175,
            'operating_power_1_W': 0.2,
            'operating_power_2_W': 0.2,
            'voltage_ratio_1': 0.3,
            'voltage_ratio_2': 0.3,
        },
        'GB',
        ambient_temperature_C=25,
    )
    assert result.error == ''
    assert result.rate is not None and result.rate > 0
    assert result.stress_adjustments


def test_nonoperating_detailed_and_parts_count_families():
    res_nonop = evaluate(
        '国产电阻器（非工作详细）',
        {'kind': '金属膜', 'quality': 'A1P', 'rated_power_W': 0.25, 'cycle_rate': 10},
        'GB',
        ambient_temperature_C=25,
    )
    assert res_nonop.error == '' and res_nonop.rate and res_nonop.rate > 0

    res_count = evaluate(
        '国产电阻器（工作计数）',
        {'kind': '膜电阻器 / 金属膜', 'group1_quality': 'A1P'},
        'GF1',
        ambient_temperature_C=25,
    )
    assert res_count.error == '' and res_count.rate and res_count.rate > 0

    from src.domain import _suggest_state_model
    from src.models.nonoperating_common import axis_value
    from src.standards import MODELS

    cap_invalid = evaluate(
        '国产电容器（非工作详细）',
        MODELS['国产电容器（非工作详细）']['example'] | {'cycle_rate': 15},
        'GB',
        ambient_temperature_C=25,
    )
    assert cap_invalid.rate is None
    assert '可选：＜0.3、1、2、3、4、5、10、20、50' in cap_invalid.error
    assert '不插值' not in cap_invalid.error and '外推' not in cap_invalid.error

    cap_lt = evaluate(
        '国产电容器（非工作详细）',
        MODELS['国产电容器（非工作详细）']['example'] | {'cycle_rate': '＜0.3'},
        'GB',
        ambient_temperature_C=25,
    )
    assert cap_lt.error == '' and cap_lt.rate and cap_lt.rate > 0

    with pytest.raises(ValueError, match='表 B.57 无匹配取值') as exc_info:
        axis_value(57, 15)
    assert '可选：' in str(exc_info.value)

    assert _suggest_state_model('国产双极型双管', True, 'nonoperating') == '待配置'
    assert _suggest_state_model('国产硅场效应双管', True, 'nonoperating') == '待配置'
    assert _suggest_state_model('国产模拟集成电路（非工作详细）', True, 'working') == '国产模拟集成电路'
    assert _suggest_state_model('国产数字集成电路（非工作详细）', True, 'working') == '国产普通数字集成电路'
    assert _suggest_state_model(
        '国产电容器（非工作详细）', True, 'working', {'kind': '瓷介电容器 / 1 类瓷介'}
    ) == '国产1类瓷介电容器'
    assert _suggest_state_model('国产电容器（非工作详细）', True, 'working', {}) == '待配置'
    assert _suggest_state_model(
        '国产MOS动态随机存储器（工作计数）', True, 'nonoperating', {'kind': '1～320 / 密封'}
    ) == '国产密封存储器（非工作计数）'
    assert _suggest_state_model('国产MOS动态随机存储器（工作计数）', True, 'nonoperating', {}) == '待配置'
    assert _suggest_state_model(
        '国产真空电子器件（非工作计数）', True, 'working', {'kind': '空间电荷控制电子管 / 整流管'}
    ) == '待配置'


def test_numerical_accuracy_cell_id_stripping_and_validation_guards():
    from src.standards import (
        AUTOMATIC_MODELS,
        MODELS,
        factor_html,
        factor_text,
        validate_parameters,
    )

    # Exact product checks against GJB/Z 299D-2024 standard lookup factors.
    r_chip = evaluate('国产片式膜电阻器', {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'}, 'GB', ambient_temperature_C=25)
    assert r_chip.rate == pytest.approx(0.0003, rel=1e-12)

    c_ceramic = evaluate('国产1类瓷介电容器', {'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4}, 'GB', ambient_temperature_C=25)
    assert c_ceramic.rate == pytest.approx(4.719e-5, rel=1e-12)

    r_nonop = evaluate('国产电阻器（非工作详细）', {'kind': '金属膜', 'quality': 'A1P', 'rated_power_W': 0.25, 'cycle_rate': 10}, 'GB', ambient_temperature_C=25)
    assert r_nonop.rate == pytest.approx(1.63e-5, rel=1e-12)

    r_cnt = evaluate('国产电阻器（工作计数）', {'kind': '膜电阻器 / 金属膜', 'group1_quality': 'A1P'}, 'GF1', ambient_temperature_C=25)
    assert r_cnt.rate == pytest.approx(0.00011, rel=1e-12)

    r_imp = evaluate('进口电阻器', {'kind': '普通膜（≤1 W）', 'resistance_ohm': 1000.0, 'quality': 'S', 'stress_ratio': 0.3}, 'GB', ambient_temperature_C=30)
    assert r_imp.rate == pytest.approx(1.728e-6, rel=1e-12)

    # All non-limitation automatic models evaluate cleanly on their own examples.
    # (Skip standard-limitation models: 进口可变电容器 + 6 counting models without quality factors.)
    expected_limitations = {
        '进口可变电容器',
        '国产灯（工作计数）', '国产激光器（工作计数）', '国产压电陀螺（工作计数）',
        '国产灯（非工作计数）', '国产激光器（非工作计数）', '国产压电陀螺（非工作计数）',
    }
    for name in AUTOMATIC_MODELS:
        spec = MODELS[name]
        ev = evaluate(name, dict(spec.example), 'GB', ambient_temperature_C=40)
        if name in expected_limitations:
            assert ev.rate is None and ev.error
        else:
            assert ev.error == '' and ev.rate is not None and ev.rate > 0, (name, ev.error)

    # Raw cell_id must not leak into factor_text, while clickable link preserves highlight target.
    dig_cnt = evaluate('国产密封单片数字集成电路（工作计数）', dict(MODELS['国产密封单片数字集成电路（工作计数）'].example), 'GB', ambient_temperature_C=25)
    txt = factor_text(dig_cnt)
    html_out = factor_html(dig_cnt)
    assert 'p182-s' not in txt
    assert 'standard://table/356/p182-s' in html_out

    # Mixed IC bipolar complexity > 60000 gives explicit limit message.
    mixed_params = dict(MODELS['国产模数混合集成电路'].example, digital_technology='双极型', digital_complexity=80000.0)
    mixed_err = evaluate('国产模数混合集成电路', mixed_params, 'GB', ambient_temperature_C=25)
    assert mixed_err.rate is None and '60000' in mixed_err.error

    # Sub-absolute-zero negative temperatures and OverflowError are caught cleanly.
    with pytest.raises(ValueError, match='绝对零度'):
        validate_parameters('国产压电陀螺', dict(MODELS['国产压电陀螺'].example, source_temperature_C=-300))

    overflow_eval = evaluate(
        '国产固体继电器',
        dict(MODELS['国产固体继电器'].example, output_circuits=10000),
        'GB',
        ambient_temperature_C=25,
    )
    assert overflow_eval.rate is None and '超出有限数值范围' in overflow_eval.error and 'Numerical result' not in overflow_eval.error

    huge_int_eval = evaluate(
        '国产片式膜电阻器',
        dict(MODELS['国产片式膜电阻器'].example, resistance_ohm=10**400),
        'GB',
        ambient_temperature_C=25,
    )
    assert huge_int_eval.rate is None and '超出有限数值范围' in huge_int_eval.error


def test_state_parameter_reuse_across_domestic_models():
    import copy

    from src.domain import Component
    from src.prediction import model_state
    from src.standards import MODELS

    forbidden_cross_state = {
        'stress_ratio',
        'junction_temperature_C',
        'supply_voltage_V',
        'cycle_rate',
        'programming_cycles',
        'case_temperature_C',
        'operating_power_W',
        'output_power_W',
        'output_power_mW',
    }
    for name, spec in MODELS.items():
        if not name.startswith('国产') or name in ('待配置', '已知单件失效率', '工程模块'):
            continue
        cur_state = model_state(name)
        tgt_state = 'nonoperating' if cur_state == 'working' else 'working'
        example = copy.deepcopy(spec.get('example') or {})
        comp = Component('TEST', name, 1, copy.deepcopy(example), domestic=True)
        comp.validate()
        comp.switch_state(tgt_state, auto_suggest=True)
        comp.validate()
        assert not (forbidden_cross_state & set(comp.parameters)), f'{name} leaked stress keys: {comp.parameters}'
        comp.switch_state(cur_state, auto_suggest=True)
        comp.validate()
        assert comp.model == name
        assert comp.parameters == example

    # Resistor chip & carbon film P <= 2W vs P > 2W boundary.
    chip_r = Component(
        'R1', '国产片式膜电阻器', 1, {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'}, domestic=True
    )
    chip_r.switch_state('nonoperating', auto_suggest=True)
    assert chip_r.model == '国产电阻器（非工作详细）'
    assert chip_r.parameters == {'kind': '片式膜电阻器', 'quality': 'A1P'}

    high_p_carbon = Component(
        'R2', '国产碳膜电阻器', 1,
        {'resistance_ohm': 470.0, 'rated_power_W': 5.0, 'quality': 'A2', 'stress_ratio': 0.3},
        domestic=True,
    )
    high_p_carbon.switch_state('nonoperating', auto_suggest=True)
    assert high_p_carbon.parameters == {'quality': 'A2'}
    high_p_carbon.validate()

    # Digital IC including gate_count input_sources + tier-2 maturity + bipolar craft mapping.
    dig_ic = Component(
        'U1',
        '国产普通数字集成电路',
        1,
        {
            'technology': '双极型',
            'package': '密封-表面安装(LCC/SO和其他)封装',
            'lead_count': 16,
            'quality': 'B1',
            'maturity': '试制／新投产／重大工艺变化或复产',
            'junction_temperature_C': 35,
            'supply_voltage_V': 5,
        },
        domestic=True,
        input_sources={'gate_count': {'kind': 'gate_count', 'inputs': {'mode': 'transistors', 'technology': '双极型', 'transistors': 150}}},
    )
    dig_ic.switch_state('nonoperating', auto_suggest=True)
    assert dig_ic.model == '国产数字集成电路（非工作详细）'
    assert dig_ic.parameters['technology'] == '双极'
    assert '表面安装' in dig_ic.parameters['package']
    assert dig_ic.parameters['lead_count'] == 16
    assert dig_ic.parameters['quality'] == 'B1'
    assert dig_ic.parameters['maturity'] == MODELS['国产数字集成电路（非工作详细）'].fields[5].choices[2]
    assert 'gate_count' in dig_ic.input_sources
    assert 'junction_temperature_C' not in dig_ic.parameters
    dig_ic.validate()

    # Mixed-signal IC, MMIC, capacitor, discrete transistor, and immediate-rate nonop models.
    mixed = Component('U2', '国产模数混合集成电路', 1, copy.deepcopy(MODELS['国产模数混合集成电路'].example), domestic=True)
    mixed.switch_state('nonoperating', auto_suggest=True)
    assert mixed.model == '国产模数混合集成电路（非工作详细）'
    assert mixed.parameters['analog_sections'][0]['transistor_count'] == 50
    assert mixed.parameters['digital_sections'][0]['gate_count'] == 50
    assert mixed.parameters['digital_sections'][0]['technology'] == '双极'

    # Parameter-complete models calculate immediately after switching.
    for w_model, w_params in (
        ('国产开关', {'switch_type': '微动式', 'quality': 'A1', 'active_contacts': 2, 'load_type': '阻性', 'stress_ratio': 0.2, 'operations_per_day': 5}),
        ('国产光纤连接器', {'cores': 4, 'transmission_mode': '多模', 'end_face': 'PC', 'coupling': 'SC', 'quality': 'A2'}),
        ('国产谐振器', {'quality': 'A1'}),
        ('国产印制板', {'plated_hole_count': 240, 'layer_count': 4, 'quality': 'A2'}),
    ):
        comp = Component('P', w_model, 1, w_params, domestic=True)
        comp.switch_state('nonoperating', auto_suggest=True)
        ev = comp.evaluate('GB', 25)
        assert ev.rate is not None and ev.rate > 0, (w_model, ev.error)

    # Counting pair shifted group_quality + oscillator quality mapping.
    cap_cnt = Component('C_CNT', '国产电容器（工作计数）', 1, {'kind': '1类瓷介电容器', 'group3_quality': 'A1P'}, domestic=True)
    cap_cnt.switch_state('nonoperating', auto_suggest=True)
    assert cap_cnt.model == '国产电容器（非工作计数）'
    assert cap_cnt.parameters == {'kind': '瓷介电容器 / 1类瓷介', 'group2_quality': 'A1P'}
    assert cap_cnt.evaluate('GB', 25).rate > 0

    osc_cnt = Component('Y_CNT', '国产振荡器和谐振器（工作计数）', 1, {'kind': '普通振荡器', 'group1_quality': 'A1S'}, domestic=True)
    osc_cnt.switch_state('nonoperating', auto_suggest=True)
    assert osc_cnt.parameters == {'kind': '振荡器', 'group1_quality': 'A1'}
    assert osc_cnt.evaluate('GB', 25).rate > 0

    # Vacuum reverse pairing.
    vac = Component('V1', '国产真空电子器件（非工作详细）', 1, {'kind': '行波管', 'quality': 'A1'}, domestic=True)
    vac.switch_state('working', auto_suggest=True)
    assert vac.model == '国产行波管'
    assert vac.parameters == {'quality': 'A1'}

    # Early blank toggle before configuring does not block later auto-suggest + reuse.
    early = Component('R_EARLY', '待配置', 1, {}, domestic=True)
    early.switch_state('nonoperating', auto_suggest=True)
    assert early.model == '待配置'
    early.switch_state('working', auto_suggest=True)
    early.model = '国产金属膜电阻器'
    early.parameters = {'resistance_ohm': 100.0, 'rated_temperature_C': 70, 'quality': 'A1P', 'stress_ratio': 0.2}
    early.switch_state('nonoperating', auto_suggest=True)
    assert early.model == '国产电阻器（非工作详细）'
    assert early.parameters == {'kind': '金属膜', 'quality': 'A1P'}

    # Explicit target customization is never clobbered, while missing compatible keys still backfill.
    custom = Component('R_CUSTOM', '国产片式膜电阻器', 1, {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'B1'}, domestic=True)
    custom.switch_state('nonoperating', auto_suggest=True)
    custom.parameters = {'kind': '金属膜', 'cycle_rate': 2}
    custom.switch_state('working', auto_suggest=True)
    custom.switch_state('nonoperating', auto_suggest=True)
    assert custom.parameters['kind'] == '金属膜'
    assert custom.parameters['cycle_rate'] == 2
    assert custom.parameters['quality'] == 'B1'


def test_temperature_ceiling_across_tables_and_ic_sources():
    from src.domain import Component, OperatingCondition, Project
    from src.input_sources import evaluate_with_sources
    from src.redacted_export import build_redacted_project
    from src.standards import AUTOMATIC_MODELS, MODELS, factor_html, factor_text

    # 1. Domestic low-frequency connector (Table 287: 10, 20, 30...): 25 ℃ and 28 ℃ both ceil to 30 ℃.
    conn_ex = dict(MODELS['国产电连接器'].example)
    conn_at_30 = evaluate('国产电连接器', conn_ex, 'GB', ambient_temperature_C=30)
    conn_at_25 = evaluate('国产电连接器', conn_ex, 'GB', ambient_temperature_C=25)
    conn_rise_3 = evaluate('国产电连接器', dict(conn_ex, temperature_rise_C=3), 'GB', ambient_temperature_C=25)
    assert conn_at_25.error == '' and conn_at_25.rate == conn_at_30.rate
    assert conn_rise_3.error == '' and conn_rise_3.rate == conn_at_30.rate
    txt = factor_text(conn_at_25)
    html_out = factor_html(conn_at_25)
    assert '温度向上取 30 ℃ 档' in txt
    assert 'p149-' not in txt
    assert 'standard://table/287/p' in html_out

    # 2. Domestic tables with 10 ℃ step or skipped 50 ℃ row.
    wire_25 = evaluate('国产功率线绕电阻器', dict(MODELS['国产功率线绕电阻器'].example), 'GB', ambient_temperature_C=25)
    wire_30 = evaluate('国产功率线绕电阻器', dict(MODELS['国产功率线绕电阻器'].example), 'GB', ambient_temperature_C=30)
    assert wire_25.rate == wire_30.rate

    rf_25 = evaluate('国产微波双极型晶体管', dict(MODELS['国产微波双极型晶体管'].example), 'GB', ambient_temperature_C=25)
    rf_30 = evaluate('国产微波双极型晶体管', dict(MODELS['国产微波双极型晶体管'].example), 'GB', ambient_temperature_C=30)
    assert rf_25.rate == rf_30.rate

    ntc_50 = evaluate('国产热敏电阻器', dict(MODELS['国产热敏电阻器'].example), 'GB', ambient_temperature_C=50)
    ntc_55 = evaluate('国产热敏电阻器', dict(MODELS['国产热敏电阻器'].example), 'GB', ambient_temperature_C=55)
    assert ntc_50.rate == ntc_55.rate

    # 3. Appendix C 30 ℃-start models at default GB @ 25 ℃ ceil cleanly to 30 ℃.
    for name in (
        '进口电阻器', '进口电容器', '进口电感器', '进口普通二极管',
        '进口继电器', '进口开关', '进口电连接器', '进口变压器',
        '进口光电子器件', '进口微波半导体分立器件',
    ):
        ex = dict(MODELS[name].example)
        at_25 = evaluate(name, ex, 'GB', ambient_temperature_C=25)
        at_30 = evaluate(name, ex, 'GB', ambient_temperature_C=30)
        assert at_25.error == '' and at_25.rate == at_30.rate, (name, at_25.error)

    # 4. Non-grid IC junction temperature (both direct 47.6 ℃ and Eq. 2 auxiliary source) ceils to 49 ℃
    #    and passes redacted project export cleanly.
    ic_ex = dict(MODELS['国产普通数字集成电路'].example)
    ic_47_6 = evaluate('国产普通数字集成电路', dict(ic_ex, junction_temperature_C=47.6), 'GB', ambient_temperature_C=25)
    ic_49 = evaluate('国产普通数字集成电路', dict(ic_ex, junction_temperature_C=49), 'GB', ambient_temperature_C=25)
    assert ic_47_6.error == '' and ic_47_6.rate == ic_49.rate
    assert '温度向上取 49 ℃ 档' in factor_text(ic_47_6)

    ic_params_src = {k: v for k, v in ic_ex.items() if k != 'junction_temperature_C'}
    comp_src = Component(
        'U1',
        '国产普通数字集成电路',
        1,
        ic_params_src,
        input_sources={
            'junction_temperature_C': {
                'kind': 'junction_temperature',
                'inputs': {
                    'case_source': 'specified',
                    'case_temperature_C': 35,
                    'resistance_source': 'specified',
                    'thermal_resistance_C_W': 12.6,
                    'power_W': 1.0,
                },
            }
        },
    )
    ev_src = evaluate_with_sources(comp_src, 'GB', 25)
    assert ev_src.error == '' and ev_src.rate == ic_49.rate
    proj = Project('T', conditions=[OperatingCondition('GB25', 'GB', 25, enabled=True)], components=[comp_src])
    redacted = build_redacted_project(proj)
    assert redacted.components[0].condition_rate('GB', 25) == pytest.approx(ic_49.rate, rel=1e-12)

    # 5. Nonoperating ceiling below lower bound and intermediate tick (e.g. Table B.42 '-10 ℃' -> '0～25').
    net_neg = evaluate(
        '国产电阻网络（非工作详细）',
        dict(MODELS['国产电阻网络（非工作详细）'].example),
        'GB',
        ambient_temperature_C=-10,
    )
    net_25 = evaluate(
        '国产电阻网络（非工作详细）',
        dict(MODELS['国产电阻网络（非工作详细）'].example),
        'GB',
        ambient_temperature_C=25,
    )
    assert net_neg.error == '' and net_neg.rate == net_25.rate
    assert '温度向上取 0～25 ℃ 档' in factor_text(net_neg)

    # 6. Exceeding table maximum temperature remains rejected cleanly.
    over_res = evaluate('进口电阻器', dict(MODELS['进口电阻器'].example), 'GB', ambient_temperature_C=200)
    assert over_res.rate is None and '上限' in over_res.error
    over_ic = evaluate('国产普通数字集成电路', dict(ic_ex, junction_temperature_C=180), 'GB', ambient_temperature_C=25)
    assert over_ic.rate is None and '上限' in over_ic.error
    over_ind = evaluate('国产片式电感器', {'hotspot_temperature_C': 250, 'quality': 'B2'}, 'GB', ambient_temperature_C=25)
    assert over_ind.rate is None and '上限' in over_ind.error

    # 7. At default GB @ 25 ℃, all non-limitation automatic models succeed (except example-bound source_temperature_C=40 on 国产压电陀螺).
    expected_skips = {
        '进口可变电容器',
        '国产灯（工作计数）', '国产激光器（工作计数）', '国产压电陀螺（工作计数）',
        '国产灯（非工作计数）', '国产激光器（非工作计数）', '国产压电陀螺（非工作计数）',
        '国产压电陀螺',
    }
    for name in AUTOMATIC_MODELS:
        ev = evaluate(name, dict(MODELS[name].example), 'GB', ambient_temperature_C=25)
        if name not in expected_skips:
            assert ev.error == '' and ev.rate is not None and ev.rate > 0, (name, ev.error)


GOLDEN_VALUE_CASES = [
    # 1. Domestic Digital IC with 5.1.1.3 CMOS 200/3.75->100 gates, LQFP-32->36 pins, Tj=47.6->49℃, Vs=3.3->4V
    (
        '国产普通数字集成电路',
        {
            'technology': 'MOS',
            'gate_count': 200 / 3.75,
            'package': '非密封-表面安装(LCC/SO和其他)封装',
            'lead_count': 32,
            'quality': 'B1',
            'maturity': '稳定生产',
            'junction_temperature_C': 47.6,
            'supply_voltage_V': 3.3,
        },
        'GF1',
        40,
        0.5 * (0.0316 * 0.43 * 0.7 + (0.0039 + 0.5249) * 2.4) * 1.0,
    ),
    # 2. Domestic MOS EEPROM (ceiled complexity 2000->2048, ceiled programming_cycles 250->500, Vs=2.5->≤3)
    (
        '国产MOS EEPROM',
        {
            'complexity': 2000,
            'quality': 'B1',
            'junction_temperature_C': 55,
            'supply_voltage_V': 2.5,
            'package': '密封-表面安装(LCC/SO和其他)封装',
            'lead_count': 28,
            'maturity': '符合相应的标准或技术条件，已稳定生产的产品',
            'programming_cycles': 250,
        },
        'GB',
        25,
        0.5 * (0.092 * 0.71 * 0.62 * 1.03 + (0.0319 + 0.2944) * 1.0) * 1.0,
    ),
    # 3. Domestic MOS PROM (ceiled complexity 1500->2048 in Table 12 & Table 30)
    (
        '国产MOS PROM',
        {
            'complexity': 1500,
            'quality': 'B2',
            'junction_temperature_C': 55,
            'supply_voltage_V': 5,
            'package': '密封-双列直插(DIP)封装',
            'lead_count': 24,
            'maturity': '符合相应的标准或技术条件，已稳定生产的产品',
        },
        'GB',
        25,
        1.0 * (0.1191 * 0.71 * 0.77 * 1.02 + (0.011 + 0.0724) * 1.0) * 1.0,
    ),
    # 4. Domestic Hybrid IC (AS=4*0.0135 + 10*0.0007 + 0.03 = 0.091, πQ=0.08, πT=1.6)
    (
        '国产混合集成电路',
        {
            'quality': 'A2',
            'substrate_area_cm2': 4.0,
            'network_process': '厚膜',
            'resistor_process': '厚膜电阻',
            'film_resistor_count': 10,
            'package_process': '厚膜',
            'maturity': '符合相应的标准或技术条件，已稳定生产的产品',
            'temperature_basis': '封装底座温度',
            'hybrid_temperature_C': 55,
            'function': '数字',
        },
        'GB',
        25,
        (4.0 * 0.0135 + 10 * 0.0007 + 0.03) * 1.0 * 0.08 * 1.0 * 1.6 * 1.0,
    ),
    # 5. Domestic Ordinary Diode (I=0.5/2.0=0.25->S=0.3, T=40℃, VR/VRM=0.5)
    (
        '国产普通二极管',
        {
            'kind': '整流二极管',
            'quality': 'A3',
            'temperature_basis': '工作环境温度',
            'rated_temperature_C': 25,
            'max_junction_temperature_C': 175,
            'operating_current_A': 0.5,
            'rated_current_A': 2.0,
            'voltage_ratio': 0.5,
            'structure': '冶金键合',
        },
        'GB',
        40,
        0.0171 * 1.0 * 0.05 * 1.5 * 0.7 * 1.0,
    ),
    # 6. Domestic BJT (power BJT, S=0.1, S2=0.3, T=40℃)
    (
        '国产双极型晶体管',
        {
            'kind': '大功率晶体管',
            'quality': 'B2',
            'temperature_basis': '工作环境温度',
            'rated_temperature_C': 25,
            'max_junction_temperature_C': 175,
            'operating_power_W': 0.1,
            'rated_power_W': 1.0,
            'application': '线性放大',
            'voltage_ratio': 0.3,
            'polarity': 'NPN 型',
        },
        'GB',
        40,
        0.033 * 1.0 * 1.0 * 1.0 * 0.35 * 1.5 * 1.0 * 1.0,
    ),
    # 7. Domestic Silicon FET (POP=3/10=0.3, A3, switch, 40℃)
    (
        '国产硅场效应晶体管',
        {
            'kind': 'MOS 型',
            'structure': '单管',
            'application': '开关',
            'quality': 'A3',
            'rated_power_W': 10.0,
            'rated_temperature_C': 25,
            'max_junction_temperature_C': 175,
            'temperature_basis': '工程环境温度',
            'operating_power_W': 3.0,
        },
        'GB',
        40,
        0.098 * 1.0 * 0.05 * 0.5 * 2.2 * 1.0 * 1.0,
    ),
    # 8. Domestic Optoelectronics (LED @ GB, 40℃)
    ('国产光电子器件', {'kind': '发光二极管', 'quality': 'B2'}, 'GB', 40, 0.0014 * 1.0 * 1.0 * 1.57),
    # 9. Domestic CW Magnetron (< 5 kW)
    (
        '国产磁控管',
        {'quality': 'B', 'kind': '连续波磁控管（额定功率小于 5kW）', 'rated_power_kW': 1, 'utilization': 1},
        'GB',
        40,
        26.2,
    ),
    # 10. Domestic Metal Film Resistor
    (
        '国产金属膜电阻器',
        {'resistance_ohm': 1000, 'rated_temperature_C': 70, 'quality': 'B2', 'stress_ratio': 0.5},
        'GB',
        40,
        0.00154,
    ),
    # 11. Domestic Resistor Network (NR=4, Tp=40℃)
    (
        '国产电阻网络',
        {'kind': '单个分立元件', 'resistor_count': 4, 'case_temperature_C': 40, 'quality': 'B2'},
        'GB',
        40,
        0.0018 * 4 * 1.92,
    ),
    # 12. Domestic Wirewound Potentiometer
    (
        '国产普通线绕电位器',
        {'resistance_ohm': 1000, 'terminal_count': 3, 'quality': 'B2', 'stress_ratio': 0.5},
        'GB',
        40,
        0.2818,
    ),
    # 13. Domestic Solid Tantalum Capacitor
    (
        '国产固体钽电解电容器',
        {
            'capacitance_uF': 10.0,
            'quality': 'A2',
            'stress_ratio': 0.5,
            'mounting': '片式',
            'series_resistance_ohm': 3.0,
            'operating_voltage_V': 10.0,
        },
        'GB',
        40,
        0.0087 * 1.0 * 0.2 * 1.0 * 0.8 * 1.2,
    ),
    # 14. Domestic Niobium Oxide Capacitor (C=10 > 3.3 μF)
    (
        '国产氧化铌电解电容器',
        {
            'capacitance_uF': 10.0,
            'quality': 'B1',
            'stress_ratio': 0.5,
            'mounting': '片式',
            'series_resistance_ohm': 2.0,
            'operating_voltage_V': 10.0,
        },
        'GB',
        40,
        0.0029 * 1.0 * 0.5 * 1.0 * 0.8 * 1.2,
    ),
    # 15. Domestic Capacitor Network (4 × non-solid tantalum)
    (
        '国产电容网络',
        {
            'capacitor_model': '国产非固体钽电解电容器',
            'capacitor_count': 4,
            'capacitance_uF': 10,
            'quality': 'B2',
            'stress_ratio': 0.5,
        },
        'GB',
        40,
        4 * 0.0226,
    ),
    # 16. Domestic Inductor & Transformer
    (
        '国产电感器',
        {'kind': '固定电感器', 'insulation_class': 'A', 'hotspot_temperature_C': 40, 'quality': 'B2'},
        'GB',
        40,
        0.0031,
    ),
    (
        '国产变压器',
        {'kind': '音频变压器 中频变压器', 'insulation_class': 'A', 'hotspot_temperature_C': 40, 'quality': 'B2'},
        'GB',
        40,
        0.0111 * 2.0,
    ),
    # 17. Domestic Electromechanical Relay (inductive S=0.5)
    (
        '国产机电继电器',
        {
            'rated_temperature_C': 85,
            'load_type': '感性',
            'stress_ratio': 0.5,
            'quality': 'B2',
            'contacts': 2,
            'rated_current_A': 5,
            'operations_per_hour': 1,
            'design_operations_per_hour': 100,
            'application': '弱功率',
            'structure': '衔铁式',
            'enclosure': '密封（漏率小于或等于1×10-1Pa·cm3/s）',
        },
        'GB',
        40,
        0.3145 * 1.0 * 1.0 * 1.5 * 1.0 * 0.1 * 0.42 * 3.5 * 1.0,
    ),
    # 18. Domestic Switch (lamp S=0.3)
    (
        '国产开关',
        {
            'switch_type': '旋转式',
            'quality': 'B2',
            'active_contacts': 4,
            'load_type': '灯',
            'stress_ratio': 0.3,
            'operations_per_day': 10,
        },
        'GB',
        40,
        (0.032 + 0.006) * 1.0 * 1.0 * 9.49 * 1.0,
    ),
    # 19. Domestic Elapsed Time Totalizer at negative temperature ratio (-0.25 -> S<=0.5, πT=0.8)
    (
        '国产时间累计器',
        {'totalizer_type': '普通(交直流)', 'quality': 'B', 'temperature_ratio': -0.25},
        'GB',
        -20,
        1.37 * 1.0 * 1.0 * 0.8,
    ),
    # 20. Domestic PCB, Solder Joint, Oscillator, Crystal Filter (f=0.1 <= 0.2 -> πf=0.90)
    (
        '国产印制板',
        {'plated_hole_count': 200, 'layer_count': 4, 'quality': 'A1'},
        'GB',
        25,
        (7e-5 * 200 + 0.0004) * 1.0 * 0.3 * 1.6,
    ),
    ('国产焊接点', {'kind': '再流焊', 'quality': 'A'}, 'GB', 25, 5.1e-5 * 1.0 * 0.25),
    ('国产振荡器', {'quality': 'B1', 'control': '温补'}, 'GB', 25, 0.028 * 1.0 * 0.65 * 1.1),
    (
        '国产压电陶瓷介电石英晶体和机械滤波器',
        {'filter_type': '石英晶体', 'quality': 'B', 'frequency_MHz': 0.1},
        'GB',
        25,
        0.012 * 1.0 * 1.0 * 0.9,
    ),
    # 21. Imported Digital IC (150->200 gates, 32->36 pins) & Imported Hybrid IC (-10℃->35℃)
    (
        '进口通用数字集成电路',
        {
            'technology': 'MOS',
            'complexity': 150,
            'package': '密封器件 / 双列直插 (DIP) 封装',
            'lead_count': 32,
            'quality': 'B',
            'junction_temperature_C': 55,
        },
        'GB',
        25,
        (0.00054 * 0.382 + 0.00081 * 1.0) * 1.0,
    ),
    (
        '进口混合集成电路',
        {
            'quality': 'S',
            'substrate_area_cm2': 1,
            'network_process': '厚膜',
            'film_resistor_count': 0,
            'package_process': '厚膜',
            'temperature_basis': '封装底座温度',
            'hybrid_temperature_C': -10,
            'function': '数字',
        },
        'GB',
        25,
        (1.0 * 0.0016 + 0.01) * 1.0 * 0.25 * 0.71 * 1.0,
    ),
    # 22. Nonoperating Digital IC (53.33->100 gates, 32->36 pins, cycle_rate=0.5->＜1) & Relay (20 mV / 100 mV)
    (
        '国产数字集成电路（非工作详细）',
        {
            'technology': 'MOS',
            'gate_count': 200 / 3.75,
            'package': '密封 / 双列直插 (DIP) 封装',
            'lead_count': 32,
            'quality': 'B2',
            'maturity': '符合相应的标准或技术 条件，已稳定生产的产品',
            'cycle_rate': 0.5,
        },
        'GB',
        25,
        0.001 * 1.0 * 1.0 * (0.87 + 0.1276) * 1.0 * 1.0,
    ),
    (
        '国产继电器（非工作详细）',
        {'kind': '机电继电器 / 非密封', 'quality': 'B1', 'contact_voltage_mV': 20},
        'GB',
        25,
        0.026 * 1.0 * 0.7,
    ),
    (
        '国产继电器（非工作详细）',
        {'kind': '机电继电器 / 非密封', 'quality': 'B1', 'contact_voltage_mV': 100},
        'GB',
        25,
        0.013 * 1.0 * 0.7,
    ),
]


@pytest.mark.parametrize(
    ('model', 'params', 'env', 'temp', 'expected'),
    GOLDEN_VALUE_CASES,
    ids=[f'{idx}_{c[0]}' for idx, c in enumerate(GOLDEN_VALUE_CASES, 1)],
)
def test_golden_values_across_major_299d_model_families(model, params, env, temp, expected):
    ev = evaluate(model, params, env, ambient_temperature_C=temp)
    assert ev.error == '', (model, ev.error)
    assert ev.rate == pytest.approx(expected, rel=1e-12)


def test_p1_ic_ceiling_totalizer_smt_gaps_and_inequality_headers():
    from src.domain import Component
    from src.input_sources import evaluate_with_sources
    from src.models.domestic_other import _smt_adjustment
    from src.models.nonoperating_other import _smt_nonop_adjustment
    from src.standards import AUTOMATIC_MODELS, MODELS, factor_html, factor_text

    # 1. [P1-3]: Clause 5.1.1.3 CMOS 200/3.75=53.33 gates & 74HC74 2*6=12 gates + LQFP-32/144/SOT-23-3/SOT-23-5
    ic_base = {k: v for k, v in MODELS['国产普通数字集成电路'].example.items() if k != 'gate_count'}
    comp_cmos = Component(
        'U_CMOS',
        '国产普通数字集成电路',
        1,
        ic_base | {'package': '非密封-表面安装(LCC/SO和其他)封装', 'lead_count': 144},
        input_sources={
            'gate_count': {'kind': 'gate_count', 'inputs': {'mode': 'transistors', 'technology': 'CMOS', 'transistors': 200}}
        },
    )
    ev_cmos = evaluate_with_sources(comp_cmos, 'GB', 25)
    ev_ref = evaluate(
        '国产普通数字集成电路',
        dict(MODELS['国产普通数字集成电路'].example, gate_count=100, package='非密封-表面安装(LCC/SO和其他)封装', lead_count=280),
        'GB',
        ambient_temperature_C=25,
    )
    assert ev_cmos.error == '' and ev_cmos.rate == pytest.approx(ev_ref.rate, rel=1e-12)
    txt_cmos = factor_text(ev_cmos)
    html_cmos = factor_html(ev_cmos)
    assert '门数向上取 100 档' in txt_cmos
    assert '引出端数向上取 280 档' in txt_cmos
    assert 'p025-' not in txt_cmos and 'p038-' not in txt_cmos
    assert 'standard://table/5/p025-' in html_cmos
    assert 'standard://table/27/p038-' in html_cmos

    comp_ff = Component(
        'U_74HC74',
        '国产普通数字集成电路',
        1,
        ic_base | {'package': '非密封-表面安装(LCC/SO和其他)封装', 'lead_count': 3},
        input_sources={'gate_count': {'kind': 'gate_count', 'inputs': {'mode': 'flip_flops', 'jk_rs': 0, 't_d': 2}}},
    )
    ev_ff = evaluate_with_sources(comp_ff, 'GB', 25)
    assert ev_ff.error == '' and '门数向上取 50 档' in factor_text(ev_ff)
    assert '引出端数向上取 4 档' in factor_text(ev_ff)

    # SOT-23-5 (5 -> 6 pins) and BGA-256 (256 -> 280 pins)
    ev_sot5 = evaluate(
        '国产普通数字集成电路',
        dict(MODELS['国产普通数字集成电路'].example, package='非密封-表面安装(LCC/SO和其他)封装', lead_count=5),
        'GB',
        ambient_temperature_C=25,
    )
    assert ev_sot5.error == '' and '引出端数向上取 6 档' in factor_text(ev_sot5)
    ev_bga256 = evaluate(
        '国产普通数字集成电路',
        dict(MODELS['国产普通数字集成电路'].example, package='密封-插接阵列(PGA/BGA和其他)封装', lead_count=256),
        'GB',
        ambient_temperature_C=25,
    )
    assert ev_bga256.error == '' and '引出端数向上取 280 档' in factor_text(ev_bga256)

    # 2. [P1-4]: 国产时间累计器 supports S <= 0.5 including 0 and negative Celsius ratios
    t_ex = dict(MODELS['国产时间累计器'].example)
    ev_half = evaluate('国产时间累计器', t_ex | {'temperature_ratio': 0.5}, 'GB', ambient_temperature_C=40)
    ev_zero = evaluate('国产时间累计器', t_ex | {'temperature_ratio': 0}, 'GB', ambient_temperature_C=0)
    ev_neg = evaluate('国产时间累计器', t_ex | {'temperature_ratio': -0.4}, 'GB', ambient_temperature_C=-34)
    assert ev_zero.error == '' and ev_zero.rate == pytest.approx(ev_half.rate, rel=1e-12)
    assert ev_neg.error == '' and ev_neg.rate == pytest.approx(ev_half.rate, rel=1e-12)
    assert evaluate('国产时间累计器', t_ex | {'temperature_ratio': 1.1}, 'GB', ambient_temperature_C=40).rate is None

    # 3. [P1-5]: SMT Table 311 & Table B.87 contiguous half-open ceiling intervals
    for fn in (_smt_adjustment, _smt_nonop_adjustment):
        assert fn(0.0)[0] == pytest.approx(0.13)
        assert fn(0.30)[0] == pytest.approx(0.13)
        v305, s305 = fn(0.305)
        assert v305 == pytest.approx(0.31) and '0.31～0.6' in s305
        assert fn(0.60)[0] == pytest.approx(0.31)
        v605, s605 = fn(0.605)
        assert v605 == pytest.approx(0.51) and '0.61～0.8' in s605
        assert fn(0.80)[0] == pytest.approx(0.51)
        v805, s805 = fn(0.805)
        assert v805 == pytest.approx(0.60) and '0.81～0.90' in s805
        assert fn(0.90)[0] == pytest.approx(0.60)
        assert fn(0.95)[0] == pytest.approx(1.0)
        with pytest.raises(ValueError):
            fn(-0.01)

    # 4. [P1-7]: supply_voltage_V across all 16 domestic IC models + Appendix B inequality choices
    domestic_vs_models = [
        name for name in AUTOMATIC_MODELS
        if name.startswith('国产') and any(f.key.endswith('supply_voltage_V') for f in MODELS[name].fields)
    ]
    assert len(domestic_vs_models) == 16
    for name in domestic_vs_models:
        base_p = dict(MODELS[name].example)
        vs_keys = [f.key for f in MODELS[name].fields if f.key.endswith('supply_voltage_V')]
        ev_le3 = evaluate(name, base_p | {k: '≤3' for k in vs_keys}, 'GB', ambient_temperature_C=25)
        ev_1v8 = evaluate(name, base_p | {k: 1.8 for k in vs_keys}, 'GB', ambient_temperature_C=25)
        ev_3v0 = evaluate(name, base_p | {k: 3 for k in vs_keys}, 'GB', ambient_temperature_C=25)
        ev_3v3 = evaluate(name, base_p | {k: 3.3 for k in vs_keys}, 'GB', ambient_temperature_C=25)
        ev_4v0 = evaluate(name, base_p | {k: 4 for k in vs_keys}, 'GB', ambient_temperature_C=25)
        assert ev_1v8.rate == pytest.approx(ev_le3.rate, rel=1e-12), name
        assert ev_3v0.rate == pytest.approx(ev_le3.rate, rel=1e-12), name
        assert ev_3v3.rate == pytest.approx(ev_4v0.rate, rel=1e-12), name
        assert evaluate(name, base_p | {k: 0 for k in vs_keys}, 'GB', ambient_temperature_C=25).rate is None
        assert evaluate(name, base_p | {k: 16 for k in vs_keys}, 'GB', ambient_temperature_C=25).rate is None

    # Appendix B cycle_rate: < 0.3 allows 0 & 0.2, rejects 0.5 (between <0.3 and 1) and 15 (between 10 and 20)
    cap_nonop = dict(MODELS['国产电容器（非工作详细）'].example)
    ev_c02 = evaluate('国产电容器（非工作详细）', cap_nonop | {'cycle_rate': 0.2}, 'GB', ambient_temperature_C=25)
    ev_clt = evaluate('国产电容器（非工作详细）', cap_nonop | {'cycle_rate': '＜0.3'}, 'GB', ambient_temperature_C=25)
    assert ev_c02.error == '' and ev_c02.rate == pytest.approx(ev_clt.rate, rel=1e-12)
    assert evaluate('国产电容器（非工作详细）', cap_nonop | {'cycle_rate': 0.5}, 'GB', ambient_temperature_C=25).rate is None
    assert evaluate('国产电容器（非工作详细）', cap_nonop | {'cycle_rate': 15}, 'GB', ambient_temperature_C=25).rate is None


def test_factor_titles_exact_fraction_temperature_and_sqlite_uri_guards(monkeypatch, tmp_path):
    import sqlite3
    import sys
    from fractions import Fraction
    from pathlib import Path

    from src import reference_data
    from src.reference_data import resolve_temperature_tick, temp_matrix_value
    from src.standards import MODELS, _factor_title, factor_text

    # 1. [P2-1]: Table-aware _factor_title disambiguation
    assert '触点负载系数 πL =' in factor_text(evaluate('国产开关', dict(MODELS['国产开关'].example), 'GB', ambient_temperature_C=40))
    assert '连接系数 πL =' in factor_text(evaluate('国产光纤连接器', dict(MODELS['国产光纤连接器'].example), 'GB', ambient_temperature_C=40))
    assert '非工作连接系数 πNL =' in factor_text(evaluate('国产光纤连接器（非工作详细）', dict(MODELS['国产光纤连接器（非工作详细）'].example), 'GB', ambient_temperature_C=25))
    assert '额定电流系数 πr =' in factor_text(evaluate('国产普通二极管', dict(MODELS['国产普通二极管'].example), 'GB', ambient_temperature_C=40))
    assert '额定电流系数 πr =' in factor_text(evaluate('国产闸流晶体管', dict(MODELS['国产闸流晶体管'].example), 'GB', ambient_temperature_C=40))
    assert '额定负载系数 πr =' in factor_text(evaluate('国产机电继电器', dict(MODELS['国产机电继电器'].example), 'GB', ambient_temperature_C=40))
    assert '控制系数 πC =' in factor_text(evaluate('国产振荡器', dict(MODELS['国产振荡器'].example), 'GB', ambient_temperature_C=40))
    assert '复杂度系数 πC =' in factor_text(evaluate('国产压电陀螺', dict(MODELS['国产压电陀螺'].example), 'GB', ambient_temperature_C=40))
    assert '电容器个数 N =' in factor_text(evaluate('国产电容网络', dict(MODELS['国产电容网络'].example), 'GB', ambient_temperature_C=40))
    assert '网络电阻数 N =' in factor_text(evaluate('进口电阻网络', dict(MODELS['进口电阻网络'].example), 'GB', ambient_temperature_C=40))
    assert '使用的金属化孔数 N =' in factor_text(evaluate('国产印制板', dict(MODELS['国产印制板'].example), 'GB', ambient_temperature_C=40))
    assert _factor_title('πS2', '表 54') == '电压应力系数'
    assert _factor_title('πSR', '表 234') == '串联电阻系数'
    assert _factor_title('πTAPS', '表 170') == '引出端系数'
    assert _factor_title('πPT', '表 30') == '可编程工艺系数'
    assert _factor_title('πP', '表 33') == '工艺系数'
    assert _factor_title('πP', '表 273') == '封装系数'
    assert _factor_title('πP', '表 292') == '接触件系数'
    assert _factor_title('πF', '表 37') == '电路功能系数'
    assert _factor_title('πM', '表 91') == '匹配网络系数'
    assert _factor_title('πB', '表 92') == '发射极镇流系数'
    assert _factor_title('模拟部分 C1', '表 6') == '电路复杂度失效率'
    assert _factor_title('第 1 管 πS2', '表 74') == '电压应力系数'

    # 2. [P2-10] & [P2-17]: Exact Fraction temperature ceiling + huge integer OverflowError -> ValueError
    t_just_over_40 = Fraction(40) + Fraction(1, 10**15)
    val_exact, src_exact = temp_matrix_value('78', t_just_over_40, 0.5, label_column=0)
    val_45, _ = temp_matrix_value('78', 45, 0.5, label_column=0)
    assert val_exact == val_45 and '温度向上取 45 ℃ 档' in src_exact

    # End-to-end semiconductor temperature_correction exact Fraction boundary (Ts=25, TM=174 -> C=149/150, 1-C=1/150, (1-C)*150=1)
    # With Ts=25, TM=175 - 1e-13 -> (1-C)*150 = 1e-13, raw Ta=40 -> T_corr = 40 + 1e-13 -> must ceil to 45 ℃!
    fet_boundary = evaluate(
        '国产硅场效应晶体管',
        dict(MODELS['国产硅场效应晶体管'].example, rated_temperature_C=25, max_junction_temperature_C=175 - 1e-12),
        'GB',
        ambient_temperature_C=40,
    )
    fet_45 = evaluate('国产硅场效应晶体管', dict(MODELS['国产硅场效应晶体管'].example), 'GB', ambient_temperature_C=45)
    assert fet_boundary.error == '' and fet_boundary.rate == pytest.approx(fet_45.rate, rel=1e-12)

    with pytest.raises(ValueError, match='范围'):
        resolve_temperature_tick('16', 10**400, ['25', '30'])
    ev_huge_temp = evaluate('国产焊接点', dict(MODELS['国产焊接点'].example), 'GB', ambient_temperature_C=10**400)
    assert ev_huge_temp.rate is None and '摄氏温度' in ev_huge_temp.error

    # 3. [P2-28]: Windows UNC authority URI normalization + OperationalError read-only fallback
    class _FakeUncPath:
        def as_uri(self):
            return 'file://server/share/GJB299D/database/standard_reference.sqlite'

    unc_uri = reference_data._sqlite_ro_uri(_FakeUncPath(), immutable=True)
    assert unc_uri.startswith('file:////server/share/')
    assert unc_uri.endswith('?mode=ro&immutable=1')

    real_connect = sqlite3.connect
    calls = []

    def _flaky_uri_connect(target, *args, **kwargs):
        calls.append((target, kwargs.get('uri', False)))
        if kwargs.get('uri'):
            raise sqlite3.OperationalError('invalid uri authority')
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, 'connect', _flaky_uri_connect)
    fallback_con = reference_data._connect_readonly(reference_data.REFERENCE_PATH, immutable=False)
    try:
        assert len(calls) == 2 and calls[0][1] is True and calls[1][1] is False
        assert fallback_con.execute('PRAGMA query_only').fetchone()[0] == 1
    finally:
        fallback_con.close()

    # 4. [P3-22]: Clear Chinese error guidance when database/standard_reference.sqlite is missing
    missing = tmp_path / 'database/standard_reference.sqlite'
    err_normal = reference_data._missing_db_error(missing)
    assert 'database/standard_reference.sqlite' in str(err_normal)
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    err_frozen = reference_data._missing_db_error(missing)
    assert '完整解压' in str(err_frozen) and 'database/standard_reference.sqlite' in str(err_frozen)
    assert Path('/home/wang/Downloads/预计工程.json').stat().st_size == 52884
