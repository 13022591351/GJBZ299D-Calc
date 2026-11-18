"""Stress, scaling performance, 264-model matrix, mega-project round-trip, boundary fuzz & lock contention tests."""

import copy
import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from src.bom import BomSource, designators
from src.domain import Component, OperatingCondition, Project
from src.prediction import model_state
from src.project_modules import create_project_module
from src.shared_io import export_redacted_file, load_supplier_project
from src.standards import (
    AUTOMATIC_MODELS,
    MODELS,
    evaluate,
    factor_html,
    factor_text,
    validate_parameters,
)
from src.storage import FORMAT, FORMAT_VERSION, RevisionConflict, load_project, save_project
from src.stress import validate_stress
from src.window import MainWindow
from src.workbook_io import export_workbook_xls, export_workbook_xlsx, is_gjb299d_workbook

ROOT = Path(__file__).resolve().parent.parent

EXPECTED_LIMITATION_MODELS = frozenset({
    '进口可变电容器',
    '国产灯（工作计数）',
    '国产激光器（工作计数）',
    '国产压电陀螺（工作计数）',
    '国产灯（非工作计数）',
    '国产激光器（非工作计数）',
    '国产压电陀螺（非工作计数）',
})


# ---------------------------------------------------------------------------
# 1. UI Scaling & O(N^2) Regression ([P1-9])
# ---------------------------------------------------------------------------


