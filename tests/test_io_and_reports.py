"""Project JSON v1, autosave sidecar, unified .xlsx/.xls workbooks, and roundtrip tests."""

import json

import openpyxl
import pytest
import xlrd

from src import APP_VERSION
from src.domain import Component, OperatingCondition
from src.shared_io import export_redacted_file, load_supplier_project
from src.storage import FORMAT_VERSION, RevisionConflict, load_project, parse_document, save_project
from src.ui.undo import clear_autosave, find_autosave, write_autosave
from src.workbook_io import (
    DETAIL_HEADER_ROW,
    OVERVIEW_SHEET,
    condition_sheet_name,
    export_workbook_xls,
    export_workbook_xlsx,
    is_gjb299d_workbook,
)


def test_project_save_load_revision_and_autosave(sample_project, isolated_tmp):
    path = isolated_tmp / 'proj.json'
    assert FORMAT_VERSION == 1
    # Attach dual-state nonoperating profile alongside active working profile.
    sample_project.set_prediction_state('nonoperating')
    sample_project.components[0] = Component(
        name='R1',
        model='国产电阻器（非工作详细）',
        quantity=4,
        parameters={'kind': '金属膜', 'quality': 'A1P', 'rated_power_W': 0.25, 'cycle_rate': 10},
        domestic=True,
        identifier=sample_project.components[0].identifier,
        profiles=sample_project.components[0].profiles,
    )
    sample_project.set_prediction_state('working')
    assert 'nonoperating' in sample_project.components[0].profiles

    rev1 = save_project(path, sample_project)
    raw = json.loads(path.read_text(encoding='utf-8'))
    assert raw['version'] == 1
    # Strict version check: reject mismatched versions without legacy fallback.
    mismatched = dict(raw, version=3)
    with pytest.raises(ValueError):
        parse_document(json.dumps(mismatched).encode('utf-8'))

    loaded, loaded_rev = load_project(path)
    assert loaded_rev == rev1
    assert loaded.name == sample_project.name
    assert 'nonoperating' in loaded.components[0].profiles
    assert loaded.calculate(loaded.conditions[0], prediction_state='nonoperating').known_rate > 0

    # Re-saving without advancing revision must raise RevisionConflict.
    with pytest.raises(RevisionConflict):
        save_project(path, sample_project)

    rev2 = save_project(path, loaded, loaded_rev)
    assert rev2 == 2

    # Stale revision and external text tamper must raise RevisionConflict.
    with pytest.raises(RevisionConflict):
        save_project(path, loaded, rev1)
    tampered = json.loads(path.read_text(encoding='utf-8'))
    tampered['project']['name'] = '外部文本篡改'
    path.write_text(json.dumps(tampered, ensure_ascii=False), encoding='utf-8')
    with pytest.raises(RevisionConflict):
        save_project(path, loaded, rev2)

    missing_path = isolated_tmp / 'deleted.json'
    with pytest.raises(RevisionConflict):
        save_project(missing_path, loaded, rev2)

    with pytest.raises(ValueError, match='重复字段'):
        parse_document(b'{"a": 1, "a": 2}')
    with pytest.raises(ValueError, match='非有限数值'):
        parse_document(b'{"format": "GJB299D-Project", "version": 1, "revision": 1, "project": NaN}')

    # Sidecar autosave lifecycle.
    assert find_autosave(path) is None
    loaded.name = '未保存草稿工程'
    sidecar = write_autosave(path, loaded)
    assert sidecar is not None and find_autosave(path) == sidecar
    recovered, _ = load_project(sidecar)
    assert recovered.name == '未保存草稿工程'
    clear_autosave(path)
    assert find_autosave(path) is None


