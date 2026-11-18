"""Comprehensive headless tests for MainWindow, ViewDialog, app.py, __main__.py, and UI regressions."""

import copy
import inspect
import os
import runpy
import sys

import pytest
from PyQt6.QtCore import QEvent, QMimeData, QPoint, QPointF, Qt, QUrl
from PyQt6.QtGui import QCloseEvent, QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent
from PyQt6.QtWidgets import QDialog, QFileDialog, QMenu, QMessageBox

import src.app as app_mod
from src.bom import BomSource
from src.bom_dialog import BomImportDialog
from src.component_fields import COLUMNS
from src.component_tree import MIXED
from src.dialogs import ComponentDialog
from src.domain import Component, OperatingCondition, Project
from src.redacted_export import build_redacted_project
from src.shared_io import export_redacted_file
from src.standards import MODELS
from src.storage import load_project, save_project
from src.theme import current_theme
from src.ui.bom_wizard import BomPreMapDialog
from src.ui.undo import autosave_path, clear_autosave, find_autosave, write_autosave
from src.view_dialog import FIXED_FIELDS, ViewDialog
from src.window import MainWindow, _extract_dropped_paths

# ---------------------------------------------------------------------------
# 1. ViewDialog, [P0-1] Collapsed Mixed-Rate Group Sorting & [P3-11] No-op Guard
# ---------------------------------------------------------------------------


def test_view_dialog_validation_and_noop_configure_view(qapp, sample_project, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a[2] if len(a) > 2 else str(a)))

    dlg = ViewDialog(sample_project)
    try:
        assert not (FIXED_FIELDS & set(dlg.visible_checks))
        assert 'origin' in dlg.group_checks
        assert dlg.group_checks['origin'].toolTip()

        # Unchecking all group fields blocks apply() with warning
        for chk in dlg.group_checks.values():
            chk.setChecked(False)
        dlg.apply()
        assert warnings and '至少勾选一个分组字段' in warnings[-1]
        assert dlg.result() != QDialog.DialogCode.Accepted

        # Check only 'model' for grouping and toggle optional columns
        dlg.group_checks['model'].setChecked(True)
        dlg.visible_checks['Manufacturer'].setChecked(True)
        dlg.visible_checks['Footprint'].setChecked(False)
        dlg.apply()
        assert dlg.result() == QDialog.DialogCode.Accepted
        assert dlg.group_fields == ['model']
        assert 'Manufacturer' in dlg.visible_fields
        assert 'Footprint' not in dlg.visible_fields
        assert FIXED_FIELDS <= set(dlg.visible_fields)
    finally:
        dlg.close()

    # [P3-11]: Clicking OK in ViewDialog without changing anything must NOT dirty project or push undo!
    win = MainWindow(copy.deepcopy(sample_project))
    try:
        win.dirty = False
        win._base_dirty = False
        win.undo_stack.clear()
        monkeypatch.setattr(ViewDialog, 'exec', lambda self_d: self_d.apply() or QDialog.DialogCode.Accepted)
        win.configure_view()
        assert win.dirty is False
        assert win.undo_stack.count() == 0
    finally:
        win.close()


