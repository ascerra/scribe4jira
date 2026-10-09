# Repository guidance

Keep this repository safe for public access. Never commit real credentials,
deployment identifiers, meeting notes, Jira backlog exports, run transcripts,
logs, artifacts, or configuration copied from an internal deployment.

Keep the canonical Python package under `.fullsend/scripts/scribe4jira`. Remote
Fullsend consumers fetch the entire scripts directory. Resolve companion files
relative to the script, not the consumer checkout. Keep one canonical schema under
the package's `data` directory.

Keep Jira credentials on the host. Preserve dry-run defaults, strict boolean
parsing, schema validation, and target-project checks. Test security changes with
synthetic data. Run pytest and a secret scan before publishing changes. Hosted
integration tests belong in a private deployment project.
