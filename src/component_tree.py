"""A grouping projection over independent calculation records; never merge inputs."""

import copy
import json
import math
import re
from dataclasses import dataclass, field

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, QSortFilterProxyModel, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate

from .analysis_methods.workspace import is_component_config_error
from .calculation import sum_rates
from .component_fields import COLUMNS
from .model_registry import model_label, model_sort_key
from .nominal_display import standard_nominal_text
from .numeric import number_text
from .parameter_display import input_text, nominal_value
from .standards import MODELS, model_name
from .stress import ceil_to_grid, parse_stress_ratio
from .theme import current_colors

MIXED = '多种值'


def stress_bin_summary(component, result):
    """Keep every selected stress axis visible before a potentially long formula."""
    symbols = {'stress_ratio': 'S', 'voltage_ratio': 'SV', 'current_ratio': 'SI', 'power_ratio': 'SP'}
    if component.disclosure:
        bins = [(symbols.get(entry['key'], entry['label']), entry['value'])
                for entry in component.disclosure.get('stress_bins', [])]
    elif result.stress_adjustments:
        bins = [(symbols.get(entry.key, entry.label), entry.selected) for entry in result.stress_adjustments]
    else:
        bins = []
        for spec in MODELS[component.model]['fields']:
            if (spec.stress_max is None or spec.key not in component.parameters
                    or not spec.active(component.parameters)):
                continue
            try:
                ratio = parse_stress_ratio(component.parameters[spec.key], component.stress_variables)
                bins.append((symbols.get(spec.key, spec.label), ceil_to_grid(ratio, spec.choices)))
            except ValueError:
                continue
    return '[档位 ' + '，'.join(f'{label}={number_text(value)}' for label, value in bins) + ']' if bins else ''


def input_values(component, *, stress=False):
    if component.disclosure:
        return ({'stress_bins': component.disclosure.get('stress_bins', [])} if stress
                else {'disclosure': component.disclosure})
    if not stress and component.model in ('已知单件失效率', '工程模块'):
        return {'condition_rates': component.condition_rate_entries(),
                'module_source': component.module_source} if component.model == '工程模块' else {
                    'condition_rates': component.condition_rate_entries()}
    values = {spec.key: component.parameters[spec.key] for spec in MODELS[component.model]['fields']
            if spec.scope == 'device' and (spec.section == '工作应力') == stress
            and spec.key in component.parameters and spec.active(component.parameters)}
    if stress and component.stress_variables:
        values['variables'] = component.stress_variables
    sources = {spec.key: component.input_sources[spec.key] for spec in MODELS[component.model].fields
               if spec.key in component.input_sources and (spec.section == '工作应力') == stress}
    if sources:
        values['input_sources'] = sources
    return values


def group_value(component, key):
    effective_model = component.disclosure['model'] if component.disclosure else component.model
    if key == 'model':
        return effective_model
    if key == 'origin':
        return component.is_domestic
    if key in ('parameters', 'stress'):
        # Compare exact stored values, never formatted text across table boundaries.
        return (effective_model, json.dumps(input_values(component, stress=key == 'stress'),
                                            sort_keys=True, ensure_ascii=False))
    row = component.bom.get('row', {})
    if key not in row:
        # An unknown field is not evidence of an identical component.
        return ('unknown', component.identifier)
    if key == 'Value':
        value = nominal_value(component)
        if value is not None:
            return ('nominal', value.kind, value.decimal_value)
    return row[key]


@dataclass(eq=False)
class Node:
    component_rows: list[int]
    values: dict
    parent_node: object = None
    children: list = field(default_factory=list)
    position: int = 0
    key: tuple = ()
    group_key: tuple = ()
    group_size: int = 1
    member_index: int = 0
    display_index: str = ''


