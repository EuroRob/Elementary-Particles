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

By submitting a contribution you confirm that:

1. You wrote it, or have the right to submit it, and it is not subject to terms that conflict
   with this agreement.
2. You license it under the AGPL-3.0 **and** grant Rob Carroll (and any company
   they assign the project to) a perpetual, worldwide, non-exclusive, royalty-free, irrevocable
   license to use, modify, sublicense and distribute it under other terms, including
   commercial licenses.
3. You keep the copyright of your contribution and may use it in any way you like.

Add this line to your pull request description to agree:

> I have read the Contributor License Agreement in CONTRIBUTING.md and agree to it.
