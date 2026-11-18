"""Unified .xlsx and .xls workbook export and import for both full reports and redacted deliveries."""

import math
import os
import re
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import openpyxl
import xlrd
import xlwt
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from . import APP_VERSION
from .domain import (
    RATE_UNIT,
    Component,
    OperatingCondition,
    Project,
    quantity_text,
    rate_condition_key,
)
from .excel_limits import MAX_PACKAGE_BYTES, check_package
from .numeric import number_text
from .parameter_disclosure import (
    disclosure_stress_text,
    stress_bin_specs,
    validate_stress_bins,
)
from .prediction import STATES, model_state
from .redacted_export import build_redacted_project
from .report_data import component_report_values, display_lines, row_height
from .standards import ENVIRONMENTS, MODELS, model_name
from .storage import (
    RevisionConflict,
    _save_lock,
    atomic_replace,
    exclusive_commit,
    preserve_or_default_mode,
)
from .workbook_reader import _read_xls, _read_xlsx

OVERVIEW_SHEET = '预计总览'

DETAIL_HEADER_ROW = 1
MAX_VISIBLE_LINES = 24
XLS_MAX_ROWS = 65536
XLS_MAX_UTF16_UNITS = 32767
INVALID_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f]')

DETAIL_WIDTHS = [8, 26, 20, 10, 8, 22, 36, 30, 52, 20, 20, 12, 18]
SUMMARY_WIDTHS = [7, 24, 12, 14, 12, 12, 14, 22, 18, 32]

SUMMARY_HEADERS = [
    '序号', '工况名称', '环境类别', '环境温度 (°C)', '预计状态',
    '元器件项数', '元器件总数量', f'预计总失效率 ({RATE_UNIT})', '预计 MTBF (h)',
    '明细工作表',
]

_MODEL_BY_DISPLAY = {model_name(key): key for key in MODELS}


def detail_headers(state):
    symbol = 'λNP' if state == 'nonoperating' else 'λp'
    stress = '非工作条件' if state == 'nonoperating' else '工作应力'
    return [
        '序号', '位号 / 名称', '原型号', '国产/进口', '数量', '计算模型',
        '参数与公开档位', stress, '计算过程 / 说明',
        f'单件 {symbol} ({RATE_UNIT})', f'小计 N{symbol} ({RATE_UNIT})',
        '占比 (%)', '状态',
    ]


def unique_sheet_name(name, used):
    cleaned = INVALID_CONTROL_CHARS.sub('', str(name)).replace('\r', '').replace('\n', '').replace('\t', '')
    base = re.sub(r'[\[\]:*?/\\]+', '_', cleaned).strip("'_ ")[:31].strip("'_ ") or '工作表'
    if base.lower() == 'history':
        base = '工作表'
    candidate, suffix = base, 1
    while candidate.lower() in used or candidate.lower() == 'history':
        suffix += 1
        ending = f'_{suffix}'
        stem = base[:31 - len(ending)].strip("'_ ") or '工作表'
        candidate = stem + ending
    used.add(candidate.lower())
    return candidate


def condition_sheet_name(condition, used, index=1):
    """Build a clean Excel sheet name formatted as 工况名称_环境类别@温度°C."""
    raw = (condition.name or '').replace('℃', '°C').strip()
    cleaned = re.sub(r'[\[\]:*?/\\]+', '_', raw).strip("'_ ") or f'工况 {index}'
    env_text = condition.environment or '未填'
    temp_text = (f'{number_text(condition.ambient_temperature_C)}°C'
                 if condition.ambient_temperature_C is not None else '未填')
    tag = f'_{env_text}@{temp_text}'
    if len(tag) >= 30:
        tag = f'_{env_text}'[:16]
    head = cleaned[:max(1, 31 - len(tag))].rstrip('_ ')
    base = f'{head}{tag}'[:31].rstrip('_')
    return unique_sheet_name(base, used)


def _text_chunks(value, width):
    if not isinstance(value, str):
        return [value]
    if display_lines(value, width) <= MAX_VISIBLE_LINES:
        return [value]
    wrapped = []
    for line in value.split('\n'):
        current, occupied = '', 0
        for character in line:
            size = 2 if unicodedata.east_asian_width(character) in ('W', 'F') else 1
            if current and occupied + size > width - 2:
                wrapped.append(current)
                current, occupied = '', 0
            current += character
            occupied += size
        wrapped.append(current)
    return ['\n'.join(wrapped[start:start + MAX_VISIBLE_LINES])
            for start in range(0, len(wrapped), MAX_VISIBLE_LINES)]


def _sanitize_row(values, *, check_xls_limit=False):
    cleaned = []
    for value in values:
        if isinstance(value, str):
            text = INVALID_CONTROL_CHARS.sub('', value).replace('℃', '°C')
            if len(text.encode('utf-16-le')) // 2 > XLS_MAX_UTF16_UNITS:
                label = '.xls' if check_xls_limit else 'Excel'
                raise ValueError(f'单元格文本超过 {label} 的 32767 字符限制，请缩短对应内容后再导出')
            cleaned.append(text)
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            try:
                is_fin = math.isfinite(value)
            except OverflowError as error:
                raise ValueError('导出数值必须有限') from error
            if not is_fin:
                raise ValueError('导出数值必须有限')
            cleaned.append(value)
        else:
            cleaned.append(value)
    return cleaned


