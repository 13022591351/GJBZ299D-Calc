"""Pure calculation data table browser with highlighted cell lookup."""

import re
import sqlite3

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from .reference_data import catalog, fragments
from .table_layout import resizable_columns, resizable_splitter
from .theme import THEME_BUS, current_colors, section_label


class ReferenceTableModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.fragment = None
        self.texts = {}
        self.highlighted_cell_id = None

    def set_fragment(self, fragment):
        self.beginResetModel()
        self.fragment = fragment
        self.texts = {c['id']: c['text'] for c in fragment['cells']} if fragment else {}
        self.endResetModel()

    def _cell_bounds(self, cell_id):
        if not self.fragment or not cell_id:
            return None
        for cell in self.fragment['cells']:
            if cell['id'] == cell_id:
                top = cell['row_no']
                left = cell['column_no']
                bottom = min(self.fragment['rows'] - 1, top + max(1, cell.get('row_span', 1)) - 1)
                right = min(self.fragment['columns'] - 1, left + max(1, cell.get('column_span', 1)) - 1)
                return self.index(top, left), self.index(bottom, right)
        return None

    def set_highlight(self, cell_id=None):
        if cell_id == self.highlighted_cell_id:
            return
        old_bounds = self._cell_bounds(self.highlighted_cell_id)
        self.highlighted_cell_id = cell_id
        new_bounds = self._cell_bounds(cell_id)
        roles = [Qt.ItemDataRole.BackgroundRole, Qt.ItemDataRole.FontRole]
        for bounds in (old_bounds, new_bounds):
            if bounds is not None and bounds[0].isValid() and bounds[1].isValid():
                self.dataChanged.emit(bounds[0], bounds[1], roles)

    def rowCount(self, parent=QModelIndex()):
        return self.fragment['rows'] if self.fragment and not parent.isValid() else 0

    def columnCount(self, parent=QModelIndex()):
        return self.fragment['columns'] if self.fragment and not parent.isValid() else 0

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            from .bom import column_name
            return column_name(section)
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not self.fragment or not index.isValid():
            return None
        cell = self.fragment['grid'][index.row(), index.column()]
        is_highlighted = self.highlighted_cell_id and cell['id'] == self.highlighted_cell_id
        if role == Qt.ItemDataRole.DisplayRole or role == Qt.ItemDataRole.ToolTipRole:
            return self.texts[cell['id']]
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.BackgroundRole and is_highlighted:
            return QColor(current_colors()['highlight_cell'])
        if role == Qt.ItemDataRole.FontRole and is_highlighted:
            font = QFont()
            font.setBold(True)
            return font
        return None


