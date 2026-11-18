"""Right-hand inspector panel with condition Pareto overview, quick edits, and standard links."""

import html
import re

from PyQt6.QtCore import Qt, QUrl, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..model_registry import model_label
from ..numeric import formatted_number as number
from ..quality_labels import quality_option
from ..standards import MODELS, conditions_text, factor_html
from ..stress import validate_stress
from ..theme import THEME_BUS, section_label, text_browser


class ComponentInspector(QFrame):
    """Synchronize detailed calculation trace and quick parameter edits with tree selection."""

    edit_requested = pyqtSignal()
    quick_parameter_changed = pyqtSignal(int, str, object)
    standard_link_clicked = pyqtSignal(QUrl)
    component_selected = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('Inspector')
        self.setMinimumWidth(290)

        self._project = None
        self._context = None
        self._selected_rows = []
        self._syncing = False
        self._active_row = None
        self._stress_spec = None

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 6, 6, 6)
        root.setSpacing(5)
        root.addWidget(section_label('器件详情与追溯', 'Section'))

        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.overview_page = self._build_overview_page()
        self.single_page = self._build_single_page()
        self.multi_page = self._build_multi_page()
        self.stack.addWidget(self.overview_page)
        self.stack.addWidget(self.single_page)
        self.stack.addWidget(self.multi_page)

        THEME_BUS.changed.connect(self._retheme)

    def _build_overview_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(5)
        self.overview_summary = section_label('', 'Formula')
        layout.addWidget(self.overview_summary)
        layout.addWidget(section_label('高风险失效率贡献 Top 5（点击定位）', 'Section'))
        self.top_list = QListWidget()
        self.top_list.itemClicked.connect(self._on_top_clicked)
        layout.addWidget(self.top_list, 1)
        return page

    def _build_single_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(5)

        head_row = QHBoxLayout()
        self.single_title = section_label('', 'Section')
        head_row.addWidget(self.single_title, 1)
        self.single_edit_btn = QPushButton('配置参数…')
        self.single_edit_btn.setObjectName('Primary')
        self.single_edit_btn.clicked.connect(self.edit_requested)
        head_row.addWidget(self.single_edit_btn)
        layout.addLayout(head_row)

        self.single_meta = section_label('', 'Formula')
        layout.addWidget(self.single_meta)

        self.quick_widget = QWidget()
        quick_form = QFormLayout(self.quick_widget)
        quick_form.setContentsMargins(0, 0, 0, 0)
        quick_form.setSpacing(6)

        self.quick_quality = QComboBox()
        self.quick_quality.currentIndexChanged.connect(self._on_quality_changed)
        quick_form.addRow('快捷质量等级', self.quick_quality)
        self.quick_quality_label = quick_form.labelForField(self.quick_quality)

        stress_row = QWidget()
        stress_layout = QHBoxLayout(stress_row)
        stress_layout.setContentsMargins(0, 0, 0, 0)
        stress_layout.setSpacing(4)
        self.quick_stress = QLineEdit()
        self.quick_stress.setPlaceholderText('输入比值或表达式，如 0.4')
        self.quick_stress.returnPressed.connect(self._on_stress_apply)
        self.quick_stress_btn = QPushButton('应用')
        self.quick_stress_btn.clicked.connect(self._on_stress_apply)
        stress_layout.addWidget(self.quick_stress, 1)
        stress_layout.addWidget(self.quick_stress_btn)
        quick_form.addRow('快捷电应力比', stress_row)
        self.quick_stress_row = stress_row
        self.quick_stress_label = quick_form.labelForField(stress_row)

        self.quick_feedback = section_label('')
        quick_form.addRow('', self.quick_feedback)
        layout.addWidget(self.quick_widget)

        layout.addWidget(section_label('公式因子与标准追溯（点击表/节链接跳转）', 'Section'))
        self.single_trace = text_browser()
        self.single_trace.setOpenLinks(False)
        self.single_trace.anchorClicked.connect(self.standard_link_clicked)
        layout.addWidget(self.single_trace, 1)
        return page

    def _build_multi_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(5)
        self.multi_summary = section_label('', 'Formula')
        layout.addWidget(self.multi_summary)
        self.multi_breakdown = text_browser()
        self.multi_breakdown.setOpenLinks(False)
        layout.addWidget(self.multi_breakdown, 1)
        action_row = QHBoxLayout()
        self.multi_edit_btn = QPushButton('批量配置选中项…')
        self.multi_edit_btn.setObjectName('Primary')
        self.multi_edit_btn.clicked.connect(self.edit_requested)
        action_row.addWidget(self.multi_edit_btn)
        action_row.addStretch()
        layout.addLayout(action_row)
        return page

    def _retheme(self, _mode):
        if self._project is not None and self._context is not None:
            self.update_selection(self._project, self._context, self._selected_rows)

    def _on_top_clicked(self, item):
        row = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(row, int):
            self.component_selected.emit(row)

    def _on_quality_changed(self, _index):
        if self._syncing or self._active_row is None:
            return
        value = self.quick_quality.currentData()
        if value is not None:
            self.quick_parameter_changed.emit(self._active_row, 'quality', value)

    def _on_stress_apply(self):
        if self._syncing or self._active_row is None or self._project is None or self._stress_spec is None:
            return
        component = self._project.components[self._active_row]
        raw_text = self.quick_stress.text().strip()
        if not raw_text:
            self.quick_feedback.setText('请输入电应力比或表达式后再应用。')
            return
        try:
            # Parse plain float literals cleanly while preserving symbolic expressions verbatim.
            if re.fullmatch(r'[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?', raw_text):
                candidate = float(raw_text)
            else:
                candidate = raw_text
            validate_stress(candidate, self._stress_spec.stress_max, self._stress_spec.label, component.stress_variables)
        except (ValueError, OverflowError) as error:
            self.quick_feedback.setText(f'输入未应用：{error}')
            return
        self.quick_feedback.setText('')
        self.quick_parameter_changed.emit(self._active_row, 'stress_ratio', candidate)

    def update_selection(self, project, context, selected_rows):
        self._project = project
        self._context = context
        self._selected_rows = list(selected_rows)

        if not project.components or not selected_rows:
            self._show_overview(project, context)
            self.stack.setCurrentIndex(0)
            return

        if len(selected_rows) == 1:
            self._show_single(project, context, selected_rows[0])
            self.stack.setCurrentIndex(1)
            return

        self._show_multi(project, context, selected_rows)
        self.stack.setCurrentIndex(2)

    def _show_overview(self, project, context):
        cond_line = conditions_text(context.environment, context.ambient_temperature_C)
        total_txt = number(context.total_rate) if context.total_rate is not None else '待完成配置'
        known_txt = number(context.known_rate) if context.known_rate is not None else '0'
        mtbf_txt = (
            f'{number(1e6 / context.total_rate)} h'
            if context.total_rate is not None and context.total_rate > 0
            else '—'
        )
        calculated_count = max(0, len(project.components) - context.pending_count)
        self.overview_summary.setText(
            f'当前工况：{cond_line}\n'
            f'工程总失效率：{total_txt} 10⁻⁶/h  ·  已配置合计：{known_txt} 10⁻⁶/h\n'
            f'预计 MTBF：{mtbf_txt}\n'
            f'统计：共 {len(project.components)} 项 · 已计算 {calculated_count} 项 · 待配置 {context.pending_count} 项'
        )
        self.top_list.clear()
        denom = context.total_rate if context.total_rate and context.total_rate > 0 else context.known_rate
        ranked = []
        for row, component in enumerate(project.components):
            result = context.evaluate(component)
            weighted = result.weighted(component.quantity)
            if weighted is not None:
                ranked.append((weighted, row, component))
        ranked.sort(key=lambda item: item[0], reverse=True)
        for weighted, row, component in ranked[:5]:
            share_txt = f'{weighted / denom * 100:.2f}%' if denom and denom > 0 else '—'
            symbol = 'λNP' if project.prediction_state == 'nonoperating' else 'λp'
            effective_model = component.disclosure['model'] if component.disclosure else component.model
            tag = model_label(effective_model)
            item = QListWidgetItem(
                f'{component.name}（{tag}）\n'
                f'N{symbol} = {number(weighted)} 10⁻⁶/h · 占比 {share_txt}'
            )
            item.setData(Qt.ItemDataRole.UserRole, row)
            self.top_list.addItem(item)
        if not ranked:
            self.top_list.addItem('暂无可计算的元器件失效率结果')

    def _show_single(self, project, context, row):
        if not 0 <= row < len(project.components):
            return
        self._active_row = row
        component = project.components[row]
        result = context.evaluate(component)
        effective_model = component.disclosure['model'] if component.disclosure else component.model
        spec = MODELS.get(effective_model, MODELS['已知单件失效率'])

        designators = ', '.join(component.bom.get('assigned_designators', [])) or component.name
        origin_tag = '国产' if component.is_domestic else '进口'
        model_tag = f'{origin_tag}{model_label(effective_model)}'
        self.single_title.setText(f'{designators}  ·  {model_tag}')

        denom = context.total_rate if context.total_rate and context.total_rate > 0 else context.known_rate
        weighted = result.weighted(component.quantity)
        symbol = 'λNP' if project.prediction_state == 'nonoperating' else 'λp'
        rate_txt = f'{number(result.rate)} 10⁻⁶/h' if result.rate is not None else '待配置'
        weighted_txt = f'{number(weighted)} 10⁻⁶/h' if weighted is not None else '—'
        share_txt = f'{weighted / denom * 100:.2f}%' if weighted is not None and denom and denom > 0 else '—'
        qty_txt = str(component.quantity) if component.quantity is not None else '待填写'

        self.single_meta.setText(
            f'标准条款：{spec.section}  ·  数量：{qty_txt}\n'
            f'单件失效率 {symbol}：{rate_txt}  ·  合计：{weighted_txt}  ·  占比：{share_txt}\n'
            f'公式：{spec.formula}'
        )

        # Quick inline controls for common quality / electrical stress fields.
        self._syncing = True
        try:
            editable = not component.disclosure and component.model not in ('待配置', '工程模块', '已知单件失效率')
            q_spec = next((f for f in spec.fields if f.key == 'quality' and f.choices and f.active(component.parameters)), None)
            has_quality = editable and q_spec is not None
            self.quick_quality.setVisible(has_quality)
            self.quick_quality_label.setVisible(has_quality)
            self.quick_quality.clear()
            if has_quality:
                self.quick_quality.addItem('请选择…', None)
                for choice in q_spec.choices:
                    title, _detail = quality_option(component.model, choice)
                    self.quick_quality.addItem(title, choice)
                cur_q = component.parameters.get('quality')
                idx = self.quick_quality.findData(cur_q)
                self.quick_quality.setCurrentIndex(idx if idx >= 0 else 0)

            s_spec = next((f for f in spec.fields if f.key == 'stress_ratio' and f.stress_max is not None and f.active(component.parameters)), None)
            has_stress = editable and s_spec is not None
            self._stress_spec = s_spec if has_stress else None
            self.quick_stress_row.setVisible(has_stress)
            self.quick_stress_label.setVisible(has_stress)
            if has_stress:
                cur_s = component.parameters.get('stress_ratio')
                self.quick_stress.setText('' if cur_s is None else (cur_s if isinstance(cur_s, str) else number(cur_s)))
            self.quick_feedback.setText('')
            self.quick_widget.setVisible(has_quality or has_stress)
        finally:
            self._syncing = False

        self.single_edit_btn.setText('编辑模块…' if component.model == '工程模块' else '配置参数…')
        self.single_trace.setHtml(factor_html(result, reference=spec.reference))

    def _show_multi(self, project, context, selected_rows):
        total_qty = 0
        qty_complete = True
        weighted_sum = 0.0
        weighted_complete = True
        calculated, pending, errors = 0, 0, []

        for row in selected_rows:
            if not 0 <= row < len(project.components):
                continue
            comp = project.components[row]
            if comp.quantity is None:
                qty_complete = False
            else:
                total_qty += comp.quantity
            res = context.evaluate(comp)
            w = res.weighted(comp.quantity)
            if res.rate is not None:
                calculated += 1
            else:
                pending += 1
                if len(errors) < 8 and comp.model != '待配置' and res.error:
                    errors.append(f'{comp.name}：{res.error}')
            if w is None:
                weighted_complete = False
            else:
                weighted_sum += w

        denom = context.total_rate if context.total_rate and context.total_rate > 0 else context.known_rate
        share_txt = f'{weighted_sum / denom * 100:.2f}%' if weighted_complete and denom and denom > 0 else '—'
        self.multi_summary.setText(
            f'已选中 {len(selected_rows)} 项元器件（合计数量：{total_qty if qty_complete else "含待填写"}）\n'
            f'组内失效率合计：{number(weighted_sum) + " 10⁻⁶/h" if weighted_complete else "含待配置项"}  ·  占比：{share_txt}\n'
            f'已计算：{calculated} 项  ·  待配置：{pending} 项'
        )
        lines = [f'<p>已选 {len(selected_rows)} 项，支持双击组行或点击下方按钮统一定制参数。</p>']
        if errors:
            lines.append('<p><b>配置异常明细（前 8 项）：</b></p>')
            lines.extend(f'<p>• {html.escape(err)}</p>' for err in errors[:8])
        self.multi_breakdown.setHtml(''.join(lines))