def _format_disclosure_parameters(disclosure):
    ranges = disclosure.get('parameter_ranges', [])
    if not ranges:
        return '公开参数档位：内部'
    lines = ['公开参数档位：']
    for item in ranges:
        lines.append(f"{item['name']}：{item['range']} [{item['source']}]")
    return '\n'.join(lines)


def _expand_detail_rows(values):
    text_columns = (8, 12)
    chunks = {col: _text_chunks(values[col], DETAIL_WIDTHS[col]) for col in text_columns}
    pages = max(len(parts) for parts in chunks.values())
    result = []
    for page in range(pages):
        row = list(values) if page == 0 else [None] * len(values)
        if page:
            row[0] = f'{values[0]} [续 {page + 1}]'
            row[1] = f'{values[1]} [续 {page + 1}]' if values[1] else ''
            row[3] = values[3]
            row[5] = values[5]
        for col, parts in chunks.items():
            row[col] = parts[page] if page < len(parts) else None
        result.append(row)
    return result


def _component_detail_row(component, result, condition, index, state, share_denom, *, redacted):
    base = component_report_values(component, result, condition, index, prediction_state=state)
    is_redacted_item = redacted or component.disclosure is not None
    origin = '国产' if component.is_domestic else '进口'
    effective_model = component.disclosure['model'] if component.disclosure else component.model
    model_cell = model_name(effective_model)
    is_auto_anon = component.disclosure is not None and bool(re.fullmatch(r'\d{6}', base[1]))
    name_cell = '' if (redacted or is_auto_anon) else base[1]
    mpn_cell = '' if is_redacted_item else base[2]
    if component.disclosure is not None:
        params_cell = _format_disclosure_parameters(component.disclosure)
        stress_cell = disclosure_stress_text(component.disclosure).replace('；', '\n')
    else:
        params_cell = base[5]
        stress_cell = base[6]
    rate_val = result.rate
    weighted_val = result.weighted(component.quantity)
    share_val = (weighted_val / share_denom * 100.0) if (weighted_val is not None and share_denom) else None
    status_cell = result.error if rate_val is None else '已计算'
    return [
        index + 1,
        name_cell,
        mpn_cell,
        origin,
        component.quantity,
        model_cell,
        params_cell,
        stress_cell,
        base[7],
        rate_val,
        weighted_val,
        share_val,
        status_cell,
    ]


def _build_workbook_model(project, *, redacted=False, _already_redacted=False):
    project.validate()
    if redacted and not _already_redacted:
        project = build_redacted_project(project)
    if not project.components:
        raise ValueError('工程没有预计对象，无法导出计算数据')
    enabled = project.enabled_conditions
    contexts = [(condition, project.calculate(condition)) for condition in enabled]
    if not contexts:
        raise ValueError('请至少勾选一种工作环境参与计算')
    used = {OVERVIEW_SHEET.lower()}

    state = project.prediction_state
    headers = detail_headers(state)

    summary_rows = []
    condition_sheets = []
    total_qty = (sum(c.quantity for c in project.components)
                 if project.components and all(c.quantity is not None for c in project.components)
                 else quantity_text(project.components))

    for cond_idx, (condition, context) in enumerate(contexts, start=1):
        title = condition_sheet_name(condition, used, cond_idx)
        mtbf_cell = (
            context.mtbf_hours
            if context.mtbf_hours is not None
            else ('∞' if getattr(context, 'mtbf_status', '') == 'infinite' else None)
        )
        summary_rows.append([
            cond_idx,
            condition.name,
            condition.environment,
            condition.ambient_temperature_C,
            STATES[state],
            len(project.components),
            total_qty,
            context.total_rate,
            mtbf_cell,
            title,
        ])
        denom = (context.total_rate if (context.total_rate is not None and context.total_rate > 0)
                 else context.known_rate if (context.known_rate is not None and context.known_rate > 0)
                 else None)
        rows = []
        for idx, component in enumerate(project.components):
            values = _component_detail_row(
                component, context.evaluate(component), condition, idx, state, denom, redacted=redacted,
            )
            rows.extend(_expand_detail_rows(values))
        condition_sheets.append((title, headers, rows))

    meta_rows = [
        ['工程名称', project.name, '依据标准', 'GJB/Z 299D—2024', '预计状态', STATES[state]],
        ['失效率单位', RATE_UNIT, '版本', APP_VERSION, None, None],
    ]
    if not redacted and project.notes.strip():
        meta_rows.append(['工程备注', project.notes.strip(), None, None, None, None])
    overview = {'meta_rows': meta_rows, 'summary_rows': summary_rows}

    return overview, condition_sheets


