#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "" ]]; then
  echo "Usage: $0 ENV_VAR [ENV_VAR ...]"
  exit 1
fi

echo "Checking 1Password CLI availability..."
if ! command -v op >/dev/null 2>&1; then
  echo "  FAIL: 'op' CLI not found on PATH"
  exit 1
fi
echo "  OK: op CLI found"

echo "Checking 1Password session/service-account state..."
if op whoami >/dev/null 2>&1; then
  echo "  OK: authenticated session detected"
else
  echo "  WARN: no authenticated session (service account token may still work)"
fi

missing=0
echo "Checking required environment variables..."
for var in "$@"; do
  value="${!var:-}"
  if [[ -z "${value// }" ]]; then
    echo "  FAIL: $var is missing"
    missing=1
  else
    echo "  OK: $var is set"
  fi
done

if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

echo "All checks passed."
