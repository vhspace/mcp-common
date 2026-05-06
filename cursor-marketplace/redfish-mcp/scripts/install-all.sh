#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="$REPO_DIR/scripts"

# --- Colors ---------------------------------------------------------------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BOLD='\033[1m'; NC='\033[0m'

# --- Forward flags ---------------------------------------------------------
ARGS=()
for arg in "$@"; do
  case "$arg" in
    --dry-run|--help|-h) ARGS+=("$arg") ;;
    *) echo -e "${RED}Unknown option: $arg${NC}"; exit 1 ;;
  esac
done

for arg in "$@"; do
  if [[ "$arg" == "--help" || "$arg" == "-h" ]]; then
    echo "Usage: $(basename "$0") [--dry-run]"
    echo "  Runs install-cursor.sh and install-claude-code.sh"
    exit 0
  fi
done

echo ""
echo -e "${BOLD}Redfish MCP — Install All${NC}"
echo -e "${BOLD}=========================${NC}"
echo ""

cursor_ok=true
claude_ok=true

echo -e "${BOLD}>>> Cursor${NC}"
echo "─────────────────────────────────"
"$SCRIPTS_DIR/install-cursor.sh" "${ARGS[@]}" || cursor_ok=false
echo ""

echo -e "${BOLD}>>> Claude Code / Claude Desktop${NC}"
echo "─────────────────────────────────"
"$SCRIPTS_DIR/install-claude-code.sh" "${ARGS[@]}" || claude_ok=false
echo ""

echo "═══════════════════════════════════"
echo -e "${BOLD}Summary${NC}"
echo "═══════════════════════════════════"
if $cursor_ok; then
  echo -e "  Cursor:        ${GREEN}✔ OK${NC}"
else
  echo -e "  Cursor:        ${RED}✖ FAILED${NC}"
fi
if $claude_ok; then
  echo -e "  Claude:        ${GREEN}✔ OK${NC}"
else
  echo -e "  Claude:        ${YELLOW}⚠ PARTIAL${NC} (check output above)"
fi
echo ""
