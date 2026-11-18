"""Readable supplier exchange files: JSON or visible value-only Excel sheets."""

import hashlib
from pathlib import Path

from .domain import Project
from .excel_limits import MAX_PACKAGE_BYTES
from .redacted_export import build_redacted_project
from .storage import parse_document, save_project

SUPPLIER_FILTER = '供应商工程 (*.json *.xlsx *.xls);;JSON 工程 (*.json);;Excel 工作簿 (*.xlsx *.xls)'
PUBLIC_FILTER = 'Excel 工作簿 (*.xlsx);;Excel 97–2003 工作簿 (*.xls);;JSON 工程 (*.json)'


def read_supplier_bytes(encoded, suffix):
    suffix = suffix.lower()
    try:
        if suffix == '.json':
            return Project.from_dict(parse_document(encoded)['project'])
        if suffix in ('.xls', '.xlsx'):
            from .workbook_io import read_workbook_bytes
            return read_workbook_bytes(encoded, suffix)
    except (TypeError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError('供应商工程内容格式无效，请重新检查文件') from error
    raise ValueError('供应商工程支持 .json、.xlsx 和 .xls 格式')


def load_supplier_bundle(path):
    path = Path(path)
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError('供应商工程文件过大，无法读取')
    raw = path.read_bytes()
    return read_supplier_bytes(raw, path.suffix), hashlib.sha256(raw).hexdigest()


def load_supplier_project(path):
    return load_supplier_bundle(path)[0]


def export_redacted_file(path, project):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in ('.json', '.xls', '.xlsx'):
        raise ValueError('脱敏导出支持 .xlsx、.xls 和 .json 格式')
    public = build_redacted_project(project)
    if suffix == '.json':
        save_project(path, public)
    elif suffix == '.xlsx':
        from .workbook_io import export_workbook_xlsx
        export_workbook_xlsx(path, public, redacted=True, exclusive=True, _already_redacted=True)
    else:
        from .workbook_io import export_workbook_xls
        export_workbook_xls(path, public, redacted=True, exclusive=True, _already_redacted=True)
    return public
