"""Device parameters and stress input. Computed factors are read-only."""

import copy
import html
from fractions import Fraction

from PyQt6.QtCore import QTimer, QUrl
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .analysis_methods.workspace import is_component_config_error
from .configuration import CALCULATION_DISABLED, nominal_parameter
from .domain import Component
from .field_editor import (
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
from .numeric import formatted_number as number
from .numeric import number_text
from .prediction import STATES
from .rate_editor import ConditionRateEditor
from .standards import MODELS, conditions_text, factor_html
from .state_transfer import reusable_from_component
from .stress_edit import StressComboBox
from .stress_variables_editor import StressVariablesEditor
from .theme import current_colors, fit_window, text_browser
from .theme import section_label as label


class ComponentDialog(QDialog):
    def __init__(self, component=None, parent=None, *, environment=None, ambient_temperature_C=None,
                 calculation_enabled=True, conditions=None, prediction_state='working'):
        super().__init__(parent)
        self.environment = environment
        self.ambient_temperature_C = ambient_temperature_C
        self.calculation_enabled = calculation_enabled
        self.conditions = (
            conditions
            if conditions is not None
            else getattr(getattr(parent, 'project', None), 'conditions', None)
        )
        self.prediction_state = prediction_state
        self.source_drafts = {component.model: copy.deepcopy(component.input_sources)} if component else {}
        self.rate_drafts = {}
        self.rate_editor = None
        self.drafts = {}
        self.variable_drafts = {}
        self._variables_height_adjusted = False
        self.variables_editor = None
        self.active_model = None
        self.original = component
        self.result_component = None
        self.fields = {}
        self._building_fields = False
        self._syncing_constraints = False
        self.preview_timer = QTimer(self)
        self.preview_timer.setSingleShot(True)
        self.preview_timer.setInterval(120)
        self.preview_timer.timeout.connect(self.calculate)
        self.setWindowTitle('编辑元器件' if component else '添加元器件')
        fit_window(self, 820, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(label('预计对象参数', 'Title'))
        self.environment_label = label('工作环境：' + conditions_text(environment, ambient_temperature_C))
        layout.addWidget(self.environment_label)
        body = QHBoxLayout()
        left = QVBoxLayout()
        form = QFormLayout()
        self.name = QLineEdit(component.name if component else '')
        self.name.setPlaceholderText('例如：R1 · 10 kΩ 电阻')
        self.quantity = QSpinBox()
        self.quantity.setRange(0, 1_000_000)
        self.quantity.setSpecialValueText('待填写')
        self.quantity.setValue((component.quantity or 0) if component else 1)
        if component and component.quantity == 1 and len(component.bom.get('assigned_designators', [])) == 1:
            self.quantity.setEnabled(False)
            self.quantity.setToolTip('此行对应一个 BOM 位号，数量固定为 1。整组数量由各位号汇总。')
        self.model_selector = ModelSelector(component, component.model if component else '待配置', prediction_state=prediction_state)
        name_row = QWidget()
        name_row_layout = QHBoxLayout(name_row)
        name_row_layout.setContentsMargins(0, 0, 0, 0)
        name_row_layout.setSpacing(8)
        name_row_layout.addWidget(self.name, 1)
        if self.quantity.isEnabled():
            self.quantity_label = label('器件数量')
            self.quantity.setMaximumWidth(120)
            name_row_layout.addWidget(self.quantity_label)
            name_row_layout.addWidget(self.quantity)
        else:
            self.quantity.hide()
        form.addRow('名称 / 位号', name_row)

        self.nominal_button = QPushButton('带入 BOM 标称值')
        self.nominal_button.setVisible(bool(component and component.bom))
        self.nominal_button.clicked.connect(self.fill_nominal)
        type_row = QWidget()
        type_row_layout = QHBoxLayout(type_row)
        type_row_layout.setContentsMargins(0, 0, 0, 0)
        type_row_layout.setSpacing(6)
        type_row_layout.addWidget(self.model_selector, 1)
        if component and component.bom:
            type_row_layout.addWidget(self.nominal_button)
        form.addRow('器件类型', type_row)
        left.addLayout(form)
        self.note = label('')
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        left.addWidget(self.scroll, 1)
        self.dual_stress_hint = label('', 'Formula')
        self.dual_stress_hint.hide()
        left.addWidget(self.dual_stress_hint)
        self.reference = QTextEdit()
        self.reference.setPlaceholderText('记录规格书、质量等级证明、实际工况及取值依据。')
        self.reference.setMaximumHeight(85)
        if component:
            self.reference.setPlainText(component.reference)
        body.addLayout(left, 1)
        self.details = QWidget()
        self.details.setMinimumWidth(360)
        self.details.setMaximumWidth(470)
        right = QVBoxLayout(self.details)
        right.setContentsMargins(4, 0, 0, 0)
        right.addWidget(label('计算过程', 'Section'))
        self.formula = label('', 'Formula')
        self.formula.setVisible(calculation_enabled)
        right.addWidget(self.formula)
        right.addWidget(self.note)
        self.trace = text_browser()
        self.trace.setOpenLinks(False)
        self.trace.anchorClicked.connect(self._open_standard_link)
        right.addWidget(self.trace, 1)
        if component and component.bom:
            row = component.bom['row']
            right.addWidget(label(f"BOM：{row.get('Value', '')} · {row.get('Description', '')}\n"
                                  f"封装：{row.get('Footprint', '')}\n厂商：{row.get('Manufacturer', '')}\n"
                                  f"型号：{row.get('Manufacturer Part Number', '')}"))
        right.addWidget(label('输入依据与工况（选填）'))
        right.addWidget(self.reference)
        body.addWidget(self.details)
        self.details.hide()
        layout.addLayout(body, 1)
        self.preview = label('填写参数后计算。', 'Formula')
        layout.addWidget(self.preview)
        buttons = QHBoxLayout()
        self.details_button = QPushButton('展开计算过程 ▸')
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self.toggle_details)
        buttons.addWidget(self.details_button)
        buttons.addStretch()
        self.buttons = QDialogButtonBox()
        self.save_button = self.buttons.addButton('保存', QDialogButtonBox.ButtonRole.AcceptRole)
        self.save_button.setObjectName('Primary')
        self.buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        self.buttons.accepted.connect(self.accept_component)
        self.buttons.rejected.connect(self.reject)
        buttons.addWidget(self.buttons)
        layout.addLayout(buttons)
        self.model_selector.changed.connect(self.build_fields)
        self.quantity.valueChanged.connect(self.invalidate)
        self._building_fields = True
        try:
            self.build_fields()
            if component and component.model != '已知单件失效率':
                for key, value in component.parameters.items():
                    if key in self.fields:
                        set_field_value(self.fields[key], value)
        finally:
            self._building_fields = False
        self.invalidate()
        self.calculate()

    def toggle_details(self, checked):
        self.details.setVisible(checked)
        self.details_button.setText('收起计算过程 ◂' if checked else '展开计算过程 ▸')
        fit_window(self, 1220 if checked else 820, self.height())

    def model(self):
        return self.model_selector.model

    def build_fields(self):
        if self.active_model is not None:
            if self.variables_editor is not None:
                self.variable_drafts[self.active_model] = self.variables_editor.draft()
            if self.active_model == '已知单件失效率' and self.rate_editor is not None:
                self.rate_drafts = self.rate_editor.drafts()
            else:
                self.drafts[self.active_model] = {
                    key: field_draft(field)
                    for key, field in self.fields.items()}
        self.active_model = self.model()
        self.fields = {}
        self.field_rows = {}
        self.source_buttons = {}
        self.variables_editor = None
        if hasattr(self, 'quantity_label'):
            base_model = self.model().removesuffix('（非工作详细）')
            self.quantity_label.setText('印制板数量（块）' if base_model == '国产印制板' else
                                        '电路板数量（块）' if base_model == '国产SMT互连' else
                                        '连接点数量（个）' if base_model == '国产焊接点' else '器件数量')
        model = MODELS[self.model()]
        self.formula.setText(model['formula'])
        self.note.setText(model['note'])
        if self.model() == '已知单件失效率':
            original = (self.original if self.original and self.original.model == self.model()
                        and self.original.rate_state == self.prediction_state else None)
            self.rate_editor = ConditionRateEditor(
                original, self.conditions, environment=self.environment,
                temperature=self.ambient_temperature_C)
            self.rate_editor.restore_drafts(self.rate_drafts)
            self.rate_editor.changed.connect(self.invalidate)
            self.fields = {'λp': self.rate_editor.current_field}
            self.scroll.setWidget(self.rate_editor)
            self.parameter_form = None
            self.invalidate()
            return
        self.rate_editor = None
        page = QWidget()
        form = QFormLayout(page)
        form.setSpacing(10)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        previous_section = None
        prev_building, self._building_fields = self._building_fields, True
        try:
            for spec in model['fields']:
                if spec.scope == 'project':
                    continue
                if spec.section != previous_section:
                    form.addRow(label(spec.section, 'Section'))
                    previous_section = spec.section
                field = create_field(spec, self.model(), use_default=True)
                connect_field_change(field, self.invalidate)
                self.fields[spec.key] = field
                self.field_rows[spec.key] = add_field_row(form, spec, field)
                if source_kind(spec, self.model()):
                    button = QPushButton('辅助计算 / 参数来源…')
                    button.clicked.connect(lambda _checked=False, selected=spec: self.configure_source(selected))
                    form.addRow('', button)
                    self.source_buttons[spec.key] = button
                    field.setEnabled(spec.key not in self.source_drafts.get(self.model(), {}))
            if any(isinstance(field, StressComboBox) for field in self.fields.values()):
                original_variables = (self.original.stress_variables if self.original and
                                      self.original.model == self.model() else {})
                definitions, _mixed = self.variable_drafts.get(self.model(), (original_variables, set()))
                self.variables_editor = StressVariablesEditor(definitions)
                self.variables_editor.changed.connect(self.invalidate)
                form.addRow(self.variables_editor)
                for field in self.fields.values():
                    if isinstance(field, StressComboBox):
                        field.variables_provider = self.variable_values
            self.scroll.setWidget(page)
            self.parameter_form = form
            for key, value in self.drafts.get(self.active_model, {}).items():
                field = self.fields.get(key)
                if field is not None:
                    restore_field_draft(field, value)
            if self.original and self.active_model not in self.drafts:
                if self.original.model == '待配置' and self.active_model != self.original.model:
                    reused_params, reused_sources = reusable_from_component(
                        self.original, self.active_model, self.prediction_state
                    )
                    for spec in model['fields']:
                        if spec.scope != 'project' and spec.key in reused_params and spec.key in self.fields:
                            set_field_value(self.fields[spec.key], reused_params[spec.key])
                    if reused_sources:
                        sources = self.source_drafts.setdefault(self.active_model, {})
                        for key, src in reused_sources.items():
                            if key not in sources and key in self.fields:
                                sources[key] = copy.deepcopy(src)
                                self.fields[key].setEnabled(False)
                nominal = nominal_parameter(self.original, self.model())
                if nominal and (self.model() != self.original.model or nominal[0] not in self.original.parameters):
                    if is_field_blank(self.fields[nominal[0]]):
                        self.fields[nominal[0]].set_number(nominal[1])
                        self.fields[nominal[0]].setToolTip('已从 BOM 标称值带入；可直接修改。')
        finally:
            self._building_fields = prev_building
        self.invalidate()

    def parameters(self, *, partial=False):
        if self.model() == '已知单件失效率':
            return {}
        values = {}
        choices = active_choices(self.fields)
        for spec in MODELS[self.model()]['fields']:
            if spec.scope == 'project' or not spec.active(choices):
                continue
            if spec.key in self.source_drafts.get(self.model(), {}):
                continue
            field = self.fields[spec.key]
            if partial and is_field_blank(field):
                continue
            val = read_field_value(spec, field, allow_incomplete=partial)
            if partial and spec.record_fields and not val:
                continue
            values[spec.key] = val
        return values

    def configure_source(self, spec):
        component = copy.deepcopy(self.original) if self.original else Component('元器件', self.model(), 1, {})
        component.model = self.model()
        component.input_sources = copy.deepcopy(self.source_drafts.get(self.model(), {}))
        dialog = InputSourceDialog(component, spec, self, environment=self.environment)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            sources = self.source_drafts.setdefault(self.model(), {})
            if dialog.result_source is None:
                sources.pop(spec.key, None)
            else:
                sources[spec.key] = dialog.result_source
            self.fields[spec.key].setEnabled(spec.key not in sources)
            self.invalidate()

    def fill_nominal(self):
        value = nominal_parameter(self.original, self.model())
        if value:
            self.fields[value[0]].set_number(value[1])
            self.calculate()
        else:
            message = '此类型没有可带入的标称值，或 BOM Value 无法明确解析；请手动填写。'
            self._style_preview('warning' if self.calculation_enabled else 'normal')
            self.preview.setText(message if self.calculation_enabled else CALCULATION_DISABLED + '。' + message)

    def _style_preview(self, level='normal'):
        if level == 'normal':
            self.preview.setStyleSheet('')
            return
        colors = current_colors()
        accent = colors['error'] if level == 'error' else colors['warning']
        weight = ' font-weight:600;' if level == 'error' else ''
        self.preview.setStyleSheet(
            f'padding:6px 8px; background:{colors["formula_bg"]}; color:{accent}; '
            f'border-left:3px solid {accent};{weight}'
        )

    def variable_values(self):
        return self.variables_editor.values() if self.variables_editor is not None else {}

    def sync_stress_variables(self):
        if self.variables_editor is None:
            return
        choices = active_choices(self.fields)
        specs = {spec.key: spec for spec in MODELS[self.model()].fields}
        fields = [field for key, field in self.fields.items()
                  if isinstance(field, StressComboBox) and specs[key].active(choices)]
        self.variables_editor.update_references([field.raw_value() for field in fields if not field.is_blank()])
        if self.variables_editor.names and not self._variables_height_adjusted:
            self._variables_height_adjusted = True
            height = min(840, self.screen().availableGeometry().height() - 40)
            if height > self.height():
                self.resize(self.width(), height)
        for field in fields:
            field.update_result()

    def _update_dual_stress_hint(self):
        if self.model() not in ('国产双极型双管', '国产硅场效应双管'):
            self.dual_stress_hint.hide()
            return
        self.dual_stress_hint.show()
        try:
            pt = Fraction(str(self.fields['package_rated_power_W'].value()))
            ps1 = Fraction(str(self.fields['rated_power_1_W'].value()))
            ps2 = Fraction(str(self.fields['rated_power_2_W'].value()))
            p1 = Fraction(str(self.fields['operating_power_1_W'].value()))
            p2 = Fraction(str(self.fields['operating_power_2_W'].value()))
            if pt <= 0 or ps1 <= 0 or ps2 <= 0 or p1 <= 0 or p2 <= 0:
                raise ValueError('功率须大于 0')
            if not max(ps1, ps2) <= pt <= ps1 + ps2 or p1 + p2 > pt or p1 > ps1 or p2 > ps2 or 2 * ps1 < pt or 2 * ps2 < pt:
                self.dual_stress_hint.setText('式（24）提示：请核查 PT、PS1、PS2 与 P1、P2 功率约束（需满足 max(PS) ≤ PT ≤ PS1+PS2 且 2PSi ≥ PT）。')
                return
            raw1 = p1 / ps1 + p2 * (2 * ps1 - pt) / (pt * ps1)
            raw2 = p2 / ps2 + p1 * (2 * ps2 - pt) / (pt * ps2)
            text = f'式（24）未校正电应力比：S1₀ = {number_text(float(raw1))}，S2₀ = {number_text(float(raw2))}'
            try:
                ts = Fraction(str(self.fields['rated_temperature_C'].value()))
                tm = Fraction(str(self.fields['max_junction_temperature_C'].value()))
                if ts >= 25 and tm <= 200 and tm > ts:
                    c = (min(tm, 175) - ts) / 150
                    if c > 0:
                        text += f' ｜ 温度系数 C = {number_text(float(c))} → S1 = {number_text(float(raw1 * c))}，S2 = {number_text(float(raw2 * c))}'
            except (KeyError, ValueError):
                pass
            self.dual_stress_hint.setText(text)
        except (KeyError, ValueError):
            self.dual_stress_hint.setText('式（24）双管应力计算器：填写 PT、PS1、PS2、P1、P2 后自动回显两管电应力比。')

    def _open_standard_link(self, url):
        qurl = QUrl(url) if isinstance(url, str) else url
        if qurl.scheme() != 'standard' or qurl.host() != 'table':
            return False
        parts = [p for p in qurl.path().strip('/').split('/') if p]
        if not parts:
            return False
        from .standard_page import StandardPage

        dialog = QDialog(self)
        dialog.setWindowTitle('标准数据表查询')
        fit_window(dialog, 1080, 680)
        layout = QVBoxLayout(dialog)
        page = StandardPage(dialog)
        layout.addWidget(page, 1)
        page.open_table(parts[0], parts[1] if len(parts) > 1 else None)
        buttons = QDialogButtonBox()
        buttons.addButton('关闭', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()
        return True

    def invalidate(self, *_args):
        if self._building_fields or self._syncing_constraints:
            return
        self._syncing_constraints = True
        try:
            sync_field_constraints(self.fields)
        finally:
            self._syncing_constraints = False
        self.sync_stress_variables()
        if getattr(self, 'parameter_form', None) is not None and len(self.fields) == sum(f.scope == 'device' for f in MODELS[self.model()]['fields']):
            choices = active_choices(self.fields)
            for spec in MODELS[self.model()]['fields']:
                if spec.scope == 'device' and (spec.when or spec.when_all):
                    self.parameter_form.setRowVisible(self.field_rows[spec.key], spec.active(choices))
                    if spec.key in self.source_buttons:
                        self.parameter_form.setRowVisible(self.source_buttons[spec.key], spec.active(choices))
        self._update_dual_stress_hint()
        self._style_preview('normal')
        self.preview.setText('正在更新计算结果…' if self.calculation_enabled else CALCULATION_DISABLED)
        self.trace.setHtml('<p>' + html.escape(MODELS[self.model()]['reference']) + '</p>')
        if self.calculation_enabled:
            self.preview_timer.start()
        else:
            self.preview_timer.stop()

    def calculate(self):
        self.preview_timer.stop()
        if not self.calculation_enabled:
            self._style_preview('normal')
            self.preview.setText(CALCULATION_DISABLED)
            self.trace.setHtml('<p>' + html.escape(MODELS[self.model()]['reference']) + '</p>')
            return None
        try:
            from .calculation import evaluate_component
            result = evaluate_component(self.component_candidate(), self.environment,
                                         self.ambient_temperature_C, self.prediction_state)
            if not self.quantity.value():
                self._style_preview('warning')
                self.preview.setText('元器件数量待填写，暂不计入工程失效率。')
            elif result.rate is None:
                if is_component_config_error(self.model(), result, calculation_enabled=True):
                    self._style_preview('error')
                    self.preview.setText('配置异常：' + result.error)
                else:
                    self._style_preview('warning')
                    self.preview.setText('待配置：' + result.error)
            else:
                self._style_preview('normal')
                self.preview.setText(f'{STATES[self.prediction_state].removesuffix("状态")}失效率  {number(result.rate)} 10⁻⁶/h'
                                     + (f'　 ·　数量 × {STATES[self.prediction_state].removesuffix("状态")}失效率  {number(result.weighted(self.quantity.value()))} 10⁻⁶/h'
                                        if self.quantity.value() != 1 else ''))
            self.trace.setHtml(factor_html(result, reference=MODELS[self.model()]['reference']))
            return result
        except (ValueError, OverflowError) as error:
            self._style_preview('error')
            self.preview.setText('配置异常：' + str(error))
            return None

    def component_candidate(self):
        component = copy.deepcopy(self.original) if self.original else Component('', self.model(), None, {})
        if component.model != self.model():
            component.parameters = {}
            component.condition_rates = []
            if hasattr(component, 'module_source'):
                component.module_source = None
            component.disclosure = None
        component.name = self.name.text().strip()
        component.model = self.model()
        component.quantity = self.quantity.value() or None
        component.reference = self.reference.toPlainText()
        component.domestic = self.model_selector.domestic
        component.stress_variables = self.variable_values()
        component.input_sources = copy.deepcopy(self.source_drafts.get(self.model(), {}))
        if component.model == '已知单件失效率' and component.rate_state != self.prediction_state:
            component.condition_rates = []
        component.rate_state = self.prediction_state
        if self.model() == '已知单件失效率':
            self.rate_editor.apply_changes(component)
        else:
            component.parameters = self.parameters(partial=True)
            component.condition_rates = []
        return component

    def done(self, result):
        self.preview_timer.stop()
        super().done(result)

    def hideEvent(self, event):
        self.preview_timer.stop()
        super().hideEvent(event)

    def _enabled_condition_checks(self):
        checks = []
        seen = set()
        if self.calculation_enabled:
            pair = (self.environment, self.ambient_temperature_C)
            seen.add(pair)
            checks.append((*pair, None))
        for cond in self.conditions or ():
            if not getattr(cond, 'enabled', False):
                continue
            pair = (cond.environment, cond.ambient_temperature_C)
            if pair not in seen:
                seen.add(pair)
                checks.append((*pair, cond.name))
        return checks

    def accept_component(self):
        try:
            component = self.component_candidate().validate()
            # Partial physical inputs are saved as pending, never as a zero failure rate.
        except (ValueError, OverflowError) as error:
            QMessageBox.warning(self, '输入需要调整', str(error))
            return
        if self.isVisible() and component.quantity is not None:
            from .calculation import evaluate_component

            for env, temp, cond_name in self._enabled_condition_checks():
                result = evaluate_component(component, env, temp, self.prediction_state)
                if is_component_config_error(component.model, result, calculation_enabled=True):
                    prefix = f'工况“{cond_name}”：' if cond_name else ''
                    answer = QMessageBox.question(
                        self,
                        '配置异常确认',
                        f'当前参数在所选工况下无法正常计算，将显示为“配置异常”：\n{prefix}{result.error}\n\n是否仍要按当前输入保存？',
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                        QMessageBox.StandardButton.No,
                    )
                    if answer != QMessageBox.StandardButton.Yes:
                        self.calculate()
                        return
                    break
        self.result_component = component
        self.accept()
