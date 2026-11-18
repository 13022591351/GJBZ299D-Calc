"""Import an engineering project as one module or copy selected independent items."""

import copy
import html
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .dialogs import label
from .domain import quantity_text
from .model_registry import model_label
from .numeric import number_text
from .parameter_disclosure import disclosure_stress_text
from .project_modules import create_project_module, module_project
from .standards import MODELS, environment_text, parameter_text
from .table_layout import resizable_columns, resizable_splitter
from .theme import current_colors, fit_window, text_browser


def clone_components(project, ids, *, source_path=None):
    """Return independent copies in source order; never merge by designator or model."""
    if isinstance(ids, (str, bytes)):
        raise ValueError('请选择要引入的预计项')
    selected = set(ids)
    available = {component.identifier for component in project.components}
    if not selected <= available:
        raise ValueError('选中的预计项已不在源工程中，请重新选择')
    copies = []
    for index, component in enumerate(project.components, 1):
        if component.identifier not in selected:
            continue
        cloned = copy.deepcopy(component)
        cloned.identifier = str(uuid4())
        if source_path is not None:
            source = f'引入来源：{project.name} · {Path(source_path).name}\n源预计项：{index} · {component.name}'
            cloned.reference = '\n\n'.join(part for part in (cloned.reference, source) if part)
            if cloned.bom:
                preserved_row = copy.deepcopy(cloned.bom['row']) if 'row' in cloned.bom else None
                cloned.bom = {'row': preserved_row} if preserved_row is not None else {}
        copies.append(cloned)
    return copies


@dataclass(frozen=True)
class ConditionPreview:
    name: str
    environment: str | None
    temperature: float | None
    status: str
    rate: float | None
    detail: str = ''


def condition_previews(project):
    """Read each enabled source condition once without changing the source project."""
    rows = []
    for condition in project.conditions:
        rate, detail = None, ''
        if not condition.enabled:
            status = '未勾选计算'
        elif not project.components:
            status = '没有内部预计项'
        elif condition.environment is None or condition.ambient_temperature_C is None:
            status = '环境条件待填写'
        else:
            context = project.calculate(condition)
            results = [(component, context.evaluate(component)) for component in project.components]
            missing = [(component, result) for component, result in results if result.rate is None]
            if context.error:
                status, detail = '合计错误', context.error
            elif missing:
                status = f'{len(missing)} 项待配置'
                detail = '\n'.join(f'{component.name}：{result.error}' for component, result in missing[:10])
                if len(missing) > 10:
                    detail += f'\n另有 {len(missing) - 10} 项待配置。'
            else:
                rate = context.total_rate
                status = '可用' if rate else '可用 · 失效率为 0'
        rows.append(ConditionPreview(condition.name, condition.environment,
                                     condition.ambient_temperature_C, status, rate, detail))
    return rows


def plain_label(text, name='Muted'):
    widget = label(text, name)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def disclosed_model(component):
    return component.disclosure['model'] if component.disclosure else component.model


class ConditionPreviewTable(QTableWidget):
    def __init__(self, project, parent=None):
        self.previews = condition_previews(project)
        self.has_calculation_error = any(preview.status == '合计错误' for preview in self.previews)
        super().__init__(len(self.previews), 5, parent)
        self.setHorizontalHeaderLabels([
            '源工况', '环境类别', '环境温度 Ta（℃）', '计算状态', '模块单件失效率（10⁻⁶/h）'])
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setAlternatingRowColors(True)
        self.setWordWrap(False)
        self.verticalHeader().hide()
        self.verticalHeader().setDefaultSectionSize(38)
        resizable_columns(
            self,
            'module-source-conditions',
            [160, 230, 150, 165, 240],
            min_widths=[120, 150, 115, 110, 165],
        )
        colors = current_colors()
        for row, preview in enumerate(self.previews):
            values = [preview.name, environment_text(preview.environment) if preview.environment else '待选择',
                      number_text(preview.temperature) if preview.temperature is not None else '待填写',
                      preview.status, number_text(preview.rate) if preview.rate is not None else '—']
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setToolTip(preview.detail if column == 3 and preview.detail else value)
                if column == 3:
                    if preview.rate is not None:
                        item.setForeground(QColor(colors['success']))
                        font = QFont()
                        font.setBold(True)
                        item.setFont(font)
                    elif preview.status == '合计错误':
                        item.setForeground(QColor(colors['error']))
                    else:
                        item.setForeground(QColor(colors['warning']))
                self.setItem(row, column, item)
                if column in (2, 4):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.item(row, 4).setData(Qt.ItemDataRole.UserRole, preview.rate)

    @property
    def available_count(self):
        return sum(preview.rate is not None for preview in self.previews)


