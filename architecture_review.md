# Architecture review: menu against the "good Python application in 2026" checklist

**Date:** 2026-10-12 · **Commit:** `81409ff` (`main`) · **Scope:** `menu/` (7,752 lines, 205 functions) and `tests/` (8,996 lines, 415 tests)

Every finding below was measured on this commit. The import graph, function sizes and coverage came from scripts run against the tree; the rest from reading the files cited.

## Verdict

Menu meets most of the checklist and is strong where it matters most for this repo: typing, tests on a real database, deterministic tooling, and the LLM guardrails. Its weakest point is architectural: the three top-level packages depend on each other in a cycle, and transaction boundaries are opened in two different styles. Both will make the planned move into food-guru harder than it needs to be. Two checklist items (async I/O, free-threading) don't fit this application, and adopting them would be a mistake. Explaining why is part of this review.

| Principle | Status | One-line reason |
|---|---|---|
| Structured monolith with domain boundaries | **Partial** | One deployable, but `db` ↔ `ingest` ↔ `nutrition` import each other (cycle) |
| Small single-purpose functions | **Mostly** | 83% of functions ≤ 30 lines; 5 exceed 100 |
| Explicit over implicit | **Partial** | Import-time engine/HTTP session; tests swap module globals |
| Ruff / PEP 8 | **Yes, redundantly** | Ruff lints, but black + isort still run beside it |
| Type hints everywhere | **Yes** | Strict mypy on `menu` + `tests`, zero `type: ignore` |
| Defensive error handling | **Yes** | 6 broad catches, all at per-item boundaries, logged and counted |
| Async I/O | **Not applicable** (deliberate) | Single-process polite crawler and batch CLIs |
| Modern runtime | **Yes** | Python 3.14; free-threading irrelevant here |
| uv / deterministic deps | **Mostly** | uv + lockfile, but CI doesn't enforce the lock |
| Testing + CI | **Yes, with gaps** | 415 tests, 87% coverage, real Postgres 18; no coverage gate |
| AI integration and guardrails | **Strong** | Models only write reviewable suggestions; two gaps |

## Findings

### 1. The package structure is a cycle, not layers *(high)*

The package-level import graph:

```
menu.db        -> menu.ingest, menu.nutrition
menu.ingest    -> menu.db, menu.nutrition
menu.nutrition -> menu.db, menu.ingest
```

Every package depends on every other, so none can be understood, tested or moved on its own. The causes are specific:

- **Pure domain logic lives under `ingest`, and the persistence layer reaches up for it.** `db/ingredients/grams.py`, `aggregate.py` and `seed.py` import `ingest.units`; `lines.py` and `resolve.py` import `ingest.ingredients`. Units and line parsing are domain rules, not ingest steps.
- **Orchestrators live in `db`.** `db/rebuild.py` imports the crawl pipeline, all three reference importers and nutrition derivation. `db/metrics.py` imports `nutrition.reports`. These are application-level jobs filed under persistence.
- **The LLM boundary crosses both ways.** `ingest/llm_review.py` imports `db.ingredients.suggestions.Suggestion`, while `db/ingredients/review_inputs.py` imports `ingest.llm_review` (`ReviewItem`, `FoodCandidate`) and `ingest.embeddings`.

**Recommendation:** make dependencies one-way, in four layers:

```
domain   (pure: units, line parsing, grams math, suggestion/review types)
  ↑
db       (models, repositories, seeding; imports domain only)
  ↑
services (crawl pipeline, nutrition, review inputs, LLM client, rebuild, metrics)
  ↑
cli      (argument parsing, printing, opening sessions)
```

This is mostly moving files, not rewriting them. Because AGENTS.md says menu mirrors food-guru's layout so that code graduates "with minimal rework", map the layers onto food-guru's existing split (`backend/app/models`, `backend/app/services`, `backend/app/api` / scripts) instead of inventing new names. Add an import-direction check, either a small pytest that walks the AST or `import-linter`, so the cycle can't come back.

### 2. Transaction boundaries are opened in two styles *(high)*

28 places open their own session with `with get_session():`, spread across 13 modules. They include library functions such as `nutrition/reports.py` (4), `nutrition/derive.py` (2), `db/ingredients/reports.py` (2), `aggregate.py` and `resolve.py`. Another 26 functions take a `session: Session` from the caller. A function that opens its own session can't be combined with others in one transaction, and its tests have to swap the global default engine (`tests/conftest.py` monkeypatches `menu.db.connection.default_engine`) instead of passing a session in.

**Recommendation:** services take a `Session`; only CLI entrypoints (and `rebuild`, as a top-level job) open one. This is food-guru's own pattern (request-scoped sessions passed down), so it also lowers the graduation cost.

### 3. Some functions are still large *(medium)*

Of 205 functions, 170 (83%) are 30 body lines or fewer, 22 are 31–60, 8 are 61–100, and 5 exceed 100 lines:

