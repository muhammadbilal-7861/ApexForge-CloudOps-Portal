# Security reporting

Do not post credentials, raw scanner output, runtime files or exploit details in public issues. Use the repository's private GitHub vulnerability reporting channel if the owner has enabled it; otherwise contact the owner privately through their GitHub profile to establish a secure channel. Private reporting and a response SLA are not yet configured.

Report affected commit/file, reproduction using synthetic data and impact. Never test the original AWS deployment or use discovered credentials. All examples are development-only. This learning/reference repository is not certified for production.

If a credential has appeared in public Git history, revoke/rotate it first, investigate access and coordinate history cleanup with the owner and all contributors. Removing a current file is insufficient. History cleanup requires explicit owner approval and a migration plan for clones/forks/caches; do not force-push automatically.

Read [the redacted audit and remaining risks](docs/SECURITY-AUDIT.md). Restrict runtime files to root:root 0600, CI reports to authorized Jenkins users and inventory to a private controlled location. Do not upload raw user data, Docker inspect environment dumps or full SSM output.

For a repeatable local history audit, install reviewed official [Gitleaks](https://github.com/gitleaks/gitleaks) and [TruffleHog](https://github.com/trufflesecurity/trufflehog) releases, fetch all authorized refs, then run `bash deploy/audit-history.sh`. It scans all reachable blobs, disables live credential verification, retains raw output privately under a temporary directory and prints aggregate counts only. Review findings privately; delete private audit artifacts only after triage.
