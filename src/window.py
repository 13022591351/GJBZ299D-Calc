"""Native Qt workbench; business calculations and persistence stay outside UI."""

import copy
import csv
import sqlite3
from pathlib import Path

from PyQt6.QtCore import (
    QItemSelection,
    QItemSelectionModel,
    QItemSelectionRange,
    QModelIndex,
    QSettings,
    Qt,
    QTimer,
    QUrl,
)
from PyQt6.QtGui import QAction, QKeySequence, QShortcut, QUndoStack
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from . import APP_VERSION
from .analysis_methods.workspace import (
    configured_analyses,
    project_condition_source_diagnostics,
)
from .analysis_page import AnalysisPage
from .batch_dialog import BatchDialog
from .bom import append_bom, read_bom_sources
from .bom_dialog import BomImportDialog
from .component_fields import COLUMNS, SORT_LABELS
from .component_tree import ComponentOrder, ComponentTree, ShareBarDelegate
from .dialogs import ComponentDialog, label
from .domain import Component, Project
from .environment_editor import EnvironmentEditor
from .pages import coverage_page
from .prediction import STATES
from .project_import import ModuleDialog, ProjectComponentImportDialog
from .shared_io import (
    PUBLIC_FILTER,
    SUPPLIER_FILTER,
    export_redacted_file,
    load_supplier_bundle,
    load_supplier_project,
)
from .standard_page import StandardPage
from .storage import load_project, save_project
from .table_layout import resizable_columns, resizable_splitter
from .theme import THEME_BUS, app_icon, apply_theme, current_theme, fit_window
from .ui import undo as _undo_mod
from .ui.bom_wizard import BomPreMapDialog
from .ui.category_bar import CategoryParetoBar
from .ui.inspector import ComponentInspector
from .ui.toolbox import ToolboxPage
from .ui.undo import ProjectUndoCommand, clear_autosave, find_autosave, write_autosave
from .view_dialog import ViewDialog

SUPPORTED_DROP_SUFFIXES = (".json", ".xlsx", ".xls", ".csv", ".tsv")


def _call_write_autosave(project_path, project, base_revision=None):
    try:
        return write_autosave(project_path, project, base_revision=base_revision)
    except TypeError:
        return write_autosave(project_path, project)


def autosave_matches_target(project_path, current_revision, *, autosave_rev=None):
    checker = getattr(_undo_mod, "autosave_matches_target", None)
    if callable(checker):
        return bool(checker(project_path, current_revision))
    if autosave_rev is not None and current_revision is not None:
        return int(autosave_rev) == int(current_revision)
    return True


def is_autosave_owned_by_other_live_pid(project_path):
    checker = getattr(_undo_mod, "is_autosave_owned_by_other_live_pid", None)
    if callable(checker):
        return bool(checker(project_path))
    return False


def _projects_equal(a, b):
    if a == b:
        return True
    if (
        len(a.components) != len(b.components)
        or len(a.conditions) != len(b.conditions)
        or a.name != b.name
        or a.notes != b.notes
        or a.prediction_state != b.prediction_state
        or a.grouped != b.grouped
        or a.group_fields != b.group_fields
        or a.visible_fields != b.visible_fields
        or a.sort_mode != b.sort_mode
        or a.analysis != b.analysis
        or a.conditions != b.conditions
    ):
        return False
    return a.to_dict() == b.to_dict()


def _extract_dropped_paths(mime_data):
    """Return local Path objects from Qt MIME data, ignoring non-file URLs."""
    if mime_data is None or not mime_data.hasUrls():
        return []
    return [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile() and url.toLocalFile()]


def _vline():
    """Return a slim vertical toolbar divider."""
    line = QFrame()
    line.setObjectName("VLine")
    line.setFrameShape(QFrame.Shape.VLine)
    return line


