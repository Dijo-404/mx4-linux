# Security reporting

MX4 Linux is in early development and version 0.2.0 is unreleased. Security fixes are considered against the current development branch; there is no maintained stable-release series or promised response time.

Do not publish vulnerabilities, credentials, sensitive configuration or device identifiers in public issues. If GitHub shows **Report a vulnerability** in this repository's Security tab, use that private reporting option. This document does not assume that private vulnerability reporting is enabled.

Otherwise, check the maintainer's [GitHub profile](https://github.com/Dijo-404) for current contact details and request a private reporting channel before sending sensitive details. If no private contact is listed, wait for one rather than placing the report in a public issue.

Once a private channel is established, provide the affected revision, environment, concise reproduction, impact and any proposed fix. Redact secrets and unrelated user data. Do not send proprietary installers or exploit payloads that operate on another person's device.

The application can write mouse settings and synthesize local desktop input. Imported Smart Actions can launch arbitrary configured commands. Review their contents before execution; validation checks structure and limits, not whether an application's behavior is trustworthy. Runtime device access uses the current user's permissions, and optional udev setup should remain separate from running the GUI.