class ComponentSelection(QWidget):
    selection_changed = pyqtSignal()

    def __init__(self, project, parent=None, *, checkable=True):
        super().__init__(parent)
        self.project, self.checkable = project, checkable
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText('搜索位号、名称、型号、标称值或计算模型…')
        tools.addWidget(self.search, 1)
        if checkable:
            self.select_visible_button = QPushButton('全选筛选结果')
            self.select_visible_button.clicked.connect(self.select_visible)
            tools.addWidget(self.select_visible_button)
            self.clear_button = QPushButton('清空选择')
            self.clear_button.clicked.connect(self.clear_selection)
            tools.addWidget(self.clear_button)
        else:
            self.nested_button = QPushButton('查看内部模块…')
            self.nested_button.clicked.connect(self.view_nested_module)
            tools.addWidget(self.nested_button)
        layout.addLayout(tools)
        self.table = QTableWidget(len(project.components), 6)
        self.table.setHorizontalHeaderLabels(['位号', '名称', '型号', '标称值', '计算模型', '数量'])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(38)
        resizable_columns(
            self.table,
            'project-import-components',
            [150, 230, 200, 100, 205, 65],
            min_widths=[100, 150, 130, 85, 150, 60],
            fixed_columns=(5,),
        )
        self.search_values = []
        for row, component in enumerate(project.components):
            original = component.bom.get('row', {})
            values = [', '.join(component.bom.get('assigned_designators', [])) or '—',
                      component.name, original.get('Manufacturer Part Number', '') or '—',
                      original.get('Value', '') or '—', model_label(disclosed_model(component)),
                      number_text(component.quantity) if component.quantity is not None else '待填写']
            source_file = component.bom.get('file_name')
            provenance = f'源工程：{project.name}\n源预计项：{row + 1}\n国产：{"是" if component.is_domestic else "否"}'
            if source_file:
                provenance += f'\n原 BOM：{source_file}'
            if component.reference:
                provenance += '\n输入依据：' + component.reference
            for column, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setToolTip(value + '\n' + provenance)
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, component.identifier)
                    if checkable:
                        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                        item.setCheckState(Qt.CheckState.Unchecked)
                if column == 5:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column, item)
            self.search_values.append(' '.join([*values, disclosed_model(component), component.model, *original.values()]).casefold())
        if checkable:
            layout.addWidget(self.table, 1)
        else:
            self.detail = text_browser()
            self.detail.setOpenExternalLinks(False)
            self.table.setMinimumHeight(120)
            self.detail_splitter = QSplitter(Qt.Orientation.Vertical)
            self.detail_splitter.addWidget(self.table)
            self.detail_splitter.addWidget(self.detail)
            resizable_splitter(self.detail_splitter, 'module-internal-details', [125, 320])
            layout.addWidget(self.detail_splitter, 1)
            self.table.currentCellChanged.connect(self.show_details)
        self.summary = plain_label('')
        layout.addWidget(self.summary)
        self.table.itemChanged.connect(self.update_selection)
        self.search.textChanged.connect(self.filter_rows)
        self.update_selection()
        if not checkable:
            if project.components:
                self.table.setCurrentCell(0, 0)
            self.show_details()

    @property
    def selected_ids(self):
        return [self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)
                for row in range(self.table.rowCount())
                if self.checkable and self.table.item(row, 0).checkState() == Qt.CheckState.Checked]

    @property
    def selected_components(self):
        ids = set(self.selected_ids)
        return [component for component in self.project.components if component.identifier in ids]

    def filter_rows(self, text):
        words = text.casefold().split()
        for row, content in enumerate(self.search_values):
            self.table.setRowHidden(row, not all(word in content for word in words))
        if not self.checkable:
            current = self.table.currentRow()
            if current < 0 or self.table.isRowHidden(current):
                first = next((row for row in range(self.table.rowCount()) if not self.table.isRowHidden(row)), -1)
                self.table.setCurrentCell(first, 0)
            self.show_details()
        self.update_selection()

    @property
    def current_component(self):
        row = self.table.currentRow()
        return (self.project.components[row] if 0 <= row < len(self.project.components)
                and not self.table.isRowHidden(row) else None)

    def show_details(self, *_args):
        component = self.current_component
        self.nested_button.setEnabled(component is not None and component.model == '工程模块')
        if component is None:
            self.detail.setPlainText('选择内部预计项查看完整参数、工作应力与输入依据。')
            return
        escape = html.escape
        content = [f'<p><b>{escape(component.name)}</b>　｜　计算模型：{escape(disclosed_model(component))}'
                   f'　｜　国产：{"是" if component.is_domestic else "否"}'
                   f'　｜　数量：{number_text(component.quantity) if component.quantity is not None else "待填写"}</p>']
        if component.model not in ('已知单件失效率', '工程模块'):
            content.append('<h3>器件参数与工作应力</h3><p>' +
                           escape(parameter_text(component.model, component.parameters)).replace('；', '<br>') + '</p>')
        if component.disclosure:
            content.append('<h3>公开参数档位</h3>')
            ranges = component.disclosure['parameter_ranges']
            content.append('<p>' + ('<br>'.join(
                f'{escape(row["name"])}：{escape(row["range"])}　[{escape(row["source"])}]'
                for row in ranges) or '内部') + '</p><p>工作应力：'
                           + escape(disclosure_stress_text(component.disclosure))
                           + '；按保存的工况失效率计算。</p>')
        if component.stress_variables:
            content.append('<h3>应力变量</h3><p>' + '<br>'.join(
                f'{escape(key)} = {escape(str(value))}' for key, value in component.stress_variables.items()) + '</p>')
        displayed = {field.key for field in MODELS[component.model]['fields']
                     if field.scope == 'device' and field.active(component.parameters)}
        additional = {key: value for key, value in component.parameters.items() if key not in displayed}
        if additional:
            content.append('<p>其他保存参数：<br>' + '<br>'.join(
                f'{escape(key)} = {escape(number_text(value))}' for key, value in additional.items()) + '</p>')
        rates = component.condition_rate_entries()
        if rates:
            content.append('<h3>各工况失效率</h3><table cellpadding="4"><tr><th>环境类别</th>'
                           '<th>环境温度 Ta（℃）</th><th>失效率（10⁻⁶/h）</th></tr>')
            for rate in rates:
                content.append(f'<tr><td>{escape(environment_text(rate["environment"]))}</td>'
                               f'<td>{number_text(rate["ambient_temperature_C"])}</td>'
                               f'<td>{number_text(rate["rate"])}</td></tr>')
            content.append('</table>')
        content.append('<h3>输入依据</h3><p>' + escape(component.reference or (
            '内部' if component.disclosure else '未填写输入依据')).replace('\n', '<br>') + '</p>')
        if component.bom:
            content.append('<h3>BOM 原始资料</h3><p>' + '<br>'.join(
                f'{escape(key)}：{escape(value)}' for key, value in component.bom.get('row', {}).items()) +
                '<br>来源：' + escape(component.bom.get('file_name', '')) + '</p>')
        if component.module_source:
            source = component.module_source
            content.append(f'<h3>内部模块来源</h3><p>{escape(source["project"]["name"])} · '
                           f'{escape(source["file_name"])}<br>SHA-256：{escape(source["sha256"])}</p>')
        self.detail.setHtml(''.join(content))

    def view_nested_module(self):
        component = self.current_component
        if component is not None and component.model == '工程模块':
            dialog = ModuleDialog(component, self, read_only=True)
            dialog.exec()

    def set_checks(self, *, visible_only, checked):
        blocked = self.table.blockSignals(True)
        try:
            for row in range(self.table.rowCount()):
                if not visible_only or not self.table.isRowHidden(row):
                    self.table.item(row, 0).setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        finally:
            self.table.blockSignals(blocked)
        self.update_selection()

    def select_visible(self):
        self.set_checks(visible_only=True, checked=True)

    def clear_selection(self):
        self.set_checks(visible_only=False, checked=False)

    def update_selection(self, *_args):
        visible = sum(not self.table.isRowHidden(row) for row in range(self.table.rowCount()))
        if not self.checkable:
            self.summary.setText(f'内部预计项 {len(self.project.components)} 项 · 当前显示 {visible} 项 · 数量 {quantity_text(self.project.components)}')
            return
        selected = self.selected_components
        hidden = sum(self.table.isRowHidden(row) and self.table.item(row, 0).checkState() == Qt.CheckState.Checked
                     for row in range(self.table.rowCount()))
        rates = sum(len(component.condition_rate_entries()) for component in selected)
        text = f'已选 {len(selected)} 项 · 数量 {quantity_text(selected)} · 当前显示 {visible} 项'
        if hidden:
            text += f' · 其中 {hidden} 个选中项在筛选范围外'
        if rates:
            text += f'\n包含 {rates} 条按环境类别与温度匹配的失效率记录。'
        self.summary.setText(text)
        self.selection_changed.emit()


