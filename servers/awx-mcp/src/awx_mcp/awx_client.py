"""HTTP client for Ansible AWX / Automation Controller REST API."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, NamedTuple

import httpx

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 502, 503, 504}
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0

#: Stable prefix of the notice AWX returns (at HTTP 200, ``text/plain``) instead
#: of the real stdout when a job's output exceeds ``STDOUT_MAX_BYTES_DISPLAY``
#: (default 1048576 bytes). The full message is e.g. ``"Standard Output too large
#: to display (3214886 bytes), only download supported for sizes over 1048576
#: bytes. ..."``. Only the ``txt``/``ansi`` renderers are capped; the
#: ``*_download`` renderers stream the full file regardless of size.
STDOUT_TOO_LARGE_MARKER = "Standard Output too large to display"

#: Upper bound (bytes) for treating a stdout response as the cap notice. The
#: real notice is a short single line; bounding the length avoids mistaking a
#: genuine log that merely *mentions* the phrase for the gate.
_TOO_LARGE_NOTICE_MAX_LEN = 4096


def is_stdout_too_large_notice(text: str) -> bool:
    """Return ``True`` if *text* is AWX's "Standard Output too large" cap notice.

    AWX returns this notice with HTTP 200 and ``text/plain`` for the capped
    ``txt``/``ansi`` stdout renderers, so it cannot be distinguished by status
    code — it must be detected by content. The notice is always a short message
    that begins with :data:`STDOUT_TOO_LARGE_MARKER`; we additionally bound the
    length so a multi-MB log that merely contains the phrase is never mistaken
    for the gate.
    """
    if not text:
        return False
    stripped = text.strip()
    if stripped.startswith(STDOUT_TOO_LARGE_MARKER):
        return True
    return len(stripped) <= _TOO_LARGE_NOTICE_MAX_LEN and STDOUT_TOO_LARGE_MARKER in stripped


class JobStdout(NamedTuple):
    """Result of fetching a job's stdout.

    Attributes:
        content: The stdout text. When :attr:`capped` is ``False`` this is the
            full (possibly cap-bypassed) output.
        capped: ``True`` if AWX returned the size-gate notice *and* it could not
            be bypassed (e.g. the caller disabled the download fallback).
        downloaded: ``True`` if the content was fetched via the cap-bypassing
            ``*_download`` renderer.
    """

    content: str
    capped: bool
    downloaded: bool


@dataclass(slots=True)
class AwxRestClient:
    """
    Minimal AWX REST client.

    - Auth: OAuth2 PAT via ``Authorization: Bearer <token>``
    - Base path: defaults to ``/api/v2``
    - Retries transient errors (429, 502, 503, 504) with exponential backoff
    """

    host: str
    token: str
    api_base_path: str = "/api/v2"
    verify_ssl: bool = True
    timeout_seconds: float = 30.0
    max_retries: int = _MAX_RETRIES
    http_transport: httpx.BaseTransport | None = field(default=None, repr=False)

    _client: httpx.Client = field(init=False, repr=False)
    _base_url_str: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._base_url_str = self._build_base_url()
        self._client = httpx.Client(
            verify=self.verify_ssl,
            timeout=httpx.Timeout(self.timeout_seconds),
            transport=self.http_transport,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
        )

    def _build_base_url(self) -> str:
        host = self.host.rstrip("/")
        base = self.api_base_path.strip("/")
        return f"{host}/{base}"

    def _url(self, endpoint: str) -> str:
        ep = endpoint.strip("/")
        return f"{self._base_url_str}/{ep}/"

    def _raise_for_status(self, r: httpx.Response, method: str, url: str) -> None:
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"AWX {method} {url} failed: {e.response.status_code} {e.response.text}"
            ) from e

    def _request_with_retry(
        self,
        method: str,
        url: str,
        **kwargs: Any,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                r = self._client.request(method, url, **kwargs)
                if r.status_code not in _RETRYABLE_STATUS_CODES or attempt == self.max_retries:
                    return r
                wait = _RETRY_BACKOFF_BASE * (2**attempt)
                if r.status_code == 429:
                    wait = float(r.headers.get("Retry-After", wait))
                logger.warning(
                    "AWX %s %s returned %s, retrying in %.1fs", method, url, r.status_code, wait
                )
                time.sleep(wait)
            except httpx.TimeoutException as e:
                last_exc = e
                if attempt == self.max_retries:
                    break
                wait = _RETRY_BACKOFF_BASE * (2**attempt)
                logger.warning("AWX %s %s timed out, retrying in %.1fs", method, url, wait)
                time.sleep(wait)
        raise RuntimeError(
            f"AWX {method} {url} failed after {self.max_retries + 1} attempts"
        ) from last_exc

    @staticmethod
    def _parse_json_or_empty(r: httpx.Response, url: str) -> Any:
        if r.status_code == 204 or not r.content:
            return {"status_code": r.status_code, "url": url}
        try:
            return r.json()
        except Exception:
            return {"status_code": r.status_code, "url": url}

    def close(self) -> None:
        self._client.close()

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("GET", url, params=params)
        self._raise_for_status(r, "GET", url)
        return r.json()

    def get_text(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        *,
        accept: str = "text/plain",
    ) -> str:
        url = self._url(endpoint)
        r = self._request_with_retry("GET", url, params=params, headers={"Accept": accept})
        self._raise_for_status(r, "GET", url)
        return r.text

    def get_job_stdout(
        self,
        job_id: int,
        *,
        fmt: str = "txt",
        start_line: int | None = None,
        end_line: int | None = None,
        bypass_cap: bool = True,
    ) -> JobStdout:
        """Fetch a job's stdout, transparently bypassing the display cap.

        AWX caps the ``txt``/``ansi`` stdout renderers at
        ``STDOUT_MAX_BYTES_DISPLAY`` (default 1 MiB): once exceeded it returns a
        short "Standard Output too large to display" notice at HTTP 200 instead
        of the real output (see :func:`is_stdout_too_large_notice`). The
        ``*_download`` renderers are *not* capped, so when *bypass_cap* is set
        (the default) and the notice is detected for a ``txt``/``ansi`` fetch,
        this retries with the matching ``{fmt}_download`` renderer to stream the
        full file.

        Args:
            job_id: The job whose stdout to fetch.
            fmt: AWX stdout format — ``txt``, ``ansi``, ``html``, ``txt_download``
                or ``ansi_download``. Download formats are always uncapped.
            start_line: Optional 1-based start line (AWX server-side pagination).
            end_line: Optional 1-based end line.
            bypass_cap: Retry via ``{fmt}_download`` when the cap notice is seen.

        Returns:
            A :class:`JobStdout` with the text and cap/download flags.
        """
        params: dict[str, Any] = {"format": fmt}
        if start_line is not None:
            params["start_line"] = start_line
        if end_line is not None:
            params["end_line"] = end_line
        # Always negotiate text/plain: the download renderers share text/plain as
        # their media type, so requesting application/json (the client default)
        # is exactly what makes ``?format=txt_download`` return HTTP 406.
        content = self.get_text(f"jobs/{job_id}/stdout", params=params, accept="text/plain")

        if not is_stdout_too_large_notice(content):
            return JobStdout(content=content, capped=False, downloaded=fmt.endswith("_download"))

        base = fmt[: -len("_download")] if fmt.endswith("_download") else fmt
        if not bypass_cap or base not in ("txt", "ansi"):
            return JobStdout(content=content, capped=True, downloaded=False)

        logger.info("Job %s stdout exceeds the display cap; retrying via %s_download", job_id, base)
        download = self.get_text(
            f"jobs/{job_id}/stdout",
            params={"format": f"{base}_download"},
            accept="text/plain",
        )
        if is_stdout_too_large_notice(download):
            # Extremely unlikely (download is uncapped), but stay honest.
            return JobStdout(content=download, capped=True, downloaded=True)
        return JobStdout(content=download, capped=False, downloaded=True)

    def post(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("POST", url, json=json)
        self._raise_for_status(r, "POST", url)
        return self._parse_json_or_empty(r, url)

    def put(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("PUT", url, json=json)
        self._raise_for_status(r, "PUT", url)
        return self._parse_json_or_empty(r, url)

    def patch(self, endpoint: str, json: dict[str, Any] | None = None) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("PATCH", url, json=json)
        self._raise_for_status(r, "PATCH", url)
        return self._parse_json_or_empty(r, url)

    def delete(self, endpoint: str) -> Any:
        url = self._url(endpoint)
        r = self._request_with_retry("DELETE", url)
        self._raise_for_status(r, "DELETE", url)
        return self._parse_json_or_empty(r, url)
