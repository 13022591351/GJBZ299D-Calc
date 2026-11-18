"""BOM nominal parsing, stress variable evaluation, grid ceiling, and auxiliary input derivations."""

import pytest

from src.analysis_methods import (
    PHYSICS_TOOLS,
    bayesian_update,
    mode_contributions,
    physics_calculate,
)
from src.configuration import nominal_parameter
from src.domain import Component, dual_transistor
from src.input_sources import gate_count, junction_temperature, validate_sources
from src.nominal import parse_nominal
from src.stress import MissingStressVariable, ceil_to_grid, parse_stress_ratio


def test_bom_nominal_parsing():
    r1 = parse_nominal('4.7kΩ', 'R')
    assert r1 is not None and r1.kind == 'R' and float(r1.decimal_value) == pytest.approx(4700.0)

    r2 = parse_nominal('4R7', 'R')
    assert r2 is not None and float(r2.decimal_value) == pytest.approx(4.7)

    c1 = parse_nominal('100nF', 'C')
    assert c1 is not None and c1.kind == 'C' and float(c1.decimal_value) == pytest.approx(1e-7)
    # Text with multiple numeric values intentionally stays unparsed.
    assert parse_nominal('100nF/25V', 'C') is None

    l1 = parse_nominal('10uH', 'L')
    assert l1 is not None and l1.kind == 'L' and float(l1.decimal_value) == pytest.approx(1e-5)

    cap_comp = Component(
        'C_BOM',
        '待配置',
        1,
        {},
        bom={'file_name': 'bom.csv', 'sha256': '0' * 64,
             'row': {'Value': '100nF'}, 'assigned_designators': ['C_BOM']},
    )
    assert nominal_parameter(cap_comp, '国产1类瓷介电容器') == ('capacitance_uF', pytest.approx(0.1))

    # Leading-zero package codes must not parse as 3/4-digit EIA values.
    for pkg in ('0201', '0402', '0603', '0805', '047'):
        for kind in ('R', 'C', 'L'):
            assert parse_nominal(pkg, kind) is None
    for zero_txt in ('0', '000', '0000', '0R'):
        parsed_zero = parse_nominal(zero_txt, 'R')
        assert parsed_zero is not None and float(parsed_zero.decimal_value) == 0.0
    assert float(parse_nominal('103', 'R').decimal_value) == pytest.approx(10000.0)
    assert float(parse_nominal('1002', 'R').decimal_value) == pytest.approx(10000.0)

    jumper = Component(
        'R_0R', '待配置', 1, {},
        bom={'file_name': 'bom.csv', 'sha256': '0' * 64,
             'row': {'Value': '0R', 'Footprint': '0603'}, 'assigned_designators': ['R_0R']},
    )
    assert nominal_parameter(jumper, '国产片式膜电阻器') == ('resistance_ohm', 0.0)
    assert nominal_parameter(jumper, '国产线绕电阻器') is None

    pkg_in_value = Component(
        'R_PKG', '待配置', 1, {},
        bom={'file_name': 'bom.csv', 'sha256': '0' * 64,
             'row': {'Value': '1206', 'Footprint': 'R1206'}, 'assigned_designators': ['R_PKG']},
    )
    assert nominal_parameter(pkg_in_value, '国产片式膜电阻器') is None


