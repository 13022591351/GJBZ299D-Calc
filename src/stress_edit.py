"""Preserve typed stress expressions while displaying their value and table selection."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from .number_edit import NumberEdit
from .numeric import number_text
from .stress import MissingStressVariable, ceil_to_grid, validate_stress
from .theme import current_colors


class StressComboBox(QComboBox):
    def __init__(self, spec, empty_text='请选择或输入…', parent=None):
        super().__init__(parent)
        self.stress_grid = spec.choices
        self.stress_max = spec.stress_max
        self.stress_label = spec.label
        self._selecting_shortcut = False
        self.variables_provider = lambda: {}
        self.mixed_variables_provider = lambda: set()
        self.setEditable(True)
        self.setLineEdit(NumberEdit())
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(None)
        self.addItem(empty_text, None)
        for value in spec.choices:
            self.addItem(number_text(value), value)
        self.setCurrentIndex(-1)
        self.lineEdit().clear()
        placeholder = ('输入小数或表达式，如 28/100、U*I/P额定'
                       if empty_text in ('请选择…', '请选择或输入…') else empty_text)
        self.lineEdit().setPlaceholderText(placeholder)
        self.lineEdit().setToolTip('支持小数、科学计数法、变量、四则运算、幂及括号；按原输入内容保存。')
        self.result_label = QLabel()
        self.result_label.setObjectName('Muted')
        self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.lineEdit().textEdited.connect(self._on_text_edited)
        self.editTextChanged.connect(self.update_result)
        self.currentIndexChanged.connect(self.shortcut_selected)
        self.activated.connect(self.shortcut_selected)
        self.update_result()

    def _on_text_edited(self, _text):
        if self._selecting_shortcut:
            return
        if self.currentIndex() != -1:
            blocked = self.blockSignals(True)
            try:
                self.setCurrentIndex(-1)
            finally:
                self.blockSignals(blocked)

    def is_blank(self):
        return not self.currentText().strip() or self.currentText() == self.itemText(0)

    def shortcut_selected(self, index):
        if index < 0 or self._selecting_shortcut:
            return
        self._selecting_shortcut = True
        try:
            data = self.itemData(index)
            # A user-selected shortcut replaces a previously loaded high-precision number.
            if data is None:
                self.lineEdit().clear()
                blocked = self.blockSignals(True)
                try:
                    self.setCurrentIndex(-1)
                finally:
                    self.blockSignals(blocked)
            elif isinstance(data, (int, float)) and not isinstance(data, bool):
                self.lineEdit().set_number(data)
            else:
                self.lineEdit().setText(self.itemText(index))
            self.update_result()
        finally:
            self._selecting_shortcut = False

    def set_number(self, value):
        self._selecting_shortcut = True
        blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(self.findData(value) if not isinstance(value, str) else -1)
        finally:
            self.blockSignals(blocked)
        try:
            if isinstance(value, str):
                self.lineEdit().setText(value)
            elif value is None:
                self.lineEdit().clear()
            else:
                self.lineEdit().set_number(value)
            self.update_result()
        finally:
            self._selecting_shortcut = False

    def setEditText(self, text):
        blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(-1)
        finally:
            self.blockSignals(blocked)
        self.lineEdit().setText(text)
        self.update_result()

    def raw_value(self):
        text, original = self.lineEdit().draft()
        return original if original is not None else text

    def value(self, *, allow_incomplete=False):
        value = self.raw_value()
        try:
            validate_stress(value, self.stress_max, self.stress_label, self.variables_provider())
        except MissingStressVariable:
            if not allow_incomplete:
                raise
        return value

    def update_result(self, *_args):
        if self.is_blank():
            self.result_label.setText('[支持小数或算式，如 28/100]')
            self.result_label.setStyleSheet('')
            return
        try:
            exact = validate_stress(self.raw_value(), self.stress_max, self.stress_label, self.variables_provider())
            selected = ceil_to_grid(exact, self.stress_grid, self.stress_label)
        except MissingStressVariable as error:
            text = ('各元器件变量分别取值，详见逐项结果' if error.names & self.mixed_variables_provider() else
                    '变量待填写：' + '、'.join(sorted(error.names)))
            self.result_label.setText(f'[{text}]')
            self.result_label.setStyleSheet(f"color:{current_colors()['warning']};")
            return
        except (ValueError, OverflowError) as error:
            raw = str(self.raw_value()).replace('（', '(').replace('）', ')')
            self.result_label.setText('[表达式未完成]' if raw.count('(') > raw.count(')') else f'[输入无效：{error}]')
            self.result_label.setStyleSheet(f"color:{current_colors()['warning']};")
            return
        self.result_label.setText(f'[比值 {number_text(float(exact))}，档位 {number_text(selected)}]')
        self.result_label.setStyleSheet('')

    def draft(self):
        return self.lineEdit().draft()

    def restore_draft(self, draft):
        blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(-1)
        finally:
            self.blockSignals(blocked)
        self.lineEdit().restore_draft(draft)
        self.update_result()


def stress_input_row(field):
    """Pair an editable raw input with its read-only interpretation."""
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(field)
    layout.addWidget(field.result_label)
    return row
