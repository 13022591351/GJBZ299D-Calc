"""Tests for scripts/sync_gitee_release.py and .github/workflows/sync-gitee.yml."""

from __future__ import annotations

import email.parser
import email.policy
import http.server
import importlib.util
import json
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "sync_gitee_release.py"
WORKFLOW_PATH = PROJECT_ROOT / ".github" / "workflows" / "sync-gitee.yml"


def _load_sync_module():
    spec = importlib.util.spec_from_file_location("sync_gitee_release", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


sync_mod = _load_sync_module()


class _MockGiteeState:
    def __init__(self, *, tag_lookup_returns_null: bool = True) -> None:
        self.tag_lookup_returns_null = tag_lookup_returns_null
        self.expected_token = "secret-gitee-token-123"
        self.releases: dict[str, dict[str, Any]] = {}
        self.attachments: dict[int, list[dict[str, Any]]] = {}
        self.uploaded_bytes: dict[tuple[int, str], bytes] = {}
        self.next_release_id = 100
        self.next_attach_id = 500
        self.fail_once_paths: set[str] = set()
        self.force_status: int | None = None
        self.requests: list[tuple[str, str]] = []


def _make_handler(state: _MockGiteeState):
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def _send_json(self, status: int, data: Any) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def _check_auth(self) -> bool:
            if state.force_status is not None:
                self._send_json(state.force_status, {"message": "forced error"})
                return False
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {state.expected_token}":
                self._send_json(401, {"message": "401 Unauthorized"})
                return False
            return True

        def _read_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length > 0 else b""

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            state.requests.append(("GET", parsed.path))
            if not self._check_auth():
                return

            if parsed.path in state.fail_once_paths:
                state.fail_once_paths.remove(parsed.path)
                self._send_json(502, {"message": "transient upstream error"})
                return

            tag_prefix = "/api/v5/repos/topstar_1/GJBZ299D-Calc/releases/tags/"
            if parsed.path.startswith(tag_prefix):
                tag = urllib.parse.unquote(parsed.path[len(tag_prefix) :])
                rel = state.releases.get(tag)
                if rel is None:
                    if state.tag_lookup_returns_null:
                        self._send_json(200, None)
                    else:
                        self._send_json(404, {"message": "404 Not Found"})
                    return
                self._send_json(200, rel)
                return

            attach_prefix = "/api/v5/repos/topstar_1/GJBZ299D-Calc/releases/"
            if parsed.path.startswith(attach_prefix) and parsed.path.endswith("/attach_files"):
                release_id = int(parsed.path[len(attach_prefix) :].split("/")[0])
                items = state.attachments.get(release_id, [])
                self._send_json(200, items)
                return

            self._send_json(404, {"message": "unknown GET path"})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            state.requests.append(("POST", parsed.path))
            raw = self._read_body()
            if not self._check_auth():
                return

            if parsed.path in state.fail_once_paths:
                state.fail_once_paths.remove(parsed.path)
                self._send_json(503, {"message": "retry upload"})
                return

            if parsed.path == "/api/v5/repos/topstar_1/GJBZ299D-Calc/releases":
                body = json.loads(raw.decode("utf-8"))
                state.next_release_id += 1
                rel = {
                    "id": state.next_release_id,
                    "tag_name": body["tag_name"],
                    "name": body["name"],
                    "body": body["body"],
                    "prerelease": body["prerelease"],
                    "target_commitish": body["target_commitish"],
                }
                state.releases[body["tag_name"]] = rel
                state.attachments[rel["id"]] = []
                self._send_json(201, rel)
                return

            attach_prefix = "/api/v5/repos/topstar_1/GJBZ299D-Calc/releases/"
            if parsed.path.startswith(attach_prefix) and parsed.path.endswith("/attach_files"):
                release_id = int(parsed.path[len(attach_prefix) :].split("/")[0])
                content_type = self.headers.get("Content-Type", "")
                msg_bytes = (
                    f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8")
                    + raw
                )
                msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(msg_bytes)
                uploaded_part = None
                for part in msg.iter_parts():
                    if part.get_param("name", header="content-disposition") == "file":
                        uploaded_part = part
                        break
                assert uploaded_part is not None
                filename = uploaded_part.get_filename() or "upload.bin"
                payload = uploaded_part.get_payload(decode=True) or b""
                state.next_attach_id += 1
                item = {
                    "id": state.next_attach_id,
                    "name": filename,
                    "size": len(payload),
                }
                state.attachments.setdefault(release_id, []).append(item)
                state.uploaded_bytes[(release_id, filename)] = payload
                self._send_json(201, item)
                return

            self._send_json(404, {"message": "unknown POST path"})

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            state.requests.append(("PATCH", parsed.path))
            raw = self._read_body()
            if not self._check_auth():
                return

            prefix = "/api/v5/repos/topstar_1/GJBZ299D-Calc/releases/"
            if parsed.path.startswith(prefix):
                release_id = int(parsed.path[len(prefix) :])
                body = json.loads(raw.decode("utf-8"))
                assert "target_commitish" not in body
                for tag, rel in state.releases.items():
                    if rel["id"] == release_id:
                        rel.update(
                            {
                                "tag_name": body["tag_name"],
                                "name": body["name"],
                                "body": body["body"],
                                "prerelease": body["prerelease"],
                            }
                        )
                        self._send_json(200, rel)
                        return
            self._send_json(404, {"message": "release not found"})

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            state.requests.append(("DELETE", parsed.path))
            if not self._check_auth():
                return

            prefix = "/api/v5/repos/topstar_1/GJBZ299D-Calc/releases/"
            if parsed.path.startswith(prefix) and "/attach_files/" in parsed.path:
                tail = parsed.path[len(prefix) :]
                rel_part, attach_part = tail.split("/attach_files/")
                release_id = int(rel_part)
                attach_id = int(attach_part)
                current = state.attachments.get(release_id, [])
                state.attachments[release_id] = [a for a in current if a["id"] != attach_id]
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            self._send_json(404, {"message": "unknown DELETE path"})

    return _Handler


@pytest.fixture()
def mock_gitee_server():
    state = _MockGiteeState()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    base_url = f"http://{host}:{port}/api/v5"
    try:
        yield base_url, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_load_release_metadata_supports_gh_and_webhook_formats(tmp_path: Path) -> None:
    gh_json = tmp_path / "gh.json"
    gh_json.write_text(
        json.dumps(
            {
                "tagName": "v1.0",
                "name": "",
                "body": "   ",
                "isPrerelease": False,
                "isDraft": False,
                "targetCommitish": "main",
                "assets": [{"name": "a.zip"}, {"name": "b.zip"}],
            }
        ),
        encoding="utf-8",
    )
    meta = sync_mod.load_release_metadata(gh_json)
    assert meta.tag_name == "v1.0"
    assert meta.name == "v1.0"
    assert meta.body == "Release v1.0"
    assert meta.prerelease is False
    assert meta.draft is False
    assert meta.target_commitish == "main"
    assert meta.asset_names == ("a.zip", "b.zip")

    webhook_json = tmp_path / "event.json"
    webhook_json.write_text(
        json.dumps(
            {
                "action": "published",
                "release": {
                    "tag_name": "v2.0-rc1",
                    "name": "RC 1",
                    "body": "Notes",
                    "prerelease": True,
                    "draft": False,
                    "target_commitish": "release/2.0",
                },
            }
        ),
        encoding="utf-8",
    )
    meta2 = sync_mod.load_release_metadata(webhook_json)
    assert meta2.tag_name == "v2.0-rc1"
    assert meta2.name == "RC 1"
    assert meta2.body == "Notes"
    assert meta2.prerelease is True
    assert meta2.target_commitish == "release/2.0"


def test_collect_assets_orders_and_detects_duplicates(tmp_path: Path) -> None:
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    (d1 / ".hidden").write_bytes(b"x")
    (d1 / "z.zip").write_bytes(b"z")
    (d1 / "a.zip").write_bytes(b"a")

    ordered = sync_mod.collect_assets(
        assets_dir=d1,
        preferred_order=("z.zip",),
    )
    assert [p.name for p in ordered] == ["z.zip", "a.zip"]

    (d2 / "a.zip").write_bytes(b"dup")
    with pytest.raises(sync_mod.GiteeSyncError, match="Duplicate asset filename"):
        sync_mod.collect_assets(assets_dir=d1, asset_paths=[d2 / "a.zip"])


def test_sync_release_create_stream_and_idempotent_replace(
    tmp_path: Path, mock_gitee_server
) -> None:
    base_url, state = mock_gitee_server
    # Force one transient failure on release tag lookup and one on attachment upload.
    state.fail_once_paths.add("/api/v5/repos/topstar_1/GJBZ299D-Calc/releases/tags/v1.0")
    state.fail_once_paths.add("/api/v5/repos/topstar_1/GJBZ299D-Calc/releases/101/attach_files")

    # Large payload (> UPLOAD_CHUNK_SIZE=1MiB) exercises multi-chunk streaming.
    linux_zip = tmp_path / "GJB299D-Calc-Linux-x86_64.zip"
    win_zip = tmp_path / "GJB299D-Calc-Windows-x86_64.zip"
    linux_payload = b"PK\x03\x04" + (b"L" * (1200 * 1024))
    win_payload = b"PK\x03\x04" + (b"W" * 4096)
    linux_zip.write_bytes(linux_payload)
    win_zip.write_bytes(win_payload)

    meta = sync_mod.ReleaseMetadata(
        tag_name="v1.0",
        name="GJB/Z 299D v1.0",
        body="Initial release\n| 文件 | SHA256 |\n",
        prerelease=False,
        target_commitish="main",
        asset_names=("GJB299D-Calc-Linux-x86_64.zip", "GJB299D-Calc-Windows-x86_64.zip"),
    )
    client = sync_mod.GiteeReleaseClient(
        state.expected_token,
        api_base=base_url,
        retries=3,
        sleep_fn=lambda _: None,
    )

    first = client.sync_release(
        "topstar_1", "GJBZ299D-Calc", meta, [linux_zip, win_zip], replace_assets=True
    )
    assert first.created is True
    assert first.release_id == 101
    assert first.uploaded_assets == (linux_zip.name, win_zip.name)
    assert first.deleted_assets == ()
    assert state.uploaded_bytes[(101, linux_zip.name)] == linux_payload
    assert state.uploaded_bytes[(101, win_zip.name)] == win_payload

    # Re-sync with replace_assets=False should skip already present assets.
    updated_meta = sync_mod.ReleaseMetadata(
        tag_name="v1.0",
        name="GJB/Z 299D v1.0",
        body="Updated notes",
        prerelease=False,
        target_commitish="main",
    )
    second = client.sync_release(
        "topstar_1", "GJBZ299D-Calc", updated_meta, [linux_zip, win_zip], replace_assets=False
    )
    assert second.created is False
    assert second.skipped_assets == (linux_zip.name, win_zip.name)
    assert second.uploaded_assets == ()
    assert state.releases["v1.0"]["body"] == "Updated notes"

    # Replace existing assets cleanly (DELETE old + POST new).
    win_zip.write_bytes(b"PK-new-win")
    third = client.sync_release(
        "topstar_1", "GJBZ299D-Calc", updated_meta, [win_zip], replace_assets=True
    )
    assert third.created is False
    assert third.deleted_assets == (win_zip.name,)
    assert third.uploaded_assets == (win_zip.name,)
    assert state.uploaded_bytes[(101, win_zip.name)] == b"PK-new-win"


def test_sync_release_rejects_draft_and_redacts_token_on_errors(
    tmp_path: Path, mock_gitee_server
) -> None:
    base_url, state = mock_gitee_server
    client = sync_mod.GiteeReleaseClient(
        "wrong-secret-token-xyz",
        api_base=base_url,
        retries=2,
        sleep_fn=lambda _: None,
    )

    draft_meta = sync_mod.ReleaseMetadata(
        tag_name="v1.0", name="v1.0", body="body", draft=True
    )
    with pytest.raises(sync_mod.GiteeSyncError, match="draft"):
        client.sync_release("topstar_1", "GJBZ299D-Calc", draft_meta, [])

    state.force_status = 403
    pub_meta = sync_mod.ReleaseMetadata(tag_name="v1.0", name="v1.0", body="body")
    with pytest.raises(sync_mod.GiteeSyncError) as exc_info:
        client.sync_release("topstar_1", "GJBZ299D-Calc", pub_meta, [])
    assert exc_info.value.status_code == 403
    assert "wrong-secret-token-xyz" not in str(exc_info.value)
    assert "projects" in str(exc_info.value)


def test_skip_assets_updates_release_text_without_deleting_attachments(
    tmp_path: Path, mock_gitee_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    base_url, state = mock_gitee_server
    asset = tmp_path / "pkg.zip"
    asset.write_bytes(b"PK-zip-data")

    client = sync_mod.GiteeReleaseClient(
        state.expected_token, api_base=base_url, sleep_fn=lambda _: None
    )
    meta = sync_mod.ReleaseMetadata(tag_name="v1.0", name="v1.0", body="Initial notes")
    res = client.sync_release("topstar_1", "GJBZ299D-Calc", meta, [asset])
    assert res.release_id == 101
    assert len(state.attachments[101]) == 1

    meta_file = tmp_path / "meta.json"
    meta_file.write_text(
        json.dumps({"tagName": "v1.0", "name": "v1.0", "body": "Edited notes"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITEE_TOKEN", state.expected_token)
    rc = sync_mod.main(
        [
            "--owner",
            "topstar_1",
            "--repo",
            "GJBZ299D-Calc",
            "--metadata-json",
            str(meta_file),
            "--assets-dir",
            str(tmp_path),
            "--skip-assets",
            "--api-base",
            base_url,
        ]
    )
    assert rc == 0
    assert state.releases["v1.0"]["body"] == "Edited notes"
    assert len(state.attachments[101]) == 1
    assert not any(method == "DELETE" for method, _ in state.requests)


def test_workflow_file_structure_and_enterprise_defaults() -> None:
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "\t" not in text
    assert "topstar_1" in text
    assert "GJBZ299D-Calc" in text
    assert "GITEE_TOKEN" in text
    assert "GITEE_USERNAME" in text
    assert "GITEE_SSH_PRIVATE_KEY" in text
    assert "scripts/sync_gitee_release.py" in text
    assert "workflow_dispatch:" in text
    assert "release:" in text
    assert "github.event.release.draft != true" in text
    assert "sync_assets:" in text
    assert "--skip-assets" in text
