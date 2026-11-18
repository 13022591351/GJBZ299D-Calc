"""Headless Qt UI integration tests covering MainWindow, StandardPage, ToolboxPage, and dialogs."""

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QComboBox, QDialog, QMessageBox

from src.batch_dialog import BatchDialog
from src.component_fields import COLUMNS
from src.dialogs import ComponentDialog
from src.domain import Component
from src.environment_editor import ENV_COLUMNS
from src.model_registry import recommend
from src.reference_data import fragments
from src.theme import current_colors, current_theme
from src.ui.bom_wizard import BomPreMapDialog, suggest_model
from src.ui.toolbox import ToolboxPage
from src.window import MainWindow


def test_mainwindow_theme_undo_autosave_and_exports_hookup(qapp, sample_project, isolated_tmp):
    proj_path = isolated_tmp / 'ui_proj.json'
    win = MainWindow(sample_project)
    assert win.save_to(proj_path)
    assert win.dirty is False

    # Theme toggle switches dark <-> light cleanly.
    start_theme = current_theme()
    win.toggle_theme()
    assert current_theme() != start_theme
    assert current_colors()['highlight_cell']
    win.toggle_theme()
    assert current_theme() == start_theme

    # Quick quality edit pushes undo and writes .autosave.json sidecar instead of clobbering disk save.
    win.select_component_row(0)
    assert win.project.components[0].parameters['quality'] == 'A1P'
    win.quick_edit_parameter(0, 'quality', 'A1M')
    assert win.project.components[0].parameters['quality'] == 'A1M'
    assert win.dirty is True
    assert win.undo_stack.canUndo()
    autosave_sidecar = proj_path.parent / f'.{proj_path.name}.autosave.json'
    assert autosave_sidecar.is_file()

    # Undo & redo restore both parameter state and selection.
    win.undo_stack.undo()
    assert win.project.components[0].parameters['quality'] == 'A1P'
    win.undo_stack.redo()
    assert win.project.components[0].parameters['quality'] == 'A1M'

    # Explicit save clears dirty bit and removes .autosave.json.
    assert win.save()
    assert win.dirty is False
    assert not autosave_sidecar.exists()
    win.close()


def test_filter_chips_sort_modes_pareto_share_and_paint(qapp, sample_project, monkeypatch):
    win = MainWindow(sample_project)
    win.show()
    qapp.processEvents()

    # Status filter chips.
    assert win.proxy.rowCount() == 3
    win.filter_chips['calculated'].click()
    assert win.proxy.rowCount() == 2
    win.filter_chips['pending'].click()
    assert win.proxy.rowCount() == 1
    win.filter_chips['all'].click()
    assert win.proxy.rowCount() == 3

    # 5 sort modes + share column formatting.
    for mode in ('bom', 'model', 'weighted_desc', 'rate_desc', 'quantity_desc'):
        idx = win.sort_box.findData(mode)
        assert idx >= 0
        win.sort_box.setCurrentIndex(idx)
    assert win.proxy.index(0, next(i for i, c in enumerate(COLUMNS) if c.key == 'quantity')).data(0x0100) == 4
    win.sort_box.setCurrentIndex(win.sort_box.findData('model'))
    assert win.proxy.rowCount() == 3
    win.sort_box.setCurrentIndex(win.sort_box.findData('weighted_desc'))
    # Pending U1 (None) must stay last when sorting descending.
    last_idx = win.proxy.index(win.proxy.rowCount() - 1, 0)
    last_rows = win.rows_for_index(last_idx)
    assert win.project.components[last_rows[0]].model == '待配置'

    share_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'share')
    shares = [
        win.proxy.index(r, share_col).data(0x0100)  # Qt.ItemDataRole.UserRole
        for r in range(win.proxy.rowCount())
    ]
    numeric_shares = [v for v in shares if isinstance(v, (int, float))]
    assert sum(numeric_shares) == pytest.approx(100.0, abs=1e-6)

    # CategoryParetoBar click filters tree.
    entries = win.category_bar.entries()
    assert entries
    first_cat = entries[0][0]
    win.category_filter_changed(first_cat)
    assert win.proxy.rowCount() == 1
    win.category_filter_changed(None)
    assert win.proxy.rowCount() == 3

    # Force full widget paint (CategoryParetoBar + ShareBarDelegate).
    pixmap = win.grab()
    assert pixmap.width() > 0

    # Duplicate & remove (with confirmation monkeypatched).
    win.select_component_row(0)
    win.duplicate_components()
    assert len(win.project.components) == 4
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    win.select_component_row(1)
    win.remove_component()
    assert len(win.project.components) == 3
    win.undo_stack.undo()
    assert len(win.project.components) == 4

    win.dirty = False
    win.close()


def test_inspector_and_standard_uri_deep_linking(qapp, sample_project):
    win = MainWindow(sample_project)

    # No selection -> Overview (page 0).
    assert win.inspector.stack.currentIndex() == 0
    assert 'MTBF' in win.inspector.overview_summary.text()

    # Single selection -> Page 1 + quick quality update.
    win.select_component_row(0)
    assert win.inspector.stack.currentIndex() == 1
    assert 'A1P' in win.inspector.quick_quality.currentText()
    idx_a1m = win.inspector.quick_quality.findData('A1M')
    assert idx_a1m >= 0
    win.inspector.quick_quality.setCurrentIndex(idx_a1m)
    assert win.project.components[0].parameters['quality'] == 'A1M'

    # Deep-link to table + specific cell highlight.
    cell_id = fragments('217')[0]['cells'][0]['id']
    assert win.open_standard_uri(f'standard://table/217/{cell_id}')
    assert win.stack.currentIndex() == 1
    assert win.standard_page.table_model.highlighted_cell_id == cell_id
    assert not win.open_standard_uri('standard://section/4.3')

    win.dirty = False
    win.close()


def test_toolbox_page_calculators(qapp):
    toolbox = ToolboxPage()
    assert toolbox.tabs.count() == 4
    # No automatic calculation before clicking calculate buttons.
    assert 'S =' not in toolbox.dual_result.text()
    assert 'Tj =' not in toolbox.tj_result.text()
    assert 'NG =' not in toolbox.gate_result.text()
    assert 'MTBF =' not in toolbox.conv_result.text()

    toolbox.dual_calc_btn.click()
    assert 'S =' in toolbox.dual_result.text()
    toolbox.tj_calc_btn.click()
    assert 'Tj =' in toolbox.tj_result.text()
    toolbox.gate_calc_btn.click()
    assert 'NG =' in toolbox.gate_result.text()

    toolbox.conv_lambda.setText('2')
    assert toolbox.conv_fit.text() == '1000'
    assert 'MTBF =' not in toolbox.conv_result.text()
    toolbox.conv_calc_btn.click()
    assert toolbox.conv_fit.text() == '2000'
    assert 'MTBF =' in toolbox.conv_result.text()
    toolbox.close()


def test_bom_pre_map_wizard_suggests_without_guessing_stress_or_quality(qapp):
    items = [
        Component('R10', '待配置', 2, {}, domestic=True,
                  bom={'file_name': 'bom.csv', 'sha256': '0' * 64,
                       'row': {'Value': '4.7k', 'Footprint': '0603', 'Description': '贴片电阻'},
                       'assigned_designators': ['R10', 'R11']}),
        Component('C10', '待配置', 1, {}, domestic=True,
                  bom={'file_name': 'bom.csv', 'sha256': '0' * 64,
                       'row': {'Value': '100nF', 'Footprint': '0603', 'Description': 'X7R 陶瓷电容'},
                       'assigned_designators': ['C10']}),
        Component('Y1', '待配置', 1, {}, domestic=True,
                  bom={'file_name': 'bom.csv', 'sha256': '0' * 64,
                       'row': {'Value': '12MHz', 'Footprint': 'HC49', 'Description': '无源晶振'},
                       'assigned_designators': ['Y1']}),
        Component('U9', '待配置', 1, {}, domestic=True,
                  bom={'file_name': 'bom.csv', 'sha256': '0' * 64,
                       'row': {'Value': 'MCU', 'Footprint': 'QFP64', 'Description': '微控制器'},
                       'assigned_designators': ['U9']}),
    ]
    assert suggest_model(items[0])[0] == '国产片式膜电阻器'
    assert suggest_model(items[1])[0] == '国产2类瓷介电容器'
    assert suggest_model(items[2])[0] == '国产谐振器'
    assert suggest_model(items[3])[0] == '待配置'

    wizard = BomPreMapDialog(items, [0, 1, 2, 3], prediction_state='working')
    assert wizard.has_suggestions()
    wizard.apply_to(items)
    assert items[0].model == '国产片式膜电阻器'
    assert items[0].parameters['resistance_ohm'] == pytest.approx(4700.0)
    # Never invent quality grade or stress ratio!
    assert 'quality' not in items[0].parameters
    assert 'stress_ratio' not in items[1].parameters
    wizard.close()


def test_component_and_batch_dialogs_unified_fields(qapp, sample_project):
    # ComponentDialog dual-transistor equation 24 preview.
    comp = Component('Q1', '国产双极型双管', 1, {'kind': '普通晶体管', 'application': '线性放大'}, domestic=True)
    dlg = ComponentDialog(comp, environment='GB', ambient_temperature_C=25,
                          conditions=sample_project.conditions, calculation_enabled=True)
    assert not dlg.dual_stress_hint.isHidden()
    assert '式（24）' in dlg.dual_stress_hint.text()
    dlg.close()

    # BatchDialog across two pending resistors.
    r_items = [
        Component('RA', '待配置', 1, {}, domestic=True,
                  bom={'file_name': 'bom.csv', 'sha256': '0' * 64, 'row': {'Value': '1k'},
                       'assigned_designators': ['RA']}),
        Component('RB', '待配置', 1, {}, domestic=True,
                  bom={'file_name': 'bom.csv', 'sha256': '0' * 64, 'row': {'Value': '2k'},
                       'assigned_designators': ['RB']}),
    ]
    bdlg = BatchDialog(r_items, 'GB', ambient_temperature_C=25, calculation_enabled=True)
    bdlg.model_selector.set_model('国产片式膜电阻器')
    bdlg.fields['film'].setCurrentIndex(bdlg.fields['film'].findData('薄膜'))
    bdlg.fields['quality'].setCurrentIndex(bdlg.fields['quality'].findData('A1P'))
    bdlg.preview()
    assert '统一修改' in bdlg.status.text()
    bdlg.apply()
    assert bdlg.result() == QDialog.DialogCode.Accepted
    assert all(item.model == '国产片式膜电阻器' for item in bdlg.result_components)
    assert all(item.parameters['quality'] == 'A1P' for item in bdlg.result_components)
    bdlg.close()