def _atomic_write_file(destination, writer_fn, suffix, *, exclusive=False):
    destination = Path(destination).resolve()
    with _save_lock(destination):
        if exclusive and destination.exists():
            raise RevisionConflict('目标已存在，请换一个文件名')
        descriptor, temp_name = tempfile.mkstemp(prefix='.gjb299d-', suffix=suffix, dir=destination.parent)
        os.close(descriptor)
        temporary = Path(temp_name)
        try:
            writer_fn(temporary)
            # Windows FlushFileBuffers requires write access on the handle.
            with temporary.open('r+b') as handle:
                os.fsync(handle.fileno())
            preserve_or_default_mode(temporary, destination)
            if exclusive:
                exclusive_commit(temporary, destination, '目标已存在，请换一个文件名')
            else:
                atomic_replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Unified .xlsx writer
# ---------------------------------------------------------------------------

def _xlsx_num_format(value, *, is_share=False):
    if is_share and isinstance(value, (int, float)) and not isinstance(value, bool):
        return '0.00'
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value and (abs(value) < 1e-6 or abs(value) >= 1e12):
            return '0.##############E+00'
        if value == int(value):
            return '0'
        return '0.###############'
    return '@'


def _xlsx_styles():
    border_side = Side(style='thin', color='CBD5E1')
    head_side = Side(style='thin', color='163A59')
    border = Border(left=border_side, right=border_side, top=border_side, bottom=border_side)
    head_border = Border(left=head_side, right=head_side, top=head_side, bottom=head_side)
    header_fill = PatternFill('solid', fgColor='1F4E78')
    key_fill = PatternFill('solid', fgColor='E8F0F8')
    alt_fill = PatternFill('solid', fgColor='F6F9FC')
    plain_fill = PatternFill('solid', fgColor='FFFFFF')
    return {
        'header': (Font(name='Microsoft YaHei', size=10, bold=True, color='FFFFFF'),
                   header_fill, Alignment(horizontal='center', vertical='center', wrap_text=True), head_border),
        'key': (Font(name='Microsoft YaHei', size=10, bold=True, color='102A43'),
                key_fill, Alignment(horizontal='left', vertical='center', wrap_text=True), border),
        'alt_left': (Font(name='Microsoft YaHei', size=10, color='1F2937'),
                     alt_fill, Alignment(horizontal='left', vertical='center', wrap_text=True), border),
        'alt_right': (Font(name='Microsoft YaHei', size=10, color='1F2937'),
                      alt_fill, Alignment(horizontal='right', vertical='center', wrap_text=True), border),
        'alt_center': (Font(name='Microsoft YaHei', size=10, color='1F2937'),
                       alt_fill, Alignment(horizontal='center', vertical='center', wrap_text=True), border),
        'plain_left': (Font(name='Microsoft YaHei', size=10, color='1F2937'),
                       plain_fill, Alignment(horizontal='left', vertical='center', wrap_text=True), border),
        'plain_right': (Font(name='Microsoft YaHei', size=10, color='1F2937'),
                        plain_fill, Alignment(horizontal='right', vertical='center', wrap_text=True), border),
        'plain_center': (Font(name='Microsoft YaHei', size=10, color='1F2937'),
                         plain_fill, Alignment(horizontal='center', vertical='center', wrap_text=True), border),
    }


def _apply_xlsx_cell(cell, value, style_tuple, *, is_share=False):
    font, fill, alignment, border = style_tuple
    if value is None:
        cell.value = None
    elif isinstance(value, str):
        cell.value = value
        cell.data_type = 's'
    else:
        cell.value = value
    cell.font = font
    cell.fill = fill
    cell.alignment = alignment
    cell.border = border
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        cell.number_format = _xlsx_num_format(value, is_share=is_share)


def _setup_xlsx_sheet(sheet, widths, freeze_cell):
    sheet.views.sheetView[0].showGridLines = True
    sheet.page_setup.orientation = sheet.ORIENTATION_LANDSCAPE
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    if freeze_cell:
        sheet.freeze_panes = freeze_cell
    for col_idx, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(col_idx)].width = width


