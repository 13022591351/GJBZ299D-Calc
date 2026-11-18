"""Independent, persistable Appendix A / E / F / D analysis workspace."""

import copy
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .analysis_methods import (
    PHYSICS_TOOLS,
    STATES,
    bayesian_update,
    failure_mode_catalog,
    mission_profile,
    mode_contributions,
    physics_calculate,
)
from .analysis_methods.common import OUTPUT_LABELS
from .analysis_methods.state import validate_analysis_state
from .analysis_methods.workspace import (
    configured_analyses,
    project_condition_sources,
    recompute_analyses,
    resolve_phase_sources,
)
from .environment_editor import ConditionNameDelegate
from .numeric import number_text
from .table_layout import configure_table_combo, resizable_columns
from .theme import section_label


def _combo(values, *, in_table=False):
    box = QComboBox()
    for key, title in values:
        box.addItem(title, key)
    if in_table:
        configure_table_combo(box, min_chars=4)
    else:
        box.setMinimumWidth(150)
    return box


def _text(value):
    return number_text(value) if type(value) in (float, int) else str(value or '')


def _vline():
    line = QFrame()
    line.setObjectName('VLine')
    line.setFrameShape(QFrame.Shape.VLine)
    return line


def _table(headers, widths, name, *, min_widths=None, fixed_columns=()):
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.setAlternatingRowColors(True)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.verticalHeader().hide()
    table.verticalHeader().setDefaultSectionSize(42)
    table.setItemDelegate(AnalysisCellDelegate(table))
    resizable_columns(table, name, widths, min_widths=min_widths, fixed_columns=fixed_columns)
    return table


def _cell(table, row, col):
    delegate = table.itemDelegate()
    if delegate.editor is not None and delegate.index.isValid() and delegate.index.row() == row and delegate.index.column() == col:
        return delegate.editor.text()
    item = table.item(row, col)
    return item.text() if item else ''


class AnalysisCellDelegate(ConditionNameDelegate):
    """Reuse table edit tracking and notify the host when an edit is cancelled."""


class AnalysisTab(QWidget):
    changed = pyqtSignal()

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self.last_result = None
        self._updating = False
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(8, 6, 8, 6)
        self.layout.setSpacing(6)
        self.description = section_label(text)
        self.layout.addWidget(self.description)
        self.form_area = QVBoxLayout()
        self.form_area.setSpacing(6)
        self.layout.addLayout(self.form_area)
        self.calculate_button = QPushButton('开始计算')
        self.calculate_button.setObjectName('Primary')
        self.calculate_button.setMinimumWidth(108)
        self.calculate_button.clicked.connect(self.calculate)
        self.output = QTextBrowser()
        self.output.setAcceptDrops(False)
        self.output.document().setDocumentMargin(8)
        self.output.setMinimumHeight(180)
        self.layout.addWidget(self.output, 1)

    def invalidate(self, *_args):
        if self._updating:
            return
        self.last_result = None
        self.output.setPlainText('输入已修改，请点击“开始计算”更新结果。')
        self.changed.emit()

    def calculate(self):
        try:
            result = self.evaluate()
        except (ValueError, TypeError, KeyError, ArithmeticError) as error:
            self.last_result = None
            self.output.setPlainText('未完成计算：' + str(error))
            return None
        self.last_result = result
        lines = [result.title, result.reference, '']
        lines.extend(f'{OUTPUT_LABELS.get(key, key)}：{number_text(value)} [{result.units[key]}]'
                     for key, value in result.outputs.items())
        lines.extend(['', '计算过程', *result.steps, '', *result.notes])
        self.output.setPlainText('\n'.join(lines))
        return result


