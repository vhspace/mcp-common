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

# --- Dependency checks -----------------------------------------------------
check_jq() {
  if command -v jq &>/dev/null; then
    return 0
  fi
  error "'jq' is not installed. Install it first:"
  echo "  Ubuntu/Debian:  sudo apt-get install jq"
  echo "  macOS:          brew install jq"
  echo "  Fedora:         sudo dnf install jq"
  exit 1
}

check_uv() {
  if command -v uv &>/dev/null; then
    success "uv found: $(uv --version)"
    return 0
  fi
  warn "'uv' is not installed."
  if $DRY_RUN; then
    info "(dry-run) Would offer to install uv"
    return 0
  fi
  read -rp "Install uv now? [Y/n] " yn
  case "${yn:-Y}" in
    [Yy]*)
      info "Installing uv via official installer…"
      curl -LsSf https://astral.sh/uv/install.sh | sh
      export PATH="$HOME/.local/bin:$PATH"
      success "uv installed: $(uv --version)"
      ;;
    *)
      error "uv is required. See https://docs.astral.sh/uv/getting-started/installation/"
      exit 1
      ;;
  esac
}

# --- Config path detection -------------------------------------------------
detect_config_path() {
  local workspace_cfg=".cursor/mcp.json"
  if [[ -f "$REPO_DIR/$workspace_cfg" ]]; then
    echo "$REPO_DIR/$workspace_cfg"
    return
  fi
  echo "$HOME/.cursor/mcp.json"
}

# --- Build server entry JSON -----------------------------------------------
build_entry() {
  cat <<EOF
{
  "mcpServers": {
    "redfish-mcp": {
      "command": "uv",
      "args": ["--directory", "$REPO_DIR", "run", "redfish-mcp"],
      "env": {
        "REDFISH_SITE": "\${REDFISH_SITE:-default}"
      }
    }
  }
}
EOF
}

# --- Main ------------------------------------------------------------------
main() {
  echo ""
  echo -e "${BOLD}Redfish MCP — Cursor Installer${NC}"
  echo -e "${BOLD}===============================${NC}"
  echo ""

  check_jq
  check_uv

  local config_path
  config_path="$(detect_config_path)"
  info "Config path: $config_path"
  info "Repo directory: $REPO_DIR"

  local new_entry
  new_entry="$(build_entry)"

  if $DRY_RUN; then
    echo ""
    info "(dry-run) Would merge the following into ${config_path}:"
    echo "$new_entry" | jq .
    echo ""
    success "(dry-run) No changes made."
    return
  fi

  local config_dir
  config_dir="$(dirname "$config_path")"
  mkdir -p "$config_dir"

  if [[ -f "$config_path" ]]; then
    local existing
    existing="$(cat "$config_path")"

    if echo "$existing" | jq -e '.mcpServers["redfish-mcp"]' &>/dev/null; then
      warn "redfish-mcp entry already exists in $config_path — updating in place."
    else
      info "Merging redfish-mcp into existing config…"
    fi

    local merged
    merged="$(echo "$existing" | jq --argjson entry "$new_entry" '
      .mcpServers = (.mcpServers // {}) * $entry.mcpServers
    ')"
    echo "$merged" | jq . > "$config_path"
  else
    info "Creating new config at $config_path"
    echo "$new_entry" | jq . > "$config_path"
  fi

  success "Cursor MCP config updated: $config_path"
  echo ""
  echo -e "${BOLD}Next steps:${NC}"
  echo "  1. Restart Cursor (or reload the window)"
  echo "  2. The 'redfish-mcp' server should appear in MCP settings"
  echo "  3. Set REDFISH_SITE env var or configure .env per AGENTS.md"
  echo ""
}

main