def test_dual_state_isolation_and_mission_sources(qapp, sample_project):
    sample_project.components = [sample_project.components[0]]
    orig_params = dict(sample_project.components[0].parameters)
    orig_rate = sample_project.calculate(sample_project.conditions[0]).total_rate
    assert orig_rate is not None and orig_rate > 0

    win = MainWindow(sample_project)
    win.show()
    qapp.processEvents()

    # Switch working -> nonoperating via UI combo box.
    nonop_idx = win.state_box.findData('nonoperating')
    win.state_box.setCurrentIndex(nonop_idx)
    qapp.processEvents()
    assert win.project.prediction_state == 'nonoperating'
    comp = win.project.components[0]
    assert comp.model == '国产电阻器（非工作详细）'
    assert 'working' in comp.profiles
    assert comp.profiles['working']['model'] == '国产片式膜电阻器'
    assert comp.profiles['working']['parameters'] == orig_params

    # Configure valid nonoperating parameters and confirm both states appear in MissionTab.
    comp.parameters = {'kind': '金属膜', 'quality': 'A1P', 'rated_power_W': 0.25, 'cycle_rate': 10}
    win.refresh()
    qapp.processEvents()
    nonop_rate = win.project.calculate(win.project.conditions[0]).total_rate
    assert nonop_rate is not None and nonop_rate > 0

    sources = win.analysis_page.panels['mission'].current_sources
    states_present = {item['prediction_state'] for item in sources}
    assert states_present == {'working', 'nonoperating'}

    # Switch back nonoperating -> working: original model/parameters & nonoperating profile both intact.
    work_idx = win.state_box.findData('working')
    win.state_box.setCurrentIndex(work_idx)
    qapp.processEvents()
    restored = win.project.components[0]
    assert restored.model == '国产片式膜电阻器'
    assert restored.parameters == orig_params
    assert 'nonoperating' in restored.profiles
    assert win.project.calculate(win.project.conditions[0]).total_rate == pytest.approx(orig_rate, rel=1e-12)
    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_flat_inline_group_expansion_no_summary_row_and_combobox_height(qapp, isolated_tmp):
    from src.domain import OperatingCondition, Project
    from src.redacted_export import build_redacted_project

    c1 = Component(
        name='R1',
        model='国产片式膜电阻器',
        quantity=2,
        parameters={'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'},
        domestic=True,
        bom={'file_name': 'g.csv', 'sha256': '0' * 64,
             'row': {'Value': '1k', 'Footprint': '0603', 'Manufacturer Part Number': 'RC0603FR-071KL'},
             'assigned_designators': ['R1']},
    )
    c2 = Component(
        name='R2',
        model='国产片式膜电阻器',
        quantity=2,
        parameters={'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'},
        domestic=True,
        bom={'file_name': 'g.csv', 'sha256': '0' * 64,
             'row': {'Value': '1k', 'Footprint': '0603', 'Manufacturer Part Number': 'RC0603FR-071KL'},
             'assigned_designators': ['R2']},
    )
    c3 = Component(
        name='C1',
        model='国产1类瓷介电容器',
        quantity=1,
        parameters={'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
        domestic=True,
        bom={'file_name': 'g.csv', 'sha256': '0' * 64,
             'row': {'Value': '100pF', 'Footprint': '0603', 'Manufacturer Part Number': 'C0603C101J5GAC'},
             'assigned_designators': ['C1']},
    )
    proj = Project('分组测试工程', components=[c1, c2, c3],
                   conditions=[OperatingCondition('地面良好 25℃', 'GB', 25, enabled=True)])
    win = MainWindow(proj)
    win.show()
    qapp.processEvents()

    assert win.table.rootIsDecorated() is False
    assert win.state_box.height() >= 32
    assert win.sort_box.height() >= 32

    share_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'share')
    index_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'index')
    model_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'model')

    # Collapsed: 1 summary row (R1, R2) + 1 standalone row (C1) = 2 rows; share sums to 100%.
    assert win.proxy.rowCount() == 2
    assert str(win.proxy.index(0, index_col).data()).startswith('▸')
    collapsed_shares = [win.proxy.index(r, share_col).data(0x0100) for r in range(win.proxy.rowCount())]
    assert sum(collapsed_shares) == pytest.approx(100.0, abs=1e-6)

    # Toggle click on column 0 expands inline and drops the duplicate summary row.
    win._table_clicked(win.proxy.index(0, index_col))
    qapp.processEvents()
    assert win.proxy.rowCount() == 3
    assert str(win.proxy.index(0, index_col).data()).startswith('▾')
    expanded_shares = [win.proxy.index(r, share_col).data(0x0100) for r in range(win.proxy.rowCount())]
    assert sum(expanded_shares) == pytest.approx(100.0, abs=1e-6)

    # Toggle back collapses neatly to 2 rows.
    win._table_clicked(win.proxy.index(0, index_col))
    qapp.processEvents()
    assert win.proxy.rowCount() == 2
    win.dirty = False
    win.close()

    # Redacted component table displays the normal model label and anonymous designators, and title shows v1.0.
    redacted_proj = build_redacted_project(proj)
    rwin = MainWindow(redacted_proj)
    designator_col = [c.key for c in COLUMNS].index('designators')
    assert rwin.proxy.index(0, model_col).data() == '片式膜电阻器'
    assert [rwin.proxy.index(r, designator_col).data() for r in range(rwin.proxy.rowCount())] == ['000001', '000002', '000003']
    assert 'v1.0' in rwin.windowTitle()
    rwin.dirty = False
    rwin.close()


