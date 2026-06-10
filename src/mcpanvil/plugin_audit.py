"""Audit MCP server repos for mcpanvil feature adoption.

Scans ``src/`` for ``mcpanvil`` imports and warns about missing
features that every MCP server should be using.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AuditFeature:
    """A mcpanvil feature to check for."""

    name: str
    import_names: list[str]
    description: str
    fix_hint: str
    required: bool = True


AUDIT_FEATURES: list[AuditFeature] = [
    AuditFeature(
        name="load_env",
        import_names=["load_env"],
        description="Standardized .env file loading",
        fix_hint="from mcpanvil.env import load_env; load_env()  # in main()",
        required=True,
    ),
    AuditFeature(
        name="setup_logging",
        import_names=["setup_logging"],
        description="Structured JSON logging",
        fix_hint="from mcpanvil import setup_logging; log = setup_logging(...)",
        required=True,
    ),
    AuditFeature(
        name="health_resource",
        import_names=["health_resource"],
        description="MCP health resource for agent connectivity checks",
        fix_hint="from mcpanvil import health_resource",
        required=True,
    ),
    AuditFeature(
        name="add_health_route",
        import_names=["add_health_route"],
        description="HTTP /health endpoint for K8s probes",
        fix_hint='from mcpanvil import add_health_route; add_health_route(mcp, "name")',
        required=True,
    ),
    AuditFeature(
        name="mcp_remediation_wrapper",
        import_names=["mcp_remediation_wrapper"],
        description="Agent-friendly error handling on MCP tools",
        fix_hint="from mcpanvil.agent_remediation import mcp_remediation_wrapper",
        required=True,
    ),
    AuditFeature(
        name="get_version",
        import_names=["get_version"],
        description="Dynamic version from package metadata",
        fix_hint='from mcpanvil.version import get_version; __version__ = get_version("pkg")',
        required=True,
    ),
    AuditFeature(
        name="MCPSettings",
        import_names=["MCPSettings"],
        description="Standard settings base class with transport/debug/auth fields",
        fix_hint="from mcpanvil import MCPSettings; class Settings(MCPSettings): ...",
        required=False,
    ),
    AuditFeature(
        name="install_cli_exception_handler",
        # Satisfied by the handler directly OR by adopting the CLI scaffolding
        # that wires it transparently: create_cli_app and build_cli_from_mcp
        # both call install_cli_exception_handler internally, so an MCP that
        # migrated to either no longer imports the handler by name. Accept all
        # three so the audit stops false-positiving on correctly-migrated MCPs.
        import_names=[
            "install_cli_exception_handler",
            "create_cli_app",
            "build_cli_from_mcp",
        ],
        description=(
            "Agent-friendly CLI error handling with GitHub issue guidance "
            "(install_cli_exception_handler, or create_cli_app / "
            "build_cli_from_mcp which wire it for you)"
        ),
        fix_hint=(
            "from mcpanvil.agent_remediation import install_cli_exception_handler "
            "— or adopt mcpanvil.cli.create_cli_app / "
            "mcpanvil.dual_mode.build_cli_from_mcp, which attach it automatically"
        ),
        required=False,
    ),
]


@dataclass
class AuditResult:
    """Result of auditing a repo."""

    features_found: list[str] = field(default_factory=list)
    features_missing_required: list[AuditFeature] = field(default_factory=list)
    features_missing_recommended: list[AuditFeature] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.features_missing_required) == 0


def collect_mcpanvil_imports(src_dir: Path) -> set[str]:
    """Parse all .py files under *src_dir* for names imported from mcpanvil."""
    names: set[str] = set()
    for py_file in src_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text())
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith("mcpanvil")
            ):
                for alias in node.names:
                    names.add(alias.name)
    return names


def audit_repo(repo_root: Path) -> AuditResult:
    """Audit a repo for mcpanvil feature adoption."""
    src_dir = repo_root / "src"
    if not src_dir.exists():
        src_dir = repo_root

    imported = collect_mcpanvil_imports(src_dir)
    result = AuditResult()

    for feature in AUDIT_FEATURES:
        found = any(name in imported for name in feature.import_names)
        if found:
            result.features_found.append(feature.name)
        elif feature.required:
            result.features_missing_required.append(feature)
        else:
            result.features_missing_recommended.append(feature)

    return result
