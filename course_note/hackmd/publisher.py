from __future__ import annotations

import json
import mimetypes
import os
import re
import socket
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from course_note.utils.env import load_env_file

DEFAULT_HACKMD_API_BASE = "https://api.hackmd.io/v1"
SCREENSHOT_LINE_RE = re.compile(r"^(?P<prefix>\s*簡報[：:]\s*)(?P<image>.+?)\s*$")
MARKDOWN_TITLE_RE = re.compile(r"^#\s+(?P<title>.+?)\s*$")
TRANSIENT_STATUS_CODES = frozenset({408, 425, 429, 500, 502, 503, 504})
DEFAULT_UPDATE_RETRY_ATTEMPTS = 6
DEFAULT_UPDATE_RETRY_DELAY_SECONDS = 2.0
DEFAULT_UPLOAD_RETRY_ATTEMPTS = 8
DEFAULT_UPLOAD_RETRY_DELAY_SECONDS = 5.0
DEFAULT_UPLOAD_SPACING_SECONDS = 1.0
DEFAULT_POST_UPLOAD_SETTLE_SECONDS = 10.0
UNIX_TIMESTAMP_MILLISECONDS_THRESHOLD = 100_000_000_000
MAX_RETRY_SLEEP_SECONDS = 3600.0


@dataclass(slots=True)
class HackMDNote:
    note_id: str
    short_id: str | None = None
    publish_link: str | None = None
    permalink: str | None = None


@dataclass(slots=True)
class HackMDPublishResult:
    note_id: str
    title: str
    publish_link: str | None
    permalink: str | None
    image_count: int


class HackMDRequestError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retryable: bool = False,
        retry_after_seconds: float | None = None,
        rate_limit_remaining: int | None = None,
        rate_limit_reset_at: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset_at = rate_limit_reset_at