def test_xlsx_and_xls_calculation_exports(sample_project, isolated_tmp):
    xlsx_path = isolated_tmp / 'report.xlsx'
    xls_path = isolated_tmp / 'report.xls'

    # Even when mission analysis is configured, Excel exports cover only 预计总览 and condition sheets.
    sample_project.analysis = {
        'version': 1,
        'mission': {
            'basis': 'duration',
            'phases': [
                {'name': '工作阶段', 'state': 'working', 'duration_hours': '100', 'rate': '0.5'},
                {'name': '贮存阶段', 'state': 'nonoperating', 'duration_hours': '900', 'rate': '0.05'},
            ],
        },
    }
    sheets_xlsx = export_workbook_xlsx(xlsx_path, sample_project)
    sheets_xls = export_workbook_xls(xls_path, sample_project)
    assert sheets_xlsx == 3 and sheets_xls == 3
    assert xlsx_path.stat().st_size > 1000
    assert xls_path.stat().st_size > 1000

    expected_sheets = [
        OVERVIEW_SHEET,
        '地面良好 25°C_GB@25°C',
        '一般固定 50°C_GF1@50°C',
    ]
    assert condition_sheet_name(OperatingCondition('工况 1', 'AUC', 25), set()) == '工况 1_AUC@25°C'

    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    assert wb.sheetnames == expected_sheets
    overview_ws = wb[OVERVIEW_SHEET]
    overview_text = [str(cell.value) for row in overview_ws.iter_rows() for cell in row if cell.value is not None]
    assert 'GJB/Z 299D—2024' in overview_text
    assert APP_VERSION in overview_text
    assert overview_ws.cell(row=2, column=3).value == '版本'
    assert overview_ws.cell(row=2, column=4).value == APP_VERSION
    assert all(
        label not in overview_text
        for label in ('说明', '交付模式', '启用工况数', '工作簿格式', 'GJB299D-Workbook', '工况状态 / 说明')
    )
    assert all('℃' not in text for text in overview_text)
    assert overview_ws.cell(row=5, column=2).value == '地面良好 25°C'
    assert overview_ws.cell(row=6, column=2).value == '一般固定 50°C'
    assert is_gjb299d_workbook(xlsx_path) is True
    detail_ws = wb['地面良好 25°C_GB@25°C']
    headers = [cell.value for cell in detail_ws[DETAIL_HEADER_ROW]]
    assert DETAIL_HEADER_ROW == 1
    assert '占比 (%)' in headers
    assert detail_ws.cell(row=2, column=2).value == 'R1, R2, R3, R4'

    xls_book = xlrd.open_workbook(str(xls_path))
    assert xls_book.sheet_names() == expected_sheets
    assert is_gjb299d_workbook(xls_path) is True
    xls_ov = xls_book.sheet_by_name(OVERVIEW_SHEET)
    xls_ov_text = [str(xls_ov.cell_value(r, c)) for r in range(xls_ov.nrows) for c in range(xls_ov.ncols)]
    assert APP_VERSION in xls_ov_text
    assert all(label not in xls_ov_text for label in ('工作簿格式', 'GJB299D-Workbook', '工况状态 / 说明'))
    xls_detail = xls_book.sheet_by_name('地面良好 25°C_GB@25°C')
    assert '占比 (%)' in [cell.value for cell in xls_detail.row(0)]
    for name in xls_book.sheet_names():
        assert '℃' not in name
        s = xls_book.sheet_by_name(name)
        for r in range(s.nrows):
            for c in range(s.ncols):
                assert '℃' not in str(s.cell_value(r, c))


@pytest.mark.parametrize('ext', ['.xlsx', '.xls', '.json'])
def test_redacted_export_and_supplier_import_roundtrip(sample_project, isolated_tmp, ext):
    # Remove unconfigured component so all components have known rates on conditions.
    project = sample_project
    project.components = [c for c in project.components if c.model != '待配置']
    expected_0 = project.calculate(project.conditions[0]).total_rate
    expected_1 = project.calculate(project.conditions[1]).total_rate
    assert expected_0 is not None and expected_0 > 0
    assert expected_1 is not None and expected_1 > 0

    out_path = isolated_tmp / f'supplier_redacted{ext}'
    export_redacted_file(out_path, project)
    assert out_path.is_file()

    if ext == '.xlsx':
        wb = openpyxl.load_workbook(out_path, data_only=True)
        assert wb.sheetnames[0] == OVERVIEW_SHEET
        ov_text = [str(cell.value) for row in wb[OVERVIEW_SHEET].iter_rows() for cell in row if cell.value is not None]
        assert APP_VERSION in ov_text
        assert all(label not in ov_text for label in ('工作簿格式', 'GJB299D-Workbook', '工况状态 / 说明'))
        assert '工况001_GB@25°C' in wb.sheetnames
        assert '工况002_GF1@50°C' in wb.sheetnames
        assert all('地面良好' not in s and '一般固定' not in s for s in wb.sheetnames)
        ws = wb['工况001_GB@25°C']
        assert ws.cell(row=2, column=2).value in (None, '')
        assert ws.cell(row=3, column=2).value in (None, '')
        assert ws.cell(row=2, column=6).value == '国产片式膜电阻器'
        assert ws.cell(row=3, column=6).value == '国产1类瓷介电容器'
    elif ext == '.xls':
        book = xlrd.open_workbook(str(out_path))
        assert book.sheet_names()[0] == OVERVIEW_SHEET
        assert '工况002_GF1@50°C' in book.sheet_names()
        assert all('地面良好' not in s and '一般固定' not in s for s in book.sheet_names())
        sheet = book.sheet_by_name('工况001_GB@25°C')
        assert sheet.cell_value(1, 1) == ''
        assert sheet.cell_value(2, 1) == ''
        assert sheet.cell_value(1, 5) == '国产片式膜电阻器'
        assert sheet.cell_value(2, 5) == '国产1类瓷介电容器'

    imported = load_supplier_project(out_path)
    assert imported.conditions[0].name == '工况001'
    assert [c.name for c in imported.components] == ['000001', '000002']
    assert imported.components[0].disclosure['model'] == '国产片式膜电阻器'
    assert imported.calculate(imported.conditions[0]).total_rate == pytest.approx(expected_0, rel=1e-9)
    assert imported.calculate(imported.conditions[1]).total_rate == pytest.approx(expected_1, rel=1e-9)


