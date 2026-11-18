"""Selectable operating conditions and independently computed, read-only results."""

from PyQt6.QtCore import QModelIndex, QPersistentModelIndex, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .analysis_methods.workspace import is_component_config_error
from .dialogs import label
from .domain import OperatingCondition, quantity_text, validate_temperature
from .numeric import number_text
from .standards import ENVIRONMENTS, conditions_text, environment_text
from .table_layout import configure_table_combo, resizable_columns
from .theme import THEME_BUS, current_colors

ENV_COLUMNS = {'enabled': 0, 'name': 1, 'environment': 2, 'temperature': 3,
               'status': 4, 'subtotal': 5, 'total': 6, 'mtbf': 7}


class ConditionNameDelegate(QStyledItemDelegate):
    """Keep the active name editor available to explicit save/leave actions."""
    draft_changed = pyqtSignal()

    def __init__(self, table):
        super().__init__(table)
        self.table = table
        self.editor = None
        self.index = QPersistentModelIndex()

    def createEditor(self, parent, option, index):
        editor = super().createEditor(parent, option, index)
        self.editor, self.index = editor, QPersistentModelIndex(index)
        editor.textEdited.connect(lambda _text: self.draft_changed.emit())
        return editor

    def setModelData(self, editor, model, index):
        if self.editor is editor:
            self.editor, self.index = None, QPersistentModelIndex()
        super().setModelData(editor, model, index)
        self.draft_changed.emit()

    def destroyEditor(self, editor, index):
        if self.editor is editor:
            self.editor, self.index = None, QPersistentModelIndex()
        super().destroyEditor(editor, index)
        self.draft_changed.emit()

    def has_pending_edit(self):
        return (self.editor is not None and self.index.isValid()
                and self.editor.text() != self.index.data(Qt.ItemDataRole.EditRole))

    def commit_pending(self):
        if self.editor is not None and self.index.isValid():
            editor, index = self.editor, QModelIndex(self.index)
            self.setModelData(editor, self.table.model(), index)
            self.table.closeEditor(editor, QAbstractItemDelegate.EndEditHint.NoHint)