def test_p0_1_collapsed_mixed_rate_group_sorting_and_header_clicks(qapp, monkeypatch):
    proj = Project('P0排序回归工程')
    proj.conditions = [OperatingCondition(name='GB25', environment='GB', ambient_temperature_C=25, enabled=True)]
    proj.components = [
        # Same model '国产片式膜电阻器' with different quality grades (different unit rates) + 1 pending item!
        Component('R1', '国产片式膜电阻器', 1, {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'}),
        Component('R2', '国产片式膜电阻器', 3, {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'B2'}),
        Component('R3', '国产片式膜电阻器', 1, {}),  # Pending inside same model group
        Component('C1', '国产1类瓷介电容器', 2, {'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4}),
        Component('U1', '待配置', 1, {}),
    ]
    proj.grouped = True

    win = MainWindow(proj)
    try:
        # Apply custom group_fields = ['model'] via configure_view()
        def _set_model_group(self_d):
            for k, chk in self_d.group_checks.items():
                chk.setChecked(k == 'model')
            self_d.apply()
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(ViewDialog, 'exec', _set_model_group)
        win.configure_view()
        assert win.project.group_fields == ['model']
        win.collapse_all_groups()

        # Verify collapsed resistor group carries MIXED ('多种值') in rate display
        rate_col = next(i for i, c in enumerate(COLUMNS) if c.key == 'rate')
        rates_display = [win.proxy.index(r, rate_col).data() for r in range(win.proxy.rowCount())]
        assert any(MIXED in str(txt) for txt in rates_display)

        # Cycle through ALL sort modes and header clicks — MUST NOT raise ValueError or qFatal!
        for mode in ('rate_desc', 'weighted_desc', 'quantity_desc', 'model', 'bom'):
            idx = win.sort_box.findData(mode)
            assert idx >= 0
            win.sort_box.setCurrentIndex(idx)

        # In rate_desc: pending '待配置' must rank last, and '国产片式膜电阻器' group (with B2 max rate) ranks first!
        win.sort_box.setCurrentIndex(win.sort_box.findData('rate_desc'))
        first_rows = win.rows_for_index(win.proxy.index(0, 0))
        last_rows = win.rows_for_index(win.proxy.index(win.proxy.rowCount() - 1, 0))
        assert win.project.components[first_rows[0]].model == '国产片式膜电阻器'
        assert win.project.components[last_rows[0]].model == '待配置'

        # Click every sortable column header
        for col_idx, spec in enumerate(COLUMNS):
            if spec.key in ('index', 'model', 'weighted', 'share', 'rate', 'quantity'):
                win._header_clicked(col_idx)

        # Toggle group expand/collapse by clicking index column
        win.sort_box.setCurrentIndex(win.sort_box.findData('bom'))
        before_rows = win.proxy.rowCount()
        win._table_clicked(win.proxy.index(0, 0))
        assert win.proxy.rowCount() > before_rows
        win.expand_all_groups()
        win.collapse_all_groups()
        assert win.proxy.rowCount() == before_rows
    finally:
        win.dirty = False
        win.close()


# ---------------------------------------------------------------------------
# 2. src/__main__.py & src/app.py (CLI, install_excepthook, [P1-2])
# ---------------------------------------------------------------------------


def test_dunder_main_and_app_excepthook_and_cli_flows(qapp, sample_project, isolated_tmp, monkeypatch):
    # 1) src/__main__.py 100% coverage
    monkeypatch.setattr(app_mod, 'main', lambda: 0)
    with pytest.raises(SystemExit) as exc_info:
        runpy.run_module('src', run_name='__main__')
    assert exc_info.value.code == 0
    monkeypatch.undo()

    # 2) install_excepthook unit test (if present in src.app)
    if hasattr(app_mod, 'install_excepthook'):
        orig_hook = sys.excepthook
        critical_calls = []
        monkeypatch.setattr(QMessageBox, 'critical', lambda *a: critical_calls.append(a))
        try:
            app_mod.install_excepthook()
            assert sys.excepthook is not orig_hook
            err = RuntimeError('simulated slot crash')
            sys.excepthook(RuntimeError, err, err.__traceback__)
            assert len(critical_calls) == 1
            # KeyboardInterrupt must not trigger error popup
            sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
            assert len(critical_calls) == 1
        finally:
            sys.excepthook = orig_hook

    # 3) CLI --help exits cleanly with 0
    monkeypatch.setattr(sys, 'argv', ['gjb299d', '--help'])
    assert app_mod.main() == 0

    # 4) Windowless stdout=None / stderr=None + valid CLI project open
    proj_path = isolated_tmp / 'cli_open.json'
    save_project(proj_path, sample_project)
    captured_windows = []
    orig_init = MainWindow.__init__

    def _track_init(self_w, *a, **kw):
        orig_init(self_w, *a, **kw)
        captured_windows.append(self_w)

    monkeypatch.setattr(MainWindow, '__init__', _track_init)
    monkeypatch.setattr(MainWindow, 'show', lambda _self: None)
    monkeypatch.setattr(qapp, 'exec', lambda: 0)
    monkeypatch.setattr(sys, 'stdout', None)
    monkeypatch.setattr(sys, 'stderr', None)
    monkeypatch.setattr(sys, 'argv', ['gjb299d', str(proj_path)])
    try:
        rc = app_mod.main()
        assert rc == 0
        assert captured_windows and captured_windows[-1].path == proj_path
    finally:
        for w in captured_windows:
            w.dirty = False
            w.close()
        captured_windows.clear()
    monkeypatch.setattr(sys, 'stdout', sys.__stdout__)
    monkeypatch.setattr(sys, 'stderr', sys.__stderr__)

    # 5) [P1-2]: Clicking Cancel on autosave prompt during CLI launch MUST NOT forcefully load disk project!
    draft = copy.deepcopy(sample_project)
    draft.name = '未保存草稿'
    write_autosave(proj_path, draft)
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Cancel)
    monkeypatch.setattr(sys, 'argv', ['gjb299d', str(proj_path)])
    try:
        assert app_mod.main() == 0
        win_cancel = captured_windows[-1]
        assert win_cancel.path is None
        assert find_autosave(proj_path) is not None
    finally:
        for w in captured_windows:
            w.dirty = False
            w.close()
        captured_windows.clear()
        clear_autosave(proj_path)

    # 6) Corrupt CLI .json shows warning ONLY ONCE (not twice!)
    corrupt_path = isolated_tmp / 'broken.json'
    corrupt_path.write_text('{not valid json', encoding='utf-8')
    warn_calls = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warn_calls.append(a))
    monkeypatch.setattr(sys, 'argv', ['gjb299d', str(corrupt_path)])
    try:
        assert app_mod.main() == 0
        assert len(warn_calls) == 1
    finally:
        for w in captured_windows:
            w.dirty = False
            w.close()


# ---------------------------------------------------------------------------
# 3. Autosave Recovery, Provenance, Corrupt Sidecar & Premature Delete ([P1-1], [P1-11], [P2-32], [P3-17])
# ---------------------------------------------------------------------------


def _write_autosave_compat(path, project, base_revision=None):
    sig = inspect.signature(write_autosave)
    if 'base_revision' in sig.parameters:
        return write_autosave(path, project, base_revision=base_revision)
    return write_autosave(path, project)


def test_autosave_provenance_external_conflict_corrupt_fallback_and_open_cancel(qapp, sample_project, isolated_tmp, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a[2] if len(a) > 2 else str(a)))

    # A) Matching revision recovery -> binds to path and saves cleanly as rev 2
    p_match = isolated_tmp / 'match.json'
    r1 = save_project(p_match, sample_project)
    draft_ok = copy.deepcopy(sample_project)
    draft_ok.name = '草稿正常恢复'
    _write_autosave_compat(p_match, draft_ok, base_revision=r1)

    win = MainWindow()
    try:
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
        assert win.open_path(p_match, prompt_autosave=True)
        assert win.project.name == '草稿正常恢复'
        assert win.path == p_match and win.dirty is True
        assert win.save()
        assert win.dirty is False and int(win.revision) == 2
        assert find_autosave(p_match) is None

        # B) [P1-1]: External file advanced to rev 2 after draft written at rev 1 -> converts recovered draft to untitled!
        p_ext = isolated_tmp / 'external.json'
        rev_base = save_project(p_ext, sample_project)
        stale_draft = copy.deepcopy(sample_project)
        stale_draft.name = '陈旧本地草稿'
        _write_autosave_compat(p_ext, stale_draft, base_revision=rev_base)

        newer_ext = copy.deepcopy(sample_project)
        newer_ext.name = '同事已提交的新版'
        save_project(p_ext, newer_ext, expected_revision=rev_base)

        warnings.clear()
        assert win.open_path(p_ext, prompt_autosave=True)
        assert win.project.name == '陈旧本地草稿'
        assert win.dirty is True
        # Must NOT bind to p_ext's rev 2 (otherwise Ctrl+S would silently overwrite colleague's save!)
        assert win.path is None and win.revision is None
        assert warnings
        disk_proj, disk_rev = load_project(p_ext)
        assert disk_proj.name == '同事已提交的新版' and int(disk_rev) == 2
        clear_autosave(p_ext)

        # C) [P3-17]: Corrupt .autosave.json when clicking Yes warns and falls back to opening valid target project!
        p_corrupt_side = isolated_tmp / 'corrupt_sidecar.json'
        save_project(p_corrupt_side, sample_project)
        autosave_path(p_corrupt_side).write_text('{broken autosave json', encoding='utf-8')
        warnings.clear()
        assert win.open_path(p_corrupt_side, prompt_autosave=True)
        assert win.path == p_corrupt_side
        assert win.project.name == sample_project.name
        assert win.dirty is False
        assert warnings

        # D) [P1-11]: Calling open(), choosing Discard, then cancelling QFileDialog MUST keep existing .autosave.json!
        win.quick_edit_parameter(0, 'quality', 'A1M')
        assert win.dirty is True
        assert find_autosave(p_corrupt_side) is not None
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Discard)
        monkeypatch.setattr(QFileDialog, 'getOpenFileName', lambda *a, **kw: ('', ''))
        win.open()
        assert win.dirty is True
        assert find_autosave(p_corrupt_side) is not None

        # E) [P1-1] Live-PID protection on clicking 'No': another live PID's autosave is NOT deleted!
        p_live_pid = isolated_tmp / 'live_pid.json'
        r_live = save_project(p_live_pid, sample_project)
        _write_autosave_compat(p_live_pid, sample_project, base_revision=r_live)
        meta_sidecar = p_live_pid.parent / f'.{p_live_pid.name}.autosave.meta.json'
        if meta_sidecar.is_file():
            import json
            meta_obj = json.loads(meta_sidecar.read_text(encoding='utf-8'))
            meta_obj['pid'] = os.getppid() if os.getppid() != os.getpid() else 1
            meta_sidecar.write_text(json.dumps(meta_obj), encoding='utf-8')
            win.dirty = False
            win._base_dirty = False
            monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.No)
            assert win.open_path(p_live_pid, prompt_autosave=True)
            assert find_autosave(p_live_pid) is not None
            clear_autosave(p_live_pid)
    finally:
        clear_autosave(isolated_tmp / 'corrupt_sidecar.json')
        win.dirty = False
        win.close()