def export_workbook_xlsx(path, project, *, redacted=False, exclusive=False, _already_redacted=False):
    """Export unified workbook to .xlsx cleanly without overwriting redacted deliverables if exclusive."""
    overview, condition_sheets = _build_workbook_model(
        project, redacted=redacted, _already_redacted=_already_redacted
    )
    styles = _xlsx_styles()
    book = openpyxl.Workbook()
    book.remove(book.active)

    # Sheet 1: Overview & Multi-Condition Summary
    ov_ws = book.create_sheet(title=OVERVIEW_SHEET)
    sep_row = len(overview['meta_rows']) + 1
    summary_header_row = sep_row + 1
    _setup_xlsx_sheet(ov_ws, SUMMARY_WIDTHS, f'C{summary_header_row + 1}')
    for r_idx, meta in enumerate(overview['meta_rows'], start=1):
        clean_meta = _sanitize_row(meta)
        for c_idx, val in enumerate(clean_meta, start=1):
            role = 'key' if (c_idx in (1, 3, 5) and val is not None) else 'plain_left'
            _apply_xlsx_cell(ov_ws.cell(row=r_idx, column=c_idx), val, styles[role])
        ov_ws.row_dimensions[r_idx].height = max(22, row_height([str(x or '') for x in clean_meta], [18, 28, 16, 20, 16, 22]) / 20.0)
    # Emit blank separator row explicitly so iter_rows preserves row indices.
    ov_ws.cell(row=sep_row, column=1, value=None)
    ov_ws.row_dimensions[sep_row].height = 10

    for c_idx, title in enumerate(SUMMARY_HEADERS, start=1):
        _apply_xlsx_cell(ov_ws.cell(row=summary_header_row, column=c_idx), title, styles['header'])
    ov_ws.row_dimensions[summary_header_row].height = 28

    for offset, row_vals in enumerate(overview['summary_rows']):
        r_idx = summary_header_row + 1 + offset
        clean_row = _sanitize_row(row_vals)
        prefix = 'alt' if offset % 2 == 1 else 'plain'
        for c_idx, val in enumerate(clean_row, start=1):
            align = ('center' if c_idx in (1, 3, 5) else
                     'right' if isinstance(val, (int, float)) and not isinstance(val, bool) else 'left')
            _apply_xlsx_cell(ov_ws.cell(row=r_idx, column=c_idx), val, styles[f'{prefix}_{align}'])
        ov_ws.row_dimensions[r_idx].height = max(22, row_height(clean_row, SUMMARY_WIDTHS) / 20.0)

    # Condition Detail Sheets (Row 1 = direct table headers)
    for title, headers, rows in condition_sheets:
        ws = book.create_sheet(title=title)
        _setup_xlsx_sheet(ws, DETAIL_WIDTHS, 'C2')
        for c_idx, header in enumerate(headers, start=1):
            _apply_xlsx_cell(ws.cell(row=DETAIL_HEADER_ROW, column=c_idx), header, styles['header'])
        ws.row_dimensions[DETAIL_HEADER_ROW].height = 28
        for offset, row_vals in enumerate(rows):
            r_idx = DETAIL_HEADER_ROW + 1 + offset
            clean_row = _sanitize_row(row_vals)
            prefix = 'alt' if offset % 2 == 1 else 'plain'
            for c_idx, val in enumerate(clean_row, start=1):
                is_num = isinstance(val, (int, float)) and not isinstance(val, bool)
                align = 'center' if c_idx in (1, 4) and not is_num or c_idx == 4 else ('right' if is_num else 'left')
                _apply_xlsx_cell(ws.cell(row=r_idx, column=c_idx), val, styles[f'{prefix}_{align}'], is_share=(c_idx == 12))
            ws.row_dimensions[r_idx].height = max(22, row_height(clean_row, DETAIL_WIDTHS) / 20.0)

    _atomic_write_file(path, lambda tmp: book.save(str(tmp)), '.xlsx', exclusive=exclusive)
    return 1 + len(condition_sheets)


# ---------------------------------------------------------------------------
# Unified .xls writer
# ---------------------------------------------------------------------------

def _xls_styles():
    base_font = 'font: name Microsoft YaHei, height 200, color black;'
    bold_font = 'font: name Microsoft YaHei, height 200, bold on, color black;'
    header_font = 'font: name Microsoft YaHei, height 200, bold on, color white;'
    border = 'borders: left thin, right thin, top thin, bottom thin;'
    styles = {
        'header': xlwt.easyxf(header_font + 'pattern: pattern solid, fore_colour dark_blue; align: horiz center, vert center, wrap on; ' + border),
        'key': xlwt.easyxf(bold_font + 'pattern: pattern solid, fore_colour ice_blue; align: horiz left, vert center, wrap on; ' + border),
        'text': xlwt.easyxf(base_font + 'align: horiz left, vert center, wrap on; ' + border),
        'center': xlwt.easyxf(base_font + 'align: horiz center, vert center, wrap on; ' + border),
        'integer': xlwt.easyxf(base_font + 'align: horiz right, vert center; ' + border, num_format_str='0'),
        'number': xlwt.easyxf(base_font + 'align: horiz right, vert center; ' + border, num_format_str='0.############'),
        'scientific': xlwt.easyxf(base_font + 'align: horiz right, vert center; ' + border, num_format_str='0.############E+00'),
        'share': xlwt.easyxf(base_font + 'align: horiz right, vert center; ' + border, num_format_str='0.00'),
    }
    return styles


def _write_xls_cell(sheet, row, col, value, styles, *, force_style=None, is_share=False):
    if force_style is not None:
        sheet.write(row, col, '' if value is None else value, styles[force_style])
        return
    if value is None or value == '':
        sheet.write(row, col, '', styles['text'])
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if is_share:
            fmt = 'share'
        elif value and (abs(value) < 1e-6 or abs(value) >= 1e12):
            fmt = 'scientific'
        elif value == int(value):
            fmt = 'integer'
        else:
            fmt = 'number'
        sheet.write(row, col, value, styles[fmt])
    else:
        sheet.write(row, col, value, styles['text'])