def test_p1_8_eia_kept_for_parameter_columns_and_blocked_for_non_parameter_columns():
    from src.configuration import configure_batch, is_parameter_value_column

    # User rule: keep unitless 3-4 digit integers parsed as EIA markings in nominal.py.
    assert parse_nominal('100', 'R').in_unit('Ω') == pytest.approx(10.0)
    assert parse_nominal('100', 'C').in_unit('pF') == pytest.approx(10.0)
    assert parse_nominal('104', 'C').in_unit('nF') == pytest.approx(100.0)
    assert parse_nominal('472', 'R').in_unit('kΩ') == pytest.approx(4.7)

    def _bom_comp(ref, val, idx='1', fp='0603'):
        return Component(
            ref,
            '待配置',
            1,
            {},
            bom={
                'file_name': 'b.csv',
                'sha256': '0' * 64,
                'row': {'Index': idx, 'Designator': ref, 'Value': val, 'Footprint': fp},
                'assigned_designators': [ref],
            },
        )

    # Genuine parameter column (EIA + explicit units) -> allow_bare_eia is True.
    genuine = [
        _bom_comp('R1', '100', '1'),
        _bom_comp('R2', '472', '2'),
        _bom_comp('C1', '104', '3'),
        _bom_comp('R3', '10k', '4'),
    ]
    assert is_parameter_value_column(genuine) is True
    configured_genuine = configure_batch(
        genuine[:2], '国产片式膜电阻器', {}, from_bom=True, calculation_enabled=False
    )
    assert configured_genuine[0][0].parameters['resistance_ohm'] == pytest.approx(10.0)
    assert configured_genuine[1][0].parameters['resistance_ohm'] == pytest.approx(4700.0)

    # Non-parameter column (<50% parse as nominal) -> bare '100' rejected, explicit '10k' still works!
    non_param = [
        _bom_comp('R1', '100', '1'),
        _bom_comp('R2', '精密采样备注', '2'),
        _bom_comp('C1', '电源去耦备注', '3'),
        _bom_comp('C2', '高频旁路备注', '4'),
        _bom_comp('R3', '10k', '5'),
    ]
    assert is_parameter_value_column(non_param) is False
    assert nominal_parameter(non_param[0], '国产片式膜电阻器', allow_bare_eia=False) is None
    assert nominal_parameter(non_param[4], '国产片式膜电阻器', allow_bare_eia=False) == (
        'resistance_ohm',
        pytest.approx(10000.0),
    )
    configured_non_param = configure_batch(
        non_param, '国产片式膜电阻器', {}, from_bom=True, calculation_enabled=False
    )
    assert 'resistance_ohm' not in configured_non_param[0][0].parameters
    assert configured_non_param[4][0].parameters['resistance_ohm'] == pytest.approx(10000.0)

    # Bare digits equal to row['Index'] (100, 101, 102) -> rejected as non-parameter index column.
    index_col = [
        _bom_comp('R1', '100', '100'),
        _bom_comp('R2', '101', '101'),
        _bom_comp('R3', '102', '102'),
    ]
    assert is_parameter_value_column(index_col) is False


def test_configure_batch_prunes_inactive_and_incompatible_choices_by():
    from src.configuration import configure_batch

    # [P2-16] Switching branch controller prunes newly-inactive parameter keys.
    diode = Component(
        'D1',
        '国产普通二极管',
        1,
        {
            'kind': '开关二极管',
            'quality': 'A2',
            'temperature_basis': '带散热片功率器件的管壳温度',
            'case_temperature_C': 65.0,
            'rated_current_A': 1.0,
            'stress_ratio': 0.3,
        },
    )
    updated, _ = configure_batch(
        [diode],
        '国产普通二极管',
        {'temperature_basis': '工作环境温度'},
        calculation_enabled=False,
    )[0]
    assert 'case_temperature_C' not in updated.parameters

    # [P1-15] Switching parent choice drops stale incompatible choices_by child value.
    conn = Component(
        'J1',
        '国产电连接器',
        1,
        {'connector_type': '低频连接器', 'counting_unit': '单个', 'quality': 'A1'},
    )
    switched, _ = configure_batch(
        [conn],
        '国产电连接器',
        {'connector_type': '射频同轴连接器'},
        calculation_enabled=False,
    )[0]
    if any(f.key == 'counting_unit' and f.choices_by for f in switched_fields_for('国产电连接器')):
        assert switched.parameters.get('counting_unit') != '单个'


def switched_fields_for(model_name):
    from src.standards import MODELS

    return MODELS[model_name].fields


def test_stress_expressions_and_grid_ceiling():
    assert parse_stress_ratio('28/100', {}) == pytest.approx(0.28)
    assert parse_stress_ratio('Vop / Vrated', {'Vop': 12.0, 'Vrated': 25.0}) == pytest.approx(0.48)
    with pytest.raises(MissingStressVariable):
        parse_stress_ratio('Vop / Vrated', {'Vop': 12.0})
    with pytest.raises(ValueError, match='0 次幂'):
        parse_stress_ratio('(sqrt(8) - 2 * sqrt(2)) ** 0', {})
    assert ceil_to_grid(0.41, (0.1, 0.2, 0.3, 0.4, 0.5, 0.6)) == 0.5


