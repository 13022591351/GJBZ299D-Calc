"""Edit explicitly scoped external failure rates without copying between conditions."""

import math

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .domain import rate_condition_key
from .number_edit import NumberEdit
from .numeric import number_text
from .standards import environment_text
from .table_layout import resizable_columns
from .theme import current_colors


def condition_key(environment, temperature):
    try:
        return rate_condition_key(environment, temperature)
    except (ValueError, TypeError):
        return None


class ConditionRateEditor(QWidget):
    changed = pyqtSignal()

    def __init__(self, component=None, conditions=None, *, environment=None, temperature=None, parent=None):
        super().__init__(parent)
        self.current_key = condition_key(environment, temperature)
        self.rows = []
        self.fields = {}
        self.current_field = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        note = QLabel('分别填写各环境类别和温度下的单件失效率；空白表示尚无适用数据。禁用计算的工况也可录入。')
        note.setWordWrap(True)
        layout.addWidget(note)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(['环境类别', '环境温度（℃）', '单件失效率（10⁻⁶/h）'])
        self.table.verticalHeader().hide()
        self.table.verticalHeader().setDefaultSectionSize(40)
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        resizable_columns(
            self.table,
            'condition-rates',
            [300, 140, 240],
            min_widths=[180, 110, 140],
        )
        layout.addWidget(self.table, 1)
        stored = {(entry['environment'], entry['ambient_temperature_C']): entry['rate']
                  for entry in component.condition_rate_entries()} if component else {}
        definitions = []
        seen = set()
        for condition in conditions or []:
            env, temp = condition.environment, condition.ambient_temperature_C
            key = condition_key(env, temp)
            if key is not None and key in seen:
                continue
            definitions.append((env, temp, key, condition.enabled, False))
            if key is not None:
                seen.add(key)
        if not any((env, temp) == (environment, temperature) for env, temp, *_rest in definitions):
            definitions.append((environment, temperature, self.current_key, True, False))
            if self.current_key is not None:
                seen.add(self.current_key)
        for key in stored:
            if key not in seen:
                definitions.append((*key, key, True, True))
                seen.add(key)
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(len(definitions))
            for row, (env, temp, key, enabled, extra) in enumerate(definitions):
                current = (env, temp) == (environment, temperature) and self.current_field is None
                title = environment_text(env)
                if current:
                    title += ' · 当前查看'
                elif extra:
                    title += ' · 已保存工况'
                labels = [title, number_text(temp) if temp is not None else '待填写']
                for column, text in enumerate(labels):
                    item = QTableWidgetItem(text)
                    if current:
                        colors = current_colors()
                        item.setBackground(QColor(colors['selection']))
                        item.setForeground(QColor(colors['selection_text']))
                        font = QFont()
                        font.setBold(True)
                        item.setFont(font)
                    self.table.setItem(row, column, item)
                field = NumberEdit()
                field.setObjectName(f'condition_rate_{row}')
                field.setPlaceholderText('可填写 0 或科学计数法' if key is not None else '先补齐工况参数')
                field.setReadOnly(key is None)
                field.setToolTip(('该工作环境未启用计算；可保存输入。' if not enabled else '')
                                 + '此值仅适用于本行的环境类别和温度；清空可移除该行已保存值。')
                original = stored.get(key)
                if original is not None:
                    field.set_number(original)
                field.textChanged.connect(self.changed)
                self.table.setCellWidget(row, 2, field)
                self.rows.append((key, field, original))
                if key is not None:
                    self.fields[key] = field
                if current and self.current_field is None:
                    self.current_field = field
        finally:
            self.table.setUpdatesEnabled(True)
        if component and component.parameters.get('λp') is not None and not stored:
            draft = QLabel('原有未绑定失效率：' + number_text(component.parameters['λp'])
                           + '。请在适用的工作环境行填写，未自动套用。')
            draft.setWordWrap(True)
            layout.addWidget(draft)

    def drafts(self):
        return {key: field.draft() for key, field, _ in self.rows if key is not None}

    def restore_drafts(self, drafts):
        for key, draft in drafts.items():
            if key in self.fields:
                self.fields[key].restore_draft(draft)

    def apply_changes(self, component):
        for key, field, original in self.rows:
            if key is None:
                continue
            if not field.text().strip():
                if original is not None:
                    component.remove_condition_rate(*key)
                continue
            try:
                value = field.value()
                if isinstance(value, bool) or not math.isfinite(value) or value < 0:
                    raise ValueError('须为有限非负数')
            except (ValueError, OverflowError) as error:
                raise ValueError(f'{environment_text(key[0])}、{number_text(key[1])} ℃ 的单件失效率须为有限非负数') from error
            if original is None or value != original:
                component.set_condition_rate(*key, value)
        return component
