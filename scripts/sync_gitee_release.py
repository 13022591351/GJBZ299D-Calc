#!/usr/bin/env python3
"""Sync a GitHub Release and its attached binary assets to Gitee OpenAPI v5."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

DEFAULT_API_BASE = "https://gitee.com/api/v5"
DEFAULT_TOKEN_ENV = "GITEE_TOKEN"
DEFAULT_TIMEOUT_SEC = 30.0
DEFAULT_UPLOAD_TIMEOUT_SEC = 300.0
DEFAULT_RETRIES = 3
DEFAULT_BACKOFF_SEC = 2.0
UPLOAD_CHUNK_SIZE = 1024 * 1024
GITEE_MAX_ATTACHMENT_BYTES = 100 * 1024 * 1024
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
USER_AGENT = "GJBZ299D-Calc-Gitee-Sync/1.0"


class GiteeSyncError(RuntimeError):
    """Error raised when communicating with or validating Gitee OpenAPI v5."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ReleaseMetadata:
    """Normalized release metadata loaded from CLI flags or GitHub JSON."""

    tag_name: str
    name: str
    body: str
    prerelease: bool = False
    target_commitish: str = "main"
    draft: bool = False
    asset_names: tuple[str, ...] = ()


@dataclass(frozen=True)
class SyncResult:
    """Summary of a completed or dry-run Gitee release sync."""

    release_id: int | None
    tag_name: str
    created: bool
    uploaded_assets: tuple[str, ...]
    deleted_assets: tuple[str, ...]
    skipped_assets: tuple[str, ...]


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _sanitize_header_filename(filename: str) -> str:
    """Strip directory components and characters unsafe in Content-Disposition."""
    base = os.path.basename(filename)
    return base.replace('"', "_").replace("\r", "_").replace("\n", "_")


def _extract_error_message(raw_body: str) -> str:
    if not raw_body.strip():
        return ""
    try:
        parsed = json.loads(raw_body)
    except ValueError:
        return raw_body.strip()[:400]
    if isinstance(parsed, dict):
        for key in ("message", "error", "error_description"):
            val = parsed.get(key)
            if val:
                return str(val)
    return raw_body.strip()[:400]


def _status_hint(status_code: int) -> str:
    if status_code == 401:
        return (
            "Check GITEE_TOKEN validity/expiration and confirm Gitee Enterprise policy "
            "allows member Personal Access Tokens."
        )
    if status_code == 403:
        return (
            "Confirm the token owner has write permission in the Gitee Enterprise repository "
            "and the token includes the 'projects' scope."
        )
    if status_code == 404:
        return (
            "Confirm --owner is the Gitee Enterprise namespace path (not personal login), "
            "the repo slug matches, and the branch/tag was pushed before syncing the release."
        )
    return ""