class ComponentTree(QAbstractItemModel):
    domestic_change_requested = pyqtSignal(list, bool)

    def __init__(self, project, parent=None, *, context=None):
        super().__init__(parent)
        self.project = project
        self.context = context
        self._rendered_inputs = None
        self._rendered_context = None
        self._share_denom = None
        self.expanded_keys = set()
        self.force_expand = False
        self._grouped_entries = []
        self.roots = []
        self.leaves = {}
        self.values = []
        self.refresh()

    def refresh(self, *, force=False):
        context = self.context or self.project.calculate(self.project.conditions[0])
        inputs = (
            self.project.components,
            self.project.grouped,
            self.project.group_fields,
            self.project.prediction_state,
        )
        # The calculate button may immediately follow an automatic environment
        # refresh. Preserve the existing projection when both inputs and results
        # are identical. A deep snapshot detects in-place edits, including two
        # different expressions that resolve to the same standard bin.
        if not force and inputs == self._rendered_inputs and context == self._rendered_context:
            return
        self.beginResetModel()
        self.roots, self.leaves, self.values = [], {}, []
        grouped, displayed_inputs = {}, {}
        calculation_enabled = context.enabled
        denom = (
            context.total_rate if (context.total_rate is not None and context.total_rate > 0)
            else context.known_rate if (context.known_rate is not None and context.known_rate > 0)
            else None
        )
        self._share_denom = denom
        for row, c in enumerate(self.project.components):
            result = context.evaluate(c)
            display_key = None
            if result.rate is not None and not c.disclosure and c.model not in ('已知单件失效率', '工程模块'):
                try:
                    display_key = (c.model, json.dumps((c.parameters, c.stress_variables, c.input_sources),
                        sort_keys=True, ensure_ascii=False, allow_nan=False), result.stress_adjustments)
                except (TypeError, ValueError):
                    pass
            texts = displayed_inputs.get(display_key) if display_key is not None else None
            if texts is None:
                texts = (input_text(c, environment=context.environment,
                                    ambient_temperature_C=context.ambient_temperature_C, result=result),
                         input_text(c, stress=True, result=result))
                if display_key is not None:
                    displayed_inputs[display_key] = texts
            values = dict(c.bom.get('row', {}))
            nominal = nominal_value(c)
            if nominal is not None:
                values['Value'] = standard_nominal_text(nominal)
            weighted_val = result.weighted(c.quantity)
            share_val = (weighted_val / denom * 100.0) if (weighted_val is not None and denom) else None
            config_error = is_component_config_error(
                c.model, result, calculation_enabled=calculation_enabled
            )
            effective_model = c.disclosure['model'] if c.disclosure else c.model
            spec = MODELS.get(effective_model)
            category = (
                None
                if effective_model == '待配置' and result.rate is None
                else (spec.category if spec is not None and spec.category else 'OTHER')
            )
            values.update(index=row + 1, designators=', '.join(c.bom.get('assigned_designators', [])) or c.name,
                          quantity=c.quantity,
                          model=model_label(effective_model),
                          raw_model=effective_model,
                          category=category,
                          origin=c.is_domestic,
                          parameters=texts[0], stress=texts[1],
                          stress_summary=stress_bin_summary(c, result),
                          rate=result.rate, weighted=weighted_val, share=share_val,
                          calculation_enabled=calculation_enabled,
                          config_error=config_error,
                          status=(
                              '未计算' if not calculation_enabled else
                              '配置异常' if config_error else
                              '待配置' if result.rate is None else
                              '已知值' if c.model == '已知单件失效率' else
                              '已计算'
                          ),
                          error=result.error,
                          search=' '.join(part for part in (
                              c.name,
                              '国产' if c.is_domestic else '进口',
                              model_name(effective_model),
                              effective_model,
                          ) if part))
            if c.model == '国产硅场效应晶体管' and result.stress_adjustments:
                selected = result.stress_adjustments[0].selected
                values['stress'] += f'；S [档位 {number_text(selected)}]'
            self.values.append(values)
            key = tuple(group_value(c, k) for k in self.project.group_fields)
            if not self.project.grouped:
                key = (c.identifier,)
            grouped.setdefault(key, []).append(row)
        self._grouped_entries = list(grouped.items())
        valid_keys = {key for key, rows in self._grouped_entries if len(rows) > 1}
        self.expanded_keys &= valid_keys
        self._build_flat_rows()
        self._rendered_inputs = copy.deepcopy(inputs)
        self._rendered_context = context
        self.endResetModel()

    def _build_flat_rows(self):
        self.roots, self.leaves = [], {}
        expand_all = self.force_expand
        for group_num, (key, rows) in enumerate(self._grouped_entries, start=1):
            if len(rows) == 1:
                row = rows[0]
                node = Node(rows, self.values[row], position=len(self.roots), key=key,
                            group_key=(), group_size=1, member_index=0, display_index=str(group_num))
                self.leaves[row] = node
                self.roots.append(node)
            elif expand_all or key in self.expanded_keys:
                # Emit only member rows when expanded so the summary row never double-counts 占比 (%).
                for member_index, row in enumerate(rows, start=1):
                    prefix = '▾ ' if member_index == 1 else '  '
                    member = Node([row], self.values[row], position=len(self.roots), key=key,
                                  group_key=key, group_size=len(rows), member_index=member_index,
                                  display_index=f'{prefix}{group_num}.{member_index}')
                    self.leaves[row] = member
                    self.roots.append(member)
            else:
                values = self.aggregate(rows)
                node = Node(list(rows), values, position=len(self.roots), key=key,
                            group_key=key, group_size=len(rows), member_index=0,
                            display_index=f'▸ {group_num}')
                for row in rows:
                    self.leaves[row] = node
                self.roots.append(node)

    def _reset_flat_projection(self):
        self.beginResetModel()
        self._build_flat_rows()
        self.endResetModel()

    def is_group_expanded(self, group_key):
        return bool(group_key) and (self.force_expand or group_key in self.expanded_keys)

    def toggle_group(self, group_key):
        if not group_key or not self.project.grouped or self.force_expand:
            return False
        if group_key in self.expanded_keys:
            self.expanded_keys.remove(group_key)
        else:
            self.expanded_keys.add(group_key)
        self._reset_flat_projection()
        return True

    def expand_group_for_row(self, row):
        for key, rows in self._grouped_entries:
            if row in rows and len(rows) > 1 and key not in self.expanded_keys:
                self.expanded_keys.add(key)
                self._reset_flat_projection()
                return True
        return False

    def expand_all(self):
        target = {key for key, rows in self._grouped_entries if len(rows) > 1}
        if target == self.expanded_keys:
            return False
        self.expanded_keys = target
        self._reset_flat_projection()
        return True

    def collapse_all(self):
        if not self.expanded_keys:
            return False
        self.expanded_keys.clear()
        self._reset_flat_projection()
        return True

    def set_force_expand(self, active):
        active = bool(active)
        if active == self.force_expand:
            return False
        self.force_expand = active
        self._reset_flat_projection()
        return True

    def aggregate(self, rows):
        values = {}
        for column in COLUMNS:
            candidates = [self.values[r].get(column.key, '') for r in rows]
            values[column.key] = candidates[0] if all(v == candidates[0] for v in candidates) else MIXED
            if column.key in ('parameters', 'stress') and len({group_value(self.project.components[r], column.key) for r in rows}) > 1:
                values[column.key] = MIXED
        values['designators'] = ', '.join(self.values[r]['designators'] for r in rows)
        values['index'] = rows[0] + 1
        quantities = [self.values[r]['quantity'] for r in rows]
        values['quantity'] = sum(quantities) if all(q is not None for q in quantities) else None
        rates = [self.values[r]['weighted'] for r in rows]
        subtotal, error = sum_rates(value for value in rates if value is not None)
        weighted = subtotal if all(value is not None for value in rates) else None
        values['weighted'] = weighted
        denom = getattr(self, '_share_denom', None)
        values['share'] = (weighted / denom * 100.0) if (weighted is not None and denom) else None
        values['error'] = error
        error_count = sum(bool(self.values[r].get('config_error')) for r in rows)
        incomplete_count = sum(
            self.values[r]['rate'] is None and not self.values[r].get('config_error')
            for r in rows
        )
        values['calculation_enabled'] = all(self.values[r]['calculation_enabled'] for r in rows)
        values['config_error'] = error_count > 0 or bool(error)
        if not error and error_count:
            child_errors = [
                f"{self.values[r]['designators']}：{self.values[r]['error']}"
                for r in rows if self.values[r].get('config_error') and self.values[r].get('error')
            ]
            values['error'] = '\n'.join(child_errors[:5])
            if len(child_errors) > 5:
                values['error'] += f'\n另有 {len(child_errors) - 5} 项配置异常，展开查看明细。'
        values['status'] = (
            '未计算' if not values['calculation_enabled'] else
            '合计错误' if error else
            f'{incomplete_count} 项待配置 · {error_count} 项异常' if (error_count and incomplete_count) else
            f'{error_count} 项配置异常' if error_count else
            f'{incomplete_count} 项待配置' if incomplete_count else
            '已计算'
        )
        categories = {self.values[r].get('category') for r in rows}
        values['category'] = next(iter(categories)) if len(categories) == 1 else None
        summaries = [self.values[row].get('stress_summary', '') for row in rows]
        values['stress_summary'] = summaries[0] if all(value == summaries[0] for value in summaries) else ''
        return values

    def index(self, row, column, parent=QModelIndex()):
        if row < 0 or column < 0 or column >= len(COLUMNS) or parent.isValid() and parent.column() != 0:
            return QModelIndex()
        nodes = parent.internalPointer().children if parent.isValid() else self.roots
        if row >= len(nodes):
            return QModelIndex()
        return self.createIndex(row, column, nodes[row])

    def parent(self, index):
        if not index.isValid() or index.internalPointer().parent_node is None:
            return QModelIndex()
        node = index.internalPointer().parent_node
        return self.createIndex(node.position, 0, node)

    def component_index(self, row, column=0):
        node = self.leaves[row]
        return self.createIndex(node.position, column, node)

    def component_rows(self, index):
        return index.internalPointer().component_rows if index.isValid() else []

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return len(parent.internalPointer().children) if parent.column() == 0 else 0
        return len(self.roots)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        return self.cell_data(index.internalPointer(), index.column(), role)

    def flags(self, index):
        flags = super().flags(index)
        if index.isValid() and COLUMNS[index.column()].key == 'origin':
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role=Qt.ItemDataRole.EditRole):
        if (not index.isValid() or COLUMNS[index.column()].key != 'origin'
                or role != Qt.ItemDataRole.CheckStateRole):
            return False
        return self.request_domestic(self.component_rows(index), value)

    def request_domestic(self, rows, value):
        state = value.value if isinstance(value, Qt.CheckState) else value
        if state not in (Qt.CheckState.Unchecked.value, Qt.CheckState.Checked.value) or not rows:
            return False
        self.domestic_change_requested.emit(list(rows), state == Qt.CheckState.Checked.value)
        return True

    def cell_data(self, node, column, role):
        key = COLUMNS[column].key
        if key == 'index':
            if role == Qt.ItemDataRole.DisplayRole:
                return node.display_index or str(node.values.get('index', node.position + 1))
            if role == Qt.ItemDataRole.ToolTipRole:
                if node.group_size > 1:
                    return (f'已展开分组成员（共 {node.group_size} 项）；点击此处折叠为汇总行。' if node.member_index
                            else f'分组汇总（共 {node.group_size} 项）；点击 ▸ 展开为各独立位号（不保留重复汇总行）。')
                return node.display_index or str(node.values.get('index', node.position + 1))
            if role == Qt.ItemDataRole.UserRole:
                return node.values.get('index', node.position + 1)
        value = node.values.get(key, '')
        if key == 'origin':
            if role == Qt.ItemDataRole.CheckStateRole:
                return (Qt.CheckState.PartiallyChecked if value == MIXED else
                        Qt.CheckState.Checked if value else Qt.CheckState.Unchecked)
            if role == Qt.ItemDataRole.DisplayRole:
                return None
            if role == Qt.ItemDataRole.ToolTipRole:
                return ('组内国产选项不同；勾选或取消只应用于当前显示的位号。' if value == MIXED else
                        '勾选为国产，未勾选为进口。切换后重新选择适用的器件类型与参数。')
        if role == Qt.ItemDataRole.UserRole:
            return value
        if role == Qt.ItemDataRole.ToolTipRole:
            if key == 'share':
                text = f'失效率占比：{value:.2f}%' if value is not None and value != '' else '尚无完整计算结果'
            else:
                text = node.values.get('error', '') if value is None else number_text(value)
            if key in ('stress', 'status') and node.values.get('error'):
                text += '\n' + node.values['error']
            if key == 'Value':
                original = list(dict.fromkeys(self.project.components[row].bom.get('row', {}).get('Value', '')
                                              for row in node.component_rows))
                if any(item and item != value for item in original):
                    text += '\nBOM 原值：' + '；'.join(original)
            if node.group_size > 1 and node.member_index == 0:
                text += f'\n共 {node.group_size} 项；点击 ▸ 展开查看各位号（不重复显示汇总行），双击批量配置。'
                if key == 'weighted' and value is None and not node.values.get('error'):
                    text += ('\n此工况未勾选计算。' if not node.values.get('calculation_enabled', True)
                             else '\n含待配置项，暂不显示整组失效率。')
            return text
        if role == Qt.ItemDataRole.DisplayRole:
            if key == 'share':
                if not node.values.get('calculation_enabled', True) or value is None or value == '':
                    return '—'
                return f'{value:.2f}%'
            if key == 'stress' and value != MIXED and node.values.get('stress_summary'):
                body = re.sub(r'，档位 [^\[\]]+\]', ']', value)
                body = re.sub(r' \[档位 [^\[\]]+\]', '', body)
                return node.values['stress_summary'] + ' ' + body
            if key in ('rate', 'weighted') and not node.values.get('calculation_enabled', True):
                return '—'
            if key in ('rate', 'weighted') and value is None:
                if node.values.get('status') == '合计错误':
                    return '合计错误'
                if node.values.get('config_error'):
                    return '配置异常'
                return '待配置'
            return ('待填写' if key == 'quantity' else '待配置') if value is None else number_text(value) if value != '' else '—'
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if key == 'status':
                return Qt.AlignmentFlag.AlignCenter
            if key in ('quantity', 'rate', 'weighted', 'share'):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.FontRole and ((node.group_size > 1 and node.member_index == 0)
                                                or (key == 'index' and node.group_size > 1)):
            font = QFont()
            font.setBold(True)
            return font
        if role == Qt.ItemDataRole.ForegroundRole:
            colors = current_colors()
            if key == 'index' and node.group_size > 1:
                return QColor(colors['accent'])
            if key == 'status' and node.values.get('config_error'):
                return QColor(colors['error'])
            if key == 'status' and (str(value).startswith('已计算') or value == '已知值'):
                return QColor(colors['success'])
            if key == 'status' and value == '未计算':
                return QColor(colors['muted'])
            if value == MIXED or key == 'status' and ('待配置' in str(value) or value == '合计错误'):
                return QColor(colors['warning'])
        return None

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            title = COLUMNS[section].label
            if self.project.prediction_state == 'nonoperating':
                title = title.replace('工作应力', '非工作条件').replace('λp', 'λNP')
            return title
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole:
            key = COLUMNS[section].key
            if key in ('origin', 'status'):
                return Qt.AlignmentFlag.AlignCenter
            if key in ('quantity', 'rate', 'weighted', 'share'):
                return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        return None


