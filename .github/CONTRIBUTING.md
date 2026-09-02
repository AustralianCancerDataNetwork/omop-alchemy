# Contributing

## Development setup

```bash
uv sync --all-extras --dev
uv run pytest -q
uv run ruff check .
```

## Ownership boundaries

Before adding general database or ORM infrastructure, check whether it belongs in a lower-level dependency. `orm-loader` owns domain-independent loading, serialization, and materialized-view lifecycle mechanics. OMOP Alchemy owns OMOP table models, clinical semantics, and the OMOP-specific selectables and row grains that consumers can pass to that infrastructure.

Do not add materialized-view DDL, lifecycle helpers, or orchestration to `omop_alchemy`. A consuming application owns its view registry, dependency and rebuild policy, and command-line or deployment workflow. 

## Opening a pull request

1. Apply **exactly one** label before merging:

   | Label | When to use |
   |---|---|
   | `breaking` | Public API change, backward-incompatible |
   | `feature` | New functionality, backward-compatible |
   | `fix` | Bug fix |
   | `dependencies` | Dependency version update |
   | `chore` | CI changes, refactoring, test additions, docs — anything that does not affect the public-facing package. Bypasses the label gate; excluded from the changelog and does not bump the version. |

2. When merging (squash), write a clear extended description in the merge dialog. That text — not the PR's opening description — becomes the changelog entry for this change. Leave it blank for `chore` PRs.

## Versioning and releases

Versions are derived from git tags; there is no version string in any source file. Releases are triggered by a maintainer publishing the standing draft release on the repository's Releases page. There is no automated commit-back to `main`.