class MissionTab(AnalysisTab):
    def __init__(self, parent=None):
        super().__init__('按明确任务剖面分别填写工作、非工作阶段。失效率须对应同一预计对象及各阶段环境条件；阶段时长自动转占空比，占空比总和须为 1。', parent)
        self.basis = _combo([('duration', '按阶段时长（h）'), ('duty', '按占空比（0～1）')])
        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addWidget(self.basis)
        add = QPushButton('＋ 添加阶段')
        add.clicked.connect(lambda: self.add_phase())
        remove = QPushButton('－ 移除阶段')
        remove.clicked.connect(self.remove_phase)
        actions.addWidget(add)
        actions.addWidget(remove)
        actions.addWidget(_vline())

        actions.addWidget(section_label('可用工况', wrap=False))
        self.sources = _combo([])
        self.sources.setMinimumWidth(220)
        self.sources.setMaximumWidth(360)
        actions.addWidget(self.sources, 1)
        self.source_button = QPushButton('加入来源工况')
        self.source_button.clicked.connect(self.add_selected_source)
        actions.addWidget(self.source_button)
        self.import_button = QPushButton('引入工程工况…')
        self.import_button.clicked.connect(self.import_source)
        actions.addWidget(self.import_button)
        actions.addStretch()
        actions.addWidget(self.calculate_button)
        self.form_area.addLayout(actions)

        self.table = _table(
            ['阶段名称', '阶段状态', '时长（h）', '阶段失效率（10⁻⁶/h）', '环境条件', '环境温度（℃）', '失效率来源'],
            [150, 135, 115, 175, 145, 130, 240],
            'analysis-mission',
            min_widths=[130, 120, 105, 145, 110, 110, 160],
        )
        self.table.setMinimumHeight(210)
        self.form_area.addWidget(self.table)
        self.basis.currentIndexChanged.connect(self.basis_changed)
        self.table.itemChanged.connect(self._on_table_item_changed)
        self.table.itemDelegate().draft_changed.connect(self.invalidate)
        self.external_sources = []
        self.current_sources = []
        self.source_issues = {}
        self.add_phase()

    def _source_lookup(self):
        return {
            (item.get('id'), item.get('prediction_state')): item
            for item in self.current_sources
            if item.get('id') and item.get('prediction_state') in STATES
        }

    def _apply_current_source_to_row(self, row, source, *, target_state=None):
        item0 = self.table.item(row, 0)
        if item0 is None:
            return
        if source is not None:
            new_state = source['prediction_state']
            updates = {
                3: _text(source['rate']),
                4: _text(source['environment']),
                5: _text(source['ambient_temperature_C']),
                6: source.get('project_name', '当前工程') + ' / ' + source['name'],
            }
            for col, text in updates.items():
                cell = self.table.item(row, col)
                if cell is None:
                    self.table.setItem(row, col, QTableWidgetItem(text))
                else:
                    cell.setText(text)
            cur_name = item0.text().strip()
            other_state = 'nonoperating' if new_state == 'working' else 'working'
            if cur_name in (source['name'], f'{source["name"]}（{STATES[other_state]}）'):
                item0.setText(f'{source["name"]}（{STATES[new_state]}）')
            item0.setData(Qt.ItemDataRole.UserRole, {**copy.deepcopy(source), 'scope': 'current'})
        else:
            cell3 = self.table.item(row, 3)
            if cell3 is not None:
                cell3.setText('')
            snapshot = item0.data(Qt.ItemDataRole.UserRole)
            if isinstance(snapshot, dict) and target_state in STATES:
                other_state = 'nonoperating' if target_state == 'working' else 'working'
                snap_name = snapshot.get('name', '')
                if snap_name and item0.text().strip() == f'{snap_name}（{STATES[other_state]}）':
                    item0.setText(f'{snap_name}（{STATES[target_state]}）')
                item0.setData(Qt.ItemDataRole.UserRole, {**snapshot, 'prediction_state': target_state})

    def _phase_state_changed(self, box):
        if self._updating:
            return
        target_row = next(
            (r for r in range(self.table.rowCount()) if self.table.cellWidget(r, 1) is box),
            -1,
        )
        if target_row >= 0:
            item0 = self.table.item(target_row, 0)
            snapshot = item0.data(Qt.ItemDataRole.UserRole) if item0 is not None else None
            if isinstance(snapshot, dict) and snapshot.get('scope') == 'current':
                target_state = box.currentData()
                match = self._source_lookup().get((snapshot.get('id'), target_state))
                prev, self._updating = self._updating, True
                try:
                    blocked = self.table.blockSignals(True)
                    try:
                        self._apply_current_source_to_row(target_row, match, target_state=target_state)
                    finally:
                        self.table.blockSignals(blocked)
                finally:
                    self._updating = prev
        self.invalidate()

    def _on_table_item_changed(self, item):
        if self._updating or item is None:
            return
        if item.column() in (3, 4, 5):
            row = item.row()
            item0 = self.table.item(row, 0)
            if item0 is not None and item0.data(Qt.ItemDataRole.UserRole) is not None:
                blocked = self.table.blockSignals(True)
                try:
                    item0.setData(Qt.ItemDataRole.UserRole, None)
                    src_item = self.table.item(row, 6)
                    if src_item is not None and ' / ' in src_item.text():
                        src_item.setText('手工输入')
                finally:
                    self.table.blockSignals(blocked)
        self.invalidate()

    def _sync_current_phase_rows(self):
        by_state = self._source_lookup()
        synced = False
        prev, self._updating = self._updating, True
        try:
            blocked = self.table.blockSignals(True)
            try:
                for row in range(self.table.rowCount()):
                    item0 = self.table.item(row, 0)
                    widget = self.table.cellWidget(row, 1)
                    snapshot = item0.data(Qt.ItemDataRole.UserRole) if item0 is not None else None
                    if not isinstance(snapshot, dict) or snapshot.get('scope') != 'current' or widget is None:
                        continue
                    row_state = widget.currentData()
                    source = by_state.get((snapshot.get('id'), row_state))
                    cell_text = [
                        self.table.item(row, col).text() if self.table.item(row, col) is not None else ''
                        for col in range(7)
                    ]
                    if source is not None:
                        expected_snapshot = {**copy.deepcopy(source), 'scope': 'current'}
                        expected_source_text = source.get('project_name', '当前工程') + ' / ' + source['name']
                        if (
                            snapshot != expected_snapshot
                            or cell_text[3] != _text(source['rate'])
                            or cell_text[4] != _text(source['environment'])
                            or cell_text[5] != _text(source['ambient_temperature_C'])
                            or cell_text[6] != expected_source_text
                        ):
                            self._apply_current_source_to_row(row, source)
                            synced = True
                    else:
                        if snapshot.get('prediction_state') != row_state or cell_text[3] != '':
                            self._apply_current_source_to_row(row, None, target_state=row_state)
                            synced = True
            finally:
                self.table.blockSignals(blocked)
        finally:
            self._updating = prev
        return synced

    def basis_changed(self):
        prev, self._updating = self._updating, True
        try:
            self.table.itemDelegate().commit_pending()
            self.table.horizontalHeaderItem(2).setText('时长（h）' if self.basis.currentData() == 'duration' else '占空比（0～1）')
            blocked = self.table.blockSignals(True)
            try:
                for row in range(self.table.rowCount()):
                    item = self.table.item(row, 2)
                    if item is not None:
                        item.setText('')
            finally:
                self.table.blockSignals(blocked)
        finally:
            self._updating = prev
        self.invalidate()

    def add_phase(self, phase=None):
        phase = phase or {}
        prev, self._updating = self._updating, True
        try:
            self.table.itemDelegate().commit_pending()
            blocked = self.table.blockSignals(True)
            try:
                row = self.table.rowCount()
                self.table.insertRow(row)
                key = 'duration_hours' if self.basis.currentData() == 'duration' else 'duty_cycle'
                values = [phase.get('name', f'阶段 {row + 1}'), '', phase.get(key, ''), phase.get('rate', ''),
                          phase.get('environment', ''), phase.get('ambient_temperature_C', ''), phase.get('source', '手工输入')]
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(_text(value)))
                state = _combo(STATES.items(), in_table=True)
                state.setCurrentIndex(max(0, state.findData(phase.get('state', 'working'))))
                state.currentIndexChanged.connect(lambda _idx, box=state: self._phase_state_changed(box))
                self.table.setCellWidget(row, 1, state)
                self.table.item(row, 0).setData(Qt.ItemDataRole.UserRole, phase.get('source_snapshot'))
            finally:
                self.table.blockSignals(blocked)
        finally:
            self._updating = prev
        self.invalidate()

    def remove_phase(self):
        prev, self._updating = self._updating, True
        removed = False
        try:
            self.table.itemDelegate().commit_pending()
            row = self.table.currentRow()
            if row >= 0:
                self.table.removeRow(row)
                removed = True
        finally:
            self._updating = prev
        if removed:
            self.invalidate()

    def clear_condition_sources(self):
        self.current_sources = []
        self.source_issues = {}
        self.refresh_sources()

    def set_condition_sources(self, sources, *, source_issues=None):
        new_issues = copy.deepcopy(source_issues) if source_issues is not None else {}
        changed = self.current_sources != sources or self.source_issues != new_issues
        self.current_sources = copy.deepcopy(sources)
        self.source_issues = new_issues
        self.refresh_sources()
        synced = self._sync_current_phase_rows()
        if (changed or synced) and any((phase.get('source_snapshot') or {}).get('scope') == 'current'
                                       for phase in self.state()['phases']):
            self.calculate()

    def refresh_sources(self):
        prev_data = self.sources.currentData()
        prev_key = (
            (prev_data.get('scope'), prev_data.get('id'), prev_data.get('prediction_state'))
            if isinstance(prev_data, dict)
            else None
        )
        entries = []
        combined = (
            [('current', item) for item in self.current_sources]
            + [('external', item) for item in self.external_sources]
        )
        for scope, source in combined:
            if source.get('rate') is None or source.get('prediction_state') not in STATES:
                continue
            text = (
                f'{source["name"]} · {STATES[source["prediction_state"]]} · '
                f'{source["environment"]} / {_text(source["ambient_temperature_C"])}℃'
            )
            entries.append((text, {**copy.deepcopy(source), 'scope': scope}))

        existing = [(self.sources.itemText(i), self.sources.itemData(i)) for i in range(self.sources.count())]
        if existing != entries:
            blocked = self.sources.blockSignals(True)
            try:
                self.sources.clear()
                target_index = 0
                for idx, (text, data) in enumerate(entries):
                    self.sources.addItem(text, data)
                    if prev_key is not None and (
                        (data.get('scope'), data.get('id'), data.get('prediction_state')) == prev_key
                        or (data.get('id'), data.get('prediction_state')) == prev_key[1:]
                    ):
                        target_index = idx
                if entries:
                    self.sources.setCurrentIndex(target_index)
            finally:
                self.sources.blockSignals(blocked)
        self.source_button.setEnabled(self.sources.count() > 0)

    def add_selected_source(self):
        source = self.sources.currentData()
        if not source:
            return
        self.add_phase({'name': f'{source["name"]}（{STATES[source["prediction_state"]]}）',
                        'state': source['prediction_state'], 'rate': source['rate'],
                        'environment': source['environment'], 'ambient_temperature_C': source['ambient_temperature_C'],
                        'source': source.get('project_name', '当前工程') + ' / ' + source['name'],
                        'source_snapshot': source})

    def import_source(self):
        path, _ = QFileDialog.getOpenFileName(self, '引入任务阶段的来源工程', '', 'JSON 工程 (*.json)')
        if not path:
            return
        try:
            from .shared_io import load_supplier_project
            project = load_supplier_project(Path(path))
            sources = [{**item, 'scope': 'external'} for item in project_condition_sources(project)]
            if not sources:
                raise ValueError('来源工程没有计算完整的已启用工况')
            self.external_sources.extend(sources)
            self.refresh_sources()
            self.invalidate()
        except (ValueError, OSError, TypeError, KeyError) as error:
            self.output.setPlainText('未引入工况：' + str(error))

    def state(self):
        key = 'duration_hours' if self.basis.currentData() == 'duration' else 'duty_cycle'
        phases = []
        for row in range(self.table.rowCount()):
            widget = self.table.cellWidget(row, 1)
            item0 = self.table.item(row, 0)
            state_val = widget.currentData() if widget is not None else 'working'
            snapshot = copy.deepcopy(item0.data(Qt.ItemDataRole.UserRole)) if item0 is not None else None
            phases.append({'name': _cell(self.table, row, 0), 'state': state_val,
                           key: _cell(self.table, row, 2), 'rate': _cell(self.table, row, 3),
                           'environment': _cell(self.table, row, 4), 'ambient_temperature_C': _cell(self.table, row, 5),
                           'source': _cell(self.table, row, 6),
                           'source_snapshot': snapshot})
        return {'basis': self.basis.currentData(), 'phases': phases, 'external_sources': copy.deepcopy(self.external_sources)}

    def restore(self, state):
        prev, self._updating = self._updating, True
        try:
            self.table.itemDelegate().commit_pending()
            self.table.setRowCount(0)
            self.basis.setCurrentIndex(max(0, self.basis.findData(state.get('basis', 'duration'))))
            self.external_sources = copy.deepcopy(state.get('external_sources', []))
            self.refresh_sources()
            for phase in state.get('phases', []):
                self.add_phase(phase)
            if self.current_sources:
                self._sync_current_phase_rows()
        finally:
            self._updating = prev

    def evaluate(self):
        data = self.state()
        return mission_profile(
            resolve_phase_sources(
                data['phases'], self.current_sources, source_issues=self.source_issues
            ),
            basis=data['basis'],
        )