def name_quantity_form(name, quantity):
    form = QFormLayout()
    name_edit = QLineEdit(name)
    name_edit.setPlaceholderText('填写模块名称或位号')
    quantity_box = QSpinBox()
    quantity_box.setRange(1, 1_000_000)
    quantity_box.setValue(quantity)
    form.addRow('模块名称 / 位号', name_edit)
    form.addRow('模块数量', quantity_box)
    return form, name_edit, quantity_box


class ProjectComponentImportDialog(QDialog):
    def __init__(self, source_project, source_path, parent=None, *, source_sha256=None):
        super().__init__(parent)
        self.source_project, self.source_path, self.source_sha256 = source_project, source_path, source_sha256
        self.result_components = None
        self.setWindowTitle('从工程引入')
        fit_window(self, 1040, 710)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label('从其他工程引入', 'Title'))
        self.source_label = plain_label(f'来源：{source_project.name} · {Path(source_path).name}')
        layout.addWidget(self.source_label)
        form = QFormLayout()
        self.mode_box = QComboBox()
        self.mode_box.addItem('整份工程作为一个模块', 'module')
        self.mode_box.addItem('选择内部元器件', 'components')
        form.addRow('引入方式', self.mode_box)
        layout.addLayout(form)
        self.pages = QStackedWidget()
        module_page = QWidget()
        module_layout = QVBoxLayout(module_page)
        module_layout.setContentsMargins(0, 0, 0, 0)
        form, self.name_edit, self.quantity_box = name_quantity_form(source_project.name, 1)
        module_layout.addLayout(form)
        module_layout.addWidget(plain_label('按一个模块计入当前工程，保留内部预计项和源工况的完整快照。'))
        self.condition_table = ConditionPreviewTable(source_project)
        module_layout.addWidget(self.condition_table, 1)
        available = self.condition_table.available_count
        unavailable = len(source_project.conditions) - available
        text = f'{available} 个工况可用'
        if unavailable:
            text += f'，另有 {unavailable} 个工况不可用。'
        if not available:
            text += '请先补齐源工程，使至少一个勾选工况能够完整计算。'
        if self.condition_table.has_calculation_error:
            text += '存在合计错误，请先修正或取消该源工况的计算，再引入模块。'
        self.module_status = plain_label(text)
        module_layout.addWidget(self.module_status)
        module_layout.addWidget(plain_label('模块失效率仅在环境类别和温度相同时使用。快照随本工程保存，不随源文件变化。'))
        self.pages.addWidget(module_page)
        self.component_picker = ComponentSelection(source_project)
        self.pages.addWidget(self.component_picker)
        layout.addWidget(self.pages, 1)
        layout.addWidget(plain_label('当前工程的预计环境保持不变。内部元器件引入后作为独立预计项保留，同名或同位号项不会互相覆盖。'))
        buttons = QDialogButtonBox()
        self.import_button = buttons.addButton('引入模块', QDialogButtonBox.ButtonRole.AcceptRole)
        self.import_button.setObjectName('Primary')
        buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.accept_import)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.mode_box.currentIndexChanged.connect(self.update_mode)
        self.name_edit.textChanged.connect(self.update_button)
        self.component_picker.selection_changed.connect(self.update_button)
        self.update_mode()

    def update_mode(self, *_args):
        self.pages.setCurrentIndex(self.mode_box.currentIndex())
        self.update_button()

    def update_button(self, *_args):
        module = self.mode_box.currentData() == 'module'
        self.import_button.setText('引入模块' if module else '引入选中项')
        self.import_button.setEnabled(bool(self.name_edit.text().strip()) and self.condition_table.available_count > 0
                                      and not self.condition_table.has_calculation_error
                                      if module else bool(self.component_picker.selected_ids))

    def accept_import(self):
        if not self.import_button.isEnabled():
            return
        try:
            if self.mode_box.currentData() == 'module':
                self.result_components = [create_project_module(
                    self.source_project, name=self.name_edit.text().strip(), quantity=self.quantity_box.value(),
                    source_path=self.source_path, expected_sha256=self.source_sha256)]
            else:
                self.result_components = clone_components(self.source_project, self.component_picker.selected_ids,
                                                          source_path=self.source_path)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, '无法引入', str(error))
            return
        super().accept()