| Function | Body lines | What to split |
|---|---|---|
| `db/ingredients/seed.py::seed_ingredient_data` | 260 | One function per seed kind: terms, not-food, conversions, food refs, rejections, derived conversions |
| `ingest/pipeline.py::main` | 150 | 12 flags in one if-chain. Use argparse subcommands with one handler each (`crawl`, `reparse`, `re-resolve`, `suggest`, `report …`) |
| `db/ingredients/aggregate.py::aggregate` | 134 | Grouping, summing and the cross-dimension fold are three steps |
| `db/ingredients/lines.py::store_recipe_ingredients` | 133 | Per-line parse → resolve → grams → row construction |
| `nutrition/derive.py::derive_nutrition` | 104 | Phase 6 rewrites this anyway (set-based derivation) |

`seed.py` (730 lines) also mixes curated data (`ALIASES`, `FDC_IDS`, `FOOD_REFS` …) with seeding logic. Moving the data into its own module (e.g. `seed_data.py`) keeps the reviewed-in-git lists apart from the code that applies them.

### 4. Hidden state and import-time side effects *(medium)*

- `menu/db/engine.py:32` builds the engine at import time from `DATABASE_URL`, and `menu/ingest/http.py:23` builds a module-global `requests.Session`. Importing a module has effects, and configuration is frozen at import.
- Tests change behaviour by monkeypatching module globals: `connection.default_engine`, `suggestions.SUGGESTIONS_PATH`, `review_inputs.EMBEDDINGS_DIR`, and the `seed` lists. Several docstrings exist only to explain this ("resolved at call time so tests can patch it", in `connection.py`, `suggestions.py`, `fetch.py`, `database.py`). The seam works, but it's implicit: a reader can't see the dependency in the signature.
- `ingest/fetch.py:52` `_politeness` and the robots cache are module state **on purpose**: the per-host delay must be process-wide, and AGENTS.md documents it. Keep these.
- Good counter-example already in the code: `connection.use_engine()` is a `ContextVar` override that is explicit, scoped and typed. Prefer that shape, or plain parameters, for new seams.

**Recommendation:** create the engine and HTTP session lazily (a cached factory) and pass dependencies such as paths, embedders and clients as parameters with defaults. `suggest_command` already does this with its embedder and client factories, so extend that to the remaining seams.

### 5. Error handling meets the bar *(no action)*

There are six `except Exception` blocks (`pipeline.py` ×4, `lines.py` ×2). Each sits at a per-item boundary (a URL, a recipe, a sitemap, an ingredient line), carries a reason in its `noqa`, logs the exception type, records the outcome on the ledger row or the line's `parse_status`, and moves on. AGENTS.md states this rule ("bad URLs don't stop the crawl"). Elsewhere errors are specific: `ingest/errors.py` has a hierarchy (`FetchError`, `BlockedError`, `DisallowedError`, `NotCachedError`), and the LLM client raises `LLMError` and catches only it and `ValueError`. Ruff's `BLE` rule enforces the pattern. This is the checklist's "catch at boundaries" done right.

### 6. Typing is strict; payload shapes are not *(low)*

`mypy --strict` passes over both `menu` and `tests` with **zero** `type: ignore`. The remaining looseness is 68 uses of `Any`, mostly at genuine boundaries: JSON-LD recipe payloads (`Recipe.data: dict[str, Any]`), CSV rows in the importers, and JSON metrics. One internal shape deserves a type: `Suggestion.proposal: dict[str, Any]` has six fixed shapes (alias, variant, line_override, not_food, food_ref, ok), which are validated by hand in two places (`llm_review._proposal`, `seed._proposal`). A pydantic discriminated union on `kind` would replace both checks with one validated model and make malformed accepted entries impossible to load.

### 7. Tooling: one formatter too many, lock not enforced *(low)*

- Ruff lints (including `I`, the isort rules), yet CI and pre-commit also run **black** and **isort**. This is three tools for two jobs, and their configs can drift: it happened twice this week, when black-only wrapping broke a ruff `noqa`. Replace both with `ruff format` and ruff's `I` rules. Food-guru is moving the same way, so this keeps the mirror.
- CI runs `uv sync`, not `uv sync --locked`, so a `pyproject.toml` change without a matching `uv.lock` passes CI. Use `--locked`.
- Pre-commit runs the full pytest suite on every commit. Since the Postgres move that's about 40 s and needs Docker. Keep the suite in CI and drop it, or narrow it, in pre-commit.
- The GitHub Actions are pinned by tag (`@v4`, `@v5`), not by commit SHA. That's acceptable for a lab, but worth pinning before this graduates.

### 8. Async: correctly absent *(no action now)*

There is no `async` code, and that's right for this application. The checklist's "embrace async I/O" targets high-concurrency servers. Menu is a batch CLI whose crawl is **deliberately single-process and sequential**, so that the per-host politeness delay and robots.txt rules hold (AGENTS.md, "Scraping rules"). Concurrency would only make it ruder to the sites it crawls. The LLM run is a few dozen sequential chunks.