def test_default_operating_condition_is_gb_25c(qapp):
    from src.domain import Project

    default_proj = Project()
    assert default_proj.conditions[0].environment == 'GB'
    assert default_proj.conditions[0].ambient_temperature_C == 25
    assert 'parameters' in default_proj.group_fields
    assert 'stress' in default_proj.group_fields

    win = MainWindow(default_proj)
    win.show()
    qapp.processEvents()
    win.environment_editor.add_condition()
    qapp.processEvents()
    assert len(win.project.conditions) == 2
    assert win.project.conditions[1].environment == 'GB'
    assert win.project.conditions[1].ambient_temperature_C == 25
    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_stress_grouping_default_and_table_combo_min_widths(qapp):
    from src.domain import OperatingCondition, Project
    from src.record_editor import RecordTableEdit
    from src.standards import MODELS

    # Identical capacitors except stress_ratio stay ungrouped by default because 'stress' is active.
    c1 = Component(
        name='C1',
        model='国产1类瓷介电容器',
        quantity=1,
        parameters={'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.2},
        domestic=True,
        bom={'file_name': 'g.csv', 'sha256': '0' * 64,
             'row': {'Value': '100pF', 'Footprint': '0603', 'Manufacturer Part Number': 'C101'},
             'assigned_designators': ['C1']},
    )
    c2 = Component(
        name='C2',
        model='国产1类瓷介电容器',
        quantity=1,
        parameters={'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.5},
        domestic=True,
        bom={'file_name': 'g.csv', 'sha256': '0' * 64,
             'row': {'Value': '100pF', 'Footprint': '0603', 'Manufacturer Part Number': 'C101'},
             'assigned_designators': ['C2']},
    )
    proj = Project('应力分组与表格下拉框测试', components=[c1, c2],
                   conditions=[OperatingCondition('地面良好 25℃', 'GB', 25, enabled=True)])
    win = MainWindow(proj)
    win.show()
    qapp.processEvents()
    assert win.proxy.rowCount() == 2

    # EnvironmentEditor embedded combos fit cleanly and resist shrinking below minimum widths.
    env_table = win.environment_editor.table
    env_box, temp_box = win.environment_editor.boxes[proj.conditions[0].identifier]
    assert env_box.minimumSizeHint().width() <= env_table.columnWidth(2)
    assert temp_box.minimumSizeHint().width() <= env_table.columnWidth(3)
    env_table.horizontalHeader().resizeSection(2, 60)
    qapp.processEvents()
    assert env_table.columnWidth(2) >= 145

    # MissionTab embedded state combo fits cleanly and clamps when dragged narrow.
    mission = win.analysis_page.panels['mission']
    if mission.table.rowCount() == 0:
        mission.add_phase()
    mission_combo = mission.table.cellWidget(0, 1)
    assert mission_combo.minimumSizeHint().width() <= mission.table.columnWidth(1)
    mission.table.horizontalHeader().resizeSection(1, 50)
    qapp.processEvents()
    assert mission.table.columnWidth(1) >= 115

    # RecordTableEdit (SMT connections) embedded combo fits cleanly and clamps properly.
    smt_fields = next(f.record_fields for f in MODELS['国产SMT互连']['fields'] if f.record_fields)
    rec = RecordTableEdit(smt_fields, model='国产SMT互连')
    rec.show()
    rec.add_record()
    qapp.processEvents()
    combo_col = next(
        c for c in range(rec.table.columnCount())
        if isinstance(rec.table.cellWidget(0, c), QComboBox)
    )
    rec_combo = rec.table.cellWidget(0, combo_col)
    assert rec_combo.minimumSizeHint().width() <= rec.table.columnWidth(combo_col)
    rec.table.horizontalHeader().resizeSection(combo_col, 50)
    qapp.processEvents()
    assert rec.table.columnWidth(combo_col) >= 135
    rec.close()

    # BomPreMapDialog column 3 model combo fits comfortably.
    wizard = BomPreMapDialog([c1, c2], [0, 1], prediction_state='working')
    wizard.show()
    qapp.processEvents()
    if wizard._controls:
        wiz_combo = wizard._controls[0][1]
        assert wiz_combo.minimumSizeHint().width() <= wizard.table.columnWidth(3)
    wizard.close()

    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_mission_tab_active_editor_does_not_crash_on_row_mutation(qapp, sample_project):
    win = MainWindow(sample_project)
    win.show()
    win.navigate(3)
    mission = win.analysis_page.panels['mission']
    win.analysis_page.tabs.setCurrentWidget(mission)
    if mission.table.rowCount() == 0:
        mission.add_phase()
    qapp.processEvents()

    win.activateWindow()
    mission.table.setFocus()
    mission.table.setCurrentCell(0, 0)
    mission.table.editItem(mission.table.item(0, 0))
    qapp.processEvents()
    delegate = mission.table.itemDelegate()
    assert delegate.editor is not None
    delegate.editor.setText('自定义阶段')

    # Clicking add_phase while cell editor is open used to trigger destroyEditor -> state() -> AttributeError.
    mission.add_phase()
    qapp.processEvents()
    assert mission.table.rowCount() == 2
    assert mission.table.item(0, 0).text() == '自定义阶段'
    assert isinstance(mission.table.cellWidget(1, 1), QComboBox)

    mission.table.selectRow(1)
    mission.remove_phase()
    qapp.processEvents()
    assert mission.table.rowCount() == 1

    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_calculated_status_green_and_responsive_columns(qapp, sample_project):
    win = MainWindow(sample_project)
    win.show()
    win.resize(1120, 720)
    qapp.processEvents()

    status_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'status')
    fg = win.proxy.index(0, status_col).data(Qt.ItemDataRole.ForegroundRole)
    assert fg == QColor(current_colors()['success'])

    env_editor = win.environment_editor
    env_status_item = env_editor.table.item(0, ENV_COLUMNS['status'])
    assert '待配置' in env_status_item.text()
    assert env_status_item.foreground().color() == QColor(current_colors()['warning'])

    # Configure remaining pending component so status becomes '已计算' and turns green.
    win.project.components[2].model = '国产片式膜电阻器'
    win.project.components[2].parameters = {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'}
    win.refresh()
    qapp.processEvents()
    env_status_item = env_editor.table.item(0, ENV_COLUMNS['status'])
    assert env_status_item.text() == '已计算'
    assert env_status_item.foreground().color() == QColor(current_colors()['success'])

    # Theme switch updates status foreground color automatically.
    win.toggle_theme()
    qapp.processEvents()
    assert env_editor.table.item(0, ENV_COLUMNS['status']).foreground().color() == QColor(current_colors()['success'])
    win.toggle_theme()
    qapp.processEvents()

    # Window maximize/expand distributes extra width across multiple flexible columns instead of stretching only MTBF.
    narrow_widths = [env_editor.table.columnWidth(c) for c in range(env_editor.table.columnCount())]
    win.resize(1800, 900)
    qapp.processEvents()
    wide_widths = [env_editor.table.columnWidth(c) for c in range(env_editor.table.columnCount())]
    expanded_cols = sum(1 for c in range(1, 7) if wide_widths[c] > narrow_widths[c])
    assert expanded_cols >= 3

    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_nonoperating_cycle_rate_dropdown_and_auto_match_traps(qapp, sample_project):
    comp = Component('C_NONOP', '国产电容器（非工作详细）', 1, {}, domestic=True)
    dlg = ComponentDialog(
        comp,
        environment='GB',
        ambient_temperature_C=25,
        conditions=sample_project.conditions,
        prediction_state='nonoperating',
        calculation_enabled=True,
    )
    kind_box = dlg.fields['kind']
    kind_box.setCurrentIndex(kind_box.findData('瓷介电容器 / 1 类瓷介'))
    qual_box = dlg.fields['quality']
    qual_box.setCurrentIndex(qual_box.findData('A1P'))
    mount_box = dlg.fields['mounting']
    mount_box.setCurrentIndex(mount_box.findData('片式'))
    cycle_widget = dlg.fields['cycle_rate']
    assert isinstance(cycle_widget, QComboBox)
    idx_lt = cycle_widget.findData('＜0.3')
    assert idx_lt >= 0
    cycle_widget.setCurrentIndex(idx_lt)
    assert dlg.parameters()['cycle_rate'] == '＜0.3'
    dlg.close()

    def _bom_comp(name, value, footprint, desc):
        return Component(
            name,
            '待配置',
            1,
            {},
            domestic=True,
            bom={'file_name': 'b.csv', 'sha256': '0' * 64,
                 'row': {'Value': value, 'Footprint': footprint, 'Description': desc},
                 'assigned_designators': [name]},
        )

    assert recommend(_bom_comp('TP1', 'TP', 'TP_1MM', '测试点')).categories == ()
    assert suggest_model(_bom_comp('TP1', '10k', '0603', '贴片电阻测试点'))[0] == '待配置'
    assert suggest_model(_bom_comp('FB1', '600R@100MHz', '0603', '贴片磁珠'))[0] == '待配置'
    assert suggest_model(_bom_comp('R1', '1206Ω', 'AXIAL-0.4', '金属膜电阻'))[0] == '国产金属膜电阻器'
    assert suggest_model(_bom_comp('R2', '10mR', '2512', '合金采样电阻'))[0] == '待配置'
    assert suggest_model(_bom_comp('RN1', '10k', '0603X4', '排阻 4P2R'))[0] == '国产电阻网络'
    assert suggest_model(_bom_comp('C1', '100nF', '0603', 'MLCC 瓷介电容'), domestic=True)[0] == '待配置'
    assert suggest_model(_bom_comp('C1', '100nF', '0603', 'MLCC 瓷介电容'), domestic=False)[0] == '进口电容器'
    assert suggest_model(_bom_comp('C2', '100nF', '0603', 'II类瓷介电容'))[0] == '国产2类瓷介电容器'
    assert suggest_model(_bom_comp('C3', '10uF', 'CASE-B', '电解电容'))[0] == '待配置'
    assert suggest_model(_bom_comp('C4', '10uF', 'CAK35', '非固体钽电容'))[0] == '国产非固体钽电解电容器'
    assert suggest_model(_bom_comp('Y1', '25MHz', 'OSC3225', '有源晶振 3.3V'))[0] == '国产振荡器'
    assert suggest_model(_bom_comp('Y2', '25MHz', 'SMD3225', '晶振'))[0] == '待配置'
    assert suggest_model(_bom_comp('Y3', '25MHz', 'SMD3225', '无源晶振 20pF'))[0] == '国产谐振器'
    assert suggest_model(_bom_comp('D1', 'TL431', 'SOT-23', '可调基准源'))[0] == '待配置'
    assert suggest_model(_bom_comp('D2', '1N4007', 'DO-41', 'SEALED RECTIFIER DIODE'))[0] == '国产普通二极管'
    assert suggest_model(_bom_comp('ZD1', '5.1V', 'SOD-123', '稳压二极管'), domestic=False)[0] == '进口其他分立半导体'


def test_ui_cross_state_parameter_reuse_and_pending_dialogs(qapp, sample_project):

    # Replace pending U1 with resonator so MainWindow switch demonstrates both partial & complete reuse.
    sample_project.components[2] = Component('Y1', '国产谐振器', 1, {'quality': 'A1'}, domestic=True)
    win = MainWindow(sample_project)
    win.show()
    qapp.processEvents()

    win.state_box.setCurrentIndex(win.state_box.findData('nonoperating'))
    qapp.processEvents()
    r1, c1, y1 = win.project.components
    assert r1.parameters == {'kind': '片式膜电阻器', 'quality': 'A1P'}
    assert c1.parameters == {'kind': '瓷介电容器 / 1 类瓷介', 'quality': 'A1P', 'mounting': '片式'}
    assert y1.model == '国产谐振器（非工作详细）'
    assert y1.parameters == {'quality': 'A1'}
    assert win.project.evaluate(y1, condition=win.project.conditions[0]).rate > 0
    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_failure_mode_tab_autosave_no_crash_and_mission_state_switch(qapp, sample_project, isolated_tmp):
    # Configure both working & nonoperating states across project components so condition sources exist for both states.
    sample_project.components = [sample_project.components[0], sample_project.components[1]]
    sample_project.set_prediction_state('nonoperating', auto_suggest=True)
    for comp in sample_project.components:
        comp.parameters['cycle_rate'] = 10
    sample_project.set_prediction_state('working', auto_suggest=True)

    proj_path = isolated_tmp / 'analysis_mission.json'
    win = MainWindow(sample_project)
    assert win.save_to(proj_path)

    # Switching failure-mode table F.1 -> F.2 -> F.10 and resetting to empty project must never crash autosave validation.
    fm_tab = win.analysis_page.panels['failure_modes']
    for tbl in ('F.2', 'F.10', 'F.1'):
        fm_tab.table_choice.setCurrentIndex(fm_tab.table_choice.findData(tbl))
        qapp.processEvents()
        assert fm_tab.state()['category']
    assert win.project.validate() is win.project

    # MissionTab: adding the same condition twice and toggling row 2 state to nonoperating auto-syncs nonoperating rate.
    mission = win.analysis_page.panels['mission']
    mission.table.setRowCount(0)
    mission.basis.setCurrentIndex(mission.basis.findData('duty'))
    work_idx = next(i for i in range(mission.sources.count()) if mission.sources.itemData(i)['prediction_state'] == 'working')
    mission.sources.setCurrentIndex(work_idx)
    mission.add_selected_source()
    mission.add_selected_source()
    state_combo_row2 = mission.table.cellWidget(1, 1)
    state_combo_row2.setCurrentIndex(state_combo_row2.findData('nonoperating'))
    qapp.processEvents()

    mission.table.item(0, 2).setText('0.1')
    mission.table.item(1, 2).setText('0.9')
    qapp.processEvents()
    result = mission.calculate()
    assert result is not None
    expected_work = sample_project.calculate(sample_project.conditions[0], prediction_state='working').total_rate
    expected_nonop = sample_project.calculate(sample_project.conditions[0], prediction_state='nonoperating').total_rate
    assert result.outputs['rate'] == pytest.approx(0.1 * expected_work + 0.9 * expected_nonop, rel=1e-9)

    # New project resets FailureModeTab cleanly while autosave hook is cleared.
    win.undo_stack.clear()
    win.dirty = False
    assert win.new_project()
    assert win.analysis_page.panels['failure_modes'].state()['category'] == '线性集成电路'
    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_coverage_author_offline_compact_columns_and_single_file_drop(qapp, sample_project, isolated_tmp, monkeypatch):
    from PyQt6.QtCore import QMimeData, QPointF, QUrl
    from PyQt6.QtGui import QDragEnterEvent
    from PyQt6.QtWidgets import QTextBrowser

    from src.pages import coverage_page
    from src.storage import save_project
    from src.workbook_io import export_workbook_xlsx

    # Coverage page keeps developer contact and offline declaration verbatim.
    page = coverage_page()
    browser = page.findChild(QTextBrowser)
    assert browser is not None
    plain = browser.toPlainText()
    assert 'DuoYuWang' in plain
    assert 'thirteenking.wang@gmail.com' in plain
    assert 'https://github.com/13022591351/GJBZ299D-Calc' in plain
    assert 'https://github.com/13022591351/GJBZ299D-Calc.git' not in plain
    assert 'MIT License' in plain
    assert 'GPT6-Astra' in plain
    assert 'Gemini 4 Carbon' in plain
    assert '软件不建立互联网连接' in plain
    page.close()

    # Maximized/wide MainWindow keeps compact centered status & right-aligned weighted columns.
    win = MainWindow(sample_project)
    assert not win.windowIcon().isNull()
    assert (win.table_model.project is sample_project)
    from pathlib import Path
    assert (Path(__file__).resolve().parent.parent / 'src' / 'assets' / 'gjb299d-calc.ico').is_file()
    win.resize(1920, 1080)
    win.show()
    qapp.processEvents()
    header = win.table.header()
    status_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'status')
    weighted_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'weighted')
    assert win.table.columnWidth(status_col) <= 85
    assert win.table.columnWidth(weighted_col) <= 145
    assert win.proxy.index(0, status_col).data(Qt.ItemDataRole.TextAlignmentRole) == Qt.AlignmentFlag.AlignCenter
    assert header.sectionViewportPosition(weighted_col) == (
        header.sectionViewportPosition(status_col) + header.sectionSize(status_col)
    )
    assert win.view_summary.wordWrap() is False
    assert '清空' in win.project_buttons

    # Undoable clear_project empties components, conditions, and analyses while keeping path.
    clear_path = isolated_tmp / 'to_clear.json'
    save_project(clear_path, sample_project)
    assert win.open_path(clear_path, prompt_autosave=False)
    assert len(win.project.components) == 3
    assert win.clear_project(confirm=False)
    assert win.path == clear_path
    assert win.project.components == []
    assert win.project.analysis == {}
    win.undo_stack.undo()
    assert len(win.project.components) == 3
    win.undo_stack.clear()
    win.dirty = False

    # Prepare dropped test files.
    drop_proj_path = isolated_tmp / 'dropped.json'
    sample_project.name = '拖入JSON工程'
    save_project(drop_proj_path, sample_project)
    xlsx_proj_path = isolated_tmp / 'dropped_report.xlsx'
    clean_proj = sample_project
    clean_proj.components = [c for c in clean_proj.components if c.model != '待配置']
    clean_proj.name = '拖入工作簿工程'
    export_workbook_xlsx(xlsx_proj_path, clean_proj)
    bad_txt_path = isolated_tmp / 'note.txt'
    bad_txt_path.write_text('not a project', encoding='utf-8')

    # Single .json file dragEnter accepted & opens project.
    mime_json = QMimeData()
    mime_json.setUrls([QUrl.fromLocalFile(str(drop_proj_path))])
    enter_json = QDragEnterEvent(QPointF(20, 20).toPoint(), Qt.DropAction.CopyAction, mime_json, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.dragEnterEvent(enter_json)
    assert enter_json.isAccepted()

    # Multiple files dragEnter rejected.
    mime_multi = QMimeData()
    mime_multi.setUrls([QUrl.fromLocalFile(str(drop_proj_path)), QUrl.fromLocalFile(str(xlsx_proj_path))])
    enter_multi = QDragEnterEvent(QPointF(20, 20).toPoint(), Qt.DropAction.CopyAction, mime_multi, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    win.dragEnterEvent(enter_multi)
    assert not enter_multi.isAccepted()

    # Avoid modal warnings during rejection checks.
    errors = []
    monkeypatch.setattr(win, 'error', lambda err: errors.append(str(err)))
    win.dirty = False

    assert not win.open_dropped_files([drop_proj_path, xlsx_proj_path])
    assert not win.open_dropped_files([isolated_tmp])
    assert not win.open_dropped_files([bad_txt_path])
    assert len(errors) == 3

    assert win.open_dropped_files([drop_proj_path])
    assert win.project.name == '拖入JSON工程'

    win.dirty = False
    assert win.open_dropped_files([xlsx_proj_path])
    assert win.project.name == '拖入工作簿工程'

    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_ui_remediation_regressions(qapp, sample_project, isolated_tmp, monkeypatch):
    from src.standards import MODELS
    from src.stress_edit import StressComboBox

    proj_a = isolated_tmp / 'proj_a.json'
    proj_b = isolated_tmp / 'proj_b.json'
    win = MainWindow(sample_project)
    assert win.save_to(proj_a)
    autosave_a = proj_a.parent / f'.{proj_a.name}.autosave.json'
    assert not autosave_a.exists()

    # 1. Undo from saved state must set dirty=True AND write .autosave.json; undo to clean removes it.
    win.quick_edit_parameter(0, 'quality', 'A1M')
    assert autosave_a.is_file()
    win.undo_stack.undo()
    assert win.dirty is False
    assert not autosave_a.exists()

    win.quick_edit_parameter(0, 'quality', 'A1M')
    assert win.save()
    assert not autosave_a.exists()
    win.undo_stack.undo()
    assert win.dirty is True
    assert autosave_a.is_file()

    # save_to(new_path) cleans up old path's autosave sidecar.
    assert win.save_to(proj_b)
    assert not autosave_a.exists()

    # 2. AnalysisPage consecutive mutations coalesce via merge_id into one undo step.
    fm_tab = win.analysis_page.panels['failure_modes']
    win.undo_stack.clear()
    fm_tab.table_choice.setCurrentIndex(fm_tab.table_choice.findData('F.2'))
    fm_tab.table_choice.setCurrentIndex(fm_tab.table_choice.findData('F.10'))
    assert win.undo_stack.count() == 1
    assert win.undo_stack.index() == 1
    win.undo_stack.undo()
    assert fm_tab.table_choice.currentData() == 'F.1'

    # 3. StandardPage whitespace-insensitive symbol search ('πQ' -> 'π Q').
    win.standard_page.search.setText('πQ')
    assert win.standard_page.list.count() > 0

    # 4. ComponentInspector quick stress subtraction expression & HTML escaping.
    win.select_component_row(1)
    assert win.inspector._active_row == 1
    assert '1类瓷介电容器' in win.inspector.single_title.text()
    win.inspector.quick_stress.setText('1-0.6')
    win.inspector._on_stress_apply()
    assert win.project.components[1].parameters['stress_ratio'] == '1-0.6'
    assert win.inspector.quick_feedback.text() == ''

    # Multi-selection error escaping against '<LDO>' tag injection.
    win.project.components[0].name = 'U1 <LDO>'
    win.project.components[0].model = '国产片式膜电阻器'
    win.project.components[0].parameters = {}
    win.refresh()
    win.inspector.update_selection(win.project, win.view_context, [0, 1])
    assert '&lt;LDO&gt;' in win.inspector.multi_breakdown.toHtml()

    # 5. Dropping a corrupted .xlsx calls win.error instead of raising out of Qt drop path.
    corrupt_xlsx = isolated_tmp / 'corrupt.xlsx'
    corrupt_xlsx.write_bytes(b'not-a-zip')
    seen_errors = []
    monkeypatch.setattr(win, 'error', lambda err: seen_errors.append(str(err)))
    win.filter_chips['calculated'].click()
    win.category_filter_changed('R')
    assert win.proxy.category_filter == 'R'
    assert win.open_dropped_files([corrupt_xlsx]) is False
    assert seen_errors

    # 6. replace_project resets active filter chips and category filter.
    win.dirty = False
    assert win.new_project()
    assert win.filter_chips['all'].isChecked()
    assert win.proxy.status_filter == 'all'
    assert win.proxy.category_filter is None

    # 7. StressComboBox starts cleanly empty and preserves numeric presets.
    sbox = StressComboBox(next(f for f in MODELS['国产1类瓷介电容器'].fields if f.key == 'stress_ratio'))
    assert sbox.lineEdit().text() == ''
    assert sbox.is_blank()
    sbox.setCurrentIndex(2)
    assert sbox.raw_value() == 0.2
    sbox.close()

    # 8. ToolboxPage rejects fractional transistor count and localizes empty float errors.
    win.toolbox_page.gate_transistors.setText('12.9')
    win.toolbox_page.calculate_gates()
    assert '整数' in win.toolbox_page.gate_result.text()
    win.toolbox_page.gate_transistors.setText('')
    win.toolbox_page.calculate_gates()
    assert '请填写有效的有限数值' in win.toolbox_page.gate_result.text()
    win.toolbox_page.calculate_tj()
    assert 'p036-' not in win.toolbox_page.tj_result.text()
    assert '表 24' in win.toolbox_page.tj_result.text()

    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_component_dialog_editable_device_temperature_combo(qapp):
    from src.dialogs import ComponentDialog
    from src.domain import Component
    from src.standards import MODELS, evaluate

    comp = Component('U1', '国产普通数字集成电路', 1, dict(MODELS['国产普通数字集成电路'].example))
    dlg = ComponentDialog(comp, environment='GB', ambient_temperature_C=25, calculation_enabled=True)
    tj_box = dlg.fields['junction_temperature_C']
    assert tj_box.isEditable()
    tj_box.setEditText('47.6')
    dlg.accept_component()
    assert dlg.result_component is not None
    assert dlg.result_component.parameters['junction_temperature_C'] == pytest.approx(47.6)
    ev = evaluate('国产普通数字集成电路', dlg.result_component.parameters, 'GB', ambient_temperature_C=25)
    ev_ceil = evaluate(
        '国产普通数字集成电路',
        dict(MODELS['国产普通数字集成电路'].example, junction_temperature_C=49),
        'GB',
        ambient_temperature_C=25,
    )
    assert ev.error == '' and ev.rate == ev_ceil.rate
    dlg.close()


def test_mission_tab_stale_snapshot_sync_save_reopen_and_state_switch(qapp, sample_project, isolated_tmp):
    from src.analysis_methods.workspace import recompute_analyses, resolve_phase_sources
    from src.storage import load_project, save_project

    # Configure both working & nonoperating profiles for all components so both states compute.
    sample_project.components = [sample_project.components[0], sample_project.components[1]]
    sample_project.set_prediction_state('nonoperating', auto_suggest=True)
    for comp in sample_project.components:
        comp.parameters['cycle_rate'] = 10
    sample_project.set_prediction_state('working', auto_suggest=True)

    cond = sample_project.conditions[0]
    live_work = sample_project.calculate(cond, prediction_state='working').total_rate
    live_nonop = sample_project.calculate(cond, prediction_state='nonoperating').total_rate
    assert live_work is not None and live_nonop is not None and live_work != live_nonop

    # Simulate stale/mismatched saved MissionTab data (like a saved file with outdated rate & snapshot state).
    stale_snapshot = {
        'id': cond.identifier,
        'name': cond.name,
        'project_name': sample_project.name,
        'environment': cond.environment,
        'ambient_temperature_C': cond.ambient_temperature_C,
        'prediction_state': 'working',
        'rate': 99.9,
        'scope': 'current',
    }
    sample_project.analysis = {
        'version': 1,
        'mission': {
            'basis': 'duty',
            'phases': [
                {
                    'name': cond.name,
                    'state': 'working',
                    'duty_cycle': '0.1',
                    'rate': '99.9',
                    'environment': cond.environment,
                    'ambient_temperature_C': str(cond.ambient_temperature_C),
                    'source': f'{sample_project.name} / {cond.name}',
                    'source_snapshot': dict(stale_snapshot),
                },
                {
                    'name': cond.name,
                    'state': 'nonoperating',
                    'duty_cycle': '0.9',
                    'rate': '99.9',
                    'environment': cond.environment,
                    'ambient_temperature_C': str(cond.ambient_temperature_C),
                    'source': f'{sample_project.name} / {cond.name}',
                    'source_snapshot': dict(stale_snapshot),
                },
            ],
            'external_sources': [],
        },
    }

    proj_path = isolated_tmp / 'stale_mission.json'
    save_project(proj_path, sample_project)

    win = MainWindow()
    assert win.open_path(proj_path, prompt_autosave=False)
    # Syncing live condition rates on open must not falsely mark a clean saved file as dirty.
    assert win.dirty is False
    assert win.undo_stack.count() == 0

    mission = win.analysis_page.panels['mission']
    expected_life = 0.1 * live_work + 0.9 * live_nonop
    assert mission.last_result is not None
    assert mission.last_result.outputs['rate'] == pytest.approx(expected_life, rel=1e-9)

    synced_state = win.analysis_page.state()
    assert win.project.analysis == synced_state
    assert win._committed_snapshot.analysis == synced_state
    p0, p1 = synced_state['mission']['phases']
    assert p0['source_snapshot']['prediction_state'] == 'working'
    assert p0['source_snapshot']['rate'] == pytest.approx(live_work, rel=1e-12)
    assert p1['source_snapshot']['prediction_state'] == 'nonoperating'
    assert p1['source_snapshot']['rate'] == pytest.approx(live_nonop, rel=1e-12)

    # Toggle phase 2 state working <-> nonoperating via table combo.
    combo = mission.table.cellWidget(1, 1)
    combo.setCurrentIndex(combo.findData('working'))
    qapp.processEvents()
    assert win.dirty is True
    assert mission.state()['phases'][1]['source_snapshot']['prediction_state'] == 'working'
    assert float(mission.table.item(1, 3).text()) == pytest.approx(live_work, rel=1e-9)

    combo.setCurrentIndex(combo.findData('nonoperating'))
    qapp.processEvents()
    assert mission.state()['phases'][1]['source_snapshot']['prediction_state'] == 'nonoperating'
    assert float(mission.table.item(1, 3).text()) == pytest.approx(live_nonop, rel=1e-9)

    # Save and reopen: synced analysis persists cleanly and opens with dirty == False.
    assert win.save()
    assert win.dirty is False
    saved_analysis = win.project.analysis
    win.close()

    reloaded, _ = load_project(proj_path)
    assert reloaded.analysis == saved_analysis

    win2 = MainWindow()
    assert win2.open_path(proj_path, prompt_autosave=False)
    assert win2.dirty is False
    assert win2.project.analysis == saved_analysis
    mission2 = win2.analysis_page.panels['mission']
    assert mission2.last_result is not None
    assert mission2.last_result.outputs['rate'] == pytest.approx(expected_life, rel=1e-9)
    results, errors = recompute_analyses(win2.project.analysis, current_sources=mission2.current_sources)
    assert errors == {} and len(results) == 1

    # When nonoperating becomes incomplete, MissionTab clears stale rate & shows clear reason without number-parse crash.
    win2.project.components[0].profiles.pop('nonoperating', None)
    win2.refresh()
    qapp.processEvents()
    assert mission2.table.item(1, 3).text() == ''
    assert mission2.last_result is None
    assert '暂不可计算' in mission2.output.toPlainText()
    assert '待配置' in mission2.output.toPlainText()
    assert cond.name in mission2.output.toPlainText()

    # Direct resolve_phase_sources on stale/blank phases also behaves cleanly.
    resolved = resolve_phase_sources(saved_analysis['mission']['phases'], [
        {
            'id': cond.identifier,
            'name': cond.name,
            'project_name': sample_project.name,
            'environment': cond.environment,
            'ambient_temperature_C': cond.ambient_temperature_C,
            'prediction_state': 'working',
            'rate': live_work,
            'scope': 'current',
        },
        {
            'id': cond.identifier,
            'name': cond.name,
            'project_name': sample_project.name,
            'environment': cond.environment,
            'ambient_temperature_C': cond.ambient_temperature_C,
            'prediction_state': 'nonoperating',
            'rate': live_nonop,
            'scope': 'current',
        },
    ])
    assert resolved[1]['rate'] == pytest.approx(live_nonop, rel=1e-12)
    with pytest.raises(ValueError, match='非工作状态尚未完成计算'):
        resolve_phase_sources(
            [{'name': cond.name, 'state': 'nonoperating', 'duty_cycle': '0.9', 'rate': '',
              'environment': 'GB', 'ambient_temperature_C': '25',
              'source': f'{sample_project.name} / {cond.name}',
              'source_snapshot': dict(stale_snapshot, prediction_state='nonoperating')}],
            [{'id': cond.identifier, 'name': cond.name, 'project_name': sample_project.name,
              'environment': 'GB', 'ambient_temperature_C': 25, 'prediction_state': 'working',
              'rate': live_work, 'scope': 'current'}],
        )

    win2.dirty = False
    win2.close()


def test_config_error_vs_pending_display_and_confirm_save(qapp, sample_project, monkeypatch):
    from src.domain import OperatingCondition, Project

    # Prevent any unexpected modal QMessageBox.warning from blocking headless tests.
    monkeypatch.setattr(
        QMessageBox,
        'warning',
        lambda *a, **kw: pytest.fail(f'Unexpected QMessageBox.warning: {a[1:3]}'),
    )

    # Prepare one calculated item (R1), one unconfigured item (U1), one incomplete item (R2),
    # and one broken config-error item (J1 exceeding Table 289 Class II 120 ℃ limit).
    r1 = sample_project.components[0]
    u1 = sample_project.components[2]
    r2 = Component('R2', '国产片式膜电阻器', 1, {}, domestic=True)
    bad_conn_params = {
        'connector_type': '低频连接器',
        'quality': 'A1',
        'counting_unit': '单个',
        'contacts': 100,
        'socket_structure': '绞线插针',
        'mating_per_1000h': 100.0,
        'material_class': 'Ⅱ类',
        'material_max_temperature_C': 125.0,
        'temperature_method': '实际温升',
        'temperature_rise_C': 10.0,
    }
    bad_conn = Component('J1', '国产电连接器', 1, dict(bad_conn_params), domestic=True)
    proj = Project(
        '配置异常与待配置区分测试',
        components=[r1, u1, r2, bad_conn],
        conditions=[OperatingCondition('工况 1', 'GB', 25, enabled=True)],
    )
    win = MainWindow(proj)
    win.show()
    qapp.processEvents()

    status_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'status')
    weighted_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'weighted')

    # ComponentTree shows '待配置' for U1/R2 (warning) and '配置异常' for J1 (error).
    statuses = [win.proxy.index(r, status_col).data() for r in range(win.proxy.rowCount())]
    assert statuses == ['已计算', '待配置', '待配置', '配置异常']
    assert win.proxy.index(3, weighted_col).data() == '配置异常'
    assert win.proxy.index(1, weighted_col).data() == '待配置'
    assert win.proxy.index(3, status_col).data(Qt.ItemDataRole.ForegroundRole) == QColor(current_colors()['error'])
    assert win.proxy.index(2, status_col).data(Qt.ItemDataRole.ForegroundRole) == QColor(current_colors()['warning'])
    assert '120' in str(win.proxy.index(3, status_col).data(Qt.ItemDataRole.ToolTipRole))

    # Filter chips separate 待配置 vs 配置异常 accurately.
    assert '(2)' in win.filter_chips['pending'].text()
    assert '(1)' in win.filter_chips['error'].text()
    win.filter_chips['error'].click()
    assert win.proxy.rowCount() == 1
    assert win.project.components[win.rows_for_index(win.proxy.index(0, 0))[0]].name == 'J1'
    win.filter_chips['pending'].click()
    assert win.proxy.rowCount() == 2
    win.filter_chips['all'].click()

    # EnvironmentEditor distinguishes mixed vs error-only vs pending-only states.
    env_table = win.environment_editor.table
    assert env_table.item(0, ENV_COLUMNS['status']).text() == '待配置 2 · 异常 1'
    assert env_table.item(0, ENV_COLUMNS['total']).text() == '配置异常'
    assert env_table.item(0, ENV_COLUMNS['status']).foreground().color() == QColor(current_colors()['error'])

    # Grouped ComponentTree row with config_error displays error status & tooltip.
    bom_j1 = Component(
        'J1',
        '国产电连接器',
        1,
        dict(bad_conn_params),
        domestic=True,
        bom={'file_name': 'b.csv', 'sha256': '0' * 64,
             'row': {'Value': 'J100', 'Footprint': 'CONN100', 'Manufacturer Part Number': 'J-100'},
             'assigned_designators': ['J1']},
    )
    bom_j2 = Component(
        'J2',
        '国产电连接器',
        1,
        dict(bad_conn_params),
        domestic=True,
        bom={'file_name': 'b.csv', 'sha256': '0' * 64,
             'row': {'Value': 'J100', 'Footprint': 'CONN100', 'Manufacturer Part Number': 'J-100'},
             'assigned_designators': ['J2']},
    )
    win.project.components = [r1, bom_j1, bom_j2]
    win.refresh()
    qapp.processEvents()
    assert win.proxy.rowCount() == 2
    assert win.proxy.index(1, status_col).data() == '2 项配置异常'
    assert win.proxy.index(1, weighted_col).data() == '配置异常'
    assert win.proxy.index(1, status_col).data(Qt.ItemDataRole.ForegroundRole) == QColor(current_colors()['error'])
    assert '120' in str(win.proxy.index(1, status_col).data(Qt.ItemDataRole.ToolTipRole))
    assert env_table.item(0, ENV_COLUMNS['status']).text() == '配置异常 2 项'
    assert env_table.item(0, ENV_COLUMNS['total']).text() == '配置异常'
    assert env_table.item(0, ENV_COLUMNS['status']).foreground().color() == QColor(current_colors()['error'])

    # Incomplete-only condition status remains '待配置 1 项' in warning color.
    win.project.components = [r1, u1]
    win.refresh()
    qapp.processEvents()
    assert env_table.item(0, ENV_COLUMNS['status']).text() == '待配置 1 项'
    assert env_table.item(0, ENV_COLUMNS['total']).text() == '待配置'
    assert env_table.item(0, ENV_COLUMNS['status']).foreground().color() == QColor(current_colors()['warning'])

    # ComponentDialog shows '配置异常：', syncs dynamic constraints, and prompts before saving when visible.
    dlg = ComponentDialog(bad_conn, environment='GB', ambient_temperature_C=25, calculation_enabled=True)
    assert dlg.preview.text().startswith('配置异常：')
    assert [dlg.fields['material_max_temperature_C'].itemData(i) for i in range(1, dlg.fields['material_max_temperature_C'].count())] == [120, 100, 90, 80]
    assert '120' in dlg.fields['material_max_temperature_C'].result_label.text()
    dlg.show()
    qapp.processEvents()

    prompts = []

    def _deny(*args, **kwargs):
        prompts.append(args[1] if len(args) > 1 else '')
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, 'question', _deny)
    dlg.accept_component()
    assert len(prompts) == 1
    assert dlg.result_component is None
    assert dlg.result() != QDialog.DialogCode.Accepted

    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    dlg.accept_component()
    assert dlg.result_component is not None
    assert dlg.result() == QDialog.DialogCode.Accepted
    dlg.close()

    # ComponentDialog preserves dependent field order (e.g. counting_unit before connector_type in dict).
    rf_comp = Component(
        'J_RF',
        '国产电连接器',
        1,
        {
            'counting_unit': '插头',
            'connector_type': '射频同轴连接器',
            'quality': 'B2',
            'contacts': 1,
            'socket_structure': '针孔',
            'mating_per_1000h': 0.5,
        },
        domestic=True,
    )
    rf_dlg = ComponentDialog(rf_comp, environment='GB', ambient_temperature_C=25, calculation_enabled=True)
    assert rf_dlg.fields['counting_unit'].currentData() == '插头'
    assert '配置异常' not in rf_dlg.preview.text() and '待配置' not in rf_dlg.preview.text()
    rf_dlg.close()

    # Incomplete component in ComponentDialog shows '待配置：' and saves without confirmation prompt.
    incomplete_dlg = ComponentDialog(r2, environment='GB', ambient_temperature_C=25, calculation_enabled=True)
    assert incomplete_dlg.preview.text().startswith('待配置：')
    incomplete_dlg.show()
    qapp.processEvents()
    monkeypatch.setattr(
        QMessageBox,
        'question',
        lambda *a, **kw: pytest.fail('incomplete component should not trigger config-error confirm dialog'),
    )
    incomplete_dlg.accept_component()
    assert incomplete_dlg.result_component is not None
    incomplete_dlg.close()

    # BatchDialog distinguishes config_error, syncs constraints with shared base_params, and confirms when visible.
    bad_conn_diff_contacts = Component(
        'J3',
        '国产电连接器',
        1,
        dict(bad_conn_params, contacts=50),
        domestic=True,
    )
    bdlg = BatchDialog([bad_conn, bad_conn_diff_contacts], 'GB', ambient_temperature_C=25, calculation_enabled=True)
    assert [bdlg.fields['material_max_temperature_C'].itemData(i) for i in range(1, bdlg.fields['material_max_temperature_C'].count())] == [120, 100, 90, 80]
    bdlg.preview()
    assert '配置异常' in bdlg.status.text()
    assert bdlg.table.item(0, 2).text().startswith('配置异常：')
    assert bdlg.table.item(0, 2).foreground().color() == QColor(current_colors()['error'])
    bdlg.show()
    qapp.processEvents()
    monkeypatch.setattr(QMessageBox, 'question', _deny)
    bdlg.apply()
    assert bdlg.result() != QDialog.DialogCode.Accepted
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
    bdlg.apply()
    assert bdlg.result() == QDialog.DialogCode.Accepted
    bdlg.close()

    win.undo_stack.clear()
    win.dirty = False
    win.close()