@pytest.mark.parametrize('ext', ['.xlsx', '.xls'])
def test_full_workbook_supplier_import_roundtrip(sample_project, isolated_tmp, ext):
    project = sample_project
    project.components = [c for c in project.components if c.model != '待配置']
    expected = project.calculate(project.conditions[0]).total_rate
    out_path = isolated_tmp / f'full_report{ext}'
    if ext == '.xlsx':
        export_workbook_xlsx(out_path, project)
    else:
        export_workbook_xls(out_path, project)
    imported = load_supplier_project(out_path)
    assert imported.calculate(imported.conditions[0]).total_rate == pytest.approx(expected, rel=1e-9)


def test_xlsx_xls_hardening_redaction_and_edge_cases(sample_project, isolated_tmp, monkeypatch):
    import copy
    import errno
    import io
    import os
    import zipfile

    from src.excel_input import read_xlsx
    from src.excel_limits import check_package
    from src.redacted_export import build_redacted_project
    from src.workbook_io import read_workbook_bytes

    proj = copy.deepcopy(sample_project)
    proj.components = [c for c in proj.components if c.model != '待配置']
    proj.components[0].bom['row']['Manufacturer Part Number'] = '#N/A'
    proj.conditions.append(OperatingCondition('重复工况', 'GB', 25, enabled=True))

    # Re-redacting an already-redacted project must preserve disclosed parameter ranges.
    red1 = build_redacted_project(proj)
    assert red1.components[0].disclosure['parameter_ranges']
    red2 = build_redacted_project(red1)
    assert red2.components[0].disclosure['parameter_ranges'] == red1.components[0].disclosure['parameter_ranges']

    # Nonoperating detail sheet populates column H ('非工作条件').
    nonop = copy.deepcopy(sample_project)
    nonop.components = [Component('R_N1', '国产电阻器（非工作详细）', 1, {'kind': '金属膜', 'quality': 'A1P', 'rated_power_W': 0.25, 'cycle_rate': 10}, domestic=True)]
    nonop.set_prediction_state('nonoperating')
    nonop_xlsx = isolated_tmp / 'nonop.xlsx'
    export_workbook_xlsx(nonop_xlsx, nonop)
    nonop_wb = openpyxl.load_workbook(nonop_xlsx, data_only=True)
    nonop_ws = nonop_wb['地面良好 25°C_GB@25°C']
    assert nonop_ws.cell(row=1, column=8).value == '非工作条件'
    assert '设备电源通-断循环率' in str(nonop_ws.cell(row=2, column=8).value)

    # #N/A text string and duplicate (env, temp) sheets round-trip cleanly.
    na_xlsx = isolated_tmp / 'na_and_dup.xlsx'
    export_workbook_xlsx(na_xlsx, proj)
    na_wb = openpyxl.load_workbook(na_xlsx, data_only=False)
    na_ws = na_wb['地面良好 25°C_GB@25°C']
    assert na_ws.cell(row=2, column=3).value == '#N/A'
    assert na_ws.cell(row=2, column=3).data_type == 's'
    loaded_na = load_supplier_project(na_xlsx)
    assert len(loaded_na.components[0].condition_rates) == 2

    # Entity expansion XML bomb and unsupported LZMA compression must be rejected.
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('[Content_Types].xml', '<?xml version="1.0"?><!DOCTYPE foo [<!ENTITY x "hi">]><Types>&x;</Types>')
        zf.writestr('xl/workbook.xml', '<workbook/>')
    with pytest.raises(ValueError):
        read_xlsx(buf.getvalue())
    with pytest.raises(ValueError):
        read_workbook_bytes(buf.getvalue(), '.xlsx')

    lzma_buf = io.BytesIO()
    with zipfile.ZipFile(lzma_buf, 'w', compression=zipfile.ZIP_LZMA) as zf:
        zf.writestr('xl/workbook.xml', b'<workbook/>')
    with zipfile.ZipFile(io.BytesIO(lzma_buf.getvalue())) as zf, pytest.raises(ValueError, match='压缩算法'):
        check_package(zf)

    # FAT32/exFAT hardlink fallback works when os.link raises EPERM.
    def _no_link(_src, _dst):
        raise PermissionError(errno.EPERM, 'hardlinks unsupported')

    monkeypatch.setattr(os, 'link', _no_link)
    usb_out = isolated_tmp / 'usb_redacted.xlsx'
    export_redacted_file(usb_out, proj)
    assert usb_out.is_file()
    with pytest.raises(RevisionConflict):
        export_redacted_file(usb_out, proj)


