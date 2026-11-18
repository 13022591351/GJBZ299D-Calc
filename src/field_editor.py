"""Shared editor construction and lossless drafts; batch change policy stays explicit."""

import math

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QComboBox, QLabel, QVBoxLayout, QWidget

from .model_spec import TEMP_CHOICE_KEYS, normalize_option
from .number_edit import NumberEdit
from .numeric import number_text
from .quality_labels import quality_option
from .stress_edit import StressComboBox, stress_input_row
from .theme import current_colors


def _find_combo_data(combo, target):
    for index in range(combo.count()):
        data = combo.itemData(index)
        if data is None:
            continue
        if isinstance(target, bool) or isinstance(data, bool):
            if type(target) is type(data) and target == data:
                return index
        elif target == data:
            return index
    return -1


def _choice_in(target, choices):
    for item in choices:
        val, _ = normalize_option(item)
        if isinstance(target, bool) or isinstance(val, bool):
            if type(target) is type(val) and target == val:
                return True
        elif target == val:
            return True
    return False


def _format_bounds(min_v, max_v, min_inc, max_inc):
    if min_v is not None and max_v is not None:
        if min_inc and max_inc:
            return f'{number_text(min_v)}～{number_text(max_v)}'
        left = f'{"≥" if min_inc else ">"} {number_text(min_v)}'
        right = f'{"≤" if max_inc else "<"} {number_text(max_v)}'
        return f'{left} 且 {right}'
    if min_v is not None:
        return f'{"≥" if min_inc else ">"} {number_text(min_v)}'
    if max_v is not None:
        return f'{"≤" if max_inc else "<"} {number_text(max_v)}'
    return ''


def _populate_choice_combo(widget, spec, model, empty_text, choices):
    widget.addItem(empty_text, None)
    is_quality = spec.key == 'quality' or spec.key.endswith('_quality')
    for value in choices:
        title, detail = (quality_option(model, value) if is_quality
                         else (number_text(value), spec.label))
        widget.addItem(title, value)
        widget.setItemData(widget.count() - 1, detail, Qt.ItemDataRole.ToolTipRole)