def test_expected_project_ui_j1_config_error_fix_and_mission_sync(qapp, isolated_tmp, monkeypatch):
    import hashlib
    import shutil
    from pathlib import Path

    from src.storage import load_project

    src_path = Path('/home/wang/Downloads/预计工程.json')
    if not src_path.is_file():
        pytest.skip('External sample project file not present')
    digest_before = hashlib.sha256(src_path.read_bytes()).hexdigest()

    tmp_json = isolated_tmp / '预计工程_copy.json'
    shutil.copy2(src_path, tmp_json)

    monkeypatch.setattr(
        QMessageBox,
        'warning',
        lambda *a, **kw: pytest.fail(f'Unexpected warning popup: {a[1:3]}'),
    )

    win = MainWindow()
    assert win.open_path(tmp_json, prompt_autosave=False)
    assert win.dirty is False

    # On open, J1 (Ⅱ类, 125 ℃) and 工况 1 visibly report 配置异常, not 待配置.
    env_table = win.environment_editor.table
    assert env_table.item(0, ENV_COLUMNS['status']).text() == '配置异常 1 项'
    assert env_table.item(0, ENV_COLUMNS['total']).text() == '配置异常'
    mission = win.analysis_page.panels['mission']
    assert mission.table.item(0, 3).text() == ''
    assert mission.table.item(1, 3).text() == ''
    assert '配置异常' in mission.output.toPlainText()

    # Fixing J1 to 120 ℃ resolves 工况 1 and MissionTab working rate immediately.
    j1_row = next(i for i, c in enumerate(win.project.components) if c.name == 'J1')
    win.quick_edit_parameter(j1_row, 'material_max_temperature_C', 120.0)
    qapp.processEvents()
    assert env_table.item(0, ENV_COLUMNS['status']).text() == '已计算'
    assert float(mission.table.item(0, 3).text()) == pytest.approx(0.08649432, rel=1e-9)

    # Save and reopen cleanly with dirty == False and synced analysis preserved.
    assert win.save()
    assert win.dirty is False
    saved_analysis = win.project.analysis
    win.close()

    reloaded, _ = load_project(tmp_json)
    assert reloaded.analysis == saved_analysis
    win2 = MainWindow()
    assert win2.open_path(tmp_json, prompt_autosave=False)
    assert win2.dirty is False
    assert win2.project.analysis == saved_analysis
    win2.close()
    assert hashlib.sha256(src_path.read_bytes()).hexdigest() == digest_before


