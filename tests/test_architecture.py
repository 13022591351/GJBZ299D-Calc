"""Architecture invariants: lazy xlwt/reports loading, purified SQLite schema, unified registry, links."""

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from src.model_spec import Evaluation
from src.reference_data import REFERENCE_PATH, stated_base_rate, stated_constant
from src.standards import AUTOMATIC_MODELS, MODELS, factor_html

ROOT = Path(__file__).resolve().parent.parent


def _run_python(code):
    env = {**os.environ, 'PYTHONDONTWRITEBYTECODE': '1', 'QT_QPA_PLATFORM': 'offscreen'}
    proc = subprocess.run(
        [sys.executable, '-c', code],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_unified_model_registry_counts_and_calculators():
    non_automatic = {'待配置', '已知单件失效率', '工程模块'}
    assert non_automatic <= set(MODELS)
    assert len(MODELS) >= 264
    assert len(AUTOMATIC_MODELS) == len(MODELS) - len(non_automatic)
    assert set(MODELS) - set(AUTOMATIC_MODELS) == non_automatic
    for name in AUTOMATIC_MODELS:
        spec = MODELS[name]
        assert spec.calculator is not None, f'{name} missing unified calculator'


def test_ui_import_does_not_eager_load_xlwt_or_reports():
    out = _run_python(
        'import sys, src.window; '
        'print("WORKBOOK_IO_LOADED=" + str("src.workbook_io" in sys.modules)); '
        'print("XLWT_LOADED=" + str("xlwt" in sys.modules))'
    )
    assert 'WORKBOOK_IO_LOADED=False' in out
    assert 'XLWT_LOADED=False' in out


def test_sqlite_uses_single_persistent_thread_local_connection():
    out = _run_python(
        'import sqlite3; orig = sqlite3.connect; count = 0\n'
        'def wrapped(*a, **kw):\n'
        '    global count; count += 1; return orig(*a, **kw)\n'
        'sqlite3.connect = wrapped\n'
        'from src import standards, reference_data\n'
        'reference_data.fragments("24"); reference_data.catalog(); reference_data.stated_base_rate("5.6.9")\n'
        'print(f"CONNECTIONS={count}")\n'
    )
    count = int(out.split('CONNECTIONS=')[1])
    assert count == 1


def test_pyproject_version_and_runtime_deps_in_sync():
    import tomllib

    import src

    data = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    assert 'version' in data['project'].get('dynamic', [])
    assert src.__version__ == '1.0'
    assert src.APP_VERSION == 'v1.0'
    deps = '\n'.join(data['project']['dependencies'])
    assert 'defusedxml' in deps


def test_purified_database_schema_and_constants():
    con = sqlite3.connect(REFERENCE_PATH.as_uri() + '?mode=ro', uri=True)
    try:
        tables = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        views = {
            row[0]
            for row in con.execute("SELECT name FROM sqlite_master WHERE type='view'")
        }
        assert tables == {'table_fragment', 'cell', 'parameter_definition', 'parameter_point', 'standard_constant'}
        assert views == {'parameter_value'}
        frag_cols = {row[1] for row in con.execute('PRAGMA table_info(table_fragment)')}
        cell_cols = {row[1] for row in con.execute('PRAGMA table_info(cell)')}
        assert not (frag_cols & {'html', 'source_line', 'page_id'})
        assert not (cell_cols & {'html', 'source_line'})
        assert con.execute('SELECT COUNT(*) FROM standard_constant').fetchone()[0] >= 17
        assert con.execute('SELECT COUNT(*) FROM table_fragment').fetchone()[0] >= 350
        assert con.execute('SELECT COUNT(*) FROM cell').fetchone()[0] >= 70000
    finally:
        con.close()

    assert stated_base_rate('5.6.9')[0] == 0.006
    assert stated_constant('5.15.1', 'λb1')[0] == 0.00007
    assert stated_constant('B.2.16.3', 'h')[0] == 0.2032
    assert 'Markdown' not in stated_base_rate('5.6.9')[1]


def test_factor_html_generates_single_pass_standard_links():
    result = Evaluation(
        rate=0.0123,
        factors=(
            ('λb', 0.01, '表 217；p125-s002-r001-c002'),
            ('πQ', 1.23, '表 C.47；p200-s001-r002-c003'),
        ),
    )
    rendered = factor_html(result, reference='GJB/Z 299D—2024 5.9.1 表 217')
    assert 'standard://table/217/p125-s002-r001-c002' in rendered
    assert 'standard://table/C.47/p200-s001-r002-c003' in rendered
    assert 'standard://section/' not in rendered
    assert 'p125-s002-r001-c002<' not in rendered
    assert rendered.count('<a ') == 3
    assert '<<a' not in rendered and 'href="standard://table/217/p125-s002-r001-c002"><a' not in rendered
