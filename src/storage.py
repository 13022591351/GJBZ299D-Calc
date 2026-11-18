"""Readable UTF-8 JSON projects, atomic saves, and optimistic revision checks."""

import errno
import hashlib
import json
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from .domain import Project
from .excel_limits import MAX_PACKAGE_BYTES

FORMAT = 'GJB299D-Project'
FORMAT_VERSION = 1
MAX_JSON_DEPTH = 128
SAVE_LOCK_TIMEOUT = 2.0
_HARDLINK_UNSUPPORTED_ERRNOS = frozenset(
    value for name in ('EPERM', 'EOPNOTSUPP', 'ENOTSUP', 'EXDEV', 'EACCES')
    if (value := getattr(errno, name, None)) is not None
)
_HARDLINK_UNSUPPORTED_WINERRORS = frozenset({1, 50})


def _current_umask():
    try:
        with open('/proc/self/status', encoding='ascii') as status_file:
            for line in status_file:
                if line.startswith('Umask:'):
                    return int(line.split()[1], 8)
    except (OSError, ValueError, IndexError):
        pass
    mask = os.umask(0o077)
    os.umask(mask)
    return mask


def default_file_mode():
    return 0o666 & ~_current_umask()


def preserve_or_default_mode(temporary, destination):
    """Apply existing destination permissions (or 0o666 & ~umask for new files) to a temp file."""
    try:
        mode = os.stat(destination).st_mode & 0o777
    except OSError:
        mode = default_file_mode()
    try:
        os.chmod(temporary, mode)
    except OSError:
        pass


def _fsync_directory(directory):
    """Best-effort fsync of parent directory so atomic renames survive power loss."""
    if os.name == 'nt':
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class RevisionConflict(ValueError):
    pass


class Revision(int):
    """Revision number with an in-memory fingerprint for external text edits."""
    def __new__(cls, value, document):
        instance = super().__new__(cls, value)
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, sort_keys=True).encode('utf-8')
        instance.fingerprint = hashlib.sha256(encoded).hexdigest()
        return instance


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'工程 JSON 中存在重复字段：{key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f'工程 JSON 不允许非有限数值：{value}')


def parse_document(encoded):
    try:
        text = encoded.decode('utf-8-sig') if isinstance(encoded, bytes) else encoded
        value = json.loads(text, object_pairs_hook=_unique_object,
                           parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError('不是有效的 UTF-8 JSON 工程文件') from error
    except RecursionError as error:
        raise ValueError('工程 JSON 嵌套层数过多，无法读取') from error
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise ValueError('工程 JSON 嵌套层数过多，无法读取')
        children = item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        pending.extend((child, depth + 1) for child in children if isinstance(child, (dict, list)))
    if not isinstance(value, dict) or set(value) != {'format', 'version', 'revision', 'project'}:
        raise ValueError('工程 JSON 顶层字段应为 format、version、revision、project')
    if value['format'] != FORMAT or type(value['version']) is not int or value['version'] != FORMAT_VERSION:
        raise ValueError('工程 JSON 格式或版本不受支持')
    if type(value['revision']) is not int or value['revision'] < 1:
        raise ValueError('工程修订号必须是正整数')
    return value


def _read_document(path):
    if path.stat().st_size > MAX_PACKAGE_BYTES:
        raise ValueError('工程文件过大，无法读取')
    return parse_document(path.read_bytes())


def load_project(path):
    value = _read_document(Path(path).resolve())
    try:
        return Project.from_dict(value['project']), Revision(value['revision'], value)
    except (TypeError, KeyError, OverflowError, RecursionError) as error:
        raise ValueError('工程内容格式无效') from error


@contextmanager
def _save_lock(path, *, timeout=SAVE_LOCK_TIMEOUT):
    """Serialize local writers per file with bounded timeout; OS releases lock on exit."""
    path = Path(path).resolve()
    lock_path = path.parent / ('.' + path.name + '.lock')
    deadline = time.monotonic() + max(0.0, float(timeout))
    if os.name == 'nt':
        import msvcrt
        while True:
            handle = lock_path.open('a+b')
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                break
            except OSError as error:
                handle.close()
                if time.monotonic() >= deadline:
                    raise RevisionConflict('另一个窗口正在保存，请稍后重试') from error
                time.sleep(min(0.04, max(0.005, deadline - time.monotonic())))
        try:
            yield
        finally:
            try:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
            handle.close()
            try:
                lock_path.unlink(missing_ok=True)
            except OSError:
                pass
    else:
        import fcntl
        while True:
            descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o666)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                os.close(descriptor)
                if error.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise RevisionConflict('另一个窗口正在保存，请稍后重试') from error
                time.sleep(min(0.04, max(0.005, deadline - time.monotonic())))
                continue
            st_fd = os.fstat(descriptor)
            try:
                st_path = os.stat(lock_path)
            except FileNotFoundError:
                st_path = None
            if st_path is None or (st_fd.st_dev, st_fd.st_ino) != (st_path.st_dev, st_path.st_ino):
                os.close(descriptor)
                continue
            try:
                yield
            finally:
                try:
                    st_cur = os.stat(lock_path)
                    if (st_cur.st_dev, st_cur.st_ino) == (st_fd.st_dev, st_fd.st_ino):
                        lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError:
                    pass
                os.close(descriptor)
            return