def test_p0_1_collapsed_mixed_rate_group_sort_and_excepthook(qapp, monkeypatch):
    import io
    import sys

    from src.app import install_excepthook
    from src.domain import OperatingCondition, Project
    from src.view_dialog import ViewDialog

    r_high = Component(
        'R1',
        '国产片式膜电阻器',
        1,
        {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1M'},
        domestic=True,
    )
    r_low = Component(
        'R2',
        '国产片式膜电阻器',
        1,
        {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'},
        domestic=True,
    )
    c_mid = Component(
        'C1',
        '国产1类瓷介电容器',
        1,
        {'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
        domestic=True,
    )
    u_pending_1 = Component('U1', '待配置', 1, {}, domestic=True)
    u_pending_2 = Component('U2', '待配置', 1, {}, domestic=True)
    proj = Project(
        'P0-1混合单件失效率折叠排序',
        components=[c_mid, u_pending_1, u_pending_2, r_low, r_high],
        conditions=[OperatingCondition('地面良好 25℃', 'GB', 25, enabled=True)],
        group_fields=['model'],
    )
    win = MainWindow(proj)
    rate_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'rate')
    model_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'model')

    # Collapsed resistor group has MIXED ('多种值') single-item rate.
    res_proxy_row = next(
        r for r in range(win.proxy.rowCount()) if '电阻' in str(win.proxy.index(r, model_col).data())
    )
    assert win.proxy.index(res_proxy_row, rate_col).data(Qt.ItemDataRole.UserRole) == '多种值'

    for mode in ('rate_desc', 'weighted_desc', 'quantity_desc', 'model', 'bom'):
        win.sort_box.setCurrentIndex(win.sort_box.findData(mode))
        assert win.proxy.rowCount() == 3

    # Under rate_desc, collapsed mixed-rate group sorts by max member rate and pending group is last.
    win.sort_box.setCurrentIndex(win.sort_box.findData('rate_desc'))
    rate_r_high = proj.calculate(proj.conditions[0]).evaluate(r_high).rate
    rate_c_mid = proj.calculate(proj.conditions[0]).evaluate(c_mid).rate
    top_rows = win.rows_for_index(win.proxy.index(0, 0))
    last_rows = win.rows_for_index(win.proxy.index(win.proxy.rowCount() - 1, 0))
    if rate_r_high > rate_c_mid:
        assert set(top_rows) == {3, 4}
    assert set(last_rows) == {1, 2}

    # ViewDialog can also configure group_fields=['model'] cleanly.
    vdlg = ViewDialog(win.project, win)
    for key, check in vdlg.group_checks.items():
        check.setChecked(key == 'model')
    vdlg.apply()
    assert vdlg.result() == QDialog.DialogCode.Accepted
    assert vdlg.group_fields == ['model']
    vdlg.close()
    win.dirty = False
    win.close()

    # install_excepthook logs traceback and invokes non-fatal QMessageBox.critical with guard.
    buf = io.StringIO()
    crit_calls = []
    monkeypatch.setattr(sys, '__stderr__', buf)
    monkeypatch.setattr(sys, 'excepthook', sys.__excepthook__)

    def _fake_critical(_parent, title, text):
        crit_calls.append((title, text))
        # Simulate nested call to verify re-entrancy guard prevents infinite recursion.
        if len(crit_calls) == 1:
            sys.excepthook(RuntimeError, RuntimeError('nested'), None)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, 'critical', _fake_critical)
    hook = install_excepthook()
    hook(ValueError, ValueError('多種值 boom'), None)
    assert 'ValueError: 多種值 boom' in buf.getvalue()
    assert len(crit_calls) == 1


