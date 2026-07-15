# Data boundaries

The repository separates runnable public examples from locally governed course
materials. A file being present on a developer machine does not mean it is
cleared for publication or eligible for a benchmark.

| Directory | Purpose | Public by default | Metric eligible |
|---|---|---:|---:|
| `public_sample/` | Original synthetic records for clone/CI/Docker smoke | yes | no |
| `eval/` | Small frozen engineering-validation fixtures | selected files only | engineering metrics only |
| `structured/` | Demonstration knowledge-point and alarm schemas/data | after review | engineering metrics only |
| `datasets/candidate/` | Source-extracted QA awaiting teacher review | no | no |
| `datasets/reviews/` | Human review audit records | no | no |
| `datasets/gold/` | Immutable teacher-accepted versions | only after rights/privacy review | yes |
| `active/` | Local operational corpus | no | no by itself |
| `candidate/`, `excluded/`, `archive/` | Raw intake and triage materials | no | no |
| `indexes/`, `processed/` | Rebuildable local artifacts | no | no |

The public image uses `public_sample/`. Operators mount an independently
reviewed corpus read-only and set `KNOWLEDGE_ROOT` for real use.

Teacher review and Gold freezing are documented in
`data/datasets/README.md`. Copyright clearance, teacher acceptance and privacy
review are separate gates; the tooling cannot grant any of them automatically.
