#!/bin/sh
set -e

if [ "${TRANSPORT}" = "http" ]; then
    exec uvicorn netbox_mcp.server:create_app \
        --factory \
        --host "${HOST:-0.0.0.0}" \
        --port "${PORT:-8000}"
else
    exec netbox-mcp "$@"
fi
