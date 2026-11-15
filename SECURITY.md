# Security

## Reporting

If you find a security problem in this code, **or anything in this repository that looks like a
real credential or personal detail that the sanitizing missed**, please do not open a public
issue. Use the repository's private vulnerability reporting ("Report a vulnerability"), or open
an issue that only says you have something to report privately, and you will be contacted.

You can expect an answer within a week.

## Design notes

- Secrets are never stored in git. Everything reads them from HashiCorp Vault at runtime
  (see `vault_helper.py`); files in this repository only name the Vault paths.
- Services are exposed through Traefik with Authelia in front by default (the `auth` tag).
- Network ranges and host names in the examples are the original homelab's private ones and are
  not reachable from the internet.
