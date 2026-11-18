"""Two-step import: select the source rectangle, then map to fixed BOM fields."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTableWidgetSelectionRange,
    QVBoxLayout,
    QWidget,
)

from .bom import IMPORT_FIELDS, IMPORT_LABELS, column_name, designators, row_quantity
from .dialogs import label
from .table_layout import configure_table_combo, resizable_columns
from .theme import COLORS, fit_window


class BomImportDialog(QDialog):
    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.sources = source if isinstance(source, list) else [source]
        source = next((s for s in self.sources if s.header_row is not None), self.sources[0])
        self.source = source
        self.result_import = None
        self.mapping_boxes = {}
        self.mapping_signature = None
        self._reverting_move = False
        self._clean_raw_header_state = None
        self._clean_preview_header_state = None
        self.setWindowTitle('导入 BOM')
        fit_window(self, 1360, 800)
        layout = QVBoxLayout(self)
        header_row = QHBoxLayout()
        header_row.setSpacing(10)
        self.title = label('1 选择数据区域', 'Title')
        header_row.addWidget(self.title)
        header_row.addWidget(label(source.file_name), 1)
        self.sheet_box = QComboBox()
        self.sheet_box.setMaximumWidth(260)
        for sheet in self.sources:
            self.sheet_box.addItem(sheet.sheet_name or 'CSV / TSV 数据')
        self.sheet_box.setCurrentIndex(self.sources.index(source))
        sheet_lbl = label('工作表')
        sheet_lbl.setVisible(bool(source.sheet_name))
        self.sheet_box.setVisible(bool(source.sheet_name))
        header_row.addWidget(sheet_lbl)
        header_row.addWidget(self.sheet_box)
        layout.addLayout(header_row)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        page = QWidget()
        first_layout = QVBoxLayout(page)
        first_layout.setContentsMargins(0, 0, 0, 0)
        first_layout.addWidget(label('鼠标框选元器件数据区域，或单击起点后按住 Shift 单击终点。默认识别 Export-Toolkit 格式，排除表头、序号和替代料号。可拖动列标题调整源列的显示顺序。'))
        self.raw_table = QTableWidget(len(source.cells), source.width)
        self.raw_table.setHorizontalHeaderLabels([column_name(c) for c in range(source.width)])
        self.raw_table.setSelectionMode(QAbstractItemView.SelectionMode.ContiguousSelection)
        self.raw_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.raw_table.setAlternatingRowColors(True)
        self.raw_table.horizontalHeader().setSectionsMovable(True)
        self.raw_table.setUpdatesEnabled(False)
        try:
            for r, row in enumerate(source.cells):
                for c, value in enumerate(row):
                    item = QTableWidgetItem(value)
                    item.setToolTip(source.cell_errors.get((r, c), value))
                    self.raw_table.setItem(r, c, item)
        finally:
            self.raw_table.setUpdatesEnabled(True)
        resizable_columns(self.raw_table, 'import-source', 165, min_widths=90, auto_fit=False)
        self._clean_raw_header_state = self.raw_table.horizontalHeader().saveState()
        first_layout.addWidget(self.raw_table, 1)
        selection_row = QHBoxLayout()
        reset = QPushButton('恢复默认区域')
        reset.clicked.connect(self.restore_default)
        selection_row.addWidget(reset)
        self.region_label = label('')
        selection_row.addWidget(self.region_label, 1)
        first_layout.addLayout(selection_row)
        self.pages.addWidget(page)
        page = QWidget()
        second_layout = QVBoxLayout(page)
        second_layout.setContentsMargins(0, 0, 0, 0)
        second_layout.addWidget(label('固定表头：序号自动生成，其余字段可选择源列或“留空”。拖动表头可调整源列的分配，固定字段顺序不变。'))
        self.preview_table = QTableWidget(0, 8)
        self.preview_table.setHorizontalHeaderLabels(['序号'] + IMPORT_LABELS)
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.horizontalHeader().setSectionsMovable(True)
        self.preview_table.horizontalHeader().setFirstSectionMovable(False)
        self.preview_table.horizontalHeader().sectionMoved.connect(self.move_mapping)
        resizable_columns(
            self.preview_table,
            'import-mapping',
            [70, 180, 135, 215, 165, 135, 145, 200],
            min_widths=[56, 120, 120, 130, 120, 120, 125, 130],
            fixed_columns=(0,),
        )
        self._clean_preview_header_state = self.preview_table.horizontalHeader().saveState()
        second_layout.addWidget(self.preview_table, 1)
        self.validation = label('')
        second_layout.addWidget(self.validation)
        second_layout.addWidget(label('数量为空且有位号时按位号数识别；位号和数量都为空时保留为待填写。仅导入非空记录，原始文件保持不变。'))
        self.pages.addWidget(page)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.back_button = QPushButton('上一步')
        self.back_button.setMinimumWidth(84)
        self.back_button.clicked.connect(self.back)
        controls.addWidget(self.back_button)
        controls.addStretch()
        self.next_button = QPushButton('下一步：分配字段')
        self.next_button.setMinimumWidth(116)
        self.next_button.setObjectName('Primary')
        self.next_button.clicked.connect(self.advance)
        controls.addWidget(self.next_button)
        self.import_button = QPushButton('导入工程')
        self.import_button.setMinimumWidth(92)
        self.import_button.setObjectName('Primary')
        self.import_button.clicked.connect(self.accept)
        controls.addWidget(self.import_button)
        cancel = QPushButton('取消')
        cancel.setMinimumWidth(84)
        cancel.clicked.connect(self.reject)
        controls.addWidget(cancel)
        layout.addLayout(controls)
        self.raw_table.itemSelectionChanged.connect(self.selection_changed)
        self.raw_table.horizontalHeader().sectionMoved.connect(self.selection_changed)
        self.restore_default()
        self.back()
        self.sheet_box.currentIndexChanged.connect(self.change_sheet)

    def change_sheet(self, index):
        self.source = self.sources[index]
        self.mapping_signature = None
        self.result_import = None
        self.raw_table.setUpdatesEnabled(False)
        self.raw_table.blockSignals(True)
        try:
            self.raw_table.clear()
            self.raw_table.setRowCount(len(self.source.cells))
            self.raw_table.setColumnCount(self.source.width)
            self.raw_table.setHorizontalHeaderLabels([column_name(c) for c in range(self.source.width)])
            for row, cells in enumerate(self.source.cells):
                for col, value in enumerate(cells):
                    item = QTableWidgetItem(value)
                    item.setToolTip(self.source.cell_errors.get((row, col), value))
                    self.raw_table.setItem(row, col, item)
            self.raw_table.column_widths.restore()
            self._clean_raw_header_state = self.raw_table.horizontalHeader().saveState()
        finally:
            self.raw_table.blockSignals(False)
            self.raw_table.setUpdatesEnabled(True)
        self.restore_default()
        self.back()

    def _reset_header_order(self, table, clean_state, *, first_fixed=False):
        header = table.horizontalHeader()
        blocked = header.blockSignals(True)
        col_widths = getattr(table, 'column_widths', None)
        restoring = col_widths.restoring if col_widths is not None else False
        if col_widths is not None:
            col_widths.restoring = True
        try:
            widths = [header.sectionSize(c) for c in range(header.count())]
            for col in range(header.count()):
                cur = header.visualIndex(col)
                if cur != col:
                    header.moveSection(cur, col)
            if clean_state is not None:
                header.restoreState(clean_state)
                header.setSectionsMovable(True)
                header.setFirstSectionMovable(not first_fixed)
                for c, w in enumerate(widths):
                    if w > 0 and c < header.count() and header.sectionSize(c) != w:
                        header.resizeSection(c, w)
        finally:
            if col_widths is not None:
                col_widths.restoring = restoring
            header.blockSignals(blocked)

    def restore_default(self):
        self._reset_header_order(self.raw_table, self._clean_raw_header_state, first_fixed=False)
        if not self.source.cells or self.source.width <= 0:
            self.raw_table.clearSelection()
            self.region_label.setText('当前工作表没有可导入的元器件数据行')
            self.next_button.setEnabled(False)
            return
        try:
            first, last, columns = self.source.default_region()
        except ValueError:
            start_row = (self.source.header_row + 1) if self.source.header_row is not None else 0
            has_data = any(
                v.strip()
                for row in self.source.cells[start_row:]
                for v in row
            )
            if not has_data:
                self.raw_table.clearSelection()
                self.region_label.setText('当前工作表没有可导入的元器件数据行')
                self.next_button.setEnabled(False)
                return
            first, last, columns = start_row, len(self.source.cells) - 1, list(range(self.source.width))
        self.raw_table.clearSelection()
        self.raw_table.setRangeSelected(QTableWidgetSelectionRange(first, min(columns), last, max(columns)), True)
        self.selection_changed()

    def selected_region(self):
        ranges = self.raw_table.selectedRanges()
        if not ranges:
            raise ValueError('请先框选数据区域')
        header = self.raw_table.horizontalHeader()
        if len(ranges) == 1 and not header.sectionsMoved():
            region = ranges[0]
            return region.topRow(), region.bottomRow(), list(range(region.leftColumn(), region.rightColumn() + 1))
        indexes = self.raw_table.selectedIndexes()
        if not indexes:
            raise ValueError('请先框选数据区域')
        rows = {i.row() for i in indexes}
        columns = sorted({i.column() for i in indexes}, key=header.visualIndex)
        if len(indexes) != len(rows) * len(columns) or len(rows) != max(rows) - min(rows) + 1:
            raise ValueError('请选择连续的数据区域')
        return min(rows), max(rows), columns

    def selection_changed(self, *_args):
        try:
            first, last, columns = self.selected_region()
            self.region_label.setText(f'已选源表第 {first + 1}～{last + 1} 行 · {len(columns)} 列 · {last - first + 1} 行')
            self.next_button.setEnabled(True)
        except ValueError as error:
            self.region_label.setText(str(error))
            self.next_button.setEnabled(False)

    def advance(self):
        try:
            self.first, self.last, self.columns = self.selected_region()
        except ValueError as error:
            self.region_label.setText(str(error))
            return
        signature = (self.first, self.last, tuple(self.columns))
        if signature != self.mapping_signature:
            self.mapping_signature = signature
            try:
                mapping = self.source.default_mapping(self.columns)
            except ValueError:
                mapping = {}
            self.mapping_boxes = {}
            self.preview_table.setUpdatesEnabled(False)
            try:
                self.preview_table.setRowCount(1)
                self.preview_table.setRowHeight(0, 42)
                self.preview_table.setItem(0, 0, QTableWidgetItem('自动'))
                for col, key in enumerate(IMPORT_FIELDS, 1):
                    box = QComboBox()
                    box.addItem('留空', None)
                    for source_col in self.columns:
                        heading = self.source.cell(self.source.header_row, source_col) if self.source.header_row is not None else ''
                        box.addItem(f'{column_name(source_col)} · {heading or self.source.cell(self.first, source_col)}', source_col)
                        box.setItemData(box.count() - 1, box.itemText(box.count() - 1), Qt.ItemDataRole.ToolTipRole)
                    idx = box.findData(mapping.get(key))
                    box.setCurrentIndex(idx if idx >= 0 else 0)
                    configure_table_combo(box, min_chars=4)
                    self.mapping_boxes[key] = box
                    self.preview_table.setCellWidget(0, col, box)
                    box.currentIndexChanged.connect(self.refresh_preview)
            finally:
                self.preview_table.setUpdatesEnabled(True)
        self.pages.setCurrentIndex(1)
        self.title.setText('2 分配字段并核对预览')
        self.back_button.show()
        self.next_button.hide()
        self.import_button.show()
        self.refresh_preview()

    def back(self):
        self.pages.setCurrentIndex(0)
        self.title.setText('1 选择数据区域')
        self.back_button.hide()
        self.next_button.show()
        self.import_button.hide()

    def _revert_preview_header(self):
        if self._reverting_move:
            return
        self._reverting_move = True
        try:
            self._reset_header_order(
                self.preview_table, self._clean_preview_header_state, first_fixed=True
            )
        finally:
            self._reverting_move = False

    def move_mapping(self, _logical, old, new):
        if self._reverting_move:
            return
        QTimer.singleShot(0, self._revert_preview_header)
        if not self.mapping_boxes or old <= 0 or new <= 0:
            return
        if old - 1 >= len(IMPORT_FIELDS) or new - 1 >= len(IMPORT_FIELDS):
            return
        assignments = [self.mapping_boxes[key].currentData() for key in IMPORT_FIELDS]
        assignments.insert(new - 1, assignments.pop(old - 1))
        for key, source_col in zip(IMPORT_FIELDS, assignments, strict=True):
            box = self.mapping_boxes[key]
            box.blockSignals(True)
            idx = box.findData(source_col)
            box.setCurrentIndex(idx if idx >= 0 else 0)
            box.blockSignals(False)
        self.refresh_preview()

    def refresh_preview(self, *_args):
        mapping = {key: box.currentData() for key, box in self.mapping_boxes.items()}
        rows = [(r, values) for r, values in self.source.mapped_rows(self.first, self.last, mapping)
                if any(v.strip() for v in values.values())]
        inferred, missing = 0, 0
        self.preview_table.setUpdatesEnabled(False)
        try:
            self.preview_table.setRowCount(len(rows) + 1)
            self.preview_table.setVerticalHeaderLabels(['源列映射'] + [f'源 {r + 1} 行' for r, _ in rows])
            for index, (source_row, values) in enumerate(rows, 1):
                self.preview_table.setItem(index, 0, QTableWidgetItem(str(index)))
                for col, key in enumerate(IMPORT_FIELDS, 1):
                    text = values[key]
                    item = QTableWidgetItem(text)
                    item.setToolTip(f'源表第 {source_row + 1} 行\n{text}')
                    if key == 'Quantity' and not text.strip():
                        refs = designators(values['Designator'])
                        item.setText(f'{len(refs)}（按位号）' if refs else '待填写')
                        item.setForeground(QColor(COLORS['accent'] if refs else COLORS['warning']))
                        inferred += bool(refs)
                        missing += not refs
                    self.preview_table.setItem(index, col, item)
        finally:
            self.preview_table.setUpdatesEnabled(True)
        try:
            self.result_import = self.source.build_import(self.first, self.last, self.columns, mapping)
            known = sum(row_quantity(row) or 0 for row in self.result_import.rows)
            self.validation.setText(f'可导入 {len(rows)} 行；已知数量 {known} 个。{inferred} 行数量按位号识别，{missing} 行数量待填写。')
            self.import_button.setEnabled(True)
        except ValueError as error:
            self.result_import = None
            self.validation.setText('请调整映射或返回修改数据区域：' + str(error))
            self.import_button.setEnabled(False)

    def accept(self):
        if self.pages.currentIndex() == 0:
            self.advance()
            return
        self.refresh_preview()
        if self.result_import is not None:
            super().accept()
