# Failures the agent has met

One small JSON file per failure: what happened, why, what fixed it, which tests keep it fixed, and what a
**new project should do from the start** so it never happens there. Read `prevention` first when starting
another project of this kind; the other fields are the evidence behind it.

| Field | Says |
|---|---|
| `id`, `date` | `fNNN`, the day it was met (YYYY-MM-DD) |
| `class` | the root cause as a class, not the instance |
| `symptom`, `site` | what the owner saw, and where |
| `root_cause` | why it happened |
| `why_tests_missed` | why the existing tests did not catch it |
| `fix` | what changed |
| `tests` | test files (or `file::test_name`) that cover the whole class |
| `prevention` | what to build in from day one |

Every bug-fix pull request adds its own file here (`CLAUDE.md` section 7). `tests/test_failures_catalogue.py`
checks each entry is complete and names tests that exist. One file per failure, so two branches never
edit the same file.

The questions the agent could not answer live beside the answer library, not here: see
`data/unanswered_questions.json` and `data/profile_answers.json` (local, not committed).

To take both to the next project as one file, run `python next_project_knowledge.py`: it writes
`data/next_project_knowledge.json` (local, because the questions name the employers applied to) with every
failure here, each one's `prevention` on its own line to read first, and every question the agent left, most
often asked first, each with the `answer_key` line that answers it. The answers themselves stay in
`data/profile_answers.json`.
