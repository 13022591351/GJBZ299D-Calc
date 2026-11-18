"""Direct batch editing: changed fields apply, untouched values remain independent."""

import copy

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .analysis_methods.workspace import is_component_config_error
from .configuration import CALCULATION_DISABLED, configure_batch
from .dialogs import label
from .domain import quantity_text
from .field_editor import (
    _choice_in,
    active_choices,
    add_field_row,
    connect_field_change,
    create_field,
    field_draft,
    is_field_blank,
    read_field_value,
    restore_field_draft,
    set_field_value,
    sync_field_constraints,
)
from .input_source_dialog import InputSourceDialog, source_kind
from .model_picker import ModelSelector
from .model_spec import TEMP_CHOICE_KEYS, normalize_option
from .numeric import number_text
from .parameter_display import input_text
from .rate_editor import condition_key
from .standards import MODELS, Evaluation, conditions_text, stress_inputs
from .state_transfer import reusable_from_component
from .stress import expression_variables
from .stress_edit import StressComboBox
from .stress_variables_editor import StressVariablesEditor
from .table_layout import resizable_columns
from .theme import current_colors, fit_window
from .ui.bom_wizard import _extract_nominal, _is_parameter_value_column


def _prune_incompatible_parameters(item, overrides):
    model = item.model
    if model not in MODELS or model in ('待配置', '已知单件失效率', '工程模块'):
        return False
    specs = [f for f in MODELS[model]['fields'] if f.scope != 'project']
    defaults = {f.key: f.default for f in MODELS[model]['fields'] if f.default is not None}
    changed = False
    for _pass in range(len(specs) + 1):
        pass_changed = False
        active_keys = {f.key for f in specs if f.active(item.parameters)}
        for k in list(item.parameters):
            if k not in active_keys:
                item.parameters.pop(k, None)
                pass_changed = True
        for k in list(item.input_sources):
            if k not in active_keys:
                item.input_sources.pop(k, None)
                pass_changed = True
        context = defaults | item.parameters
        for spec in specs:
            if (
                spec.key not in item.parameters
                or spec.key in overrides
                or not spec.choices_by
                or spec.choices is None
            ):
                continue
            resolved = spec.resolve_choices(context)
            if resolved is None:
                continue
            val = item.parameters[spec.key]
            if not spec.ceil_choices and spec.key not in TEMP_CHOICE_KEYS:
                if not _choice_in(val, resolved):
                    item.parameters.pop(spec.key, None)
                    pass_changed = True
            elif isinstance(val, str):
                if not _choice_in(val, resolved):
                    item.parameters.pop(spec.key, None)
                    pass_changed = True
            elif isinstance(val, (int, float)) and not isinstance(val, bool):
                nums = [
                    normalize_option(c)[0]
                    for c in resolved
                    if not isinstance(normalize_option(c)[0], bool)
                    and isinstance(normalize_option(c)[0], (int, float))
                ]
                has_overflow = any(
                    isinstance(normalize_option(c)[0], str)
                    and any(op in normalize_option(c)[0] for op in ('＞', '>', '≥'))
                    for c in resolved
                )
                if nums and not has_overflow and val > max(nums):
                    item.parameters.pop(spec.key, None)
                    pass_changed = True
        if pass_changed:
            changed = True
        else:
            break
    if changed and stress_inputs(model):
        names, pending = set(), []
        for spec in stress_inputs(model):
            if spec.key in item.parameters and spec.active(item.parameters):
                pending.extend(expression_variables(item.parameters[spec.key]))
        while pending:
            name = pending.pop()
            if name in names:
                continue
            names.add(name)
            definition = item.stress_variables.get(name, '')
            if not (isinstance(definition, str) and not definition.strip()):
                pending.extend(expression_variables(definition) - names)
        item.stress_variables = {k: v for k, v in item.stress_variables.items() if k in names}
    return changed


