"""Comprehensive headless unit & integration tests for all dialog and input editor modules."""

import copy

import openpyxl
import pytest
from PyQt6.QtCore import QItemSelectionModel
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QFileDialog,
    QMessageBox,
    QTableWidgetSelectionRange,
)

from src.analysis_methods.workspace import is_component_config_error
from src.batch_dialog import BatchDialog
from src.bom import BomSource, append_bom, read_bom_sources
from src.bom_dialog import BomImportDialog
from src.calculation import evaluate_component
from src.dialogs import ComponentDialog
from src.domain import Component, OperatingCondition, Project
from src.field_editor import CompositeComboBox, sync_field_constraints
from src.input_source_dialog import InputSourceDialog, source_kind
from src.model_picker import ModelPickerDialog, ModelSelector, domestic_context
from src.model_spec import Input
from src.number_edit import NumberEdit
from src.project_import import (
    ModuleDialog,
    ProjectComponentImportDialog,
    clone_components,
)
from src.project_modules import create_project_module
from src.rate_editor import ConditionRateEditor, condition_key
from src.record_editor import RecordTableEdit
from src.shared_io import export_redacted_file, load_supplier_bundle
from src.standards import MODELS
from src.storage import save_project
from src.stress_edit import StressComboBox
from src.stress_variables_editor import StressVariablesEditor
from src.ui.bom_wizard import BomPreMapDialog, suggest_model


def _make_bom_comp(designator, value='', footprint='', desc='', mpn='', index='1', domestic=True):
    return Component(
        name=designator,
        model='待配置',
        quantity=1,
        parameters={},
        domestic=domestic,
        bom={
            'file_name': 'test.csv',
            'sha256': 'a' * 64,
            'row': {
                'Index': str(index),
                'Designator': designator,
                'Value': value,
                'Footprint': footprint,
                'Description': desc,
                'Quantity': '1',
                'Manufacturer': 'TestMfr',
                'Manufacturer Part Number': mpn,
            },
            'assigned_designators': [designator],
        },
    )


# ---------------------------------------------------------------------------
# 1. BomImportDialog (src/bom_dialog.py)
# ---------------------------------------------------------------------------


def test_bom_import_dialog_multisheet_header_only_guard_and_region_selection(qapp, isolated_tmp):
    wb = openpyxl.Workbook()
    ws_header_only = wb.active
    ws_header_only.title = 'EmptyHeaderSheet'
    ws_header_only.append(['Designator', 'Value', 'Footprint', 'Quantity'])

    ws_valid = wb.create_sheet('MainBOM')
    ws_valid.append(['Index', 'Designator', 'Value', 'Description', 'Footprint', 'Quantity', 'Manufacturer', 'MPN'])
    ws_valid.append(['1', 'R1, R2', '10k', '贴片电阻', '0603', '', 'Yageo', 'RC0603FR-0710KL'])
    ws_valid.append(['2', 'C1', '100nF', 'X7R电容', '0603', '1', 'Murata', 'GRM188R71H104KA93'])
    ws_valid.append(['3', '', '', '导热垫片', '', '', '3M', 'PAD-01'])

    ws_no_header = wb.create_sheet('Headerless')
    ws_no_header.append(['R10', '4.7k', '0603', '1'])
    ws_no_header.append(['C10', '100nF', '0603', '1'])

    xlsx_path = isolated_tmp / 'multisheet_bom.xlsx'
    wb.save(xlsx_path)

    sources = read_bom_sources(xlsx_path)
    assert len(sources) == 3

    dlg = BomImportDialog(sources)
    try:
        # Sheet 0 is header-only: [P2-34] guard must disable next_button and NOT select header row as data.
        assert dlg.sheet_box.currentIndex() == 0
        assert not dlg.next_button.isEnabled()
        assert not dlg.raw_table.selectedRanges()
        dlg.accept()  # On page 0 with invalid selection -> stays on page 0 and result_import stays None
        assert dlg.pages.currentIndex() == 0
        assert dlg.result_import is None

        # Switch to valid sheet (index 1).
        dlg.sheet_box.setCurrentIndex(1)
        assert dlg.source.sheet_name == 'MainBOM'
        assert dlg.next_button.isEnabled()
        assert '已选源表第 2～4 行' in dlg.region_label.text()

        # Clear selection -> disables next_button; non-contiguous selection -> rejected; restore_default recovers.
        dlg.raw_table.clearSelection()
        assert not dlg.next_button.isEnabled()
        dlg.raw_table.selectionModel().select(
            dlg.raw_table.model().index(1, 1), QItemSelectionModel.SelectionFlag.Select
        )
        dlg.raw_table.selectionModel().select(
            dlg.raw_table.model().index(3, 2), QItemSelectionModel.SelectionFlag.Select
        )
        assert not dlg.next_button.isEnabled()
        assert '连续' in dlg.region_label.text()

        # Move raw_table header section then restore_default resets visual column order and selection.
        dlg.raw_table.clearSelection()
        dlg.raw_table.setRangeSelected(QTableWidgetSelectionRange(1, 1, 2, 3), True)
        dlg.raw_table.horizontalHeader().moveSection(1, 2)
        assert dlg.raw_table.horizontalHeader().sectionsMoved()
        first, last, cols = dlg.selected_region()
        assert (first, last) == (1, 2) and len(cols) == 3

        dlg.restore_default()
        assert not dlg.raw_table.horizontalHeader().sectionsMoved()
        assert dlg.next_button.isEnabled()

        # Advance to step 2 (mapping & preview).
        dlg.accept()  # Page 0 accept advances to page 1
        assert dlg.pages.currentIndex() == 1
        assert dlg.preview_table.rowCount() == 4  # 1 mapping row + 3 data rows
        assert '按位号' in dlg.preview_table.item(1, 5).text()
        assert dlg.preview_table.item(3, 5).text() == '待填写'
        assert dlg.import_button.isEnabled()

        # Duplicate column mapping disables import_button with clear message.
        des_col = dlg.mapping_boxes['Designator'].currentData()
        orig_val_idx = dlg.mapping_boxes['Value'].currentIndex()
        dlg.mapping_boxes['Value'].setCurrentIndex(dlg.mapping_boxes['Value'].findData(des_col))
        assert not dlg.import_button.isEnabled()
        assert dlg.result_import is None
        assert '重复分配' in dlg.validation.text()

        # Restore mapping -> valid again; back & advance reuses mapping_signature.
        dlg.mapping_boxes['Value'].setCurrentIndex(orig_val_idx)
        assert dlg.import_button.isEnabled()
        dlg.back()
        assert dlg.pages.currentIndex() == 0
        dlg.advance()
        assert dlg.pages.currentIndex() == 1

        dlg.accept()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.result_import is not None
        assert len(dlg.result_import.rows) == 3

        # Switch to headerless sheet (index 2) and verify content-based mapping.
        dlg.sheet_box.setCurrentIndex(2)
        assert dlg.pages.currentIndex() == 0
        assert dlg.next_button.isEnabled()
        dlg.advance()
        assert dlg.mapping_boxes['Designator'].currentData() == 0
        assert dlg.mapping_boxes['Value'].currentData() == 1
        assert dlg.import_button.isEnabled()
    finally:
        dlg.close()