def test_p1_1_p1_2_p3_17_autosave_provenance_cli_cancel_and_corrupt_sidecar(
    qapp, sample_project, isolated_tmp, monkeypatch
):
    import sys

    from PyQt6.QtWidgets import QApplication

    import src.app as app_mod
    import src.window as window_mod
    from src.storage import load_project, save_project
    from src.ui.undo import find_autosave

    proj_path = isolated_tmp / 'rev_conflict.json'
    win1 = MainWindow(sample_project)
    assert win1.save_to(proj_path)
    win1.quick_edit_parameter(0, 'quality', 'A1M')
    assert find_autosave(proj_path) is not None
    # Simulate an abrupt crash leaving the autosave sidecar on disk.
    win1.path = None
    win1.dirty = False
    win1.close()

    # Simulate colleague externally saving rev 2 and rev 3 while stale autosave is from rev 1.
    ext_proj, r1 = load_project(proj_path)
    ext_proj.notes = 'colleague_rev2'
    r2 = save_project(proj_path, ext_proj, r1)
    ext_proj.notes = 'colleague_newer_save'
    r3 = save_project(proj_path, ext_proj, r2)
    assert int(r3) == 3

    warnings_seen = []
    monkeypatch.setattr(
        QMessageBox, 'warning', lambda _p, title, text: warnings_seen.append((title, text))
    )
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    win2 = MainWindow()
    assert win2.open_path(proj_path, prompt_autosave=True)
    assert warnings_seen
    # Recovered stale draft must detach from target path so Ctrl+S opens Save As instead of clobbering rev 3!
    assert win2.path is None
    assert win2.revision is None
    assert win2.dirty is True
    assert win2.project.components[0].parameters['quality'] == 'A1M'
    disk_proj, disk_rev = load_project(proj_path)
    assert int(disk_rev) == 3
    assert disk_proj.notes == 'colleague_newer_save'
    win2.dirty = False
    win2.close()

    # Live-PID guard prevents another window clicking No from deleting an active owner's autosave.
    monkeypatch.setattr(window_mod, 'is_autosave_owned_by_other_live_pid', lambda _p: True)
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No
    )
    win_no = MainWindow()
    assert win_no.open_path(proj_path, prompt_autosave=True)
    assert find_autosave(proj_path) is not None
    win_no.close()
    monkeypatch.setattr(window_mod, 'is_autosave_owned_by_other_live_pid', lambda _p: False)

    # [P1-2] CLI Cancel on autosave prompt stays cancelled and preserves sidecar without reopening file.
    created_windows = []
    orig_mw = app_mod.MainWindow

    def _track_mw(*a, **kw):
        w = orig_mw(*a, **kw)
        created_windows.append(w)
        return w

    monkeypatch.setattr(app_mod, 'MainWindow', _track_mw)
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Cancel
    )
    monkeypatch.setattr(QApplication, 'exec', staticmethod(lambda: 0))
    monkeypatch.setattr(sys, 'argv', ['gjbz299d-calc', str(proj_path)])
    assert app_mod.main() == 0
    assert created_windows[-1].path is None
    assert find_autosave(proj_path) is not None
    created_windows[-1].close()

    # [P1-2] CLI corrupt JSON shows only ONE error dialog (no duplicate fallback load).
    corrupt_json = isolated_tmp / 'corrupt_cli.json'
    corrupt_json.write_text('{bad json', encoding='utf-8')
    warnings_seen.clear()
    monkeypatch.setattr(sys, 'argv', ['gjbz299d-calc', str(corrupt_json)])
    assert app_mod.main() == 0
    assert len(warnings_seen) == 1
    created_windows[-1].close()

    # [P3-17] Corrupt .autosave.json warns once and falls back to opening valid target project!
    sidecar = find_autosave(proj_path)
    assert sidecar is not None
    sidecar.write_text('{corrupt sidecar', encoding='utf-8')
    warnings_seen.clear()
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes
    )
    win3 = MainWindow()
    assert win3.open_path(proj_path, prompt_autosave=True)
    assert len(warnings_seen) == 1
    assert win3.path == proj_path
    assert win3.dirty is False
    assert win3.project.notes == 'colleague_newer_save'
    win3.close()


