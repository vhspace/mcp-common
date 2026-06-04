"""Ansible log parser for AWX job stdout.

Parses Ansible playbook output into structured data: play recaps, failed tasks,
warnings, changed tasks, and per-host statistics. Supports smart truncation
strategies for very large logs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_PLAY_RE = re.compile(r"^PLAY \[(.+?)\]\s*\*+", re.MULTILINE)
_TASK_RE = re.compile(r"^(?:TASK|RUNNING HANDLER) \[(.+?)\]\s*\*+", re.MULTILINE)
_RECAP_HEADER_RE = re.compile(r"^PLAY RECAP\s*\*+", re.MULTILINE)
_RECAP_LINE_RE = re.compile(
    r"^(\S+)\s*:\s*"
    r"ok=(\d+)\s+"
    r"changed=(\d+)\s+"
    r"unreachable=(\d+)\s+"
    r"failed=(\d+)"
    r"(?:\s+skipped=(\d+))?"
    r"(?:\s+rescued=(\d+))?"
    r"(?:\s+ignored=(\d+))?",
    re.MULTILINE,
)
_WARNING_RE = re.compile(r"^\[WARNING\]:?\s*(.+?)$", re.MULTILINE)
# fatal: [host]: FAILED! => {json}
_FATAL_RE = re.compile(
    r"^fatal:\s+\[(.+?)\]:\s+(\w+)!\s+=>\s+(.+?)$",
    re.MULTILINE,
)
# failed: [host] (item=x) => {json}  OR  failed: [host] => {json}
_FAILED_ITEM_RE = re.compile(
    r"^failed:\s+\[(.+?)\](?:\s+\(item=(.+?)\))?\s+=>\s+(.+?)$",
    re.MULTILINE,
)
_CHANGED_LINE_RE = re.compile(
    r"^changed:\s+\[(.+?)\]",
    re.MULTILINE,
)
_HOST_RESULT_RE = re.compile(
    r"^(ok|changed|fatal|failed|skipping|ignored|rescued|unreachable):\s+\[(.+?)\]",
    re.MULTILINE,
)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences so regexes match colored AWX output."""
    return _ANSI_RE.sub("", text)


@dataclass
class HostStats:
    host: str
    ok: int = 0
    changed: int = 0
    unreachable: int = 0
    failed: int = 0
    skipped: int = 0
    rescued: int = 0
    ignored: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "ok": self.ok,
            "changed": self.changed,
            "unreachable": self.unreachable,
            "failed": self.failed,
            "skipped": self.skipped,
            "rescued": self.rescued,
            "ignored": self.ignored,
        }


@dataclass
class FailedTask:
    host: str
    task: str
    module: str
    message: str
    line_number: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "host": self.host,
            "task": self.task,
            "module": self.module,
            "message": self.message,
        }
        if self.line_number is not None:
            d["line_number"] = self.line_number
        return d


@dataclass
class ParsedLog:
    plays: list[str] = field(default_factory=list)
    tasks: list[str] = field(default_factory=list)
    failed_tasks: list[FailedTask] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    host_stats: list[HostStats] = field(default_factory=list)
    recap_text: str = ""
    total_lines: int = 0
    has_failures: bool = False
    overall_result: str = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "plays": self.plays,
            "total_tasks": len(self.tasks),
            "failed_tasks": [f.to_dict() for f in self.failed_tasks],
            "warnings": self.warnings,
            "host_stats": [h.to_dict() for h in self.host_stats],
            "recap_text": self.recap_text,
            "total_lines": self.total_lines,
            "has_failures": self.has_failures,
            "overall_result": self.overall_result,
        }


