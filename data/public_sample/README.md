# Public sample corpus

This directory contains small, original demonstration records for cloning,
CI and Docker smoke tests. It does not contain school courseware, manuals or
student data, and it must not be used as an authoritative robot manual.

Operators can mount a reviewed local corpus and set `KNOWLEDGE_ROOT` to that
read-only directory.

`synthetic_classroom_v1/` contains ten deterministic, original mini-lessons
used by `rag_synthetic_180_v1.csv`. The questions simulate student wording and
the expected labels come from a deterministic specification. No row is a real
student record, no label is a human-teacher decision, and the resulting metrics
are engineering-only rather than Gold or production-quality evidence.