def test_appendix_analysis_engines_bayes_failure_modes_and_physics():
    # Appendix E: prior_hours = 0.5 / 1e-7 = 5e6, exposure = 2e7, rate = (1.5 / 2.5e7) * 1e6 = 0.06
    bayes = bayesian_update(
        0.1,
        [{'source': '现场试验', 'failures': 1, 'component_hours': 1e7, 'acceleration_factor': 2.0}],
        prior_failures=0.5,
    )
    assert bayes.outputs['rate'] == pytest.approx(0.06)
    assert bayes.outputs['prior_component_hours'] == pytest.approx(5e6)

    # Appendix F: F.1 linear IC sums to 100%.
    fm = mode_contributions(0.05, 'F.1', '线性集成电路')
    assert fm.outputs['percent_sum'] == pytest.approx(100.0)
    assert fm.outputs['contribution_sum'] == pytest.approx(0.05)

    # Appendix D: all 11 PoF formulas evaluate with valid outputs; humidity keeps clarity note.
    for tool_id, tool in PHYSICS_TOOLS.items():
        params = {'parameter_source': '手册标定'}
        for spec in tool.fields:
            if spec.key == 'ea' and spec.minimum is not None and spec.minimum < 0:
                params[spec.key] = -0.15
            elif spec.key == 'ea':
                params[spec.key] = 0.7
            elif spec.key in ('reference_rh', 'test_rh'):
                params[spec.key] = 0.8
            elif spec.key in ('rh', 'use_rh'):
                params[spec.key] = 0.5
            elif spec.minimum is not None and spec.minimum > 0:
                params[spec.key] = spec.minimum
            elif spec.key.endswith(('_K', '_C', '_Hz')):
                params[spec.key] = 100.0
            else:
                params[spec.key] = 2.0 if spec.minimum is None else 1.5
        if tool_id == 'tddb':
            params.update({'a': 0.1, 'b': 0.001, 'c': -100.0, 'd': 1000.0, 'voltage_V': 5.0})
        res = physics_calculate(tool_id, params)
        assert res.outputs and all(v != 0 for v in res.outputs.values())
    assert 'ANSI/VITA 51.2' in ''.join(PHYSICS_TOOLS['humidity'].note)


def test_dual_transistor_gate_count_and_tj_helpers():
    assert dual_transistor(1, 10, 1, 15, 1) == pytest.approx(1 / 10 + 1 * (20 - 15) / (15 * 10))

    ng_cmos, _ = gate_count({'mode': 'transistors', 'technology': 'CMOS', 'transistors': 375})
    assert ng_cmos == pytest.approx(100.0)

    ng_ff, _ = gate_count({'mode': 'flip_flops', 'jk_rs': 2, 't_d': 3})
    assert ng_ff == pytest.approx(34.0)

    tj, trace = junction_temperature(
        {
            'case_source': 'specified',
            'case_temperature_C': 50.0,
            'resistance_source': 'specified',
            'thermal_resistance_C_W': 30.0,
            'power_W': 0.5,
        },
        'GB',
    )
    assert tj == pytest.approx(65.0)
    assert '式（2）' in trace

    imported_ic = Component(
        'U_IMP',
        '进口微处理器',
        1,
        {'technology': 'MOS', 'transistor_count': 1000, 'package': '密封－双列直插（DIP）封装',
         'lead_count': 40, 'quality': 'B'},
        domestic=False,
        input_sources={
            'junction_temperature_C': {
                'kind': 'junction_temperature',
                'inputs': {'case_source': 'table24', 'resistance_source': 'specified',
                           'thermal_resistance_C_W': 30.0, 'power_W': 0.5},
            }
        },
    )
    with pytest.raises(ValueError, match='进口器件'):
        validate_sources(imported_ic)