def test_p2_32_bom_replace_mode_confirms_discard_before_premap_wizard(qapp, sample_project, isolated_tmp, monkeypatch):
    csv_path = isolated_tmp / 'tiny.csv'
    csv_path.write_text('Designator,Value,Footprint,Quantity\nR99,10k,0603,1\n', encoding='utf-8')

    win = MainWindow(copy.deepcopy(sample_project))
    try:
        assert win.dirty is True
        src = BomSource('tiny.csv', 'e' * 64, [['Designator', 'Value', 'Footprint', 'Quantity'], ['R99', '10k', '0603', '1']], header_row=0)
        first, last, cols = src.default_region()
        built = src.build_import(first, last, cols, src.default_mapping(cols))

        monkeypatch.setattr(BomImportDialog, 'exec', lambda self_d: setattr(self_d, 'result_import', built) or QDialog.DialogCode.Accepted)
        monkeypatch.setattr(win, 'import_target', lambda: 'replace')
        # User clicks Cancel on confirm_discard()!
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Cancel)

        wizard_calls = []
        monkeypatch.setattr(BomPreMapDialog, 'exec', lambda _self: wizard_calls.append(1) or QDialog.DialogCode.Accepted)
        assert win.import_bom(path=csv_path) is False
        assert wizard_calls == []
    finally:
        win.dirty = False
        win.close()