class HackMDClient:
    def __init__(
        self,
        api_base: str | None = None,
        api_token: str | None = None,
        *,
        update_retry_attempts: int = DEFAULT_UPDATE_RETRY_ATTEMPTS,
        update_retry_delay_seconds: float = DEFAULT_UPDATE_RETRY_DELAY_SECONDS,
        upload_retry_attempts: int = DEFAULT_UPLOAD_RETRY_ATTEMPTS,
        upload_retry_delay_seconds: float = DEFAULT_UPLOAD_RETRY_DELAY_SECONDS,
        upload_spacing_seconds: float = DEFAULT_UPLOAD_SPACING_SECONDS,
    ) -> None:
        load_env_file()
        self.api_base = (api_base or os.getenv("HACKMD_API_BASE") or DEFAULT_HACKMD_API_BASE).rstrip("/")
        self.api_token = (api_token or os.getenv("HACKMD_API") or os.getenv("HACKMD_API_KEY") or "").strip()
        self.update_retry_attempts = max(update_retry_attempts, 1)
        self.update_retry_delay_seconds = max(update_retry_delay_seconds, 0.0)
        self.upload_retry_attempts = max(upload_retry_attempts, 1)
        self.upload_retry_delay_seconds = max(upload_retry_delay_seconds, 0.0)
        self.upload_spacing_seconds = max(upload_spacing_seconds, 0.0)
        self._rate_limit_limit: int | None = None
        self._rate_limit_remaining: int | None = None
        self._rate_limit_reset_at: float | None = None
        if not self.api_token:
            raise RuntimeError("HackMD API token is missing. Set HACKMD_API in your environment or .env file.")

    def create_note(
        self,
        title: str,
        content: str,
        *,
        read_permission: str | None = None,
        write_permission: str | None = None,
    ) -> HackMDNote:
        payload: dict[str, Any] = {"title": title, "content": content}
        if read_permission is not None and write_permission is not None:
            payload["readPermission"] = read_permission
            payload["writePermission"] = write_permission
        body = self._request_json("POST", "/notes", payload=payload, expected_statuses=(200, 201))
        note_id = self._extract_text(body, "id", "noteId")
        if not note_id:
            raise RuntimeError(f"HackMD create note response did not include a note id: {body!r}")
        return HackMDNote(
            note_id=note_id,
            short_id=self._extract_text(body, "shortId"),
            publish_link=self._extract_text(body, "publishLink"),
            permalink=self._extract_text(body, "permalink"),
        )

    def update_note(self, note: HackMDNote, *, content: str) -> None:
        payload: dict[str, Any] = {"content": content}
        note_ids = [note.note_id]
        if note.short_id and note.short_id not in note_ids:
            note_ids.append(note.short_id)

        last_error: RuntimeError | None = None
        for note_id in note_ids:
            for attempt in range(self.update_retry_attempts):
                try:
                    self._request_json("PATCH", f"/notes/{note_id}", payload=payload, expected_statuses=(200, 202))
                    return
                except HackMDRequestError as exc:
                    last_error = exc
                    if not self._should_retry(exc, attempt, self.update_retry_attempts):
                        break
                    self._sleep_before_retry(self._retry_delay_seconds(exc, attempt, self.update_retry_delay_seconds))
                except RuntimeError as exc:
                    last_error = exc
                    break

        if last_error is not None:
            raise last_error

    def upload_image(self, note_id: str, image_path: Path) -> str:
        content_type, _ = mimetypes.guess_type(image_path.name)
        boundary = f"----CourseNoteBoundary{uuid.uuid4().hex}"
        image_bytes = image_path.read_bytes()
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                (
                    f'Content-Disposition: form-data; name="image"; '
                    f'filename="{image_path.name}"\r\n'
                ).encode("utf-8"),
                f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n".encode("utf-8"),
                image_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )

        last_error: RuntimeError | None = None
        for attempt in range(self.upload_retry_attempts):
            try:
                response = self._request_json(
                    "POST",
                    f"/notes/{note_id}/images",
                    payload=body,
                    content_type=f"multipart/form-data; boundary={boundary}",
                    expected_statuses=(200, 201),
                )
                link = self._extract_text(response, "link")
                if not link:
                    raise RuntimeError(f"HackMD image upload response did not include a link: {response!r}")
                link = self._normalize_upload_link(link)
                if self.upload_spacing_seconds > 0:
                    self._sleep_before_retry(self.upload_spacing_seconds)
                return link
            except HackMDRequestError as exc:
                last_error = exc
                if not self._should_retry(exc, attempt, self.upload_retry_attempts):
                    break
                self._sleep_before_retry(self._retry_delay_seconds(exc, attempt, self.upload_retry_delay_seconds))

        if last_error is not None:
            raise last_error
        raise RuntimeError("HackMD image upload failed before a response was received.")

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | bytes | None = None,
        content_type: str = "application/json",
        expected_statuses: tuple[int, ...] = (200,),
    ) -> Any:
        data: bytes | None
        if isinstance(payload, dict):
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        else:
            data = payload

        self._wait_for_rate_limit_window()

        url = f"{self.api_base}{path}"
        headers = {"Content-Type": content_type}
        last_error: HackMDRequestError | None = None

        for authorization in self._authorization_candidates():
            request = urllib.request.Request(
                url=url,
                data=data,
                headers={**headers, "Authorization": authorization},
                method=method,
            )
            try:
                with urllib.request.urlopen(request, timeout=120) as response:
                    status = response.getcode()
                    self._update_rate_limit_state(response.headers)
                    response_body = response.read().decode("utf-8", errors="ignore")
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")
                self._update_rate_limit_state(exc.headers)
                if exc.code == 401 and authorization != self._authorization_candidates()[-1]:
                    continue
                raise HackMDRequestError(
                    f"HackMD request failed with status {exc.code}: {detail or exc.reason}",
                    status_code=exc.code,
                    retryable=exc.code in TRANSIENT_STATUS_CODES,
                    retry_after_seconds=self._extract_retry_after_seconds(exc.headers.get("Retry-After") if exc.headers else None),
                    rate_limit_remaining=self._rate_limit_remaining,
                    rate_limit_reset_at=self._rate_limit_reset_at,
                ) from exc
            except (TimeoutError, socket.timeout) as exc:
                raise HackMDRequestError(
                    f"HackMD request to {url} timed out after 120 seconds.",
                    retryable=True,
                    rate_limit_remaining=self._rate_limit_remaining,
                    rate_limit_reset_at=self._rate_limit_reset_at,
                ) from exc
            except urllib.error.URLError as exc:
                raise HackMDRequestError(
                    f"Failed to reach HackMD at {url}: {exc.reason}",
                    retryable=True,
                    rate_limit_remaining=self._rate_limit_remaining,
                    rate_limit_reset_at=self._rate_limit_reset_at,
                ) from exc

            if status not in expected_statuses:
                last_error = HackMDRequestError(
                    f"HackMD request returned unexpected status {status}: {response_body}",
                    status_code=status,
                    retryable=status in TRANSIENT_STATUS_CODES,
                    rate_limit_remaining=self._rate_limit_remaining,
                    rate_limit_reset_at=self._rate_limit_reset_at,
                )
                continue

            if not response_body.strip():
                return None
            try:
                return json.loads(response_body)
            except json.JSONDecodeError:
                return response_body

        if last_error is not None:
            raise last_error
        raise RuntimeError("HackMD request failed before a response was received.")

    def _should_retry(self, error: HackMDRequestError, attempt: int, total_attempts: int) -> bool:
        return error.retryable and attempt + 1 < total_attempts

    def _retry_delay_seconds(self, error: HackMDRequestError, attempt: int, base_delay: float) -> float:
        if error.retry_after_seconds is not None:
            return max(error.retry_after_seconds, base_delay)

        if error.rate_limit_reset_at is not None and error.status_code == 429:
            return max(error.rate_limit_reset_at - time.time(), base_delay, 0.0)

        delay = base_delay * (2**attempt)
        if error.status_code == 429:
            return max(delay, 15.0)
        return delay

    def _wait_for_rate_limit_window(self) -> None:
        if self._rate_limit_remaining is None or self._rate_limit_remaining > 0 or self._rate_limit_reset_at is None:
            return

        delay = self._rate_limit_reset_at - time.time()
        if delay > 0:
            self._sleep_before_retry(delay)

        self._rate_limit_remaining = None
        self._rate_limit_reset_at = None

    def _sleep_before_retry(self, delay_seconds: float) -> None:
        bounded_delay = min(max(delay_seconds, 0.0), MAX_RETRY_SLEEP_SECONDS)
        time.sleep(bounded_delay)

    def _update_rate_limit_state(self, headers: Any) -> None:
        limit = self._extract_int_header(self._header_value(headers, "X-RateLimit-UserLimit"))
        remaining = self._extract_int_header(self._header_value(headers, "X-RateLimit-UserRemaining"))
        reset_at = self._extract_unix_timestamp(self._header_value(headers, "X-RateLimit-UserReset"))

        if limit is not None:
            self._rate_limit_limit = limit
        if remaining is not None:
            self._rate_limit_remaining = remaining
        if reset_at is not None:
            self._rate_limit_reset_at = reset_at

    def _header_value(self, headers: Any, key: str) -> str | None:
        if headers is None:
            return None
        getter = getattr(headers, "get", None)
        if callable(getter):
            value = getter(key)
            return value if isinstance(value, str) else None
        return None

    def _extract_int_header(self, value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value.strip())
        except ValueError:
            return None

    def _extract_unix_timestamp(self, value: str | None) -> float | None:
        if value is None:
            return None
        try:
            reset_at = float(value.strip())
        except ValueError:
            return None
        if reset_at >= UNIX_TIMESTAMP_MILLISECONDS_THRESHOLD:
            reset_at /= 1000.0
        return reset_at if reset_at >= 0 else None

    def _extract_retry_after_seconds(self, value: str | None) -> float | None:
        if value is None:
            return None
        try:
            retry_after = float(value.strip())
        except ValueError:
            return None
        return retry_after if retry_after >= 0 else None

    def _authorization_candidates(self) -> list[str]:
        token = self.api_token.strip()
        if token.lower().startswith("bearer "):
            raw = token[7:].strip()
            candidates = [token]
            if raw:
                candidates.append(raw)
            return candidates
        return [f"Bearer {token}", token]

    def _extract_text(self, payload: Any, *keys: str) -> str | None:
        if not isinstance(payload, dict):
            return None

        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value

        nested = payload.get("data")
        if isinstance(nested, dict):
            for key in keys:
                value = nested.get(key)
                if isinstance(value, str) and value:
                    return value
        return None

    def _normalize_upload_link(self, link: str) -> str:
        parsed = urllib.parse.urlparse(link)
        if parsed.scheme and parsed.netloc:
            return link
        if link.startswith("/"):
            return urllib.parse.urljoin("https://hackmd.io", link)
        return link