def _build_600_component_project() -> Project:
    proj = Project('600器件性能压测工程')
    proj.grouped = False
    proj.conditions = [
        OperatingCondition(name='地面良好 25℃', environment='GB', ambient_temperature_C=25, enabled=True),
        OperatingCondition(name='一般固定 40℃', environment='GF1', ambient_temperature_C=40, enabled=True),
        OperatingCondition(name='平稳移动 55℃', environment='GM1', ambient_temperature_C=55, enabled=True),
        OperatingCondition(name='舰船舱内 35℃', environment='NS1', ambient_temperature_C=35, enabled=True),
        OperatingCondition(name='无人舱 70℃', environment='AUF', ambient_temperature_C=70, enabled=True),
    ]
    ic_example = dict(MODELS['国产普通数字集成电路'].example)
    diode_example = dict(MODELS['国产普通二极管'].example)
    items = []
    for i in range(1, 601):
        mod = i % 6
        if mod in (0, 1):
            des = f'R{i}'
            comp = Component(
                name=des,
                model='国产片式膜电阻器',
                quantity=1 + (i % 4),
                parameters={'resistance_ohm': float(100 * ((i % 20) + 1)), 'film': '薄膜', 'quality': 'A1P'},
                bom={'file_name': 'perf.csv', 'sha256': '1' * 64, 'row': {'Value': '1k', 'Footprint': '0603', 'Manufacturer Part Number': f'MPN-R{i % 15}'}, 'assigned_designators': [des]},
            )
        elif mod == 2:
            des = f'C{i}'
            comp = Component(
                name=des,
                model='国产1类瓷介电容器',
                quantity=2,
                parameters={'capacitance_uF': 0.001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
                bom={'file_name': 'perf.csv', 'sha256': '1' * 64, 'row': {'Value': '1nF', 'Footprint': '0603', 'Manufacturer Part Number': f'MPN-C{i % 10}'}, 'assigned_designators': [des]},
            )
        elif mod == 3:
            des = f'U{i}'
            comp = Component(name=des, model='国产普通数字集成电路', quantity=1, parameters=dict(ic_example))
        elif mod == 4:
            des = f'D{i}'
            comp = Component(name=des, model='国产普通二极管', quantity=1, parameters=dict(diode_example))
        else:
            des = f'X{i}'
            comp = Component(name=des, model='待配置', quantity=1, parameters={})
        items.append(comp)
    proj.components = items
    proj.validate()
    return proj


def test_ui_scaling_600_components_x_5_conditions_and_rapid_undo_redo(qapp):
    proj = _build_600_component_project()
    all_600_ids = [c.identifier for c in proj.components]

    win = MainWindow(proj)
    try:
        win.show()
        qapp.processEvents()
        assert win.proxy.rowCount() == 600

        # 1) Assert refresh(all_600_ids) completes in < 1.5s (was ~10.3s before [P1-9] fix!)
        t0 = time.perf_counter()
        win.refresh(all_600_ids)
        refresh_elapsed = time.perf_counter() - t0
        assert refresh_elapsed < 1.5, f'refresh(all_600_ids) took {refresh_elapsed:.3f}s (expected < 1.5s)'
        assert len(win.selected_rows()) == 600

        # 2) Search filter & status-chip toggle with all 600 selected completes in < 0.8s
        t1 = time.perf_counter()
        win.filter_field.setText('R1')
        win.filter_field.clear()
        win.filter_chips['calculated'].click()
        win.filter_chips['pending'].click()
        win.filter_chips['all'].click()
        filter_elapsed = time.perf_counter() - t1
        assert filter_elapsed < 0.8, f'Filter + status chip toggles took {filter_elapsed:.3f}s (expected < 0.8s)'

        # 3) duplicate_components on 200 selected rows + 15x rapid Undo/Redo cycles preserve exact project.to_dict()!
        ids_200 = all_600_ids[:200]
        win.refresh(ids_200, recalculate=False)
        assert len(win.selected_rows()) == 200
        before_dict = copy.deepcopy(win.project.to_dict())

        win.duplicate_components()
        assert len(win.project.components) == 800
        after_dict = copy.deepcopy(win.project.to_dict())

        for cycle in range(15):
            win.undo_stack.undo()
            assert len(win.project.components) == 600, f'Undo cycle {cycle} count mismatch'
            assert win.project.to_dict() == before_dict, f'Undo cycle {cycle} state drift'
            win.undo_stack.redo()
            assert len(win.project.components) == 800, f'Redo cycle {cycle} count mismatch'
            assert win.project.to_dict() == after_dict, f'Redo cycle {cycle} state drift'
    finally:
        win.undo_stack.clear()
        win.dirty = False
        win.close()


# ---------------------------------------------------------------------------
# 2. All-264-Model Stress Matrix, State Round-Trip & Mega-Project Export
# ---------------------------------------------------------------------------


def test_all_264_models_stress_matrix_determinism_and_trace_html():
    assert len(MODELS) >= 264
    environments = ('GB', 'GF1', 'GM1', 'NS1', 'ARW', 'SF')
    temperatures = (-40.0, 0.0, 37.3, 68.5)

    # First verify that at ('GB', 37.3 ℃) (non-grid temperature!), every non-limitation automatic model evaluates > 0
    for name in AUTOMATIC_MODELS:
        spec = MODELS[name]
        params = dict(spec.example)
        if 'source_environment' in params:
            params['source_environment'] = 'GB'
        if 'source_temperature_C' in params:
            params['source_temperature_C'] = 37.3
        ev1 = evaluate(name, params, 'GB', ambient_temperature_C=37.3)
        ev2 = evaluate(name, params, 'GB', ambient_temperature_C=37.3)
        assert ev1 == ev2, f'Non-deterministic evaluation on {name}'
        if name in EXPECTED_LIMITATION_MODELS:
            assert ev1.rate is None and ev1.error
        else:
            assert ev1.error == '' and ev1.rate is not None and ev1.rate > 0, f'{name} failed at GB 37.3℃: {ev1.error}'
            rendered_html = factor_html(ev1, reference=spec.reference)
            rendered_txt = factor_text(ev1)
            assert rendered_html.count('<a ') == rendered_html.count('</a>')
            assert 'Traceback' not in rendered_html
            # Raw cell IDs (e.g. p125-s002-r001-c002) must be stripped from plain factor_text
            assert '-r0' not in rendered_txt or 'standard://' not in rendered_txt

    # Sweep all AUTOMATIC_MODELS across all 6 environments x 4 non-grid temperatures
    for name in AUTOMATIC_MODELS:
        spec = MODELS[name]
        params = dict(spec.example)
        for env in environments:
            for temp in temperatures:
                ev = evaluate(name, params, env, ambient_temperature_C=temp)
                if ev.rate is not None:
                    assert math.isfinite(ev.rate) and ev.rate > 0, (name, env, temp, ev.rate)
                    assert ev.error == ''
                else:
                    assert ev.error, f'{name} returned rate=None with empty error at ({env}, {temp})'
                    assert 'Traceback' not in ev.error and 'KeyError' not in ev.error

    # Targeted cross-model regression checks ([P1-3], [P1-4], [P1-5], [P1-7])
    # [P1-4]: 国产时间累计器 at 0 ℃ and negative Celsius ratio
    for ratio in (-0.25, 0.0, 0.5):
        ev_timer = evaluate('国产时间累计器', dict(MODELS['国产时间累计器'].example, temperature_ratio=ratio), 'GB', ambient_temperature_C=25)
        assert ev_timer.error == '' and ev_timer.rate is not None and ev_timer.rate > 0, (ratio, ev_timer.error)

    # [P1-7]: 3.3 V & 1.8 V supply across all 16 domestic IC models with *supply_voltage_V
    digital_vs_models = [
        name for name in AUTOMATIC_MODELS
        if name.startswith('国产') and any(f.key.endswith('supply_voltage_V') for f in MODELS[name].fields)
    ]
    assert len(digital_vs_models) >= 16
    for name in digital_vs_models:
        vs_keys = [f.key for f in MODELS[name].fields if f.key.endswith('supply_voltage_V')]
        for vs in (1.8, 3.3):
            overrides = dict.fromkeys(vs_keys, vs)
            ev_vs = evaluate(name, {**MODELS[name].example, **overrides}, 'GB', ambient_temperature_C=37.3)
            assert ev_vs.error == '' and ev_vs.rate is not None and ev_vs.rate > 0, (name, vs, ev_vs.error)


def test_working_nonoperating_set_prediction_state_roundtrip_all_models():
    for start_state, opposite_state in (('working', 'nonoperating'), ('nonoperating', 'working')):
        proj = Project(f'StateRoundTrip_{start_state}', prediction_state=start_state)
        proj.conditions = [OperatingCondition(name='GB25', environment='GB', ambient_temperature_C=25, enabled=True)]
        originals = []
        for idx, name in enumerate(AUTOMATIC_MODELS, 1):
            if name in EXPECTED_LIMITATION_MODELS or model_state(name) != start_state:
                continue
            example = copy.deepcopy(MODELS[name].example)
            comp = Component(
                name=f'ITEM_{idx}',
                model=name,
                quantity=1,
                parameters=copy.deepcopy(example),
                domestic=not name.startswith('进口'),
                rate_state=start_state,
            )
            comp.validate()
            proj.components.append(comp)
            originals.append((name, example))

        assert len(proj.components) > 40
        proj.validate()

        assert proj.set_prediction_state(opposite_state, auto_suggest=True) is True
        proj.validate()
        assert proj.prediction_state == opposite_state

        assert proj.set_prediction_state(start_state, auto_suggest=True) is True
        proj.validate()
        assert proj.prediction_state == start_state

        for comp, (exp_model, exp_params) in zip(proj.components, originals, strict=True):
            assert comp.model == exp_model
            assert comp.parameters == exp_params


def test_synthetic_mega_project_90_plus_models_full_io_and_redacted_roundtrip(isolated_tmp):
    mega = Project('90+模型合成巨型工程', prediction_state='working')
    mega.conditions = [
        OperatingCondition(name='地面良好 25℃', environment='GB', ambient_temperature_C=25, enabled=True),
        OperatingCondition(name='一般固定 37.3℃', environment='GF1', ambient_temperature_C=37.3, enabled=True),
    ]

    # Pick all working AUTOMATIC_MODELS whose example evaluates cleanly in both GB 25℃ and GF1 37.3℃
    added_models = []
    for idx, name in enumerate(AUTOMATIC_MODELS, 1):
        if name in EXPECTED_LIMITATION_MODELS or model_state(name) != 'working':
            continue
        params = copy.deepcopy(MODELS[name].example)
        ev_gb = evaluate(name, params, 'GB', ambient_temperature_C=25)
        ev_gf = evaluate(name, params, 'GF1', ambient_temperature_C=37.3)
        if ev_gb.rate is not None and ev_gf.rate is not None:
            mega.components.append(
                Component(
                    name=f'M{idx:03d}',
                    model=name,
                    quantity=1 + (idx % 3),
                    parameters=params,
                    domestic=not name.startswith('进口'),
                )
            )
            added_models.append(name)
            if len(added_models) >= 95:
                break

    # Also include: 已知单件失效率, 工程模块, stress_variables expression component, and Clause 5.1.1.3 gate_count component
    mega.components.append(
        Component(
            name='EXT_RATE_1',
            model='已知单件失效率',
            quantity=2,
            parameters={},
            condition_rates=[
                {'environment': 'GB', 'ambient_temperature_C': 25, 'rate': 0.015},
                {'environment': 'GF1', 'ambient_temperature_C': 37.3, 'rate': 0.035},
            ],
        )
    )
    sub_mod_proj = Project('内部子模块')
    sub_mod_proj.conditions = copy.deepcopy(mega.conditions)
    sub_mod_proj.components = [
        Component('SUB_R1', '国产片式膜电阻器', 4, {'resistance_ohm': 4700.0, 'film': '薄膜', 'quality': 'A1P'})
    ]
    mega.components.append(create_project_module(sub_mod_proj, name='SUB_MOD_1', quantity=2))
    mega.components.append(
        Component(
            name='SECRET_STRESS_CAP',
            model='国产1类瓷介电容器',
            quantity=3,
            parameters={'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 'V_secret / 50'},
            stress_variables={'V_secret': 18.5},
        )
    )
    ic_derived_params = dict(MODELS['国产普通数字集成电路'].example)
    ic_derived_params.pop('gate_count', None)
    mega.components.append(
        Component(
            name='CMOS_DERIVED_IC',
            model='国产普通数字集成电路',
            quantity=1,
            parameters=ic_derived_params,
            input_sources={'gate_count': {'kind': 'gate_count', 'inputs': {'mode': 'transistors', 'technology': 'CMOS', 'transistors': 200}}},
        )
    )

    assert len(mega.components) >= 90
    mega.validate()

    orig_gb = mega.calculate(mega.conditions[0]).total_rate
    orig_gf = mega.calculate(mega.conditions[1]).total_rate
    assert orig_gb is not None and orig_gb > 0
    assert orig_gf is not None and orig_gf > 0

    # 1) JSON save & reload round-trip
    json_path = isolated_tmp / 'mega.json'
    save_project(json_path, mega)
    reloaded_json, _ = load_project(json_path)
    assert reloaded_json.calculate(reloaded_json.conditions[0]).total_rate == pytest.approx(orig_gb, rel=1e-12)
    assert reloaded_json.calculate(reloaded_json.conditions[1]).total_rate == pytest.approx(orig_gf, rel=1e-12)

    # 2) Full report XLSX & XLS export
    xlsx_report = isolated_tmp / 'mega_report.xlsx'
    xls_report = isolated_tmp / 'mega_report.xls'
    assert export_workbook_xlsx(xlsx_report, mega) >= 3
    assert export_workbook_xls(xls_report, mega) >= 3
    assert is_gjb299d_workbook(xlsx_report)
    assert is_gjb299d_workbook(xls_report)

    # 3) Redacted JSON, XLSX, and XLS export & supplier reload round-trip + secret non-leakage check
    for suffix in ('.json', '.xlsx', '.xls'):
        red_path = isolated_tmp / f'mega_redacted{suffix}'
        export_redacted_file(red_path, mega)
        assert b'V_secret' not in red_path.read_bytes()
        sup = load_supplier_project(red_path)
        sup_rates = {
            (c.environment, c.ambient_temperature_C): sup.calculate(c).total_rate
            for c in sup.enabled_conditions
        }
        assert sup_rates[('GB', 25.0)] == pytest.approx(orig_gb, rel=1e-9)
        assert sup_rates[('GF1', 37.3)] == pytest.approx(orig_gf, rel=1e-9)


# ---------------------------------------------------------------------------
# 3. Boundary, Fuzz & Concurrency Stress ([P2-17], [P2-18], [P2-20])
# ---------------------------------------------------------------------------


def test_60000_designator_bom_cell_rejected_fast():
    # Single-range 60,000 designators rejected immediately
    t0 = time.perf_counter()
    with pytest.raises(ValueError):
        designators('R1-R60000')
    assert time.perf_counter() - t0 < 0.5

    # Multi-segment 60,000 designators in a single cell (6 segments x 10,000) rejected fast (< 1.5s)
    sixty_k_cell = 'R1-R10000, C1-C10000, L1-L10000, D1-D10000, U1-U10000, Q1-Q10000'
    src_single_cell = BomSource(
        file_name='bomb.csv',
        sha256='f' * 64,
        cells=[
            ['Designator', 'Value', 'Quantity'],
            [sixty_k_cell, '10k', ''],
        ],
        header_row=0,
    )
    t1 = time.perf_counter()
    with pytest.raises(ValueError):
        first, last, cols = src_single_cell.default_region()
        src_single_cell.build_import(first, last, cols, src_single_cell.default_mapping(cols))
    elapsed_cell = time.perf_counter() - t1
    assert elapsed_cell < 1.5, f'60k designator cell took {elapsed_cell:.3f}s'

    # Multi-row 60,000 designators across 6 rows x 10,000 exceeding per-import cap rejected fast (< 1.5s)
    src_multi_row = BomSource(
        file_name='bomb_rows.csv',
        sha256='e' * 64,
        cells=[
            ['Designator', 'Value', 'Quantity'],
            ['R1-R10000', '10k', ''],
            ['C1-C10000', '10k', ''],
            ['L1-L10000', '10k', ''],
            ['D1-D10000', '10k', ''],
            ['U1-U10000', '10k', ''],
            ['Q1-Q10000', '10k', ''],
        ],
        header_row=0,
    )
    t2 = time.perf_counter()
    with pytest.raises(ValueError):
        first, last, cols = src_multi_row.default_region()
        src_multi_row.build_import(first, last, cols, src_multi_row.default_mapping(cols))
    elapsed_rows = time.perf_counter() - t2
    assert elapsed_rows < 1.5, f'60k multi-row BOM took {elapsed_rows:.3f}s'


def test_huge_10_pow_400_integers_caught_cleanly_as_value_error(sample_project, isolated_tmp):
    huge = 10**400

    # 1) OperatingCondition.validate
    with pytest.raises(ValueError):
        OperatingCondition(name='HugeTemp', environment='GB', ambient_temperature_C=huge).validate()

    # 2) Component.validate across quantity, condition_rates, and parameters
    with pytest.raises(ValueError):
        Component('R1', '国产片式膜电阻器', huge, {'resistance_ohm': 100.0, 'film': '薄膜', 'quality': 'A1P'}).validate()

    with pytest.raises(ValueError):
        Component(
            'X1', '已知单件失效率', 1, {},
            condition_rates=[{'environment': 'GB', 'ambient_temperature_C': 25, 'rate': huge}],
        ).validate()

    with pytest.raises(ValueError):
        Component(
            'X2', '已知单件失效率', 1, {},
            condition_rates=[{'environment': 'GB', 'ambient_temperature_C': huge, 'rate': 0.01}],
        ).validate()

    with pytest.raises(ValueError):
        Component(
            'R2', '国产片式膜电阻器', 1,
            {'resistance_ohm': huge, 'film': '薄膜', 'quality': 'A1P'},
        ).validate()

    with pytest.raises(ValueError):
        validate_parameters('国产1类瓷介电容器', {'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4, 'temperature_C': huge}, include_project=True)

    # 3) Stress expression & variable overflow
    with pytest.raises(ValueError):
        validate_stress('10**400', 1.0, 'S')
    with pytest.raises(ValueError):
        validate_stress('U / 10', 1.0, 'S', {'U': huge})

    # 4) JSON project decode with 401-digit integer literals -> ValueError (never OverflowError!)
    base_doc = {
        'format': FORMAT,
        'version': FORMAT_VERSION,
        'revision': 1,
        'project': sample_project.to_dict(),
    }
    raw_json = json.dumps(base_doc, ensure_ascii=False)
    huge_literal = '1' + ('0' * 400)
    mutated_json = raw_json.replace('"ambient_temperature_C": 25', f'"ambient_temperature_C": {huge_literal}')
    huge_path = isolated_tmp / 'huge_int.json'
    huge_path.write_text(mutated_json, encoding='utf-8')
    with pytest.raises(ValueError):
        load_project(huge_path)


def test_concurrent_posix_save_lock_raises_revision_conflict_quickly(sample_project, isolated_tmp):
    target_path = isolated_tmp / 'contended.json'
    r1 = save_project(target_path, sample_project)

    holder_code = (
        'import sys, time\n'
        'from pathlib import Path\n'
        'from src.storage import _save_lock\n'
        'target = Path(sys.argv[1]).resolve()\n'
        'with _save_lock(target):\n'
        '    print("LOCKED", flush=True)\n'
        '    time.sleep(10)\n'
    )
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'}
    proc = subprocess.Popen(
        [sys.executable, '-B', '-c', holder_code, str(target_path)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        ready_line = proc.stdout.readline().strip()
        assert ready_line == 'LOCKED'

        # 1) Unrelated sibling project in the SAME directory must NOT be blocked by target_path's lock!
        sibling_path = isolated_tmp / 'unrelated_sibling.json'
        t_sib = time.perf_counter()
        save_project(sibling_path, sample_project)
        sib_elapsed = time.perf_counter() - t_sib
        assert sib_elapsed < 0.8, f'Unrelated file in same directory blocked for {sib_elapsed:.3f}s!'

        # 2) Contended save on target_path must raise RevisionConflict within ~2-3.5s instead of hanging forever!
        t_lock = time.perf_counter()
        with pytest.raises(RevisionConflict):
            save_project(target_path, sample_project, expected_revision=r1)
        lock_elapsed = time.perf_counter() - t_lock
        assert lock_elapsed < 4.0, f'_save_lock contention took {lock_elapsed:.3f}s (expected < 4.0s)'
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