class CompositeComboBox(QComboBox):
    """Editable preset/table selector with inline constraint, range, and ceiling feedback."""

    def __init__(self, spec, empty_text='请选择…', parent=None):
        super().__init__(parent)
        self.spec = spec
        self.temp_label = spec.label
        self.empty_text = empty_text
        self._active_options = spec.composite_options()
        self._table_choices = spec.choices
        self._min_value = spec.min_value
        self._max_value = spec.max_value
        self._min_inclusive = spec.min_inclusive
        self._max_inclusive = spec.max_inclusive
        self._constraint_hint = spec.constraint_hint
        self._constraint_signature = None
        self._selecting_shortcut = False

        self.setEditable(True)
        self.setLineEdit(NumberEdit())
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(None)

        self.result_label = QLabel()
        self.result_label.setObjectName('Muted')
        self.result_label.setTextFormat(Qt.TextFormat.PlainText)
        self.result_label.setWordWrap(True)
        self.result_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        self._populate_items()
        self.setCurrentIndex(-1)
        self.lineEdit().clear()
        self._refresh_hints()

        self.lineEdit().textEdited.connect(self._on_text_edited)
        self.currentIndexChanged.connect(self._shortcut_selected)
        self.activated.connect(self._shortcut_selected)
        self.editTextChanged.connect(self.update_status)
        self.update_status()

    def _on_text_edited(self, _text):
        if self._selecting_shortcut:
            return
        if self.currentIndex() != -1:
            blocked = self.blockSignals(True)
            try:
                self.setCurrentIndex(-1)
            finally:
                self.blockSignals(blocked)

    def _populate_items(self):
        self.clear()
        self.addItem(self.empty_text, None)
        for item in self._active_options:
            value, custom_text = normalize_option(item)
            display = custom_text if custom_text is not None else number_text(value)
            self.addItem(display, value)
            if custom_text is not None:
                self.setItemData(self.count() - 1, custom_text, Qt.ItemDataRole.ToolTipRole)

    def _string_options(self):
        pool = list(self._active_options)
        if self._table_choices:
            pool.extend(self._table_choices)
        seen = []
        for item in pool:
            value, _ = normalize_option(item)
            if isinstance(value, str) and value not in seen:
                seen.append(value)
        return tuple(seen)

    def _selected_string_value(self):
        text = self.lineEdit().text().strip()
        if not text:
            return None
        if self.currentIndex() >= 0 and self.lineEdit().text() == self.itemText(self.currentIndex()):
            data = self.itemData(self.currentIndex())
            if isinstance(data, str):
                return data
        for option in self._string_options():
            if text == option:
                return option
        return None

    def _table_ticks(self):
        pool = self._table_choices if self._table_choices is not None else self._active_options
        nums, overflows = [], []
        for item in pool:
            val, _ = normalize_option(item)
            if isinstance(val, str):
                overflows.append(val)
            elif not isinstance(val, bool) and isinstance(val, (int, float)):
                nums.append(val)
        return sorted(dict.fromkeys(nums)), overflows

    def _table_range_text(self):
        if self.spec.choices is None:
            return ''
        nums, overflows = self._table_ticks()
        unit = ' ℃' if self.spec.key in TEMP_CHOICE_KEYS else ''
        if nums and overflows:
            return f'最高 {number_text(nums[-1])}{unit}，或 {"/".join(overflows)}'
        if nums:
            return f'最高 {number_text(nums[-1])}{unit}'
        return ''

    def _refresh_hints(self):
        bounds = _format_bounds(self._min_value, self._max_value, self._min_inclusive, self._max_inclusive)
        table_info = self._table_range_text()
        if self.empty_text == '请选择…':
            if self.spec.key in TEMP_CHOICE_KEYS and table_info:
                placeholder = f'请选择或输入温度（{table_info}）'
            elif bounds:
                placeholder = f'请选择或输入（{bounds}）'
            elif table_info:
                placeholder = f'请选择或输入（{table_info}）'
            else:
                placeholder = '请选择或输入数值'
        else:
            placeholder = self.empty_text
        self.lineEdit().setPlaceholderText(placeholder)

        parts = [self.spec.label]
        if self._constraint_hint:
            parts.append(self._constraint_hint)
        elif bounds:
            parts.append(f'有效范围：{bounds}')
        if self.spec.choices is not None and (self.spec.key in TEMP_CHOICE_KEYS or self.spec.ceil_choices):
            parts.append('可下拉选择标准档位，或直接输入数值；查表时自动向上取整至下一档。')
        self.lineEdit().setToolTip('：'.join(parts))

    def is_blank(self):
        text = self.lineEdit().text().strip()
        return not text or text == self.itemText(0)

    def _shortcut_selected(self, index):
        if index < 0 or self._selecting_shortcut:
            return
        self._selecting_shortcut = True
        try:
            data = self.itemData(index)
            if data is None:
                already_empty = self.lineEdit().text() == ''
                self.lineEdit().clear()
                blocked = self.blockSignals(True)
                try:
                    self.setCurrentIndex(-1)
                finally:
                    self.blockSignals(blocked)
                if already_empty:
                    return
            elif isinstance(data, str):
                if self.lineEdit().text() == data:
                    return
                self.lineEdit().setText(data)
            elif isinstance(data, (int, float)) and not isinstance(data, bool):
                if self.lineEdit().draft() == (number_text(data), data):
                    return
                self.lineEdit().set_number(data)
            self.update_status()
        finally:
            self._selecting_shortcut = False

    def set_number(self, value):
        self._selecting_shortcut = True
        blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(_find_combo_data(self, value) if value is not None else -1)
        finally:
            self.blockSignals(blocked)
        try:
            if value is None:
                self.lineEdit().clear()
            elif isinstance(value, str):
                self.lineEdit().setText(value)
            else:
                self.lineEdit().set_number(value)
            self.update_status()
        finally:
            self._selecting_shortcut = False

    def setEditText(self, text):
        blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(-1)
        finally:
            self.blockSignals(blocked)
        self.lineEdit().setText(text)
        self.update_status()

    def value(self):
        if self.is_blank():
            raise ValueError('请选择或填写' + self.temp_label)
        string_val = self._selected_string_value()
        if string_val is not None:
            return string_val
        if self.currentIndex() >= 0 and self.lineEdit().text() == self.itemText(self.currentIndex()):
            data = self.itemData(self.currentIndex())
            if isinstance(data, (int, float)) and not isinstance(data, bool):
                return data
        return self.lineEdit().value()

    def draft(self):
        return self.lineEdit().draft()

    def restore_draft(self, draft):
        blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(-1)
        finally:
            self.blockSignals(blocked)
        self.lineEdit().restore_draft(draft)
        self.update_status()

    def set_constraints(self, options, table_choices, min_v, max_v, min_inc, max_inc, hint):
        signature = (tuple(options), table_choices, min_v, max_v, min_inc, max_inc, hint)
        if signature == self._constraint_signature:
            return
        self._constraint_signature = signature
        self._active_options = tuple(options)
        self._table_choices = table_choices
        self._min_value = min_v
        self._max_value = max_v
        self._min_inclusive = min_inc
        self._max_inclusive = max_inc
        self._constraint_hint = hint
        saved_draft = self.lineEdit().draft()
        blocked = self.blockSignals(True)
        try:
            self._populate_items()
            text, orig = saved_draft
            match_val = orig if orig is not None else (text if text else None)
            matched_idx = _find_combo_data(self, match_val) if match_val is not None else -1
            self.setCurrentIndex(matched_idx)
            self.lineEdit().restore_draft(saved_draft)
            self._refresh_hints()
        finally:
            self.blockSignals(blocked)
        self.update_status()

    def _set_status(self, message, *, warning=False):
        self.result_label.setText(f'[{message}]')
        self.result_label.setStyleSheet(
            f"color:{current_colors()['warning']};" if warning else ''
        )

    def update_status(self, *_args):
        bounds = _format_bounds(self._min_value, self._max_value, self._min_inclusive, self._max_inclusive)
        hint = self._constraint_hint
        if self.is_blank():
            if hint and bounds and bounds not in hint:
                info = f'{bounds}；{hint}'
            elif hint:
                info = hint
            elif bounds:
                info = bounds
            elif self._active_options:
                samples = [
                    normalize_option(item)[1] or number_text(normalize_option(item)[0])
                    for item in self._active_options[:5]
                ]
                info = '、'.join(samples) + ('…' if len(self._active_options) > 5 else '')
            else:
                info = '输入有限数值'
            self._set_status(f'可选或输入：{info}')
            return

        string_val = self._selected_string_value()
        if string_val is not None:
            allowed_strings = {
                normalize_option(item)[0]
                for item in (self._table_choices if self._table_choices is not None else self._active_options)
                if isinstance(normalize_option(item)[0], str)
            }
            if string_val in allowed_strings:
                suffix = f'；{hint}' if hint else ''
                self._set_status(f'当前档位：{string_val}{suffix}')
            else:
                self._set_status(f'当前条件下不可选：{string_val}', warning=True)
            return

        try:
            if self.currentIndex() >= 0 and self.lineEdit().text() == self.itemText(self.currentIndex()):
                data = self.itemData(self.currentIndex())
                val = data if isinstance(data, (int, float)) and not isinstance(data, bool) else self.lineEdit().value()
            else:
                val = self.lineEdit().value()
            if isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                raise ValueError('请输入有限数值')
        except (ValueError, OverflowError):
            self._set_status('输入无效：请输入有限数值', warning=True)
            return

        if self.spec.integer and int(val) != val:
            self._set_status(f'输入无效：{self.spec.label}须为整数', warning=True)
            return
        if (
            self.spec.allow_negative
            and ('温度' in self.spec.label or self.spec.key.endswith('_temperature_C'))
            and val < -273.15
        ):
            self._set_status('输入无效：不得低于绝对零度（-273.15 ℃）', warning=True)
            return
        if not self.spec.allow_negative and val < 0:
            self._set_status('输入无效：须为' + ('正数' if self.spec.positive else '非负数'), warning=True)
            return

        if self._min_value is not None and (val < self._min_value if self._min_inclusive else val <= self._min_value):
            op = '≥' if self._min_inclusive else '>'
            suffix = f'（{hint}）' if hint else ''
            self._set_status(f'超出范围：须 {op} {number_text(self._min_value)}{suffix}', warning=True)
            return
        if self._max_value is not None and (val > self._max_value if self._max_inclusive else val >= self._max_value):
            op = '≤' if self._max_inclusive else '<'
            suffix = f'（{hint}）' if hint else ''
            self._set_status(f'超出范围：须 {op} {number_text(self._max_value)}{suffix}', warning=True)
            return
        if not self.spec.allow_negative and self.spec.positive and val == 0:
            self._set_status('输入无效：须为正数', warning=True)
            return

        if self.spec.choices is not None and (self.spec.key in TEMP_CHOICE_KEYS or self.spec.ceil_choices):
            nums, overflows = self._table_ticks()
            unit = ' ℃' if self.spec.key in TEMP_CHOICE_KEYS else ''
            if nums:
                if val <= nums[-1]:
                    ceiled = next(t for t in nums if t >= val)
                    suffix = f'；{hint}' if hint else ''
                    if ceiled == val:
                        self._set_status(f'当前 {number_text(val)}{unit}（表列档位 {number_text(ceiled)}{unit}）{suffix}')
                    else:
                        self._set_status(f'当前 {number_text(val)}{unit} → 向上取档 {number_text(ceiled)}{unit}{suffix}')
                    return
                if overflows:
                    overflow = overflows[0]
                    suffix = f'；{hint}' if hint else ''
                    self._set_status(f'当前 {number_text(val)}{unit} → 归入 {overflow} 档{suffix}')
                    return
                self._set_status(f'超出表列上限：最高 {number_text(nums[-1])}{unit}', warning=True)
                return

        if hint:
            self._set_status(f'当前取值 {number_text(val)}；{hint}')
        elif bounds:
            self._set_status(f'当前取值 {number_text(val)}；有效范围 {bounds}')
        else:
            self._set_status(f'当前取值 {number_text(val)}')


