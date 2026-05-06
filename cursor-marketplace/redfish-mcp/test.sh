#!/bin/bash
# Quick test script for Redfish MCP server
# Uses credentials from ~/.oh-my-zsh/custom/together.zsh

set -e

cd "$(dirname "$0")"

# Source credentials
if [ -f ~/.oh-my-zsh/custom/together.zsh ]; then
    source ~/.oh-my-zsh/custom/together.zsh
    export REDFISH_IP=192.168.196.54
    export REDFISH_USER=$ORI_REDFISH_USER
    export REDFISH_PASSWORD=$ORI_REDFISH_PASSOWRD
else
    echo "Warning: ~/.oh-my-zsh/custom/together.zsh not found"
    echo "Set REDFISH_IP, REDFISH_USER, REDFISH_PASSWORD manually"
fi

echo "Running tests..."
echo "Target: ${REDFISH_IP:-not set}"

# Run all tests
.venv/bin/pytest tests/test_unit.py tests/test_mcp_tools.py -v

# Run integration tests if credentials are available
if [ -n "$REDFISH_IP" ] && [ -n "$REDFISH_USER" ] && [ -n "$REDFISH_PASSWORD" ]; then
    echo ""
    echo "Running integration tests against $REDFISH_IP..."
    .venv/bin/pytest -m integration -v
else
    echo ""
    echo "Skipping integration tests (no credentials)"
fi