def _is_finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


class ShareBarDelegate(QStyledItemDelegate):
    """Draw a subtle proportional horizontal fill bar behind percentage share cells."""

    def paint(self, painter, option, index):
        pct = index.data(Qt.ItemDataRole.UserRole)
        if _is_finite_number(pct) and pct > 0 and not (option.state & QStyle.StateFlag.State_Selected):
            rect = option.rect.adjusted(3, 4, -3, -4)
            if rect.width() > 4 and rect.height() > 2:
                bar_w = max(2, int(rect.width() * min(float(pct), 100.0) / 100.0))
                painter.save()
                painter.fillRect(
                    rect.left(),
                    rect.top(),
                    bar_w,
                    rect.height(),
                    QColor(current_colors()['share_bar']),
                )
                painter.restore()
        super().paint(painter, option, index)


class ComponentOrder(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setRecursiveFilteringEnabled(False)
        self.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.status_filter = 'all'
        self.category_filter = None

    def set_status_filter(self, mode):
        self.status_filter = mode if mode in ('all', 'pending', 'calculated', 'error') else 'all'
        self.invalidateFilter()

    def set_category_filter(self, category):
        self.category_filter = category
        self.invalidateFilter()

    def has_active_filter(self):
        return bool(self.filterRegularExpression().pattern()
                    or self.status_filter != 'all'
                    or self.category_filter is not None)

    @staticmethod
    def _numeric_sort_key(source, node, field, row):
        val = node.values.get(field) if node is not None else None
        if (
            field == 'rate'
            and val == MIXED
            and node is not None
            and node.group_size > 1
            and node.member_index == 0
        ):
            val = max(
                (
                    v
                    for r in node.component_rows
                    if r < len(source.values) and _is_finite_number(v := source.values[r].get('rate'))
                ),
                default=None,
            )
        return (0, -float(val), row) if _is_finite_number(val) else (1, 0.0, row)

    def lessThan(self, left, right):
        source = self.sourceModel()
        lrow = min(source.component_rows(left))
        rrow = min(source.component_rows(right))
        mode = source.project.sort_mode
        if mode == 'model':
            return (model_sort_key(source.project.components[lrow].model), lrow) < (
                model_sort_key(source.project.components[rrow].model), rrow)
        if mode in ('weighted_desc', 'rate_desc', 'quantity_desc'):
            field = {'weighted_desc': 'weighted', 'rate_desc': 'rate', 'quantity_desc': 'quantity'}[mode]
            lnode = left.internalPointer()
            rnode = right.internalPointer()
            return self._numeric_sort_key(source, lnode, field, lrow) < self._numeric_sort_key(
                source, rnode, field, rrow
            )
        return lrow < rrow

    def filterAcceptsRow(self, row, parent):
        if parent.isValid():
            return False
        index = self.sourceModel().index(row, 0, parent)
        node = index.internalPointer()
        return node is not None and self.matches(node)

    def matches(self, node):
        if self.status_filter == 'pending' and (node.values.get('rate') is not None or node.values.get('config_error')):
            return False
        if self.status_filter == 'calculated' and node.values.get('rate') is None:
            return False
        if self.status_filter == 'error' and not node.values.get('config_error'):
            return False
        if self.category_filter is not None and node.values.get('category') != self.category_filter:
            return False
        expression = self.filterRegularExpression()
        if not expression.pattern():
            return True
        searchable = [node.values.get(c.key, '') for c in COLUMNS if c.key not in ('index', 'share')]
        searchable.append(node.values.get('search', ''))
        return expression.match(' '.join(number_text(v) for v in searchable)).hasMatch()
