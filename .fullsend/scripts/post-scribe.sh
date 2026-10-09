#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
: "${FULLSEND_VALIDATED_ITERATION_DIR:?A validated Fullsend iteration is required}"
RESULT_FILE="${FULLSEND_VALIDATED_ITERATION_DIR}/output/agent-result.json"
if [[ ! -f "$RESULT_FILE" ]]; then
  RESULT_FILE="${FULLSEND_VALIDATED_ITERATION_DIR}/agent-result.json"
fi
"$SCRIBE_PYTHON" -m scribe4jira.fullsend post "$RESULT_FILE"