class HackMDPublisher:
    def __init__(
        self,
        client: HackMDClient | None = None,
        *,
        post_upload_settle_seconds: float = DEFAULT_POST_UPLOAD_SETTLE_SECONDS,
    ) -> None:
        self.client = client or HackMDClient()
        self.post_upload_settle_seconds = max(post_upload_settle_seconds, 0.0)

    def publish_markdown(
        self,
        note_path: Path,
        *,
        image_dir: Path | None = Path("workdir/frames"),
        title: str | None = None,
    ) -> HackMDPublishResult:
        note_path = note_path.resolve()
        content = note_path.read_text(encoding="utf-8")
        note_title = title or self._extract_title(content, note_path)

        if not self._contains_local_images(content):
            created_note = self.client.create_note(note_title, content)
            return HackMDPublishResult(
                note_id=created_note.note_id,
                title=note_title,
                publish_link=created_note.publish_link,
                permalink=created_note.permalink,
                image_count=0,
            )

        created_note = self.client.create_note(note_title, content)
        enriched_content, image_count = self._inject_uploaded_images(
            content,
            note_path=note_path,
            note_id=created_note.note_id,
            image_dir=image_dir,
        )
        if image_count > 0 and self.post_upload_settle_seconds > 0:
            self._sleep_before_finalize(self.post_upload_settle_seconds)
        try:
            self.client.update_note(created_note, content=enriched_content)
        except HackMDRequestError as exc:
            if not exc.retryable:
                raise
            created_note = self.client.create_note(note_title, enriched_content)
        return HackMDPublishResult(
            note_id=created_note.note_id,
            title=note_title,
            publish_link=created_note.publish_link,
            permalink=created_note.permalink,
            image_count=image_count,
        )

    def _sleep_before_finalize(self, delay_seconds: float) -> None:
        time.sleep(delay_seconds)

    def _inject_uploaded_images(
        self,
        content: str,
        *,
        note_path: Path,
        note_id: str,
        image_dir: Path | None,
    ) -> tuple[str, int]:
        lines = content.splitlines()
        rendered_lines: list[str] = []
        uploaded_links: dict[Path, str] = {}
        upload_count = 0

        for line in lines:
            rendered_lines.append(line)
            match = SCREENSHOT_LINE_RE.match(line)
            if not match:
                continue

            image_ref = match.group("image").strip()
            image_path = self._resolve_image_path(image_ref, note_path, image_dir)
            image_url = uploaded_links.get(image_path)
            if image_url is None:
                image_url = self.client.upload_image(note_id, image_path)
                uploaded_links[image_path] = image_url
            rendered_lines.append(f"![{image_path.name}]({image_url})")
            upload_count += 1

        suffix = "\n" if content.endswith("\n") else ""
        return "\n".join(rendered_lines) + suffix, upload_count

    def _contains_local_images(self, content: str) -> bool:
        return any(SCREENSHOT_LINE_RE.match(line) for line in content.splitlines())

    def _resolve_image_path(self, image_ref: str, note_path: Path, image_dir: Path | None) -> Path:
        candidate = Path(image_ref)
        if candidate.is_absolute() and candidate.is_file():
            return candidate.resolve()

        search_roots = self._build_search_roots(note_path, image_dir)
        for root in search_roots:
            resolved = (root / candidate).resolve()
            if resolved.is_file():
                return resolved

        searched = ", ".join(str(root) for root in search_roots)
        raise FileNotFoundError(f"Could not find image '{image_ref}' for {note_path.name}. Searched: {searched}")

    def _build_search_roots(self, note_path: Path, image_dir: Path | None) -> list[Path]:
        roots: list[Path] = []

        if image_dir is not None:
            if image_dir.is_absolute():
                roots.append(image_dir.resolve())
            else:
                roots.append((note_path.parent / image_dir).resolve())
                roots.append((Path.cwd() / image_dir).resolve())

        defaults = [
            note_path.parent,
            note_path.parent / "frames",
            note_path.parent / "workdir" / "frames",
            Path.cwd(),
            Path.cwd() / "frames",
            Path.cwd() / "workdir" / "frames",
        ]

        deduped: list[Path] = []
        seen: set[Path] = set()
        for root in roots + defaults:
            resolved = root.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            deduped.append(resolved)
        return deduped

    def _extract_title(self, content: str, note_path: Path) -> str:
        for line in content.splitlines():
            match = MARKDOWN_TITLE_RE.match(line.strip())
            if match:
                return match.group("title").strip()
        return note_path.stem