# ---------------------------------------------------------------------------
# 4. Invalid Temperature Draft closeEvent & Duplicate Undo Guard ([P1-10])
# ---------------------------------------------------------------------------


def test_p1_10_invalid_temperature_draft_no_duplicate_undo_and_close_discard(qapp, sample_project, isolated_tmp, monkeypatch):
    proj_path = isolated_tmp / 'temp_draft.json'
    win = MainWindow(copy.deepcopy(sample_project))
    try:
        assert win.save_to(proj_path)
        assert win.undo_stack.count() == 0

        cond0 = win.project.conditions[0]
        _, temp_box = win.environment_editor.boxes[cond0.identifier]
        temp_box.setEditText('abc_invalid')
        win.environment_editor.temperature_input_changed(cond0, temp_box, 'abc_invalid')

        monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: None)
        for _ in range(4):
            assert win.commit_pending_edits() is False

        # [P1-10]: Repeated failed commits on the same invalid text MUST NOT push 4 duplicate undo records!
        assert win.undo_stack.count() <= 1

        # [P1-10]: Closing window and choosing Discard MUST accept the closeEvent instead of trapping the user!
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Discard)
        close_ev = QCloseEvent()
        win.closeEvent(close_ev)
        assert close_ev.isAccepted()
    finally:
        clear_autosave(proj_path)
        win.dirty = False
        win.close()


# ---------------------------------------------------------------------------
# 5. Redacted Project CategoryParetoBar Consistency ([P1-12])
# ---------------------------------------------------------------------------


