# Security Policy

## Supported runtime

The maintained network-facing compatibility backend uses `apps/backend-compat/requirements.txt`. GitHub Actions installs and audits that environment before running backend tests.

## Historical paper environment

`apps/backend-compat/requirements-lock-py310.txt` is an immutable-style record of the Python 3.10.20 environment used for the paper experiments. It includes AllenSDK 2.16.2 and transitive versions required to reproduce the published computation. Some of those historical dependencies have subsequently acquired vulnerability advisories.

Use the historical lock only for isolated, offline reproduction with trusted local MRI/CCF data. Do not expose that environment as a network service. Reproducing the paper and running a maintained deployment are separate environment contracts.

## Reporting

Report security issues privately through the repository's GitHub Security Advisories page. Do not publish exploit details in a public issue before a fix is available.
