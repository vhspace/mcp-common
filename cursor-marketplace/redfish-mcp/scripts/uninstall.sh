#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT_NAME="$(basename "$0")"

# --- Colors ---------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
BOLD='\033[1m'; NC='\033[0m'

# --- Flags -----------------------------------------------------------------
DRY_RUN=false
for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY_RUN=true ;;
    --help|-h)
      echo "Usage: $SCRIPT_NAME [--dry-run]"
      echo "  --dry-run   Show what would be done without making changes"
      exit 0
      ;;
    *) echo -e "${RED}Unknown option: $arg${NC}"; exit 1 ;;
  esac
done

# --- Helpers ---------------------------------------------------------------
info()    { echo -e "${BLUE}ℹ ${NC}$*"; }
success() { echo -e "${GREEN}✔ ${NC}$*"; }
warn()    { echo -e "${YELLOW}⚠ ${NC}$*"; }
error()   { echo -e "${RED}✖ ${NC}$*" >&2; }

# --- Remove from a JSON config file ---------------------------------------
remove_from_json_config() {
  local config_path="$1"
  local label="$2"

  if [[ ! -f "$config_path" ]]; then
    info "$label: config not found at $config_path — nothing to do."
    return
  fi

  if ! command -v jq &>/dev/null; then
    error "jq is required for uninstall. Install it first."
    exit 1
  fi

  if ! jq -e '.mcpServers["redfish-mcp"]' "$config_path" &>/dev/null; then
    info "$label: no redfish-mcp entry in $config_path — already clean."
    return
  fi

  if $DRY_RUN; then
    info "(dry-run) Would remove redfish-mcp from $config_path"
    return
  fi

  local updated
  updated="$(jq 'del(.mcpServers["redfish-mcp"])' "$config_path")"
  echo "$updated" | jq . > "$config_path"
  success "$label: removed redfish-mcp from $config_path"
}

# --- Remove from Claude Code CLI ------------------------------------------
remove_from_claude_cli() {
  if ! command -v claude &>/dev/null; then
    info "Claude Code CLI: 'claude' not found — skipping."
    return
  fi

  if $DRY_RUN; then
    info "(dry-run) Would run: claude mcp remove redfish-mcp"
    return
  fi

  info "Removing redfish-mcp from Claude Code CLI…"
  claude mcp remove redfish-mcp 2>/dev/null && \
    success "Claude Code CLI: redfish-mcp removed." || \
    warn "Claude Code CLI: redfish-mcp was not registered (or already removed)."
}

# --- Main ------------------------------------------------------------------
main() {
  echo ""
  echo -e "${BOLD}Redfish MCP — Uninstaller${NC}"
  echo -e "${BOLD}=========================${NC}"
  echo ""

  echo -e "${BOLD}[1/4] Cursor (user-level)${NC}"
  remove_from_json_config "$HOME/.cursor/mcp.json" "Cursor (user)"
  echo ""

  echo -e "${BOLD}[2/4] Cursor (workspace-level)${NC}"
  remove_from_json_config "$REPO_DIR/.cursor/mcp.json" "Cursor (workspace)"
  echo ""

  echo -e "${BOLD}[3/4] Claude Desktop${NC}"
  remove_from_json_config "$HOME/.claude/claude_desktop_config.json" "Claude Desktop"
  echo ""

  echo -e "${BOLD}[4/4] Claude Code CLI${NC}"
  remove_from_claude_cli
  echo ""

  success "Uninstall complete."
  echo ""
}

main