class BayesTab(AnalysisTab):
    def __init__(self, parent=None):
        super().__init__('结合初始预计值和测试、现场使用数据修正失效率。累积小时数为元器件小时；非加速数据的 AF 填 1。', parent)
        self.initial = QLineEdit()
        self.initial.setMaximumWidth(170)
        self.prior = QLineEdit('0.5')
        self.prior.setMaximumWidth(110)

        bar = QHBoxLayout()
        bar.setSpacing(6)
        bar.addWidget(section_label('初始 λ1（10⁻⁶/h）'))
        bar.addWidget(self.initial)
        bar.addSpacing(8)
        bar.addWidget(section_label('初始失效数 a0'))
        bar.addWidget(self.prior)
        bar.addWidget(_vline())
        add = QPushButton('＋ 添加数据组')
        add.clicked.connect(lambda: self.add_observation())
        remove = QPushButton('－ 移除数据组')
        remove.clicked.connect(self.remove_observation)
        bar.addWidget(add)
        bar.addWidget(remove)
        bar.addStretch()
        bar.addWidget(self.calculate_button)
        self.form_area.addLayout(bar)

        self.table = _table(
            ['数据来源', '失效数', '累积元器件小时数（h）', '加速因子 AF'],
            [260, 120, 220, 160],
            'analysis-bayes',
            min_widths=[140, 90, 150, 110],
        )
        self.table.setMinimumHeight(190)
        self.form_area.addWidget(self.table)
        self.initial.textChanged.connect(self.invalidate)
        self.prior.textChanged.connect(self.invalidate)
        self.table.itemChanged.connect(self.invalidate)
        self.table.itemDelegate().draft_changed.connect(self.invalidate)
        self.add_observation()

    def add_observation(self, row=None):
        row = row or {}
        prev, self._updating = self._updating, True
        try:
            self.table.itemDelegate().commit_pending()
            blocked = self.table.blockSignals(True)
            try:
                index = self.table.rowCount()
                self.table.insertRow(index)
                for col, key in enumerate(('source', 'failures', 'component_hours', 'acceleration_factor')):
                    self.table.setItem(index, col, QTableWidgetItem(_text(row.get(key, 1 if key == 'acceleration_factor' else ''))))
            finally:
                self.table.blockSignals(blocked)
        finally:
            self._updating = prev
        self.invalidate()

    def remove_observation(self):
        prev, self._updating = self._updating, True
        removed = False
        try:
            self.table.itemDelegate().commit_pending()
            if self.table.currentRow() >= 0:
                self.table.removeRow(self.table.currentRow())
                removed = True
        finally:
            self._updating = prev
        if removed:
            self.invalidate()

    def state(self):
        return {'initial_rate': self.initial.text(), 'prior_failures': self.prior.text(),
                'observations': [{key: _cell(self.table, row, col)
                                  for col, key in enumerate(('source', 'failures', 'component_hours', 'acceleration_factor'))}
                                 for row in range(self.table.rowCount())]}

    def restore(self, state):
        prev, self._updating = self._updating, True
        try:
            self.table.itemDelegate().commit_pending()
            self.initial.setText(_text(state.get('initial_rate', '')))
            self.prior.setText(_text(state.get('prior_failures', .5)))
            self.table.setRowCount(0)
            for row in state.get('observations', []):
                self.add_observation(row)
        finally:
            self._updating = prev

    def evaluate(self):
        return bayesian_update(**self.state())