def test_supplier_module_sha256_and_bom_formats(sample_project, isolated_tmp):
    import copy

    import xlwt

    from src.bom import append_bom, designators, read_bom, read_bom_sources
    from src.project_import import clone_components
    from src.project_modules import create_project_module
    from src.shared_io import load_supplier_bundle

    proj = copy.deepcopy(sample_project)
    proj.components = [c for c in proj.components if c.model != '待配置']

    # Module import works across .json, .xlsx, and .xls using SHA-256 verification.
    for ext in ('.json', '.xlsx', '.xls'):
        mod_path = isolated_tmp / f'sub{ext}'
        export_redacted_file(mod_path, proj)
        loaded, digest = load_supplier_bundle(mod_path)
        mod = create_project_module(loaded, name=f'Mod{ext}', source_path=mod_path, expected_sha256=digest)
        assert mod.model == '工程模块'
        with pytest.raises(ValueError, match='已修改'):
            create_project_module(loaded, name='Bad', source_path=mod_path, expected_sha256='0' * 64)

    # External cloned items strip source designators so host append_bom does not collide.
    cloned = clone_components(proj, [proj.components[0].identifier], source_path=isolated_tmp / 'other.json')
    assert 'assigned_designators' not in cloned[0].bom

    # Chinese pause comma and range designator expansion.
    assert designators('R1、R2、R3') == ['R1', 'R2', 'R3']
    assert designators('R10-R14') == ['R10', 'R11', 'R12', 'R13', 'R14']
    assert designators('C01~C03') == ['C01', 'C02', 'C03']

    # CSV BOMs in UTF-8, GB18030, and UTF-16LE BOM.
    csv_text = '序号,位号,标称值,描述,数量\n1,"R1、R2",1k,贴片电阻,2\n2,C1-C3,100nF,贴片电容,\n'
    for enc_name, enc in (('utf8.csv', 'utf-8-sig'), ('gb.csv', 'gb18030'), ('u16.csv', 'utf-16')):
        p = isolated_tmp / enc_name
        p.write_bytes(csv_text.encode(enc))
        imported = read_bom(p)
        host = copy.deepcopy(sample_project)
        host.components = []
        assert append_bom(host, imported) == 5
        assert [c.bom['assigned_designators'] for c in host.components] == [['R1'], ['R2'], ['C1'], ['C2'], ['C3']]

    # .xlsx BOM read.
    bom_xlsx = isolated_tmp / 'bom.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(['Designator', 'Value', 'Quantity'])
    ws.append(['R20, R21', '10k', 2])
    wb.save(bom_xlsx)
    assert [c.bom['assigned_designators'] for c in read_bom(bom_xlsx).components()] == [['R20'], ['R21']]

    # .xls BOM with out-of-range Date serial must record cell error without crashing.
    bom_xls = isolated_tmp / 'bom_date.xls'
    xls_wb = xlwt.Workbook()
    xls_ws = xls_wb.add_sheet('BOM')
    xls_ws.write(0, 0, 'Designator')
    xls_ws.write(0, 1, 'Value')
    xls_ws.write(1, 0, 'R30')
    date_style = xlwt.easyxf(num_format_str='yyyy-mm-dd')
    xls_ws.write(1, 1, 20261117, date_style)
    xls_wb.save(str(bom_xls))
    sources = read_bom_sources(bom_xls)
    assert (1, 1) in sources[0].cell_errors and '日期' in sources[0].cell_errors[1, 1]


