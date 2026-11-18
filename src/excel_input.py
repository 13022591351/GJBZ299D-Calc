"""Read spreadsheet values and number formats without changing the workbook."""

import datetime
import io
import math
import posixpath
import re
from xml.etree import ElementTree as StdlibElementTree
from zipfile import BadZipFile, ZipFile

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

from .excel_limits import MAX_CELLS, MAX_PACKAGE_BYTES, check_cells, check_package
from .numeric import number_text


def check_file_size(size):
    if size > MAX_PACKAGE_BYTES:
        raise ValueError('Excel 文件过大，请拆分工作簿后导入')


def _check_cells(count):
    check_cells(count, maximum=MAX_CELLS)


def cell_text(value, number_format='General'):
    if value is None:
        return ''
    number_format = number_format or 'General'
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat(sep=' ') if isinstance(value, datetime.datetime) else value.isoformat()
    if isinstance(value, bool):
        return 'TRUE' if value else 'FALSE'
    if isinstance(value, (int, float)):
        try:
            is_fin = math.isfinite(value)
        except OverflowError as error:
            raise ValueError('单元格包含非有限数值') from error
        if not is_fin:
            raise ValueError('单元格包含非有限数值')
        format_code = number_format.split(';')[0]
        if re.fullmatch(r'0+', format_code) and float(value).is_integer():
            return ('-' if value < 0 else '') + str(abs(int(value))).zfill(len(format_code))
        if re.fullmatch(r'0(?:\.[0#]+)?%', format_code):
            digits = len(format_code.split('.')[1].rstrip('%')) if '.' in format_code else 0
            return f'{value * 100:.{digits}f}%'
        return number_text(value)
    return str(value)


def _remap_visible_merges(merges, height, hidden_rows):
    if not hidden_rows:
        return merges
    phys_to_vis = {}
    vis_idx = 0
    for r in range(height):
        if r not in hidden_rows:
            phys_to_vis[r] = vis_idx
            vis_idx += 1
    remapped = []
    for top, bottom, left, right in merges:
        vis_span = [phys_to_vis[r] for r in range(top, min(bottom, height)) if r in phys_to_vis]
        if not vis_span:
            continue
        new_top, new_bottom = vis_span[0], vis_span[-1] + 1
        if new_bottom - new_top > 1 or right - left > 1:
            remapped.append((new_top, new_bottom, left, right))
    return remapped


def merged_errors(merges, rows, columns):
    """Mark only the displayed data area; a formatted merge may span a full column."""
    errors = {}
    covered = 0
    for top, bottom, left, right in merges:
        if bottom - top > 1 or right - left > 1:
            bottom, right = min(bottom, rows), min(right, columns)
            if bottom <= top or right <= left:
                continue
            covered += (bottom - top) * (right - left)
            _check_cells(covered)
            for row in range(top, bottom):
                for col in range(left, right):
                    errors[row, col] = '数据区域含合并单元格，请先在 Excel 中取消合并并核对各行数据，或不映射该列'
    return errors


def _xlsx_sheet_manifest(archive):
    """Resolve worksheet XML paths and visibility via standard OOXML workbook + rels parts."""
    by_lower = {name.lower(): name for name in archive.namelist()}
    rels_member = by_lower.get('xl/_rels/workbook.xml.rels')
    wb_member = by_lower.get('xl/workbook.xml')
    if not rels_member or not wb_member:
        return {}
    rels = {}
    with archive.open(rels_member) as rels_stream:
        rels_root = ElementTree.parse(rels_stream).getroot()
    for elem in rels_root.iter():
        if elem.tag.rsplit('}', 1)[-1] == 'Relationship':
            rid = elem.attrib.get('Id')
            target = elem.attrib.get('Target', '')
            if rid and target:
                norm = (
                    posixpath.normpath(target.lstrip('/'))
                    if target.startswith('/')
                    else posixpath.normpath('xl/' + target)
                )
                rels[rid] = by_lower.get(norm.lower(), norm)

    manifest = {}
    with archive.open(wb_member) as wb_stream:
        wb_root = ElementTree.parse(wb_stream).getroot()
    for elem in wb_root.iter():
        if elem.tag.rsplit('}', 1)[-1] == 'sheet':
            name = elem.attrib.get('name', '')
            state = elem.attrib.get('state', 'visible')
            rid = next((v for k, v in elem.attrib.items() if k == 'id' or k.endswith('}id')), None)
            if name and rid in rels:
                manifest[name] = (rels[rid], state)
    return manifest