def parse_ansible_log(content: str) -> ParsedLog:
    """Parse Ansible playbook stdout into structured data."""
    content = _strip_ansi(content)
    result = ParsedLog()
    lines = content.split("\n")
    result.total_lines = len(lines)

    result.plays = _PLAY_RE.findall(content)
    result.tasks = _TASK_RE.findall(content)
    result.warnings = _WARNING_RE.findall(content)

    current_task = ""
    for i, line in enumerate(lines):
        task_match = _TASK_RE.match(line)
        if task_match:
            current_task = task_match.group(1)
            continue

        # fatal: [host]: FAILED! => ...
        fatal_match = _FATAL_RE.match(line)
        if fatal_match:
            host, module, raw_msg = fatal_match.groups()
            result.failed_tasks.append(
                FailedTask(
                    host=host,
                    task=current_task,
                    module=module,
                    message=_clean_fatal_message(raw_msg),
                    line_number=i + 1,
                )
            )
            continue

        # failed: [host] (item=x) => ...
        failed_item_match = _FAILED_ITEM_RE.match(line)
        if failed_item_match:
            host = failed_item_match.group(1)
            item = failed_item_match.group(2) or ""
            raw_msg = failed_item_match.group(3)
            msg = _clean_fatal_message(raw_msg)
            task_label = f"{current_task} (item={item})" if item else current_task
            result.failed_tasks.append(
                FailedTask(
                    host=host,
                    task=task_label,
                    module="FAILED",
                    message=msg,
                    line_number=i + 1,
                )
            )

    recap_match = _RECAP_HEADER_RE.search(content)
    if recap_match:
        recap_start = recap_match.start()
        result.recap_text = content[recap_start:].strip()

        for m in _RECAP_LINE_RE.finditer(content, recap_start):
            result.host_stats.append(
                HostStats(
                    host=m.group(1),
                    ok=int(m.group(2)),
                    changed=int(m.group(3)),
                    unreachable=int(m.group(4)),
                    failed=int(m.group(5)),
                    skipped=int(m.group(6) or 0),
                    rescued=int(m.group(7) or 0),
                    ignored=int(m.group(8) or 0),
                )
            )

    result.has_failures = bool(result.failed_tasks) or any(
        h.failed > 0 or h.unreachable > 0 for h in result.host_stats
    )
    result.overall_result = _determine_result(result)
    return result


def extract_recap(content: str) -> dict[str, Any]:
    """Extract just the PLAY RECAP section and host stats."""
    content = _strip_ansi(content)
    recap_match = _RECAP_HEADER_RE.search(content)
    if not recap_match:
        return {"found": False, "recap_text": "", "host_stats": []}

    recap_text = content[recap_match.start() :].strip()
    host_stats = []
    for m in _RECAP_LINE_RE.finditer(content, recap_match.start()):
        host_stats.append(
            HostStats(
                host=m.group(1),
                ok=int(m.group(2)),
                changed=int(m.group(3)),
                unreachable=int(m.group(4)),
                failed=int(m.group(5)),
                skipped=int(m.group(6) or 0),
                rescued=int(m.group(7) or 0),
                ignored=int(m.group(8) or 0),
            ).to_dict()
        )

    return {"found": True, "recap_text": recap_text, "host_stats": host_stats}


def extract_failures(content: str) -> list[dict[str, Any]]:
    """Extract all fatal/failed task occurrences with context."""
    content = _strip_ansi(content)
    failures: list[dict[str, Any]] = []
    lines = content.split("\n")
    current_task = ""

    for i, line in enumerate(lines):
        task_match = _TASK_RE.match(line)
        if task_match:
            current_task = task_match.group(1)
            continue

        context_start = max(0, i - 2)
        context_end = min(len(lines), i + 5)

        fatal_match = _FATAL_RE.match(line)
        if fatal_match:
            host, module, raw_msg = fatal_match.groups()
            failures.append(
                {
                    "host": host,
                    "task": current_task,
                    "module": module,
                    "message": _clean_fatal_message(raw_msg),
                    "line_number": i + 1,
                    "context": "\n".join(lines[context_start:context_end]),
                }
            )
            continue

        failed_item_match = _FAILED_ITEM_RE.match(line)
        if failed_item_match:
            host = failed_item_match.group(1)
            item = failed_item_match.group(2) or ""
            raw_msg = failed_item_match.group(3)
            task_label = f"{current_task} (item={item})" if item else current_task
            failures.append(
                {
                    "host": host,
                    "task": task_label,
                    "module": "FAILED",
                    "message": _clean_fatal_message(raw_msg),
                    "line_number": i + 1,
                    "context": "\n".join(lines[context_start:context_end]),
                }
            )

    return failures


def extract_warnings(content: str) -> list[str]:
    """Extract all [WARNING] messages."""
    return _WARNING_RE.findall(_strip_ansi(content))


