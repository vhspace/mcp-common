"""Entry point for ``python -m mcp_common.testing.eval``.

Dispatches to the eval sub-CLIs:

- ``python -m mcp_common.testing.eval --log-dir ./logs/ [--dry-run|--create-issues|--auto-fix]``
  → the legacy ``report`` command (analyze eval logs and file/remediate failures).
- ``python -m mcp_common.testing.eval description-qa --server <module> [--server ...]``
  → the heuristic description-quality gate (#88 Phase 3a).

The report command stays a single-command Typer app (bare invocation, unchanged
for backward compat). Anything whose first arg is ``description-qa`` is routed
to :func:`mcp_common.testing.eval.description_qa.qa_main`.
"""

from __future__ import annotations

import sys


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "description-qa":
        from mcp_common.testing.eval.description_qa import qa_main

        qa_main(argv[1:])
        return
    from mcp_common.testing.eval.report import main as report_main

    report_main()


if __name__ == "__main__":
    main()
