"""User-resizable columns remembered locally without changing engineering data."""

from PyQt6.QtCore import QEvent, QObject, QSettings, Qt
from PyQt6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QHeaderView,
    QLineEdit,
    QTableWidget,
    QTreeView,
)


def configure_table_combo(combo, *, min_chars=4):
    """Keep table cell combos compact while expanding their popup list to fit item text."""
    combo.setMinimumWidth(0)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(min_chars)
    view = combo.view()
    if view is not None:
        metrics = combo.fontMetrics()
        widest = max((metrics.horizontalAdvance(combo.itemText(i)) for i in range(combo.count())), default=0)
        if widest > 0:
            view.setMinimumWidth(min(560, max(160, widest + 38)))
    return combo


class ColumnWidths(QObject):
    def __init__(self, table, name, defaults=150, *, min_widths=None, fixed_columns=(), auto_fit=True):
        super().__init__(table)
        self.table = table
        self.header = table.header() if isinstance(table, QTreeView) else table.horizontalHeader()
        self.name = name
        self.defaults = defaults
        self.min_widths = min_widths
        self.fixed_columns = frozenset(fixed_columns)
        self.auto_fit = auto_fit
        self.settings = QSettings('DuoYuWang', 'GJB299D')
        self.restoring = False
        self._weights = {}
        self.header.show()
        self.header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.header.setStretchLastSection(False)
        self.header.setMinimumSectionSize(36)
        self.header.setToolTip('拖动列边界调整宽度；双击边界适应内容。列宽在本机自动记忆。')
        self.table.viewport().installEventFilter(self)
        self.table.installEventFilter(self)
        self.restore()
        self.header.sectionResized.connect(self.save)
        self.header.sectionCountChanged.connect(self.restore)

    def _default_width(self, column):
        return (self.defaults[column] if column < len(self.defaults) else 150
                ) if isinstance(self.defaults, (list, tuple)) else self.defaults

    def _min_width(self, column):
        if isinstance(self.min_widths, (list, tuple)):
            return max(36, int(self.min_widths[column])) if column < len(self.min_widths) else 42
        if isinstance(self.min_widths, int):
            return max(36, self.min_widths)
        if isinstance(self.table, QTableWidget) and self.table.rowCount() > 0:
            widget = self.table.cellWidget(0, column)
            if isinstance(widget, QComboBox):
                return 115
            if isinstance(widget, (QLineEdit, QAbstractSpinBox)):
                return 90
        return 42

    def fit_to_viewport(self):
        if not self.auto_fit or self.restoring:
            return
        avail = self.table.viewport().width()
        count = self.header.count()
        if avail <= 80 or count == 0:
            return
        visible = [c for c in range(count) if not self.header.isSectionHidden(c)]
        if not visible:
            return
        fixed_widths = {c: max(self._min_width(c), self._weights.get(c, self._default_width(c)))
                        for c in visible if c in self.fixed_columns}
        flex = [c for c in visible if c not in self.fixed_columns]
        if not flex:
            widths = fixed_widths
        else:
            flex_avail = avail - sum(fixed_widths.values())
            min_map = {c: self._min_width(c) for c in flex}
            if flex_avail <= sum(min_map.values()):
                widths = {**fixed_widths, **min_map}
            else:
                weights = {c: max(min_map[c], self._weights.get(c, self._default_width(c))) for c in flex}
                locked = {}
                remaining = list(flex)
                avail_rem = flex_avail
                while remaining:
                    total_w = sum(weights[c] for c in remaining) or len(remaining)
                    newly_locked = []
                    for c in remaining:
                        if int(avail_rem * weights[c] / total_w) < min_map[c]:
                            locked[c] = min_map[c]
                            newly_locked.append(c)
                    if not newly_locked:
                        break
                    avail_rem -= sum(locked[c] for c in newly_locked)
                    remaining = [c for c in remaining if c not in locked]
                widths = {**fixed_widths, **locked}
                if remaining:
                    total_w = sum(weights[c] for c in remaining) or len(remaining)
                    for c in remaining:
                        widths[c] = max(min_map[c], int(avail_rem * weights[c] / total_w))
                    rem = avail - sum(widths.values())
                    order = sorted(remaining, key=lambda c: weights[c], reverse=True)
                    idx = 0
                    while rem > 0 and order:
                        widths[order[idx % len(order)]] += 1
                        rem -= 1
                        idx += 1
        self.restoring = True
        try:
            for column, width in widths.items():
                if self.header.sectionSize(column) != width:
                    self.header.resizeSection(column, width)
        finally:
            self.restoring = False

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.fit_to_viewport()
        return super().eventFilter(obj, event)

    def restore(self, *_args):
        self.restoring = True
        try:
            self._weights = {}
            for column in range(self.header.count()):
                default = self._default_width(column)
                minimum = self._min_width(column)
                try:
                    width = int(self.settings.value(f'column_weights/{self.name}/{column}', max(default, minimum)))
                except (ValueError, TypeError):
                    width = max(default, minimum)
                width = max(minimum, min(width, 3000))
                self._weights[column] = width
                if not self.header.isSectionHidden(column):
                    self.header.resizeSection(column, width)
        finally:
            self.restoring = False
        self.fit_to_viewport()

    def save(self, column, _before, width):
        if self.restoring or width <= 0 or self.header.isSectionHidden(column):
            return
        minimum = self._min_width(column)
        if width < minimum:
            self.restoring = True
            try:
                self.header.resizeSection(column, minimum)
            finally:
                self.restoring = False
            width = minimum
        if (QApplication.mouseButtons() & Qt.MouseButton.LeftButton) or not self.auto_fit:
            self._weights[column] = width
            self.settings.setValue(f'column_weights/{self.name}/{column}', width)


def resizable_columns(table, name, defaults=150, *, min_widths=None, fixed_columns=(), auto_fit=True):
    table.column_widths = ColumnWidths(
        table, name, defaults, min_widths=min_widths, fixed_columns=fixed_columns, auto_fit=auto_fit,
    )


class SplitterWidths(QObject):
    def __init__(self, splitter, name, defaults):
        super().__init__(splitter)
        self.splitter, self.name = splitter, name
        self.settings = QSettings('DuoYuWang', 'GJB299D')
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(4)
        widths = self.settings.value(f'splitters/{name}', defaults)
        try:
            widths = [int(width) for width in widths]
            if len(widths) != splitter.count() or any(width <= 0 for width in widths):
                widths = defaults
        except (ValueError, TypeError):
            widths = defaults
        splitter.setSizes(widths)
        for index in range(1, splitter.count()):
            splitter.handle(index).setToolTip('拖动分隔条调整栏目宽度，位置在本机自动记忆。')
        splitter.splitterMoved.connect(self.save)

    def save(self, *_args):
        self.settings.setValue(f'splitters/{self.name}', self.splitter.sizes())


def resizable_splitter(splitter, name, defaults):
    splitter.saved_widths = SplitterWidths(splitter, name, defaults)