def test_bom_import_dialog_deferred_header_move_and_cell_errors(qapp):
    source = BomSource(
        file_name='err.xlsx',
        sha256='b' * 64,
        cells=[
            ['Designator', 'Value', 'Footprint', 'Quantity'],
            ['R1', '10k', '0603', '1'],
            ['R2', '#REF!', '0603', '1'],
        ],
        header_row=0,
        sheet_name='Sheet1',
        cell_errors={(2, 1): '公式计算错误 #REF!'},
    )
    dlg = BomImportDialog(source)
    try:
        dlg.advance()
        assert dlg.pages.currentIndex() == 1
        # Value column has a cell_error -> import_button must be disabled!
        assert not dlg.import_button.isEnabled()
        assert '#REF!' in dlg.validation.text()

        # Test [P2-33]: dragging preview header section 1 -> 2 defers visual revert via QTimer.singleShot(0, ...).
        before_des = dlg.mapping_boxes['Designator'].currentData()
        before_val = dlg.mapping_boxes['Value'].currentData()
        header = dlg.preview_table.horizontalHeader()
        header.moveSection(1, 2)
        qapp.processEvents()
        assert not header.sectionsMoved()
        assert dlg.mapping_boxes['Designator'].currentData() == before_val
        assert dlg.mapping_boxes['Value'].currentData() == before_des

        # Dragging involving section 0 (index column) must revert visual move without altering assignments.
        cur_assignments = [dlg.mapping_boxes[k].currentData() for k in dlg.mapping_boxes]
        header.moveSection(1, 0)
        qapp.processEvents()
        assert not header.sectionsMoved()
        assert [dlg.mapping_boxes[k].currentData() for k in dlg.mapping_boxes] == cur_assignments

        # Swap back Designator & Value, then unmap Value (the column with #REF!) -> import succeeds!
        header.moveSection(2, 1)
        qapp.processEvents()
        assert dlg.mapping_boxes['Designator'].currentData() == before_des
        dlg.mapping_boxes['Value'].setCurrentIndex(0)  # '留空'
        assert dlg.import_button.isEnabled()
        assert dlg.result_import is not None
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 2. BomPreMapDialog & suggest_model (src/ui/bom_wizard.py) + [P1-8] EIA Guard
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ('designator', 'value', 'footprint', 'desc', 'domestic', 'state', 'expected'),
    [
        ('RN1', '10k', '0603x4', '排阻', True, 'working', '国产电阻网络'),
        ('R1', '0.01R', '2512', '采样分流电阻 SHUNT', True, 'working', '待配置'),
        ('R2', '100R', '0805', '精密金属箔电阻 FOIL', True, 'working', '国产箔式电阻器'),
        ('R3', '10R', 'AXIAL', '功率线绕电阻', True, 'working', '国产功率线绕电阻器'),
        ('R4', '10R', 'AXIAL', '普通线绕电阻 WIREWOUND', True, 'working', '国产线绕电阻器'),
        ('R5', '1M', 'AXIAL', '玻璃釉电阻 GLAZE', True, 'working', '国产玻璃釉电阻器'),
        ('R6', '4.7k', 'AXIAL', '碳膜电阻', True, 'working', '国产碳膜电阻器'),
        ('R7', '1k', 'AXIAL', '引线金属膜电阻', True, 'working', '国产金属膜电阻器'),
        ('R8', '1k', '0603', '贴片电阻', False, 'working', '进口电阻器'),
        ('R9', '1k', '0603', '贴片电阻', True, 'nonoperating', '国产电阻器（非工作详细）'),
        ('R10', '1k', '0603', '贴片电阻', False, 'nonoperating', '待配置'),
        ('CN1', '100nF', '0603', '排容 4P2C', True, 'working', '国产电容网络'),
        ('C1', '100nF', '0603', 'X7R 陶瓷', True, 'working', '国产2类瓷介电容器'),
        ('C2', '22pF', '0402', 'C0G NP0', True, 'working', '国产1类瓷介电容器'),
        ('C3', '1uF', '0805', 'MLCC 陶瓷电容', True, 'working', '待配置'),
        ('C4', '1uF', '0805', 'MLCC 陶瓷电容', False, 'working', '进口电容器'),
        ('C5', '47uF', 'CAK35', '非固体钽电解', True, 'working', '国产非固体钽电解电容器'),
        ('C6', '10uF', 'SMD', '氧化铌电解电容', True, 'working', '国产氧化铌电解电容器'),
        ('C7', '10uF', 'CASE-A', '固体钽电容 TANT', True, 'working', '国产固体钽电解电容器'),
        ('C8', '100uF', 'SMD', '铝电解电容', True, 'working', '国产铝电解电容器'),
        ('C9', '100pF', 'RADIAL', '云母电容 MICA', True, 'working', '国产云母电容器'),
        ('FB1', '600R@100MHz', '0603', '磁珠', True, 'working', '待配置'),
        ('L1', '10uH', 'SMD', '脉冲变压器', True, 'working', '国产变压器'),
        ('L2', '4.7uH', '0603', '贴片电感', True, 'working', '国产片式电感器'),
        ('L3', '100uH', 'AXIAL', '色环电感', True, 'working', '国产电感器'),
        ('T1', '1:1', 'SMD', '隔离变压器', True, 'working', '国产变压器'),
        ('Y1', '25MHz', 'SMD3225', '3.3V 有源晶振 TCXO', True, 'working', '国产振荡器'),
        ('Y2', '8MHz', 'HC-49', '无源石英谐振器 XTAL', True, 'working', '国产谐振器'),
        ('U1', 'PC817', 'SOP-4', '光电耦合器 OPTOCOUPLER', True, 'working', '国产光电子器件'),
        ('LED1', 'GREEN', '0603', '发光二极管', True, 'working', '国产光电子器件'),
        ('D1', 'TL431', 'SOT-23', '精密基准源', True, 'working', '待配置'),
        ('ZD1', '5.1V', 'SOD-123', '齐纳稳压二极管', True, 'working', '国产电压调整、电压基准及电流调整二极管'),
        ('D2', 'SS14', 'SMA', '肖特基整流二极管', True, 'working', '国产普通二极管'),
        ('TP1', 'TEST', 'PAD', '测试点', True, 'working', '待配置'),
    ],
)
def test_suggest_model_matrix(designator, value, footprint, desc, domestic, state, expected):
    comp = _make_bom_comp(designator, value=value, footprint=footprint, desc=desc, domestic=domestic)
    model, reason = suggest_model(comp, domestic=domestic, prediction_state=state)
    assert model == expected
    assert reason


def test_bom_pre_map_wizard_eia_parameter_column_guard(qapp):
    # Case A: Genuine parameter column -> bare 3-4 digit EIA markings ("100" -> 10 Ω, "472" -> 4.7 kΩ, "104" -> 0.1 μF) MUST work!
    genuine_items = [
        _make_bom_comp('R1', value='100', footprint='0603', desc='贴片电阻', index='1'),
        _make_bom_comp('R2', value='472', footprint='0603', desc='贴片电阻', index='2'),
        _make_bom_comp('R3', value='10k', footprint='0603', desc='贴片电阻', index='3'),
        _make_bom_comp('C1', value='104', footprint='0603', desc='X7R 电容', index='4'),
    ]
    wiz_ok = BomPreMapDialog(genuine_items, [0, 1, 2, 3], prediction_state='working')
    try:
        assert wiz_ok.has_suggestions()
        applied = wiz_ok.apply_to(genuine_items)
        assert len(applied) == 4
        assert genuine_items[0].parameters['resistance_ohm'] == pytest.approx(10.0)
        assert genuine_items[1].parameters['resistance_ohm'] == pytest.approx(4700.0)
        assert genuine_items[2].parameters['resistance_ohm'] == pytest.approx(10000.0)
        assert genuine_items[3].parameters['capacitance_uF'] == pytest.approx(0.1)
    finally:
        wiz_ok.close()

    # Case B: Non-parameter column mapped to Value (e.g. sequential row indices or arbitrary part text) -> bare "100" MUST NOT become 10 Ω!
    non_param_items = [
        _make_bom_comp('R1', value='100', footprint='0603', desc='贴片电阻', index='100'),
        _make_bom_comp('R2', value='101', footprint='0603', desc='贴片电阻', index='101'),
        _make_bom_comp('R3', value='102', footprint='0603', desc='贴片电阻', index='102'),
        _make_bom_comp('R4', value='103', footprint='0603', desc='贴片电阻', index='103'),
    ]
    wiz_guard = BomPreMapDialog(non_param_items, [0, 1, 2, 3], prediction_state='working')
    try:
        wiz_guard.apply_to(non_param_items)
        assert non_param_items[0].model == '国产片式膜电阻器'
        assert 'resistance_ohm' not in non_param_items[0].parameters
    finally:
        wiz_guard.close()

    # Case C: Non-parameter text column (<50% parseable passive rows) + switching combo in wizard UI.
    noisy_items = [
        _make_bom_comp('R1', value='100', footprint='0603', desc='贴片电阻', index='1'),
        _make_bom_comp('R2', value='PACKAGE_A', footprint='0603', desc='贴片电阻', index='2'),
        _make_bom_comp('R3', value='LOT_XYZ', footprint='0603', desc='贴片电阻', index='3'),
        _make_bom_comp('C1', value='100nF', footprint='0603', desc='X7R 电容', index='4'),
    ]
    wiz_noisy = BomPreMapDialog(noisy_items, [0, 1, 2, 3], prediction_state='working')
    try:
        # Switch first cluster combo to '进口电阻器' and uncheck/check behavior.
        chk0, cmb0, _ = wiz_noisy._controls[0]
        cmb0.setCurrentIndex(cmb0.findData('待配置'))
        assert not chk0.isChecked()
        cmb0.setCurrentIndex(cmb0.findData('进口电阻器'))
        assert chk0.isChecked()
        wiz_noisy.apply_to(noisy_items)
        assert noisy_items[0].model == '进口电阻器'
        assert noisy_items[0].domestic is False
        # Bare '100' skipped because column is not predominantly parameter values, while explicit '100nF' still works!
        assert 'resistance_ohm' not in noisy_items[0].parameters
        assert noisy_items[3].parameters['capacitance_uF'] == pytest.approx(0.1)
    finally:
        wiz_noisy.close()


