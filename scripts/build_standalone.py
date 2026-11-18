#!/usr/bin/env python3
"""Build Linux (.run + .zip) and Wine Windows (.exe + .zip) standalone releases."""

import argparse
import ast
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOT = PROJECT_ROOT / ".wine-win64"
WIN_PYTHON_VERSION = "3.12.8"
MIN_SUPPORTED_PYTHON = (3, 12)
NUPKG_URL = f"https://api.nuget.org/v3-flatcontainer/python/{WIN_PYTHON_VERSION}/python.{WIN_PYTHON_VERSION}.nupkg"
NUPKG_SHA256 = "406856be971d957e0bee7a5cefe20a5ec78d70a495e9e33cd0e53d31faec049d"
NUPKG_FILE = CACHE_ROOT / "downloads" / f"python.{WIN_PYTHON_VERSION}.nupkg"
PY_STAGED = CACHE_ROOT / "python312-staged"
WHEELS_DIR = CACHE_ROOT / "wheels"
WINE_PREFIX = CACHE_ROOT / "prefix"
DEFAULT_PIP_INDEX_URL = "https://mirrors.cloud.tencent.com/pypi/simple/"

SUBPROCESS_TIMEOUT_SHORT = 60
SUBPROCESS_TIMEOUT_INIT = 300
SUBPROCESS_TIMEOUT_BUILD = 900

WIN_REQUIREMENTS = (
    "PyQt6==6.8.1",
    "PyQt6-Qt6==6.8.2",
    "defusedxml>=0.7.1,<1",
    "openpyxl>=3.1,<4",
    "xlrd>=2.0,<3",
    "xlwt>=1.3,<2",
    "pyinstaller>=6.16,<7",
    "pefile>=2022.5.30",
    "pywin32-ctypes>=0.2.1",
    "setuptools>=75",
)
_REQUIRED_WHEEL_PREFIXES = (
    "pyqt6-",
    "pyqt6_qt6-",
    "defusedxml-",
    "openpyxl-",
    "xlrd-",
    "xlwt-",
    "pyinstaller-",
)


def _requirements_tag():
    return hashlib.sha256("\n".join(WIN_REQUIREMENTS).encode("utf-8")).hexdigest()[:12]


def _sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_nupkg(path, expected_sha256=NUPKG_SHA256):
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(f"缺少 Windows Python NuGet 包：{target}")
    actual = _sha256_file(target)
    if actual.lower() != expected_sha256.lower():
        raise ValueError(
            f"Windows Python NuGet 包 SHA-256 校验失败（期望 {expected_sha256}，实际 {actual}）"
        )


def _verify_wheels_dir(wheels_dir):
    wheels_dir = Path(wheels_dir)
    if not wheels_dir.is_dir():
        raise FileNotFoundError(f"缺少离线 wheel 缓存目录：{wheels_dir}")
    wheel_files = sorted(wheels_dir.glob("*.whl"))
    lower_names = [w.name.lower() for w in wheel_files]
    missing = [
        prefix.rstrip("-_")
        for prefix in _REQUIRED_WHEEL_PREFIXES
        if not any(name.startswith(prefix) for name in lower_names)
    ]
    if missing:
        raise ValueError(f"离线 wheel 缓存缺失核心依赖包：{', '.join(missing)}")
    for whl in wheel_files:
        if whl.stat().st_size == 0:
            raise ValueError(f"离线 wheel 文件为空：{whl.name}")
        try:
            with zipfile.ZipFile(whl) as archive:
                names = archive.namelist()
                if not any(name.endswith(".dist-info/RECORD") for name in names):
                    raise ValueError(f"离线 wheel 缺少 RECORD 清单：{whl.name}")
        except zipfile.BadZipFile as error:
            raise ValueError(f"离线 wheel 压缩包损坏：{whl.name}") from error


