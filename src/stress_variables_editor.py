"""Edit the raw values of variables referenced by the current stress expressions."""

import copy

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QLabel,
    QLineEdit,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .numeric import number_text
from .stress import expression_variables
from .table_layout import resizable_columns
from .theme import current_colors

MIXED_VARIABLE_TEXT = '多种值 · 分别保留'


class _StressVariableDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        placeholder = index.data(Qt.ItemDataRole.UserRole)
        if placeholder and not option.text:
            option.text = str(placeholder)
            muted = QColor(current_colors()['muted'])
            option.palette.setColor(QPalette.ColorRole.Text, muted)
            option.palette.setColor(QPalette.ColorRole.HighlightedText, muted)

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        if isinstance(editor, QLineEdit):
            placeholder = index.data(Qt.ItemDataRole.UserRole) or '输入数值或表达式；留空表示待填写'
            editor.setPlaceholderText(str(placeholder))
        return editor


class StressVariablesEditor(QWidget):
    changed = pyqtSignal(str)

    def __init__(self, definitions=None, *, mixed_names=(), initial_mixed_names=None, sources=(), parent=None):
        super().__init__(parent)
        self.definitions = copy.deepcopy(definitions or {})
        self.mixed_names = set(mixed_names or ())
        self.initial_mixed_names = set(
            self.mixed_names if initial_mixed_names is None else initial_mixed_names
        )
        for name in self.mixed_names:
            if self.definitions.get(name) == MIXED_VARIABLE_TEXT:
                self.definitions.pop(name, None)
        self.sources = sources
        self.names = []
        self.root_names = set()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(4)
        heading = QLabel('应力变量')
        heading.setObjectName('Section')
        layout.addWidget(heading)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(['变量', '数值或表达式'])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setWordWrap(False)
        self.table.setAlternatingRowColors(True)
        self.table.setItemDelegateForColumn(1, _StressVariableDelegate(self.table))
        resizable_columns(self.table, 'stress-variables', [160, 420], min_widths=[120, 180])
        self.table.itemChanged.connect(self.item_changed)
        layout.addWidget(self.table)
        self.setVisible(False)

    def draft(self):
        return copy.deepcopy(self.definitions), set(self.mixed_names)

    def values(self):
        return {name: self.definitions.get(name, '') for name in self.names if name not in self.mixed_names}

    def update_references(self, expressions):
        roots, incomplete = set(), False
        for expression in expressions:
            if expression is None or isinstance(expression, str) and not expression.strip():
                continue
            try:
                roots.update(expression_variables(expression))
            except ValueError:
                incomplete = True
        if incomplete:
            roots.update(self.root_names)
        else:
            self.root_names = roots.copy()
        names, pending = set(), list(roots)
        while pending:
            name = pending.pop()
            if name in names:
                continue
            names.add(name)
            definitions = ([source.get(name, '') for source in self.sources] if name in self.mixed_names else
                           [self.definitions.get(name, '')])
            for definition in definitions:
                if definition is None or isinstance(definition, str) and not definition.strip():
                    continue
                try:
                    pending.extend(expression_variables(definition) - names)
                except ValueError:
                    continue
        ordered = sorted(names)
        if ordered != self.names:
            self.names = ordered
            self.rebuild()
        self.setVisible(bool(self.names))

    def rebuild(self):
        blocked = self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(self.names))
            for row, name in enumerate(self.names):
                key = QTableWidgetItem(name)
                key.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                self.table.setItem(row, 0, key)
                value = '' if name in self.mixed_names else number_text(self.definitions.get(name, ''))
                item = QTableWidgetItem(value)
                if name in self.initial_mixed_names:
                    item.setData(Qt.ItemDataRole.UserRole, MIXED_VARIABLE_TEXT)
                    item.setToolTip('各位号当前取值不同；留空分别保留原值，填写后统一覆盖（可引用其他变量）。')
                else:
                    item.setToolTip('按原文保存；可引用其他变量。留空表示待填写。')
                self.table.setItem(row, 1, item)
        finally:
            self.table.blockSignals(blocked)
        height = self.table.horizontalHeader().sizeHint().height() + min(5, len(self.names)) * 38 + 4
        self.table.setFixedHeight(height)

    def item_changed(self, item):
        if item.column() != 1 or not 0 <= item.row() < len(self.names):
            return
        name = self.names[item.row()]
        text = item.text()
        if name in self.initial_mixed_names and (not text.strip() or text.strip() == MIXED_VARIABLE_TEXT):
            self.definitions.pop(name, None)
            self.mixed_names.add(name)
            if text != '':
                blocked = self.table.blockSignals(True)
                try:
                    item.setText('')
                finally:
                    self.table.blockSignals(blocked)
        else:
            self.definitions[name] = text
            self.mixed_names.discard(name)
        self.changed.emit(name)