class FailureModeTab(AnalysisTab):
    def __init__(self, parent=None):
        super().__init__('按表列频数比计算各失效模式失效率贡献。', parent)
        self.table_choice = _combo((number, '表 ' + number) for number in failure_mode_catalog())
        self.table_choice.setMaximumWidth(420)
        self.category = QComboBox()
        self.category.setMaximumWidth(420)
        self.rate = QLineEdit()
        self.rate.setMaximumWidth(420)

        card = QFrame()
        card.setObjectName('Card')
        card.setMaximumWidth(820)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(10, 8, 10, 8)
        card_layout.setSpacing(6)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        form.addRow('标准表格', self.table_choice)
        form.addRow('元器件或部件名称', self.category)
        form.addRow('元器件失效率（10⁻⁶/h）', self.rate)
        card_layout.addLayout(form)
        btn_row = QHBoxLayout()
        btn_row.addWidget(self.calculate_button)
        btn_row.addStretch()
        card_layout.addLayout(btn_row)
        self.form_area.addWidget(card)

        self.table_choice.currentIndexChanged.connect(self.refresh_categories)
        self.category.currentIndexChanged.connect(self.invalidate)
        self.rate.textChanged.connect(self.invalidate)
        self.refresh_categories()

    def refresh_categories(self):
        prev, self._updating = self._updating, True
        try:
            blocked = self.category.blockSignals(True)
            try:
                self.category.clear()
                self.category.addItems(failure_mode_catalog()[self.table_choice.currentData()])
            finally:
                self.category.blockSignals(blocked)
        finally:
            self._updating = prev
        self.invalidate()

    def state(self):
        return {'table': self.table_choice.currentData(), 'category': self.category.currentText() or None, 'rate': self.rate.text()}

    def restore(self, state):
        prev, self._updating = self._updating, True
        try:
            self.table_choice.setCurrentIndex(max(0, self.table_choice.findData(state.get('table', 'F.1'))))
            self.refresh_categories()
            target_cat = state.get('category')
            if isinstance(target_cat, str):
                idx = self.category.findText(target_cat)
                if idx >= 0:
                    self.category.setCurrentIndex(idx)
            self.rate.setText(_text(state.get('rate', '')))
        finally:
            self._updating = prev

    def evaluate(self):
        return mode_contributions(**self.state())