def smart_truncate(
    content: str,
    limit: int,
    strategy: str = "tail",
) -> dict[str, Any]:
    """Intelligently truncate log content.

    Strategies:
        head: First `limit` chars (original behavior)
        tail: Last `limit` chars (most useful for seeing failures/recaps)
        head_tail: First limit/4 + last 3*limit/4 chars
        recap_context: PLAY RECAP + surrounding context, padded with tail
    """
    if len(content) <= limit:
        return {
            "content": content,
            "truncated": False,
            "strategy": strategy,
            "original_length": len(content),
        }

    if strategy == "head":
        truncated = content[:limit]
        return {
            "content": truncated,
            "truncated": True,
            "strategy": "head",
            "original_length": len(content),
            "note": f"Showing first {limit} of {len(content)} chars",
        }

    if strategy == "tail":
        truncated = content[-limit:]
        first_newline = truncated.find("\n")
        if first_newline > 0 and first_newline < 200:
            truncated = truncated[first_newline + 1 :]
        return {
            "content": truncated,
            "truncated": True,
            "strategy": "tail",
            "original_length": len(content),
            "note": f"Showing last ~{limit} of {len(content)} chars",
        }

    if strategy == "head_tail":
        head_size = limit // 4
        tail_size = limit - head_size
        head = content[:head_size]
        tail_raw = content[-tail_size:]
        first_newline = tail_raw.find("\n")
        if first_newline > 0 and first_newline < 200:
            tail_raw = tail_raw[first_newline + 1 :]
        separator = f"\n\n--- [{len(content) - head_size - tail_size} chars omitted] ---\n\n"
        truncated = head + separator + tail_raw
        return {
            "content": truncated,
            "truncated": True,
            "strategy": "head_tail",
            "original_length": len(content),
            "note": f"Showing first {head_size} + last ~{tail_size} of {len(content)} chars",
        }

    if strategy == "recap_context":
        recap_match = _RECAP_HEADER_RE.search(content)
        if recap_match:
            recap_start = max(0, recap_match.start() - 500)
            recap_section = content[recap_start:]
            if len(recap_section) >= limit:
                return smart_truncate(recap_section, limit, strategy="tail")
            remaining = limit - len(recap_section)
            head = content[:remaining]
            separator = f"\n\n--- [{recap_start - remaining} chars omitted] ---\n\n"
            truncated = head + separator + recap_section
            return {
                "content": truncated,
                "truncated": True,
                "strategy": "recap_context",
                "original_length": len(content),
                "note": f"Showing head + PLAY RECAP context from {len(content)} chars",
            }
        return smart_truncate(content, limit, strategy="tail")

    return smart_truncate(content, limit, strategy="tail")


@dataclass
class StdoutBlock:
    """A contiguous section of Ansible stdout belonging to one PLAY/TASK."""

    kind: str  # "play", "task", "recap", "preamble"
    play_name: str = ""
    task_name: str = ""
    lines: list[str] = field(default_factory=list)
    hosts: dict[str, str] = field(default_factory=dict)  # host -> status


def parse_stdout_blocks(content: str) -> list[StdoutBlock]:
    """Split raw Ansible stdout into structured blocks for filtering."""
    content = _strip_ansi(content)
    content = content.replace("\r\n", "\n")
    lines = content.split("\n")
    blocks: list[StdoutBlock] = []
    current_play = ""
    current: StdoutBlock | None = None

    def _flush() -> None:
        nonlocal current
        if current is not None:
            blocks.append(current)
            current = None

    for line in lines:
        play_match = _PLAY_RE.match(line)
        if play_match:
            _flush()
            current_play = play_match.group(1)
            current = StdoutBlock(kind="play", play_name=current_play, lines=[line])
            continue

        recap_match = _RECAP_HEADER_RE.match(line)
        if recap_match:
            _flush()
            current = StdoutBlock(kind="recap", play_name=current_play, lines=[line])
            continue

        task_match = _TASK_RE.match(line)
        if task_match:
            _flush()
            current = StdoutBlock(
                kind="task",
                play_name=current_play,
                task_name=task_match.group(1),
                lines=[line],
            )
            continue

        if current is None:
            current = StdoutBlock(kind="preamble", lines=[line])
        else:
            current.lines.append(line)
            host_match = _HOST_RESULT_RE.match(line)
            if host_match:
                status, host = host_match.group(1), host_match.group(2)
                current.hosts[host] = status

    _flush()
    return blocks