def export_workbook_xls(path, project, *, redacted=False, exclusive=False, _already_redacted=False):
    """Export unified workbook to .xls using the same layout as .xlsx."""
    overview, condition_sheets = _build_workbook_model(
        project, redacted=redacted, _already_redacted=_already_redacted
    )
    styles = _xls_styles()
    book = xlwt.Workbook(encoding='utf-8')

    # Sheet 1: Overview & Multi-Condition Summary
    ov_ws = book.add_sheet(OVERVIEW_SHEET)
    for r_idx, meta in enumerate(overview['meta_rows']):
        clean_meta = _sanitize_row(meta, check_xls_limit=True)
        for c_idx, val in enumerate(clean_meta):
            _write_xls_cell(
                ov_ws, r_idx, c_idx, val, styles,
                force_style=('key' if (c_idx in (0, 2, 4) and val is not None) else None),
            )
        ov_ws.row(r_idx).height = max(360, row_height([str(x or '') for x in clean_meta], [18, 28, 16, 20, 16, 22]))
        ov_ws.row(r_idx).height_mismatch = True

    sep_idx = len(overview['meta_rows'])
    ov_ws.write(sep_idx, 0, '', styles['text'])
    ov_ws.row(sep_idx).height = 180

    hdr_row = sep_idx + 1
    for c_idx, title in enumerate(SUMMARY_HEADERS):
        ov_ws.write(hdr_row, c_idx, title, styles['header'])
    ov_ws.row(hdr_row).height = 520
    ov_ws.row(hdr_row).height_mismatch = True

    for offset, row_vals in enumerate(overview['summary_rows']):
        r_idx = hdr_row + 1 + offset
        if r_idx >= XLS_MAX_ROWS:
            raise ValueError('预计总览行数超过 .xls 单工作表限制')
        clean_row = _sanitize_row(row_vals, check_xls_limit=True)
        for c_idx, val in enumerate(clean_row):
            force = 'center' if (c_idx in (0, 2, 4) and not (isinstance(val, (int, float)) and not isinstance(val, bool))) else None
            _write_xls_cell(ov_ws, r_idx, c_idx, val, styles, force_style=force)
        ov_ws.row(r_idx).height = max(360, row_height(clean_row, SUMMARY_WIDTHS))
        ov_ws.row(r_idx).height_mismatch = True
    for col, width in enumerate(SUMMARY_WIDTHS):
        ov_ws.col(col).width = width * 256
    ov_ws.set_panes_frozen(True)
    ov_ws.set_horz_split_pos(hdr_row + 1)
    ov_ws.set_vert_split_pos(2)

    # Condition Detail Sheets
    for title, headers, rows in condition_sheets:
        ws = book.add_sheet(title)
        for c_idx, header in enumerate(headers):
            ws.write(0, c_idx, header, styles['header'])
        ws.row(0).height = 540
        ws.row(0).height_mismatch = True
        for offset, row_vals in enumerate(rows):
            r_idx = 1 + offset
            if r_idx >= XLS_MAX_ROWS:
                raise ValueError('元器件计算明细行数超过 .xls 单工作表 65536 行限制')
            clean_row = _sanitize_row(row_vals, check_xls_limit=True)
            for c_idx, val in enumerate(clean_row):
                _write_xls_cell(ws, r_idx, c_idx, val, styles, is_share=(c_idx == 11))
            ws.row(r_idx).height = max(360, row_height(clean_row, DETAIL_WIDTHS))
            ws.row(r_idx).height_mismatch = True
        for col, width in enumerate(DETAIL_WIDTHS):
            ws.col(col).width = width * 256
        ws.set_panes_frozen(True)
        ws.set_horz_split_pos(1)
        ws.set_vert_split_pos(2)
        ws.set_portrait(False)

    _atomic_write_file(path, lambda tmp: book.save(str(tmp)), '.xls', exclusive=exclusive)
    return 1 + len(condition_sheets)


# ---------------------------------------------------------------------------
# Unified .xlsx / .xls reader & supplier importer
# ---------------------------------------------------------------------------

def _cell_text(value):
    return '' if value is None else str(value).strip()


def _parse_int(value, field_name):
    if isinstance(value, bool):
        raise ValueError(f'{field_name} 须为整数')
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(f'{field_name} 须为整数')
        return int(value)
    text = _cell_text(value)
    if re.fullmatch(r'-?\d+', text):
        return int(text)
    raise ValueError(f'{field_name} 须为整数')