class MainWindow(QMainWindow):
    def __init__(self, project=None):
        super().__init__()
        self.setWindowIcon(app_icon())
        self.project = project or Project()
        self.path = None
        self.revision = None
        self._base_dirty = bool(project)
        self.dirty = bool(project)
        self._refreshing = False
        self._committing_edits = False
        self._environment_commit_changed = False
        self._applying_undo = False
        self._recording_mutation = False
        self._resetting_stack = False
        self._last_autosave_ok = True
        self._analysis_merge_epoch = 0
        self._committed_snapshot = copy.deepcopy(self.project)
        self._committed_selected_ids = []
        self.view_context = None

        self._autosave_timer = QTimer(self)
        self._autosave_timer.setSingleShot(True)
        self._autosave_timer.setInterval(350)
        self._autosave_timer.timeout.connect(self._write_autosave_if_needed)

        self._analysis_merge_timer = QTimer(self)
        self._analysis_merge_timer.setSingleShot(True)
        self._analysis_merge_timer.setInterval(1500)
        self._analysis_merge_timer.timeout.connect(self._advance_analysis_merge_epoch)

        self.undo_stack = QUndoStack(self)
        self.undo_stack.setUndoLimit(50)
        self.undo_stack.cleanChanged.connect(self._sync_dirty_from_undo)
        self.undo_stack.indexChanged.connect(self._on_undo_index_changed)

        fit_window(self, 1520, 920, min_width=960, min_height=600)
        self.setAcceptDrops(True)
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        sidebar = QFrame()
        sidebar.setObjectName("Navigation")
        side = QHBoxLayout(sidebar)
        side.setContentsMargins(8, 2, 8, 2)
        side.addWidget(label("GJB/Z 299D", "Brand", wrap=False))
        side.addSpacing(12)
        self.navigation = []
        self.stack = QStackedWidget()
        for i, title in enumerate(("工程与计算", "标准参数", "工程工具箱", "任务与分析", "数据与说明")):
            button = QPushButton(title)
            button.setObjectName("Nav")
            button.setCheckable(True)
            button.clicked.connect(lambda _checked=False, index=i: self.navigate(index))
            side.addWidget(button)
            self.navigation.append(button)
        side.addStretch()

        self.undo_button = QPushButton("↶ 撤销")
        self.undo_button.setEnabled(False)
        self.undo_button.setToolTip("撤销上一步工程修改 (Ctrl+Z)")
        self.undo_button.clicked.connect(self.undo_stack.undo)
        self.undo_stack.canUndoChanged.connect(self.undo_button.setEnabled)
        side.addWidget(self.undo_button)

        self.redo_button = QPushButton("↷ 重做")
        self.redo_button.setEnabled(False)
        self.redo_button.setToolTip("重做已撤销的修改 (Ctrl+Y)")
        self.redo_button.clicked.connect(self.undo_stack.redo)
        self.undo_stack.canRedoChanged.connect(self.redo_button.setEnabled)
        side.addWidget(self.redo_button)
        side.addWidget(_vline())

        self.theme_button = QPushButton()
        self.theme_button.setToolTip("在深色与浅色界面主题之间切换")
        self.theme_button.clicked.connect(self.toggle_theme)
        self._sync_theme_button()
        side.addWidget(self.theme_button)
        side.addWidget(_vline())
        side.addWidget(label("GJB/Z 299D—2024", wrap=False))

        outer.addWidget(sidebar)
        outer.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.stack.addWidget(self.make_project_page())
        self.standard_page = StandardPage()
        self.stack.addWidget(self.standard_page)
        self.toolbox_page = ToolboxPage()
        self.stress_page = self.toolbox_page
        self.stack.addWidget(self.toolbox_page)
        self.analysis_page = AnalysisPage()
        self.analysis_page.restore_state(self.project.analysis)
        self.analysis_page.changed.connect(self.analysis_changed)
        self.stack.addWidget(self.analysis_page)
        self.stack.addWidget(coverage_page())

        THEME_BUS.changed.connect(self._on_theme_changed)
        self.create_shortcuts()
        self.navigate(0)
        self.refresh(recalculate=False)
        self._committed_snapshot = copy.deepcopy(self.project)

    def _sync_theme_button(self):
        self.theme_button.setText("☀ 浅色" if current_theme() == "dark" else "🌙 深色")

    def toggle_theme(self):
        app = QApplication.instance()
        if app is None:
            return
        next_mode = "light" if current_theme() == "dark" else "dark"
        apply_theme(app, next_mode)

    def _on_theme_changed(self, _mode):
        self._sync_theme_button()
        self.table.viewport().update()
        self.category_bar.update()

    def create_shortcuts(self):
        for title, shortcut, callback in [
            ("新建工程", "Ctrl+N", self.new_project),
            ("打开工程…", "Ctrl+O", self.open),
            ("保存工程", "Ctrl+S", self.save),
            ("另存为…", "Ctrl+Shift+S", self.save_as),
            ("导入 BOM…", "Ctrl+I", self.import_bom),
            ("撤销", "Ctrl+Z", self.undo_stack.undo),
            ("重做", "Ctrl+Y", self.undo_stack.redo),
            ("重做(备选)", "Ctrl+Shift+Z", self.undo_stack.redo),
        ]:
            action = QAction(title, self)
            action.setShortcut(QKeySequence(shortcut))
            action.triggered.connect(callback)
            self.addAction(action)

    def make_project_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(4)

        title_row = QHBoxLayout()
        title_row.setSpacing(6)
        self.project_title = label(self.project.name, "Title", wrap=False)
        title_row.addWidget(self.project_title)
        title_row.addWidget(_vline())
        title_row.addWidget(label("预计状态", wrap=False))
        self.state_box = QComboBox()
        for key, title in STATES.items():
            self.state_box.addItem(title, key)
        self.state_box.setCurrentIndex(self.state_box.findData(self.project.prediction_state))
        self.state_box.currentIndexChanged.connect(self.prediction_changed)
        title_row.addWidget(self.state_box)
        title_row.addStretch()

        self.project_buttons = {}
        for title, callback in (("新建", self.new_project), ("打开", self.open), ("清空", self.clear_project),
                                ("保存", self.save), ("另存为", self.save_as), ("导出计算数据", self.export)):
            if title == "导出计算数据":
                title_row.addWidget(_vline())
            button = QPushButton(title)
            if title == "清空":
                button.setToolTip("清空当前工程内的全部元器件、工况与分析数据（可按 Ctrl+Z 撤销）")
            if title == "导出计算数据":
                button.setText("导出报告 / 脱敏")
                self.export_menu = QMenu(button)
                self.export_menu.addAction("计算数据（XLSX / XLS）…", self.export)
                self.export_menu.addAction("脱敏工程（XLSX / XLS / JSON）…", self.export_redacted)
                button.setMenu(self.export_menu)
            else:
                button.clicked.connect(callback)
            title_row.addWidget(button)
            self.project_buttons[title] = button
        layout.addLayout(title_row)

        self.environment_editor = EnvironmentEditor(self.project)
        self.environment_editor.changed.connect(self.environment_changed)
        self.environment_editor.draft_changed.connect(self.update_title)
        self.environment_editor.view_changed.connect(self.view_condition_changed)
        self.calculate_button = self.environment_editor.calculate_button
        self.calculate_button.clicked.connect(self.calculate_project)
        layout.addWidget(self.environment_editor)

        self.toolbar_card = QFrame()
        self.toolbar_card.setObjectName("ToolbarCard")
        toolbar_layout = QVBoxLayout(self.toolbar_card)
        toolbar_layout.setContentsMargins(6, 5, 6, 5)
        toolbar_layout.setSpacing(4)

        actions = QHBoxLayout()
        actions.setSpacing(5)
        self.import_button = QPushButton("导入 BOM")
        self.import_button.setObjectName("Primary")
        self.import_button.clicked.connect(self.import_bom)
        actions.addWidget(self.import_button)

        self.project_import_button = QPushButton("从工程引入")
        self.project_import_button.clicked.connect(self.import_project_components)
        self.project_import_button.setToolTip("将供应商工程作为一个模块引入，或选择其中的元器件。")
        actions.addWidget(self.project_import_button)
        actions.addWidget(_vline())

        self.add_button = QPushButton("＋ 添加元器件")
        self.add_button.clicked.connect(self.add_component)
        actions.addWidget(self.add_button)

        self.add_board_button = QPushButton("＋ 结构与互连")
        self.structure_menu = QMenu(self.add_board_button)
        self.structure_actions = {}
        for title, callback in (("印制板本体", self.add_board), ("焊接点", self.add_solder),
                                ("SMT互连（板级）", self.add_smt)):
            action = self.structure_menu.addAction(title)
            action.triggered.connect(callback)
            self.structure_actions[title] = action
        self.structure_menu.addSeparator()
        self.structure_menu.addAction("封装、基片与载体如何计入…", self.show_carrier_scope)
        self.add_board_button.setMenu(self.structure_menu)
        self.add_board_button.setToolTip("补充印制板本体、焊接点或板级 SMT 热循环项；已包含在整件率中的部分不重复添加。")
        actions.addWidget(self.add_board_button)

        self.edit_button = QPushButton("配置选中项")
        self.edit_button.clicked.connect(self.edit_component)
        self.edit_button.setToolTip("选中组时批量设置，选中位号时单独设置；仅覆盖修改的参数。")
        actions.addWidget(self.edit_button)

        self.delete_button = QPushButton("移除")
        self.delete_button.clicked.connect(self.remove_component)
        actions.addWidget(self.delete_button)
        actions.addStretch()

        self.filter_field = QLineEdit()
        self.filter_field.setClearButtonEnabled(True)
        self.filter_field.setMinimumWidth(220)
        self.filter_field.setMaximumWidth(320)
        self.filter_field.setPlaceholderText("搜索位号、标称值、型号或计算模型…")
        actions.addWidget(self.filter_field)

        self.inspector_toggle = QPushButton("侧边详情")
        self.inspector_toggle.setCheckable(True)
        self.inspector_toggle.setChecked(True)
        actions.addWidget(self.inspector_toggle)
        toolbar_layout.addLayout(actions)

        order_row = QHBoxLayout()
        order_row.setSpacing(5)
        self.filter_chips = {}
        self.chip_group = QButtonGroup(self)
        self.chip_group.setExclusive(True)
        for mode, caption in (("all", "全部"), ("pending", "待配置"), ("calculated", "已计算"), ("error", "配置异常")):
            chip = QPushButton(caption)
            chip.setObjectName("Chip")
            chip.setCheckable(True)
            if mode == "all":
                chip.setChecked(True)
            chip.clicked.connect(lambda _c=False, m=mode: self.status_filter_changed(m))
            self.chip_group.addButton(chip)
            self.filter_chips[mode] = chip
            order_row.addWidget(chip)

        order_row.addSpacing(6)
        self.view_summary = label("", wrap=False)
        order_row.addWidget(self.view_summary)
        order_row.addStretch()

        order_row.addWidget(label("排序", wrap=False))
        self.sort_box = QComboBox()
        for key, title in SORT_LABELS.items():
            self.sort_box.addItem(title, key)
        self.sort_box.currentIndexChanged.connect(self.order_changed)
        order_row.addWidget(self.sort_box)
        order_row.addWidget(_vline())

        self.group_box = QCheckBox("分组显示")
        self.group_box.setChecked(self.project.grouped)
        self.group_box.toggled.connect(self.grouping_changed)
        order_row.addWidget(self.group_box)

        self.view_button = QPushButton("列与分组…")
        self.view_button.clicked.connect(self.configure_view)
        order_row.addWidget(self.view_button)

        self.expand_button = QPushButton("展开")
        self.collapse_button = QPushButton("折叠")
        order_row.addWidget(self.expand_button)
        order_row.addWidget(self.collapse_button)
        toolbar_layout.addLayout(order_row)
        layout.addWidget(self.toolbar_card)

        self.category_bar = CategoryParetoBar()
        self.category_bar.category_clicked.connect(self.category_filter_changed)
        layout.addWidget(self.category_bar)

        context = self.environment_editor.results[self.environment_editor.selected_condition_id]
        self.table_model = ComponentTree(self.project, context=context)
        self.table_model.domestic_change_requested.connect(
            self.change_domestic, Qt.ConnectionType.QueuedConnection
        )
        self.proxy = ComponentOrder(self)
        self.proxy.setSourceModel(self.table_model)
        self.proxy.setFilterKeyColumn(-1)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy.setSortRole(Qt.ItemDataRole.UserRole)
        self.filter_field.textChanged.connect(self.filter_changed)

        self.table = QTreeView()
        self.table.setUniformRowHeights(True)
        self.table.setMinimumHeight(160)
        self.table.setRootIsDecorated(False)
        self.table.setIndentation(0)
        self.table.setExpandsOnDoubleClick(False)
        self.expand_button.clicked.connect(self.expand_all_groups)
        self.collapse_button.clicked.connect(self.collapse_all_groups)
        self.table.clicked.connect(self._table_clicked)
        self.table.setModel(self.proxy)
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_table_context_menu)

        share_col = next(i for i, spec in enumerate(COLUMNS) if spec.key == "share")
        self.share_delegate = ShareBarDelegate(self.table)
        self.table.setItemDelegateForColumn(share_col, self.share_delegate)
        min_col_widths = {
            "index": 46, "designators": 100, "origin": 44, "Value": 80,
            "Footprint": 64, "Manufacturer Part Number": 92, "quantity": 46,
            "model": 120, "parameters": 120, "stress": 120, "status": 68,
            "rate": 140, "weighted": 140, "share": 72, "Manufacturer": 90, "Description": 120,
        }
        fixed_cols = tuple(
            i for i, spec in enumerate(COLUMNS)
            if spec.key in ("index", "origin", "quantity", "status", "rate", "weighted", "share")
        )
        resizable_columns(
            self.table,
            "components-v8",
            [spec.width for spec in COLUMNS],
            min_widths=[min_col_widths[spec.key] for spec in COLUMNS],
            fixed_columns=fixed_cols,
        )
        self.table.header().setSectionsClickable(True)
        self.table.header().sectionClicked.connect(self._header_clicked)

        self.table.doubleClicked.connect(self.edit_component)
        self.table.selectionModel().selectionChanged.connect(self.selection_changed)

        for key_seq, cb in (
            (Qt.Key.Key_Return, self.edit_component),
            (Qt.Key.Key_Enter, self.edit_component),
            (Qt.Key.Key_Delete, self.remove_component),
            ("Ctrl+D", self.duplicate_components),
        ):
            sc = QShortcut(QKeySequence(key_seq), self.table)
            sc.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            sc.activated.connect(cb)

        self.inspector = ComponentInspector()
        self.inspector_toggle.toggled.connect(self.inspector.setVisible)
        self.inspector.edit_requested.connect(self.edit_component)
        self.inspector.quick_parameter_changed.connect(self.quick_edit_parameter)
        self.inspector.standard_link_clicked.connect(self.open_standard_uri)
        self.inspector.component_selected.connect(self.select_component_row)

        self.work_splitter = QSplitter()
        self.work_splitter.addWidget(self.table)
        self.work_splitter.addWidget(self.inspector)
        self.work_splitter.setStretchFactor(0, 1)
        self.work_splitter.setStretchFactor(1, 0)
        resizable_splitter(self.work_splitter, "project-inspector", [1165, 325])
        layout.addWidget(self.work_splitter, 1)
        return page

    def navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.navigation):
            button.setChecked(i == index)

    def open_standard_uri(self, url):
        """Navigate to the StandardPage table viewer and highlight the linked table/cell."""
        qurl = QUrl(url) if isinstance(url, str) else url
        if qurl.scheme() != "standard":
            return False
        host = qurl.host()
        parts = [p for p in qurl.path().strip("/").split("/") if p]
        if host == "table" and parts:
            self.navigate(1)
            return self.standard_page.open_table(parts[0], parts[1] if len(parts) > 1 else None)
        return False

    def _advance_analysis_merge_epoch(self):
        self._analysis_merge_epoch += 1

    def _untitled_autosave_anchor(self):
        settings_file = QSettings(
            QSettings.Format.IniFormat, QSettings.Scope.UserScope, "DuoYuWang", "GJB299D"
        ).fileName()
        return Path(settings_file).parent / "untitled_project.json"

    def untitled_autosave_path(self):
        return find_autosave(self._untitled_autosave_anchor())

    def _clear_untitled_autosave(self):
        clear_autosave(self._untitled_autosave_anchor())

    def _clear_path_autosave_if_owned(self, target_path):
        if target_path is not None and not is_autosave_owned_by_other_live_pid(target_path):
            clear_autosave(target_path)

    def recover_untitled_autosave(self, *, prompt=True):
        anchor = self._untitled_autosave_anchor()
        autosave = find_autosave(anchor)
        if autosave is None:
            return False
        if prompt:
            answer = QMessageBox.question(
                self,
                "发现未保存恢复草稿",
                "检测到上次存在未保存的未命名工程自动恢复草稿，是否从恢复草稿继续编辑？",
                QMessageBox.StandardButton.Yes
                | QMessageBox.StandardButton.No
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Yes,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.No:
                clear_autosave(anchor)
                return False
        try:
            recovered, _rev = load_project(autosave)
        except (ValueError, OSError, sqlite3.Error) as error:
            QMessageBox.warning(
                self,
                "恢复草稿已损坏",
                f"无法读取未命名工程的自动恢复草稿（{error}），已忽略损坏草稿。",
            )
            clear_autosave(anchor)
            return False
        self.replace_project(recovered, path=None, revision=None, dirty=True, keep_untitled_autosave=True)
        self.statusBar().showMessage("已恢复未保存的未命名工程草稿；确认无误后请点击“保存”")
        return True

    def _sync_dirty_from_undo(self, *_args):
        self.dirty = self._base_dirty or not self.undo_stack.isClean()
        self.update_title()

    def _write_autosave_if_needed(self):
        if self.environment_editor.temperature_errors:
            return False
        target_path = self.path if self.path is not None else self._untitled_autosave_anchor()
        base_rev = self.revision if self.path is not None else None
        if self.dirty:
            try:
                target_path.parent.mkdir(parents=True, exist_ok=True)
                _call_write_autosave(target_path, self.project, base_rev)
                self._last_autosave_ok = True
                return True
            except (OSError, ValueError) as error:
                self._last_autosave_ok = False
                self.statusBar().showMessage(f"自动恢复草稿写入失败：{error}")
                return False
        else:
            if self.path is not None:
                self._clear_path_autosave_if_owned(self.path)
            self._clear_untitled_autosave()
            self._last_autosave_ok = True
            return True

    def _flush_autosave(self):
        if self._autosave_timer.isActive():
            self._autosave_timer.stop()
            self._write_autosave_if_needed()

    def _on_undo_index_changed(self, _index):
        if self._recording_mutation or self._resetting_stack:
            return
        self._sync_dirty_from_undo()
        self._autosave_timer.stop()
        self._write_autosave_if_needed()

    def _apply_undo_snapshot(self, project_copy, selected_ids):
        self._autosave_timer.stop()
        self._applying_undo = True
        self._refreshing = True
        try:
            self.project = copy.deepcopy(project_copy)
            if hasattr(self, "analysis_page"):
                self.analysis_page.clear_condition_sources()
                self.analysis_page.restore_state(self.project.analysis)
        finally:
            self._refreshing = False
        try:
            self.refresh(selected_ids)
            self._committed_snapshot = copy.deepcopy(self.project)
            self._committed_selected_ids = list(selected_ids)
        finally:
            self._applying_undo = False

    def _record_mutation(self, description, selected_ids=None, *, merge_id=-1, debounce_autosave=False):
        if self._applying_undo:
            return
        before_proj = self._committed_snapshot
        after_proj = copy.deepcopy(self.project)
        if _projects_equal(before_proj, after_proj):
            if selected_ids is not None:
                self._committed_selected_ids = list(selected_ids)
            return
        if merge_id < 0:
            self._analysis_merge_timer.stop()
            self._analysis_merge_epoch += 1
        if selected_ids is None:
            selected_ids = [
                self.project.components[row].identifier
                for row in self.selected_rows() if row < len(self.project.components)
            ]
        before_sel = list(self._committed_selected_ids)
        after_sel = list(selected_ids)
        cmd = ProjectUndoCommand(
            description,
            before_proj,
            after_proj,
            before_sel,
            after_sel,
            self._apply_undo_snapshot,
            already_applied=True,
            merge_id=merge_id,
            copy_projects=False,
        )
        self._recording_mutation = True
        try:
            self.undo_stack.push(cmd)
        finally:
            self._recording_mutation = False
        self._committed_snapshot = after_proj
        self._committed_selected_ids = after_sel
        self._sync_dirty_from_undo()
        if debounce_autosave:
            self._autosave_timer.start()
        else:
            self._autosave_timer.stop()
            self._write_autosave_if_needed()

    def _sync_project_analysis(self):
        if not hasattr(self, "analysis_page"):
            return
        state = self.analysis_page.state()
        if (self.project.analysis or configured_analyses(state)) and state != self.project.analysis:
            self.project.analysis = state

    def analysis_changed(self):
        if not self._refreshing and not self._applying_undo:
            state = self.analysis_page.state()
            if state != self.project.analysis:
                self.project.analysis = state
                tab_offset = getattr(
                    self.analysis_page, "active_merge_offset", self.analysis_page.tabs.currentIndex()
                )
                merge_id = 1001 + tab_offset + self._analysis_merge_epoch * 16
                self._analysis_merge_timer.start()
                self._record_mutation("更新任务分析配置", merge_id=merge_id, debounce_autosave=True)

    def prediction_changed(self):
        if self._refreshing or self._applying_undo:
            return
        if not self.commit_pending_edits():
            self.state_box.blockSignals(True)
            self.state_box.setCurrentIndex(self.state_box.findData(self.project.prediction_state))
            self.state_box.blockSignals(False)
            return
        target_state = self.state_box.currentData()
        if not self.project.set_prediction_state(target_state, auto_suggest=True):
            return
        self.refresh()
        self.table_model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(COLUMNS) - 1)
        self._record_mutation("切换工作/非工作预计状态")

    def _toggle_group_key(self, group_key):
        sel_model = self.table.selectionModel()
        blocked = sel_model.blockSignals(True)
        changed = False
        try:
            if self.table_model.toggle_group(group_key):
                self.proxy.invalidate()
                changed = True
        finally:
            sel_model.blockSignals(blocked)
        if changed:
            self.table.viewport().update()
            self.selection_changed()
            self.update_view_summary()

    def _table_clicked(self, index):
        if not index.isValid() or not self.project.grouped:
            return
        if COLUMNS[index.column()].key != "index":
            return
        src = self.proxy.mapToSource(index)
        node = src.internalPointer() if src.isValid() else None
        if node is not None and node.group_size > 1:
            self._toggle_group_key(node.group_key)

    def expand_all_groups(self):
        sel_model = self.table.selectionModel()
        blocked = sel_model.blockSignals(True)
        changed = False
        try:
            if self.table_model.expand_all():
                self.proxy.invalidate()
                changed = True
        finally:
            sel_model.blockSignals(blocked)
        if changed:
            self.table.viewport().update()
            self.selection_changed()
        self.update_view_summary()

    def collapse_all_groups(self):
        sel_model = self.table.selectionModel()
        blocked = sel_model.blockSignals(True)
        changed = False
        try:
            if self.table_model.collapse_all():
                self.proxy.invalidate()
                changed = True
        finally:
            sel_model.blockSignals(blocked)
        if changed:
            self.table.viewport().update()
            self.selection_changed()
        self.update_view_summary()

    def view_condition_changed(self, *_args):
        if not self._refreshing:
            self.refresh(recalculate=False)

    def environment_changed(self, *_args):
        if self._refreshing or self._applying_undo:
            return
        if self._committing_edits:
            self._environment_commit_changed = True
            return
        self.refresh()
        self._record_mutation("调整工作环境与工况")

    def commit_pending_edits(self, *, quiet=False):
        """Make every command observe the same committed input, or keep its draft."""
        if self._committing_edits:
            return not self.environment_editor.temperature_errors
        self._committing_edits = True
        self._environment_commit_changed = False
        try:
            if hasattr(self, "analysis_page"):
                self.analysis_page.commit_pending_edits()
            valid = self.environment_editor.commit_pending_edits()
        finally:
            self._committing_edits = False
        if self._environment_commit_changed:
            self.refresh()
            self._record_mutation("提交工作环境温度")
        if valid:
            self._flush_autosave()
        elif not quiet:
            self.error(ValueError("工作环境中仍有无效温度输入，请修正后再执行操作；输入内容已保留。"))
        return valid

    def order_changed(self, *_args):
        if self._refreshing or self._applying_undo:
            return
        if not self.commit_pending_edits():
            self.sort_box.blockSignals(True)
            self.sort_box.setCurrentIndex(self.sort_box.findData(self.project.sort_mode))
            self.sort_box.blockSignals(False)
            return
        new_mode = self.sort_box.currentData()
        if new_mode and new_mode != self.project.sort_mode:
            self.project.sort_mode = new_mode
            self.proxy.invalidate()
            self._record_mutation("修改排序方式")

    def _header_clicked(self, logical_index):
        key = COLUMNS[logical_index].key
        mapping = {
            "index": "bom",
            "model": "model",
            "weighted": "weighted_desc",
            "share": "weighted_desc",
            "rate": "rate_desc",
            "quantity": "quantity_desc",
        }
        mode = mapping.get(key)
        if mode and mode != self.project.sort_mode:
            idx = self.sort_box.findData(mode)
            if idx >= 0:
                self.sort_box.setCurrentIndex(idx)

    def grouping_changed(self, checked):
        if self._refreshing or self._applying_undo:
            return
        if not self.commit_pending_edits():
            self.group_box.blockSignals(True)
            self.group_box.setChecked(self.project.grouped)
            self.group_box.blockSignals(False)
            return
        self.project.grouped = checked
        self.refresh()
        self._record_mutation("切换分组显示")

    def configure_view(self):
        if not self.commit_pending_edits():
            return
        dialog = ViewDialog(self.project, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            if (
                set(dialog.group_fields) == set(self.project.group_fields)
                and dialog.visible_fields == self.project.visible_fields
            ):
                return
            self.project.group_fields = dialog.group_fields
            self.project.visible_fields = dialog.visible_fields
            self.refresh()
            self._record_mutation("调整显示字段与分组依据")

    def status_filter_changed(self, mode):
        sel_model = self.table.selectionModel()
        blocked = sel_model.blockSignals(True)
        try:
            self.proxy.set_status_filter(mode)
            self.table_model.set_force_expand(self.proxy.has_active_filter())
            self.proxy.invalidate()
        finally:
            sel_model.blockSignals(blocked)
        self.table.viewport().update()
        self.selection_changed()
        self.update_view_summary()

    def category_filter_changed(self, category):
        sel_model = self.table.selectionModel()
        blocked = sel_model.blockSignals(True)
        try:
            self.proxy.set_category_filter(category)
            self.table_model.set_force_expand(self.proxy.has_active_filter())
            self.proxy.invalidate()
        finally:
            sel_model.blockSignals(blocked)
        self.category_bar.update_data(self.project, self.view_context, active_category=category)
        self.category_bar.setVisible(
            bool(self.category_bar.entries()) or category is not None
        )
        self.table.viewport().update()
        self.selection_changed()
        self.update_view_summary()

    def filter_changed(self, text):
        sel_model = self.table.selectionModel()
        blocked = sel_model.blockSignals(True)
        try:
            self.proxy.setFilterFixedString(text)
            self.table_model.set_force_expand(self.proxy.has_active_filter())
            self.proxy.invalidate()
        finally:
            sel_model.blockSignals(blocked)
        self.table.viewport().update()
        self.selection_changed()
        self.update_view_summary()

    def update_view_summary(self):
        roots = self.proxy.rowCount()
        count = sum(len(self.rows_for_index(self.proxy.index(i, 0))) for i in range(roots))
        condition = self.environment_editor.selected_condition
        current = (condition.name + ("（未计算）" if not condition.enabled else "")) if condition else "无工况"
        cat_txt = f" · 类别={self.proxy.category_filter}" if self.proxy.category_filter else ""
        self.view_summary.setText(f"明细工况：{current}{cat_txt} · {roots} 行 · {count} 项")
        fields = "、".join(c.label for c in COLUMNS if c.key in self.project.group_fields)
        self.group_box.setToolTip("分组依据：" + fields)

    def _update_filter_chips(self):
        total = len(self.table_model.values)
        errors = sum(1 for v in self.table_model.values if v.get("config_error"))
        calculated = sum(1 for v in self.table_model.values if v.get("rate") is not None)
        pending = sum(1 for v in self.table_model.values if v.get("rate") is None and not v.get("config_error"))
        counts = {"all": total, "pending": pending, "calculated": calculated, "error": errors}
        labels = {"all": "全部", "pending": "待配置", "calculated": "已计算", "error": "配置异常"}
        for key, chip in self.filter_chips.items():
            chip.setText(f"{labels[key]} ({counts[key]})")

    def calculate_project(self):
        if not self.commit_pending_edits():
            return
        try:
            self.project.validate()
            self.refresh()
            count = len(self.project.enabled_conditions)
            self.statusBar().showMessage(
                f"已更新 {count} 个勾选工况的计算结果；点击工况行查看元器件明细"
                if count else "尚未勾选参与计算的工作环境"
            )
        except (ValueError, OverflowError) as error:
            self.error(error)

    def mark_dirty(self):
        self._base_dirty = True
        self.dirty = True
        self.update_title()

    def update_title(self):
        unsaved = self.dirty or self.environment_editor.has_pending_edits()
        if hasattr(self, "project_title"):
            self.project_title.setText(self.project.name)
        self.setWindowTitle(f"{'● ' if unsaved else ''}{self.project.name} — GJB299D 可靠性预计 {APP_VERSION}")
        self.statusBar().showMessage(
            f"{'未保存更改' if unsaved else '已保存' if self.path else '已就绪'}  ·  "
            f"{self.path or '尚未保存工程'}"
        )

    def refresh(self, selected_ids=None, *, recalculate=True):
        self._refreshing = True
        try:
            self.state_box.blockSignals(True)
            self.state_box.setCurrentIndex(self.state_box.findData(self.project.prediction_state))
            self.state_box.blockSignals(False)

            if selected_ids is None:
                selected_ids = [
                    self.table_model.project.components[row].identifier
                    for row in self.selected_rows()
                    if row < len(self.table_model.project.components)
                ]

            self.environment_editor.sync(self.project, recalculate=recalculate)
            if hasattr(self, "analysis_page"):
                sources, source_issues = project_condition_source_diagnostics(
                    self.project, active_results=self.environment_editor.results
                )
                self.analysis_page.set_condition_sources(sources, source_issues=source_issues)
                self._sync_project_analysis()

            condition = self.environment_editor.selected_condition
            self.view_context = self.environment_editor.results[condition.identifier]

            sel_model = self.table.selectionModel()
            sel_blocked = sel_model.blockSignals(True)
            first_index = None
            try:
                self.table_model.context = self.view_context
                self.table_model.project = self.project
                self.table_model.refresh()
                self.table_model.headerDataChanged.emit(Qt.Orientation.Horizontal, 0, len(COLUMNS) - 1)

                self.group_box.blockSignals(True)
                self.group_box.setChecked(self.project.grouped)
                self.group_box.blockSignals(False)
                self.expand_button.setEnabled(self.project.grouped)
                self.collapse_button.setEnabled(self.project.grouped)

                self.table.column_widths.restoring = True
                try:
                    for column, spec in enumerate(COLUMNS):
                        self.table.setColumnHidden(column, spec.key not in self.project.visible_fields)
                finally:
                    self.table.column_widths.restoring = False
                self.table.column_widths.fit_to_viewport()

                self.sort_box.blockSignals(True)
                self.sort_box.setCurrentIndex(self.sort_box.findData(self.project.sort_mode))
                self.sort_box.blockSignals(False)
                self.proxy.invalidate()

                selected_set = set(selected_ids)
                matched_proxy_rows = []
                if selected_set:
                    for row, component in enumerate(self.project.components):
                        if component.identifier in selected_set:
                            index = self.proxy.mapFromSource(self.table_model.component_index(row))
                            if index.isValid():
                                if first_index is None:
                                    first_index = index
                                matched_proxy_rows.append(index.row())

                selection = QItemSelection()
                if matched_proxy_rows:
                    unique_rows = sorted(set(matched_proxy_rows))
                    last_col = len(COLUMNS) - 1
                    span_start = span_end = unique_rows[0]
                    for r in unique_rows[1:]:
                        if r == span_end + 1:
                            span_end = r
                        else:
                            selection.append(
                                QItemSelectionRange(
                                    self.proxy.index(span_start, 0),
                                    self.proxy.index(span_end, last_col),
                                )
                            )
                            span_start = span_end = r
                    selection.append(
                        QItemSelectionRange(
                            self.proxy.index(span_start, 0),
                            self.proxy.index(span_end, last_col),
                        )
                    )

                sel_model.clearSelection()
                if not selection.isEmpty():
                    sel_model.select(selection, QItemSelectionModel.SelectionFlag.Select)
                if first_index is not None:
                    sel_model.setCurrentIndex(first_index, QItemSelectionModel.SelectionFlag.NoUpdate)
            finally:
                sel_model.blockSignals(sel_blocked)

            if first_index is not None:
                self.table.scrollTo(first_index)
            self.table.viewport().update()

            self._update_filter_chips()
            self.category_bar.update_data(
                self.project, self.view_context, active_category=self.proxy.category_filter
            )
            self.category_bar.setVisible(
                bool(self.category_bar.entries()) or self.proxy.category_filter is not None
            )
            self.selection_changed()
            self.update_view_summary()
            self.update_title()
        finally:
            self._refreshing = False

    def rows_for_index(self, index):
        if not index.isValid():
            return []
        src = self.proxy.mapToSource(index)
        node = src.internalPointer() if src.isValid() else None
        if node is not None and not node.children:
            return node.component_rows
        if self.proxy.hasChildren(index):
            return [
                row for i in range(self.proxy.rowCount(index))
                for row in self.rows_for_index(self.proxy.index(i, 0, index))
            ]
        return self.table_model.component_rows(src)

    def selected_rows(self):
        sel_model = self.table.selectionModel()
        if sel_model is None:
            return []
        rows = set()
        for rng in sel_model.selection():
            if not rng.isValid():
                continue
            parent = rng.parent()
            for proxy_row in range(rng.top(), rng.bottom() + 1):
                rows.update(self.rows_for_index(self.proxy.index(proxy_row, 0, parent)))
        return sorted(rows)

    def selected_row(self):
        selected = self.selected_rows()
        return selected[0] if len(selected) == 1 else -1

    def _reset_filters(self):
        blocked = self.filter_field.blockSignals(True)
        try:
            self.filter_field.clear()
        finally:
            self.filter_field.blockSignals(blocked)
        self.filter_chips["all"].setChecked(True)
        sel_model = self.table.selectionModel()
        sel_blocked = sel_model.blockSignals(True)
        try:
            self.proxy.setFilterFixedString("")
            self.proxy.set_status_filter("all")
            self.proxy.set_category_filter(None)
            self.table_model.set_force_expand(False)
        finally:
            sel_model.blockSignals(sel_blocked)

    def select_component_row(self, row):
        if not 0 <= row < len(self.project.components):
            return
        if self.table_model.expand_group_for_row(row):
            self.proxy.invalidate()
            self.update_view_summary()
        index = self.proxy.mapFromSource(self.table_model.component_index(row))
        if not index.isValid() and self.proxy.has_active_filter():
            self._reset_filters()
            self.table_model.expand_group_for_row(row)
            self.proxy.invalidate()
            self.category_bar.update_data(self.project, self.view_context, active_category=None)
            self.update_view_summary()
            index = self.proxy.mapFromSource(self.table_model.component_index(row))
        if index.isValid():
            self.table.selectionModel().select(
                index,
                QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows,
            )
            self.table.selectionModel().setCurrentIndex(index, QItemSelectionModel.SelectionFlag.NoUpdate)
            self.table.scrollTo(index)

    def selection_changed(self, *_args):
        selected = self.selected_rows()
        self.edit_button.setEnabled(bool(selected))
        self.delete_button.setEnabled(bool(selected))
        self.edit_button.setText("批量配置" if len(selected) > 1 else "配置选中项")
        if hasattr(self, "inspector") and self.view_context is not None:
            self.inspector.update_selection(self.project, self.view_context, selected)
        if not self._refreshing and not self._applying_undo:
            self._committed_selected_ids = [
                self.project.components[r].identifier for r in selected if r < len(self.project.components)
            ]

    def _show_table_context_menu(self, pos):
        index = self.table.indexAt(pos)
        if index.isValid() and not self.table.selectionModel().isRowSelected(index.row(), index.parent()):
            self.table.selectionModel().select(
                index, QItemSelectionModel.SelectionFlag.ClearAndSelect | QItemSelectionModel.SelectionFlag.Rows
            )
        rows = self.selected_rows()
        if not rows:
            return
        menu = QMenu(self.table)
        menu.addAction("批量配置选中项…" if len(rows) > 1 else "配置选中项…", self.edit_component)
        menu.addAction("复制元器件 (Ctrl+D)", self.duplicate_components)

        if index.isValid() and self.project.grouped and not self.table_model.force_expand:
            src = self.proxy.mapToSource(index)
            node = src.internalPointer() if src.isValid() else None
            if node is not None and node.group_size > 1:
                expanded = self.table_model.is_group_expanded(node.group_key)
                menu.addAction(
                    "折叠所在分组" if expanded else "展开所在分组",
                    lambda gk=node.group_key: self._toggle_group_key(gk),
                )

        mpns = {
            self.project.components[r].bom.get("row", {}).get("Manufacturer Part Number", "").strip()
            for r in rows
        } - {""}
        if len(mpns) == 1:
            mpn = next(iter(mpns))
            menu.addAction(f"选中同型号 ({mpn})", lambda m=mpn: self.select_same_mpn(m))

        all_dom = all(self.project.components[r].is_domestic for r in rows)
        menu.addAction(
            "切换为进口" if all_dom else "切换为国产",
            lambda r=rows, target=not all_dom: self.change_domestic(r, target),
        )

        menu.addSeparator()
        menu.addAction("移除 (Delete)", self.remove_component)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def select_same_mpn(self, mpn):
        matching_ids = [
            c.identifier for c in self.project.components
            if c.bom.get("row", {}).get("Manufacturer Part Number", "").strip() == mpn
        ]
        if matching_ids:
            self.refresh(matching_ids, recalculate=False)

    def duplicate_components(self):
        rows = self.selected_rows()
        if not rows or not self.commit_pending_edits():
            return
        from uuid import uuid4
        used_refs = {
            ref.upper()
            for comp in self.project.components
            for ref in comp.bom.get("assigned_designators", [])
        }
        copies = []
        for row in rows:
            clone = copy.deepcopy(self.project.components[row])
            clone.identifier = str(uuid4())
            clone.name = f"{clone.name} (副本)"
            if clone.bom:
                clone.bom.pop("source", None)
                clone.bom.pop("sha256", None)
                if "assigned_designators" in clone.bom:
                    renamed = []
                    for ref in clone.bom["assigned_designators"]:
                        candidate = f"{ref}_copy"
                        seq = 2
                        while candidate.upper() in used_refs:
                            candidate = f"{ref}_copy{seq}"
                            seq += 1
                        used_refs.add(candidate.upper())
                        renamed.append(candidate)
                    clone.bom["assigned_designators"] = renamed
            copies.append(clone)
        insert_at = max(rows) + 1
        self.project.components[insert_at:insert_at] = copies
        ids = [c.identifier for c in copies]
        self.finish_configuration(ids, description=f"复制 {len(copies)} 项元器件")

    def quick_edit_parameter(self, row, key, value):
        if not 0 <= row < len(self.project.components) or not self.commit_pending_edits():
            return
        candidate = copy.deepcopy(self.project.components[row])
        candidate.parameters[key] = value
        try:
            candidate.validate()
        except (ValueError, OverflowError) as error:
            self.error(error)
            return
        self.project.components[row] = candidate
        self.finish_configuration([candidate.identifier], description=f"快捷修改 {key}")

    def import_target(self):
        if not self.project.components:
            return "current"
        prompt = QMessageBox(self)
        prompt.setWindowTitle("导入 BOM")
        prompt.setText("当前工程已有元器件或印制板。请选择本次导入方式。")
        replace = prompt.addButton("作为另一个工程", QMessageBox.ButtonRole.AcceptRole)
        append = prompt.addButton("追加到当前工程", QMessageBox.ButtonRole.ActionRole)
        prompt.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        prompt.setDefaultButton(append)
        prompt.exec()
        return "replace" if prompt.clickedButton() is replace else "current" if prompt.clickedButton() is append else None

    def import_bom(self, _checked=False, *, path=None):
        if not self.commit_pending_edits():
            return False
        if path is None:
            path, _ = QFileDialog.getOpenFileName(
                self, "导入 BOM", "", "BOM 表格 (*.xlsx *.xls *.csv *.tsv);;所有文件 (*)"
            )
            if not path:
                return False
            QApplication.processEvents()
        else:
            path = str(path)
        try:
            source = read_bom_sources(path)
            mode = self.import_target()
            if mode is None:
                return False
            if mode == "replace" and not self.confirm_discard(allow_invalid_draft=True):
                return False
            QApplication.processEvents()
            dialog = BomImportDialog(source, self)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return False
            QApplication.processEvents()
            imported = dialog.result_import
            candidate = Project(Path(path).stem) if mode == "replace" else copy.deepcopy(self.project)
            before_count = len(candidate.components)
            count = append_bom(candidate, imported)
            if candidate.name == "未命名工程":
                candidate.name = Path(path).stem

            new_indices = list(range(before_count, len(candidate.components)))
            wizard = BomPreMapDialog(
                candidate.components,
                new_indices,
                prediction_state=candidate.prediction_state,
                parent=self,
            )
            if wizard.has_suggestions() and wizard.exec() == QDialog.DialogCode.Accepted:
                wizard.apply_to(candidate.components)
            QApplication.processEvents()

            if mode == "replace":
                self.replace_project(candidate, dirty=True)
            else:
                self.project = candidate
                ids = [self.project.components[i].identifier for i in new_indices]
                self.refresh(ids)
                self._record_mutation(f"导入 BOM（{len(imported.rows)} 行物料）", ids)
            self.statusBar().showMessage(f"已导入 {len(imported.rows)} 行物料、{count} 个位号；展开分组可单独配置")
            return True
        except (ValueError, OSError, csv.Error) as error:
            self.error(error)
            return False

    def change_domestic(self, rows, checked):
        if not self.commit_pending_edits():
            self.refresh(recalculate=False)
            return
        rows = sorted(set(r for r in rows if 0 <= r < len(self.project.components)))
        if not rows:
            return
        replacements = []
        cleared = 0
        try:
            for row in rows:
                item = copy.deepcopy(self.project.components[row])
                cleared += int(item.set_domestic(checked))
                item.validate()
                replacements.append((row, item))
        except (ValueError, OverflowError) as error:
            self.error(error)
            self.refresh(recalculate=False)
            return
        for row, item in replacements:
            self.project.components[row] = item
        ids = [item.identifier for _, item in replacements]
        self.refresh(ids)
        self._record_mutation("切换国产/进口属性", ids)
        self.statusBar().showMessage("国产选项已更新" + (f"；{cleared} 项需重新选择适用模型和参数" if cleared else ""))

    def import_project_components(self):
        if not self.commit_pending_edits():
            return
        path, _ = QFileDialog.getOpenFileName(self, "从工程引入模块或元器件", "", SUPPLIER_FILTER)
        if not path:
            return
        QApplication.processEvents()
        try:
            source, source_sha256 = load_supplier_bundle(path)
            if source.prediction_state != self.project.prediction_state:
                raise ValueError("来源工程与当前工程的预计状态不同，请分别预计后在任务剖面中组合")
            dialog = ProjectComponentImportDialog(source, path, self, source_sha256=source_sha256)
            if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.result_components:
                return
            QApplication.processEvents()
            candidate = copy.deepcopy(self.project)
            candidate.components.extend(dialog.result_components)
            candidate.validate()
            self.project = candidate
            ids = [item.identifier for item in dialog.result_components]
            self.refresh(ids)
            self._record_mutation("从工程引入模块或元器件", ids)
            self.statusBar().showMessage(f"已引入 {len(dialog.result_components)} 项；按当前工程各工况独立匹配计算")
        except (ValueError, OSError) as error:
            self.error(error)

    def add_supplement(self, model, title):
        if not self.commit_pending_edits():
            return
        if self.project.prediction_state == "nonoperating":
            model += "（非工作详细）"
        count = sum(c.model == model for c in self.project.components)
        item = Component(f"{title} {count + 1}", model, 1, {})
        dialog = ComponentDialog(
            item,
            self,
            environment=self.view_context.environment,
            ambient_temperature_C=self.view_context.ambient_temperature_C,
            conditions=self.project.conditions,
            calculation_enabled=self.environment_editor.selected_condition.enabled,
            prediction_state=self.project.prediction_state,
        )
        dialog.setWindowTitle("添加" + title)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.project.components.append(dialog.result_component)
            self.finish_configuration([dialog.result_component.identifier], description="添加" + title)

    def add_board(self):
        self.add_supplement("国产印制板", "印制板")

    def add_solder(self):
        self.add_supplement("国产焊接点", "焊接点")

    def add_smt(self):
        self.add_supplement("国产SMT互连", "SMT互连板")

    def show_carrier_scope(self):
        QMessageBox.information(
            self,
            "封装、基片与载体的计入方式",
            "<p><b>印制板本体</b>：按 5.15.1 单独添加，填写实际板数、金属化孔数和层数。</p>"
            "<p><b>焊接点</b>：按 5.15.2 单独添加，数量为连接点数；整件失效率已包含的连接不重复添加。</p>"
            "<p><b>陶瓷、密封或气密封装</b>：是对应器件的封装属性。普通集成电路已经通过表 27 或 C.13 计入，不能另加一项封装失效率。</p>"
            "<p><b>混合集成电路</b>：按 5.1.3 或 C.2.2.3 整体计算，衬底、膜电阻、外贴元器件及工艺封装属于内部组成；请选择对应混合集成电路模型，并按规定条件提供内部微电路合计。</p>"
            "<p><b>陶瓷、金属基、柔性等基板的 SMT 互连</b>：材料属于表 312 的热膨胀参数；在板级 SMT 表中录入全部连接类别，按 5.15.3 自动取最大失效率。数量填写板数，不能按焊点数重复相加。</p>",
        )

    def persist_if_open(self):
        """Legacy hook retained for callers; writes .autosave.json without overwriting user save."""
        if self._committing_edits or self.environment_editor.temperature_errors:
            return
        self._write_autosave_if_needed()

    def finish_configuration(self, selected_ids, *, description="配置元器件"):
        self.refresh(selected_ids)
        self._record_mutation(description, selected_ids)
        if not self._last_autosave_ok:
            self.statusBar().showMessage("更改已生效，但自动恢复草稿写入失败（请尽快按 Ctrl+S 保存正式工程文件）")
        elif self.path is not None:
            self.statusBar().showMessage("更改已生效并写入临时恢复草稿（Ctrl+Z 可撤销；按 Ctrl+S 保存正式工程文件）")
        else:
            self.statusBar().showMessage("配置已写回列表并写入临时恢复草稿（Ctrl+Z 可撤销；尚未保存正式工程，请点击“保存”）")

    def add_component(self):
        if not self.commit_pending_edits():
            return
        dialog = ComponentDialog(
            parent=self,
            environment=self.view_context.environment,
            ambient_temperature_C=self.view_context.ambient_temperature_C,
            conditions=self.project.conditions,
            calculation_enabled=self.environment_editor.selected_condition.enabled,
            prediction_state=self.project.prediction_state,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.project.components.append(dialog.result_component)
            self.finish_configuration([dialog.result_component.identifier], description="添加元器件")

    def edit_component(self, index=None):
        if not self.commit_pending_edits():
            return
        if isinstance(index, QModelIndex) and index.isValid():
            key = COLUMNS[index.column()].key
            src = self.proxy.mapToSource(index)
            node = src.internalPointer() if src.isValid() else None
            if key == "origin" or (key == "index" and node is not None and node.group_size > 1):
                return
        rows = self.rows_for_index(index) if isinstance(index, QModelIndex) and index.isValid() else self.selected_rows()
        if not rows:
            return
        if len(rows) > 1:
            self.batch_configure(rows=rows)
            return
        row = rows[0]
        original_id = self.project.components[row].identifier
        component = self.project.components[row]
        dialog = (
            ModuleDialog(component, self)
            if component.model == "工程模块"
            else ComponentDialog(
                component,
                self,
                environment=self.view_context.environment,
                ambient_temperature_C=self.view_context.ambient_temperature_C,
                conditions=self.project.conditions,
                calculation_enabled=self.environment_editor.selected_condition.enabled,
                prediction_state=self.project.prediction_state,
            )
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            target = next(i for i, item in enumerate(self.project.components) if item.identifier == original_id)
            self.project.components[target] = dialog.result_component
            self.finish_configuration([original_id], description="编辑元器件参数")

    def batch_configure(self, _checked=False, *, rows=None):
        if not self.commit_pending_edits():
            return
        rows = self.selected_rows() if rows is None else rows
        if not rows:
            return
        if any(self.project.components[row].model == "工程模块" for row in rows):
            QMessageBox.information(self, "模块参数", "工程模块保留供应商内部参数，请单独打开模块查看或修改名称、数量。")
            return
        dialog = BatchDialog(
            [self.project.components[i] for i in rows],
            self.view_context.environment,
            self,
            ambient_temperature_C=self.view_context.ambient_temperature_C,
            calculation_enabled=self.environment_editor.selected_condition.enabled,
            prediction_state=self.project.prediction_state,
        )
        if dialog.exec() == QDialog.DialogCode.Accepted:
            for row, item in zip(rows, dialog.result_components, strict=True):
                self.project.components[row] = item
            self.finish_configuration(
                [item.identifier for item in dialog.result_components],
                description=f"批量配置 {len(rows)} 项元器件",
            )

    def remove_component(self):
        if not self.commit_pending_edits():
            return
        rows = self.selected_rows()
        if rows and QMessageBox.question(self, "移除元器件", f"移除所选的 {len(rows)} 项元器件？") == QMessageBox.StandardButton.Yes:
            for row in reversed(rows):
                del self.project.components[row]
            self.refresh([])
            self._record_mutation(f"移除 {len(rows)} 项元器件", [])

    def confirm_discard(self, *, allow_invalid_draft=False):
        valid = self.commit_pending_edits(quiet=allow_invalid_draft)
        if not valid:
            if not allow_invalid_draft:
                return False
            answer = QMessageBox.question(
                self,
                "工程尚未保存",
                "当前存在未保存更改或无效的环境温度输入，是否保存当前工程？",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Save,
            )
            if answer == QMessageBox.StandardButton.Save:
                return self.save()
            if answer == QMessageBox.StandardButton.Discard:
                self._autosave_timer.stop()
                self.environment_editor.discard_invalid_drafts()
                return True
            return False
        self._flush_autosave()
        if not self.dirty:
            return True
        answer = QMessageBox.question(
            self,
            "工程尚未保存",
            "是否保存当前工程的更改？",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        if answer == QMessageBox.StandardButton.Discard:
            self._autosave_timer.stop()
            return True
        return False

    def replace_project(
        self,
        project,
        path=None,
        revision=None,
        dirty=False,
        *,
        preserve_autosave_for=None,
        keep_untitled_autosave=False,
    ):
        if project is self.project and not self.commit_pending_edits():
            return False
        self._autosave_timer.stop()
        self._analysis_merge_timer.stop()
        self._analysis_merge_epoch += 1
        old_path = self.path
        self.environment_editor.discard_invalid_drafts()
        self._resetting_stack = True
        try:
            self.undo_stack.clear()
        finally:
            self._resetting_stack = False
        if old_path is not None:
            old_resolved = Path(old_path).resolve()
            preserve_resolved = (
                Path(preserve_autosave_for).resolve() if preserve_autosave_for is not None else None
            )
            new_resolved = Path(path).resolve() if path is not None else None
            if old_resolved != preserve_resolved and (
                new_resolved is None or new_resolved != old_resolved or not dirty
            ):
                self._clear_path_autosave_if_owned(old_path)
        if not keep_untitled_autosave and (path is not None or not dirty):
            self._clear_untitled_autosave()
        self.project, self.path, self.revision = project, path, revision
        self._base_dirty = dirty
        self.dirty = dirty
        self._last_autosave_ok = True
        self._committed_snapshot = copy.deepcopy(project)
        self._committed_selected_ids = []
        self._refreshing = True
        try:
            if hasattr(self, "analysis_page"):
                self.analysis_page.clear_condition_sources()
                self.analysis_page.restore_state(project.analysis)
        finally:
            self._refreshing = False
        self._reset_filters()
        self.refresh([])
        self._committed_snapshot = copy.deepcopy(self.project)
        if dirty and path is None and not keep_untitled_autosave:
            self._write_autosave_if_needed()
        self.navigate(0)
        return True

    def new_project(self):
        if not self.confirm_discard(allow_invalid_draft=True):
            return False
        self.replace_project(Project())
        self.statusBar().showMessage("已新建空工程，可导入 BOM 或添加元器件")
        return True

    def clear_project(self, _checked=False, *, confirm=True):
        """Clear all components, operating conditions, notes, and analyses (undoable via Ctrl+Z)."""
        valid = self.commit_pending_edits(quiet=True)
        if not valid:
            if confirm:
                if not self.confirm_discard(allow_invalid_draft=True):
                    return False
            else:
                self.environment_editor.discard_invalid_drafts()
        already_empty = (
            not self.project.components
            and not self.project.notes
            and not self.project.analysis
            and len(self.project.conditions) == 1
            and self.project.conditions[0].environment == "GB"
            and self.project.conditions[0].ambient_temperature_C == 25
            and self.project.conditions[0].enabled
        )
        if already_empty:
            self.statusBar().showMessage("当前工程已为空")
            return False
        if confirm:
            answer = QMessageBox.question(
                self,
                "清空工程",
                "确定清空当前工程中的全部元器件、环境工况与分析配置吗？（清空后可按 Ctrl+Z 撤销）",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        self._autosave_timer.stop()
        fresh = Project()
        self.project.components = []
        self.project.conditions = fresh.conditions
        self.project.analysis = {}
        self.project.notes = ""
        self._refreshing = True
        try:
            if hasattr(self, "analysis_page"):
                self.analysis_page.clear_condition_sources()
                self.analysis_page.restore_state(self.project.analysis)
        finally:
            self._refreshing = False
        self._reset_filters()
        self.refresh([])
        self._record_mutation("清空工程内容", [])
        self.statusBar().showMessage("已清空当前工程内容（Ctrl+Z 可撤销）")
        return True

    def open(self):
        if not self.confirm_discard(allow_invalid_draft=True):
            return
        path, _ = QFileDialog.getOpenFileName(self, "打开工程", "", "JSON 工程 (*.json)")
        if path:
            self.open_path(path, prompt_autosave=True)

    def open_path(self, path, *, prompt_autosave=True):
        """Open a saved JSON project, offering recovery if a sidecar .autosave.json exists."""
        target = Path(path)
        try:
            project, revision = load_project(target)
            autosave = find_autosave(target) if prompt_autosave else None
            if autosave is not None:
                answer = QMessageBox.question(
                    self,
                    "发现未保存恢复草稿",
                    f"检测到“{target.name}”存在上次未保存的自动恢复草稿，是否从恢复草稿继续编辑？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
                    QMessageBox.StandardButton.Yes,
                )
                if answer == QMessageBox.StandardButton.Cancel:
                    return False
                if answer == QMessageBox.StandardButton.Yes:
                    try:
                        recovered, autosave_rev = load_project(autosave)
                    except (ValueError, OSError, sqlite3.Error) as error:
                        QMessageBox.warning(
                            self,
                            "恢复草稿已损坏",
                            f"无法读取自动恢复草稿（{error}），将回退打开已保存的正式工程文件。",
                        )
                    else:
                        if autosave_matches_target(target, revision, autosave_rev=autosave_rev):
                            self.replace_project(
                                recovered, target, revision, dirty=True, preserve_autosave_for=target
                            )
                            self.statusBar().showMessage("已恢复未保存草稿；确认无误后按 Ctrl+S 保存工程")
                        else:
                            QMessageBox.warning(
                                self,
                                "原工程文件已在外部更新",
                                f"“{target.name}”在自动恢复草稿生成后已被外部修改。"
                                "为避免覆盖更新后的正式文件，恢复草稿已作为未命名副本载入，请检查后“另存为”保存。",
                            )
                            self.replace_project(
                                recovered,
                                path=None,
                                revision=None,
                                dirty=True,
                                preserve_autosave_for=target,
                            )
                            self.statusBar().showMessage(
                                "原工程文件已在外部更新；恢复草稿已转为未命名副本，请使用“另存为”保存"
                            )
                        return True
                else:
                    self._clear_path_autosave_if_owned(target)
            self.replace_project(project, target, revision, dirty=False)
            return True
        except (ValueError, OSError, sqlite3.Error) as error:
            self.error(error)
            return False

    def save_to(self, path, revision=None):
        if not self.commit_pending_edits():
            return False
        self._autosave_timer.stop()
        self._sync_project_analysis()
        old_path = self.path
        value = save_project(path, self.project, revision)
        self.path, self.revision = Path(path), value
        self._base_dirty = False
        self.dirty = False
        self._last_autosave_ok = True
        self._committed_snapshot = copy.deepcopy(self.project)
        self.undo_stack.setClean()
        self._clear_path_autosave_if_owned(old_path)
        self._clear_path_autosave_if_owned(self.path)
        self._clear_untitled_autosave()
        self.update_title()
        return True

    def save(self):
        if self.path is None:
            return self.save_as()
        try:
            return self.save_to(self.path, self.revision)
        except (ValueError, OSError, sqlite3.Error) as error:
            self.error(error)
            return False

    def save_as(self):
        if not self.commit_pending_edits():
            return False
        path, _ = QFileDialog.getSaveFileName(
            self,
            "另存工程（请选择新文件名）",
            "预计工程.json",
            "JSON 工程 (*.json)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return False
        if not path.lower().endswith(".json"):
            path += ".json"
        try:
            revision = self.revision if self.path and Path(path).resolve() == self.path.resolve() else None
            return self.save_to(path, revision)
        except (ValueError, OSError, sqlite3.Error) as error:
            self.error(error)
            return False

    def export(self):
        if not self.commit_pending_edits():
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出计算数据",
            "可靠性预计.xlsx",
            "Excel 工作簿 (*.xlsx);;Excel 97–2003 工作簿 (*.xls)",
        )
        if not path:
            return
        suffix = Path(path).suffix.lower()
        if suffix not in (".xlsx", ".xls"):
            path += ".xls" if "*.xls)" in selected_filter and "*.xlsx" not in selected_filter else ".xlsx"
            suffix = Path(path).suffix.lower()
        try:
            from .workbook_io import export_workbook_xls, export_workbook_xlsx
            sheet_count = export_workbook_xls(path, self.project) if suffix == ".xls" else export_workbook_xlsx(path, self.project)
            self.statusBar().showMessage(f"已导出 {sheet_count} 个工作表：{path}")
        except (ValueError, OSError) as error:
            self.error(error)

    def export_redacted(self):
        if not self.commit_pending_edits():
            return
        path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出脱敏工程（请选择新文件名）",
            "GJB299D-001.xlsx",
            PUBLIC_FILTER,
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not path:
            return
        if Path(path).suffix.lower() not in (".json", ".xlsx", ".xls"):
            if "*.json" in selected_filter:
                path += ".json"
            elif "*.xls)" in selected_filter and "*.xlsx" not in selected_filter:
                path += ".xls"
            else:
                path += ".xlsx"
        try:
            export_redacted_file(path, self.project)
            self.statusBar().showMessage(f"已导出脱敏工程：{path}；匹配工况的失效率与原工程一致")
        except (ValueError, OSError) as error:
            self.error(error)

    def error(self, error):
        QMessageBox.warning(self, "操作未完成", str(error))

    def dragEnterEvent(self, event):
        paths = _extract_dropped_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        if len(paths) != 1:
            self.statusBar().showMessage("每次仅支持拖入单个文件")
            event.ignore()
            return
        target = paths[0]
        if not target.is_file():
            self.statusBar().showMessage("不支持拖入文件夹")
            event.ignore()
            return
        if target.suffix.lower() not in SUPPORTED_DROP_SUFFIXES:
            self.statusBar().showMessage("支持拖入单个 .json 工程或 .xlsx / .xls / .csv / .tsv 文件")
            event.ignore()
            return
        self.statusBar().showMessage(f"松开鼠标以打开：{target.name}")
        event.acceptProposedAction()

    def dragMoveEvent(self, event):
        paths = _extract_dropped_paths(event.mimeData())
        if len(paths) == 1 and paths[0].is_file() and paths[0].suffix.lower() in SUPPORTED_DROP_SUFFIXES:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        paths = _extract_dropped_paths(event.mimeData())
        if self.open_dropped_files(paths):
            event.acceptProposedAction()
        else:
            event.ignore()

    def open_dropped_files(self, paths):
        """Validate a single dropped file and route it to project open or BOM import."""
        if len(paths) != 1:
            self.error(ValueError("每次仅支持拖入单个文件"))
            return False
        target = Path(paths[0])
        if not target.is_file():
            self.error(ValueError("不支持拖入文件夹或不存在的文件"))
            return False
        suffix = target.suffix.lower()
        if suffix not in SUPPORTED_DROP_SUFFIXES:
            self.error(ValueError("仅支持拖入 .json 工程文件或 .xlsx / .xls / .csv / .tsv 表格文件"))
            return False
        if suffix == ".json":
            if not self.confirm_discard(allow_invalid_draft=True):
                return False
            return self.open_path(target, prompt_autosave=True)
        if suffix in (".xlsx", ".xls"):
            try:
                from .workbook_io import is_gjb299d_workbook
                is_workbook = is_gjb299d_workbook(target)
            except (ValueError, OSError) as error:
                self.error(error)
                return False
            if is_workbook:
                if not self.confirm_discard(allow_invalid_draft=True):
                    return False
                try:
                    project = load_supplier_project(target)
                    self.replace_project(project, path=None, revision=None, dirty=False)
                    self.statusBar().showMessage(
                        f"已从工作簿加载只读工况失效率快照：{target.name}"
                    )
                    return True
                except (ValueError, OSError) as error:
                    self.error(error)
                    return False
        return bool(self.import_bom(path=target))

    def closeEvent(self, event):
        if self.confirm_discard(allow_invalid_draft=True):
            self._autosave_timer.stop()
            self._clear_path_autosave_if_owned(self.path)
            self._clear_untitled_autosave()
            event.accept()
        else:
            event.ignore()