def filter_stdout(
    content: str,
    *,
    filter_mode: str = "all",
    play: str | None = None,
    host: str | None = None,
    task: str | None = None,
) -> str:
    """Filter Ansible stdout by status, play, host, or task pattern.

    Args:
        content: Raw Ansible stdout text.
        filter_mode: "all" (default), "errors", or "changed".
        play: Play name substring or 1-based index (e.g. "1" for first play).
        host: Hostname pattern matched via fnmatch.
        task: Task name pattern matched via fnmatch.

    Returns:
        Filtered stdout text reassembled from matching blocks.
    """
    import fnmatch as _fnmatch

    content = content.replace("\r\n", "\n")
    blocks = parse_stdout_blocks(content)
    if not blocks:
        return content

    play_index: int | None = None
    play_substr: str | None = None
    if play is not None:
        try:
            play_index = int(play)
        except ValueError:
            play_substr = play.lower()

    error_statuses = {"fatal", "failed", "unreachable"}
    changed_statuses = {"changed"}

    play_counter = 0
    kept: list[StdoutBlock] = []

    for block in blocks:
        if block.kind == "preamble":
            kept.append(block)
            continue

        if block.kind == "recap":
            kept.append(block)
            continue

        if block.kind == "play":
            play_counter += 1

        if play is not None:
            if play_index is not None:
                if play_counter != play_index:
                    continue
            elif play_substr is not None:
                if block.play_name.lower().find(play_substr) == -1:
                    continue

        if task is not None and block.kind == "task":
            if not _fnmatch.fnmatch(block.task_name.lower(), task.lower()):
                continue

        if block.kind == "task":
            if filter_mode == "errors":
                relevant = {h for h, s in block.hosts.items() if s in error_statuses}
                if not relevant:
                    continue
            elif filter_mode == "changed":
                relevant = {h for h, s in block.hosts.items() if s in changed_statuses}
                if not relevant:
                    continue

            if host is not None:
                matching = {h for h in block.hosts if _fnmatch.fnmatch(h.lower(), host.lower())}
                if filter_mode != "all":
                    status_set = error_statuses if filter_mode == "errors" else changed_statuses
                    matching = matching & {h for h, s in block.hosts.items() if s in status_set}
                if not matching:
                    continue
                kept.append(_filter_block_lines_by_hosts(block, matching))
                continue

        kept.append(block)

    return _reassemble_blocks(kept)


def _filter_block_lines_by_hosts(block: StdoutBlock, allowed_hosts: set[str]) -> StdoutBlock:
    """Return a copy of block keeping only host-result lines for allowed_hosts."""
    filtered_lines: list[str] = []
    for line in block.lines:
        host_match = _HOST_RESULT_RE.match(line)
        if host_match:
            h = host_match.group(2)
            if h in allowed_hosts:
                filtered_lines.append(line)
        else:
            filtered_lines.append(line)
    return StdoutBlock(
        kind=block.kind,
        play_name=block.play_name,
        task_name=block.task_name,
        lines=filtered_lines,
        hosts={h: s for h, s in block.hosts.items() if h in allowed_hosts},
    )


def _reassemble_blocks(blocks: list[StdoutBlock]) -> str:
    """Join kept blocks back into readable text."""
    parts: list[str] = []
    for block in blocks:
        parts.append("\n".join(block.lines))
    return "\n".join(parts)


def _clean_fatal_message(raw: str) -> str:
    """Trim overly verbose JSON blobs in fatal messages."""
    msg = raw.strip()
    if len(msg) > 500:
        msg = msg[:500] + "..."
    return msg


def _determine_result(parsed: ParsedLog) -> str:
    if not parsed.host_stats:
        if parsed.failed_tasks:
            return "failed"
        return "unknown"
    if any(h.unreachable > 0 for h in parsed.host_stats):
        return "unreachable"
    if any(h.failed > 0 for h in parsed.host_stats):
        return "failed"
    return "successful"
