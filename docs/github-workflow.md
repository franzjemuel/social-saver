# GitHub engineering workflow

- Repository: private `franzjemuel/social-saver`; default branch `main`.
- Import provenance: `social-saver-bootstrap-v3.8.zip`, SHA-256
  `c9ef24b7ef600e565dafc5e2a6a12857b7d5ad8c189f9e269f2116b7fa5e7573`.
  The initial commit preserves source files and all migrations; generated Python
  and pytest caches were excluded.
- One concrete improvement per branch and PR. Read AGENTS, state and backlog first.
- CI: install, compile, tests, offline readiness, Compose validation, wheel
  installation outside the checkout, three image builds and service import smoke.
- Required checks should be `test` and `containers`, with stale approvals dismissed
  and force pushes/deletions disabled when repository plan capabilities permit.
- A green import/build gate is not a deployment gate. Database and provider
  integration tests must run separately on staging.
- Risky changes remain open for review. Production releases always need approval.
- Update PROJECT_STATE and BACKLOG in every engineering PR. Record failed and
  unavailable checks explicitly. Never represent static source assertions as live
  tenant-isolation or provider acceptance tests.

Packaging uses explicit package discovery because this application deliberately
contains several top-level Python packages. See the
[setuptools documentation](https://setuptools.pypa.io/en/stable/userguide/package_discovery.html).

## Account-plan limitation

GitHub rejected the private-repository branch-protection request with HTTP 403:
this account needs GitHub Pro (or a public repository) for that feature. The
repository remains private. The PR/check policy is currently procedural, not
server-enforced; do not claim protection is enabled.