def test_autosave_provenance_meta_and_live_pid(sample_project, isolated_tmp):
    import os

    from src.ui.undo import (
        autosave_matches_target,
        autosave_meta_path,
        is_autosave_owned_by_other_live_pid,
        read_autosave_meta,
    )

    path = isolated_tmp / 'prov.json'
    rev1 = save_project(path, sample_project)
    draft = sample_project
    draft.name = '草稿版本'
    sidecar = write_autosave(path, draft, base_revision=rev1)
    meta_file = autosave_meta_path(path)
    assert sidecar.is_file() and meta_file.is_file()
    raw_sidecar = json.loads(sidecar.read_text(encoding='utf-8'))
    assert set(raw_sidecar) == {'format', 'version', 'revision', 'project'}
    assert raw_sidecar['revision'] == int(rev1)

    meta = read_autosave_meta(path)
    assert meta['base_revision'] == int(rev1)
    assert meta['base_fingerprint'] == rev1.fingerprint
    assert meta['pid'] == os.getpid()
    assert autosave_matches_target(path, rev1) is True
    assert is_autosave_owned_by_other_live_pid(path) is False

    # Simulate another live process (ppid) owning the sidecar vs dead PID.
    meta_other = dict(meta, pid=os.getppid())
    meta_file.write_text(json.dumps(meta_other), encoding='utf-8')
    if os.getppid() > 0 and os.getppid() != os.getpid():
        assert is_autosave_owned_by_other_live_pid(path) is True
    meta_file.write_text(json.dumps(dict(meta, pid=999_999_999)), encoding='utf-8')
    assert is_autosave_owned_by_other_live_pid(path) is False

    # External save advances revision -> provenance mismatch!
    loaded, loaded_rev = load_project(path)
    loaded.name = '外部同事更新'
    rev2 = save_project(path, loaded, loaded_rev)
    assert autosave_matches_target(path, rev2) is False

    # Legacy sidecar without .meta.json stays backward-compatible (True).
    meta_file.unlink()
    assert autosave_matches_target(path, rev2) is True

    # Re-write meta, then clear_autosave removes both sidecar and meta.
    write_autosave(path, draft, base_revision=rev2)
    assert autosave_meta_path(path).is_file()
    clear_autosave(path)
    assert find_autosave(path) is None and not autosave_meta_path(path).is_file()