class StandardPage(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
        layout.setSpacing(4)
        header_row = QHBoxLayout()
        header_row.addWidget(section_label('标准数据表', 'Title', wrap=False))
        header_row.addSpacing(12)
        header_row.addWidget(section_label('GJB/Z 299D—2024 计算系数表与参数数据查询。'), 1)
        layout.addLayout(header_row)

        self.groups = {}
        self.current_fragments = ()
        for table in catalog():
            self.groups.setdefault(table['number'], []).append(table)

        self.search = QLineEdit()
        self.search.setClearButtonEnabled(True)
        self.search.setPlaceholderText('搜索表号、器件名称或参数符号…')
        self.splitter = QSplitter()

        directory = QWidget()
        directory_layout = QVBoxLayout(directory)
        directory_layout.setContentsMargins(0, 0, 0, 0)
        directory_layout.setSpacing(4)
        directory_layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        directory_layout.addWidget(self.list, 1)
        directory.setMinimumWidth(200)
        self.splitter.addWidget(directory)

        right = QWidget()
        detail = QVBoxLayout(right)
        detail.setContentsMargins(2, 0, 0, 0)
        detail.setSpacing(4)
        meta_row = QHBoxLayout()
        self.source = section_label('')
        meta_row.addWidget(self.source, 1)
        self.fragment_label = section_label('分表', wrap=False)
        self.fragment_box = QComboBox()
        self.fragment_box.setMinimumWidth(140)
        self.fragment_box.setMaximumWidth(210)
        meta_row.addWidget(self.fragment_label)
        meta_row.addWidget(self.fragment_box)
        detail.addLayout(meta_row)

        self.table = QTableView()
        self.table_model = ReferenceTableModel(self.table)
        self.table.setModel(self.table_model)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(True)
        self.table.verticalHeader().hide()
        resizable_columns(self.table, 'standard', 140, min_widths=90)
        self.table.verticalHeader().setDefaultSectionSize(38)
        detail.addWidget(self.table, 1)
        right.setMinimumWidth(400)
        self.splitter.addWidget(right)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        resizable_splitter(self.splitter, 'standard-directory', [320, 900])
        layout.addWidget(self.splitter, 1)

        self.search.textChanged.connect(self.filter)
        self.list.currentItemChanged.connect(self.show_table)
        self.fragment_box.currentIndexChanged.connect(self.show_fragment)
        THEME_BUS.changed.connect(self._on_theme_changed)
        self.filter()

    def _on_theme_changed(self, _mode):
        self.table.viewport().update()

    def filter(self):
        query = self.search.text().strip().lower()
        normalized_query = re.sub(r'\s+', '', query)
        exact_number = re.sub(r'^表\s*', '', query).replace(' ', '').upper()
        self.list.clear()
        for number, tables in self.groups.items():
            title = tables[0]['caption']
            normalized_title = re.sub(r'\s+', '', title).lower()
            if exact_number in self.groups:
                if number != exact_number:
                    continue
            elif normalized_query and normalized_query not in normalized_title:
                continue
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, number)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        else:
            self.fragment_box.clear()
            self.fragment_label.hide()
            self.fragment_box.hide()
            self.table.clearSpans()
            self.table_model.set_fragment(None)
            self.source.setText('没有匹配的表格')

    def show_table(self, current, _previous=None):
        if not current:
            return
        number = current.data(Qt.ItemDataRole.UserRole)
        try:
            self.current_fragments = fragments(number)
        except (sqlite3.Error, OSError) as error:
            self.current_fragments = ()
            self.fragment_box.clear()
            self.fragment_label.hide()
            self.fragment_box.hide()
            self.table.clearSpans()
            self.table_model.set_fragment(None)
            self.source.setText(f'读取表 {number} 失败：{error}')
            return
        self.table_model.set_highlight(None)
        multi = len(self.current_fragments) > 1
        self.fragment_label.setVisible(multi)
        self.fragment_box.setVisible(multi)
        self.fragment_box.blockSignals(True)
        self.fragment_box.clear()
        for table in self.current_fragments:
            self.fragment_box.addItem(f'分表 {table["seq"]}')
        self.fragment_box.blockSignals(False)
        suffix = f'（共 {len(self.current_fragments)} 个分表）' if multi else ''
        self.source.setText(f'表 {number}{suffix} · GJB/Z 299D—2024')
        self.show_fragment(0)

    def show_fragment(self, index):
        if not 0 <= index < len(self.current_fragments):
            return
        table = self.current_fragments[index]
        self.table.clearSpans()
        self.table_model.set_fragment(table)
        for cell in table['cells']:
            if cell['row_span'] > 1 or cell['column_span'] > 1:
                self.table.setSpan(cell['row_no'], cell['column_no'], cell['row_span'], cell['column_span'])
        if self.table_model.rowCount() <= 250:
            self.table.resizeRowsToContents()
        else:
            for row in range(6):
                self.table.resizeRowToContents(row)
        self.table.scrollToTop()

    def open_table(self, number, cell_id=None):
        """Select the requested table and optionally highlight and scroll to one cell_id."""
        normalized = re.sub(r'^表\s*', '', str(number).strip()).replace(' ', '').upper()
        if normalized not in self.groups:
            return False

        def _find_row():
            for row in range(self.list.count()):
                if self.list.item(row).data(Qt.ItemDataRole.UserRole) == normalized:
                    return row
            return -1

        row = _find_row()
        if row < 0:
            self.search.clear()
            row = _find_row()
        if row < 0:
            return False
        self.list.setCurrentRow(row)

        if cell_id:
            for frag_idx, table in enumerate(self.current_fragments):
                match_cell = next((c for c in table['cells'] if c['id'] == cell_id), None)
                if match_cell is not None:
                    self.fragment_box.setCurrentIndex(frag_idx)
                    self.table_model.set_highlight(cell_id)
                    target = self.table_model.index(match_cell['row_no'], match_cell['column_no'])
                    self.table.selectionModel().setCurrentIndex(target, self.table.selectionModel().SelectionFlag.ClearAndSelect)
                    self.table.scrollTo(target, QAbstractItemView.ScrollHint.PositionAtCenter)
                    return True
        self.fragment_box.setCurrentIndex(0)
        self.table_model.set_highlight(None)
        return True
