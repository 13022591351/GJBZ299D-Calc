"""Editable component records with the same lossless interface as scalar inputs."""

import copy

from PyQt6.QtCore import QEvent, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from .table_layout import configure_table_combo, resizable_columns


class RecordTableEdit(QWidget):
    textChanged = pyqtSignal(str)

    def __init__(self, fields, model='', *, batch_mode=False, parent=None):
        super().__init__(parent)
        self.fields = tuple(fields)
        self.model = model
        self._loading = False
        self._modified = False
        self._batch_mode = bool(batch_mode)
        self._last_active_row = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        controls = QHBoxLayout()
        self.hint = QLabel('填写板上全部连接类别；计算取最大失效率。数量按电路板计。')
        self.hint.setWordWrap(True)
        controls.addWidget(self.hint, 1)
        self.add_button = QPushButton('添加连接类别')
        self.add_button.clicked.connect(lambda: self.add_record())
        controls.addWidget(self.add_button)
        self.remove_button = QPushButton('移除所选类别')
        self.remove_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.remove_button.clicked.connect(self.remove_records)
        controls.addWidget(self.remove_button)
        layout.addLayout(controls)
        self.table = QTableWidget(0, len(self.fields))
        self.table.setHorizontalHeaderLabels([field.label for field in self.fields])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setMinimumHeight(190)
        resizable_columns(
            self.table,
            'record-' + '-'.join(field.key for field in self.fields),
            [210 if field.choices else 160 for field in self.fields],
            min_widths=[135 if field.choices else 100 for field in self.fields],
            auto_fit=(len(self.fields) <= 5),
        )
        layout.addWidget(self.table)

    def text(self):
        return f'{self.table.rowCount()} 类连接' if self.table.rowCount() else ''

    def is_blank(self):
        if self._batch_mode:
            if self._modified:
                return False
            return self.table.rowCount() == 0
        if self.table.rowCount() == 0:
            return True
        from .field_editor import is_choice_combo, is_field_blank

        for row in range(self.table.rowCount()):
            context = self._raw_context(row)
            for col, field in enumerate(self.fields):
                if not field.active(context):
                    continue
                widget = self.table.cellWidget(row, col)
                if is_choice_combo(widget):
                    if widget.currentData() is not None:
                        return False
                elif widget is not None and not is_field_blank(widget):
                    return False
        return True

    def clear_to_untouched(self):
        loading, self._loading = self._loading, True
        try:
            self.table.setRowCount(0)
        finally:
            self._loading = loading
        self._modified = False
        self._last_active_row = None

    def setPlaceholderText(self, text):
        self.hint.setText(text)

    def _row_for_widget(self, widget):
        viewport = self.table.viewport()
        cur = widget
        while cur is not None and cur is not self.table and cur is not viewport:
            parent = cur.parentWidget()
            if parent is viewport:
                for r in range(self.table.rowCount()):
                    for c in range(self.table.columnCount()):
                        if self.table.cellWidget(r, c) is cur:
                            return r
                idx = self.table.indexAt(cur.pos())
                return idx.row() if idx.isValid() else -1
            cur = parent
        return -1

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.FocusIn):
            row = self._row_for_widget(obj)
            if 0 <= row < self.table.rowCount():
                self._last_active_row = row
                if not self.table.selectionModel().selectedRows():
                    self.table.selectRow(row)
        return super().eventFilter(obj, event)

    def _widget(self, row, key):
        column = next(i for i, field in enumerate(self.fields) if field.key == key)
        return self.table.cellWidget(row, column)

    def _raw_context(self, row):
        from .field_editor import is_choice_combo, is_field_blank

        context = {}
        for col, field in enumerate(self.fields):
            widget = self.table.cellWidget(row, col)
            if is_choice_combo(widget):
                context[field.key] = widget.currentData()
            elif widget is not None:
                if is_field_blank(widget):
                    context[field.key] = None
                else:
                    try:
                        context[field.key] = widget.value()
                    except (TypeError, ValueError, OverflowError):
                        context[field.key] = None
        return context

    def _sync_rows(self):
        from .field_editor import sync_field_constraints

        loading, self._loading = self._loading, True
        try:
            for row in range(self.table.rowCount()):
                row_widgets = {
                    field.key: self.table.cellWidget(row, col)
                    for col, field in enumerate(self.fields)
                    if self.table.cellWidget(row, col) is not None
                }
                sync_field_constraints(row_widgets)
                context = self._raw_context(row)
                for col, field in enumerate(self.fields):
                    widget = self.table.cellWidget(row, col)
                    if widget is not None:
                        widget.setEnabled(field.active(context))
        finally:
            self._loading = loading

    def _changed(self, *_, mark_modified=True):
        if self._loading:
            return
        if mark_modified:
            self._modified = True
        self._sync_rows()
        self.textChanged.emit(self.text())

    def add_record(self, value=None):
        from .field_editor import connect_field_change, create_field, set_field_value

        value = value or {}
        if not isinstance(value, dict) or set(value) - {field.key for field in self.fields}:
            raise ValueError('连接类别必须为字段名称与数值组成的记录')
        row = self.table.rowCount()
        self.table.insertRow(row)
        loading, self._loading = self._loading, True
        try:
            for col, field in enumerate(self.fields):
                widget = create_field(field, self.model, use_default=True)
                if isinstance(widget, QComboBox):
                    configure_table_combo(widget, min_chars=5)
                widget.installEventFilter(self)
                if isinstance(widget, QComboBox) and widget.lineEdit() is not None:
                    widget.lineEdit().installEventFilter(self)
                self.table.setCellWidget(row, col, widget)
                if field.key in value:
                    set_field_value(widget, value[field.key])
                connect_field_change(widget, self._changed)
        finally:
            self._loading = loading
        if not self._loading:
            self._changed(mark_modified=True)

    def remove_records(self):
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()}, reverse=True)
        if not rows:
            for candidate in (self.table.focusWidget(), QApplication.focusWidget()):
                if candidate is not None:
                    focused_row = self._row_for_widget(candidate)
                    if 0 <= focused_row < self.table.rowCount():
                        rows = [focused_row]
                        break
        if not rows and self._last_active_row is not None and 0 <= self._last_active_row < self.table.rowCount():
            rows = [self._last_active_row]
        if not rows:
            return
        for row in rows:
            self.table.removeRow(row)
        self._last_active_row = None
        if not self._loading:
            self._modified = True
            self._changed(mark_modified=False)

    remove_selected = remove_records

    def value(self, *, allow_incomplete=False):
        from .field_editor import is_choice_combo, is_field_blank

        records = []
        for row in range(self.table.rowCount()):
            context, values = self._raw_context(row), {}
            row_has_user_input = False
            for col, field in enumerate(self.fields):
                if not field.active(context):
                    continue
                widget = self.table.cellWidget(row, col)
                if is_choice_combo(widget):
                    value = widget.currentData()
                    if value is None:
                        if allow_incomplete:
                            continue
                        raise ValueError(f'第 {row + 1} 类连接：请选择{field.label}')
                    row_has_user_input = True
                else:
                    if is_field_blank(widget):
                        if field.default is not None:
                            value = field.default
                        elif allow_incomplete:
                            continue
                        else:
                            raise ValueError(f'第 {row + 1} 类连接：待填写{field.label}')
                    else:
                        row_has_user_input = True
                        try:
                            value = (
                                widget.value(allow_incomplete=allow_incomplete)
                                if field.stress_max is not None
                                else widget.value()
                            )
                        except (TypeError, ValueError, OverflowError) as error:
                            raise ValueError(f'第 {row + 1} 类连接：{field.label}数值无效') from error
                values[field.key] = value
            if allow_incomplete and (not values or not row_has_user_input):
                continue
            records.append(values)
        return records

    def set_number(self, value):
        if not isinstance(value, list):
            raise ValueError('连接类别须为记录列表')
        loading, self._loading = self._loading, True
        try:
            self.table.setRowCount(0)
            for record in value:
                self.add_record(copy.deepcopy(record))
        finally:
            self._loading = loading
        self._modified = False
        self._last_active_row = None
        self._sync_rows()
        self.textChanged.emit(self.text())

    def draft(self):
        from .field_editor import field_draft

        return {
            'rows': copy.deepcopy([
                {field.key: field_draft(self.table.cellWidget(row, col))
                 for col, field in enumerate(self.fields)}
                for row in range(self.table.rowCount())
            ]),
            'modified': self._modified,
        }

    def restore_draft(self, draft):
        from .field_editor import restore_field_draft

        if isinstance(draft, dict) and 'rows' in draft:
            rows = draft['rows']
            modified = bool(draft.get('modified', False))
        else:
            rows = draft
            modified = False
        loading, self._loading = self._loading, True
        try:
            self.table.setRowCount(0)
            for record in copy.deepcopy(rows):
                self.add_record()
                row = self.table.rowCount() - 1
                for col, field in enumerate(self.fields):
                    if field.key in record:
                        restore_field_draft(self.table.cellWidget(row, col), record[field.key])
        finally:
            self._loading = loading
        self._modified = modified
        self._last_active_row = None
        self._sync_rows()
        self.textChanged.emit(self.text())