# ---------------------------------------------------------------------------
# 3. ConditionRateEditor (src/rate_editor.py) & ComponentDialog Drafts
# ---------------------------------------------------------------------------


def test_condition_rate_editor_and_component_dialog_draft_roundtrip(qapp):
    assert condition_key(None, 25) is None
    assert condition_key('GB', None) is None
    assert condition_key('GB', 25) == ('GB', 25.0)

    conditions = [
        OperatingCondition(name='地面良好', environment='GB', ambient_temperature_C=25, enabled=True),
        OperatingCondition(name='地面良好重复', environment='GB', ambient_temperature_C=25, enabled=True),
        OperatingCondition(name='固定禁用', environment='GF1', ambient_temperature_C=55, enabled=False),
        OperatingCondition(name='未填温度', environment='GM1', ambient_temperature_C=None, enabled=True),
    ]
    comp = Component(
        name='MOD-1',
        model='已知单件失效率',
        quantity=2,
        parameters={},
        condition_rates=[{'environment': 'NS1', 'ambient_temperature_C': 40, 'rate': 0.025}],
    )
    editor = ConditionRateEditor(comp, conditions, environment='GB', temperature=25)
    try:
        # 3 unique conditions from list + 1 extra stored ('NS1', 40) = 4 rows
        assert editor.table.rowCount() == 4
        assert editor.fields[('NS1', 40.0)].value() == pytest.approx(0.025)
        # Incomplete condition row is read-only
        incomplete_field = [f for k, f, _ in editor.rows if k is None][0]
        assert incomplete_field.isReadOnly()

        editor.fields[('GB', 25.0)].setText('0.0125')
        editor.fields[('GF1', 55.0)].setText('1.5e-3')
        editor.fields[('NS1', 40.0)].clear()  # Clearing removes stored condition rate!
        editor.apply_changes(comp)
        assert comp.condition_rate('GB', 25) == pytest.approx(0.0125)
        assert comp.condition_rate('GF1', 55) == pytest.approx(0.0015)
        assert comp.condition_rate('NS1', 40) is None

        # Negative rate rejected
        editor.fields[('GB', 25.0)].setText('-0.1')
        with pytest.raises(ValueError, match='有限非负数'):
            editor.apply_changes(comp)
    finally:
        editor.close()

    # Legacy unbound λp hint label coverage
    legacy_comp = Component('LEG', '已知单件失效率', 1, {'λp': 0.05})
    legacy_ed = ConditionRateEditor(legacy_comp, conditions[:1], environment='GB', temperature=25)
    legacy_ed.close()

    # Draft save/restore across model switches inside ComponentDialog
    dlg = ComponentDialog(
        Component('X1', '已知单件失效率', 1, {}),
        environment='GB',
        ambient_temperature_C=25,
        conditions=conditions[:3],
        calculation_enabled=True,
    )
    try:
        dlg.rate_editor.fields[('GB', 25.0)].setText('0.088')
        dlg.rate_editor.fields[('GF1', 55.0)].setText('0.176')
        dlg.model_selector.set_model('国产片式膜电阻器')
        dlg.fields['resistance_ohm'].setText('2200')
        # Switch back to 已知单件失效率 -> drafts must be intact!
        dlg.model_selector.set_model('已知单件失效率')
        assert dlg.rate_editor.fields[('GB', 25.0)].text() == '0.088'
        assert dlg.rate_editor.fields[('GF1', 55.0)].text() == '0.176'
        # Switch to 国产片式膜电阻器 -> 2200 draft intact!
        dlg.model_selector.set_model('国产片式膜电阻器')
        assert dlg.fields['resistance_ohm'].text() == '2200'
        # Return to 已知单件失效率 and save
        dlg.model_selector.set_model('已知单件失效率')
        dlg.accept_component()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.result_component.condition_rate('GB', 25) == pytest.approx(0.088)
        assert dlg.result_component.condition_rate('GF1', 55) == pytest.approx(0.176)
    finally:
        dlg.close()


# ---------------------------------------------------------------------------
# 4. InputSourceDialog (src/input_source_dialog.py) + End-to-End Evaluation
# ---------------------------------------------------------------------------


def test_input_source_dialog_gate_count_and_junction_temp_end_to_end(qapp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a[2] if len(a) > 2 else str(a)))

    # Clause 5.1.1.3: CMOS 200 transistors -> 53.333333 gates + [P1-3] ceiling + [P1-7] 3.3V ceiling + 32 pins ceiling!
    ic_params = {
        'technology': 'MOS',
        'package': '密封-双列直插(DIP)封装',
        'lead_count': 32,
        'quality': 'B2',
        'maturity': '稳定生产',
        'junction_temperature_C': 55,
        'supply_voltage_V': 3.3,
    }
    ic_comp = Component(
        'U1',
        '国产普通数字集成电路',
        1,
        dict(ic_params),
        # Also verify [P3-24]: unknown initial combo value falls back to index 0 rather than -1!
        input_sources={'gate_count': {'kind': 'gate_count', 'inputs': {'mode': 'transistors', 'technology': 'INVALID_TECH', 'transistors': 200}}},
    )
    gate_spec = next(f for f in MODELS['国产普通数字集成电路'].fields if f.key == 'gate_count')
    assert source_kind(gate_spec, '国产普通数字集成电路') == 'gate_count'

    dlg_gate = InputSourceDialog(ic_comp, gate_spec, environment='GB')
    try:
        assert dlg_gate.inputs['technology'].currentIndex() >= 0  # [P3-24] never -1
        # Test flip-flops mode first: 2 JK + 2 D = 2*8 + 2*6 = 28 gates
        dlg_gate.inputs['mode'].setCurrentIndex(dlg_gate.inputs['mode'].findData('flip_flops'))
        dlg_gate.inputs['jk_rs'].setText('2')
        dlg_gate.inputs['t_d'].setText('2')
        assert '28' in dlg_gate.preview.text()

        # Switch to CMOS 200 transistors -> 53.33 gates
        dlg_gate.inputs['mode'].setCurrentIndex(dlg_gate.inputs['mode'].findData('transistors'))
        dlg_gate.inputs['technology'].setCurrentIndex(dlg_gate.inputs['technology'].findData('CMOS'))
        dlg_gate.inputs['transistors'].setText('200')
        assert '53.33' in dlg_gate.preview.text()
        dlg_gate.save_source()
        assert dlg_gate.result() == QDialog.DialogCode.Accepted
        ic_comp.input_sources['gate_count'] = dlg_gate.result_source
    finally:
        dlg_gate.close()

    # Now attach Equation (2) junction_temperature: Tj = Tc(45) + theta_jc(20) * P(0.5) = 55 ℃
    tj_spec = next(f for f in MODELS['国产普通数字集成电路'].fields if f.key == 'junction_temperature_C')
    assert source_kind(tj_spec, '国产普通数字集成电路') == 'junction_temperature'
    dlg_tj = InputSourceDialog(ic_comp, tj_spec, environment='GB')
    try:
        dlg_tj.method.setCurrentIndex(1)
        dlg_tj.inputs['case_source'].setCurrentIndex(dlg_tj.inputs['case_source'].findData('specified'))
        dlg_tj.inputs['case_temperature_C'].setText('45')
        dlg_tj.inputs['resistance_source'].setCurrentIndex(dlg_tj.inputs['resistance_source'].findData('specified'))
        dlg_tj.inputs['thermal_resistance_C_W'].setText('20')
        dlg_tj.inputs['power_W'].setText('0.5')
        assert '55' in dlg_tj.preview.text()
        dlg_tj.save_source()
        assert dlg_tj.result() == QDialog.DialogCode.Accepted
        ic_comp.input_sources['junction_temperature_C'] = dlg_tj.result_source

        # Also test Table 24 & Table 25 domestic lookup
        dlg_tj.inputs['case_source'].setCurrentIndex(dlg_tj.inputs['case_source'].findData('table24'))
        dlg_tj.inputs['resistance_source'].setCurrentIndex(dlg_tj.inputs['resistance_source'].findData('table25'))
        assert '计算值' in dlg_tj.preview.text()
    finally:
        dlg_tj.close()

    # End-to-end evaluation with derived 53.33 gates + 55℃ Tj + 32 pins + 3.3V!
    ic_comp.parameters.pop('junction_temperature_C', None)
    ic_comp.validate()
    ev = evaluate_component(ic_comp, 'GB', 25, 'working')
    assert ev.error == '' and ev.rate is not None and ev.rate > 0

    # Clearing source back to manual input returns result_source = None
    dlg_clear = InputSourceDialog(ic_comp, tj_spec, environment='GB')
    try:
        dlg_clear.method.setCurrentIndex(0)
        assert '直接填写' in dlg_clear.preview.text()
        dlg_clear.save_source()
        assert dlg_clear.result() == QDialog.DialogCode.Accepted
        assert dlg_clear.result_source is None
    finally:
        dlg_clear.close()

    # Imported IC attempting Table 24 must warn and block save
    imp_ic = Component('U2', '进口通用数字集成电路', 1, dict(MODELS['进口通用数字集成电路'].example), domestic=False)
    imp_tj_spec = next(f for f in MODELS['进口通用数字集成电路'].fields if f.key == 'junction_temperature_C')
    dlg_imp = InputSourceDialog(imp_ic, imp_tj_spec, environment='GB')
    try:
        dlg_imp.method.setCurrentIndex(1)
        dlg_imp.inputs['case_source'].setCurrentIndex(dlg_imp.inputs['case_source'].findData('table24'))
        dlg_imp.inputs['thermal_resistance_C_W'].setText('20')
        dlg_imp.inputs['power_W'].setText('0.2')
        warnings.clear()
        dlg_imp.save_source()
        assert warnings and '进口器件' in warnings[-1]
    finally:
        dlg_imp.close()


