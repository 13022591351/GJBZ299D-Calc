"""Explicit input derivation: inputs remain editable and are saved in the project."""

import copy

from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .input_sources import (
    GATE_KEYS,
    RATE_KEYS,
    TEMPERATURE_KEYS,
    gate_count,
    junction_temperature,
    load_source,
    thermal_packages,
    validate_sources,
)
from .number_edit import NumberEdit
from .numeric import number_text
from .standards import MODELS, strip_cell_ids


def source_kind(spec, model):
    if spec.key in GATE_KEYS and '门数' in spec.label:
        return 'gate_count'
    if spec.key in TEMPERATURE_KEYS and MODELS[model].category == 'IC':
        return 'junction_temperature'
    if spec.key in RATE_KEYS:
        return 'project_rate'
    if spec.key == 'mounted_ic_rate':
        return 'mounted_microcircuits'
    return None


class InputSourceDialog(QDialog):
    def __init__(self, component, spec, parent=None, *, environment='GB'):
        super().__init__(parent)
        self.component, self.spec = component, spec
        self.environment = environment or 'GB'
        self.kind = source_kind(spec, component.model)
        self.result_source = None
        self.source = copy.deepcopy(component.input_sources.get(spec.key, {}))
        self.document = self.source.get('source')
        self.setWindowTitle(spec.label + ' · 辅助计算')
        self.resize(650, 450)
        layout = QVBoxLayout(self)
        self.method = QComboBox()
        self.method.addItem('直接填写参数', None)
        self.method.addItem({'gate_count': '等效门数换算 [5.1.1.3]',
                             'junction_temperature': '管壳温度、热阻与功耗求结温 [式（2）]',
                             'project_rate': '关联组成元器件工程',
                             'mounted_microcircuits': '关联外贴微电路工程 [表40 / 表C.25]'}[self.kind], self.kind)
        layout.addWidget(self.method)
        self.body = QWidget()
        self.form = QFormLayout(self.body)
        self.inputs = {}
        self.input_titles = {}
        values = self.source.get('inputs', {})
        if self.kind == 'gate_count':
            self.combo('mode', '换算依据', [('晶体管数', 'transistors'), ('触发器数量', 'flip_flops')], values)
            self.combo('technology', '工艺', [(v, v) for v in ('双极型', 'CMOS', '其他MOS')], values)
            self.numeric('transistors', '晶体管数', values)
            self.numeric('jk_rs', 'J-K / R-S 触发器数量', values)
            self.numeric('t_d', 'T / D 触发器数量', values)
        elif self.kind == 'junction_temperature':
            self.combo('case_source', '管壳温度来源', [('用户确定', 'specified'), ('表24通用值', 'table24')], values)
            self.numeric('case_temperature_C', '管壳温度 Tc [℃]', values)
            self.combo('resistance_source', '热阻来源', [('规格书或实测值', 'specified'), ('表25参考值', 'table25')], values)
            self.numeric('thermal_resistance_C_W', '结到管壳热阻 [℃/W]', values)
            self.combo('package', '表25封装形式', [(v, v) for v in thermal_packages()], values)
            self.numeric('power_W', '实际耗散功率 P [W]', values)
            note = QLabel('Tc 是管壳温度，不能用工程环境温度直接代替。表24、25属于国产微电路条款；进口器件请填写实际管壳温度及热阻。')
            note.setWordWrap(True)
            self.form.addRow(note)
        else:
            self.file_label = QLabel(self.document['file_name'] if self.document else '尚未选择组成工程')
            self.file_label.setWordWrap(True)
            self.form.addRow(self.file_label)
            button = QPushButton('选择工程 [JSON / XLS / XLSX]…')
            button.clicked.connect(self.choose_project)
            self.form.addRow(button)
            note = QLabel('保存工程快照，按当前环境、温度重新计算；文件后续变化不会静默替换快照。' if self.kind == 'project_rate' else
                          '组成工程须含适用的半导体集成电路。各芯片须设置结温辅助计算，按 Tc=35℃及规定修正系数计算。')
            note.setWordWrap(True)
            self.form.addRow(note)
        layout.addWidget(self.body)
        self.preview = QLabel('')
        self.preview.setWordWrap(True)
        layout.addWidget(self.preview)
        layout.addStretch()
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save_source)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.method.setCurrentIndex(1 if self.source else 0)
        self.method.currentIndexChanged.connect(self.refresh)
        for field in self.inputs.values():
            (field.currentIndexChanged if isinstance(field, QComboBox) else field.textChanged).connect(self.refresh)
        self.refresh()

    def combo(self, key, title, options, values):
        field = QComboBox()
        for label, value in options:
            field.addItem(label, value)
        if key in values:
            idx = field.findData(values[key])
            field.setCurrentIndex(idx if idx >= 0 else 0)
        self.inputs[key] = field
        self.form.addRow(title, field)

    def numeric(self, key, title, values):
        field = NumberEdit()
        if key in values:
            field.set_number(values[key])
        self.inputs[key] = field
        self.input_titles[key] = title
        self.form.addRow(title, field)

    def choose_project(self):
        path, _ = QFileDialog.getOpenFileName(self, '选择组成工程', '', '工程 (*.json *.xls *.xlsx)')
        if path:
            try:
                self.document = load_source(path)
                self.file_label.setText(self.document['file_name'])
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, '工程无法读取', str(error))
            self.refresh()

    def selected_inputs(self):
        if self.kind == 'gate_count':
            keys = ('mode', 'technology', 'transistors') if self.inputs['mode'].currentData() == 'transistors' else ('mode', 'jk_rs', 't_d')
        else:
            keys = ['case_source', 'resistance_source', 'power_W']
            keys.append('case_temperature_C' if self.inputs['case_source'].currentData() == 'specified' else None)
            keys.append('thermal_resistance_C_W' if self.inputs['resistance_source'].currentData() == 'specified' else 'package')
        values = {}
        for key in keys:
            if not key:
                continue
            try:
                values[key] = (self.inputs[key].currentData() if isinstance(self.inputs[key], QComboBox)
                               else self.inputs[key].value())
            except ValueError as error:
                raise ValueError('请填写有效的' + self.input_titles[key]) from error
        return values

    def candidate(self):
        if self.method.currentData() is None:
            return None
        if self.kind in ('project_rate', 'mounted_microcircuits'):
            return {'kind': self.kind, 'source': copy.deepcopy(self.document)}
        return {'kind': self.kind, 'inputs': self.selected_inputs()}

    def refresh(self, *_args):
        self.body.setEnabled(self.method.currentData() is not None)
        if self.kind == 'gate_count':
            transistors = self.inputs['mode'].currentData() == 'transistors'
            for key in ('technology', 'transistors', 'jk_rs', 't_d'):
                self.form.setRowVisible(self.inputs[key], transistors == (key in ('technology', 'transistors')))
        elif self.kind == 'junction_temperature':
            self.form.setRowVisible(self.inputs['case_temperature_C'], self.inputs['case_source'].currentData() == 'specified')
            specified = self.inputs['resistance_source'].currentData() == 'specified'
            self.form.setRowVisible(self.inputs['thermal_resistance_C_W'], specified)
            self.form.setRowVisible(self.inputs['package'], not specified)
        try:
            candidate = self.candidate()
            if not candidate:
                self.preview.setText('解除辅助计算后，参数恢复为直接填写。')
            elif self.kind in ('gate_count', 'junction_temperature'):
                value, trace = (gate_count(candidate['inputs']) if self.kind == 'gate_count' else
                                junction_temperature(candidate['inputs'], self.environment))
                self.preview.setText(f'[计算值 {number_text(value)}]\n{strip_cell_ids(trace)}')
            else:
                self.preview.setText('关联后，元器件计算过程将显示组成工程的计算结果。')
        except (ValueError, OverflowError) as error:
            self.preview.setText(str(error))

    def save_source(self):
        try:
            candidate = self.candidate()
            component = copy.deepcopy(self.component)
            if candidate is None:
                component.input_sources.pop(self.spec.key, None)
            else:
                component.input_sources[self.spec.key] = candidate
            validate_sources(component)
            self.result_source = candidate
        except (ValueError, OverflowError) as error:
            QMessageBox.warning(self, '输入需要调整', str(error))
            return
        self.accept()