def _parse_number(value, field_name, *, allow_none=False):
    if value is None or value == '':
        if allow_none:
            return None
        raise ValueError(f'{field_name} 不能为空')
    if isinstance(value, bool):
        raise ValueError(f'{field_name} 须为有限数值')
    if isinstance(value, (int, float)):
        try:
            is_fin = math.isfinite(value)
        except OverflowError as error:
            raise ValueError(f'{field_name} 须为有限数值') from error
        if not is_fin:
            raise ValueError(f'{field_name} 须为有限数值')
        return float(value) if isinstance(value, float) else value
    text = _cell_text(value)
    if text in ('', '—', '-') and allow_none:
        return None
    try:
        parsed = float(text)
    except ValueError as error:
        raise ValueError(f'{field_name} 须为有限数值') from error
    if not math.isfinite(parsed):
        raise ValueError(f'{field_name} 须为有限数值')
    return parsed


def _parse_disclosure(model_key, params_text, stress_text):
    ranges = []
    if params_text and params_text != '公开参数档位：内部':
        lines = [line.strip() for line in params_text.splitlines() if line.strip()]
        if lines and lines[0].startswith('公开参数档位'):
            lines = lines[1:]
        for line in lines:
            matched = re.fullmatch(r'([^：]+)：(.+?)\s+\[([^\[\]]+)\]', line)
            if matched:
                ranges.append({'name': matched.group(1).strip(),
                               'range': matched.group(2).strip(),
                               'source': matched.group(3).strip()})
            else:
                raise ValueError(f'无法解析公开参数档位：{line}')
    bins = []
    if stress_text and stress_text != '内部' and stress_text != '—':
        specs = stress_bin_specs(model_key)
        parts = [item.strip() for item in re.split(r'[;\n；]+', stress_text) if item.strip()]
        for part in parts:
            matched = re.fullmatch(r'(.+?)\s+\[档位\s+([^\[\]]+)\]', part)
            if not matched:
                raise ValueError(f'无法解析公开应力档位：{part}')
            label_text = matched.group(1).strip()
            val = float(matched.group(2).strip())
            matched_key = next((k for k, spec in specs.items() if spec['label'] == label_text), None)
            if matched_key is None:
                raise ValueError(f'模型 {model_key} 不支持应力档位 {label_text}')
            spec = specs[matched_key]
            canonical = next((v for v in spec['values'] if math.isclose(float(v), val, rel_tol=1e-9, abs_tol=1e-12)), None)
            if canonical is None:
                raise ValueError(f'公开应力档位 {val} 不在标准档位表中')
            bins.append({'key': matched_key, 'label': spec['label'], 'value': canonical, 'source': spec['sources'][0]})
    disclosure = {
        'model': model_key,
        'parameter_ranges': ranges,
        'stress': '标准档位' if bins else '内部',
        'stress_bins': bins,
    }
    validate_stress_bins(disclosure)
    return disclosure


def _trim_trailing_none(rows):
    out = []
    for row in rows:
        trimmed = list(row)
        while trimmed and trimmed[-1] is None:
            trimmed.pop()
        if any(cell is not None and _cell_text(cell) != '' for cell in trimmed):
            out.append(trimmed)
        else:
            out.append([])
    while out and not out[-1]:
        out.pop()
    return out


def _static_param_lines(params_txt):
    normalized = []
    for line in params_txt.splitlines():
        if not line or line.startswith('本工况') or '工程环境温度' in line:
            continue
        normalized.append(re.sub(r'，(?:计算值 [^\]\n]+|待计算)\]', ']', line))
    return tuple(normalized)


def _detail_entry_signature(entry):
    seq_num, item_name, mpn, domestic, qty, model_txt, params_txt, stress_txt, _rate = entry
    is_redacted = params_txt.startswith('公开参数档位')
    if is_redacted:
        return (seq_num, item_name, mpn, domestic, qty, model_txt, True, params_txt, stress_txt)
    return (seq_num, item_name, mpn, domestic, qty, model_txt, False, _static_param_lines(params_txt))


