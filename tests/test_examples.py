"""Validation suite for all synthetic example BOMs, projects, reports, and redacted packages."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from src.analysis_methods import recompute_analyses
from src.analysis_methods.workspace import project_condition_sources
from src.bom import append_bom, read_bom, read_bom_sources
from src.domain import Project
from src.nominal import parse_nominal
from src.project_modules import create_project_module
from src.shared_io import load_supplier_bundle
from src.storage import load_project
from src.window import MainWindow
from src.workbook_io import is_gjb299d_workbook

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_DIR = ROOT / 'example'
BOM_DIR = EXAMPLE_DIR / '01_bom_import'
PROJECTS_DIR = EXAMPLE_DIR / '02_projects'
REPORTS_DIR = EXAMPLE_DIR / '03_reports_and_redacted'


def test_example_directory_layout():
    assert (EXAMPLE_DIR / 'README.md').is_file()
    assert sorted(p.name for p in BOM_DIR.iterdir()) == [
        'avionics_io_bom.xlsx',
        'legacy_control_bom.xls',
        'power_servo_bom_utf8.csv',
        'rf_frontend_bom_gb18030.csv',
        'sensor_board_bom.tsv',
    ]
    assert sorted(p.name for p in PROJECTS_DIR.iterdir()) == [
        '01_working_stress_comprehensive.json',
        '02_nonoperating_detailed.json',
        '03_parts_count_early_design.json',
        '04_hybrid_and_subcircuits.json',
        '05_system_module_integration.json',
        'sub_lc_filter_circuit.json',
        'sub_mounted_microcircuits.json',
    ]
    assert sorted(p.name for p in REPORTS_DIR.iterdir()) == [
        'full_report_sample.xls',
        'full_report_sample.xlsx',
        'redacted_delivery_sample.json',
        'redacted_delivery_sample.xls',
        'redacted_delivery_sample.xlsx',
    ]


def test_example_boms_read_and_import():
    # GB18030 file must not decode as UTF-8, confirming genuine GB18030 coverage.
    gb_path = BOM_DIR / 'rf_frontend_bom_gb18030.csv'
    with pytest.raises(UnicodeDecodeError):
        gb_path.read_bytes().decode('utf-8')

    # Multi-sheet .xlsx with title row and extra metadata columns.
    xlsx_sources = read_bom_sources(BOM_DIR / 'avionics_io_bom.xlsx')
    assert len(xlsx_sources) == 2

    for bom_path in sorted(BOM_DIR.iterdir()):
        sources = read_bom_sources(bom_path)
        assert sources, f'{bom_path.name} produced no BomSource'
        imported = read_bom(bom_path)
        assert imported.rows, f'{bom_path.name} produced no rows'
        host = Project('BOM校验工程')
        count = append_bom(host, imported)
        assert count == len(host.components) and count > 0

    # Specific range expansion & nominal value parsing on power_servo_bom_utf8.csv.
    servo_import = read_bom(BOM_DIR / 'power_servo_bom_utf8.csv')
    servo_proj = Project('伺服BOM校验')
    append_bom(servo_proj, servo_import)
    refs = {ref for c in servo_proj.components for ref in c.bom.get('assigned_designators', [])}
    for expected_ref in ('R10', 'R11', 'R12', 'R13', 'C1', 'C2', 'C3', 'C4', 'U1', 'U2'):
        assert expected_ref in refs

    values_by_ref = {
        c.bom['assigned_designators'][0]: c.bom['row']['Value']
        for c in servo_proj.components
        if c.bom.get('assigned_designators')
    }
    assert parse_nominal(values_by_ref['R1'], 'R').in_unit('Ω') == pytest.approx(0.0)
    assert parse_nominal(values_by_ref['R2'], 'R').in_unit('Ω') == pytest.approx(4.7)
    assert parse_nominal(values_by_ref['R10'], 'R').in_unit('Ω') == pytest.approx(10_000.0)
    assert parse_nominal(values_by_ref['R14'], 'R').in_unit('Ω') == pytest.approx(5_620_000.0)
    assert parse_nominal(values_by_ref['C1'], 'C').in_unit('uF') == pytest.approx(0.0047)
    assert parse_nominal(values_by_ref['C5'], 'C').in_unit('uF') == pytest.approx(100.0)
    assert parse_nominal(values_by_ref['L1'], 'L').in_unit('H') == pytest.approx(47e-6)


@pytest.mark.parametrize(
    'filename',
    [
        '01_working_stress_comprehensive.json',
        '02_nonoperating_detailed.json',
        '03_parts_count_early_design.json',
        '04_hybrid_and_subcircuits.json',
        '05_system_module_integration.json',
        'sub_mounted_microcircuits.json',
        'sub_lc_filter_circuit.json',
    ],
)
def test_example_projects_evaluate_cleanly(filename):
    project, _revision = load_project(PROJECTS_DIR / filename)
    assert project.enabled_conditions
    for condition in project.enabled_conditions:
        result = project.calculate(condition)
        assert result.error == ''
        assert result.pending_count == 0
        assert result.total_rate is not None and result.total_rate > 0

    results, errors = recompute_analyses(
        project.analysis,
        current_sources=project_condition_sources(project),
    )
    assert errors == {}
    if filename == '01_working_stress_comprehensive.json':
        assert {item.method for item in results} == {
            'mission',
            'bayes',
            'failure_modes',
            'physics:thermal',
        }


@pytest.mark.parametrize(
    'filename',
    ['01_working_stress_comprehensive.json', '03_parts_count_early_design.json'],
)
def test_dual_state_example_projects_switch_to_nonoperating_cleanly(filename):
    project, _ = load_project(PROJECTS_DIR / filename)
    assert project.prediction_state == 'working'
    assert project.set_prediction_state('nonoperating') is True
    for condition in project.enabled_conditions:
        result = project.calculate(condition)
        assert result.error == ''
        assert result.pending_count == 0
        assert result.total_rate is not None and result.total_rate > 0
    assert project.set_prediction_state('working') is True


def test_flagship_and_cascaded_project_features():
    flagship, _ = load_project(PROJECTS_DIR / '01_working_stress_comprehensive.json')
    by_prefix = {c.name.split(' ', 1)[0]: c for c in flagship.components}

    assert set(by_prefix['U1'].input_sources) == {'gate_count', 'junction_temperature_C'}
    assert by_prefix['U2'].parameters['junction_temperature_C'] == 47.6
    r_metal = next(c for c in flagship.components if c.model == '国产金属膜电阻器')
    assert r_metal.stress_variables == {'Pr': 0.25, 'Vbus': 12}
    assert r_metal.parameters['stress_ratio'] == 'Vbus*0.01/Pr'
    assert by_prefix['Q1'].model == '国产双极型双管'
    assert len(by_prefix['SMT1'].parameters['connections']) == 2

    j1 = next(c for c in flagship.components if c.name.startswith('J1'))
    j2 = next(c for c in flagship.components if c.name.startswith('J2'))
    j3 = next(c for c in flagship.components if c.name.startswith('J3'))
    assert j1.parameters['material_class'] == 'Ⅱ类'
    assert j1.parameters['material_max_temperature_C'] == 120.0
    assert j1.parameters['contacts'] == 22
    assert j2.parameters['contact_current_A'] == 2.5
    assert j3.parameters['connector_type'] == '射频同轴连接器'

    # Verify upward ceiling behaviors at GB @ 25℃
    gb_cond = flagship.enabled_conditions[0]
    j1_ev = flagship.evaluate(j1, condition=gb_cond)
    j2_ev = flagship.evaluate(j2, condition=gb_cond)
    u2_ev = flagship.evaluate(by_prefix['U2'], condition=gb_cond)
    d2_ev = flagship.evaluate(by_prefix['D2'], condition=gb_cond)
    assert any(sym == 'πP' and '向上取 25 档' in src for sym, _val, src in j1_ev.factors)
    assert any(sym == 'ΔT' and '电流向上取 3 A 档' in src and val == 2.1 for sym, val, src in j2_ev.factors)
    assert any(sym == 'πT' and '向上取 49 ℃ 档' in src for sym, _val, src in u2_ev.factors)
    assert d2_ev.rate is not None and d2_ev.rate > 0
    assert any(sym == 'πT' and '向上取 30 ℃ 档' in src for sym, _val, src in d2_ev.factors)

    # Verify subcircuit SHA-256 integrity
    sub_mounted_sha = hashlib.sha256((PROJECTS_DIR / 'sub_mounted_microcircuits.json').read_bytes()).hexdigest()
    sub_lc_sha = hashlib.sha256((PROJECTS_DIR / 'sub_lc_filter_circuit.json').read_bytes()).hexdigest()
    hybrid_proj, _ = load_project(PROJECTS_DIR / '04_hybrid_and_subcircuits.json')
    hic = next(c for c in hybrid_proj.components if c.model == '国产混合集成电路')
    lc = next(c for c in hybrid_proj.components if c.model == '国产LC滤波器')
    gyro = next(c for c in hybrid_proj.components if c.model == '国产压电陀螺')
    assert hic.input_sources['mounted_ic_rate']['source']['sha256'] == sub_mounted_sha
    assert lc.input_sources['constituent_rate']['source']['sha256'] == sub_lc_sha
    assert gyro.input_sources['rear_circuit_rate']['source']['sha256'] == sub_lc_sha

    # Verify system module SHA-256 integrity against flagship project
    flagship_sha = hashlib.sha256((PROJECTS_DIR / '01_working_stress_comprehensive.json').read_bytes()).hexdigest()
    sys_proj, _ = load_project(PROJECTS_DIR / '05_system_module_integration.json')
    mod = next(c for c in sys_proj.components if c.model == '工程模块')
    mod.validate_module_source()
    assert mod.module_source['sha256'] == flagship_sha


def test_example_reports_and_redacted_roundtrip():
    flagship, _ = load_project(PROJECTS_DIR / '01_working_stress_comprehensive.json')
    expected_totals = [flagship.calculate(c).total_rate for c in flagship.enabled_conditions]

    for wb_name in (
        'full_report_sample.xlsx',
        'full_report_sample.xls',
        'redacted_delivery_sample.xlsx',
        'redacted_delivery_sample.xls',
    ):
        assert is_gjb299d_workbook(REPORTS_DIR / wb_name) is True

    for delivery_name in (
        'full_report_sample.xlsx',
        'full_report_sample.xls',
        'redacted_delivery_sample.json',
        'redacted_delivery_sample.xlsx',
        'redacted_delivery_sample.xls',
    ):
        path = REPORTS_DIR / delivery_name
        loaded, digest = load_supplier_bundle(path)
        assert len(loaded.enabled_conditions) == len(expected_totals)
        actual_totals = [loaded.calculate(c).total_rate for c in loaded.enabled_conditions]
        for actual, expected in zip(actual_totals, expected_totals, strict=True):
            assert actual == pytest.approx(expected, rel=1e-9)

        if delivery_name.startswith('redacted_'):
            assert [c.name for c in loaded.components] == [f'{i:06d}' for i in range(1, len(flagship.components) + 1)]
            assert [c.name for c in loaded.enabled_conditions] == ['工况001', '工况002', '工况003']
            assert loaded.notes == ''
            assert all(c.reference == '' for c in loaded.components)
            module = create_project_module(
                loaded,
                name=f'Module-{delivery_name}',
                source_path=path,
                expected_sha256=digest,
            )
            assert module.model == '工程模块'
            module.validate_module_source()


def test_flagship_opens_cleanly_in_main_window(qapp, isolated_tmp):
    src_path = PROJECTS_DIR / '01_working_stress_comprehensive.json'
    tmp_json = isolated_tmp / 'flagship_copy.json'
    shutil.copy2(src_path, tmp_json)

    win = MainWindow()
    try:
        assert win.open_path(tmp_json) is True
        assert win.dirty is False
        assert win.analysis_page.panels['mission'].last_result is not None
        payload = win.analysis_page.export_payload()
        assert payload['errors'] == {}
        assert len(payload['results']) == 4

        assert win.save() is True
        assert win.dirty is False

        reopened = MainWindow()
        try:
            assert reopened.open_path(tmp_json) is True
            assert reopened.dirty is False
            assert reopened.analysis_page.export_payload()['errors'] == {}
        finally:
            reopened.close()
    finally:
        win.close()