def test_p1_12_redacted_project_category_pareto_bar_matches_tree_filter(qapp):
    proj = Project('脱敏帕累托测试')
    proj.conditions = [OperatingCondition(name='GB25', environment='GB', ambient_temperature_C=25, enabled=True)]
    proj.components = [
        Component('U1', '国产普通数字集成电路', 1, dict(MODELS['国产普通数字集成电路'].example)),
        Component('R1', '国产片式膜电阻器', 4, {'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'}),
        Component('C1', '国产1类瓷介电容器', 2, {'capacitance_uF': 0.001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4}),
        Component('X1', '已知单件失效率', 1, {}, condition_rates=[{'environment': 'GB', 'ambient_temperature_C': 25, 'rate': 0.01}]),
    ]
    redacted = build_redacted_project(proj)

    win = MainWindow(redacted)
    try:
        win.resize(1400, 820)
        win.show()
        qapp.processEvents()
        win.category_bar.grab()  # Force paintEvent to populate _hit_rects

        codes = {code for code, *_ in win.category_bar.entries()}
        assert codes == {'IC', 'R', 'C', 'OTHER'}

        # Clicking each category must filter >= 1 row in the component tree (never 0 rows!)
        for code in ('IC', 'R', 'C', 'OTHER'):
            win.category_filter_changed(code)
            assert win.proxy.rowCount() >= 1, f'Category {code} filtered to 0 rows on redacted project!'

        # Exercise mousePressEvent hit-testing on CategoryParetoBar (both segment click and '清除筛选 ×')
        win.category_bar.grab()
        clear_rects = [rect for rect, code in win.category_bar._hit_rects if code is None]
        if clear_rects:
            pt = clear_rects[0].center()
            ev = QMouseEvent(
                QEvent.Type.MouseButtonPress,
                QPointF(pt),
                Qt.MouseButton.LeftButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
            win.category_bar.mousePressEvent(ev)
            assert win.proxy.category_filter is None
            assert win.proxy.rowCount() == 4
    finally:
        win.dirty = False
        win.close()


# ---------------------------------------------------------------------------
# 6. Drag-and-Drop, Context Menu, Structure Supplements, Exports, Clear & Pages
# ---------------------------------------------------------------------------


def test_drag_and_drop_context_menu_supplements_exports_and_pages(qapp, sample_project, isolated_tmp, monkeypatch):
    warnings = []
    infos = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a: warnings.append(a[2] if len(a) > 2 else str(a)))
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: infos.append(a[2] if len(a) > 2 else str(a)))

    proj_json = isolated_tmp / 'dropped.json'
    save_project(proj_json, sample_project)
    calc_project = copy.deepcopy(sample_project)
    calc_project.components = [c for c in calc_project.components if c.model != '待配置']
    sup_xlsx = isolated_tmp / 'supplier_drop.xlsx'
    export_redacted_file(sup_xlsx, calc_project)
    bad_txt = isolated_tmp / 'notes.txt'
    bad_txt.write_text('hello', encoding='utf-8')

    # MIME extraction helper
    mime_web = QMimeData()
    mime_web.setUrls([QUrl('https://example.com/file.json')])
    assert _extract_dropped_paths(mime_web) == []

    win = MainWindow()
    try:
        win.show()
        qapp.processEvents()

        # Drag/drop validation branches: 0 files, 2 files, directory, unsupported suffix, valid .json, valid supplier .xlsx
        for urls, should_accept in [
            ([QUrl('https://example.com')], False),
            ([QUrl.fromLocalFile(str(proj_json)), QUrl.fromLocalFile(str(sup_xlsx))], False),
            ([QUrl.fromLocalFile(str(isolated_tmp))], False),
            ([QUrl.fromLocalFile(str(bad_txt))], False),
            ([QUrl.fromLocalFile(str(proj_json))], True),
        ]:
            mime = QMimeData()
            mime.setUrls(urls)
            drag_enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            win.dragEnterEvent(drag_enter)
            assert drag_enter.isAccepted() is should_accept
            drag_move = QDragMoveEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            win.dragMoveEvent(drag_move)
            assert drag_move.isAccepted() is should_accept

        # Drop valid JSON project
        mime_json = QMimeData()
        mime_json.setUrls([QUrl.fromLocalFile(str(proj_json))])
        drop_ev = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime_json, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        win.dropEvent(drop_ev)
        assert drop_ev.isAccepted()
        assert win.path == proj_json

        # Drop supplier .xlsx snapshot
        assert win.open_dropped_files([sup_xlsx])
        assert win.path is None and len(win.project.components) >= 2

        # Reload sample_project to exercise context menu, MPN selection, domestic switch, supplements & exports
        assert win.open_path(proj_json)
        win.select_component_row(0)

        # Context menu construction (without blocking QMenu.exec)
        captured_menus = []
        monkeypatch.setattr(QMenu, 'exec', lambda self_m, *_a: captured_menus.append([act.text() for act in self_m.actions()]))
        rect0 = win.table.visualRect(win.proxy.index(0, 0))
        win._show_table_context_menu(rect0.center())
        assert captured_menus and any('同型号' in t for t in captured_menus[-1])

        # select_same_mpn & duplicate twice (_copy and _copy2)
        win.select_same_mpn('RC0603FR-071KL')
        assert win.selected_rows() == [0]
        win.duplicate_components()
        win.select_component_row(0)
        win.duplicate_components()
        assert any('R1_copy2' in c.bom.get('assigned_designators', []) for c in win.project.components if c.bom)

        # Toggle domestic -> imported on domestic resistor clears incompatible model to '待配置'
        win.change_domestic([0], False)
        assert win.project.components[0].is_domestic is False
        assert win.project.components[0].model == '待配置'
        win.undo_stack.undo()
        assert win.project.components[0].model == '国产片式膜电阻器'

        # Structure supplements (add_board, add_solder, add_smt, show_carrier_scope)
        def _accept_supplement(self_d):
            self_d.result_component = Component(self_d.name.text(), self_d.model(), 1, dict(MODELS[self_d.model()].example))
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(ComponentDialog, 'exec', _accept_supplement)
        before_n = len(win.project.components)
        win.add_board()
        win.add_solder()
        win.add_smt()
        assert len(win.project.components) == before_n + 3
        win.show_carrier_scope()
        assert infos

        # Export XLSX / XLS (testing auto-suffix) and Redacted JSON / XLSX / XLS
        for target_base, filt, expected_suffix in [
            (isolated_tmp / 'rep_out', 'Excel 工作簿 (*.xlsx)', '.xlsx'),
            (isolated_tmp / 'rep_out_xls', 'Excel 97–2003 工作簿 (*.xls)', '.xls'),
        ]:
            monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, tb=target_base, fl=filt, **_kw: (str(tb), fl))
            win.export()
            assert (isolated_tmp / (target_base.name + expected_suffix)).is_file()

        # Remove any pending '待配置' component via remove_component() so undo snapshot stays consistent
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
        pending_ids = [c.identifier for c in win.project.components if c.model == '待配置']
        if pending_ids:
            win.refresh(pending_ids, recalculate=False)
            win.remove_component()
        for target_base, filt, expected_suffix in [
            (isolated_tmp / 'red_out', 'JSON (*.json)', '.json'),
            (isolated_tmp / 'red_out_xlsx', 'Excel (*.xlsx)', '.xlsx'),
            (isolated_tmp / 'red_out_xls', 'Excel (*.xls)', '.xls'),
        ]:
            monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, tb=target_base, fl=filt, **_kw: (str(tb), fl))
            win.export_redacted()
            assert (isolated_tmp / (target_base.name + expected_suffix)).is_file()

        # Clear project + Undo restores full project; calling clear_project again on empty returns False
        expected_before_clear = len(win.project.components)
        monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.StandardButton.Yes)
        assert win.clear_project(confirm=True) is True
        assert len(win.project.components) == 0
        assert win.clear_project(confirm=False) is False
        win.undo_stack.undo()
        assert len(win.project.components) == expected_before_clear

        # Navigate across all 5 top-level pages & test StandardPage search miss + theme toggle
        for page_idx in range(5):
            win.navigate(page_idx)
            assert win.stack.currentIndex() == page_idx
        win.standard_page.search.setText('NO_MATCH_TABLE_99999')
        assert win.standard_page.source.text() == '没有匹配的表格'
        win.standard_page.search.clear()
        assert win.standard_page.open_table('217')

        # [P2-29] & [P2-30]: Mission source combo selection preserved across refresh; analysis tabs use distinct merge_id
        mission = win.analysis_page.panels['mission']
        if mission.sources.count() > 1:
            mission.sources.setCurrentIndex(1)
            chosen_text = mission.sources.currentText()
            win.refresh(recalculate=False)
            assert mission.sources.currentText() == chosen_text

        start_theme = current_theme()
        win.toggle_theme()
        assert current_theme() != start_theme
        win.toggle_theme()
    finally:
        clear_autosave(proj_json)
        win.dirty = False
        win.close()