def check_build_runtime_parity(root=PROJECT_ROOT):
    """Verify src/ syntax compatibility with target Python 3.12 runtime and report version baseline."""
    if sys.version_info < MIN_SUPPORTED_PYTHON:
        raise RuntimeError(
            f"构建宿主 Python 版本过低（当前 {sys.version.split()[0]}，要求 >= 3.12）"
        )
    src_dir = Path(root) / "src"
    for py_file in sorted(src_dir.rglob("*.py")):
        source = py_file.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(py_file), feature_version=MIN_SUPPORTED_PYTHON)
        except SyntaxError as error:
            raise RuntimeError(
                f"{py_file.relative_to(root)} 使用了超出 Windows 目标运行时 Python {WIN_PYTHON_VERSION} 的语法：{error}"
            ) from error
    try:
        from PyQt6.QtCore import PYQT_VERSION_STR
    except Exception:
        PYQT_VERSION_STR = "unknown"
    host_py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    print(
        f"[Parity]  Host Python {host_py} / PyQt6 {PYQT_VERSION_STR} | "
        f"Windows target Python {WIN_PYTHON_VERSION} / PyQt6 6.8.1 (Python >=3.12 AST verified)"
    )


def _download_python_nupkg():
    if NUPKG_FILE.is_file():
        try:
            _verify_nupkg(NUPKG_FILE)
            return
        except ValueError:
            NUPKG_FILE.unlink(missing_ok=True)
    NUPKG_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = NUPKG_FILE.with_suffix(".part")
    print(f"Downloading Windows Python {WIN_PYTHON_VERSION} -> {NUPKG_FILE} ...")
    try:
        with urllib.request.urlopen(NUPKG_URL, timeout=60) as response, tmp.open("wb") as dst:
            shutil.copyfileobj(response, dst)
        _verify_nupkg(tmp)
        tmp.replace(NUPKG_FILE)
    finally:
        tmp.unlink(missing_ok=True)


