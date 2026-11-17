# Contributing

Use Python 3.10 or newer and an isolated virtual environment. Install `.[dev]` and run the lint, test, coverage and package checks in the README before proposing a change.

Deliver changes on `main`, update the source version and `CHANGELOG.md`, and promptly push the completed commit and its annotated version tag. Follow [RELEASING.md](RELEASING.md); never rewrite published tags or force-push.

The client depends only on the documented public HTTP API. Keep service internals, deployment details, personal machine paths, credentials, logs and paper data out of this repository. Tests must use generated keys, temporary directories and local fixtures. Live-service checks belong in private operational tooling, not checked-in tests.

All runtime modules must remain in coverage measurement. Both statement and branch coverage must stay at least 95%; add meaningful regression tests for new behavior and failures. Do not add coverage exclusions to bypass the gate.

Document user-facing commands and API changes. Test dry runs, permission failures and conflict handling for mutations. Do not automatically follow redirects with credentials, bypass certificate validation, introduce telemetry, or silently overwrite concurrent edits.

Build a wheel and source archive and run `tools/check_public_tree.py --artifacts dist` before distributing them. The MIT license covers original client code; preserve attribution when incorporating third-party material.
