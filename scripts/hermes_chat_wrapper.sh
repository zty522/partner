#!/bin/bash
# Wrapper: hermes_chat_wrapper.sh
# Avoids PIPE deadlock by using file redirection.
# Python subprocess handles timeout, not this script.
STDOUT_FILE="$1"
STDERR_FILE="$2"
shift 2
exec "$@" > "$STDOUT_FILE" 2> "$STDERR_FILE"
