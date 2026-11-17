# Security

- Create a separate API Key for each device or automation. Give it only the permissions and projects it needs.
- Keys can be reviewed and revoked on your Copaper account's API Keys page. Revocation and expiration prevent new requests.
- Prefer hidden input (`cp-cli login --key`), stdin, or your CI secret store. A literal command-line key can appear in shell history or process listings.
- Credentials are stored outside projects, bound to the configured service URL, with owner-only POSIX permissions. Treat configuration backups as secrets. On Windows, keep the directory protected by your user account's access controls.
- The CLI requires HTTPS except for loopback development services, verifies TLS certificates, and never forwards credentials through redirects.
- Authentication uses the public API. It never imports browser cookies or executes downloaded project scripts.
- Synchronization excludes credentials, private keys, Git metadata and local bindings. Filesystem symlinks are rejected. File hashes and remote revision preconditions detect concurrent changes; default synchronization refuses overlapping changes.
- `--force` explicitly allows overwriting the selected side. Inspect `diff` and keep independent backups for important work.
- Verbose diagnostics contain request methods and endpoint paths, not request credentials or bodies. Terminal output redacts key-like strings.

Do not submit real keys, credential files, private papers or unsanitized diagnostics in an issue. If a key is exposed, revoke it on the website before reporting the incident. Review the report and remove personal file paths and project content before sharing it.

For a vulnerability, use the repository's private vulnerability reporting feature if available. If that feature is unavailable, contact the service administrator privately rather than posting a working exploit or credential in a public issue.