def test_input_source_dialog_mounted_ic_and_project_rate(qapp, isolated_tmp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a[2] if len(a) > 2 else str(a)))

    # 1) Build sub-project for mounted_microcircuits (Table 40 requires IC with junction_temperature input_source)
    sub_ic_proj = Project('外贴芯片子工程')
    sub_ic_proj.conditions = [OperatingCondition(name='GB25', environment='GB', ambient_temperature_C=25, enabled=True)]
    chip_params = dict(MODELS['国产普通数字集成电路'].example)
    chip_params.pop('junction_temperature_C', None)
    chip = Component(
        'DIE1',
        '国产普通数字集成电路',
        2,
        chip_params,
        input_sources={
            'junction_temperature_C': {
                'kind': 'junction_temperature',
                'inputs': {
                    'case_source': 'specified',
                    'case_temperature_C': 40.0,
                    'resistance_source': 'specified',
                    'thermal_resistance_C_W': 15.0,
                    'power_W': 0.2,
                },
            }
        },
    )
    sub_ic_proj.components = [chip]
    sub_ic_path = isolated_tmp / 'sub_mounted_ic.json'
    save_project(sub_ic_path, sub_ic_proj)

    hybrid = Component('H1', '国产混合集成电路', 1, dict(MODELS['国产混合集成电路'].example))
    mounted_spec = next(f for f in MODELS['国产混合集成电路'].fields if f.key == 'mounted_ic_rate')
    assert source_kind(mounted_spec, '国产混合集成电路') == 'mounted_microcircuits'

    dlg_mounted = InputSourceDialog(hybrid, mounted_spec, environment='GB')
    try:
        dlg_mounted.method.setCurrentIndex(1)
        # Choosing invalid file triggers warning cleanly
        bad_file = isolated_tmp / 'corrupt.json'
        bad_file.write_text('{bad', encoding='utf-8')
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(bad_file), ''))
        dlg_mounted.choose_project()
        assert warnings

        # Choosing valid sub-project succeeds
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(sub_ic_path), ''))
        dlg_mounted.choose_project()
        assert dlg_mounted.file_label.text() == 'sub_mounted_ic.json'
        dlg_mounted.save_source()
        assert dlg_mounted.result() == QDialog.DialogCode.Accepted
        hybrid.input_sources['mounted_ic_rate'] = dlg_mounted.result_source
    finally:
        dlg_mounted.close()

    hybrid.parameters.pop('mounted_ic_rate', None)
    ev_hybrid = evaluate_component(hybrid, 'GB', 25, 'working')
    assert ev_hybrid.error == '' and ev_hybrid.rate is not None and ev_hybrid.rate > 0

    # 2) Build sub-project for project_rate (e.g. 国产压电陀螺 rear_circuit_rate) and also test [P2-15] missing condition!
    rear_proj = Project('后置电路工程')
    rear_proj.conditions = [OperatingCondition(name='GB25', environment='GB', ambient_temperature_C=25, enabled=True)]
    rear_proj.components = [
        Component('R1', '国产片式膜电阻器', 4, {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'})
    ]
    rear_xlsx = isolated_tmp / 'rear_supplier.xlsx'
    export_redacted_file(rear_xlsx, rear_proj)

    gyro_params = dict(MODELS['国产压电陀螺'].example)
    gyro_params.pop('rear_circuit_rate', None)
    gyro = Component('G1', '国产压电陀螺', 1, gyro_params)
    rear_spec = next(f for f in MODELS['国产压电陀螺'].fields if f.key == 'rear_circuit_rate')
    assert source_kind(rear_spec, '国产压电陀螺') == 'project_rate'

    dlg_rate = InputSourceDialog(gyro, rear_spec, environment='GB')
    try:
        dlg_rate.method.setCurrentIndex(1)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: (str(rear_xlsx), ''))
        dlg_rate.choose_project()
        dlg_rate.save_source()
        assert dlg_rate.result() == QDialog.DialogCode.Accepted
        gyro.input_sources['rear_circuit_rate'] = dlg_rate.result_source
    finally:
        dlg_rate.close()

    ev_match = evaluate_component(gyro, 'GB', 25, 'working')
    assert ev_match.error == '' and ev_match.rate is not None and ev_match.rate > 0

    # [P2-15]: Unmatched condition (GF1 55℃) on project_rate must be treated as pending (not config error)!
    ev_missing_cond = evaluate_component(gyro, 'GF1', 55, 'working')
    assert ev_missing_cond.rate is None
    assert not is_component_config_error(gyro.model, ev_missing_cond, calculation_enabled=True)


# ---------------------------------------------------------------------------
# 5. ProjectComponentImportDialog & ModuleDialog (src/project_import.py & project_modules.py)
# ---------------------------------------------------------------------------


def test_project_component_import_and_nested_module_dialogs(qapp, isolated_tmp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a[2] if len(a) > 2 else str(a)))

    # Inner leaf module
    leaf_proj = Project('底层电源子模块')
    leaf_proj.conditions = [OperatingCondition(name='GB25', environment='GB', ambient_temperature_C=25, enabled=True)]
    leaf_proj.components = [
        Component('R_LEAF', '国产片式膜电阻器', 2, {'resistance_ohm': 100.0, 'film': '薄膜', 'quality': 'A1P'})
    ]
    leaf_module = create_project_module(leaf_proj, name='U_LEAF_MOD', quantity=1)

    # Supplier project with enabled, disabled, and incomplete conditions + diverse component types
    sup_proj = Project('供应商组件工程')
    sup_proj.conditions = [
        OperatingCondition(name='常温工作', environment='GB', ambient_temperature_C=25, enabled=True),
        OperatingCondition(name='禁用工况', environment='GF1', ambient_temperature_C=50, enabled=False),
        OperatingCondition(name='待填工况', environment=None, ambient_temperature_C=None, enabled=True),
    ]
    redacted_sub = Project('脱敏源')
    redacted_sub.conditions = [OperatingCondition(name='GB25', environment='GB', ambient_temperature_C=25, enabled=True)]
    redacted_sub.components = [
        Component('C_RED', '国产1类瓷介电容器', 1, {'capacitance_uF': 0.001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.3})
    ]
    redacted_json = isolated_tmp / 'redacted_sub.json'
    export_redacted_file(redacted_json, redacted_sub)
    redacted_loaded, _ = load_supplier_bundle(redacted_json)

    var_comp = Component(
        'C_VAR',
        '国产1类瓷介电容器',
        1,
        {'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 'U_op / 50'},
        stress_variables={'U_op': 15},
        bom={'file_name': 'sup.csv', 'sha256': 'c' * 64, 'row': {'Value': '10nF', 'Manufacturer Part Number': 'CAP-10N'}, 'assigned_designators': ['C_VAR']},
    )
    sup_proj.components = [redacted_loaded.components[0], var_comp, leaf_module]

    sup_json = isolated_tmp / 'supplier_full.json'
    save_project(sup_json, sup_proj)
    loaded_sup, sup_sha = load_supplier_bundle(sup_json)

    # 1) Test clone_components validation guards
    with pytest.raises(ValueError, match='请选择'):
        clone_components(loaded_sup, 'not-a-list-string')
    with pytest.raises(ValueError, match='不在源工程中'):
        clone_components(loaded_sup, ['non-existent-id'])

    # 2) ProjectComponentImportDialog: both 'module' and 'components' modes
    dlg_imp = ProjectComponentImportDialog(loaded_sup, sup_json, source_sha256=sup_sha)
    try:
        assert dlg_imp.condition_table.available_count == 1
        assert dlg_imp.import_button.isEnabled()
        dlg_imp.name_edit.setText('   ')
        assert not dlg_imp.import_button.isEnabled()
        dlg_imp.name_edit.setText('引入组件A')
        dlg_imp.quantity_box.setValue(2)
        assert dlg_imp.import_button.isEnabled()

        # Switch to 'components' mode: filter, select_visible, hidden selection summary, clear_selection
        dlg_imp.mode_box.setCurrentIndex(1)
        assert not dlg_imp.import_button.isEnabled()
        dlg_imp.component_picker.search.setText('c_var')
        dlg_imp.component_picker.select_visible()
        assert len(dlg_imp.component_picker.selected_ids) == 1
        dlg_imp.component_picker.search.setText('u_leaf_mod')
        assert '在筛选范围外' in dlg_imp.component_picker.summary.text()
        dlg_imp.component_picker.search.clear()
        dlg_imp.component_picker.select_visible()
        assert len(dlg_imp.component_picker.selected_ids) == 3
        dlg_imp.accept_import()
        assert dlg_imp.result() == QDialog.DialogCode.Accepted
        assert len(dlg_imp.result_components) == 3
        assert '引入来源' in dlg_imp.result_components[1].reference
    finally:
        dlg_imp.close()

    # Import as a single 工程模块 + verify file-tamper protection
    dlg_mod_imp = ProjectComponentImportDialog(loaded_sup, sup_json, source_sha256=sup_sha)
    try:
        dlg_mod_imp.name_edit.setText('整机子模块')
        dlg_mod_imp.quantity_box.setValue(3)
        dlg_mod_imp.accept_import()
        assert dlg_mod_imp.result() == QDialog.DialogCode.Accepted
        imported_module = dlg_mod_imp.result_components[0]
        assert imported_module.model == '工程模块'
        assert imported_module.quantity == 3
    finally:
        dlg_mod_imp.close()

    # Tamper check: passing wrong expected_sha256 triggers QMessageBox.warning
    dlg_tamper = ProjectComponentImportDialog(loaded_sup, sup_json, source_sha256='0' * 64)
    try:
        warnings.clear()
        dlg_tamper.accept_import()
        assert warnings and '已修改' in warnings[-1]
    finally:
        dlg_tamper.close()

    # Also test importing from exported redacted .xlsx supplier bundle
    sup_xlsx = isolated_tmp / 'supplier.xlsx'
    export_redacted_file(sup_xlsx, leaf_proj)
    xlsx_proj, xlsx_sha = load_supplier_bundle(sup_xlsx)
    dlg_xlsx = ProjectComponentImportDialog(xlsx_proj, sup_xlsx, source_sha256=xlsx_sha)
    try:
        dlg_xlsx.accept_import()
        assert dlg_xlsx.result() == QDialog.DialogCode.Accepted
        assert dlg_xlsx.result_components[0].model == '工程模块'
    finally:
        dlg_xlsx.close()

    # 3) ModuleDialog: inspect all internal rows, open nested read-only ModuleDialog, and edit name/quantity
    nested_opened = []
    orig_exec = ModuleDialog.exec

    def _spy_nested_exec(self_dlg):
        nested_opened.append(self_dlg.read_only)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(ModuleDialog, 'exec', _spy_nested_exec)
    mod_dlg = ModuleDialog(imported_module, read_only=False)
    try:
        mod_dlg.tabs.setCurrentIndex(1)
        picker = mod_dlg.component_picker
        for r in range(picker.table.rowCount()):
            picker.table.setCurrentCell(r, 0)
            assert picker.detail.toPlainText().strip()
        # Row 2 is leaf_module ('工程模块') -> nested_button must be enabled!
        picker.table.setCurrentCell(2, 0)
        assert picker.nested_button.isEnabled()
        picker.view_nested_module()
        assert nested_opened == [True]

        # Filter hiding all rows clears detail gracefully
        picker.search.setText('no_such_item_zzz')
        assert not picker.nested_button.isEnabled()
        picker.search.clear()

        mod_dlg.name_edit.setText('改名后模块')
        mod_dlg.quantity_box.setValue(5)
        mod_dlg.accept_module()
        assert mod_dlg.result_component.name == '改名后模块'
        assert mod_dlg.result_component.quantity == 5
    finally:
        monkeypatch.setattr(ModuleDialog, 'exec', orig_exec)
        mod_dlg.close()

    # Evaluate imported_module under matching ('GB', 25) vs mismatched ('NS1', 60) condition
    assert evaluate_component(imported_module, 'GB', 25, 'working').rate > 0
    mismatch_ev = evaluate_component(imported_module, 'NS1', 60, 'working')
    assert mismatch_ev.rate is None
    assert not is_component_config_error('工程模块', mismatch_ev, calculation_enabled=True)


# ---------------------------------------------------------------------------
# 6. ModelPickerDialog & ModelSelector (src/model_picker.py) + [P2-35]
# ---------------------------------------------------------------------------


def test_model_picker_dialog_and_selector_state_and_origin_guards(qapp, monkeypatch):
    with pytest.raises(ValueError, match='国产选项'):
        domestic_context(None, '待配置', domestic='invalid')

    r_comp = _make_bom_comp('R1', value='1k', footprint='0603', desc='贴片电阻', domestic=True)
    picker = ModelPickerDialog(r_comp, current_model='国产片式膜电阻器', prediction_state='working')
    try:
        assert picker.models.count() > 0
        # Switch categories: 全部标准类别 (0), 按位号筛选 (1), IC, RES
        for row in range(min(5, picker.categories.count())):
            picker.categories.setCurrentRow(row)
        picker.categories.setCurrentRow(0)
        # Filter by method_box
        if picker.method_box.count() > 1:
            picker.method_box.setCurrentIndex(1)
            picker.method_box.setCurrentIndex(0)
        # Search hit & miss
        picker.search.setText('片式膜电阻器')
        assert picker.models.count() >= 1
        picker.models.setCurrentRow(0)
        assert picker.apply_button.isEnabled()
        picker.search.setText('绝不存在的模型名称_xyz')
        assert picker.models.count() == 0
        assert not picker.apply_button.isEnabled()
        picker.search.setText('片式膜电阻器')
        picker.models.setCurrentRow(0)
        picker.apply()
        assert picker.result() == QDialog.DialogCode.Accepted
        assert picker.result_model == '国产片式膜电阻器'
    finally:
        picker.close()

    # Mixed domestic=None and nonoperating state picker
    picker_mixed = ModelPickerDialog(None, domestic=None, prediction_state='nonoperating')
    try:
        assert '国产选项不同' in picker_mixed.status.text()
    finally:
        picker_mixed.close()

    # [P2-35]: ModelSelector must NOT raise uncaught ValueError on domestic/state mismatch,
    # and must prevent re-selecting '[与当前预计状态不符]' placeholder items!
    selector = ModelSelector(r_comp, model='国产电阻器（非工作详细）', domestic=True, prediction_state='working')
    try:
        mismatch_idx = selector.display.findData('国产电阻器（非工作详细）')
        assert mismatch_idx >= 0
        # Switch to valid working model
        selector.set_model('国产片式膜电阻器')
        assert selector.model == '国产片式膜电阻器'
        # Attempt to set imported model on domestic=True selector -> must not crash with uncaught ValueError
        try:
            selector.set_model('进口电阻器')
        except ValueError:
            pytest.fail('[P2-35] ModelSelector.set_model raised uncaught ValueError on domestic mismatch')
        assert selector.model == '国产片式膜电阻器'

        # Attempt to re-select incompatible state placeholder via combo index -> stays on valid working model!
        mismatch_idx = selector.display.findData('国产电阻器（非工作详细）')
        if mismatch_idx >= 0:
            selector.display.setCurrentIndex(mismatch_idx)
            assert selector.model == '国产片式膜电阻器'

        # choose() button integration
        monkeypatch.setattr(ModelPickerDialog, 'exec', lambda self_d: setattr(self_d, 'result_model', '国产碳膜电阻器') or QDialog.DialogCode.Accepted)
        selector.choose()
        assert selector.model == '国产碳膜电阻器'
    finally:
        selector.close()


# ---------------------------------------------------------------------------
# 7. NumberEdit, StressComboBox, CompositeComboBox, StressVariablesEditor,
#    RecordTableEdit, BatchDialog & ComponentDialog Regressions
# ---------------------------------------------------------------------------


def test_number_edit_strict_ascii_and_unicode_minus(qapp):
    ed = NumberEdit()
    try:
        exact_third = 1.0 / 3.0
        ed.set_number(exact_third)
        assert ed.value() == exact_third
        text_draft = ed.draft()
        ed.setText('−55')  # U+2212 minus from GJB tables
        assert ed.value() == -55.0
        ed.setText('－12.5e-2')  # U+FF0D fullwidth minus
        assert ed.value() == pytest.approx(-0.125)

        # [P2-37]: Python float underscore '1_5' and fullwidth digits '１２' MUST raise ValueError!
        for bad in ('1_5', '10_000', '１２', 'NaN', 'inf', ''):
            ed.setText(bad)
            with pytest.raises(ValueError):
                ed.value()

        ed.restore_draft(text_draft)
        assert ed.value() == exact_third
    finally:
        ed.close()


def test_stress_combo_box_reselection_and_expression_states(qapp):
    spec = next(f for f in MODELS['国产1类瓷介电容器'].fields if f.key == 'stress_ratio')
    box = StressComboBox(spec, empty_text='多种值 · 分别保留')
    try:
        # Select preset 0.2 -> float
        idx_02 = box.findData(0.2)
        assert idx_02 > 0
        box.setCurrentIndex(idx_02)
        assert isinstance(box.raw_value(), (int, float)) and box.raw_value() == pytest.approx(0.2)

        # [P1-14]: User manually types expression in lineEdit (emitting textEdited), then re-activates SAME index!
        box.lineEdit().setText('0.15 + 0.05')
        box.lineEdit().textEdited.emit('0.15 + 0.05')
        box.activated.emit(idx_02)
        if box.currentIndex() != idx_02:
            box.setCurrentIndex(idx_02)
        assert isinstance(box.raw_value(), (int, float)) and box.raw_value() == pytest.approx(0.2)

        # Re-selecting index 0 ('多种值 · 分别保留') must clear lineEdit, never write literal prompt text!
        box.lineEdit().setText('0.3')
        box.lineEdit().textEdited.emit('0.3')
        box.activated.emit(0)
        if box.currentIndex() != 0:
            box.setCurrentIndex(0)
        assert box.is_blank()
        assert box.lineEdit().text() == ''

        # Incomplete parenthesis & missing variable status labels
        box.setEditText('0.5 * (1 + ')
        assert '未完成' in box.result_label.text()
        box.setEditText('U_bus / 50')
        assert '变量待填写' in box.result_label.text()
        box.mixed_variables_provider = lambda: {'U_bus'}
        box.update_result()
        assert '分别取值' in box.result_label.text()
    finally:
        box.close()


def test_composite_combo_box_reentrancy_and_status_hints(qapp):
    spec = Input(
        'lead_count',
        '引出端数 NP',
        choices=(16, 28, 36, 48, 64, '＞64'),
        presets=((16, '16 脚 DIP'), (36, '36 脚 SOP'), '＞64'),
        ceil_choices=True,
        integer=True,
        min_value=1,
    )
    box = CompositeComboBox(spec)
    try:
        # [P2-39]: Selecting a custom-labeled preset via currentIndexChanged + activated must not reset currentIndex to -1
        box.setCurrentIndex(1)
        box.activated.emit(1)
        assert box.value() == 16
        assert '表列档位 16' in box.result_label.text()

        # Non-grid integer -> ceiling hint
        box.setEditText('32')
        assert box.value() == 32.0
        assert '向上取档 36' in box.result_label.text()

        # Overflow bin hint
        box.setEditText('100')
        assert '归入 ＞64 档' in box.result_label.text()

        # Non-integer warning
        box.setEditText('12.5')
        assert '整数' in box.result_label.text()

        # Out-of-bounds (< min_value) warning
        box.setEditText('0')
        assert '超出范围' in box.result_label.text()
    finally:
        box.close()


def test_batch_dialog_mixed_stress_variables_clear_preserves_originals(qapp):
    # [P1-13]: Clearing a mixed stress variable ('多种值 · 分别保留') must NOT wipe individual component values!
    c1 = Component(
        'C1', '国产1类瓷介电容器', 1,
        {'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 'U / 50'},
        stress_variables={'U': 15},
    )
    c2 = Component(
        'C2', '国产1类瓷介电容器', 1,
        {'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 'U / 50'},
        stress_variables={'U': 25},
    )
    bdlg = BatchDialog([c1, c2], 'GB', ambient_temperature_C=25, calculation_enabled=True)
    try:
        ed: StressVariablesEditor = bdlg.variables_editor
        assert ed is not None and 'U' in ed.mixed_names
        # Ensure literal '多种值 · 分别保留' is never stored in definitions
        assert ed.definitions.get('U') != '多种值 · 分别保留'

        # User edits U to '20', then clears U back to '' -> must restore mixed preservation!
        u_row = ed.names.index('U')
        ed.table.item(u_row, 1).setText('20')
        bdlg.preview()
        assert all(item.stress_variables['U'] == '20' for item in bdlg.result_components)

        ed.table.item(u_row, 1).setText('')
        bdlg.preview()
        assert bdlg.result_components is not None
        assert bdlg.result_components[0].stress_variables['U'] == 15
        assert bdlg.result_components[1].stress_variables['U'] == 25
    finally:
        bdlg.close()


def test_batch_dialog_cascading_choices_and_source_updates_unlock(qapp, monkeypatch):
    # 1) [P1-15]: Parent dropdown change in BatchDialog clears incompatible sub-choices on result_components
    j1 = Component(
        'J1', '国产电连接器', 1,
        {'connector_type': '低频连接器', 'counting_unit': '单个', 'quality': 'B2'},
    )
    j2 = Component(
        'J2', '国产电连接器', 1,
        {'connector_type': '低频连接器', 'counting_unit': '单个', 'quality': 'B2'},
    )
    bdlg_conn = BatchDialog([j1, j2], 'GB', ambient_temperature_C=25, calculation_enabled=True)
    try:
        ct_box = bdlg_conn.fields['connector_type']
        ct_box.setCurrentIndex(ct_box.findData('射频同轴连接器'))
        bdlg_conn.preview()
        assert bdlg_conn.result_components is not None
        # '单个' is invalid for '射频同轴连接器' -> must be pruned (pending, 0 config errors)!
        for item in bdlg_conn.result_components:
            assert item.parameters['connector_type'] == '射频同轴连接器'
            assert item.parameters.get('counting_unit') != '单个'
        assert bdlg_conn._error_count == 0
    finally:
        bdlg_conn.close()

    # 2) [P2-38]: Multi-hop sync_field_constraints on 国产电容网络
    dlg_capnet = ComponentDialog(
        Component('CN1', '国产电容网络', 1, dict(MODELS['国产电容网络'].example)),
        environment='GB', ambient_temperature_C=25,
    )
    try:
        cm_box = dlg_capnet.fields['capacitor_model']
        for target_cap in ('国产1类瓷介电容器', '国产可变电容器', '国产纸和薄膜电容器'):
            idx = cm_box.findData(target_cap)
            if idx >= 0:
                cm_box.setCurrentIndex(idx)
                sync_field_constraints(dlg_capnet.fields)
    finally:
        dlg_capnet.close()

    # 3) [P2-36]: Stale invalid scalar text in BatchDialog must NOT lock out Save after setting an input_source!
    u1 = Component('U1', '国产普通数字集成电路', 1, dict(MODELS['国产普通数字集成电路'].example))
    u2 = Component('U2', '国产普通数字集成电路', 1, dict(MODELS['国产普通数字集成电路'].example))
    bdlg_ic = BatchDialog([u1, u2], 'GB', ambient_temperature_C=25, calculation_enabled=True)
    try:
        bdlg_ic.toggle_details(True)
        tj_widget = bdlg_ic.fields['junction_temperature_C']
        tj_widget.setEditText('invalid_temp_abc')
        bdlg_ic.preview()
        assert not bdlg_ic.apply_button.isEnabled()

        # Configure valid junction_temperature source via configure_source -> unlocks Save!
        valid_src = {
            'kind': 'junction_temperature',
            'inputs': {'case_source': 'specified', 'case_temperature_C': 40.0, 'resistance_source': 'specified', 'thermal_resistance_C_W': 15.0, 'power_W': 0.5},
        }
        monkeypatch.setattr(
            InputSourceDialog,
            'exec',
            lambda self_d: setattr(self_d, 'result_source', valid_src) or QDialog.DialogCode.Accepted,
        )
        tj_spec = next(f for f in MODELS['国产普通数字集成电路'].fields if f.key == 'junction_temperature_C')
        bdlg_ic.configure_source(tj_spec)
        bdlg_ic.preview()
        assert not tj_widget.isEnabled()
        assert bdlg_ic.apply_button.isEnabled()
        assert bdlg_ic.result_components is not None

        # reset_fields clears changes cleanly
        bdlg_ic.reset_fields()
        bdlg_ic.preview()
        assert bdlg_ic.apply_button.isEnabled()
    finally:
        bdlg_ic.close()


def test_record_table_edit_focus_removal_blank_skip_and_batch_clear(qapp, monkeypatch):
    smt_example = MODELS['国产SMT互连'].example['connections'][0]
    smt_spec = MODELS['国产SMT互连'].fields[0]

    # 1) [P2-40]: Clicking '添加连接类别' without filling anything skips completely blank row {}
    dlg_smt = ComponentDialog(
        Component('PCB_SMT', '国产SMT互连', 1, {}),
        environment='GB', ambient_temperature_C=25,
    )
    try:
        rec_edit: RecordTableEdit = dlg_smt.fields['connections']
        rec_edit.add_button.click()
        # Clear any default values in the newly added row to make it completely blank {}
        for col, f in enumerate(rec_edit.fields):
            w = rec_edit.table.cellWidget(0, col)
            if hasattr(w, 'setCurrentIndex') and not hasattr(w, 'lineEdit'):
                w.setCurrentIndex(0)
            elif hasattr(w, 'lineEdit') and w.lineEdit() is not None:
                w.lineEdit().clear()
            elif hasattr(w, 'clear'):
                w.clear()
        cand = dlg_smt.component_candidate()
        assert cand.parameters.get('connections', []) == []
    finally:
        dlg_smt.close()

    # 2) [P1-16] Part A: Removing focused inner widget row when selectedRows() is empty
    rec = RecordTableEdit(smt_spec.record_fields, model='国产SMT互连')
    try:
        rec.set_number([copy.deepcopy(smt_example), copy.deepcopy(smt_example)])
        assert rec.table.rowCount() == 2
        rec.table.selectionModel().clearSelection()
        assert not rec.table.selectionModel().selectedRows()
        inner_widget = rec.table.cellWidget(1, 0)
        monkeypatch.setattr(rec.table, 'focusWidget', lambda: inner_widget)
        monkeypatch.setattr(QApplication, 'focusWidget', staticmethod(lambda: inner_widget))
        rec.remove_records()
        assert rec.table.rowCount() == 1
    finally:
        rec.close()

    # 3) [P1-16] Part B: Deleting all shared rows in BatchDialog actively clears records instead of reverting!
    s1 = Component('SMT1', '国产SMT互连', 1, {'connections': [copy.deepcopy(smt_example)]})
    s2 = Component('SMT2', '国产SMT互连', 1, {'connections': [copy.deepcopy(smt_example)]})
    bdlg_smt = BatchDialog([s1, s2], 'GB', ambient_temperature_C=25, calculation_enabled=True)
    try:
        batch_rec: RecordTableEdit = bdlg_smt.fields['connections']
        assert batch_rec.table.rowCount() == 1
        batch_rec.table.selectRow(0)
        batch_rec.remove_records()
        assert batch_rec.table.rowCount() == 0
        bdlg_smt.preview()
        assert bdlg_smt.result_components is not None
        for item in bdlg_smt.result_components:
            assert 'connections' not in item.parameters or item.parameters['connections'] == []
    finally:
        bdlg_smt.close()


def test_component_dialog_bom_designator_lock_dual_transistor_and_timer_stop(qapp, sample_project, monkeypatch):
    # 1) [P1-17]: Renaming a single-designator BOM component must NEVER overwrite bom['assigned_designators']!
    bom_r1 = _make_bom_comp('R1', value='10k', footprint='0603', desc='贴片电阻', mpn='RC0603')
    bom_r1.name = 'R1 · RC0603'
    bom_r1.model = '国产片式膜电阻器'
    bom_r1.parameters = {'resistance_ohm': 10000.0, 'film': '薄膜', 'quality': 'A1P'}

    dlg = ComponentDialog(
        bom_r1,
        environment='GB',
        ambient_temperature_C=25,
        conditions=sample_project.conditions,
        calculation_enabled=True,
    )
    try:
        dlg.toggle_details(True)
        assert not dlg.details.isHidden()
        dlg.fill_nominal()
        dlg.name.setText('R1 · 10 kΩ 精密采样电阻')
        dlg.accept_component()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.result_component.name == 'R1 · 10 kΩ 精密采样电阻'
        assert dlg.result_component.bom['assigned_designators'] == ['R1']

        # Verify subsequent append_bom still detects duplicate 'R1'!
        proj = Project('DupCheck')
        proj.components = [dlg.result_component]
        dup_src = BomSource('dup.csv', 'd' * 64, [['Designator', 'Value'], ['R1', '10k']], header_row=0)
        first, last, cols = dup_src.default_region()
        dup_imp = dup_src.build_import(first, last, cols, dup_src.default_mapping(cols))
        with pytest.raises(ValueError, match='R1'):
            append_bom(proj, dup_imp)

        # Standard link viewer inside ComponentDialog
        monkeypatch.setattr(QDialog, 'exec', lambda _self: QDialog.DialogCode.Rejected)
        assert dlg._open_standard_link('standard://table/217')
        assert not dlg._open_standard_link('https://example.com')
    finally:
        dlg.close()

    # 2) Dual-transistor Equation (24) live hint (valid & invalid constraint branches)
    dual = Component('Q1', '国产双极型双管', 1, dict(MODELS['国产双极型双管'].example))
    dlg_dual = ComponentDialog(dual, environment='GB', ambient_temperature_C=25)
    try:
        dlg_dual.calculate()
        assert 'S1₀ =' in dlg_dual.dual_stress_hint.text()
        assert '温度系数 C =' in dlg_dual.dual_stress_hint.text()
        # Violate P1 > PS1
        dlg_dual.fields['operating_power_1_W'].setText('5')
        dlg_dual.invalidate()
        assert '请核查' in dlg_dual.dual_stress_hint.text()
        # [P3-23]: Closing dialog stops preview_timer!
        assert dlg_dual.preview_timer.isActive()
        dlg_dual.reject()
        assert not dlg_dual.preview_timer.isActive()
    finally:
        dlg_dual.close()


def test_project_modules_error_and_digest_branches(isolated_tmp):
    from src.project_modules import module_project, source_condition_rates

    # Non-module component rejected by module_project
    with pytest.raises(ValueError, match='没有来源工程快照'):
        module_project(Component('R1', '国产片式膜电阻器', 1, {}))

    # Project with no calculable enabled condition rejected by create_project_module
    empty_proj = Project('无可用工况')
    empty_proj.conditions = [OperatingCondition('Disabled', 'GB', 25, enabled=False)]
    with pytest.raises(ValueError, match='没有已启用且计算完整的工况'):
        create_project_module(empty_proj, name='M_EMPTY')

    # Duplicate condition keys with conflicting rates (via aggregate error / mocked calculate)
    dup_proj = Project('冲突工况')
    c_a = OperatingCondition('A', 'GB', 25, enabled=True)
    c_b = OperatingCondition('B', 'GB', 25, enabled=True)
    dup_proj.conditions = [c_a, c_b]
    dup_proj.components = [
        Component('R1', '国产片式膜电阻器', 1, {'resistance_ohm': 100.0, 'film': '薄膜', 'quality': 'A1P'})
    ]
    orig_calc = dup_proj.calculate
    calls = []

    import dataclasses

    def _skewed_calc(cond):
        ctx = orig_calc(cond)
        calls.append(cond)
        if len(calls) == 2:
            return dataclasses.replace(ctx, total_rate=(ctx.total_rate or 0.001) * 2.0)
        return ctx

    dup_proj.calculate = _skewed_calc
    with pytest.raises(ValueError, match='不同计算结果'):
        source_condition_rates(dup_proj)

    # Content mismatch when expected_sha256 is None and disk file changed after loading
    valid_proj = Project('原始子工程')
    valid_proj.conditions = [OperatingCondition('GB25', 'GB', 25, enabled=True)]
    valid_proj.components = [
        Component('R1', '国产片式膜电阻器', 1, {'resistance_ohm': 100.0, 'film': '薄膜', 'quality': 'A1P'})
    ]
    disk_path = isolated_tmp / 'mutated_after_open.json'
    r1 = save_project(disk_path, valid_proj)
    mutated = copy.deepcopy(valid_proj)
    mutated.components[0].quantity = 9
    save_project(disk_path, mutated, expected_revision=r1)
    with pytest.raises(ValueError, match='已修改'):
        create_project_module(valid_proj, name='M_DIFF', source_path=disk_path, expected_sha256=None)


def test_stress_variables_delegate_painting_editor_and_drafts(qapp):
    from PyQt6.QtWidgets import QStyleOptionViewItem

    c1 = Component(
        'C1', '国产1类瓷介电容器', 1,
        {'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 'U / 50'},
        stress_variables={'U': 'U_base + 2', 'U_base': 10},
    )
    c2 = Component(
        'C2', '国产1类瓷介电容器', 1,
        {'capacitance_uF': 0.01, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 'U / 50'},
        stress_variables={'U': 'U_base + 8', 'U_base': 10},
    )
    bdlg = BatchDialog([c1, c2], 'GB', ambient_temperature_C=25, calculation_enabled=True)
    try:
        bdlg.show()
        qapp.processEvents()
        ed = bdlg.variables_editor
        assert ed is not None and {'U', 'U_base'} <= set(ed.names)
        # Render table to cover _StressVariableDelegate.initStyleOption
        ed.grab()
        delegate = ed.table.itemDelegateForColumn(1)
        editor_widget = delegate.createEditor(
            ed.table.viewport(), QStyleOptionViewItem(), ed.table.model().index(0, 1)
        )
        assert editor_widget is not None
        editor_widget.deleteLater()

        # Switch model in BatchDialog and back -> exercises variables_editor.draft() & variable_drafts restore
        bdlg.model_selector.set_model('国产2类瓷介电容器')
        bdlg.model_selector.set_model('国产1类瓷介电容器')
        assert 'U' in bdlg.variables_editor.mixed_names
    finally:
        bdlg.close()


def test_record_table_edit_complete_validation_drafts_and_events(qapp):
    from PyQt6.QtCore import QEvent
    from PyQt6.QtGui import QFocusEvent

    smt_spec = MODELS['国产SMT互连'].fields[0]
    smt_example = MODELS['国产SMT互连'].example['connections'][0]

    rec = RecordTableEdit(smt_spec.record_fields, model='国产SMT互连', batch_mode=False)
    try:
        assert rec.is_blank()
        with pytest.raises(ValueError, match='记录'):
            rec.add_record('not-a-dict')
        with pytest.raises(ValueError, match='记录列表'):
            rec.set_number('not-a-list')

        rec.add_record()
        # Blank choice or blank numeric raises ValueError when allow_incomplete=False
        for col, f in enumerate(rec.fields):
            w = rec.table.cellWidget(0, col)
            if hasattr(w, 'setCurrentIndex') and not hasattr(w, 'lineEdit'):
                w.setCurrentIndex(0)
        with pytest.raises(ValueError):
            rec.value(allow_incomplete=False)

        # Load valid record, test invalid numeric text error, then restore valid draft
        rec.set_number([copy.deepcopy(smt_example), copy.deepcopy(smt_example)])
        assert not rec.is_blank()
        saved_draft = rec.draft()
        dist_w = rec._widget(0, 'distance_mm')
        dist_w.setText('1_5')
        with pytest.raises(ValueError, match='数值无效'):
            rec.value(allow_incomplete=False)

        # Restore both dict draft and legacy list draft
        rec.restore_draft(saved_draft)
        vals = rec.value(allow_incomplete=False)
        assert len(vals) == 2
        rec.restore_draft(saved_draft['rows'])
        assert len(rec.value(allow_incomplete=False)) == 2

        # FocusIn eventFilter selects row and tracks _last_active_row
        rec.table.selectionModel().clearSelection()
        cell_w = rec.table.cellWidget(1, 0)
        rec.eventFilter(cell_w, QFocusEvent(QEvent.Type.FocusIn))
        assert rec._last_active_row == 1
        rec.table.selectionModel().clearSelection()
        rec.remove_records()
        assert rec.table.rowCount() == 1

        rec.clear_to_untouched()
        assert rec.table.rowCount() == 0 and not rec._modified
    finally:
        rec.close()


def test_component_and_batch_dialogs_reusable_profiles_disabled_calc_and_confirmations(qapp, sample_project, monkeypatch):
    # 1) ComponentDialog: pending component with opposite-state profile reuses parameters + calculation_enabled=False + qty=0
    pending_from_nonop = Component(
        'R_REUSE',
        '待配置',
        1,
        {},
        profiles={
            'nonoperating': {
                'model': '国产电阻器（非工作详细）',
                'parameters': {'kind': '片式膜电阻器', 'quality': 'A1P'},
                'condition_rates': [],
                'module_source': None,
                'disclosure': None,
                'stress_variables': {},
                'rate_state': 'nonoperating',
                'input_sources': {},
            }
        },
    )
    dlg_reuse = ComponentDialog(
        pending_from_nonop,
        environment='GB',
        ambient_temperature_C=25,
        calculation_enabled=False,
    )
    try:
        dlg_reuse.model_selector.set_model('国产片式膜电阻器')
        assert dlg_reuse.fields['quality'].currentData() == 'A1P'
        dlg_reuse.fill_nominal()
        dlg_reuse.calculate()
    finally:
        dlg_reuse.close()

    # Quantity = 0 ('待填写') + visible config-error confirmation in ComponentDialog (Class 1 ceramic cap at 125℃ > 85℃ limit)
    err_cap = Component(
        'C_ERR',
        '国产1类瓷介电容器',
        2,
        {'capacitance_uF': 0.001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
    )
    hot_conds = [OperatingCondition('Hot125', 'GB', 125, enabled=True)]
    dlg_err = ComponentDialog(
        err_cap,
        environment='GB',
        ambient_temperature_C=125,
        conditions=hot_conds,
        calculation_enabled=True,
    )
    try:
        dlg_err.show()
        qapp.processEvents()
        dlg_err.quantity.setValue(0)
        dlg_err.calculate()
        assert '数量待填写' in dlg_err.preview.text()
        dlg_err.quantity.setValue(1)
        dlg_err.calculate()
        assert '配置异常' in dlg_err.preview.text()

        # First click No on confirmation -> stays open; then click Yes -> accepts!
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
        dlg_err.accept_component()
        assert dlg_err.result() != QDialog.DialogCode.Accepted
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
        dlg_err.accept_component()
        assert dlg_err.result() == QDialog.DialogCode.Accepted
    finally:
        dlg_err.close()

    # 2) BatchDialog: zero-edit apply, calculation_enabled=False, 已知单件失效率 mode, and visible config-error confirmation
    ext1 = Component('E1', '已知单件失效率', 1, {}, condition_rates=[{'environment': 'GB', 'ambient_temperature_C': 25, 'rate': 0.01}])
    ext2 = Component('E2', '已知单件失效率', 1, {})
    bdlg_ext = BatchDialog([ext1, ext2], 'GB', ambient_temperature_C=25, calculation_enabled=False)
    try:
        bdlg_ext.preview()
        bdlg_ext.apply()  # Zero-edit apply
        assert bdlg_ext.result() == QDialog.DialogCode.Accepted
    finally:
        bdlg_ext.close()

    err1 = copy.deepcopy(err_cap)
    err2 = copy.deepcopy(err_cap)
    err2.name = 'C_ERR2'
    bdlg_err = BatchDialog([err1, err2], 'GB', ambient_temperature_C=125, calculation_enabled=True)
    try:
        bdlg_err.show()
        qapp.processEvents()
        bdlg_err.fields['quality'].setCurrentIndex(bdlg_err.fields['quality'].findData('A1M'))
        bdlg_err.preview()
        assert bdlg_err._error_count == 2
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
        bdlg_err.apply()
        assert bdlg_err.result() != QDialog.DialogCode.Accepted
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
        bdlg_err.apply()
        assert bdlg_err.result() == QDialog.DialogCode.Accepted
    finally:
        bdlg_err.close()