def test_p1_11_p2_32_p3_16_discard_preserves_autosave_until_switch_and_untitled_scratch(
    qapp, sample_project, isolated_tmp, monkeypatch
):
    from PyQt6.QtWidgets import QFileDialog

    import src.window as window_mod
    from src.ui.undo import find_autosave

    keep_path = isolated_tmp / 'keep_autosave.json'
    win = MainWindow(sample_project)
    assert win.save_to(keep_path)
    win.quick_edit_parameter(0, 'quality', 'A1M')
    assert win.dirty is True
    assert find_autosave(keep_path) is not None

    # [P1-11] open() -> Discard -> Cancel file dialog MUST keep .autosave.json intact!
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Discard
    )
    monkeypatch.setattr(QFileDialog, 'getOpenFileName', staticmethod(lambda *a, **kw: ('', '')))
    win.open()
    assert win.dirty is True
    assert find_autosave(keep_path) is not None

    # [P1-11] Dropping a corrupt JSON -> Discard -> failed load MUST also keep .autosave.json intact!
    bad_json = isolated_tmp / 'bad_drop.json'
    bad_json.write_text('not-json', encoding='utf-8')
    monkeypatch.setattr(win, 'error', lambda _e: None)
    assert win.open_dropped_files([bad_json]) is False
    assert win.dirty is True
    assert find_autosave(keep_path) is not None

    # [P2-32] import_bom in 'replace' mode checks confirm_discard BEFORE opening BOM mapping dialogs.
    csv_path = isolated_tmp / 'mini.csv'
    csv_path.write_text('Designator,Value,Quantity\nR9,10k,1\n', encoding='utf-8')
    monkeypatch.setattr(win, 'import_target', lambda: 'replace')
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Cancel
    )
    monkeypatch.setattr(
        window_mod,
        'BomImportDialog',
        lambda *a, **kw: pytest.fail('BomImportDialog should not open when confirm_discard cancelled'),
    )
    assert win.import_bom(path=csv_path) is False
    assert find_autosave(keep_path) is not None

    # Switching via new_project() with Discard now cleans up old path's autosave.
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Discard
    )
    assert win.new_project()
    assert find_autosave(keep_path) is None

    # [P3-16] Untitled dirty project writes scratch autosave in QSettings dir and recovers cleanly.
    assert win.untitled_autosave_path() is None
    win.environment_editor.add_condition()
    assert win.dirty is True
    assert win.untitled_autosave_path() is not None

    win_recover = MainWindow()
    assert win_recover.recover_untitled_autosave(prompt=False)
    assert len(win_recover.project.conditions) == 2
    assert win_recover.dirty is True

    # [P3-16] OSError during write_autosave marks _last_autosave_ok=False and shows accurate status.
    def _boom_autosave(*_a, **_kw):
        raise OSError('disk full')

    with monkeypatch.context() as mp:
        mp.setattr(window_mod, 'write_autosave', _boom_autosave)
        win_recover.project.components.append(
            Component(
                'R_ERR',
                '国产片式膜电阻器',
                1,
                {'resistance_ohm': 100.0, 'film': '薄膜', 'quality': 'A1P'},
            )
        )
        win_recover.finish_configuration([win_recover.project.components[0].identifier])
        assert win_recover._last_autosave_ok is False
        assert '失败' in win_recover.statusBar().currentMessage()

    # Closing with Discard clears untitled scratch autosave.
    win_recover.close()
    assert win.untitled_autosave_path() is None
    win.dirty = False
    win.close()