TemperatureComboBox = CompositeComboBox


def composite_input_row(field):
    """Pair an editable composite input with its inline constraint/ceiling hint."""
    row = QWidget()
    layout = QVBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    layout.addWidget(field)
    layout.addWidget(field.result_label)
    return row


def add_field_row(form, spec, field):
    """Give tabular inputs the full width and keep long scalar labels readable."""
    title = QLabel(spec.label.split(' = ', 1)[0].replace('（标准表取值）', ''))
    title.setWordWrap(True)
    if spec.record_fields:
        row_widget = QWidget()
        layout = QVBoxLayout(row_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(title)
        layout.addWidget(field)
        form.addRow(row_widget)
    else:
        title.setFixedWidth(200)
        row_widget = (
            stress_input_row(field) if isinstance(field, StressComboBox)
            else composite_input_row(field) if isinstance(field, CompositeComboBox)
            else field
        )
        form.addRow(title, row_widget)
    return row_widget


def create_field(spec, model, *, empty_text='请选择…', placeholder='请填写数值，支持科学计数法',
                 use_default=False):
    if spec.record_fields:
        from .record_editor import RecordTableEdit
        widget = RecordTableEdit(spec.record_fields, model=model, batch_mode=not use_default)
        if not use_default:
            widget.setPlaceholderText('未修改时保留各位号的连接类别；修改后整表应用于选中项。计算取板上最大失效率。')
    elif spec.stress_max is not None:
        widget = StressComboBox(spec, empty_text)
    elif spec.is_composite():
        widget = CompositeComboBox(spec, empty_text)
    elif spec.choices is not None:
        widget = QComboBox()
        _populate_choice_combo(widget, spec, model, empty_text, spec.choices)
        if spec.key == 'quality' or spec.key.endswith('_quality'):
            widget.currentIndexChanged.connect(lambda _index: widget.setToolTip(
                widget.currentData(Qt.ItemDataRole.ToolTipRole) or '请选择质量等级'))
    else:
        widget = NumberEdit()
        widget.setPlaceholderText(placeholder)
    if isinstance(widget, QComboBox):
        # Long standard descriptions remain available in the popup and tooltip;
        # their text must not force the entire parameter form beyond its viewport.
        widget.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        widget.setMinimumContentsLength(12)
    widget._field_spec = spec
    widget._field_model = model
    widget.setObjectName('input_' + spec.key)
    widget.setToolTip(spec.label)
    if use_default and spec.default is not None:
        set_field_value(widget, spec.default)
    return widget


def is_choice_combo(widget):
    return isinstance(widget, QComboBox) and not isinstance(widget, (StressComboBox, CompositeComboBox))


def is_field_blank(widget):
    if hasattr(widget, 'is_blank'):
        return widget.is_blank()
    if isinstance(widget, QComboBox):
        return widget.currentData() is None
    return not widget.text().strip()


def active_choices(fields):
    return {key: widget.currentData() for key, widget in fields.items()
            if is_choice_combo(widget) and widget.currentData() is not None}


def sync_field_constraints(fields, base_params=None):
    """Synchronize filtered choices, preset lists, and active limits across dependent fields."""
    context = dict(base_params or {})
    context.update(active_choices(fields))
    for key, widget in fields.items():
        if key not in context and isinstance(widget, CompositeComboBox) and not widget.is_blank():
            try:
                val = widget.value()
            except ValueError:
                val = None
            if val is not None:
                context[key] = val

    dropped_widgets = []
    for _pass in range(len(fields) + 1):
        changed_in_pass = False
        for widget in fields.values():
            spec = getattr(widget, '_field_spec', getattr(widget, 'spec', None))
            if spec is None or (not spec.choices_by and not spec.limits_by):
                continue
            if isinstance(widget, CompositeComboBox):
                resolved = spec.resolve_choices(context)
                options = resolved if resolved is not None else spec.composite_options(context)
                table_choices = resolved if (spec.choices is not None and resolved is not None) else spec.choices
                min_v, max_v, min_inc, max_inc, hint = spec.active_limits(context)
                widget.set_constraints(options, table_choices, min_v, max_v, min_inc, max_inc, hint)
            elif is_choice_combo(widget) and spec.choices is not None:
                resolved = spec.resolve_choices(context)
                target_choices = resolved if resolved is not None else spec.choices
                current_choices = tuple(widget.itemData(i) for i in range(1, widget.count()))
                if current_choices != target_choices:
                    prev_data = widget.currentData()
                    empty_label = widget.itemText(0) if widget.count() else '请选择…'
                    model = getattr(widget, '_field_model', '')
                    is_quality = spec.key == 'quality' or spec.key.endswith('_quality')
                    blocked = widget.blockSignals(True)
                    dropped = False
                    try:
                        widget.clear()
                        _populate_choice_combo(widget, spec, model, empty_label, target_choices)
                        if prev_data is not None:
                            new_index = _find_combo_data(widget, prev_data)
                            if new_index >= 0:
                                widget.setCurrentIndex(new_index)
                            else:
                                widget.setCurrentIndex(0)
                                dropped = True
                        else:
                            widget.setCurrentIndex(0)
                        if is_quality:
                            widget.setToolTip(widget.currentData(Qt.ItemDataRole.ToolTipRole) or '请选择质量等级')
                    finally:
                        widget.blockSignals(blocked)
                    if dropped:
                        context.pop(spec.key, None)
                        changed_in_pass = True
                        if widget not in dropped_widgets:
                            dropped_widgets.append(widget)
                if spec.key in context and not _choice_in(context[spec.key], target_choices):
                    context.pop(spec.key, None)
                    changed_in_pass = True
        if not changed_in_pass:
            break
    for widget in dropped_widgets:
        widget.currentIndexChanged.emit(0)


def read_field_value(spec, widget, *, allow_incomplete=False, empty_numeric_error=None):
    if spec.record_fields or isinstance(widget, StressComboBox):
        return widget.value(allow_incomplete=allow_incomplete)
    if is_choice_combo(widget):
        value = widget.currentData()
        if value is None:
            raise ValueError('请选择' + spec.label)
        return value
    try:
        return widget.value()
    except ValueError as error:
        raise ValueError(empty_numeric_error or ('请填写' + spec.label)) from error


def set_field_value(widget, value):
    if hasattr(widget, 'set_number'):
        widget.set_number(value)
    else:
        idx = _find_combo_data(widget, value) if value is not None else 0
        widget.setCurrentIndex(idx if idx >= 0 else 0)


def field_draft(widget):
    return widget.currentData() if is_choice_combo(widget) else widget.draft()


def restore_field_draft(widget, draft):
    if is_choice_combo(widget):
        idx = _find_combo_data(widget, draft) if draft is not None else 0
        widget.setCurrentIndex(idx if idx >= 0 else 0)
    else:
        widget.restore_draft(draft)


def connect_field_change(widget, callback):
    if isinstance(widget, (StressComboBox, CompositeComboBox)):
        widget.editTextChanged.connect(callback)
    elif isinstance(widget, QComboBox):
        widget.currentIndexChanged.connect(callback)
    else:
        widget.textChanged.connect(callback)