class BatchDialog(QDialog):
    def __init__(self, components, environment, parent=None, *, ambient_temperature_C=None,
                 calculation_enabled=True, prediction_state='working', conditions=None):
        super().__init__(parent)
        self.components = components
        self.environment = environment
        self.ambient_temperature_C = ambient_temperature_C
        self.calculation_enabled = calculation_enabled
        self.prediction_state = prediction_state
        self.conditions = (
            conditions
            if conditions is not None
            else getattr(getattr(parent, 'project', None), 'conditions', None)
        )
        self.source_updates = {}
        self.result_components = None
        self.fields, self.drafts = {}, {}
        self.changed_keys = set()
        self.variable_changed_keys = set()
        self.variable_drafts = {}
        self._variables_height_adjusted = False
        self.variables_editor = None
        self.active_model = None
        self._syncing_constraints = False
        self._base_params = {}
        self._error_count = 0
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(150)
        self.preview_timer.timeout.connect(self.preview)
        self.setWindowTitle('批量设置元器件参数')
        fit_window(self, 820, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(label(f'批量设置 · {quantity_text(components)} 个元器件', 'Title'))
        layout.addWidget(label('工作环境：' + conditions_text(environment, ambient_temperature_C)))
        layout.addWidget(label('直接修改需要统一的参数，未修改项各自保留；“多种值”表示各位号取值不同。'))
        common = {c.model for c in components}
        origins = {c.is_domestic for c in components}
        self.model_selector = ModelSelector(
            components[0], next(iter(common)) if len(common) == 1 else '待配置',
            domestic=next(iter(origins)) if len(origins) == 1 else None, prediction_state=prediction_state)
        layout.addWidget(self.model_selector)
        self.rate_scope_note = label('仅修改当前查看环境和温度的已知失效率；其他工况的值分别保留。')
        self.rate_scope_note.hide()
        layout.addWidget(self.rate_scope_note)
        body = QHBoxLayout()
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        body.addWidget(self.scroll, 1)
        self.details = QWidget()
        self.details.setMinimumWidth(480)
        right = QVBoxLayout(self.details)
        right.setContentsMargins(4, 0, 0, 0)
        right.addWidget(label('逐项计算结果' if calculation_enabled else '逐项参数', 'Section'))
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['位号 / 名称', '修改后的参数', '计算状态'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        resizable_columns(self.table, 'batch', [130, 300, 200], min_widths=[110, 200, 150])
        right.addWidget(self.table)
        self.from_bom = QCheckBox('重新从 BOM 带入标称值（覆盖已填阻值 / 电容量）')
        right.addWidget(self.from_bom)
        body.addWidget(self.details, 1)
        self.details.hide()
        layout.addLayout(body, 1)
        self.status = label('', 'Formula')
        layout.addWidget(self.status)
        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self._details_caption = '逐项结果' if calculation_enabled else '逐项参数'
        self.details_button = QPushButton(f'展开{self._details_caption} ▸')
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self.toggle_details)
        buttons.addWidget(self.details_button)
        self.reset_button = QPushButton('撤销参数修改')
        self.reset_button.clicked.connect(self.reset_fields)
        buttons.addWidget(self.reset_button)
        buttons.addStretch()
        self.buttons = QDialogButtonBox()
        self.apply_button = self.buttons.addButton('保存', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName('Primary')
        self.buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.apply)
        self.buttons.rejected.connect(self.reject)
        buttons.addWidget(self.buttons)
        layout.addLayout(buttons)
        self.model_selector.changed.connect(self.build_fields)
        self.from_bom.toggled.connect(self.invalidate)
        self.build_fields()
        self.preview()

    def toggle_details(self, checked):
        self.details.setVisible(checked)
        self.details_button.setText(('收起' if checked else '展开') + self._details_caption + (' ◂' if checked else ' ▸'))
        fit_window(self, 1260 if checked else 820, self.height())

    def _component_existing_source(self, comp, model, key, reusable_cache=None):
        if comp.model == model:
            return comp.input_sources.get(key)
        if comp.model == '待配置':
            if reusable_cache is not None and id(comp) in reusable_cache:
                return reusable_cache[id(comp)][1].get(key)
            return reusable_from_component(comp, model, self.prediction_state)[1].get(key)
        return None

    def configure_source(self, spec):
        component = copy.deepcopy(self.components[0])
        model = self.model_selector.model
        if component.model != model:
            component.input_sources = {}
        component.model = model
        update = self.source_updates.get(model, {})
        if spec.key in update:
            if update[spec.key] is None:
                component.input_sources.pop(spec.key, None)
            else:
                component.input_sources[spec.key] = copy.deepcopy(update[spec.key])
        else:
            existing = [self._component_existing_source(c, model, spec.key) for c in self.components]
            if existing and existing[0] is not None and all(s == existing[0] for s in existing):
                component.input_sources[spec.key] = copy.deepcopy(existing[0])
            else:
                component.input_sources.pop(spec.key, None)
        dialog = InputSourceDialog(component, spec, self, environment=self.environment)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.source_updates.setdefault(model, {})[spec.key] = dialog.result_source
            if dialog.result_source is not None:
                self.changed_keys.discard(spec.key)
            elif is_field_blank(self.fields[spec.key]):
                self.changed_keys.discard(spec.key)
            else:
                self.changed_keys.add(spec.key)
            self.fields[spec.key].setEnabled(dialog.result_source is None)
            self.invalidate()

    def reset_fields(self):
        self.source_updates.pop(self.model_selector.model, None)
        self.drafts.pop(self.model_selector.model, None)
        self.variable_drafts.pop(self.model_selector.model, None)
        self.active_model = None
        self._syncing_constraints = False
        self._base_params = {}
        self._error_count = 0
        self.from_bom.setChecked(False)
        self.build_fields()

    def build_fields(self):
        if self.active_model is not None:
            if self.variables_editor is not None:
                self.variable_drafts[self.active_model] = (set(self.variable_changed_keys), self.variables_editor.draft())
            self.drafts[self.active_model] = (set(self.changed_keys), {
                key: field_draft(field)
                for key, field in self.fields.items()})
        model = self.model_selector.model
        self.rate_scope_note.setVisible(model == '已知单件失效率')
        self.active_model = model
        self.changed_keys, draft = self.drafts.get(model, (set(), {}))
        self.changed_keys = set(self.changed_keys)
        self.fields = {}
        self.field_rows = {}
        self.source_buttons = {}
        self.variables_editor = None
        self.variable_changed_keys = set()
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        previous_section = None
        reusable_cache = {
            id(c): reusable_from_component(c, model, self.prediction_state)
            for c in self.components
            if c.model == '待配置'
        }
        allow_bare_eia = _is_parameter_value_column(self.components)
        model_source_updates = self.source_updates.get(model, {})
        for spec in MODELS[model]['fields']:
            if spec.scope == 'project':
                continue
            if spec.section != previous_section:
                form.addRow(label(spec.section, 'Section'))
                previous_section = spec.section
            values = []
            for c in self.components:
                value = (c.condition_rate(self.environment, self.ambient_temperature_C)
                         if model == '已知单件失效率' and c.model == model and c.rate_state == self.prediction_state else
                         c.parameters.get(spec.key) if c.model == model else
                         reusable_cache[id(c)][0].get(spec.key) if c.model == '待配置' else None)
                nominal = _extract_nominal(c, model, allow_bare_eia=allow_bare_eia)
                if value is None and nominal and nominal[0] == spec.key:
                    value = nominal[1]
                values.append(value)
            shared = values[0] is not None and all(v == values[0] for v in values)
            mixed = not shared and any(v is not None for v in values)
            field = create_field(spec, model,
                                 empty_text='多种值 · 分别保留' if mixed else '保持原值 / 暂不填写',
                                 placeholder='多种值，留空保留各自值' if mixed else '填写数值；留空保留原值')
            if shared:
                set_field_value(field, values[0])
            if spec.key in draft:
                restore_field_draft(field, draft[spec.key])
            if model == '已知单件失效率':
                complete = condition_key(self.environment, self.ambient_temperature_C) is not None
                field.setReadOnly(not complete)
                field.setPlaceholderText('填写后仅应用于当前工况；留空保留各自记录' if complete
                                         else '请先补齐当前工作环境和温度')
            connect_field_change(field, lambda _value, key=spec.key: self.field_changed(key))
            field.setToolTip(spec.label + '\n修改后统一应用；未修改时各位号分别保留。')
            self.fields[spec.key] = field
            self.field_rows[spec.key] = add_field_row(form, spec, field)
            if source_kind(spec, model):
                button = QPushButton('统一设置辅助计算 / 参数来源…')
                button.clicked.connect(lambda _checked=False, selected=spec: self.configure_source(selected))
                form.addRow('', button)
                self.source_buttons[spec.key] = button
                existing_sources = [
                    self._component_existing_source(c, model, spec.key, reusable_cache)
                    for c in self.components
                ]
                all_have_source = bool(existing_sources) and all(s is not None for s in existing_sources)
                if spec.key in model_source_updates:
                    field_enabled = model_source_updates[spec.key] is None
                else:
                    field_enabled = not all_have_source
                field.setEnabled(field_enabled)
                if not field_enabled:
                    self.changed_keys.discard(spec.key)
        if any(isinstance(field, StressComboBox) for field in self.fields.values()):
            sources = [component.stress_variables if component.model == model else {} for component in self.components]
            definitions, initial_mixed = {}, set()
            for name in set().union(*(source.keys() for source in sources)):
                values = [source.get(name) for source in sources]
                if values[0] is not None and all(value == values[0] for value in values):
                    definitions[name] = values[0]
                else:
                    initial_mixed.add(name)
            changed, (definitions, mixed) = self.variable_drafts.get(
                model, (set(), (definitions, set(initial_mixed)))
            )
            self.variable_changed_keys = set(changed) - set(mixed)
            self.variables_editor = StressVariablesEditor(
                definitions,
                mixed_names=mixed,
                initial_mixed_names=initial_mixed,
                sources=sources,
            )
            self.variables_editor.changed.connect(self.variable_changed)
            form.addRow(self.variables_editor)
            for field in self.fields.values():
                if isinstance(field, StressComboBox):
                    field.variables_provider = self.variable_values
                    field.mixed_variables_provider = lambda: self.variables_editor.mixed_names
        self.scroll.setWidget(page)
        self.parameter_form = form
        matching = [c for c in self.components if c.model == model]
        self._base_params = {}
        if matching:
            common_keys = set.intersection(*(set(c.parameters) for c in matching))
            for k in common_keys:
                vals = [c.parameters[k] for c in matching]
                if vals[0] is not None and all(v == vals[0] for v in vals):
                    self._base_params[k] = vals[0]
        self.invalidate()

    def field_changed(self, key):
        if is_field_blank(self.fields[key]):
            self.changed_keys.discard(key)
        else:
            self.changed_keys.add(key)
        self.invalidate()

    def variable_values(self):
        return self.variables_editor.values() if self.variables_editor is not None else {}

    def variable_changed(self, name):
        if self.variables_editor is not None and name in self.variables_editor.mixed_names:
            self.variable_changed_keys.discard(name)
        else:
            self.variable_changed_keys.add(name)
        self.invalidate()

    def sync_stress_variables(self):
        if self.variables_editor is None:
            return
        expressions = []
        choices = active_choices(self.fields)
        specs = {spec.key: spec for spec in MODELS[self.model_selector.model].fields}
        for key, field in self.fields.items():
            if not isinstance(field, StressComboBox):
                continue
            applicable = [component for component in self.components
                          if specs[key].active((component.parameters if component.model == self.model_selector.model else {}) | choices)]
            if not applicable:
                continue
            if key in self.changed_keys and not field.is_blank():
                expressions.append(field.raw_value())
            else:
                expressions.extend(component.parameters[key] for component in applicable
                                   if component.model == self.model_selector.model and key in component.parameters)
        self.variables_editor.update_references(expressions)
        if self.variables_editor.names and not self._variables_height_adjusted:
            self._variables_height_adjusted = True
            height = min(840, self.screen().availableGeometry().height() - 40)
            if height > self.height():
                self.resize(self.width(), height)
        for field in self.fields.values():
            if isinstance(field, StressComboBox):
                field.update_result()

    def invalidate(self, *_args):
        if self._syncing_constraints:
            return
        self._syncing_constraints = True
        try:
            sync_field_constraints(self.fields, self._base_params)
        finally:
            self._syncing_constraints = False
        self.sync_stress_variables()
        if hasattr(self, 'parameter_form'):
            choices = active_choices(self.fields)
            for spec in MODELS[self.model_selector.model]['fields']:
                if spec.scope != 'device' or not (spec.when or spec.when_all):
                    continue
                applicable = any(spec.active((c.parameters if c.model == self.model_selector.model else {}) | choices)
                                 for c in self.components)
                self.parameter_form.setRowVisible(self.field_rows[spec.key], applicable)
                if spec.key in self.source_buttons:
                    self.parameter_form.setRowVisible(self.source_buttons[spec.key], applicable)
        self.result_components = None
        self._error_count = 0
        self.apply_button.setEnabled(self.model_selector.model != '待配置')
        self.reset_button.setEnabled(bool(self.changed_keys or self.variable_changed_keys or self.source_updates.get(self.model_selector.model)) or self.from_bom.isChecked())
        self.table.setRowCount(0)
        self.status.setText('正在更新计算结果…' if self.calculation_enabled else CALCULATION_DISABLED)
        self.preview_timer.start()

    def done(self, result):
        self.preview_timer.stop()
        super().done(result)

    def hideEvent(self, event):
        self.preview_timer.stop()
        super().hideEvent(event)

    def has_changes(self):
        if self.result_components is None or len(self.result_components) != len(self.components):
            return False
        return list(self.result_components) != list(self.components)

    def preview(self):
        self.preview_timer.stop()
        try:
            model = self.model_selector.model
            model_source_updates = self.source_updates.get(model, {})
            active_sources = {k for k, v in model_source_updates.items() if v is not None}
            overrides = {}
            cleared_record_keys = set()
            specs = {spec.key: spec for spec in MODELS[model].fields}
            for key in self.changed_keys:
                if key in active_sources:
                    continue
                field = self.fields[key]
                if not field.isEnabled() or self.field_rows[key].isHidden():
                    continue
                val = read_field_value(
                    specs[key], field, allow_incomplete=True,
                    empty_numeric_error='请填写有效数值，或清空该项以保留各自原值',
                )
                if specs[key].record_fields and not val:
                    if field.table.rowCount() == 0:
                        cleared_record_keys.add(key)
                    continue
                overrides[key] = val
            stress_var_overrides = {
                name: self.variables_editor.definitions[name]
                for name in self.variable_changed_keys
                if self.variables_editor is not None
                and name in self.variables_editor.names
                and name not in self.variables_editor.mixed_names
                and name in self.variables_editor.definitions
            }
            source_components = self.components
            if cleared_record_keys:
                source_components = [copy.deepcopy(c) for c in self.components]
                for comp in source_components:
                    if comp.model == model:
                        for key in cleared_record_keys:
                            comp.parameters.pop(key, None)
            has_any_edit = bool(
                overrides
                or cleared_record_keys
                or stress_var_overrides
                or model_source_updates
                or self.from_bom.isChecked()
                or any(c.model != model or c.rate_state != self.prediction_state for c in self.components)
            )
            results = configure_batch(
                source_components,
                model,
                overrides,
                from_bom=self.from_bom.isChecked(),
                fill_missing_nominal=has_any_edit,
                environment=self.environment,
                ambient_temperature_C=self.ambient_temperature_C,
                calculation_enabled=self.calculation_enabled,
                prediction_state=self.prediction_state,
                input_source_overrides=model_source_updates,
                stress_variable_overrides=stress_var_overrides,
            )
            if has_any_edit:
                from .calculation import evaluate_component

                repaired = []
                for item, result in results:
                    if _prune_incompatible_parameters(item, overrides):
                        item.validate()
                        if self.calculation_enabled:
                            result = (
                                Evaluation(None, error='元器件数量待填写')
                                if item.quantity is None
                                else evaluate_component(
                                    item, self.environment, self.ambient_temperature_C, self.prediction_state
                                )
                            )
                    repaired.append((item, result))
                results = repaired
            else:
                results = [
                    (copy.deepcopy(orig), result)
                    for orig, (_item, result) in zip(self.components, results, strict=True)
                ]
            pending = 0
            errors = 0
            colors = current_colors()
            self.table.setUpdatesEnabled(False)
            try:
                self.table.setRowCount(len(results))
                for i, (item, result) in enumerate(results):
                    is_err = False
                    if not self.calculation_enabled:
                        status = CALCULATION_DISABLED
                    elif result.rate is None:
                        is_err = is_component_config_error(item.model, result, calculation_enabled=True)
                        if is_err:
                            errors += 1
                            status = '配置异常：' + result.error
                        else:
                            pending += 1
                            status = result.error
                    else:
                        symbol = 'λNP' if self.prediction_state == 'nonoperating' else 'λp'
                        status = f'{symbol} = {number_text(result.rate)}'
                    description = '\n'.join(text for stress in (False, True)
                        if (text := input_text(item, stress=stress, detailed=True, result=result,
                            environment=self.environment, ambient_temperature_C=self.ambient_temperature_C)) != '—')
                    if item.model == '已知单件失效率':
                        value = item.condition_rate(self.environment, self.ambient_temperature_C)
                        description = ('当前工况尚未填写' if value is None else
                                       f'当前工况单件失效率：{number_text(value)} 10⁻⁶/h')
                    texts = [item.name, description, status]
                    for j, text in enumerate(texts):
                        cell = QTableWidgetItem(text)
                        cell.setToolTip(text)
                        if j == 2:
                            if not self.calculation_enabled:
                                cell.setForeground(QColor(colors['muted']))
                            elif result.rate is not None:
                                cell.setForeground(QColor(colors['success']))
                                font = QFont()
                                font.setBold(True)
                                cell.setFont(font)
                            elif is_err:
                                cell.setForeground(QColor(colors['error']))
                            else:
                                cell.setForeground(QColor(colors['warning']))
                        self.table.setItem(i, j, cell)
            finally:
                self.table.setUpdatesEnabled(True)
            self.result_components = [item for item, result in results]
            self._error_count = errors
            self.apply_button.setEnabled(True)
            modified_count = len(overrides) + len(cleared_record_keys)
            summary = (f'统一修改 {modified_count} 项参数 · {len(results) - pending - errors} 个已计算'
                       + (f' · {errors} 个配置异常' if errors else '')
                       + (f' · {pending} 个待补充，可先保存' if pending else '')
                       if self.calculation_enabled else
                       CALCULATION_DISABLED + f' · 统一修改 {modified_count} 项参数，可保存')
            if self.variable_changed_keys:
                summary += f' · 修改 {len(self.variable_changed_keys)} 个变量'
            self.status.setText(summary
                                + ('；已更换器件类型，原类型参数将清除。'
                                   if any(c.model != self.model_selector.model for c in self.components)
                                   else '；其余参数各自保留。'))
        except (ValueError, OverflowError) as error:
            self.result_components = None
            self._error_count = 0
            self.status.setText(str(error) if self.calculation_enabled else
                                CALCULATION_DISABLED + '；输入需要调整：' + str(error))
            self.apply_button.setEnabled(False)

    def _count_config_errors_across_conditions(self):
        if not self.result_components:
            return 0
        checks = []
        seen = set()
        if self.calculation_enabled:
            pair = (self.environment, self.ambient_temperature_C)
            seen.add(pair)
            checks.append(pair)
        for cond in self.conditions or ():
            if not getattr(cond, 'enabled', False):
                continue
            pair = (cond.environment, cond.ambient_temperature_C)
            if pair not in seen:
                seen.add(pair)
                checks.append(pair)
        if not checks:
            return 0
        if len(checks) == 1 and self.calculation_enabled:
            return self._error_count
        from .calculation import evaluate_component

        error_indices = set()
        for idx, item in enumerate(self.result_components):
            if item.quantity is None:
                continue
            for env, temp in checks:
                res = evaluate_component(item, env, temp, self.prediction_state)
                if is_component_config_error(item.model, res, calculation_enabled=True):
                    error_indices.add(idx)
                    break
        return len(error_indices)

    def apply(self):
        self.preview()
        if self.result_components is None:
            QMessageBox.warning(self, '输入需要调整', self.status.text())
            return
        if self.isVisible():
            error_count = self._count_config_errors_across_conditions()
            if error_count:
                answer = QMessageBox.question(
                    self,
                    '配置异常确认',
                    f'当前配置后仍有 {error_count} 项在启用工况下无法正常计算，将显示为“配置异常”。\n\n是否仍要按当前修改保存？',
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No,
                )
                if answer != QMessageBox.StandardButton.Yes:
                    return
        self.accept()
