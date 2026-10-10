# Contributing

Start from current main, create a focused branch and open a PR. Do not merge or deploy through an unreviewed change. Preserve secret scanning, pytest, Sonar Quality Gate, Trivy gates, immutable image identity, explicit AWS approvals and exact rollback identity checks.

Use fake inventory from tests/fixtures; never copy live secrets or AWS IDs into fixtures, screenshots or PRs. Run pytest, ShellCheck, Bash/Python syntax validation and relevant integration tests with disposable local resources. Review generated diffs and scanner reports privately before publishing a redacted summary.

Dependency/base-image updates require provenance review, lock regeneration, vulnerability scans and two clean same-platform builds. Changes to IAM, bootstrap, source-template inventory or release verification require security review and regression coverage. Do not modify live capacity/RDS/secrets as part of repository work.

No LICENSE has been chosen. Obtain the owner's licensing decision before adding legal terms or claiming this repository is licensed open source.
