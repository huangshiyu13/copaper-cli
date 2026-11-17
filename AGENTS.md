# Repository workflow

- Deliver completed work on `main`. Fetch the latest remote state before editing and preserve existing worktree changes.
- For each delivered change, update `src/copaper_cli/__init__.py`, the README version, and `CHANGELOG.md`. The package metadata, `cp-cli --version`, and request User-Agent all derive from `__version__`.
- Use semantic versions and annotated `vX.Y.Z` tags. Never move existing release tags, rewrite published history, or force-push.
- Follow `RELEASING.md`: run release validation, lint, tests, both 95% coverage gates, package builds, and public-tree scans before delivery. Do not lower coverage requirements or exclude runtime files.
- Commit and promptly push completed changes to `origin/main` and push their release tag. Verify the remote branch and tag before reporting completion; report a blocked push explicitly.
- This repository is intended for public use. Never include real keys, credential files, private machine paths, platform internals, operational scripts, logs, coverage reports, or user paper data. Use generated credentials and local fixtures in tests.
- Do not publish to a package registry or change repository visibility unless requested.