def _resolve_worksheet_member(archive, sheet, manifest):
    if sheet.title in manifest:
        return manifest[sheet.title][0]
    fallback = getattr(sheet, '_worksheet_path', None)
    if fallback:
        cleaned = fallback.lstrip('/')
        by_lower = {name.lower(): name for name in archive.namelist()}
        if cleaned.lower() in by_lower:
            return by_lower[cleaned.lower()]
        return fallback
    raise KeyError(sheet.title)


def _xlsx_layout(archive, worksheet_member):
    """Scan sparse XML without expanding dimensions or merged-cell placeholders."""
    from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

    rows = columns = stored = row_number = column = 0
    merges, hidden_rows, sheet_data = [], set(), None
    with archive.open(worksheet_member) as source:
        for event, element in ElementTree.iterparse(source, events=('start', 'end')):
            tag = element.tag.rsplit('}', 1)[-1]
            if event == 'start':
                if tag == 'sheetData':
                    sheet_data = element
                elif tag == 'row':
                    row_number = int(element.attrib.get('r', row_number + 1))
                    column = 0
                    if element.attrib.get('hidden', '').lower() in ('1', 'true'):
                        hidden_rows.add(row_number - 1)
                continue
            if tag == 'c':
                stored += 1
                _check_cells(stored)
                if 'r' in element.attrib:
                    row, column = coordinate_to_tuple(element.attrib['r'])
                else:
                    row, column = row_number, column + 1
                if not (1 <= row <= 1048576 and 1 <= column <= 16384):
                    raise ValueError('Excel 单元格位置无效')
                has_data = any(child.tag.rsplit('}', 1)[-1] == 'f'
                               or child.tag.rsplit('}', 1)[-1] == 'v' and child.text is not None
                               or child.tag.rsplit('}', 1)[-1] == 'is' and any(
                                   text.tag.rsplit('}', 1)[-1] == 't' and text.text is not None
                                   for text in child.iter()) for child in element)
                if has_data:
                    rows, columns = max(rows, row), max(columns, column)
                    _check_cells(rows * columns)
                element.clear()
            elif tag == 'mergeCell':
                left, top, right, bottom = range_boundaries(element.attrib['ref'])
                if (None in (left, top, right, bottom) or not 1 <= top <= bottom <= 1048576
                        or not 1 <= left <= right <= 16384):
                    raise ValueError('Excel 合并单元格范围无效')
                merges.append((top - 1, bottom, left - 1, right))
                _check_cells(stored + len(merges))
                element.clear()
            elif tag == 'row':
                element.clear()
                if sheet_data is not None:
                    sheet_data.clear()
    return rows, columns, merges, stored + len(merges), hidden_rows