def load_release_metadata(path: Path) -> ReleaseMetadata:
    """Load ReleaseMetadata from `gh release view --json` or a GitHub event payload."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise GiteeSyncError(f"Failed to read metadata JSON {path}: {exc}") from exc
    except ValueError as exc:
        raise GiteeSyncError(f"Invalid metadata JSON in {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise GiteeSyncError(f"Metadata JSON in {path} must be an object.")

    payload = raw["release"] if isinstance(raw.get("release"), dict) else raw

    tag_name = str(payload.get("tagName") or payload.get("tag_name") or "").strip()
    if not tag_name:
        raise GiteeSyncError(f"Release tag is missing in metadata JSON {path}.")

    raw_name = str(payload.get("name") or "").strip()
    name = raw_name or tag_name

    raw_body = str(payload.get("body") or "")
    body = raw_body if raw_body.strip() else f"Release {tag_name}"

    if "isPrerelease" in payload:
        prerelease = _as_bool(payload.get("isPrerelease"))
    else:
        prerelease = _as_bool(payload.get("prerelease"))

    if "isDraft" in payload:
        draft = _as_bool(payload.get("isDraft"))
    else:
        draft = _as_bool(payload.get("draft"))

    target_commitish = str(
        payload.get("targetCommitish") or payload.get("target_commitish") or ""
    ).strip() or "main"

    raw_assets = payload.get("assets") or []
    asset_names: list[str] = []
    if isinstance(raw_assets, list):
        for item in raw_assets:
            if isinstance(item, dict) and item.get("name"):
                asset_names.append(str(item["name"]))

    return ReleaseMetadata(
        tag_name=tag_name,
        name=name,
        body=body,
        prerelease=prerelease,
        target_commitish=target_commitish,
        draft=draft,
        asset_names=tuple(asset_names),
    )


def collect_assets(
    *,
    assets_dir: Path | None = None,
    asset_paths: list[Path] | None = None,
    preferred_order: tuple[str, ...] = (),
) -> list[Path]:
    """Collect and deterministically order release asset files."""
    candidates: list[Path] = []
    seen_resolved: set[Path] = set()
    by_name: dict[str, Path] = {}

    def _add(file_path: Path) -> None:
        resolved = file_path.resolve()
        if resolved in seen_resolved:
            return
        if file_path.name in by_name:
            raise GiteeSyncError(
                f"Duplicate asset filename {file_path.name!r}: {by_name[file_path.name]} and {file_path}"
            )
        seen_resolved.add(resolved)
        by_name[file_path.name] = file_path
        candidates.append(file_path)

    if assets_dir is not None:
        if not assets_dir.exists() or not assets_dir.is_dir():
            raise GiteeSyncError(f"Assets directory does not exist: {assets_dir}")
        for entry in sorted(assets_dir.iterdir(), key=lambda p: p.name):
            if entry.is_file() and not entry.name.startswith("."):
                _add(entry)

    for path in asset_paths or []:
        if not path.exists() or not path.is_file():
            raise GiteeSyncError(f"Asset file does not exist: {path}")
        _add(path)

    rank = {name: idx for idx, name in enumerate(preferred_order)}
    candidates.sort(key=lambda p: (rank.get(p.name, len(rank)), p.name))
    return candidates


class GiteeReleaseClient:
    """Minimal stdlib client for Gitee OpenAPI v5 Releases and attachments."""

    def __init__(
        self,
        token: str,
        *,
        api_base: str = DEFAULT_API_BASE,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        upload_timeout: float = DEFAULT_UPLOAD_TIMEOUT_SEC,
        retries: int = DEFAULT_RETRIES,
        backoff: float = DEFAULT_BACKOFF_SEC,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        clean_token = token.strip()
        if not clean_token:
            raise GiteeSyncError("GITEE_TOKEN is empty.")
        self.token = clean_token
        self.api_base = api_base.rstrip("/")
        self._parsed_base = urllib.parse.urlparse(self.api_base)
        if self._parsed_base.scheme not in {"http", "https"} or not self._parsed_base.netloc:
            raise GiteeSyncError(f"Invalid Gitee API base URL: {api_base}")
        self.timeout = timeout
        self.upload_timeout = upload_timeout
        self.retries = max(1, retries)
        self.backoff = max(0.0, backoff)
        self.sleep_fn = sleep_fn

    def _redact(self, text: str) -> str:
        return text.replace(self.token, "***")

    def _build_url(self, rel_path: str, query: dict[str, Any] | None = None) -> str:
        base_path = self._parsed_base.path.rstrip("/")
        path = f"{base_path}/{rel_path.lstrip('/')}"
        qs = f"?{urllib.parse.urlencode(query)}" if query else ""
        return urllib.parse.urlunparse(
            (self._parsed_base.scheme, self._parsed_base.netloc, path, "", qs.lstrip("?"), "")
        )

    def _headers(self, *, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
            "User-Agent": USER_AGENT,
        }
        if extra:
            headers.update(extra)
        return headers

    def _format_http_error(self, status_code: int, reason: str, raw_body: str, context: str) -> str:
        detail = _extract_error_message(raw_body)
        hint = _status_hint(status_code)
        parts = [f"{context}: HTTP {status_code} {reason}".strip()]
        if detail:
            parts.append(f"detail={detail}")
        if hint:
            parts.append(f"hint={hint}")
        return self._redact(" | ".join(parts))

    def _parse_json_bytes(self, raw: bytes, context: str) -> Any:
        if not raw or not raw.strip():
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise GiteeSyncError(
                self._redact(f"{context}: failed to decode JSON response: {exc}")
            ) from exc

    def _request_json(
        self,
        method: str,
        rel_path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        context: str,
        allow_404: bool = False,
    ) -> Any:
        url = self._build_url(rel_path, query=query)
        payload: bytes | None = None
        extra_headers: dict[str, str] | None = None
        if json_body is not None:
            payload = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            extra_headers = {"Content-Type": "application/json"}

        for attempt in range(self.retries):
            req = urllib.request.Request(
                url,
                data=payload,
                headers=self._headers(extra=extra_headers),
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return self._parse_json_bytes(resp.read(), context)
            except urllib.error.HTTPError as exc:
                raw_err = exc.read().decode("utf-8", errors="replace")
                if allow_404 and exc.code == 404:
                    return None
                if exc.code in RETRYABLE_STATUS_CODES and attempt + 1 < self.retries:
                    self.sleep_fn(self.backoff * (2**attempt))
                    continue
                raise GiteeSyncError(
                    self._format_http_error(exc.code, str(exc.reason), raw_err, context),
                    status_code=exc.code,
                ) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError, http.client.HTTPException) as exc:
                if attempt + 1 < self.retries:
                    self.sleep_fn(self.backoff * (2**attempt))
                    continue
                raise GiteeSyncError(
                    self._redact(f"{context}: request failed after {self.retries} attempts: {exc}")
                ) from exc

        raise GiteeSyncError(f"{context}: exhausted retries.")

    def _open_connection(self, timeout: float) -> http.client.HTTPConnection:
        host = self._parsed_base.hostname
        if not host:
            raise GiteeSyncError(f"Missing hostname in API base URL: {self.api_base}")
        port = self._parsed_base.port
        if self._parsed_base.scheme == "https":
            return http.client.HTTPSConnection(host, port=port, timeout=timeout)
        return http.client.HTTPConnection(host, port=port, timeout=timeout)

    def _repo_prefix(self, owner: str, repo: str) -> str:
        owner_q = urllib.parse.quote(owner, safe="")
        repo_q = urllib.parse.quote(repo, safe="")
        return f"repos/{owner_q}/{repo_q}"

    def find_release_by_tag(self, owner: str, repo: str, tag: str) -> dict[str, Any] | None:
        """Fetch an existing Gitee release by tag, returning None if absent.

        Note: Gitee OpenAPI v5 returns either HTTP 404 or HTTP 200 with JSON `null`
        when a tag does not yet have an associated release.
        """
        tag_q = urllib.parse.quote(tag, safe="")
        rel_path = f"{self._repo_prefix(owner, repo)}/releases/tags/{tag_q}"
        data = self._request_json(
            "GET",
            rel_path,
            context=f"Fetch Gitee release tag {tag}",
            allow_404=True,
        )
        if not isinstance(data, dict) or data.get("id") is None:
            return None
        return data

    def upsert_release(
        self, owner: str, repo: str, meta: ReleaseMetadata
    ) -> tuple[dict[str, Any], bool]:
        """Create or update a Gitee release idempotently."""
        if meta.draft:
            raise GiteeSyncError(
                f"Refusing to sync draft release {meta.tag_name!r} to Gitee."
            )

        prefix = self._repo_prefix(owner, repo)
        existing = self.find_release_by_tag(owner, repo, meta.tag_name)
        if existing is None:
            payload = {
                "tag_name": meta.tag_name,
                "name": meta.name,
                "body": meta.body,
                "prerelease": meta.prerelease,
                "target_commitish": meta.target_commitish,
            }
            created = self._request_json(
                "POST",
                f"{prefix}/releases",
                json_body=payload,
                context=f"Create Gitee release {meta.tag_name}",
            )
            if not isinstance(created, dict) or created.get("id") is None:
                raise GiteeSyncError(
                    f"Create Gitee release {meta.tag_name}: unexpected response {created!r}"
                )
            return created, True

        release_id = int(existing["id"])
        payload = {
            "tag_name": meta.tag_name,
            "name": meta.name,
            "body": meta.body,
            "prerelease": meta.prerelease,
        }
        updated = self._request_json(
            "PATCH",
            f"{prefix}/releases/{release_id}",
            json_body=payload,
            context=f"Update Gitee release {meta.tag_name} (id={release_id})",
        )
        if not isinstance(updated, dict) or updated.get("id") is None:
            raise GiteeSyncError(
                f"Update Gitee release {meta.tag_name}: unexpected response {updated!r}"
            )
        return updated, False

    def list_attachments(self, owner: str, repo: str, release_id: int) -> list[dict[str, Any]]:
        """List all attachments on a Gitee release across pages."""
        prefix = self._repo_prefix(owner, repo)
        items: list[dict[str, Any]] = []
        page = 1
        per_page = 100
        while True:
            batch = self._request_json(
                "GET",
                f"{prefix}/releases/{release_id}/attach_files",
                query={"page": page, "per_page": per_page},
                context=f"List attachments for release id={release_id}",
            )
            if not isinstance(batch, list) or not batch:
                break
            dict_items = [item for item in batch if isinstance(item, dict)]
            items.extend(dict_items)
            if len(batch) < per_page:
                break
            page += 1
        return items

    def delete_attachment(
        self, owner: str, repo: str, release_id: int, attach_id: int
    ) -> None:
        """Delete an attachment from a Gitee release."""
        prefix = self._repo_prefix(owner, repo)
        self._request_json(
            "DELETE",
            f"{prefix}/releases/{release_id}/attach_files/{attach_id}",
            context=f"Delete attachment id={attach_id} on release id={release_id}",
        )

    def upload_attachment(
        self, owner: str, repo: str, release_id: int, file_path: Path
    ) -> dict[str, Any]:
        """Stream a file to Gitee OpenAPI v5 multipart attach_files endpoint."""
        if not file_path.exists() or not file_path.is_file():
            raise GiteeSyncError(f"Asset file does not exist: {file_path}")
        file_size = file_path.stat().st_size
        if file_size > GITEE_MAX_ATTACHMENT_BYTES:
            print(
                f"Warning: {file_path.name} is {file_size / (1024 * 1024):.1f} MiB, "
                "which exceeds Gitee's documented 100 MiB attachment limit.",
                file=sys.stderr,
            )

        prefix = self._repo_prefix(owner, repo)
        base_path = self._parsed_base.path.rstrip("/")
        request_target = f"{base_path}/{prefix}/releases/{release_id}/attach_files"
        safe_name = _sanitize_header_filename(file_path.name)
        context = f"Upload attachment {file_path.name} to release id={release_id}"

        for attempt in range(self.retries):
            boundary = f"----GiteeSyncBoundary{uuid.uuid4().hex}"
            preamble = (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="file"; filename="{safe_name}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n"
            ).encode("utf-8")
            epilogue = f"\r\n--{boundary}--\r\n".encode("utf-8")
            content_length = len(preamble) + file_size + len(epilogue)

            conn = self._open_connection(self.upload_timeout)
            try:
                conn.putrequest("POST", request_target)
                conn.putheader("Authorization", f"Bearer {self.token}")
                conn.putheader("Accept", "application/json")
                conn.putheader("User-Agent", USER_AGENT)
                conn.putheader("Content-Type", f"multipart/form-data; boundary={boundary}")
                conn.putheader("Content-Length", str(content_length))
                conn.endheaders()

                conn.send(preamble)
                with file_path.open("rb") as fh:
                    while True:
                        chunk = fh.read(UPLOAD_CHUNK_SIZE)
                        if not chunk:
                            break
                        conn.send(chunk)
                conn.send(epilogue)

                resp = conn.getresponse()
                raw_resp = resp.read()
                if 200 <= resp.status < 300:
                    parsed = self._parse_json_bytes(raw_resp, context)
                    if not isinstance(parsed, dict):
                        raise GiteeSyncError(f"{context}: unexpected response {parsed!r}")
                    return parsed

                raw_text = raw_resp.decode("utf-8", errors="replace")
                if resp.status in RETRYABLE_STATUS_CODES and attempt + 1 < self.retries:
                    self.sleep_fn(self.backoff * (2**attempt))
                    continue
                raise GiteeSyncError(
                    self._format_http_error(resp.status, resp.reason, raw_text, context),
                    status_code=resp.status,
                )
            except (TimeoutError, ConnectionError, OSError, http.client.HTTPException) as exc:
                if attempt + 1 < self.retries:
                    self.sleep_fn(self.backoff * (2**attempt))
                    continue
                raise GiteeSyncError(
                    self._redact(f"{context}: upload failed after {self.retries} attempts: {exc}")
                ) from exc
            finally:
                conn.close()

        raise GiteeSyncError(f"{context}: exhausted retries.")

    def sync_release(
        self,
        owner: str,
        repo: str,
        meta: ReleaseMetadata,
        assets: list[Path],
        *,
        replace_assets: bool = True,
        dry_run: bool = False,
    ) -> SyncResult:
        """Create/update the Gitee release and upload or replace its attachments."""
        if meta.draft:
            raise GiteeSyncError(
                f"Refusing to sync draft release {meta.tag_name!r} to Gitee."
            )

        if dry_run:
            return SyncResult(
                release_id=None,
                tag_name=meta.tag_name,
                created=False,
                uploaded_assets=tuple(p.name for p in assets),
                deleted_assets=(),
                skipped_assets=(),
            )

        release_obj, created = self.upsert_release(owner, repo, meta)
        release_id = int(release_obj["id"])
        existing_attachments = self.list_attachments(owner, repo, release_id) if assets else []

        deleted: list[str] = []
        uploaded: list[str] = []
        skipped: list[str] = []

        for asset in assets:
            matches = [a for a in existing_attachments if a.get("name") == asset.name]
            if matches and not replace_assets:
                skipped.append(asset.name)
                continue
            for item in matches:
                attach_id = item.get("id")
                if attach_id is not None:
                    self.delete_attachment(owner, repo, release_id, int(attach_id))
                    deleted.append(asset.name)
            self.upload_attachment(owner, repo, release_id, asset)
            uploaded.append(asset.name)

        return SyncResult(
            release_id=release_id,
            tag_name=meta.tag_name,
            created=created,
            uploaded_assets=tuple(uploaded),
            deleted_assets=tuple(deleted),
            skipped_assets=tuple(skipped),
        )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True, help="Gitee namespace/enterprise slug (e.g. topstar_1)")
    parser.add_argument("--repo", required=True, help="Gitee repository slug (e.g. GJBZ299D-Calc)")
    parser.add_argument(
        "--metadata-json",
        type=Path,
        default=None,
        help="Path to `gh release view --json ...` output or GitHub release webhook JSON",
    )
    parser.add_argument("--tag", default="", help="Release tag override (if --metadata-json is omitted)")
    parser.add_argument("--name", default="", help="Release title override")
    parser.add_argument("--body", default="", help="Release body override")
    parser.add_argument("--body-file", type=Path, default=None, help="Read release body from UTF-8 file")
    parser.add_argument("--target-commitish", default="main", help="Target branch or commit SHA")
    parser.add_argument("--prerelease", action="store_true", help="Mark release as prerelease")
    parser.add_argument("--assets-dir", type=Path, default=None, help="Directory of downloaded release assets")
    parser.add_argument(
        "--asset",
        type=Path,
        action="append",
        default=[],
        help="Additional asset file to upload (repeatable)",
    )
    parser.add_argument(
        "--keep-existing-assets",
        action="store_true",
        help="Skip uploading assets whose filenames already exist on the Gitee release",
    )
    parser.add_argument(
        "--skip-assets",
        action="store_true",
        help="Sync release metadata/body text only without uploading or deleting attachments",
    )
    parser.add_argument("--api-base", default=DEFAULT_API_BASE, help=f"Default: {DEFAULT_API_BASE}")
    parser.add_argument("--token-env", default=DEFAULT_TOKEN_ENV, help=f"Default: {DEFAULT_TOKEN_ENV}")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SEC)
    parser.add_argument("--upload-timeout", type=float, default=DEFAULT_UPLOAD_TIMEOUT_SEC)
    parser.add_argument("--retries", type=int, default=DEFAULT_RETRIES)
    parser.add_argument("--dry-run", action="store_true", help="Validate metadata/files without mutating Gitee")
    return parser.parse_args(argv)


def _resolve_metadata(args: argparse.Namespace) -> ReleaseMetadata:
    if args.metadata_json is not None:
        meta = load_release_metadata(args.metadata_json)
    else:
        tag = args.tag.strip()
        if not tag:
            raise GiteeSyncError("Either --metadata-json or --tag must be provided.")
        body = args.body
        if args.body_file is not None:
            body = args.body_file.read_text(encoding="utf-8")
        meta = ReleaseMetadata(
            tag_name=tag,
            name=args.name.strip() or tag,
            body=body if body.strip() else f"Release {tag}",
            prerelease=bool(args.prerelease),
            target_commitish=args.target_commitish.strip() or "main",
        )
    return meta


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        meta = _resolve_metadata(args)
        assets = (
            []
            if args.skip_assets
            else collect_assets(
                assets_dir=args.assets_dir,
                asset_paths=args.asset,
                preferred_order=meta.asset_names,
            )
        )
        token = os.environ.get(args.token_env, "")
        if not token.strip() and args.dry_run:
            token = "dry-run-placeholder"
        client = GiteeReleaseClient(
            token,
            api_base=args.api_base,
            timeout=args.timeout,
            upload_timeout=args.upload_timeout,
            retries=args.retries,
        )
        result = client.sync_release(
            args.owner,
            args.repo,
            meta,
            assets,
            replace_assets=not args.keep_existing_assets,
            dry_run=args.dry_run,
        )
    except GiteeSyncError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    action = "DRY-RUN" if args.dry_run else ("CREATED" if result.created else "UPDATED")
    print(
        f"[{action}] {args.owner}/{args.repo} release tag={result.tag_name} "
        f"(id={result.release_id}) | uploaded={len(result.uploaded_assets)} "
        f"replaced={len(result.deleted_assets)} skipped={len(result.skipped_assets)}"
    )
    for filename in result.uploaded_assets:
        print(f"  + uploaded: {filename}")
    for filename in result.skipped_assets:
        print(f"  = skipped (already present): {filename}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