def project_from_workbook_rows(sheets):
    """Reconstruct and strictly verify a Project from unified workbook sheets."""
    if not sheets or sheets[0][0] != OVERVIEW_SHEET:
        raise ValueError(f'工作簿第 1 页须为“{OVERVIEW_SHEET}”')
    if len(sheets) != len({name for name, _ in sheets}):
        raise ValueError('工作簿存在重复名称的工作表')
    sheet_map = {name: _trim_trailing_none(rows) for name, rows in sheets}
    ov_rows = sheet_map[OVERVIEW_SHEET]
    header_idx = next(
        (i for i, row in enumerate(ov_rows)
         if [_cell_text(c) for c in row[:len(SUMMARY_HEADERS)]] == SUMMARY_HEADERS),
        None,
    )
    if header_idx is None or header_idx < 2 or len(ov_rows) <= header_idx + 1:
        raise ValueError('“预计总览”工作表缺少元数据或工况汇总行')

    meta = {}
    for row in ov_rows[:header_idx]:
        for idx in range(0, len(row) - 1, 2):
            key = _cell_text(row[idx])
            if key:
                meta[key] = row[idx + 1]
    if _cell_text(meta.get('依据标准')) != 'GJB/Z 299D—2024':
        raise ValueError('Excel 工作簿依据标准不受支持')
    ver_txt = _cell_text(meta.get('版本'))
    if not re.fullmatch(r'v\d+(?:\.\d+)+', ver_txt):
        raise ValueError('Excel 工作簿版本格式无效')
    project_name = _cell_text(meta.get('工程名称'))
    if not project_name:
        raise ValueError('工程名称不能为空')
    if _cell_text(meta.get('失效率单位')) != RATE_UNIT:
        raise ValueError('工作簿失效率单位与标准单位不一致')
    state_label = _cell_text(meta.get('预计状态'))
    state = next((k for k, v in STATES.items() if v == state_label), None)
    if state is None:
        raise ValueError('工作簿预计状态无效')

    conditions = []
    condition_specs = []
    for offset, row in enumerate(ov_rows[header_idx + 1:], start=1):
        if not row:
            continue
        padded = list(row) + [None] * max(0, len(SUMMARY_HEADERS) - len(row))
        _parse_int(padded[0], f'第 {offset} 行序号')
        cond_name = _cell_text(padded[1])
        env = _cell_text(padded[2])
        if env not in ENVIRONMENTS:
            raise ValueError(f'工况“{cond_name}”环境类别无效：{env}')
        temp = _parse_number(padded[3], f'工况“{cond_name}”温度')
        row_state_label = _cell_text(padded[4])
        if row_state_label != state_label:
            raise ValueError(f'工况“{cond_name}”预计状态与工程不一致')
        declared_total = _parse_number(padded[7], f'工况“{cond_name}”总失效率', allow_none=True)
        detail_title = _cell_text(padded[9])
        if not detail_title or detail_title not in sheet_map:
            raise ValueError(f'找不到工况“{cond_name}”对应的明细工作表：{detail_title}')
        condition = OperatingCondition(cond_name or f'工况{offset:03d}', env, temp)
        conditions.append(condition)
        condition_specs.append((condition, declared_total, detail_title))

    if not conditions or all(total is None for _, total, _ in condition_specs):
        raise ValueError('导入工作簿至少需包含一个完整计算的工况')
    referenced_sheets = {OVERVIEW_SHEET} | {detail_title for _, _, detail_title in condition_specs}
    if set(sheet_map) - referenced_sheets:
        raise ValueError('工作簿包含未在“预计总览”中引用的附加工作表')

    expected_headers = detail_headers(state)
    roster = None
    roster_signature = None
    per_condition_rates = []
    for condition, declared_total, detail_title in condition_specs:
        d_rows = sheet_map[detail_title]
        if not d_rows or [_cell_text(c) for c in d_rows[0]] != expected_headers:
            raise ValueError(f'工作表“{detail_title}”第 1 行明细列标题格式无效')
        entries = []
        subtotal_sum = []
        has_missing = False
        for row in d_rows[1:]:
            if not row:
                continue
            padded = list(row) + [None] * max(0, len(expected_headers) - len(row))
            seq_txt = _cell_text(padded[0])
            if '[续 ' in seq_txt:
                continue
            seq_num = _parse_int(padded[0], f'工作表“{detail_title}”序号')
            if seq_num != len(entries) + 1:
                raise ValueError(f'工作表“{detail_title}”序号须按 1 开始连续递增')
            raw_name = _cell_text(padded[1])
            mpn = _cell_text(padded[2])
            origin_txt = _cell_text(padded[3])
            if origin_txt not in ('国产', '进口'):
                raise ValueError(f'工作表“{detail_title}”第 {seq_num} 项国产/进口标记无效')
            domestic = origin_txt == '国产'
            model_txt = _cell_text(padded[5])
            params_txt = _cell_text(padded[6])
            stress_txt = _cell_text(padded[7])
            is_redacted = params_txt.startswith('公开参数档位')
            if not is_redacted and not raw_name:
                raise ValueError(f'工作表“{detail_title}”第 {seq_num} 项位号/名称不能为空')
            item_name = raw_name or f'{seq_num:06d}'
            resolved = _MODEL_BY_DISPLAY.get(model_txt)
            if not resolved:
                raise ValueError(f'工作表“{detail_title}”第 {seq_num} 项无法识别标准计算模型：{model_txt}')
            qty = _parse_int(padded[4], f'{item_name} 数量')
            unit_rate = _parse_number(padded[9], f'{item_name} 单件失效率', allow_none=True)
            row_sub = _parse_number(padded[10], f'{item_name} 小计失效率', allow_none=True)
            if unit_rate is None:
                has_missing = True
            else:
                if row_sub is not None and not math.isclose(qty * unit_rate, row_sub, rel_tol=1e-9, abs_tol=1e-12):
                    raise ValueError(f'工作表“{detail_title}”中 {item_name} 的数量×单件失效率与小计不一致')
                subtotal_sum.append(qty * unit_rate)
            entries.append((seq_num, item_name, mpn, domestic, qty, model_txt, params_txt, stress_txt, unit_rate))

        if declared_total is not None:
            if has_missing or not entries:
                raise ValueError(f'工作表“{detail_title}”已声明完整失效率，但明细存在未计算项')
            if not math.isclose(math.fsum(subtotal_sum), declared_total, rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError(f'工作表“{detail_title}”元器件小计之和与预计总览总失效率不一致')

        signature = [_detail_entry_signature(e) for e in entries]
        if roster is None:
            roster = entries
            roster_signature = signature
        elif signature != roster_signature:
            raise ValueError(f'工作表“{detail_title}”与前序工况的元器件清单、型号或参数档位不一致')
        per_condition_rates.append((condition, entries))

    components = []
    for idx, base in enumerate(roster or []):
        _seq, item_name, mpn, domestic, qty, model_txt, params_txt, stress_txt, _rate = base
        cond_rate_map = {}
        for condition, entries in per_condition_rates:
            rate_val = entries[idx][8]
            if rate_val is None:
                continue
            rkey = rate_condition_key(condition.environment, condition.ambient_temperature_C)
            existing = cond_rate_map.get(rkey)
            if existing is not None and not math.isclose(existing['rate'], rate_val, rel_tol=1e-9, abs_tol=1e-12):
                raise ValueError(f'工作簿相同环境与温度下 {item_name} 的失效率不一致')
            cond_rate_map.setdefault(rkey, {
                'environment': condition.environment,
                'ambient_temperature_C': condition.ambient_temperature_C,
                'rate': rate_val,
            })
        cond_rates = list(cond_rate_map.values())
        disclosure = None
        is_redacted = params_txt.startswith('公开参数档位')
        resolved = _MODEL_BY_DISPLAY[model_txt]
        if is_redacted:
            if resolved == '待配置':
                raise ValueError(f'无法识别脱敏条目对应的标准模型：{model_txt}')
            if resolved not in ('已知单件失效率', '工程模块') and model_state(resolved) != state:
                raise ValueError(f'脱敏模型 {resolved} 与工程预计状态不一致')
            disclosure = _parse_disclosure(resolved, params_txt, stress_txt)
        components.append(Component(
            name=item_name,
            model='已知单件失效率',
            quantity=qty,
            parameters={},
            reference='' if is_redacted else mpn,
            domestic=domestic,
            condition_rates=cond_rates,
            disclosure=disclosure,
            rate_state=state,
        ))

    return Project(
        name=project_name,
        notes=_cell_text(meta.get('工程备注')),
        components=components,
        conditions=conditions,
        prediction_state=state,
    ).validate()


def read_workbook_bytes(encoded, suffix):
    """Parse and verify unified .xlsx or .xls workbook bytes into a validated Project."""
    if not isinstance(encoded, bytes) or len(encoded) > MAX_PACKAGE_BYTES:
        raise ValueError('Excel 文件内容无效或过大')
    suffix = suffix.lower()
    if suffix not in ('.xlsx', '.xls'):
        raise ValueError('工作簿仅支持 .xlsx 或 .xls 格式')
    try:
        sheets = _read_xlsx(encoded) if suffix == '.xlsx' else _read_xls(encoded)
        return project_from_workbook_rows(sheets)
    except (ValueError, OSError):
        raise
    except Exception as error:
        raise ValueError('公开 Excel 文件格式无效或不完整') from error


def is_gjb299d_workbook(path):
    """Return True only when an .xlsx/.xls file carries the expected 预计总览 metadata."""
    target = Path(path)
    suffix = target.suffix.lower()
    if suffix not in ('.xlsx', '.xls') or not target.is_file():
        return False
    rows = []
    try:
        if target.stat().st_size > MAX_PACKAGE_BYTES:
            return False
        if suffix == '.xlsx':
            with zipfile.ZipFile(target) as archive:
                check_package(archive, maximum_bytes=MAX_PACKAGE_BYTES)
            book = openpyxl.load_workbook(target, read_only=True, data_only=True, keep_links=False)
            try:
                if not book.sheetnames or book.sheetnames[0] != OVERVIEW_SHEET:
                    return False
                ws = book[OVERVIEW_SHEET]
                for row in ws.iter_rows(max_row=8, max_col=16, values_only=True):
                    rows.append(list(row))
            finally:
                book.close()
        else:
            book = xlrd.open_workbook(str(target), on_demand=True)
            try:
                if not book.sheet_names() or book.sheet_names()[0] != OVERVIEW_SHEET:
                    return False
                ws = book.sheet_by_index(0)
                for row_idx in range(min(8, ws.nrows)):
                    rows.append([ws.cell_value(row_idx, col) for col in range(min(16, ws.ncols))])
            finally:
                book.release_resources()
    except Exception:
        return False
    meta = {}
    for row in rows:
        for idx in range(0, len(row) - 1, 2):
            key = _cell_text(row[idx])
            if key:
                meta[key] = _cell_text(row[idx + 1])
    return (
        bool(meta.get('工程名称'))
        and meta.get('依据标准') == 'GJB/Z 299D—2024'
        and meta.get('失效率单位') == RATE_UNIT
    )
