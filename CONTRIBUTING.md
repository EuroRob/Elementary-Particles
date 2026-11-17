# Contributing

Thank you for looking. Issues, questions, ideas and pull requests are all welcome.

## How this repository works

This repository is **generated** from a private homelab repository. A pull request here is
reviewed, then applied to the private repository by hand, and comes back in the next export.
Your commit will therefore not appear here as-is; you will be credited in the commit message.

## Good first contributions

- Questions where the docs were unclear: they show us what to write next.
- Making a role or module less specific to this homelab (variables instead of fixed values).
- New service particles (`services/<category>/<name>/service.yaml`) for software you run.
- Tests for code under `integration/`.

## Ground rules

- Never put a secret, token, password or personal detail in an issue or pull request.
- Keep one change per pull request, and say why in the description.
- Python: run the tests next to the code you change (`python3 -m pytest integration/`).

## Contributor License Agreement

Contributions need a signed Contributor License Agreement, [CLA.md](CLA.md). It lets the
maintainer keep the project available under more than one licence; you keep your copyright.

How it works: open a pull request, and the CLA bot comments with the text to post to sign
(one comment, once, for all your later pull requests). Until you have signed, the pull request
shows a failing `CLA` check and cannot be merged.
