"""QUndoCommand snapshot manager and crash-safe .autosave.json helpers."""

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

from PyQt6.QtGui import QUndoCommand

from ..storage import (
    FORMAT,
    FORMAT_VERSION,
    Revision,
    _read_document,
    atomic_replace,
    preserve_or_default_mode,
)


class ProjectUndoCommand(QUndoCommand):
    """Reversible snapshot command over Project state and component selection."""

    def __init__(
        self,
        description,
        before_project,
        after_project,
        before_selected,
        after_selected,
        apply_fn,
        *,
        already_applied=True,
        merge_id=-1,
        copy_projects=True,
    ):
        super().__init__(description)
        self._before_project = copy.deepcopy(before_project) if copy_projects else before_project
        self._after_project = copy.deepcopy(after_project) if copy_projects else after_project
        self._before_selected = list(before_selected)
        self._after_selected = list(after_selected)
        self._apply_fn = apply_fn
        self._skip_first = already_applied
        self._merge_id = merge_id

    def id(self):
        return self._merge_id

    def mergeWith(self, other):
        if self._merge_id < 0 or other.id() != self._merge_id:
            return False
        self._after_project = other._after_project
        self._after_selected = list(other._after_selected)
        return True

    def redo(self):
        if self._skip_first:
            self._skip_first = False
            return
        self._apply_fn(copy.deepcopy(self._after_project), list(self._after_selected))

    def undo(self):
        self._apply_fn(copy.deepcopy(self._before_project), list(self._before_selected))


def autosave_path(project_path):
    """Return sidecar .autosave.json path next to a saved project file."""
    resolved = Path(project_path).resolve()
    return resolved.parent / ('.' + resolved.name + '.autosave.json')


def autosave_meta_path(project_path):
    """Return sidecar .autosave.meta.json path recording provenance of .autosave.json."""
    resolved = Path(project_path).resolve()
    return resolved.parent / ('.' + resolved.name + '.autosave.meta.json')


def _target_fingerprint(resolved_path):
    if not resolved_path.is_file():
        return None
    try:
        doc = _read_document(resolved_path)
        return Revision(doc['revision'], doc).fingerprint
    except (ValueError, OSError):
        try:
            return hashlib.sha256(resolved_path.read_bytes()).hexdigest()
        except OSError:
            return None


def _atomic_write_text(target, encoded):
    descriptor, name = tempfile.mkstemp(prefix='.autosave-', suffix='.tmp', dir=target.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        preserve_or_default_mode(temporary, target)
        atomic_replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def write_autosave(project_path, project, *, base_revision=None):
    """Atomically persist unsaved working copy and baseline provenance beside project_path."""
    if project_path is None:
        return None
    resolved = Path(project_path).resolve()
    target = autosave_path(resolved)
    meta_target = autosave_meta_path(resolved)
    has_base = base_revision is not None and not isinstance(base_revision, bool)
    rev_int = int(base_revision) if has_base and int(base_revision) >= 1 else None
    document = {
        'format': FORMAT,
        'version': FORMAT_VERSION,
        'revision': rev_int if rev_int is not None else 1,
        'project': project.to_dict(),
    }
    encoded = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    _atomic_write_text(target, encoded)

    base_fingerprint = getattr(base_revision, 'fingerprint', None) or _target_fingerprint(resolved)
    meta = {
        'base_revision': rev_int,
        'base_fingerprint': base_fingerprint,
        'pid': os.getpid(),
    }
    meta_encoded = json.dumps(meta, ensure_ascii=False, allow_nan=False, indent=2) + '\n'
    _atomic_write_text(meta_target, meta_encoded)
    return target


def read_autosave_meta(project_path):
    """Read and parse .autosave.meta.json if present and valid, else None."""
    if project_path is None:
        return None
    meta_file = autosave_meta_path(project_path)
    if not meta_file.is_file():
        return None
    try:
        value = json.loads(meta_file.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def autosave_matches_target(project_path, current_revision):
    """Return True if sidecar provenance matches current on-disk revision & fingerprint."""
    if project_path is None:
        return False
    resolved = Path(project_path).resolve()
    meta_file = autosave_meta_path(resolved)
    if not meta_file.is_file():
        return True
    meta = read_autosave_meta(resolved)
    if meta is None or current_revision is None or isinstance(current_revision, bool):
        return False
    base_rev = meta.get('base_revision')
    if base_rev is not None and (type(base_rev) is not int or base_rev != int(current_revision)):
        return False
    base_fp = meta.get('base_fingerprint')
    curr_fp = getattr(current_revision, 'fingerprint', None) or _target_fingerprint(resolved)
    if base_fp is not None and (not isinstance(base_fp, str) or curr_fp is None or base_fp != curr_fp):
        return False
    return True


def _is_pid_alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    if os.name == 'nt':
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == 259
                return True
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    try:
        stat_text = Path(f'/proc/{pid}/stat').read_text(encoding='ascii')
        after_comm = stat_text.rsplit(')', 1)[-1].split()
        if after_comm and after_comm[0] == 'Z':
            return False
    except OSError:
        pass
    return True


def is_autosave_owned_by_other_live_pid(project_path):
    """Return True when .autosave.meta.json belongs to a different currently-running process."""
    meta = read_autosave_meta(project_path)
    if not meta:
        return False
    pid = meta.get('pid')
    if type(pid) is not int or pid <= 0 or pid == os.getpid():
        return False
    return _is_pid_alive(pid)


def clear_autosave(project_path):
    """Remove any existing .autosave.json and .autosave.meta.json sidecars for project_path."""
    if project_path is not None:
        for getter in (autosave_path, autosave_meta_path):
            try:
                getter(project_path).unlink(missing_ok=True)
            except OSError:
                pass


def find_autosave(project_path):
    """Return the existing .autosave.json sidecar path if present, else None."""
    if project_path is None:
        return None
    candidate = autosave_path(project_path)
    return candidate if candidate.is_file() else None
