# scribe4jira

A custom Fullsend agent that turns meeting notes into Jira Cloud issue proposals
and comments. The model produces structured JSON; host scripts validate it and
apply confidence, content, project-scope, and duplicate checks before Jira writes.

This repository contains source code and synthetic examples only. Run real meeting
and Jira processing in a private deployment project. Fullsend transcripts contain
the input context, so keep CI logs and artifacts private. Never run production
meeting processing in a public GitHub Actions workflow.

## Use from another repository

Register the harness in the consumer's `.fullsend/config.yaml` using an immutable
commit and the SHA-256 of the harness file:

```yaml
version: "1"
agents:
  - name: scribe
    source: https://raw.githubusercontent.com/ascerra/scribe4jira/<commit-sha>/.fullsend/harness/scribe.yaml#sha256=<harness-sha256>
allowed_remote_resources:
  - https://raw.githubusercontent.com/ascerra/scribe4jira/
```

Fullsend fetches the complete companion scripts directory, including the Python
package and pinned dependencies. No authenticated GitHub checkout is required.
Use a current Fullsend version that composes resources relative to a registered
remote harness. The GitLab test uses the consumer project’s existing source-built
Fullsend installer. Create a lock file before running:

```sh
fullsend lock scribe --forge gitlab --fullsend-dir .fullsend --update
fullsend run scribe --forge gitlab --fullsend-dir .fullsend --target-repo . --output-dir output
```

Use the consumer project's existing Fullsend runner and sandbox setup. Run only
from a protected branch. Disable CI debug tracing. Configure the following host
environment values through private CI settings; do not commit real values:

- `JIRA_SERVER`, `JIRA_EMAIL`, `JIRA_PROJECT_KEY`
- `JIRA_API_TOKEN_FILE`: a protected File variable containing the Jira token
  (`JIRA_API_TOKEN` is also supported for existing deployments)
- `JIRA_DEFAULT_ISSUE_TYPE`, `JIRA_DEFAULT_PRIORITY`: valid target-project names
- `SCRIBE_WORKSPACE_DIR`: a private temporary directory outside CI artifacts
- `SCRIBE_DRY_RUN=true`: preview without writing; set `false` for an authorized run
- `SCRIBE_MAX_NEW_ISSUES=1`: optional host-side cap for a controlled single-issue test
- `SCRIBE_MODE=new_issues_only`: useful for an isolated new-issue proof
- `SCRIBE_NOTES_SOURCE=file` and `SCRIBE_NOTES_FILE`: a local note file, or
  `SCRIBE_NOTES_SOURCE=drive` plus `SCRIBE_DRIVE_CREDENTIALS` and a search query
- `GOOGLE_APPLICATION_CREDENTIALS`: host path to the inference credential JSON
- `GCP_OIDC_TOKEN_FILE`: host path to the GitLab OIDC token file; set an empty value
  when the inference credentials do not use OIDC
- `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`: the inference deployment values

Drive credentials are used only by the host-side fetcher. Jira credentials remain
on the host. The sandbox receives inference credentials and the scrubbed meeting
and Jira context. Configure `JIRA_USE_SERVICE_ACCOUNT` and `JIRA_CLOUD_ID` only when
the Jira account requires Atlassian's gateway routing.

## Local development

```sh
python3 -m venv .venv
.venv/bin/pip install -r .fullsend/scripts/requirements.txt -r requirements-dev.txt
.venv/bin/pytest
```

The canonical Python package lives in `.fullsend/scripts/scribe4jira` so it travels
with a remotely fetched script bundle. The schema lives in the package's `data`
directory. Tests use synthetic credentials, notes, and Jira responses.

There is no open-source license granted by this snapshot. Select an appropriate
license with the source owners before distributing it as licensed open source.
