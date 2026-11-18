"""Shared pytest configuration forcing offscreen Qt and isolated QSettings."""

import atexit
import os
import shutil
import tempfile
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PyQt6.QtCore import QSettings

_SETTINGS_DIR = tempfile.mkdtemp(prefix='gjb299d_qsettings_')
atexit.register(lambda: shutil.rmtree(_SETTINGS_DIR, ignore_errors=True))

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)


@pytest.fixture(scope='session')
def qapp():
    from src.app import create_application

    app = create_application()
    yield app


@pytest.fixture(autouse=True)
def _isolate_qsettings_and_theme(tmp_path):
    from PyQt6.QtWidgets import QApplication

    from src.theme import apply_theme, current_theme

    per_test = tmp_path / '_qsettings'
    per_test.mkdir(exist_ok=True)
    QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, str(per_test))
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(per_test))
    try:
        yield
    finally:
        app = QApplication.instance()
        if app is not None and current_theme() != 'dark':
            apply_theme(app, 'dark', persist=False)
        QSettings.setPath(QSettings.Format.NativeFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _SETTINGS_DIR)


@pytest.fixture
def sample_project():
    from src.domain import Component, OperatingCondition, Project

    project = Project('单元测试示例工程')
    project.conditions = [
        OperatingCondition(name='地面良好 25℃', environment='GB', ambient_temperature_C=25, enabled=True),
        OperatingCondition(name='一般固定 50℃', environment='GF1', ambient_temperature_C=50, enabled=True),
    ]
    project.components = [
        Component(
            name='R1',
            model='国产片式膜电阻器',
            quantity=4,
            parameters={'resistance_ohm': 1000.0, 'film': '薄膜', 'quality': 'A1P'},
            domestic=True,
            bom={'file_name': 'sample.csv', 'sha256': '0' * 64,
                 'row': {'Value': '1k', 'Footprint': '0603', 'Manufacturer Part Number': 'RC0603FR-071KL'},
                 'assigned_designators': ['R1', 'R2', 'R3', 'R4']},
        ),
        Component(
            name='C1',
            model='国产1类瓷介电容器',
            quantity=2,
            parameters={'capacitance_uF': 0.0001, 'mounting': '片式', 'quality': 'A1P', 'stress_ratio': 0.4},
            domestic=True,
            bom={'file_name': 'sample.csv', 'sha256': '0' * 64,
                 'row': {'Value': '100pF', 'Footprint': '0603', 'Manufacturer Part Number': 'C0603C101J5GAC'},
                 'assigned_designators': ['C1', 'C2']},
        ),
        Component(
            name='U1',
            model='待配置',
            quantity=1,
            parameters={},
            domestic=True,
            bom={'file_name': 'sample.csv', 'sha256': '0' * 64,
                 'row': {'Value': 'STM32F4', 'Footprint': 'LQFP64', 'Manufacturer Part Number': 'STM32F407'},
                 'assigned_designators': ['U1']},
        ),
    ]
    project.validate()
    return project


@pytest.fixture
def isolated_tmp(tmp_path: Path):
    return tmp_path
