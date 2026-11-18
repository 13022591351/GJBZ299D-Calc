"""Strict binary and OOXML readers that reject formulas, macros, links, and hidden data."""

import io
import re
import struct
import zipfile
from xml.etree import ElementTree as StdlibElementTree

import openpyxl
import xlrd
from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from xlrd.compdoc import CompDoc

from .excel_limits import MAX_CELLS, MAX_PACKAGE_BYTES, check_cells, check_package


def _check_xlsx_package(encoded):
    from openpyxl.utils.cell import coordinate_to_tuple

    with zipfile.ZipFile(io.BytesIO(encoded)) as archive:
        members = check_package(archive, maximum_bytes=MAX_PACKAGE_BYTES)
        forbidden = (
            'vba', 'macrosheet', 'dialogsheet', 'externallink', 'connections',
            'querytable', 'embeddings/', 'activex/', 'customxml/', 'oleobject',
        )
        allowed = re.compile(
            r'(?:\[content_types\]\.xml|_rels/\.rels|docprops/(?:core|app)\.xml|'
            r'xl/(?:workbook|styles|sharedstrings)\.xml|xl/_rels/workbook\.xml\.rels|'
            r'xl/theme/theme[0-9]+\.xml|xl/worksheets/sheet[0-9]+\.xml|'
            r'xl/worksheets/_rels/sheet[0-9]+\.xml\.rels|'
            r'xl/printersettings/printersettings[0-9]+\.bin)\Z'
        )
        total_cells = 0
        dense_cells = 0
        for member in members:
            lower = member.filename.lower()
            if any(part in lower for part in forbidden):
                raise ValueError('工作簿 Excel 不能包含宏、外部链接或嵌入数据')
            if member.is_dir():
                continue
            if not allowed.fullmatch(lower):
                raise ValueError('工作簿 Excel 包含普通单元格格式以外的附加数据')
            if not lower.endswith(('.xml', '.rels')):
                continue
            is_worksheet = lower.startswith('xl/worksheets/') and not lower.startswith('xl/worksheets/_rels/')
            cells = set()
            sheet_max_row = 0
            sheet_max_col = 0
            root_element = None
            try:
                with archive.open(member) as xml_stream:
                    for event, element in ElementTree.iterparse(xml_stream, events=('start', 'end')):
                        tag = element.tag.rsplit('}', 1)[-1]
                        if event == 'start':
                            if root_element is None:
                                root_element = element
                            if tag == 'Relationship' and element.attrib.get('TargetMode', '').lower() == 'external':
                                raise ValueError('工作簿 Excel 不能包含外部链接')
                            if tag == 'Override' and any(
                                term in element.attrib.get('ContentType', '').lower()
                                for term in ('macroenabled', 'macrosheet', 'vba', 'dialogsheet')
                            ):
                                raise ValueError('工作簿 Excel 不能包含宏')
                            if tag == 'definedName' and element.attrib.get('name') not in (
                                '_xlnm.Print_Area', '_xlnm.Print_Titles', '_xlnm._FilterDatabase',
                            ):
                                raise ValueError('工作簿 Excel 不能包含自定义隐藏名称数据')
                            if tag == 'workbookView' and element.attrib.get('visibility', 'visible') != 'visible':
                                raise ValueError('工作簿 Excel 不能隐藏工作簿窗口')
                            if is_worksheet:
                                if tag == 'f':
                                    raise ValueError('工作簿 Excel 须使用普通数值单元格，不能包含公式')
                                if tag in ('row', 'col') and element.attrib.get('hidden', '').lower() in ('1', 'true'):
                                    raise ValueError('工作簿 Excel 不能隐藏数据行或列')
                                if tag == 'sheetFormatPr' and element.attrib.get('zeroHeight', '').lower() in ('1', 'true'):
                                    raise ValueError('工作簿 Excel 不能隐藏数据行')
                                if (
                                    (tag == 'row' and element.attrib.get('ht') in ('0', '0.0'))
                                    or (tag == 'col' and element.attrib.get('width') in ('0', '0.0'))
                                ):
                                    raise ValueError('工作簿 Excel 的数据行高和列宽不能为零')
                                if tag == 'c':
                                    address = element.attrib.get('r')
                                    if address is None or address in cells:
                                        raise ValueError('工作簿 Excel 包含重复或无效的单元格位置')
                                    cells.add(address)
                                    total_cells += 1
                                    check_cells(total_cells, maximum=MAX_CELLS)
                                    try:
                                        row_idx, col_idx = coordinate_to_tuple(address)
                                    except ValueError as coord_err:
                                        raise ValueError('工作簿 Excel 包含重复或无效的单元格位置') from coord_err
                                    if not (1 <= row_idx <= 1048576 and 1 <= col_idx <= 16384):
                                        raise ValueError('工作簿 Excel 包含重复或无效的单元格位置')
                                    sheet_max_row = max(sheet_max_row, row_idx)
                                    sheet_max_col = max(sheet_max_col, col_idx)
                                    check_cells(dense_cells + sheet_max_row * sheet_max_col, maximum=MAX_CELLS)
                        else:
                            if element is not root_element:
                                element.clear()
                            if root_element is not None and tag in ('row', 'si', 'xf', 'Relationship', 'Override'):
                                root_element.clear()
            except (StdlibElementTree.ParseError, DefusedXmlException) as error:
                raise ValueError('工作簿 Excel XML 结构无效或不安全') from error
            dense_cells += sheet_max_row * sheet_max_col
            check_cells(dense_cells, maximum=MAX_CELLS)


