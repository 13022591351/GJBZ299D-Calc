"""Compact single-row category Pareto distribution bar with click-to-filter support."""

from PyQt6.QtCore import QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from ..model_registry import CATEGORIES
from ..standards import MODELS
from ..theme import THEME_BUS, current_colors

_SEGMENT_PALETTE_DARK = (
    '#2d7fc1', '#2c9f7e', '#b9832d', '#7f6bc4', '#c25b6e', '#5897a6', '#7b8a99',
)
_SEGMENT_PALETTE_LIGHT = (
    '#1f78c1', '#1a8a6c', '#b45309', '#6d59b8', '#b83e54', '#2e7d8f', '#64748b',
)


class CategoryParetoBar(QWidget):
    """Single-row category share indicator and quick Pareto filter."""

    category_clicked = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(30)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._entries = []
        self._active_category = None
        self._hit_rects = []
        THEME_BUS.changed.connect(self._on_theme_changed)

    def _on_theme_changed(self, _mode):
        self.update()

    def entries(self):
        return list(self._entries)

    def update_data(self, project, context, active_category=None):
        self._active_category = active_category
        totals = {}
        for component in project.components:
            result = context.evaluate(component)
            weighted = result.weighted(component.quantity)
            if weighted is None or weighted <= 0:
                continue
            effective_model = component.disclosure['model'] if component.disclosure else component.model
            spec = MODELS.get(effective_model)
            category = spec.category if spec is not None and spec.category else 'OTHER'
            totals[category] = totals.get(category, 0.0) + weighted

        grand = sum(totals.values())
        ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
        self._entries = [
            (code, CATEGORIES.get(code, ('其他电子元器件', '', 0))[0], value,
             (value / grand * 100.0) if grand > 0 else 0.0)
            for code, value in ranked
        ]
        if not self._entries:
            self.setToolTip('尚无已计算的有效失效率占比数据')
        else:
            lines = [f'{code} {name}：{pct:.2f}%（{val:.4g}×10⁻⁶/h）'
                     for code, name, val, pct in self._entries[:10]]
            lines.append('点击色块或标签按类别筛选；再次点击恢复全部。')
            self.setToolTip('\n'.join(lines))
        self.update()

    def paintEvent(self, _event):
        colors = current_colors()
        palette = _SEGMENT_PALETTE_LIGHT if colors['background'] == '#f4f6f8' else _SEGMENT_PALETTE_DARK
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)

        outer = self.rect().adjusted(0, 0, -1, -1)
        painter.fillRect(outer, QColor(colors['surface']))
        painter.setPen(QPen(QColor(colors['border'])))
        painter.drawRect(outer)

        self._hit_rects = []
        if not self._entries:
            painter.setPen(QColor(colors['muted']))
            painter.drawText(
                outer.adjusted(10, 0, -10, 0),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                '类别占比分布（完成参数配置后显示器件贡献分布）',
            )
            return

        fm = QFontMetrics(painter.font())
        prefix = '类别占比'
        prefix_w = fm.horizontalAdvance(prefix) + 12
        painter.setPen(QColor(colors['muted']))
        painter.drawText(QRect(outer.left() + 8, outer.top(), prefix_w, outer.height()),
                         Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, prefix)

        bar_w = min(280, max(120, outer.width() // 4))
        bar_rect = QRect(outer.left() + 8 + prefix_w, outer.top() + (outer.height() - 10) // 2, bar_w, 10)
        painter.fillRect(bar_rect, QColor(colors['raised']))

        x = bar_rect.left()
        for idx, (code, _name, _val, pct) in enumerate(self._entries):
            if idx == len(self._entries) - 1:
                seg_w = max(0, bar_rect.right() - x + 1)
            else:
                seg_w = max(2, int(bar_w * pct / 100.0)) if pct > 0 else 0
            if seg_w <= 0:
                continue
            seg_rect = QRect(x, bar_rect.top(), min(seg_w, bar_rect.right() - x + 1), bar_rect.height())
            color = QColor(palette[idx % len(palette)])
            if self._active_category is not None and code != self._active_category:
                color.setAlpha(95)
            painter.fillRect(seg_rect, color)
            if code == self._active_category:
                painter.setPen(QPen(QColor(colors['heading']), 1))
                painter.drawRect(seg_rect.adjusted(0, 0, -1, -1))
            self._hit_rects.append((seg_rect, code))
            x += seg_w

        lx = bar_rect.right() + 14
        right_limit = outer.right() - (86 if self._active_category is not None else 10)
        for idx, (code, name, _val, pct) in enumerate(self._entries[:6]):
            label = f'{code} {name} {pct:.1f}%'
            item_w = fm.horizontalAdvance(label) + 20
            if lx + item_w > right_limit:
                break
            chip_rect = QRect(lx, outer.top() + 5, item_w, outer.height() - 10)
            dot_rect = QRect(lx + 3, outer.top() + (outer.height() - 8) // 2, 8, 8)
            color = QColor(palette[idx % len(palette)])
            painter.fillRect(dot_rect, color)
            if code == self._active_category:
                painter.setPen(QPen(QColor(colors['accent'])))
                painter.drawRect(chip_rect.adjusted(0, 0, -1, -1))
                painter.setPen(QColor(colors['heading']))
            else:
                painter.setPen(QColor(colors['muted'] if self._active_category else colors['text']))
            painter.drawText(chip_rect.adjusted(14, 0, -2, 0), Qt.AlignmentFlag.AlignVCenter, label)
            self._hit_rects.append((chip_rect, code))
            lx += item_w + 6

        if self._active_category is not None:
            clear_rect = QRect(outer.right() - 84, outer.top() + 5, 76, outer.height() - 10)
            painter.setPen(QColor(colors['accent']))
            painter.drawText(clear_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, '清除筛选 ×')
            self._hit_rects.append((clear_rect, None))

    def mousePressEvent(self, event):
        pos = event.position().toPoint()
        for rect, code in reversed(self._hit_rects):
            if rect.contains(pos):
                if code is None or code == self._active_category:
                    self.category_clicked.emit(None)
                else:
                    self.category_clicked.emit(code)
                return
        super().mousePressEvent(event)
