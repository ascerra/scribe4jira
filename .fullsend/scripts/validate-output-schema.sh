#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
: "${FULLSEND_OUTPUT_SCHEMA:?Fullsend must supply the resolved schema}"
RESULT_FILE="output/$(basename -- "${FULLSEND_OUTPUT_FILE:-agent-result.json}")"
"$SCRIBE_PYTHON" -m scribe4jira.fullsend validate "$RESULT_FILE" "$FULLSEND_OUTPUT_SCHEMA"