def test_p1_9_bulk_selection_refresh_and_filter_scaling(qapp):
    import time

    from src.domain import OperatingCondition, Project

    comps = [
        Component(
            name=f'R{i}',
            model='国产片式膜电阻器' if i % 3 else '待配置',
            quantity=1,
            parameters={'resistance_ohm': 1000.0 + i, 'film': '薄膜', 'quality': 'A1P'} if i % 3 else {},
            domestic=True,
            bom={
                'file_name': 'bulk.csv',
                'sha256': '0' * 64,
                'row': {'Value': f'{i}k', 'Footprint': '0603', 'Manufacturer Part Number': f'MPN-{i}'},
                'assigned_designators': [f'R{i}'],
            },
        )
        for i in range(1, 301)
    ]
    proj = Project(
        '大选区性能回归',
        components=comps,
        conditions=[OperatingCondition('GB 25℃', 'GB', 25, enabled=True)],
    )
    win = MainWindow(proj)
    all_ids = [c.identifier for c in comps]

    call_count = 0
    orig_sel_changed = win.selection_changed

    def _counted_sel_changed(*a, **kw):
        nonlocal call_count
        call_count += 1
        return orig_sel_changed(*a, **kw)

    win.selection_changed = _counted_sel_changed

    t0 = time.perf_counter()
    win.refresh(all_ids, recalculate=False)
    refresh_elapsed = time.perf_counter() - t0
    assert refresh_elapsed < 1.0
    assert call_count == 1
    assert len(win.selected_rows()) == 300

    call_count = 0
    t1 = time.perf_counter()
    win.filter_field.setText('R1')
    win.status_filter_changed('calculated')
    win.status_filter_changed('all')
    win.filter_field.clear()
    filter_elapsed = time.perf_counter() - t1
    assert filter_elapsed < 0.8
    assert call_count == 4

    win.dirty = False
    win.close()


def test_p1_10_invalid_temperature_draft_no_duplicate_undo_and_close_discard(
    qapp, sample_project, monkeypatch
):
    from PyQt6.QtGui import QCloseEvent

    win = MainWindow(sample_project)
    win.undo_stack.clear()
    win._base_dirty = False
    win.dirty = False

    errors = []
    monkeypatch.setattr(win, 'error', lambda err: errors.append(str(err)))

    cid = win.project.conditions[0].identifier
    _env_box, temp_box = win.environment_editor.boxes[cid]
    temp_box.setEditText('abc')
    win.environment_editor.temperature_input_changed(win.project.conditions[0], temp_box, 'abc')

    # Repeatedly committing the same invalid draft must NOT push duplicate undo entries!
    for _ in range(5):
        assert win.commit_pending_edits() is False
    assert len(errors) == 5
    assert win.undo_stack.count() == 1

    # Cancel keeps window open and preserves draft.
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Cancel
    )
    ev_cancel = QCloseEvent()
    win.closeEvent(ev_cancel)
    assert not ev_cancel.isAccepted()
    assert temp_box.currentText() == 'abc'

    # Save runs strict validation and blocks closing while temperature is invalid.
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Save
    )
    ev_save = QCloseEvent()
    win.closeEvent(ev_save)
    assert not ev_save.isAccepted()

    # Discard drops invalid draft and allows window to close cleanly!
    monkeypatch.setattr(
        QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Discard
    )
    ev_discard = QCloseEvent()
    win.closeEvent(ev_discard)
    assert ev_discard.isAccepted()


def test_p1_12_redacted_project_category_pareto_bar_matches_tree_filter(qapp):
    from src.domain import OperatingCondition, Project
    from src.redacted_export import build_redacted_project

    cond = OperatingCondition('地面良好 25℃', 'GB', 25, enabled=True)
    proj = Project(
        '脱敏分类一致性测试',
        components=[
            Component(
                'R1',
                '国产片式膜电阻器',
                4,
                {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'},
                domestic=True,
            ),
            Component(
                'C1',
                '国产1类瓷介电容器',
                2,
                {'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
                domestic=True,
            ),
            Component(
                'MOD1',
                '已知单件失效率',
                1,
                {},
                domestic=True,
                condition_rates=[
                    {
                        'environment': 'GB',
                        'ambient_temperature_C': 25,
                        'rate': 0.05,
                    }
                ],
            ),
        ],
        conditions=[cond],
    )
    redacted = build_redacted_project(proj)
    win = MainWindow(redacted)

    codes = [entry[0] for entry in win.category_bar.entries()]
    assert set(codes) == {'R', 'C', 'OTHER'}
    for code in codes:
        win.category_filter_changed(code)
        assert win.proxy.rowCount() == 1
    win.category_filter_changed(None)
    assert win.proxy.rowCount() == 3
    win.dirty = False
    win.close()


def test_p2_29_p2_30_p2_31_p3_11_to_p3_15_polish_regressions(qapp, sample_project, monkeypatch):
    from src.view_dialog import ViewDialog

    sample_project.components = [sample_project.components[0], sample_project.components[1]]
    win = MainWindow(sample_project)
    win.undo_stack.clear()
    win._base_dirty = False
    win.dirty = False

    # [P2-29] MissionTab preserves selected condition in sources combo across MainWindow.refresh().
    mission = win.analysis_page.panels['mission']
    assert mission.sources.count() == 2
    mission.sources.setCurrentIndex(1)
    selected_before = mission.sources.currentData()
    win.refresh(recalculate=False)
    assert mission.sources.currentIndex() == 1
    assert mission.sources.currentData()['id'] == selected_before['id']

    # [P2-30] Edits on distinct analysis sub-tabs use distinct merge_ids instead of collapsing into 1.
    win.analysis_page.panels['bayes'].initial.setText('1.25')
    win.analysis_page.panels['failure_modes'].rate.setText('2.50')
    assert win.undo_stack.count() == 2
    win.undo_stack.clear()
    win.dirty = False

    # [P2-31] EnvironmentEditor updates condition widgets in place without destroying focused widgets.
    cid = win.project.conditions[0].identifier
    chk_before = win.environment_editor.checkboxes[cid]
    env_before, temp_before = win.environment_editor.boxes[cid]
    chk_before.setChecked(False)
    chk_before.setChecked(True)
    env_before.setCurrentIndex(env_before.findData('GF1'))
    assert win.environment_editor.checkboxes[cid] is chk_before
    assert win.environment_editor.boxes[cid] == (env_before, temp_before)
    win.undo_stack.clear()
    win.dirty = False

    # [P3-11] Accepting ViewDialog with no changes does NOT dirty project or push undo entry.
    def _noop_exec(self_dlg):
        self_dlg.apply()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ViewDialog, 'exec', _noop_exec)
    win.configure_view()
    assert win.dirty is False
    assert win.undo_stack.count() == 0

    # [P3-12] AnalysisCellDelegate.destroyEditor emits draft_changed only once.
    delegate = mission.table.itemDelegate()
    emitted = []
    delegate.draft_changed.connect(lambda: emitted.append(1))
    editor = QComboBox(mission.table)
    delegate.destroyEditor(editor, mission.table.model().index(0, 0))
    assert len(emitted) == 1

    # [P3-13] Theme toggle recolors EnvironmentEditor without recalculating conditions.
    with monkeypatch.context() as mp:
        mp.setattr(
            win.project,
            'calculate',
            lambda *a, **kw: pytest.fail('theme toggle should not recalculate'),
        )
        win.toggle_theme()
        win.toggle_theme()

    # [P3-14] ReferenceTableModel.set_highlight emits dataChanged instead of bare layoutChanged.
    ref_model = win.standard_page.table_model
    frag = fragments('217')[0]
    ref_model.set_fragment(frag)
    data_signals, layout_signals = [], []
    ref_model.dataChanged.connect(lambda tl, br, roles: data_signals.append((tl.row(), br.row(), roles)))
    ref_model.layoutChanged.connect(lambda: layout_signals.append(1))
    ref_model.set_highlight(frag['cells'][0]['id'])
    assert len(data_signals) == 1
    assert layout_signals == []
    ref_model.set_highlight(None)
    assert len(data_signals) == 2
    assert layout_signals == []

    win.dirty = False
    win.close()
