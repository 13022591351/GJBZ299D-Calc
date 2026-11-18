"""Independent standard-model selection window with evidence-based recommendations."""

import html

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .model_registry import (
    CATEGORIES,
    category_for_model,
    matches_domestic,
    model_label,
    model_sort_key,
    recommend,
)
from .prediction import METHODS, method_state
from .standards import MODELS
from .theme import fit_window, text_browser

_AUTO = object()


def domestic_context(component, model, domestic):
    if domestic is not _AUTO:
        if domestic is not None and type(domestic) is not bool:
            raise ValueError('国产选项格式无效')
        return domestic
    if component is not None:
        return component.is_domestic
    return not model.startswith('进口')


class ModelPickerDialog(QDialog):
    def __init__(self, component=None, current_model='待配置', parent=None, *, domestic=_AUTO, prediction_state='working'):
        super().__init__(parent)
        self.result_model = None
        self.component = component
        self.prediction_state = prediction_state
        self.domestic = domestic_context(component, current_model, domestic)
        self.recommendation = recommend(component) if component else None
        self.setWindowTitle('选择失效率预计模型')
        fit_window(self, 1160, 780)
        layout = QVBoxLayout(self)
        heading = QLabel('失效率预计模型')
        heading.setObjectName('Title')
        layout.addWidget(heading)
        hint = QLabel(self.recommendation.reason if component else '按标准类别选择，再填写器件参数和应力条件。')
        hint.setWordWrap(True)
        layout.addWidget(hint)
        controls = QHBoxLayout()
        self.method_box = QComboBox()
        self.method_box.addItem('全部适用方法', None)
        for key, title in METHODS.items():
            if method_state(key) == prediction_state:
                self.method_box.addItem(title, key)
        controls.addWidget(self.method_box)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜索名称、器件类型或标准表号…')
        controls.addWidget(self.search, 1)
        layout.addLayout(controls)
        splitter = QSplitter()
        self.categories = QListWidget()
        self.categories.addItem(QListWidgetItem('全部标准类别'))
        self.categories.item(0).setData(Qt.ItemDataRole.UserRole, None)
        if self.recommendation and self.recommendation.categories:
            item = QListWidgetItem('按位号筛选')
            item.setData(Qt.ItemDataRole.UserRole, 'recommended')
            self.categories.addItem(item)
        for code, (title, section, page) in CATEGORIES.items():
            item = QListWidgetItem(f'{code}  {title}')
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setToolTip(f'第 {section} 节，标准第 {page} 页')
            self.categories.addItem(item)
        splitter.addWidget(self.categories)
        self.models = QListWidget()
        self.models.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        splitter.addWidget(self.models)
        self.detail = text_browser()
        splitter.addWidget(self.detail)
        splitter.setSizes([240, 360, 500])
        layout.addWidget(splitter, 1)
        self.status = QLabel('')
        layout.addWidget(self.status)
        buttons = QDialogButtonBox()
        self.apply_button = buttons.addButton('使用此模型', QDialogButtonBox.ButtonRole.AcceptRole)
        self.apply_button.setObjectName('Primary')
        buttons.addButton('取消', QDialogButtonBox.ButtonRole.RejectRole)
        buttons.accepted.connect(self.apply)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.categories.currentRowChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.method_box.currentIndexChanged.connect(self.refresh)
        self.models.currentItemChanged.connect(self.show_detail)
        self.models.itemDoubleClicked.connect(lambda _item: self.apply())
        self.categories.setCurrentRow(1 if self.recommendation and self.recommendation.categories else 0)
        for i in range(self.models.count()):
            if self.models.item(i).data(Qt.ItemDataRole.UserRole) == current_model:
                self.models.setCurrentRow(i)
                break

    def refresh(self, *_args):
        self.models.clear()
        category = self.categories.currentItem().data(Qt.ItemDataRole.UserRole) if self.categories.currentItem() else None
        term = self.search.text().strip().lower()
        names = sorted((m for m in MODELS if m not in ('待配置', '工程模块')),
                       key=model_sort_key)
        for name in names:
            if name != '已知单件失效率' and (method_state(MODELS[name].method) != self.prediction_state or
                    self.method_box.currentData() is not None and MODELS[name].method != self.method_box.currentData()):
                continue
            group = category_for_model(name)
            if category == 'recommended' and group not in self.recommendation.categories:
                continue
            if category not in (None, 'recommended') and category != group:
                continue
            if not matches_domestic(name, self.domestic):
                continue
            text = model_label(name) + ' ' + name + ' ' + MODELS[name]['reference'] + ' ' + MODELS[name]['note']
            if term and term not in text.lower():
                continue
            item = QListWidgetItem(model_label(name))
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.models.addItem(item)
        hint = ('所选位号的国产选项不同；请先在主表统一，再批量选择器件类型。'
                if self.domestic is None else '已按主表国产选项筛选；质量等级与具体工艺仍需确认。')
        self.status.setText(f'当前显示 {self.models.count()} 个可用模型。' + hint)
        self.show_detail()

    def show_detail(self, *_args):
        item = self.models.currentItem()
        self.apply_button.setEnabled(item is not None)
        if item is None:
            self.detail.setHtml('<h3>选择一个模型查看依据</h3><p>窗口左侧是标准器件类别。列表按主表国产选项筛选；应依据实际类型和适用条件选择，不以相似模型替代。</p>')
            return
        name = item.data(Qt.ItemDataRole.UserRole)
        model = MODELS[name]
        fields = ''.join(f'<li>{html.escape(f.label)}</li>' for f in model['fields'] if f.scope == 'device')
        self.detail.setHtml(f'<h2>{html.escape(model_label(name))}</h2><p>{html.escape(model["formula"])}</p>'
                            f'<p>{html.escape(model["reference"])}</p><p>{html.escape(model["note"])}</p>'
                            f'<h3>需要填写的参数</h3><ul>{fields}</ul><p>环境类别与环境温度由工程统一提供。</p>')

    def apply(self):
        item = self.models.currentItem()
        if item:
            self.result_model = item.data(Qt.ItemDataRole.UserRole)
            self.accept()