class ModuleDialog(QDialog):
    def __init__(self, component, parent=None, *, read_only=False):
        super().__init__(parent)
        self.original = component
        self.read_only = read_only
        self.result_component = None
        source_project = module_project(component)
        self.setWindowTitle('工程模块')
        fit_window(self, 1040, 710)
        layout = QVBoxLayout(self)
        layout.addWidget(plain_label('工程模块', 'Title'))
        form, self.name_edit, self.quantity_box = name_quantity_form(component.name, component.quantity or 1)
        self.name_edit.setEnabled(not read_only)
        self.quantity_box.setEnabled(not read_only)
        layout.addLayout(form)
        source = component.module_source
        self.source_label = plain_label(f'来源：{source_project.name} · {source["file_name"] or "未提供文件名"}')
        layout.addWidget(self.source_label)
        self.hash_label = plain_label('SHA-256：' + (source['sha256'] or '未提供文件摘要'))
        layout.addWidget(self.hash_label)
        layout.addWidget(plain_label('源工程快照随本工程保存，不随源文件变化。内部预计项和源工况为只读；模块按匹配的环境类别及温度计算。'))
        self.tabs = QTabWidget()
        self.condition_table = ConditionPreviewTable(source_project)
        self.tabs.addTab(self.condition_table, '源工况结果')
        self.component_picker = ComponentSelection(source_project, checkable=False)
        self.tabs.addTab(self.component_picker, '内部预计项')
        layout.addWidget(self.tabs, 1)
        buttons = QDialogButtonBox()
        self.save_button = None
        if not read_only:
            self.save_button = buttons.addButton('保存', QDialogButtonBox.ButtonRole.AcceptRole)
            self.save_button.setObjectName('Primary')
            buttons.accepted.connect(self.accept_module)
            self.name_edit.textChanged.connect(lambda text: self.save_button.setEnabled(bool(text.strip())))
        buttons.addButton('关闭' if read_only else '取消', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept_module(self):
        if self.read_only or not self.name_edit.text().strip():
            return
        result = copy.deepcopy(self.original)
        result.name, result.quantity = self.name_edit.text().strip(), self.quantity_box.value()
        try:
            result.validate()
        except ValueError as error:
            QMessageBox.warning(self, '无法保存', str(error))
            return
        self.result_component = result
        super().accept()