def test_storage_permissions_and_posix_save_lock_contention(sample_project, isolated_tmp):
    import os
    import subprocess
    import sys

    from src.storage import _save_lock

    proj = sample_project
    proj.components = [c for c in proj.components if c.model != '待配置']
    path = isolated_tmp / 'shared_perm.json'
    save_project(path, proj)
    os.chmod(path, 0o664)
    _, rev1 = load_project(path)
    save_project(path, proj, rev1)
    assert (path.stat().st_mode & 0o777) == 0o664

    xlsx_out = isolated_tmp / 'shared_report.xlsx'
    export_workbook_xlsx(xlsx_out, proj)
    os.chmod(xlsx_out, 0o640)
    export_workbook_xlsx(xlsx_out, proj)
    assert (xlsx_out.stat().st_mode & 0o777) == 0o640

    # Concurrent POSIX per-file .lock contention raises RevisionConflict fast and cleans up lock file.
    if os.name != 'nt':
        lock_target = isolated_tmp / 'contended.json'
        holder = subprocess.Popen(
            [
                sys.executable,
                '-B',
                '-c',
                (
                    'import sys, time; from src.storage import _save_lock\n'
                    'with _save_lock(sys.argv[1]):\n'
                    '    sys.stdout.write("LOCKED\\n"); sys.stdout.flush()\n'
                    '    time.sleep(5)\n'
                ),
                str(lock_target),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout.readline().strip() == 'LOCKED'
            with pytest.raises(RevisionConflict, match='另一个窗口正在保存'), _save_lock(lock_target, timeout=0.15):
                pass
        finally:
            holder.terminate()
            holder.wait(timeout=5)


def test_bom_column_guard_limits_csv_streaming_and_hidden_excel(isolated_tmp, monkeypatch):
    import xlwt

    import src.excel_limits as excel_limits
    from src.bom import BomSource, designators, read_bom, read_bom_sources

    # Headerless BOM: infers Designator, Value, Footprint, Quantity by content rather than index.
    headerless = BomSource(
        'raw.csv',
        '0' * 64,
        [
            ['1', 'STM32-BOARD', 'R1', '0603', '10k', '1'],
            ['2', 'STM32-BOARD', 'R2, R3', '0603', '472', '2'],
            ['3', 'STM32-BOARD', 'C1', '0402', '104', '1'],
        ],
        header_row=None,
    )
    inferred = headerless.default_mapping(list(range(6)))
    assert inferred['Designator'] == 2
    assert inferred['Footprint'] == 3
    assert inferred['Value'] == 4
    assert inferred['Quantity'] == 5

    # Headerless BOM with sequential index + MPNs (no valid R/C/L Value column) must NOT map Value!
    no_val = BomSource(
        'mpn.csv',
        '0' * 64,
        [
            ['101', 'U1', 'STM32F407VGT6', '1'],
            ['102', 'U2', 'AD7606BSTZ', '1'],
            ['103', 'R1', '贴片精密电阻', '1'],
        ],
        header_row=None,
    )
    no_val_map = no_val.default_mapping(list(range(4)))
    assert no_val_map['Designator'] == 1
    assert no_val_map['Quantity'] == 3
    assert no_val_map['Value'] is None

    # Headered BOM with Altium 'Comment' column holding non-parameter text -> remapped to Description!
    bad_comment = BomSource(
        'altium.csv',
        '0' * 64,
        [
            ['Designator', 'Comment', 'Footprint', 'Quantity'],
            ['R1', '100', '0603', '1'],
            ['R2', '精密采样电阻', '0603', '1'],
            ['C1', '去耦陶瓷电容', '0402', '1'],
        ],
        header_row=0,
    )
    bc_map = bad_comment.default_mapping([0, 1, 2, 3])
    assert bc_map['Value'] is None
    assert bc_map['Description'] == 1

    # Genuine Altium 'Comment' column holding R/C values -> stays mapped to Value!
    good_comment = BomSource(
        'altium_ok.csv',
        '0' * 64,
        [
            ['Designator', 'Comment', 'Footprint', 'Quantity'],
            ['R1', '10k', '0603', '1'],
            ['R2', '472', '0603', '1'],
            ['C1', '100nF', '0402', '1'],
        ],
        header_row=0,
    )
    assert good_comment.default_mapping([0, 1, 2, 3])['Value'] == 1

    # Per-row (10,000) and per-import (50,000) designator expansion caps.
    with pytest.raises(ValueError, match='位号'):
        designators('R1-R6000, R6001-R12000')

    many_rows = [['Designator', 'Value']] + [
        [f'R{i * 8000 + 1}-R{(i + 1) * 8000}', '10k'] for i in range(7)
    ]
    huge_src = BomSource('huge.csv', '0' * 64, many_rows, header_row=0)
    with pytest.raises(ValueError, match='上限'):
        huge_src.build_import(1, 7, [0, 1], huge_src.default_mapping([0, 1]))

    # Streaming CSV MAX_CELLS guard aborts incrementally.
    with monkeypatch.context() as mp:
        mp.setattr(excel_limits, 'MAX_CELLS', 50)
        big_csv = isolated_tmp / 'too_many_cells.csv'
        big_csv.write_text('\n'.join('a,b,c,d,e' for _ in range(20)), encoding='utf-8')
        with pytest.raises(ValueError, match='过大'):
            read_bom_sources(big_csv)

    # Hidden sheets and hidden DNP rows skipped in both .xlsx and .xls BOM imports.
    dnp_xlsx = isolated_tmp / 'dnp.xlsx'
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = 'MainBOM'
    ws.append(['Designator', 'Value', 'Quantity'])
    ws.append(['R1', '10k', 1])
    ws.append(['R2', '10k', 1])
    ws.row_dimensions[3].hidden = True
    ws.append(['R3', '4.7k', 1])
    old_ws = wb.create_sheet('OldRev')
    old_ws.append(['Designator', 'Value'])
    old_ws.append(['U99', 'OLD'])
    old_ws.sheet_state = 'hidden'
    wb.save(dnp_xlsx)
    xlsx_srcs = read_bom_sources(dnp_xlsx)
    assert [s.sheet_name for s in xlsx_srcs] == ['MainBOM']
    assert [c.bom['assigned_designators'] for c in read_bom(dnp_xlsx).components()] == [['R1'], ['R3']]

    dnp_xls = isolated_tmp / 'dnp.xls'
    xwb = xlwt.Workbook()
    xws = xwb.add_sheet('MainBOM')
    for col, title in enumerate(('Designator', 'Value', 'Quantity')):
        xws.write(0, col, title)
    for r_idx, row_vals in ((1, ('C1', '100nF', 1)), (2, ('C2', '100nF', 1)), (3, ('C3', '10nF', 1))):
        for col, val in enumerate(row_vals):
            xws.write(r_idx, col, val)
    xws.row(2).hidden = True
    xold = xwb.add_sheet('HiddenSheet')
    xold.write(0, 0, 'Designator')
    xold.write(1, 0, 'U88')
    xold.visibility = 1
    xwb.save(str(dnp_xls))
    xls_srcs = read_bom_sources(dnp_xls)
    assert [s.sheet_name for s in xls_srcs] == ['MainBOM']
    assert [c.bom['assigned_designators'] for c in read_bom(dnp_xls).components()] == [['C1'], ['C3']]


def test_workbook_cross_sheet_checks_single_redact_and_sparse_bomb(sample_project, isolated_tmp, monkeypatch):
    import copy
    import io
    import zipfile

    import src.shared_io as shared_io
    from src.redacted_export import build_redacted_project
    from src.workbook_io import read_workbook_bytes

    proj = copy.deepcopy(sample_project)
    proj.components = [c for c in proj.components if c.model != '待配置']

    # [P3-19] export_redacted_file invokes build_redacted_project only ONCE.
    calls = []
    orig_redact = build_redacted_project

    def _counted_redact(p):
        calls.append(1)
        return orig_redact(p)

    with monkeypatch.context() as mp:
        mp.setattr(shared_io, 'build_redacted_project', _counted_redact)
        red_xlsx = isolated_tmp / 'once_redacted.xlsx'
        export_redacted_file(red_xlsx, proj)
        assert len(calls) == 1

    # [P2-24] Tampering MPN (col C) on 2nd sheet or adding unreferenced sheet is rejected.
    full_xlsx = isolated_tmp / 'full_check.xlsx'
    export_workbook_xlsx(full_xlsx, proj)

    wb_mpn = openpyxl.load_workbook(full_xlsx)
    wb_mpn[wb_mpn.sheetnames[2]].cell(row=2, column=3, value='TAMPERED-MPN')
    buf_mpn = io.BytesIO()
    wb_mpn.save(buf_mpn)
    with pytest.raises(ValueError, match='不一致'):
        read_workbook_bytes(buf_mpn.getvalue(), '.xlsx')

    wb_extra = openpyxl.load_workbook(full_xlsx)
    extra = wb_extra.create_sheet('SecretNotes')
    extra.cell(row=1, column=1, value='note')
    buf_extra = io.BytesIO()
    wb_extra.save(buf_extra)
    with pytest.raises(ValueError, match='附加工作表'):
        read_workbook_bytes(buf_extra.getvalue(), '.xlsx')

    wb_red = openpyxl.load_workbook(red_xlsx)
    ws2 = wb_red[wb_red.sheetnames[2]]
    ws2.cell(row=2, column=7, value='公开参数档位：内部')
    buf_red = io.BytesIO()
    wb_red.save(buf_red)
    with pytest.raises(ValueError, match='不一致'):
        read_workbook_bytes(buf_red.getvalue(), '.xlsx')

    # [P2-22] Sparse XFD1048576 dense-cell bomb rejected in streaming iterparse before openpyxl load.
    sparse_buf = io.BytesIO()
    with zipfile.ZipFile(sparse_buf, 'w') as zf:
        zf.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        zf.writestr(
            'xl/worksheets/sheet1.xml',
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1048576"><c r="XFD1048576"><v>1</v></c></row></sheetData></worksheet>',
        )
    with pytest.raises(ValueError, match='过大'):
        read_workbook_bytes(sparse_buf.getvalue(), '.xlsx')


def test_report_data_process_traces_project_modules_and_build_standalone(isolated_tmp, monkeypatch):
    import subprocess
    import zipfile

    import scripts.build_standalone as bs
    from src.analysis_methods import (
        bayesian_update,
        mission_profile,
        mode_contributions,
        physics_calculate,
    )
    from src.domain import Project
    from src.project_modules import create_project_module, module_project, source_condition_rates
    from src.report_data import (
        analysis_report_rows,
        calculation_process,
        component_report_values,
        numerical_substitution,
    )
    from src.standards import MODELS, evaluate

    # report_data.py: numerical_substitution, SMT trace, FET trace, and analysis_report_rows.
    sub_ok = numerical_substitution('λp = λb × πQ × πE', (('λb', 0.02, ''), ('πQ', 1.5, ''), ('πE', 2.0, '')), 0.06)
    assert sub_ok == '0.02 × 1.5 × 2'
    assert numerical_substitution('λp = λb × πQ', (('λb', 0.02, ''), ('πQ', 1.5, '')), 9.99) is None
    assert numerical_substitution('λp = max(A, B)', (('A', 1.0, ''), ('B', 2.0, '')), 2.0) is None

    smt_spec = MODELS['国产SMT互连']
    smt_comp = Component('PCB_SMT', '国产SMT互连', 1, dict(smt_spec.example))
    smt_eval = evaluate('国产SMT互连', smt_comp.parameters, 'GB', ambient_temperature_C=smt_spec.example_temperature)
    smt_proc = calculation_process(smt_comp, smt_eval, smt_spec.example_temperature)
    assert 'λSMT[整板] = max(' in smt_proc

    fet_spec = MODELS['国产硅场效应晶体管']
    fet_comp = Component('Q_FET', '国产硅场效应晶体管', 2, dict(fet_spec.example))
    fet_eval = evaluate('国产硅场效应晶体管', fet_comp.parameters, 'GB', ambient_temperature_C=fet_spec.example_temperature)
    fet_vals = component_report_values(fet_comp, fet_eval, OperatingCondition('GB', 'GB', fet_spec.example_temperature), 0)
    assert 'C = (' in fet_vals[7] and '小计 =' in fet_vals[7]

    m_res = mission_profile([
        {
            'name': '巡航',
            'state': 'working',
            'environment': 'GB',
            'ambient_temperature_C': 25,
            'duration_hours': 10,
            'rate': 0.2,
            'source': '手册估算',
        },
    ])
    b_res = bayesian_update(0.1, [{'source': '试验', 'failures': 0, 'component_hours': 1e6, 'acceleration_factor': 1}])
    f_res = mode_contributions(0.1, 'F.1', '线性集成电路')
    p_res = physics_calculate('em', {'parameter_source': '标定', 'a': 1.0, 'current_density': 10.0, 'n': 2.0, 'ea': 0.7, 'temperature_K': 350.0})
    for res in (m_res, b_res, f_res, p_res):
        rows = analysis_report_rows(res)
        assert any(r[0] == '参数' for r in rows) and any(r[0] == '结果' for r in rows)

    # project_modules.py: in-memory create_project_module, module_project, and conflicting rates error.
    c_known = Component(
        'K1', '已知单件失效率', 1, {},
        condition_rates=[{'environment': 'GB', 'ambient_temperature_C': 25, 'rate': 0.05}],
    )
    sub_p = Project('InMemSub', components=[c_known], conditions=[OperatingCondition('C1', 'GB', 25)])
    mod_comp = create_project_module(sub_p, name='内存子模块')
    restored = module_project(mod_comp)
    assert restored.name == 'InMemSub'
    with pytest.raises(ValueError, match='没有来源工程快照'):
        module_project(c_known)

    c_conflict = Component('R_CONF', '国产片式膜电阻器', 1, dict(MODELS['国产片式膜电阻器'].example))
    dup_cond_proj = Project(
        'Dup',
        components=[c_conflict],
        conditions=[OperatingCondition('A', 'GB', 25), OperatingCondition('B', 'GB', 25)],
    )
    assert len(source_condition_rates(dup_cond_proj)) == 1

    # scripts/build_standalone.py unit tests (strictly without invoking real Wine!).
    env = bs._build_wine_env({'DISPLAY': ':0', 'WAYLAND_DISPLAY': 'wayland-0', 'HOME': '/tmp/h'}, prefix=isolated_tmp / 'pfx')
    assert 'DISPLAY' not in env and 'WAYLAND_DISPLAY' not in env
    assert env['WINEDLLOVERRIDES'] == 'mscoree,mshtml=;winemenubuilder.exe=d'
    assert env['WINEDEBUG'] == '-all'

    bs.check_build_runtime_parity(bs.PROJECT_ROOT)

    fake_nupkg = isolated_tmp / 'fake.nupkg'
    fake_nupkg.write_bytes(b'corrupted-bytes')
    with pytest.raises(ValueError, match='SHA-256'):
        bs._verify_nupkg(fake_nupkg)

    wheels_dir = isolated_tmp / 'wheels'
    wheels_dir.mkdir()
    for pkg in ('PyQt6-6.8.1', 'PyQt6_Qt6-6.8.2', 'defusedxml-0.7.1', 'openpyxl-3.1.5', 'xlrd-2.0.2', 'xlwt-1.3.0', 'pyinstaller-6.16.0'):
        whl = wheels_dir / f'{pkg}-py3-none-any.whl'
        with zipfile.ZipFile(whl, 'w') as zf:
            zf.writestr(f'{pkg}.dist-info/RECORD', '')
    bs._verify_wheels_dir(wheels_dir)

    bad_whl = wheels_dir / 'extra-1.0-py3-none-any.whl'
    bad_whl.write_bytes(b'not-a-zip')
    with pytest.raises(ValueError, match='损坏'):
        bs._verify_wheels_dir(wheels_dir)

    # Verify build_windows cleanup calls wineserver -k with timeout even when PyInstaller fails.
    seen_calls = []

    def _fake_run(cmd, **kwargs):
        seen_calls.append((list(cmd), kwargs.get('timeout'), dict(kwargs.get('env') or {})))
        if 'PyInstaller' in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bs, 'ensure_wine_environment', lambda *_a, **_kw: ('/usr/bin/wine64', bs._build_wine_env({'DISPLAY': ':99'}, prefix=isolated_tmp / 'pfx')))
    monkeypatch.setattr(bs, '_to_wine_path', lambda _w, _e, p: 'Z:' + str(p))
    monkeypatch.setattr(bs.shutil, 'which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr(bs.subprocess, 'run', _fake_run)
    with pytest.raises(subprocess.CalledProcessError):
        bs.build_windows(bs.PROJECT_ROOT)
    assert any(cmd == ['/usr/bin/wineserver', '-k'] and timeout is not None for cmd, timeout, _e in seen_calls)
    assert all('DISPLAY' not in call_env and 'WAYLAND_DISPLAY' not in call_env for _c, _t, call_env in seen_calls)