class ModelSelector(QWidget):
    changed = pyqtSignal()

    def __init__(self, component=None, model='待配置', parent=None, *, domestic=_AUTO, prediction_state='working'):
        super().__init__(parent)
        self.component = component
        self.prediction_state = prediction_state
        self.domestic = domestic_context(component, model, domestic)
        self.model = model
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.display = QComboBox()
        self.display.addItem('请选择器件类型…', '待配置')
        recommendation = recommend(component) if component else None
        categories = recommendation.categories if recommendation else ()
        names = sorted((name for name in MODELS if name not in ('待配置', '工程模块')
                        and matches_domestic(name, self.domestic)
                        and (name == '已知单件失效率' or method_state(MODELS[name].method) == prediction_state)),
                       key=lambda name: (category_for_model(name) not in categories,
                                         model_sort_key(name)))
        for name in names:
            suggested = category_for_model(name) in categories
            self.display.addItem(model_label(name) + (' · 推荐' if suggested else ''), name)
        if model not in names and model != '待配置':
            self.display.addItem(model_label(model) + ' [与当前预计状态不符]', model)
        self.display.setCurrentIndex(self.display.findData(model))
        self.display.setMaxVisibleItems(16)
        self.display.currentIndexChanged.connect(lambda _index: self.set_model(self.display.currentData()))
        self.display.setToolTip('所选位号的国产选项不同；请先在主表统一国产选项。' if self.domestic is None else
                                '已按主表国产选项筛选。推荐仅依据位号和描述，质量等级与具体工艺仍需确认。')
        layout.addWidget(self.display, 1)
        self.button = QPushButton('分类查找…')
        self.button.clicked.connect(self.choose)
        layout.addWidget(self.button)

    def _is_selectable_model(self, model):
        if not isinstance(model, str) or model not in MODELS or model == '工程模块':
            return False
        if model == '待配置':
            return True
        if not matches_domestic(model, self.domestic):
            return False
        if model != '已知单件失效率' and method_state(MODELS[model].method) != self.prediction_state:
            return False
        return True

    def set_model(self, model):
        if not self._is_selectable_model(model):
            self.display.blockSignals(True)
            try:
                idx = self.display.findData(self.model)
                self.display.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                self.display.blockSignals(False)
            return
        if model != self.model:
            prev_model = self.model
            self.model = model
            self.display.blockSignals(True)
            try:
                if prev_model != '待配置' and not self._is_selectable_model(prev_model):
                    stale_idx = self.display.findData(prev_model)
                    if stale_idx >= 0:
                        self.display.removeItem(stale_idx)
                idx = self.display.findData(model)
                self.display.setCurrentIndex(idx if idx >= 0 else 0)
            finally:
                self.display.blockSignals(False)
            self.changed.emit()

    def choose(self):
        dialog = ModelPickerDialog(self.component, self.model, self, domestic=self.domestic,
                                   prediction_state=self.prediction_state)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.set_model(dialog.result_model)