class PhysicsTab(AnalysisTab):
    def __init__(self, parent=None):
        super().__init__('输入适用于实际材料、工艺和结构的参数进行单项失效物理计算。', parent)
        self.model = _combo((key, model.name) for key, model in PHYSICS_TOOLS.items())
        self.model.setMaximumWidth(420)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(section_label('失效机理模型', wrap=False))
        top.addWidget(self.model)
        top.addStretch()
        top.addWidget(self.calculate_button)
        self.form_area.addLayout(top)

        self.note = section_label('')
        self.form_area.addWidget(self.note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.form_widget = QWidget()
        self.form = QFormLayout(self.form_widget)
        self.form.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.form.setVerticalSpacing(8)
        self.form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        scroll.setWidget(self.form_widget)
        scroll.setMinimumHeight(230)
        self.form_area.addWidget(scroll)
        self.layout.setStretch(1, 2)
        self.inputs = {}
        self.model.currentIndexChanged.connect(self.build_inputs)
        self.build_inputs()

    def build_inputs(self):
        prev, self._updating = self._updating, True
        try:
            while self.form.rowCount():
                self.form.removeRow(0)
            self.inputs = {}
            model = PHYSICS_TOOLS[self.model.currentData()]
            self.note.setText(model.reference + '；' + model.note)
            source = QLineEdit()
            source.setMaximumWidth(460)
            source.setMinimumHeight(32)
            self.inputs['parameter_source'] = source
            self.form.addRow('材料/试验/设计参数依据', source)
            source.textChanged.connect(self.invalidate)
            for field in model.fields:
                editor = QLineEdit(_text(field.default) if field.default is not None else '')
                editor.setMaximumWidth(460)
                editor.setMinimumHeight(32)
                self.inputs[field.key] = editor
                lbl = QLabel(f'{field.label} [{field.unit}]')
                lbl.setWordWrap(True)
                lbl.setMinimumWidth(290)
                lbl.setMaximumWidth(350)
                lbl.setMinimumHeight(30)
                self.form.addRow(lbl, editor)
                editor.textChanged.connect(self.invalidate)
        finally:
            self._updating = prev
        self.invalidate()

    def state(self):
        return {'model_id': self.model.currentData(), 'parameters': {key: editor.text() for key, editor in self.inputs.items()}}

    def restore(self, state):
        prev, self._updating = self._updating, True
        try:
            self.model.setCurrentIndex(max(0, self.model.findData(state.get('model_id', 'em'))))
            for key, editor in self.inputs.items():
                editor.setText(_text(state.get('parameters', {}).get(key, '')))
        finally:
            self._updating = prev

    def evaluate(self):
        return physics_calculate(**self.state())


class AnalysisPage(QWidget):
    """The host persists state(), supplies sources, and exports export_payload()."""
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(4)
        header = QHBoxLayout()
        header.addWidget(section_label('任务与分析', 'Title', wrap=False))
        header.addSpacing(12)
        header.addWidget(section_label('GJB/Z 299D—2024 附录 A 全寿命剖面、附录 E 经验修正、附录 F 失效模式与附录 D 失效物理。'), 1)
        layout.addLayout(header)

        self.tabs = QTabWidget()
        self.active_merge_offset = 0
        self.panels = {'mission': MissionTab(), 'bayes': BayesTab(),
                       'failure_modes': FailureModeTab(), 'physics': PhysicsTab()}
        titles = ('任务剖面与全寿命', '失效率修正', '失效模式', '失效物理单项')
        for idx, ((_key, panel), name) in enumerate(zip(self.panels.items(), titles, strict=True)):
            self.tabs.addTab(panel, name)
            panel.changed.connect(lambda i=idx: self._on_panel_changed(i))
        layout.addWidget(self.tabs, 1)

    def _on_panel_changed(self, tab_index):
        self.active_merge_offset = tab_index
        self.changed.emit()

    def commit_pending_edits(self):
        for key in ('mission', 'bayes'):
            self.panels[key].table.itemDelegate().commit_pending()

    def clear_condition_sources(self):
        self.panels['mission'].clear_condition_sources()

    def set_condition_sources(self, sources, *, source_issues=None):
        self.panels['mission'].set_condition_sources(sources, source_issues=source_issues)

    def state(self):
        return {'version': 1, **{key: panel.state() for key, panel in self.panels.items()}}

    def restore_state(self, state):
        validate_analysis_state(state)
        if not state:
            state = {}
        blocked = self.blockSignals(True)
        try:
            for key, panel in self.panels.items():
                panel.restore(copy.deepcopy(state.get(key, {})))
                if key in configured_analyses(state):
                    panel.calculate()  # Saved outputs are never trusted.
                else:
                    panel.last_result = None
                    panel.output.setPlainText('添加任务阶段后计算。' if key == 'mission' else '填写参数后计算。')
        finally:
            self.blockSignals(blocked)

    def export_payload(self):
        mission = self.panels['mission']
        results, errors = recompute_analyses(
            self.state(),
            current_sources=mission.current_sources,
            source_issues=mission.source_issues,
        )
        return {'version': 1, 'results': [result.to_dict() for result in results], 'errors': errors}
