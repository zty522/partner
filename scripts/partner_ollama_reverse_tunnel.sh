#!/usr/bin/env bash
set -euo pipefail

# Expose this computer's local Ollama to the remote Partner server.
#
# Usage:
#   scripts/partner_ollama_reverse_tunnel.sh hermes-tiOA
#   scripts/partner_ollama_reverse_tunnel.sh ubuntu@203.0.113.10
#
# Requirement:
#   Ollama is running on this computer at 127.0.0.1:11434.
#
# Behavior:
#   Remote server gets 127.0.0.1:11434 forwarded to this computer's Ollama.
#   If this laptop sleeps/powers off/network drops, Partner falls back to its
#   primary agent backend and records Ollama as unavailable.

REMOTE="${1:-hermes-tiOA}"
REMOTE_PORT="${PARTNER_REMOTE_OLLAMA_PORT:-11434}"
LOCAL_HOST="${PARTNER_LOCAL_OLLAMA_HOST:-127.0.0.1}"
LOCAL_PORT="${PARTNER_LOCAL_OLLAMA_PORT:-11434}"

echo "Forwarding remote 127.0.0.1:${REMOTE_PORT} -> ${LOCAL_HOST}:${LOCAL_PORT}"
echo "Keep this terminal open. Press Ctrl-C to stop the tunnel."

exec ssh \
  -N \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=3 \
  -R "${REMOTE_PORT}:${LOCAL_HOST}:${LOCAL_PORT}" \
  "${REMOTE}"