def read_xlsx(data):
    from openpyxl import load_workbook
    from openpyxl.utils.exceptions import InvalidFileException

    books = []
    try:
        check_file_size(len(data))
        with ZipFile(io.BytesIO(data)) as archive:
            check_package(archive, maximum_bytes=MAX_PACKAGE_BYTES)
            manifest = _xlsx_sheet_manifest(archive)
            formulas = load_workbook(io.BytesIO(data), data_only=False, read_only=True, keep_links=False)
            books.append(formulas)
            layouts, dense, stored = [], 0, 0
            visible_sheets = []
            for sheet in formulas.worksheets:
                xml_state = manifest.get(sheet.title, (None, 'visible'))[1]
                if getattr(sheet, 'sheet_state', 'visible') != 'visible' or xml_state != 'visible':
                    continue
                ws_member = _resolve_worksheet_member(archive, sheet, manifest)
                rows, columns, merges, count, hidden_rows = _xlsx_layout(archive, ws_member)
                dense, stored = dense + rows * columns, stored + count
                _check_cells(dense)
                _check_cells(stored)
                visible_sheets.append(sheet)
                layouts.append((rows, columns, merges, hidden_rows))
        cached = load_workbook(io.BytesIO(data), data_only=True, read_only=True, keep_links=False)
        books.append(cached)
        result = []
        for sheet, (height, width, merges, hidden_rows) in zip(visible_sheets, layouts, strict=True):
            if not height or not width:
                continue
            values = cached[sheet.title]
            vis_height = sum(1 for r in range(height) if r not in hidden_rows)
            if not vis_height:
                continue
            remapped_merges = _remap_visible_merges(merges, height, hidden_rows)
            errors = merged_errors(remapped_merges, vis_height, width)
            cells = []
            bounds = dict(min_row=1, min_col=1, max_row=height, max_col=width)
            for r, (row, saved) in enumerate(zip(sheet.iter_rows(**bounds), values.iter_rows(**bounds), strict=True)):
                if r in hidden_rows:
                    continue
                vis_r = len(cells)
                output = []
                for c, (cell, cached_cell) in enumerate(zip(row, saved, strict=True)):
                    value = cached_cell.value if cell.data_type == 'f' else cell.value
                    if cell.data_type == 'f' and value is None:
                        errors[vis_r, c] = '公式没有已保存的计算结果，请用 Excel 重新计算并保存后导入'
                        value = cell.value
                    if cell.data_type == 'e' or cached_cell.data_type == 'e':
                        errors[vis_r, c] = '单元格含 Excel 错误值：' + str(value)
                    output.append(cell_text(value, cell.number_format))
                cells.append(output)
            if any(v for row in cells for v in row):
                result.append((sheet.title, cells, errors, remapped_merges))
        return result
    except (BadZipFile, InvalidFileException, KeyError, StdlibElementTree.ParseError, DefusedXmlException) as error:
        raise ValueError('无法读取 Excel 文件，请确认它是未加密的 .xlsx 工作簿') from error
    finally:
        for book in books:
            book.close()


def read_xls(data):
    import xlrd
    from xlrd.compdoc import CompDocError

    book = None
    try:
        check_file_size(len(data))
        book = xlrd.open_workbook(file_contents=data, formatting_info=True, ragged_rows=True,
                                 on_demand=True)
        result, dense, stored = [], 0, 0
        for index in range(book.nsheets):
            sheet = book.sheet_by_index(index)
            if sheet.visibility != 0:
                book.unload_sheet(index)
                continue
            hidden_rows = {r for r, info in sheet.rowinfo_map.items() if getattr(info, 'hidden', 0)}
            height = width = 0
            for row in range(sheet.nrows):
                length = sheet.row_len(row)
                stored += length
                _check_cells(stored)
                if row in hidden_rows:
                    continue
                for col, cell in enumerate(sheet.row(row)):
                    if cell.ctype not in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) and cell.value != '':
                        height, width = max(height, row + 1), max(width, col + 1)
            dense += height * width
            _check_cells(dense)
            vis_height = sum(1 for r in range(height) if r not in hidden_rows)
            if not vis_height or not width:
                book.unload_sheet(index)
                continue
            remapped_merges = _remap_visible_merges(sheet.merged_cells, height, hidden_rows)
            errors = merged_errors(remapped_merges, vis_height, width)
            cells = []
            for row in range(height):
                if row in hidden_rows:
                    continue
                vis_r = len(cells)
                output = []
                for col in range(width):
                    if col >= sheet.row_len(row):
                        output.append('')
                        continue
                    cell = sheet.cell(row, col)
                    value = cell.value
                    if cell.ctype == xlrd.XL_CELL_DATE:
                        try:
                            value = xlrd.xldate.xldate_as_datetime(value, book.datemode)
                        except (ValueError, OverflowError, xlrd.xldate.XLDateError):
                            errors[vis_r, col] = '单元格日期值超出范围'
                    elif cell.ctype == xlrd.XL_CELL_ERROR:
                        value = xlrd.error_text_from_code.get(value, 'Excel 错误')
                        errors[vis_r, col] = '单元格含 Excel 错误值：' + value
                    elif cell.ctype == xlrd.XL_CELL_BOOLEAN:
                        value = bool(value)
                    format_key = book.xf_list[cell.xf_index].format_key
                    fmt = book.format_map.get(format_key)
                    format_code = fmt.format_str if fmt and fmt.format_str else 'General'
                    output.append(cell_text(value, format_code))
                cells.append(output)
            if any(v for row in cells for v in row):
                result.append((sheet.name, cells, errors, remapped_merges))
            book.unload_sheet(index)
        return result
    except (xlrd.XLRDError, CompDocError) as error:
        raise ValueError('无法读取 .xls 文件：' + str(error)) from error
    finally:
        if book is not None:
            book.release_resources()