The real issue is graduation: food-guru is async (asyncpg). Keep I/O at the edges (fetch, DB session, LLM client) so that porting is a mechanical change of those adapters. Findings 1 and 2 are what make that possible.

### 9. Runtime: modern, free-threading irrelevant *(no action)*

`requires-python = ">=3.14"`, and the code uses current syntax: `X | None` and PEP 695 generics in tests. Free-threaded Python helps CPU-bound multi-threaded work. Menu is I/O-bound and single-process by design, so there is nothing to gain.

### 10. Tests and CI: strong, with measurable gaps *(medium)*

What's there:
- 415 tests and 87% line coverage.
- Real Postgres 18 via one testcontainers instance per session, with a template-cloned database per test.
- Saved HTML fixtures, so tests never touch live sites.
- Fakes for the LLM client and embedder, so tests make no network calls and download no models.
- Strict pytest with warnings treated as errors.
- CI on every push and PR.

Gaps:

| Module | Coverage |
|---|---|
| `ingest/pipeline.py` | 71% (the CLI dispatch, untested branches) |
| `nutrition/sources/fdc.py` / `cnf.py` / `cofid.py` | 77% / 79% / 83% |
| `ingest/embeddings_eval.py`, `ingest/__main__.py` | 0% |

There's no coverage floor in CI, so coverage can only drift down. Add `--cov-fail-under=85`. Splitting `main` into subcommands (finding 3) also makes the CLI testable per command.

### 11. AI integration: the strongest part of the design *(two gaps)*

What's right:
- **Models never write resolution.** Every LLM verdict lands as a `pending` entry in `suggestions.json`, which is reviewed as a git diff. Only entries set to `accepted` become seeds, and hand seeds win conflicts.
- **Models produce no numbers.** The plan forbids it, and the mechanical checks enforce it before a suggestion is kept:
  - numbers must appear in the raw line;
  - targets must be known concepts;
  - food-ref picks must be among the offered candidates;
  - the kind must be allowed for that input set;
  - a contradictory "none fits" that still names a candidate is dropped.
- **The client is behind a protocol** (`LLMClient`, `Embedder`). Responses use strict JSON schemas, the token ceiling scales with the chunk, retries back off on 429/5xx with a capped Retry-After, and the key comes from the environment only.
- **Embeddings were measured before being trusted.** At 61.3% accuracy, no better than calling every pair positive, they were demoted to a recall tool rather than a classifier.
- **Generated code is reviewed.** Every worker diff in this repo was reviewed and verified before merge. That review caught real defects:
  - an FK that would have broken re-imports;
  - an SQLite parameter overflow;
  - a prune crash;
  - a re-resolve that missed seed changes;
  - an 8-minute LLM hang;
  - test isolation leaks.

Gaps:
1. **No evaluation set for the LLM's verdicts.** Embeddings have `synonym_pairs.json` plus an eval script; the LLM has only a 7-item smoke test. Add a labelled fixture (queue, audit, pair and food-ref items with expected kinds) and an eval script that reports accuracy per input set. Run it before each large `--suggest` run and whenever the model or prompt changes.
2. **Prompt changes aren't traceable.** A suggestion records the model id (`model: {"llm", "embedding"}`) but not which prompt produced it. Add a short hash of the system prompt and schema to `model`, so a reviewer can tell old-prompt suggestions apart after a prompt edit.

## Housekeeping

`menu/db/recipe_urls/`, `menu/db/sitemap/`, `tests/db/recipe_urls/` and `tests/db/sitemap/` contain only `__pycache__`. They're untracked leftovers of the phase-1 rename, harmless but misleading in a directory listing. Delete them.

## Recommended order

1. **CI hygiene** (finding 7, finding 10's floor): `uv sync --locked`, ruff-only formatting, a coverage floor, and lighter pre-commit. This is small, immediate, and stops drift.
2. **One-way layers and session boundaries** (findings 1, 2): file moves plus an import-direction test, then pass sessions from the CLI. Do this before phase 6, which adds a new derivation service, so it lands in the right layer.
3. **Split the large functions** (finding 3): `seed_ingredient_data`, `main` into subcommands, and `store_recipe_ingredients`. `derive_nutrition` waits for phase 6.
4. **Explicit dependencies** (finding 4): lazy engine and session factories, parameters instead of patched globals.
5. **AI evaluation** (finding 11): the LLM eval fixture and prompt hashing, before the first large `--suggest` run on real data.
6. **Typed proposals** (finding 6) whenever `suggestions.py` is next touched.

## Where the checklist doesn't fit

Two checklist items shouldn't be applied here: async I/O (finding 8) and free-threading (finding 9). "Best practice" lists describe the median web service, and menu is a deliberately polite, single-process batch tool. The rules that make it a good citizen of other people's websites take precedence over generic concurrency advice, and AGENTS.md records why.