def _read_xlsx(encoded):
    _check_xlsx_package(encoded)
    book = openpyxl.load_workbook(
        io.BytesIO(encoded), data_only=False, read_only=False, keep_links=False, keep_vba=False,
    )
    try:
        sheets, cells = [], 0
        for sheet in book.worksheets:
            if sheet.sheet_state != 'visible':
                raise ValueError('工作簿 Excel 不能包含隐藏工作表')
            cells += sheet.max_row * sheet.max_column
            check_cells(cells, maximum=MAX_CELLS)
            rows = []
            for row in sheet.iter_rows():
                values = []
                for cell in row:
                    if cell.data_type in ('f', 'e') or cell.number_format.replace(' ', '') == ';;;':
                        raise ValueError('工作簿 Excel 不能包含公式、错误值或隐藏单元格')
                    values.append(cell.value)
                rows.append(values)
            sheets.append((sheet.title, rows))
        if len(book.worksheets) != len(book.sheetnames):
            raise ValueError('工作簿 Excel 只支持普通单元格工作表')
        return sheets
    finally:
        book.close()


def _check_xls_records(encoded):
    document = CompDoc(encoded, logfile=io.StringIO())
    for node in document.dirlist:
        if node.etype == 1 or node.name.lower() in ('vba', '_vba_project', '_vba_project_cur', 'project', 'projectwm'):
            raise ValueError('工作簿 Excel 不能包含 VBA 宏或嵌入对象')
        if node.etype == 2 and node.name not in (
            'Workbook', 'Book', '\x05SummaryInformation', '\x05DocumentSummaryInformation', '\x01CompObj',
        ):
            raise ValueError('工作簿 Excel 包含普通单元格格式以外的附加数据流')
    stream = document.get_named_stream('Workbook') or document.get_named_stream('Book')
    if not stream:
        raise ValueError('不是有效的 .xls 工作簿')
    position = 0
    while position + 4 <= len(stream):
        opcode, size = struct.unpack_from('<HH', stream, position)
        position += 4
        if position + size > len(stream):
            raise ValueError('.xls 数据记录不完整')
        data = stream[position:position + size]
        position += size
        if opcode in (0x0006, 0x0206, 0x0406, 0x0221, 0x04BC):
            raise ValueError('工作簿 Excel 须使用普通数值单元格，不能包含公式')
        if opcode in (0x002F, 0x00D3, 0x0023, 0x0223, 0x0051, 0x0059, 0x005A, 0x01B8):
            raise ValueError('工作簿 Excel 不能包含加密、宏或外部链接')
        if opcode == 0x0018 and (len(data) < 14 or struct.unpack_from('<H', data)[0] & 0x000E):
            raise ValueError('工作簿 Excel 不能包含宏名称或自定义函数')
        if opcode == 0x0085 and (len(data) < 6 or data[4] != 0 or data[5] != 0):
            raise ValueError('工作簿 Excel 不能包含隐藏工作表或宏工作表')
        if opcode in (0x0009, 0x0209, 0x0409, 0x0809) and (
            len(data) < 4 or struct.unpack_from('<H', data, 2)[0] not in (0x0005, 0x0010)
        ):
            raise ValueError('工作簿 Excel 只支持普通单元格工作表')
        if opcode == 0x01AE and (len(data) < 4 or data[2:4] != b'\x01\x04'):
            raise ValueError('工作簿 Excel 不能包含外部链接')


def _read_xls(encoded):
    _check_xls_records(encoded)
    book = xlrd.open_workbook(file_contents=encoded, formatting_info=True, logfile=io.StringIO())
    try:
        if any(item.name not in ('Print_Area', 'Print_Titles', '_FilterDatabase') for item in book.name_obj_list):
            raise ValueError('工作簿 Excel 不能包含自定义隐藏名称数据')
        sheets, cells = [], 0
        for sheet in book.sheets():
            if (
                sheet.visibility
                or any(item.hidden for item in sheet.rowinfo_map.values())
                or any(item.hidden for item in sheet.colinfo_map.values())
            ):
                raise ValueError('工作簿 Excel 不能隐藏工作表、数据行或列')
            cells += sheet.nrows * sheet.ncols
            check_cells(cells, maximum=MAX_CELLS)
            rows = []
            for row_index in range(sheet.nrows):
                values = []
                for column in range(sheet.ncols):
                    cell = sheet.cell(row_index, column)
                    if cell.ctype in (xlrd.XL_CELL_ERROR, xlrd.XL_CELL_DATE, xlrd.XL_CELL_BOOLEAN):
                        raise ValueError('工作簿 Excel 包含不支持的错误值、日期或布尔单元格')
                    formatting = book.xf_list[cell.xf_index]
                    number_format = book.format_map.get(formatting.format_key)
                    if number_format and number_format.format_str.replace(' ', '') == ';;;':
                        raise ValueError('工作簿 Excel 不能包含隐藏单元格')
                    values.append(None if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK) else cell.value)
                rows.append(values)
            sheets.append((sheet.name, rows))
        return sheets
    finally:
        book.release_resources()
