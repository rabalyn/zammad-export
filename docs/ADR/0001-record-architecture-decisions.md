# 0001 – Record architecture decisions

- Status: accepted
- Date: 2026-10-14

## Context

Several people will use and maybe maintain this archive. Decisions about personal
data in particular must be traceable later ("why do we keep field X?").

## Decision

Every relevant decision is a short Markdown file in `docs/ADR/`, numbered, using
[template.md](template.md). Decisions are not edited after the fact. A changed decision gets
a new ADR that supersedes the old one, or a dated "Decision update" section at the end.

## Consequences

- The `README.md` in this folder is the index and also holds the implementation plan.
- Privacy decisions (ADR 0005) are documented in a way we can show to the data protection officer.
