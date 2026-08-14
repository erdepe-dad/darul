# Security Policy

## Supported versions

Until Darul reaches a stable release, security fixes target the latest `0.1.x` revision on the default branch. Older snapshots may not receive backports.

## Reporting a vulnerability

Do not open a public issue for suspected vulnerabilities or credential exposure.

Use GitHub's private vulnerability-reporting flow:

https://github.com/erdepe-dad/darul/security/advisories/new

Include:

- Affected version or commit
- Impact and realistic attack scenario
- Reproduction steps or proof of concept
- Relevant configuration with all secrets redacted
- Suggested mitigation, if known

Maintainers should acknowledge a report within seven days. Timing for validation, fixes, and disclosure depends on severity and reproducibility. Please allow a reasonable remediation period before public disclosure.

## Security boundaries

- The parser reads repository source files locally.
- Neo4j credentials remain in the Python server process.
- The visualization API is read-only but does not include authentication.
- LAN binding exposes repository structure and decision data to reachable clients.
- Cloudflare or another reverse proxy is responsible for remote identity enforcement.
- The event logger can persist prompts, paths, tool payloads, and other sensitive session data.
- Neo4j named-volume persistence protects against container replacement, not disk loss, compromise, or operator deletion.

## Secret exposure response

If a credential is committed or published:

1. Revoke or rotate it immediately; deleting the file is not sufficient.
2. Remove it from the current tree.
3. Rewrite Git history if necessary.
4. Invalidate cached artifacts, releases, container images, and logs that contain it.
5. Document the affected scope privately before disclosure.

Run `scripts/public-release-audit.py` before publishing changes. The audit is a defense-in-depth check and does not replace a dedicated secret scanner or human review.
