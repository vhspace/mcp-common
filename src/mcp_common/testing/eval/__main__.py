"""Entry point for ``python -m mcp_common.testing.eval``.

Dispatches to the eval sub-CLIs:

- ``python -m mcp_common.testing.eval --log-dir ./logs/ [--dry-run|--create-issues|--auto-fix]``
  → the legacy ``report`` command (analyze eval logs and file/remediate failures).
- ``python -m mcp_common.testing.eval description-qa --server <module> [--server ...]``
  → the heuristic description-quality gate (#88 Phase 3a).
- ``python -m mcp_common.testing.eval parity --reference <mcp.eval> --candidate <cli.eval>``
  → MCP ↔ CLI equivalence regression via Inspect ``.eval`` log comparison
  (#88 Phase 4a). On-demand; not a CI gate.
- ``python -m mcp_common.testing.eval deepeval-failures --source <logs/>``
  → post-hoc DeepEval faithfulness/hallucination on INCORRECT/PARTIAL samples
  only (#88 Phase 4b). On-demand; requires the ``[eval-scoring]`` extra.

The report command stays a single-command Typer app (bare invocation, unchanged
for backward compat). Anything whose first arg is a known subcommand is routed
to that subcommand's ``*_main``; otherwise the legacy ``report`` command runs.
"""

from __future__ import annotations

import sys

_SUBCOMMANDS = {
    "description-qa": "mcp_common.testing.eval.description_qa:qa_main",
    "parity": "mcp_common.testing.eval.parity:parity_main",
    "deepeval-failures": "mcp_common.testing.eval.deepeval_on_failures:deepeval_failures_main",
}


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] in _SUBCOMMANDS:
        module_path, attr = _SUBCOMMANDS[argv[0]].split(":")
        import importlib

        handler = getattr(importlib.import_module(module_path), attr)
        raise SystemExit(handler(argv[1:]))
    from mcp_common.testing.eval.report import main as report_main

    report_main()


if __name__ == "__main__":
    main()