def atomic_replace(temporary, destination):
    """Replace destination atomically, retrying transient Windows file-scanner locks."""
    delays = (0.02, 0.04, 0.08) if os.name == 'nt' else ()
    for attempt in range(len(delays) + 1):
        try:
            os.replace(temporary, destination)
            _fsync_directory(Path(destination).parent)
            return
        except PermissionError as error:
            if attempt < len(delays):
                time.sleep(delays[attempt])
                continue
            raise PermissionError(
                f'无法写入文件（可能正被 Excel 等程序占用或目录只读）：{destination}'
            ) from error


def exclusive_commit(temporary, destination, conflict_message):
    """Commit a new file without overwriting an existing destination, even without hardlinks."""
    try:
        os.link(temporary, destination)
        _fsync_directory(Path(destination).parent)
        return
    except FileExistsError as error:
        raise RevisionConflict(conflict_message) from error
    except OSError as error:
        unsupported = (
            error.errno in _HARDLINK_UNSUPPORTED_ERRNOS
            or getattr(error, 'winerror', None) in _HARDLINK_UNSUPPORTED_WINERRORS
        )
        if not unsupported:
            raise
        try:
            mode = os.stat(temporary).st_mode & 0o777
        except OSError:
            mode = default_file_mode()
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_BINARY', 0)
        try:
            dest_fd = os.open(destination, flags, mode)
        except FileExistsError as excl_error:
            raise RevisionConflict(conflict_message) from excl_error
        try:
            with os.fdopen(dest_fd, 'wb') as dst, Path(temporary).open('rb') as src:
                shutil.copyfileobj(src, dst)
                dst.flush()
                os.fsync(dst.fileno())
        except Exception:
            Path(destination).unlink(missing_ok=True)
            raise
        _fsync_directory(Path(destination).parent)


def save_project(path, project, expected_revision=None):
    payload = project.to_dict()
    path = Path(path).resolve()
    if expected_revision is not None and (not isinstance(expected_revision, int)
                                         or isinstance(expected_revision, bool) or expected_revision < 1):
        raise ValueError('预期修订号必须是正整数')
    with _save_lock(path):
        if expected_revision is None:
            if path.exists():
                raise RevisionConflict('目标已存在，请换一个文件名或先打开该工程')
            revision = 1
        else:
            if not path.is_file():
                raise RevisionConflict('原工程文件已被移走，请另存为新文件')
            current = _read_document(path)
            fingerprint = getattr(expected_revision, 'fingerprint', None)
            if (current['revision'] != expected_revision or (fingerprint is not None
                    and fingerprint != Revision(current['revision'], current).fingerprint)):
                raise RevisionConflict('工程已被另一窗口修改，请另存为，或重新打开后编辑')
            revision = expected_revision + 1
        document = {'format': FORMAT, 'version': FORMAT_VERSION, 'revision': revision, 'project': payload}
        encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
        descriptor, name = tempfile.mkstemp(prefix='.gjb299d-', suffix='.tmp', dir=path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            preserve_or_default_mode(temporary, path)
            if expected_revision is None:
                exclusive_commit(temporary, path, '目标已存在，请换一个文件名或先打开该工程')
            else:
                atomic_replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return Revision(revision, document)