def _extract_python_nupkg():
    if (PY_STAGED / "python.exe").is_file():
        return
    _verify_nupkg(NUPKG_FILE)
    staging = PY_STAGED.with_name(PY_STAGED.name + ".tmp")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    staging_root = staging.resolve()
    with zipfile.ZipFile(NUPKG_FILE) as archive:
        for member in archive.infolist():
            if not member.filename.startswith("tools/") or member.is_dir():
                continue
            rel = member.filename[len("tools/"):]
            if not rel:
                continue
            target = (staging / rel).resolve()
            if not target.is_relative_to(staging_root):
                raise ValueError(f"Unsafe path in Python nupkg archive: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
    shutil.rmtree(PY_STAGED, ignore_errors=True)
    staging.rename(PY_STAGED)


def prepare_windows_cache(host_python, *, refresh=False):
    """Download and stage Windows Python 3.12.8 + win_amd64 wheels offline."""
    _download_python_nupkg()
    _extract_python_nupkg()
    tag = _requirements_tag()
    marker = WHEELS_DIR / f".wheels-{tag}.ok"
    cache_valid = False
    if marker.is_file() and not refresh:
        try:
            _verify_wheels_dir(WHEELS_DIR)
            cache_valid = True
        except (OSError, ValueError):
            marker.unlink(missing_ok=True)
    if not cache_valid:
        if refresh:
            shutil.rmtree(WHEELS_DIR, ignore_errors=True)
        WHEELS_DIR.mkdir(parents=True, exist_ok=True)
        for old_marker in WHEELS_DIR.glob(".wheels-*.ok"):
            old_marker.unlink(missing_ok=True)
        index_url = os.environ.get("PIP_INDEX_URL", DEFAULT_PIP_INDEX_URL)
        print(f"Downloading win_amd64 wheels -> {WHEELS_DIR} ...")
        subprocess.run(
            [
                host_python,
                "-m",
                "pip",
                "download",
                "-i",
                index_url,
                "--timeout",
                "20",
                "--retries",
                "2",
                "-d",
                str(WHEELS_DIR),
                "--platform",
                "win_amd64",
                "--python-version",
                "312",
                "--only-binary=:all:",
                *WIN_REQUIREMENTS,
            ],
            check=True,
            timeout=SUBPROCESS_TIMEOUT_BUILD,
        )
        _verify_wheels_dir(WHEELS_DIR)
        marker.write_text("ok\n", encoding="utf-8")
    return PY_STAGED, WHEELS_DIR


def _build_wine_env(base_env=None, *, prefix=WINE_PREFIX):
    """Build a headless Wine environment stripped of X11/Wayland displays and menu builders."""
    source = os.environ if base_env is None else base_env
    env = {
        key: value
        for key, value in source.items()
        if key not in ("DISPLAY", "WAYLAND_DISPLAY")
    }
    env.update({
        "WINEPREFIX": str(prefix),
        "WINEARCH": "win64",
        "WINEDEBUG": "-all",
        "WINEDLLOVERRIDES": "mscoree,mshtml=;winemenubuilder.exe=d",
    })
    return env


def _kill_wineserver(env):
    """Terminate any lingering wineserver/winedevice background daemons for this WINEPREFIX."""
    wineserver = shutil.which("wineserver")
    if not wineserver:
        return
    try:
        subprocess.run(
            [wineserver, "-k"],
            env=env,
            check=False,
            timeout=SUBPROCESS_TIMEOUT_SHORT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.SubprocessError, OSError):
        pass


def _to_wine_path(wine_bin, env, path):
    resolved = str(Path(path).resolve())
    winepath = shutil.which("winepath")
    try:
        cmd = [winepath, "-w", resolved] if winepath else [wine_bin, "winepath", "-w", resolved]
        out = subprocess.check_output(
            cmd, env=env, text=True, timeout=SUBPROCESS_TIMEOUT_SHORT
        ).strip()
        if out:
            return out
    except (subprocess.SubprocessError, OSError):
        pass
    return "Z:" + resolved.replace("/", "\\")


def ensure_wine_environment(host_python, *, refresh_wheels=False):
    """Ensure win64 WINEPREFIX has Python 3.12 + all offline wheels installed."""
    prepare_windows_cache(host_python, refresh=refresh_wheels)
    wine_bin = shutil.which("wine64") or shutil.which("wine")
    if wine_bin is None:
        raise RuntimeError(
            "未检测到 wine64 / wine；请先安装 Wine 后重试"
        )
    env = _build_wine_env()
    wineserver = shutil.which("wineserver")
    wineboot = shutil.which("wineboot")
    ran_wine = False
    try:
        if not (WINE_PREFIX / "system.reg").is_file():
            WINE_PREFIX.mkdir(parents=True, exist_ok=True)
            boot_cmd = [wineboot, "--init"] if wineboot else [wine_bin, "wineboot", "--init"]
            ran_wine = True
            subprocess.run(boot_cmd, env=env, check=True, timeout=SUBPROCESS_TIMEOUT_INIT)
            if wineserver:
                subprocess.run(
                    [wineserver, "-w"], env=env, check=False, timeout=SUBPROCESS_TIMEOUT_SHORT
                )

        wine_py_dir = WINE_PREFIX / "drive_c" / "Python312"
        if not (wine_py_dir / "python.exe").is_file():
            shutil.rmtree(wine_py_dir, ignore_errors=True)
            shutil.copytree(PY_STAGED, wine_py_dir)

        tag = _requirements_tag()
        deps_marker = WINE_PREFIX / f".deps-{tag}.ok"
        if not deps_marker.is_file() or refresh_wheels:
            for old_marker in WINE_PREFIX.glob(".deps-*.ok"):
                old_marker.unlink(missing_ok=True)
            ran_wine = True
            wheels_win = _to_wine_path(wine_bin, env, WHEELS_DIR)
            subprocess.run(
                [
                    wine_bin,
                    r"C:\Python312\python.exe",
                    "-B",
                    "-m",
                    "pip",
                    "uninstall",
                    "-y",
                    "PyQt6",
                    "PyQt6-Qt6",
                ],
                env=env,
                check=False,
                timeout=SUBPROCESS_TIMEOUT_INIT,
            )
            subprocess.run(
                [
                    wine_bin,
                    r"C:\Python312\python.exe",
                    "-B",
                    "-m",
                    "pip",
                    "install",
                    "--no-index",
                    "--find-links",
                    wheels_win,
                    "--no-warn-script-location",
                    *WIN_REQUIREMENTS,
                ],
                env=env,
                check=True,
                timeout=SUBPROCESS_TIMEOUT_BUILD,
            )
            if wineserver:
                subprocess.run(
                    [wineserver, "-w"], env=env, check=False, timeout=SUBPROCESS_TIMEOUT_SHORT
                )
            deps_marker.write_text("ok\n", encoding="utf-8")
    finally:
        if ran_wine:
            _kill_wineserver(env)
    return wine_bin, env


def _populate_bundle_dir(bundle_dir, root):
    db_src = root / "database" / "standard_reference.sqlite"
    if not db_src.is_file():
        raise FileNotFoundError(f"缺少运行数据库：{db_src}")
    db_dst = bundle_dir / "database" / "standard_reference.sqlite"
    db_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(db_src, db_dst)


def _write_zip_archive(bundle_dir, archive):
    archive.unlink(missing_ok=True)
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for file_path in sorted(bundle_dir.rglob("*")):
            if not file_path.is_file():
                continue
            arcname = file_path.relative_to(bundle_dir.parent).as_posix()
            info = zipfile.ZipInfo.from_file(file_path, arcname)
            info.compress_type = zipfile.ZIP_DEFLATED
            with zf.open(info, "w") as dst, file_path.open("rb") as src:
                shutil.copyfileobj(src, dst)


def build_linux(root, *, clean=False):
    check_build_runtime_parity(root)
    bundle_dir = root / "dist" / "GJB299D-Calc-Linux-x86_64"
    work_dir = root / "build" / "pyinstaller-linux"
    spec_path = root / "packaging" / "GJB299D-Calc.spec"
    shutil.rmtree(bundle_dir, ignore_errors=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        shutil.rmtree(work_dir, ignore_errors=True)

    cache_dir = root / "build" / "pyinstaller-cache-linux"
    cache_dir.mkdir(parents=True, exist_ok=True)
    linux_env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("DISPLAY", "WAYLAND_DISPLAY")
    }
    linux_env["PYINSTALLER_CONFIG_DIR"] = str(cache_dir)
    cmd = [
        sys.executable,
        "-B",
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--distpath",
        str(bundle_dir),
        "--workpath",
        str(work_dir),
    ]
    if clean:
        cmd.append("--clean")
    subprocess.run(
        cmd + [str(spec_path)],
        cwd=root,
        env=linux_env,
        check=True,
        timeout=SUBPROCESS_TIMEOUT_BUILD,
    )

    exe = bundle_dir / "GJB299D-Calc.run"
    if not exe.is_file():
        raise FileNotFoundError(f"Linux 打包未生成目标文件：{exe}")
    exe.chmod(0o755)
    _populate_bundle_dir(bundle_dir, root)

    (root / "dist" / "GJB299D-Calc-Linux-x86_64.tar.gz").unlink(missing_ok=True)
    archive = root / "dist" / "GJB299D-Calc-Linux-x86_64.zip"
    _write_zip_archive(bundle_dir, archive)

    print(
        f"[Linux]   Dir:     {bundle_dir} (exe {exe.stat().st_size / (1024 * 1024):.1f} MB)"
    )
    print(
        f"[Linux]   Archive: {archive} ({archive.stat().st_size / (1024 * 1024):.1f} MB)"
    )
    return bundle_dir, archive


def build_windows(root, *, clean=False, refresh_wheels=False):
    check_build_runtime_parity(root)
    bundle_dir = root / "dist" / "GJB299D-Calc-Windows-x86_64"
    work_dir = root / "build" / "pyinstaller-win64"
    spec_path = root / "packaging" / "GJB299D-Calc.spec"
    shutil.rmtree(bundle_dir, ignore_errors=True)
    bundle_dir.mkdir(parents=True, exist_ok=True)
    if clean:
        shutil.rmtree(work_dir, ignore_errors=True)

    win_cache_dir = root / "build" / "pyinstaller-cache-win64"
    win_cache_dir.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        win_env = {**os.environ, "PYINSTALLER_CONFIG_DIR": str(win_cache_dir)}
        cmd = [
            sys.executable,
            "-B",
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--distpath",
            str(bundle_dir),
            "--workpath",
            str(work_dir),
        ]
        if clean:
            cmd.append("--clean")
        subprocess.run(
            cmd + [str(spec_path)],
            cwd=root,
            env=win_env,
            check=True,
            timeout=SUBPROCESS_TIMEOUT_BUILD,
        )
    else:
        env = _build_wine_env()
        try:
            wine_bin, env = ensure_wine_environment(
                sys.executable, refresh_wheels=refresh_wheels
            )
            env["PYINSTALLER_CONFIG_DIR"] = _to_wine_path(wine_bin, env, win_cache_dir)
            cmd = [
                wine_bin,
                r"C:\Python312\python.exe",
                "-B",
                "-m",
                "PyInstaller",
                "--noconfirm",
                "--distpath",
                _to_wine_path(wine_bin, env, bundle_dir),
                "--workpath",
                _to_wine_path(wine_bin, env, work_dir),
            ]
            if clean:
                cmd.append("--clean")
            subprocess.run(
                cmd + [_to_wine_path(wine_bin, env, spec_path)],
                cwd=root,
                env=env,
                check=True,
                timeout=SUBPROCESS_TIMEOUT_BUILD,
            )
            wineserver = shutil.which("wineserver")
            if wineserver:
                subprocess.run(
                    [wineserver, "-w"],
                    env=env,
                    check=False,
                    timeout=SUBPROCESS_TIMEOUT_SHORT,
                )
        finally:
            _kill_wineserver(env)

    exe = bundle_dir / "GJB299D-Calc.exe"
    if not exe.is_file():
        raise FileNotFoundError(f"Windows 打包未生成目标文件：{exe}")
    if exe.stat().st_size < 30 * 1024 * 1024:
        raise RuntimeError(
            f"Windows 可执行文件体积异常（{exe.stat().st_size} bytes），疑似缺失 Qt 运行库"
        )
    _populate_bundle_dir(bundle_dir, root)

    archive = root / "dist" / "GJB299D-Calc-Windows-x86_64.zip"
    _write_zip_archive(bundle_dir, archive)

    print(
        f"[Windows] Dir:     {bundle_dir} (exe {exe.stat().st_size / (1024 * 1024):.1f} MB)"
    )
    print(
        f"[Windows] Archive: {archive} ({archive.stat().st_size / (1024 * 1024):.1f} MB)"
    )
    return bundle_dir, archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target",
        choices=("all", "linux", "windows"),
        default="all",
        help="Target platform(s) to package",
    )
    parser.add_argument(
        "--prepare-windows",
        action="store_true",
        help="Download Windows Python 3.12.8 and win_amd64 wheels into .wine-win64/ only",
    )
    parser.add_argument("--clean", action="store_true", help="Clean PyInstaller build cache")
    parser.add_argument(
        "--refresh-wheels",
        action="store_true",
        help="Re-download win_amd64 wheels into .wine-win64/wheels and reinstall into Wine Python",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    if args.prepare_windows:
        staged, wheels = prepare_windows_cache(sys.executable, refresh=args.refresh_wheels)
        print(f"Windows Python staged: {staged}")
        print(f"Windows wheels cached: {wheels}")
        return 0

    if args.target in ("all", "linux"):
        build_linux(root, clean=args.clean)
    if args.target in ("all", "windows"):
        build_windows(root, clean=args.clean, refresh_wheels=args.refresh_wheels)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
