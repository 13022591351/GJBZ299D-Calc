"""Choose optional columns and exact-match grouping fields in a lined table."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .component_fields import COLUMNS
from .dialogs import label

FIXED_FIELDS = {'index', 'designators', 'quantity'}


class ViewDialog(QDialog):
    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setWindowTitle('显示字段与分组依据')
        self.resize(650, 640)
        self.visible_checks, self.group_checks = {}, {}
        self._initial_group_fields = list(project.group_fields)
        self._initial_visible_fields = list(project.visible_fields)
        self.group_fields = list(project.group_fields)
        self.visible_fields = list(project.visible_fields)
        layout = QVBoxLayout(self)
        layout.addWidget(label('字段与分组', 'Title'))
        layout.addWidget(label('勾选的分组字段全部相同才归为一组。其他字段不一致时显示“多种值”。'))
        columns = [column for column in COLUMNS if column.key not in FIXED_FIELDS]
        self.table = QTableWidget(len(columns), 3)
        self.table.setHorizontalHeaderLabels(['字段', '显示', '分组依据'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            self.table.setColumnWidth(column, 105)
        for row, column in enumerate(columns):
            item = QTableWidgetItem(column.label.replace('\n', ' '))
            item.setData(Qt.ItemDataRole.UserRole, column.key)
            self.table.setItem(row, 0, item)
            visible = self.add_check(row, 1, column.key in project.visible_fields)
            self.visible_checks[column.key] = visible
            if column.groupable:
                group = self.add_check(row, 2, column.key in project.group_fields)
                if column.key == 'origin':
                    hint = '按主表“国产”勾选状态区分。'
                    visible.setToolTip(hint)
                    group.setToolTip(hint)
                self.group_checks[column.key] = group
            else:
                unavailable = QTableWidgetItem('—')
                unavailable.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 2, unavailable)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox()
        buttons.addButton('应用', QDialogButtonBox.ButtonRole.AcceptRole)
        buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def add_check(self, row, column, checked):
        cell = QWidget()
        cell.setStyleSheet('background: transparent;')
        layout = QHBoxLayout(cell)
        layout.setContentsMargins(0, 0, 0, 0)
        check = QCheckBox()
        check.setChecked(checked)
        layout.addWidget(check, alignment=Qt.AlignmentFlag.AlignCenter)
        self.table.setCellWidget(row, column, cell)
        return check

    def apply(self):
        group_fields = [k for k, c in self.group_checks.items() if c.isChecked()]
        selected = FIXED_FIELDS | {k for k, c in self.visible_checks.items() if c.isChecked()}
        visible_fields = [column.key for column in COLUMNS if column.key in selected]
        if not group_fields:
            QMessageBox.warning(self, '请选择分组依据', '至少勾选一个分组字段；可在主界面关闭“分组显示”。')
            return
        self.group_fields = (
            list(self._initial_group_fields)
            if set(group_fields) == set(self._initial_group_fields)
            else group_fields
        )
        self.visible_fields = (
            list(self._initial_visible_fields)
            if visible_fields == self._initial_visible_fields
            else visible_fields
        )
        self.accept()
