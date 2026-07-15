# Public release checklist

The repository is not public-release ready until every required item below is
verified. A checked item needs machine-readable or reviewer evidence; absence
of a finding is not copyright clearance.

## Included in the public repository

- Application code, tests, configuration templates and documentation.
- Synthetic/de-identified examples in `data/public_sample`.
- Dataset schemas, candidate-review tooling and small evaluation fixtures that
  have passed a separate content review.
- Selected reports that contain no secret, personal data or local absolute path.

## Excluded by default

- `data/active`, `data/candidate`, `data/excluded`, model indexes and databases.
- School courseware, question banks, vendor manuals and the original task brief.
- API keys, `.env`, model caches, traces containing user content and raw bad cases.

## Required gates

- [ ] Teacher/copyright owner approves every public evaluation fixture and Gold item.
- [ ] `python scripts/audit_public_release.py --strict` passes on the staged tree.
- [ ] Secret scanning and personal-data review pass independently.
- [ ] GitHub Actions test, coverage and Docker jobs pass on a clean clone.
- [ ] Docker Compose build/up, `/health`, `/ready`, three task demonstrations,
      persistent-volume restart and recovery are recorded on a Docker host.
- [ ] Repository visibility, owner, license wording and publication name are confirmed.
- [ ] No single model smoke is described as a quality benchmark.

## Current blocker

The local development machine did not have the Docker CLI during the v0.4.0
review. The workflow therefore builds and health-checks the portable image on
GitHub Actions, but Compose persistence and restart acceptance still require a
real Docker host.

Run the complete Docker gate on that host with:

```powershell
python scripts/accept_docker.py
```

The command writes a machine-readable report and returns a non-zero exit code
when any build, readiness, three-task or persistence check fails.
