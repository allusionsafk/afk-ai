# AFK LocalAI security policy

AFK AI is Friend Beta software. Please report security or privacy problems that
could affect the installer, local service boundary, diagnostics, updates,
download integrity, or user data.

## Report privately

Use GitHub private vulnerability reporting:

https://github.com/allusionsafk/localai-windows-starter/security/advisories/new

> [!CAUTION]
> Do not publish exploit details, credentials, private logs, chat content,
> documents, `.env` values, tokens, cookies, or other sensitive material in a
> normal GitHub issue.

## In scope

Examples include:

- bypasses of installer or release integrity checks
- local services becoming reachable outside the intended local network boundary
  without explicit opt-in
- credential, secret, prompt, chat, document, or file-content leakage
- command injection or unsafe privilege changes
- unexpected system modification
- unsafe update or dependency behaviour
- privacy documentation that does not match observed network behaviour

## What to include

Please provide:

1. AFK AI tag, commit, or branch
2. Windows version
3. only hardware details relevant to the issue
4. exact reproduction steps
5. expected behaviour
6. observed behaviour
7. security or privacy impact
8. a sanitised diagnostic excerpt when necessary

Before sharing logs or screenshots, remove usernames, private paths, unrelated
host details, credentials, tokens, cookies, API keys, prompts, chats, and
documents.

## Version scope

The current public Friend Beta is `v0.1.7rc1`. The repository also contains an
unpublished `v0.2.0-rc1` source candidate. Development on `master` can move ahead
of either record, so every report should identify the exact tag or commit tested.

For installation, hardware, or ordinary product bugs, use
[SUPPORT.md](SUPPORT.md).