def test_mainwindow_dialogs_inspector_quick_stress_and_save_as(qapp, sample_project, isolated_tmp, monkeypatch):
    from src.batch_dialog import BatchDialog
    from src.project_import import ModuleDialog
    from src.project_modules import create_project_module

    infos = []
    monkeypatch.setattr(QMessageBox, 'information', lambda *a: infos.append(a[2] if len(a) > 2 else str(a)))

    sub = Project('子模块')
    sub.conditions = [OperatingCondition('GB25', 'GB', 25, enabled=True)]
    sub.components = [
        Component('R_S', '国产片式膜电阻器', 1, {'resistance_ohm': 100.0, 'film': '薄膜', 'quality': 'A1P'})
    ]
    mod_comp = create_project_module(sub, name='MOD_A', quantity=1)

    proj = copy.deepcopy(sample_project)
    proj.components.append(mod_comp)
    win = MainWindow(proj)
    try:
        win.show()
        qapp.processEvents()

        # Inspector quick stress editing on C1 (row 1, 国产1类瓷介电容器)
        win.select_component_row(1)
        insp = win.inspector
        assert insp.stack.currentIndex() == 1
        insp.quick_stress.setText('')
        insp._on_stress_apply()
        assert '请输入' in insp.quick_feedback.text()
        insp.quick_stress.setText('9.9')  # Exceeds stress_max
        insp._on_stress_apply()
        assert '未应用' in insp.quick_feedback.text()
        insp.quick_stress.setText('0.3')
        insp._on_stress_apply()
        assert win.project.components[1].parameters['stress_ratio'] == pytest.approx(0.3)
        insp.quick_stress.setText('0.15 + 0.15')
        insp._on_stress_apply()
        assert win.project.components[1].parameters['stress_ratio'] == '0.15 + 0.15'

        # Multi-selection inspector view (page 2) & overview Top-5 click navigation (page 0)
        win.refresh([win.project.components[0].identifier, win.project.components[1].identifier], recalculate=False)
        assert insp.stack.currentIndex() == 2
        win.refresh([], recalculate=False)
        assert insp.stack.currentIndex() == 0
        if insp.top_list.count() > 0:
            insp._on_top_clicked(insp.top_list.item(0))
            assert len(win.selected_rows()) == 1

        # add_component & edit_component (single ComponentDialog, ModuleDialog, BatchDialog, and module-in-batch guard)
        monkeypatch.setattr(
            ComponentDialog,
            'exec',
            lambda self_d: setattr(
                self_d,
                'result_component',
                Component('R_ADDED', '国产片式膜电阻器', 1, {'resistance_ohm': 220.0, 'film': '薄膜', 'quality': 'A1P'}),
            ) or QDialog.DialogCode.Accepted,
        )
        before_cnt = len(win.project.components)
        win.add_component()
        assert len(win.project.components) == before_cnt + 1

        # Edit single normal component
        win.select_component_row(0)
        win.edit_component()

        # Edit 工程模块 component -> routes to ModuleDialog
        mod_row = next(i for i, c in enumerate(win.project.components) if c.model == '工程模块')
        monkeypatch.setattr(
            ModuleDialog,
            'exec',
            lambda self_m: setattr(self_m, 'result_component', copy.deepcopy(self_m.original)) or QDialog.DialogCode.Accepted,
        )
        win.select_component_row(mod_row)
        win.edit_component()

        # Batch configure including 工程模块 -> shows QMessageBox.information guard
        infos.clear()
        win.batch_configure(rows=[0, mod_row])
        assert infos and '工程模块' in infos[-1]

        # Batch configure on two regular components -> routes to BatchDialog
        monkeypatch.setattr(
            BatchDialog,
            'exec',
            lambda self_b: setattr(self_b, 'result_components', copy.deepcopy(self_b.components)) or QDialog.DialogCode.Accepted,
        )
        win.batch_configure(rows=[0, 1])

        # calculate_project & save_as (auto-appending .json suffix)
        win.calculate_project()
        save_target_no_ext = isolated_tmp / 'saved_via_save_as'
        monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(save_target_no_ext), 'JSON 工程 (*.json)'))
        assert win.save_as() is True
        assert (isolated_tmp / 'saved_via_save_as.json').is_file()
        assert win.dirty is False
    finally:
        win.dirty = False
        win.close()