class EnvironmentEditor(QFrame):
    changed = pyqtSignal()
    draft_changed = pyqtSignal()
    view_changed = pyqtSignal(str)

    def __init__(self, project, parent=None):
        super().__init__(parent)
        self.setObjectName('Card')
        self.project = project
        self.snapshot = None
        self._row_ids = None
        self.selected_condition_id = None
        self.checkboxes = {}
        self.boxes = {}
        self.temperature_errors = {}
        self.temperature_drafts = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(5)
        self.collapse_button = QPushButton('▾ 预计环境与工况结果')
        self.collapse_button.setObjectName('Subtle')
        self.collapse_button.setCheckable(True)
        self.collapse_button.setChecked(True)
        self.collapse_button.toggled.connect(self._toggle_body)
        header.addWidget(self.collapse_button)

        self.count_label = label('', wrap=False)
        header.addWidget(self.count_label, 1)

        self.add_button = QPushButton('＋ 添加环境')
        self.add_button.clicked.connect(self.add_condition)
        header.addWidget(self.add_button)
        self.remove_button = QPushButton('－ 移除环境')
        self.remove_button.clicked.connect(self.remove_condition)
        header.addWidget(self.remove_button)

        divider = QFrame()
        divider.setObjectName('VLine')
        divider.setFrameShape(QFrame.Shape.VLine)
        header.addWidget(divider)

        self.calculate_button = QPushButton('计算工程')
        self.calculate_button.setObjectName('Primary')
        self.calculate_button.setMinimumWidth(92)
        header.addWidget(self.calculate_button)
        layout.addLayout(header)

        self.body_widget = QWidget()
        body_layout = QVBoxLayout(self.body_widget)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(4)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels([
            '计算', '工况名称', '环境类别', '环境温度 Ta（℃）', '计算状态',
            '已配置小计（10⁻⁶/h）', '失效率合计（10⁻⁶/h）', 'MTBF（h）'])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.verticalHeader().hide()
        self.name_delegate = ConditionNameDelegate(self.table)
        self.name_delegate.draft_changed.connect(self.draft_changed)
        self.table.setItemDelegateForColumn(ENV_COLUMNS['name'], self.name_delegate)
        resizable_columns(
            self.table,
            'environments-results',
            [48, 165, 210, 145, 130, 170, 180, 140],
            min_widths=[44, 110, 150, 125, 105, 135, 145, 115],
            fixed_columns=(0,),
        )
        self._layout_timer = QTimer(self)
        self._layout_timer.setSingleShot(True)
        self._layout_timer.timeout.connect(self._refresh_layout)
        self.table.itemChanged.connect(self.name_changed)
        self.table.currentCellChanged.connect(self.selection_changed)
        self.table.horizontalScrollBar().rangeChanged.connect(self._schedule_layout_refresh)
        self.table.horizontalHeader().sectionResized.connect(self._schedule_layout_refresh)
        body_layout.addWidget(self.table)

        self.temperature_message = label('')
        self.temperature_message.setWordWrap(True)
        self.temperature_message.setStyleSheet(f"color:{current_colors()['warning']};")
        self.temperature_message.hide()
        body_layout.addWidget(self.temperature_message)
        layout.addWidget(self.body_widget)
        THEME_BUS.changed.connect(self._on_theme_changed)
        self.sync(project)

    def _on_theme_changed(self, *_args):
        self.temperature_message.setStyleSheet(f"color:{current_colors()['warning']};")
        for condition in self.project.conditions:
            if condition.identifier in self.boxes:
                self.style_temperature_error(
                    self.boxes[condition.identifier][1],
                    condition.identifier in self.temperature_errors,
                )
        self._apply_result_cell_colors()
        self.table.viewport().update()

    def _schedule_layout_refresh(self, *_args):
        self._layout_timer.start(0)

    def _refresh_layout(self):
        self.table.column_widths.fit_to_viewport()
        self.update_table_height()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_layout_refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_layout_refresh()

    def _toggle_body(self, expanded):
        self.body_widget.setVisible(expanded)
        self.collapse_button.setText(('▾ ' if expanded else '▸ ') + '预计环境与工况结果')
        self.update_selection_summary()

    def _condition_by_id(self, identifier):
        return next((c for c in self.project.conditions if c.identifier == identifier), None)

    @property
    def selected_condition(self):
        return self._condition_by_id(self.selected_condition_id)

    def sync(self, project, *, recalculate=True):
        if project is not self.project:
            self.temperature_errors.clear()
            self.temperature_drafts.clear()
        self.project = project
        identifiers = {c.identifier for c in project.conditions}
        self.temperature_errors = {key: value for key, value in self.temperature_errors.items() if key in identifiers}
        self.temperature_drafts = {key: value for key, value in self.temperature_drafts.items() if key in identifiers}
        row_ids = tuple(c.identifier for c in project.conditions)
        snapshot = (
            project.prediction_state,
            tuple(
                (c.identifier, c.name, c.environment, c.ambient_temperature_C, c.enabled)
                for c in project.conditions
            ),
        )
        rows_changed = row_ids != self._row_ids or self.table.rowCount() != len(project.conditions)
        state_changed = snapshot != self.snapshot
        previous = self.selected_condition_id
        selected = self._condition_by_id(previous)
        if selected is None:
            selected = next(
                (c for c in project.conditions if c.enabled),
                project.conditions[0] if project.conditions else None,
            )
        self.selected_condition_id = selected.identifier if selected else None
        blocked = self.table.blockSignals(True)
        try:
            if rows_changed:
                self._row_ids = row_ids
                self.snapshot = snapshot
                self.build_condition_rows()
            elif state_changed:
                self.snapshot = snapshot
                self.update_condition_rows_in_place()
            # Component edits require evaluation; selecting another result row does not.
            if recalculate or rows_changed or state_changed:
                self.refresh_results()
            if selected and (rows_changed or self.table.currentRow() != project.conditions.index(selected)):
                self.table.selectRow(project.conditions.index(selected))
        finally:
            self.table.blockSignals(blocked)
        self.remove_button.setEnabled(len(project.conditions) > 1)
        self.count_label.setText(f'预计项 {len(project.components)} · 数量 {quantity_text(project.components)}')
        self.count_label.setToolTip('勾选的工况分别计算并导出；选中行用于查看下方预计项明细。')
        self.update_selection_summary()
        self.table.column_widths.fit_to_viewport()
        self.update_table_height()
        self._schedule_layout_refresh()
        if previous != self.selected_condition_id and self.selected_condition_id:
            self.view_changed.emit(self.selected_condition_id)

    def update_selection_summary(self):
        condition = self.selected_condition
        base = f'预计项 {len(self.project.components)} · 数量 {quantity_text(self.project.components)}'
        if condition and not self.collapse_button.isChecked():
            ctx = getattr(self, 'results', {}).get(condition.identifier)
            has_error = bool(ctx and (ctx.error or any(
                is_component_config_error(c.model, ctx.evaluate(c), calculation_enabled=ctx.enabled)
                for c in self.project.components
            )))
            rate_str = (
                f'总 λ = {number_text(ctx.total_rate)} 10⁻⁶/h'
                if ctx and ctx.total_rate is not None
                else f'小计 = {number_text(ctx.known_rate)} 10⁻⁶/h（含配置异常）'
                if ctx and ctx.known_rate is not None and has_error
                else f'小计 = {number_text(ctx.known_rate)} 10⁻⁶/h'
                if ctx and ctx.known_rate is not None
                else '配置异常' if has_error else '待配置'
            )
            self.count_label.setText(f'{base}  ｜  当前：{condition.name}（{rate_str}）')
        else:
            self.count_label.setText(base)
        text = ('当前查看：' + condition.name + ' · ' +
                conditions_text(condition.environment, condition.ambient_temperature_C)
                if condition else '尚无工作环境')
        self.table.setToolTip(text)

    def selection_changed(self, row, _column, _previous_row, _previous_column):
        if not 0 <= row < len(self.project.conditions):
            return
        identifier = self.project.conditions[row].identifier
        if identifier != self.selected_condition_id:
            self.selected_condition_id = identifier
            self.update_selection_summary()
            self.view_changed.emit(identifier)

    def _sync_temperature_box(self, condition, temperature):
        cid = condition.identifier
        blocked = temperature.blockSignals(True)
        line_edit = temperature.lineEdit()
        line_blocked = line_edit.blockSignals(True) if line_edit is not None else False
        try:
            if condition.ambient_temperature_C is None:
                index = 0
            else:
                index = temperature.findData(condition.ambient_temperature_C)
                if index < 0:
                    temperature.addItem(
                        f'{number_text(condition.ambient_temperature_C)} ℃',
                        condition.ambient_temperature_C,
                    )
                    index = temperature.count() - 1
            committed_text = (
                '请选择温度…'
                if condition.ambient_temperature_C is None
                else f'{number_text(condition.ambient_temperature_C)} ℃'
            )
            if cid in self.temperature_drafts:
                draft_text = self.temperature_drafts[cid]
                temperature.committed_temperature_text = committed_text
                temperature.temperature_was_edited = True
                if temperature.currentText() != draft_text:
                    temperature.setEditText(draft_text)
                self.style_temperature_error(temperature, cid in self.temperature_errors)
            else:
                if temperature.currentIndex() != index:
                    temperature.setCurrentIndex(index)
                if temperature.currentText() != committed_text:
                    temperature.setEditText(committed_text)
                temperature.committed_temperature_text = committed_text
                temperature.temperature_was_edited = False
                self.style_temperature_error(temperature, False)
        finally:
            if line_edit is not None:
                line_edit.blockSignals(line_blocked)
            temperature.blockSignals(blocked)

    def update_condition_rows_in_place(self):
        for row, condition in enumerate(self.project.conditions):
            cid = condition.identifier
            checkbox = self.checkboxes.get(cid)
            if checkbox is not None and checkbox.isChecked() != condition.enabled:
                c_blocked = checkbox.blockSignals(True)
                try:
                    checkbox.setChecked(condition.enabled)
                finally:
                    checkbox.blockSignals(c_blocked)
            name_item = self.table.item(row, 1)
            if name_item is None:
                self.table.setItem(row, 1, QTableWidgetItem(condition.name))
            elif name_item.text() != condition.name:
                name_item.setText(condition.name)
            pair = self.boxes.get(cid)
            if pair is not None:
                environment, temperature = pair
                env_idx = environment.findData(condition.environment)
                if env_idx < 0:
                    env_idx = 0
                if environment.currentIndex() != env_idx:
                    e_blocked = environment.blockSignals(True)
                    try:
                        environment.setCurrentIndex(env_idx)
                    finally:
                        environment.blockSignals(e_blocked)
                self._sync_temperature_box(condition, temperature)

    def build_condition_rows(self):
        self.table.setRowCount(0)
        self.table.setRowCount(len(self.project.conditions))
        self.checkboxes = {}
        self.boxes = {}
        for row, condition in enumerate(self.project.conditions):
            cid = condition.identifier
            checkbox = QCheckBox()
            checkbox.setToolTip('勾选后计算并导出本工况；取消后不计算、不导出。')
            checkbox.setChecked(condition.enabled)
            checkbox.toggled.connect(
                lambda checked, cond_id=cid: (
                    (c := self._condition_by_id(cond_id)) is not None and self.set_enabled(c, checked)
                )
            )
            self.checkboxes[cid] = checkbox
            cell = QWidget()
            cell.setStyleSheet('background: transparent;')
            center = QHBoxLayout(cell)
            center.setContentsMargins(0, 0, 0, 0)
            center.setAlignment(Qt.AlignmentFlag.AlignCenter)
            center.addWidget(checkbox)
            self.table.setCellWidget(row, 0, cell)
            self.table.setItem(row, 1, QTableWidgetItem(condition.name))
            environment = QComboBox()
            environment.addItem('请选择环境类别…', None)
            for code in ENVIRONMENTS:
                environment.addItem(environment_text(code), code)
            env_idx = environment.findData(condition.environment)
            environment.setCurrentIndex(env_idx if env_idx >= 0 else 0)
            configure_table_combo(environment, min_chars=6)
            environment.currentIndexChanged.connect(
                lambda _index, cond_id=cid, box=environment: (
                    (c := self._condition_by_id(cond_id)) is not None
                    and self.set_environment(c, box.currentData())
                )
            )
            self.table.setCellWidget(row, 2, environment)
            temperature = QComboBox()
            temperature.setEditable(True)
            temperature.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
            temperature.setCompleter(None)
            temperature.setToolTip('可选择常用温度，或直接输入温度；支持小数、科学计数法和 ℃ 单位。')
            temperature.addItem('请选择温度…', None)
            for value in range(-55, 176, 5):
                temperature.addItem(f'{value} ℃', value)
            configure_table_combo(temperature, min_chars=4)
            self._sync_temperature_box(condition, temperature)
            temperature.lineEdit().textEdited.connect(
                lambda text, cond_id=cid, box=temperature: (
                    (c := self._condition_by_id(cond_id)) is not None
                    and self.temperature_input_changed(c, box, text)
                )
            )
            temperature.currentIndexChanged.connect(
                lambda index, cond_id=cid, box=temperature: (
                    (c := self._condition_by_id(cond_id)) is not None
                    and self.temperature_selected(c, box, index)
                )
            )
            temperature.lineEdit().editingFinished.connect(
                lambda cond_id=cid, box=temperature: (
                    (c := self._condition_by_id(cond_id)) is not None
                    and self.temperature_edited(c, box)
                )
            )
            self.table.setCellWidget(row, 3, temperature)
            self.boxes[cid] = environment, temperature

    def _apply_result_cell_colors(self):
        colors = current_colors()
        for row in range(self.table.rowCount()):
            for column in range(ENV_COLUMNS['status'], self.table.columnCount()):
                item = self.table.item(row, column)
                if item is None:
                    continue
                text = item.text()
                if column == ENV_COLUMNS['status']:
                    if text.startswith('已计算'):
                        item.setForeground(QColor(colors['success']))
                    elif text == '合计错误' or '无效' in text or '异常' in text:
                        item.setForeground(QColor(colors['error']))
                    elif text.startswith(('待配置', '请选择')):
                        item.setForeground(QColor(colors['warning']))
                    else:
                        item.setForeground(QColor(colors['muted']))
                else:
                    item.setForeground(QColor(colors['text']))

    def refresh_results(self):
        self.results = {condition.identifier: self.project.calculate(condition) for condition in self.project.conditions}
        errors = [f'{condition.name}：{self.temperature_errors[condition.identifier]}'
                  for condition in self.project.conditions if condition.identifier in self.temperature_errors]
        self.temperature_message.setText('\n'.join(errors))
        self.temperature_message.setVisible(bool(errors))
        for row, condition in enumerate(self.project.conditions):
            error_rows = []
            pending_rows = []
            error_count = 0
            aggregate_error = ''
            subtotal = total = mtbf = None
            complete = False
            valid_temperature = condition.ambient_temperature_C is not None
            if valid_temperature:
                try:
                    validate_temperature(condition.ambient_temperature_C)
                except ValueError:
                    valid_temperature = False
            if condition.identifier in self.temperature_errors:
                status = '温度输入无效' if condition.enabled else '未计算 · 温度输入无效'
            elif not condition.enabled:
                status = '未计算'
            elif not self.project.components:
                status = '暂无预计项'
            elif condition.environment is None and condition.ambient_temperature_C is None:
                status = '请选择环境和温度'
            elif condition.environment not in ENVIRONMENTS:
                status = '请选择环境类别'
            elif not valid_temperature:
                status = '请选择有效温度'
            else:
                context = self.results[condition.identifier]
                evaluations = [(component, context.evaluate(component)) for component in self.project.components]
                error_rows = [
                    (component, result) for component, result in evaluations
                    if is_component_config_error(component.model, result, calculation_enabled=True)
                ]
                pending_rows = [
                    (component, result) for component, result in evaluations
                    if result.rate is None and not is_component_config_error(
                        component.model, result, calculation_enabled=True
                    )
                ]
                error_count = len(error_rows)
                incomplete_count = len(pending_rows)
                subtotal, total, mtbf = context.known_rate, context.total_rate, context.mtbf_hours
                aggregate_error = context.error
                complete = total is not None
                if aggregate_error:
                    status = '合计错误'
                elif error_count and incomplete_count:
                    status = f'待配置 {incomplete_count} · 异常 {error_count}'
                elif error_count:
                    status = f'配置异常 {error_count} 项'
                elif incomplete_count:
                    status = f'待配置 {incomplete_count} 项'
                elif total == 0:
                    status = '已计算 · 失效率为 0'
                else:
                    status = '已计算'
            details = [f'{component.name}：{result.error}' for component, result in error_rows]
            details.extend(f'{component.name}：{result.error}' for component, result in pending_rows)
            if aggregate_error:
                details.insert(0, aggregate_error)
            elif complete and total > 0 and mtbf is None:
                details.append('MTBF 超出数值范围；工作失效率仍为有效计算结果。')
            tooltip = '\n'.join([status, *details[:10]])
            if condition.identifier in self.temperature_errors:
                tooltip += '\n' + self.temperature_errors[condition.identifier]
            if len(details) > 10:
                tooltip += f'\n另有 {len(details) - 10} 项异常或待配置，选中此工况行查看。'
            values = [status, subtotal, total, mtbf]
            for column, value in enumerate(values, ENV_COLUMNS['status']):
                if column == ENV_COLUMNS['status']:
                    text = status
                elif value is not None:
                    text = number_text(value, grouping=True)
                elif aggregate_error:
                    text = '无法计算' if column == ENV_COLUMNS['mtbf'] else '合计错误'
                elif column == ENV_COLUMNS['mtbf'] and complete:
                    text = '无有限值' if total == 0 else '超出数值范围'
                elif not condition.enabled or not self.project.components or column == ENV_COLUMNS['subtotal']:
                    text = '—'
                elif error_count:
                    text = '无法计算' if column == ENV_COLUMNS['mtbf'] else '配置异常'
                else:
                    text = '待配置'
                item = self.table.item(row, column)
                if item is None:
                    item = QTableWidgetItem()
                    item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    self.table.setItem(row, column, item)
                item.setText(text)
                item.setData(Qt.ItemDataRole.UserRole, value)
                item.setToolTip(
                    tooltip
                    if column in (ENV_COLUMNS['status'], ENV_COLUMNS['total']) or aggregate_error
                    else text
                )
                font = QFont()
                if column == ENV_COLUMNS['status'] and text.startswith('已计算'):
                    font.setBold(True)
                elif column == ENV_COLUMNS['total'] and total is not None:
                    font.setBold(True)
                item.setFont(font)
                if column > ENV_COLUMNS['status']:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._apply_result_cell_colors()

    def update_table_height(self, *_args):
        visible_rows = max(1, min(5, self.table.rowCount()))
        header_h = max(self.table.horizontalHeader().height(), self.table.horizontalHeader().sizeHint().height(), 36)
        height = header_h + visible_rows * 42 + 2 * self.table.frameWidth()
        bar = self.table.horizontalScrollBar()
        if bar.maximum() > bar.minimum() or bar.isVisible():
            height += bar.sizeHint().height()
        self.table.setFixedHeight(height)

    def set_enabled(self, condition, checked):
        if condition.enabled == checked:
            return
        condition.enabled = checked
        self.changed.emit()

    def set_environment(self, condition, value):
        if condition.environment == value:
            return
        condition.environment = value
        self.changed.emit()

    @staticmethod
    def style_temperature_error(box, invalid):
        box.setStyleSheet(f"QComboBox {{ border-color:{current_colors()['warning']}; }}" if invalid else '')

    def temperature_selected(self, condition, box, index):
        if index < 0 or self.boxes.get(condition.identifier, (None, None))[1] is not box:
            return
        self.temperature_errors.pop(condition.identifier, None)
        self.temperature_drafts.pop(condition.identifier, None)
        box.temperature_was_edited = False
        box.committed_temperature_text = box.currentText()
        self.style_temperature_error(box, False)
        self.set_temperature(condition, box.itemData(index), force=True)

    def temperature_input_changed(self, condition, box, text):
        box.temperature_was_edited = True
        self.temperature_drafts[condition.identifier] = text
        self.draft_changed.emit()

    def temperature_edited(self, condition, box):
        # A refresh replaces row editors; late focus signals from the old widget
        # must not overwrite the replacement row's committed temperature.
        if self.boxes.get(condition.identifier, (None, None))[1] is not box:
            return True
        original_text = box.currentText()
        if (not box.temperature_was_edited
                and original_text == box.committed_temperature_text
                and condition.identifier not in self.temperature_errors):
            return True
        text = original_text.strip()
        try:
            if not text or text == '请选择温度…':
                value = None
            else:
                value = float(text.removesuffix('℃').removesuffix('°C').strip().replace('−', '-'))
                validate_temperature(value)
        except (ValueError, OverflowError):
            had_no_prior_error = condition.identifier not in self.temperature_errors
            prev_draft = self.temperature_drafts.get(condition.identifier)
            self.temperature_errors[condition.identifier] = (
                '环境温度须为不低于 -273.15 ℃ 的有限数值；无效输入不参与计算。'
            )
            self.temperature_drafts[condition.identifier] = original_text
            self.style_temperature_error(box, True)
            if condition.ambient_temperature_C is not None or had_no_prior_error:
                self.set_temperature(condition, None, force=had_no_prior_error)
            elif prev_draft != original_text:
                self.draft_changed.emit()
            return False
        had_error = self.temperature_errors.pop(condition.identifier, None) is not None
        self.temperature_drafts.pop(condition.identifier, None)
        self.style_temperature_error(box, False)
        box.temperature_was_edited = False
        box.committed_temperature_text = '请选择温度…' if value is None else f'{number_text(value)} ℃'
        box.setEditText(box.committed_temperature_text)
        self.set_temperature(condition, value, force=had_error)
        self.draft_changed.emit()
        return True

    def discard_invalid_drafts(self):
        self.name_delegate.commit_pending()
        if not self.temperature_errors and not self.temperature_drafts:
            return
        affected = set(self.temperature_errors) | set(self.temperature_drafts)
        self.temperature_errors.clear()
        self.temperature_drafts.clear()
        for cid in affected:
            cond = self._condition_by_id(cid)
            pair = self.boxes.get(cid)
            if cond is not None and pair is not None:
                self._sync_temperature_box(cond, pair[1])
        self.temperature_message.clear()
        self.temperature_message.hide()
        self.draft_changed.emit()

    def has_pending_edits(self):
        return bool(self.temperature_drafts) or self.name_delegate.has_pending_edit()

    def commit_pending_edits(self):
        """Commit without relying on focus movement; caller batches change signals."""
        self.name_delegate.commit_pending()
        for condition in self.project.conditions:
            self.temperature_edited(condition, self.boxes[condition.identifier][1])
        return not self.temperature_errors

    def set_temperature(self, condition, value, *, force=False):
        if condition.ambient_temperature_C == value and not force:
            return
        condition.ambient_temperature_C = value
        self.changed.emit()

    def name_changed(self, item):
        if item.column() == 1:
            condition = self.project.conditions[item.row()]
            if condition.name == item.text():
                return
            condition.name = item.text()
            self.changed.emit()

    def add_condition(self):
        self.project.conditions.append(OperatingCondition(f'工况 {len(self.project.conditions) + 1}'))
        self.changed.emit()
        self.table.selectRow(len(self.project.conditions) - 1)

    def remove_condition(self):
        row = self.table.currentRow()
        if row < 0 or len(self.project.conditions) == 1:
            return
        self.project.conditions.pop(row)
        self.changed.emit()