def test_core_domain_overflow_state_transfer_bayes_physics_and_numeric_helpers():
    from src.analysis_methods.workspace import (
        is_component_config_error,
        project_condition_source_diagnostics,
    )
    from src.calculation import calculate_condition
    from src.domain import OperatingCondition, Project, finite, validate_temperature
    from src.model_spec import Evaluation
    from src.numeric import formatted_number, number_text, scale_decimal
    from src.state_transfer import translate_state_parameters
    from src.stress import StressAdjustment
    from src.stress_display import adjustment_text, stress_value_text

    # [P2-17] Huge integer 10**400 raises ValueError rather than OverflowError.
    with pytest.raises(ValueError):
        finite(10**400, '测试项')
    with pytest.raises(ValueError):
        validate_temperature(10**400)

    # [P2-15] project_rate missing condition / incomplete sub-project counts as pending (False).
    assert is_component_config_error(
        '国产LC滤波器', Evaluation(None, error='组成工程没有对应环境、温度且已启用的工况')
    ) is False
    assert is_component_config_error(
        '国产LC滤波器', Evaluation(None, error='组成工程的对应工况未完整计算：待配置 2 项')
    ) is False
    assert is_component_config_error(
        '国产LC滤波器', Evaluation(None, error='请选择有效的质量等级')
    ) is True

    # [P3-3] gate_count scalar fallback survives when derived input_source is rejected.
    params, sources = translate_state_parameters(
        '国产数字集成电路（非工作详细）',
        {'technology': '双极', 'complexity': 100, 'package': '密封 / 双列直插 (DIP) 封装', 'lead_count': 16, 'quality': 'B1'},
        {
            'complexity': {
                'kind': 'gate_count',
                'inputs': {'mode': 'transistors', 'technology': 'CMOS', 'transistors': 375},
            }
        },
        '国产普通数字集成电路',
    )
    assert sources == {}
    assert params.get('gate_count') == 100

    # [P3-4] Uppercase SHA-256 hex accepted in input_sources.py.
    sub_proj = Project(
        'Sub',
        components=[Component('R_S', '已知单件失效率', 1, {}, condition_rates=[{'environment': 'GB', 'ambient_temperature_C': 25, 'rate': 0.01}])],
        conditions=[OperatingCondition('GB25', 'GB', 25)],
    )
    lc = Component(
        'FL1',
        '国产LC滤波器',
        1,
        {'method': '组成元器件失效率求和', 'quality': 'A1'},
        input_sources={
            'constituent_rate': {
                'kind': 'project_rate',
                'source': {'file_name': 'sub.json', 'sha256': 'A' * 64, 'project': sub_proj.to_dict()},
            }
        },
    )
    validate_sources(lc)
    assert lc.evaluate('GB', 25).rate is not None

    # [P3-5] Single-bound physics error shows clean operator instead of [0, None].
    with pytest.raises(ValueError) as exc_info:
        physics_calculate(
            'em',
            {
                'parameter_source': '试验',
                'a': 1.0,
                'current_density': 10.0,
                'n': 2.0,
                'ea': -0.5,
                'temperature_K': 320.0,
            },
        )
    assert 'None' not in str(exc_info.value) and '≥ 0' in str(exc_info.value)

    # [P3-6] Blank '' prior_failures and acceleration_factor fall back to 0.5 and 1.
    bayes_blank = bayesian_update(
        0.1,
        [{'source': '试验A', 'failures': 0, 'component_hours': 5e6, 'acceleration_factor': ''}],
        prior_failures='',
    )
    assert bayes_blank.inputs['prior_failures'] == pytest.approx(0.5)
    assert bayes_blank.details[0]['acceleration_factor'] == pytest.approx(1.0)

    # [P3-7] component_for_state returns independent clone and deep-copies bom.
    orig = Component(
        'R_CLONE',
        '待配置',
        1,
        {},
        bom={'row': {'Value': '10k'}},
    )
    same_state = orig.component_for_state('working')
    other_state = orig.component_for_state('nonoperating')
    assert same_state is not orig
    same_state.bom['row']['Value'] = 'mutated'
    other_state.bom['row']['Value'] = 'mutated2'
    assert orig.bom['row']['Value'] == '10k'

    # [P3-8] Component.validate() does not mutate rate_state on invalid mismatch.
    bad_state = Component('R_BAD', '国产片式膜电阻器', 1, {}, rate_state='nonoperating')
    with pytest.raises(ValueError, match='预计状态'):
        bad_state.validate()
    assert bad_state.rate_state == 'nonoperating'

    # [P3-10] Zero total failure rate distinguishes infinite MTBF from incomplete.
    zero_comp = Component(
        'Z1',
        '已知单件失效率',
        2,
        {},
        condition_rates=[{'environment': 'GB', 'ambient_temperature_C': 25, 'rate': 0.0}],
    )
    cond = OperatingCondition('GB25', 'GB', 25)
    zero_res = calculate_condition([zero_comp], cond)
    assert zero_res.total_rate == 0.0
    assert zero_res.mtbf_hours is None
    assert zero_res.mtbf_status == 'infinite'
    assert zero_res.is_zero_rate is True
    assert zero_res.mtbf_text == '∞'

    inc_res = calculate_condition([Component('P1', '待配置', 1, {})], cond)
    assert inc_res.mtbf_status == 'incomplete'
    assert inc_res.is_zero_rate is False
    assert inc_res.mtbf_text == '待计算'

    # [P2-14] project_condition_source_diagnostics active_results cache path.
    diag_proj = Project('Diag', components=[zero_comp], conditions=[cond])
    active_map = {cond.identifier: zero_res}
    s1, i1 = project_condition_source_diagnostics(diag_proj, active_results=active_map)
    s2, i2 = project_condition_source_diagnostics(diag_proj, active_results=active_map)
    assert s1 == s2 and i1 == i2

    # numeric.py & stress_display.py full branch coverage.
    assert scale_decimal('4.7', 3) == pytest.approx(4700.0)
    with pytest.raises(ValueError):
        scale_decimal('NaN', 0)
    with pytest.raises(ValueError):
        scale_decimal('1', 400)
    with pytest.raises(ValueError):
        scale_decimal('1', -400)
    assert number_text(1e-320) == repr(1e-320)
    assert formatted_number(None) == '无有限值'

    assert '比值' in stress_value_text('12/24', 1.0)
    assert '档位' in stress_value_text(0.25, 1.0, grid=(0.1, 0.3, 0.5))
    assert '[待配置]' in stress_value_text('Vop/Vmax', 1.0, {})
    adj1 = StressAdjustment('stress_ratio', '电应力比 S', 0.25, 0.3, '', '1/4')
    adj2 = StressAdjustment('stress_ratio', '电应力比 S', 0.25, 0.3, '', '')
    assert '1/4' in adjustment_text(adj1)
    assert adjustment_text(adj2) == '0.25 [档位 0.3]'
