# Target data model: upgrade plan

## Context

The scratch schema (`menu/db/database.py`) grew one feature at a time: crawl, then ingredient lines, then reference data, then nutrition. Each step was sound on its own. Together they leave the lab unable to answer its own questions reliably: what was crawled and what failed, why a line got the grams it got, and whether the last seed change made nutrition better or worse. This plan sets the target model, the "moonshot", and a phased route to it from the current DB. Every phase can be shipped and checked on its own.

The DB stays scratch storage (AGENTS.md). The point of the moonshot is not durability. It's that **everything in the DB is either raw capture, curated seed, or derived and reproducible**, so the DB can be rebuilt and two versions of the lab's logic can be compared on numbers.

### Findings from the current DB (2026-10-04)
- **Foreign keys are not enforced.** `PRAGMA foreign_keys` is 0, and `menu/db/engine.py` never turns it on. Every `ForeignKey` in the schema is decorative. There are no orphans today, but only because the code happens not to create any.
- **Reference re-imports churn ids.** `import_cofid` (and the FDC/CNF importers) delete every `ref_food` row of the source and re-insert. `ingredient_food_ref.ref_food_id` points at the old surrogate ids. SQLite can reuse a deleted rowid (no `AUTOINCREMENT`), so after a re-import a confirmed mapping can silently point at a *different* food. Today startup seeding re-creates the seeded rows, but stale rows stay behind.
- **`ix_ref_food_source` is not unique**, although `(source_id, source_food_id)` is the source's own key (21,801 rows, 21,801 distinct pairs).
- **The vocabulary is mostly parser output:**
  - 8,228 canonical ingredients and 8,236 aliases, of which 8,216 are self-aliases (alias text = ingredient name).
  - 4,963 ingredients are used by exactly one line.
  - "Canonical" currently means "a name the parser produced and the heuristics let through". `prune_orphan_ingredients` exists to clean that up after every reparse.
- **Seeding an alias needs a full `--reparse`.** Lines store `ingredient_id` directly, so moving "warm milk" to "milk" only reaches existing lines by re-running the parser over all 96,494 of them. Only 43,618 of those lines are distinct texts.
- **Crawl state is per sitemap, not per URL:**
  - `recipe_urls` holds successful extractions only. Failed, blocked, disallowed and no-recipe URLs leave no row, and there's no `fetched_at`, HTTP status or `lastmod`.
  - An unfinished sitemap is re-walked from the top (123 of 274 sitemaps are done).
  - `add_recipe` ignores a conflict, so a recipe that changed upstream is never updated.
  - `name` is the page `<title>`, not the recipe name.
- **The DB can't be rebuilt from raw data.** The page cache is keyed by `sha256(url)` and sitemap XML isn't cached. The URL list lives only in `recipe_urls`, so deleting `database.db` means re-fetching every sitemap.
- **The DB is the only raw copy (checked 2026-10-11).** `.cache/` is empty: the HTML of all 7,689 stored recipes and the `.cache/sources/` reference downloads are gone. `recipe_urls.data` is the only surviving capture of the JSON-LD, so a rebuild that needs the HTML cache would lose every recipe. **Decision (2026-10-11): recrawling is acceptable.** No bootstrap from the old DB: phase 1 starts a fresh DB and recrawls, which refills the cache.
- **The crawl writes are not atomic.** `add_recipe` and `store_recipe_ingredients` are separate transactions, so a crash between them leaves a recipe with no lines.
- **Conversion factors lose their origin:**
  - `seed.py:271`/`:351` write `density_g_per_ml` and `unit_weight_g` onto `ingredient` whether they came from the hand seed, FDC or CNF.
  - One `unit_weight_g` can't hold the size-specific weights the nutrition plan relies on (egg: small 38, medium 44, large 50 g).
  - On lines, `grams_source` is a free-text label, not a pointer to the factor that was used.
- **`ingredient_food_ref` packs three states into two columns:**
  - candidate = `role NULL, confirmed 0` (4,351 rows); confirmed = `role X, confirmed 1` (79 per role).
  - Invalid combinations are representable.
  - "One confirmed reference per role" (really per role *per source*, since the site's `nutrition_sources` order picks between them) is enforced nowhere.
  - A candidate that was reviewed and rejected looks the same as an unreviewed one, so `--ref-review` keeps showing it.
- **Derived results have no history:**
  - `recipe_nutrition` keeps one derived row per recipe, overwritten on each run.
  - The validation baseline in `nutrition_plan.md` (King Arthur kcal 14%, BBC 53%) exists only as text. The next seed change overwrites the data behind it.
  - `definitions_mixed` is one boolean per recipe, so it can't say *which* nutrient mixed definitions.
- **Derivation is N+1:**
  - `derive_nutrition` runs two queries per line (`_nutrition_ref`) and opens one session per recipe.
  - `derive_all_nutrition` then opens a second session per recipe for the published row.
  - The confirmed reference set is tiny (79 ingredients), so everything it needs fits in memory.
- **Naming has drifted from food-guru.** food-guru (Postgres + Alembic) uses plural table names (`recipes`, `ingredients`, `recipe_ingredients`); menu uses singular ones. AGENTS.md says names should mirror food-guru.
- **The review queue is mostly one pattern:**
  - It holds 3,632 lines, but only 1,790 distinct texts.
  - 2,688 lines (74%, 902 texts) are King Arthur lines with the brand inside the name ("1 tsp King Arthur Pure Vanilla Extract").
  - The ten most common texts alone account for 1,302 lines.
  - Lines whose parse *raised* are only logged by `store_recipe_ingredients`, so they never reach the queue at all.
- **Silent misparses are a bigger problem than the queue.** Some lines parse cleanly into the wrong ingredient:
  - "beef stock or gravy" → *beef gravy*
  - "ice cream or extra soured cream, to serve" → *extra soured cream*
  - "chopped nuts, chocolate chips, or other add-ins" → *other add-in*
  - "12-hole mini bundt tin" → an *ingredient*

  4,963 concepts are used by a single line, and that's where these errors sit. They're never flagged.
- **Parsing fails at the name boundary, not the quantity.** 36,943 of 40,251 BBC lines (92%) have no comma ("150g provolone cheese cut into cubes or mozzarella pearls"). Where the name ends is a question of vocabulary, not of line shape. This is why the trained parser stays and regex is only used for fixed patterns.
- **Synonyms and dialect split concepts:**
  - confectioners' sugar / icing sugar / powdered sugar / confectioners’ sugar (curly apostrophe): 673 / 389 / 41 / 15 lines, four concepts for one ingredient.
  - aubergine / eggplant, rocket / arugula, coriander leaf / cilantro.
  - The opposite trap: coriander / ground coriander / coriander seed (353 / 175 / 82 lines). These are close in meaning, but the fresh herb and the spice are different foods.
- **`--ref-review` can't cross dialects.** It matches reference foods by "every word of the name appears in the description", so "icing sugar" never finds FDC "Sugars, powdered", and "aubergine" never finds "Eggplant, raw". With only 79 confirmed references, mapping is the bottleneck for nutrition.

## Principles

1. **Three layers, never mixed in one table:**
   - **Raw:** what was captured (pages, sitemaps, reference releases). Append or replace, never hand-edited.
   - **Curated:** hand-made decisions (aliases, overrides, confirmed references, conversion seeds). These live in git (`seed.py`) and are loaded into the DB at startup, as today.
   - **Derived:** everything computed from the other two (parses, resolution, grams, nutrition). It can always be recomputed, and it carries the version of the logic and data that produced it.
2. **Stable natural keys at the boundary.** Surrogate ids stay internal. Anything that outlives a re-import or graduates to food-guru is keyed by something the world defines: URL, term text, `(source, source_food_id)`.
3. **Every derived number can name its inputs.** Grams name the conversion factor row, nutrients name the reference food and definition, and runs name the parser version, seed digest and source versions.
4. **States are enums, not flag combinations.** Use CHECK-constrained columns (SQLAlchemy `Enum(..., native_enum=False)`), so an invalid state can't be stored.
5. **The DB enforces what the docstrings promise:** FKs on, uniqueness as indexes, one-of rules as partial unique indexes.
6. **Rebuildable.** `menu-rebuild` produces the whole DB from the cache, seeds and source downloads, with no network. Rebuild-and-compare is how each phase is verified.
7. **Cheapest reliable layer first; models only suggest.** Each layer shrinks what the next one has to handle:
   1. regex for fixed site patterns and the quantity grammar
   2. the trained parser for the name boundary
   3. curated seeds for known mistakes
   4. embeddings to find candidates
   5. an LLM to classify them

   Embeddings and LLM output never write to resolution directly. They land in a suggestions file that's reviewed in git, and only accepted entries become seeds. Models handle language (what the ingredient is, which part is an alternative, what's equipment), never numbers: no densities, weights or nutrients.

## Target model

Table names are plural, to match food-guru. Columns are shown without types where the type is obvious.

### Raw layer
| Table | Key columns | Replaces / why |
|---|---|---|
| `sitemaps` | `id`, `site`, `url UNIQUE`, `parent_id NULL` (index→child), `kind` (index/urlset), `status` (pending/done/failed/blocked), `fetched_at`, `error` | `sitemap`. Same queue, with a recorded outcome instead of a boolean. |
| `pages` | `id`, `site NOT NULL`, `url UNIQUE`, `sitemap_id`, `lastmod NULL`, `status` (queued/fetched/no_recipe/failed/blocked/disallowed), `http_status`, `attempts`, `fetched_at`, `content_sha256`, `error` | **New: the crawl ledger.** The per-URL outcome is a query. A re-run continues from `queued` pages instead of re-walking sitemaps. `content_sha256` detects upstream changes. The URL→cache mapping makes the cache replayable. |
| `recipes` | `id`, `page_id UNIQUE`, `name` (JSON-LD `name`), `data` JSON, `data_sha256`, `extractor` (json_ld/test_id/microdata), `servings`, `yield_text` | `recipe_urls`. Recipe split from page. Upsert on `data_sha256` change. Written in the same transaction as its lines. |
| `food_sources` | as `food_source`, plus `imported_at`, `release_sha256` | Ties every value to the exact file it came from. |
| `ref_foods` | `id`, `source_id`, `source_food_id`, `description`, `category`, `refuse_pct`; **`UNIQUE(source_id, source_food_id)`**, `UNIQUE(id, source_id)` | `ref_food`. Importers **upsert** on the natural key, so ids survive re-imports. A food missing from a new release is deleted only if nothing references it, and is logged otherwise. |
| `ref_portions`, `ref_nutrients`, `nutrient_maps` | unchanged shape | Replaced wholesale per source on import. Nothing outside the ref layer points at them except `ingredient_conversions.ref_portion_id`, which is re-derived after an import. |
| `recipe_published_nutrients` | `recipe_id`, `nutrient`, `amount`, `raw_text`; PK `(recipe_id, nutrient)` | The `published` half of `recipe_nutrition`. It's parsed from captured data, so it belongs to the raw side and has no run. |

### Vocabulary (curated + derived)
| Table | Key columns | Replaces / why |
|---|---|---|
| `ingredients` | `id`, `name UNIQUE`, `origin` (seed/auto) | `ingredient`, without the conversion columns. `origin=auto` marks a concept created from a parser name. Auto concepts with no term in use are dropped by a query, which replaces `prune_orphan_ingredients`. |
| `ingredient_terms` | `id`, `term UNIQUE`, `ingredient_id NULL`, `variant`, `resolution` (seed/auto/review/not_food) | `ingredient_alias`. **Every parsed name is a term.** Lines point at terms, not ingredients. Re-pointing a term re-resolves every line that uses it with no reparse. `resolution=review` *is* the review queue, grouped by term with line counts. `not_food` marks equipment and other non-ingredients ("12-hole mini bundt tin"); those lines stay but are left out of aggregation and nutrition. Self-aliases disappear: a term is the name. |
| `ingredient_conversions` | `id`, `ingredient_id`, `kind` (density/unit_weight), `unit NULL` (size or named unit: small, medium, large, clove, slice, stick, pinch…), `value`, `origin` (seed/fdc/cnf/…), `ref_portion_id NULL`; `UNIQUE(ingredient_id, kind, unit, origin)` | `ingredient.density_g_per_ml` / `unit_weight_g` and the derive-on-seed code. All candidate factors are stored side by side, and precedence (seed > FDC > CNF) is applied at read time. Size-specific and named-unit weights fit naturally. |
| `ingredient_food_refs` | `ingredient_id`, `ref_food_id`, `source_id`, `role` (conversion/nutrition), `status` (candidate/confirmed/rejected), `origin` (seed/parser); PK `(ingredient_id, ref_food_id, role)`; composite FK `(ref_food_id, source_id) → ref_foods(id, source_id)`; partial `UNIQUE(ingredient_id, role, source_id) WHERE status='confirmed'` | `ingredient_food_ref`. One status column instead of role-NULL + bool. `source_id` is denormalised so the database can enforce one confirmed reference per role per source. The composite FK keeps it consistent with the food. `rejected` takes reviewed candidates out of `--ref-review`. |

### Lines
| Table | Key columns | Replaces / why |
|---|---|---|
| `recipe_ingredients` | as today, but `term_id` replaces `ingredient_id` (the ingredient is reached through the term), `alternative_of` FK with `ON DELETE CASCADE`, `conversion_id NULL` replaces `grams_source`, `parser_version`, `parse_status` (ok/failed) | `recipe_ingredient`. Grams trace back to the exact factor row. The parser version on each line makes a stale parse visible. |
| `line_parses` *(conditional, see phase 5)* | `text_norm`, `unit_system`, `parser_version`, `payload` JSON; PK of all three except `payload` | **New: a parse cache.** 43,618 distinct texts against 96,494 lines. Build it only if a measured reparse is slow enough to matter. |

### Derived results
| Table | Key columns | Replaces / why |
|---|---|---|
| `derivation_runs` | `id`, `started_at`, `kind` (nutrition), `parser_version`, `seed_digest` (hash of `seed.py` data), `sources` JSON `{name: version}`, `note` | **New.** Each `--derive-nutrition` is a run. The validation baseline becomes data you can compare across seed changes. |
| `recipe_nutrition_runs` | `run_id`, `recipe_id`, `coverage`, `unconverted_lines`; PK `(run_id, recipe_id)` | The per-recipe metadata of the derived `recipe_nutrition` row. |
| `recipe_nutrients` | `run_id`, `recipe_id`, `nutrient`, `amount`, `definitions` (sorted, comma-free JSON list), `sources` JSON list; PK `(run_id, recipe_id, nutrient)` | Derived nutrients as **one row per nutrient** (long format). Mixed definitions are visible per nutrient, not as one boolean per recipe. It has the same shape as `ref_nutrients`, so adding a nutrient is a `nutrient_maps` row, not a schema change. food-guru's wide `recipes.calories/fat/…` columns are filled *from* this at graduation. Runs are pruned to the last N (default 10). |

### Suggestions (curated, in git) and embeddings (derived, on disk)
- **`menu/db/ingredients/suggestions.json`** holds what the models propose, one entry per proposal:
  - `kind`: one of `alias`, `variant`, `line_override`, `not_food`, `food_ref`
  - `subject`: the term or line text
  - `proposal`
  - `evidence`: neighbours, similarity, the LLM's one-line reason
  - `model`: the embedding and LLM ids and versions
  - `status`: pending, accepted or rejected

  You accept or reject entries by editing `status` in a reviewed diff. Seeding loads accepted entries exactly like `ALIASES` / `LINE_OVERRIDES` / `FOOD_REFS`. A rejected entry is never proposed again, and this file replaces phase 2's `seed.REJECTED_REFS`. Because it's in git, a rebuild keeps every decision and needs no model.
- **Embedding cache:** `.cache/embeddings/<model>.npz` (texts + float32 vectors), gitignored like the page cache. About 30k vectors (8k concepts + 22k reference descriptions) × 384 dimensions comes to roughly 45 MB. It's kept out of the scratch DB on purpose: it can always be recomputed, and nothing queries it with SQL.

### Kept as is
- Raw JSON-LD in `recipes.data`. It's the raw record menu exists to produce, and costs about 27 MB.
- Seeds as Python data in git. Curated decisions belong in reviewed diffs, not in the DB.
- `create_all` without migrations. The DB is scratch, and **rebuild is the migration**. Alembic is food-guru's job at graduation.
- A single process and SQLite.

## Phases

Each phase ends with a rebuild, `uv run pytest`, `uv run mypy menu tests`, `uv run ruff check .`, and a **metrics diff** against the previous phase (phase 0). A phase must not change a number it didn't intend to change.

### Phase 0: guardrails and the metrics snapshot (done)
Shipped in `77b72a2`, `74e3e01`, `cb18ecd`. The baseline is `metrics/baseline-2026-10-05.json`, not `-10-04`.

- `engine.py`: a `connect` event listener that sets `PRAGMA foreign_keys=ON`, `journal_mode=WAL` and `synchronous=NORMAL`. Add a test that the pragma is on.
- `menu-ingest metrics <PATH>` writes a JSON snapshot:
  - row counts per table
  - lines with grams, broken down by `grams_source`
  - lines with `ingredient_id` NULL
  - mean coverage
  - recipes with coverage ≥ 0.9
  - median validation error per site and nutrient
  - the count of distinct ingredients
- Commit the current snapshot as `metrics/baseline-2026-10-04.json`. Every later phase diffs against it.
- Quick wins that fit the current schema: a unique `ix_ref_food_source`, and enum CHECKs on `recipe_nutrition.source`, `ingredient_food_ref.role`, `recipe_ingredient.dimension` and `base_unit`. The data already satisfies all of them.

### Phase 1: crawl ledger and recipes (raw layer)
- Add `sitemaps`, `pages` and `recipes` as above.
- ~~One-off bootstrap from the old DB~~ — dropped: the old DB is retired and the sites are recrawled into a fresh one (see Findings).
- Pipeline changes:
  - `process_url` records every outcome on its page.
  - `import_sitemap` inserts queued pages with `lastmod`.
  - `crawl_sitemap` works from queued pages.
  - The recipe and its lines are written in **one transaction**.
  - `name` comes from JSON-LD.
  - Sitemap XML goes through the disk cache like pages.
- `menu-rebuild`: new DB file → initialise → seed → import refs from `.cache/sources/` (skipped with a warning when a release isn't downloaded) → replay the cached sitemaps and pages (no network; a page whose HTML isn't cached stays `queued` for the next live crawl) → derive.
- Check: the metrics diff should show identical recipe and line counts, with `name` the only intended change. Counts come from a fresh recrawl, so they differ from the baseline by whatever changed upstream; the per-site line and grams *ratios* are what should hold.

### Phase 2: reference stability
- `ref_foods` natural-key upsert in all three importers (`fdc.py`, `cofid.py`, `cnf.py`). Portions, nutrients and maps are still replaced per source.
- `ingredient_food_refs` with `status`, `origin`, the denormalised `source_id`, the composite FK and the partial unique index.
- Seeding maps old to new: `confirmed=1` → `confirmed`, otherwise `candidate`.
- `--ref-review` gains a way to record a rejection. Rejections are curated, so they live in a `seed.REJECTED_REFS` list in git and are loaded at startup.
- Test: import a fixture slice, confirm a mapping, re-import the same slice → the mapping still points at the same `source_food_id`.

### Phase 3: vocabulary (terms → concepts)
- `ingredient_terms` replaces `ingredient_alias`, and `recipe_ingredients.term_id` replaces `ingredient_id`.
- Resolution moves from `_resolve_ingredient` (find, or create ingredient + self-alias) to a two-step flow:
  1. Upsert the term: seed resolution if seeded, `review` if `name_needs_review`/`line_needs_review` flags it, otherwise `auto` with an auto concept of the same name.
  2. The line stores `term_id`.
- `LINE_OVERRIDES` resolve to the override's term.
- `--re-resolve`: re-apply seeds and review heuristics to terms only, with no parser. This replaces `--reparse` as the everyday command after a seed change. `--reparse` stays for parser or unit changes.
- Aggregation, derivation and reports join `recipe_ingredients → ingredient_terms → ingredients`. Only the select changes, because the join path is fixed.
- Check: identical metrics. Distinct ingredients may drop slightly where the old code created two concepts for one term.

### Phase 4: vocabulary distillation (rules → embeddings → LLM review)
Quote folding and the site strip patterns in 4a don't depend on terms, so they can ship before phase 3. Take a metrics snapshot between them so each diff shows one change.

These four steps run in order. After each one, the metrics snapshot records review-queue lines, distinct concepts and confirmed references, so it's clear what each layer contributed.

**4a. Deterministic rules first**
- Site-level name cleanup before parsing: `SiteConfig.ingredient_strip_patterns: tuple[str, ...]` (regexes), applied by the generic pipeline, so the site-specific part stays in config.
  - King Arthur gets `\bKing Arthur\s+`.
  - The six `king arthur …` entries in `seed.ALIASES` are rewritten brand-free.
  - `raw_text` keeps the original line.
- Fold typographic quotes and NBSP in `canonical_name` (’ → ', “ ” → "). This merges "confectioners’ sugar" into "confectioners' sugar" with no model involved.
- Lines whose parse raises are stored with `parse_status='failed'` (a new `recipe_ingredients` column: `ok`/`failed`) and no term. Today they're only logged, so they never reach the queue.
- Check: the review queue drops from 3,632 lines to about 950 before any model runs.

**4b. Local embeddings**
- `menu/ingest/embeddings.py::embed(texts) -> np.ndarray` (L2-normalised) is the only entry point, wrapping fastembed's `TextEmbedding`, as `ingredients.py` wraps the parser.
  - Model: `BAAI/bge-small-en-v1.5` (384 dims, ONNX runtime, no torch, cp314 wheels on macOS arm64).
  - Weights are downloaded once into `.cache/models/`. Embedding runs offline after that, and nothing calls a network at ingest time.
  - Confirm the weights' licence on the model card before adding the dependency (the same rule as reference data: commercial use, no share-alike).
- What gets embedded:
  - **Concepts:** the name plus its most frequent raw line as context. Short names alone embed noisily, and the context separates "coriander (fresh, chopped)" from "coriander (ground)".
  - **Reference foods:** description + category.
- Nearest neighbours are a numpy dot product over the cache, with no vector store.
- **`--ref-review`** shows the top 5 reference foods per source by embedding, next to the existing all-words text match. This fixes dialect gaps: icing sugar → "Sugars, powdered", aubergine → "Eggplant, raw".
- **Concept pairs:** for each `auto` concept, its top 5 neighbours above a similarity floor, excluding pairs already joined by a term. These pairs feed 4c.
- **Settle the model by measurement:**
  - *Ground truth for food refs:* recall@5 of the confirmed `FOOD_REFS` (the ingredient's confirmed food appears in its embedding top 5 for that source).
  - *Ground truth for concepts:* a hand-labelled `tests/ingest/fixtures/synonym_pairs.json` with these pairs:

    | Pair | Label |
    |---|---|
    | icing sugar / confectioners' sugar | same |
    | aubergine / eggplant | same |
    | rocket / arugula | same |
    | coriander leaf / cilantro | same |
    | whole milk → milk | variant |
    | coriander / ground coriander | different |
    | baking soda / baking powder | different |
    | caster sugar / icing sugar | different |

  - Compare `bge-small` with one larger local model, and record both scores here. Keep the small one unless the larger is clearly better.
  - **Result, `bge-small-en-v1.5` (2026-10-11, 31 labelled pairs):** the best threshold is 0.476 at 61.3% accuracy, which is what calling every pair positive scores (19 of 31). So similarity alone does **not** separate these pairs. The near-miss "different" pairs score 0.71–0.86, inside the same/variant band (0.48–0.90), and coriander / ground coriander scores highest of all at 0.859. Embeddings are therefore a recall tool here (candidates for 4c), never a classifier, and the concept-pair floor has to be low. Recall@5 on `FOOD_REFS` hasn't run yet because the ref tables are empty; run `uv run menu-embed-eval` after the reference imports. The larger-model comparison is still to do.

**4c. LLM review (Gemini 3.8 Flash)**
- `menu/ingest/llm_review.py::suggest(items) -> list[Suggestion]`, with the provider and model as config. Before building, confirm the model offers schema-constrained JSON output and a discounted batch mode; neither has been checked yet.
- `menu-ingest suggest` is an explicit command that never runs inside a crawl. It groups about 50 items per request under one fixed prompt, submits them through batch mode, and skips any item already in `suggestions.json` (any status). Four input sets, all as distinct texts:
  1. **Queue:** review-queue terms with their raw lines. What is the ingredient, and is there an alternative or equipment in the line?
  2. **Audit:** single-use `auto` concepts (4,963) with their line. Is the parsed name right, was the right alternative kept, is it food? This catches the silent misparses ("beef gravy", "12-hole mini bundt tin").
  3. **Pairs from 4b:** same, variant or different.
  4. **Reference candidates from 4b:** which of the top 5, if any, is this ingredient?
- Mechanical checks before a suggestion is written. Failures are dropped and counted:
  - every number in a proposal appears in the raw text;
  - units are known to pint;
  - an alias target is an existing concept or marked `new`;
  - pair and food-ref proposals carry no numbers at all.
- Measure the real token cost on the first batch and record it here.
- **Provider, checked live 2026-10-11:** the user's Fireworks deployment (GLM-5.3 Flash, a reasoning model) through its OpenAI-compatible endpoint, configured by `MENU_LLM_BASE_URL` / `MENU_LLM_MODEL` / `FIREWORKS_API_KEY`. Strict `json_schema` output works, but the deployment does not enforce key order and there is no batch mode, so chunks go out synchronously. Findings:
  - `max_tokens` must scale with the chunk (1,000 + 150 per item). A flat 16k ceiling let one 1-item request pad its reply for 8 minutes.
  - "ok" on a food-ref item was read as "this candidate is fine", so the no-match answer is `none` on the wire.
  - Smoke run, 7 items across all four sets: about 1,150 prompt and 400–540 completion tokens, 5–19 s. Every verdict was right, including coriander / ground coriander (different), whole milk (variant of milk), aubergine (alias of eggplant), icing sugar (FDC "Sugars, powdered") and rapeseed oil (no candidate). The first batch over real data is still to be measured.

- **Status (2026-10-11):** built and tested offline — `review_inputs.py` builds the four sets, `menu-ingest suggest` runs them (with `--dry-run` and `--limit`), `report ref-review` shows embedding neighbours, and accepted entries seed like hand seeds (4d). The first real batch and its token cost wait for the recrawl and the reference imports.

**4d. Accept**
- Review the `suggestions.json` diff and set `status`. Seeding loads accepted entries, and `--re-resolve` applies them without reparsing.
- Check, in the metrics diff:
  - review-queue lines, distinct concepts and confirmed references move in the expected direction;
  - mean nutrition coverage rises;
  - validation medians don't regress.

### Phase 5: conversions and line provenance
- `ingredient_conversions` is filled by seeding (origin `seed`) and by a derivation step over confirmed conversion references (origin = source). It keeps **all** sizes and named units, not just `medium`.
- `to_grams` reads the factors in precedence order, and lines store `conversion_id`.
- Fuzzy units from the nutrition plan step 3 (pinch, knob, dash) become seeded `named_unit` conversions, not code.
- Measure `--reparse` wall time. If it takes more than about 2 minutes, add `line_parses` keyed by `(text_norm, unit_system, parser_version)`. Otherwise record the timing here and skip it.
- Check: the metrics diff should show lines with grams ≥ 56,889. Size-specific egg weights may change grams on egg lines, and this phase should list those changes explicitly.

### Phase 6: derivation runs and set-based nutrition
- `derivation_runs`, `recipe_nutrition_runs`, `recipe_nutrients` and `recipe_published_nutrients` replace `recipe_nutrition`.
- `derive_all_nutrition` reads the confirmed references, their nutrients, the prepared forms and the site preferences into memory once, then streams recipes in batches of 500 per transaction. No query per line.
- `--validate-nutrition [--run N] [--against M]` compares a run with the published values, or two runs with each other.
- Check:
  - The latest run's validation medians equal the baseline (King Arthur kcal 14%, BBC 53%).
  - The derivation is noticeably faster than before; record both timings here.

### Phase 7: graduation mapping
- Write the menu → food-guru mapping into this file:
  - `pages.url` → `recipes.source_url`
  - `ingredient_terms` ↔ `ingredients.normalized_name`
  - the latest accepted run → the wide nutrition columns on food-guru `recipes`
  - `JSON` → `JSONB`
  - CHECK enums → native enums or CHECKs, matching food-guru's convention
- One Alembic migration per table group, written in food-guru when the code moves, not here.

## Deliberately not doing
- **Postgres in menu.** SQLite matches the single-process crawl, and portability comes from plain types, not a server.
- **Alembic in menu.** A scratch DB that rebuilds from raw data doesn't need migrations, and they would slow down the lab.
- **HTML in the DB.** Pages stay on disk, and the ledger points at them.
- **ORM `relationship()` graphs.** The repositories run plain queries over a session today, and that stays the pattern.
- **Models writing resolution directly.** Neither embeddings nor the LLM ever merge, alias or map anything: they write suggestions, and you accept them. Similarity puts "coriander" next to "ground coriander", and it can't tell *related* from *same*.
- **Models producing numbers.** No densities, weights or nutrients from an LLM. "Never guess" from `nutrition_plan.md` stands.
- **A vector database or sqlite-vec.** About 30k vectors is a millisecond numpy product.
- **Regex as the main parser.** It's good for the quantity grammar and fixed site patterns, but the name boundary needs vocabulary (92% of BBC lines have no comma), which is what the trained parser provides.
- **Cross-site recipe dedupe** (same recipe syndicated under two URLs). It's possible with `data_sha256` but has no consumer yet.

## Open decisions
- **Plural table names now or at graduation?** Recommended: now. Every table is rebuilt in phases 1–6 anyway, and AGENTS.md asks names to mirror food-guru.
- **Run retention:** keep the last 10 runs plus any run named in `note`?
- **Where rejected candidates live:** git, not the DB, so a rebuild keeps them. This starts as `seed.REJECTED_REFS` in phase 2 and moves into `suggestions.json` in phase 4.
- **Similarity floor and k for concept pairs:** start at top 5 and tune the floor on `synonym_pairs.json`, so the LLM sees few obvious non-pairs.

## Critical files
- Modified:
  - `menu/db/database.py`, `menu/db/engine.py`
  - `menu/db/{sitemap,recipe_urls,ingredients}/*`
  - `menu/ingest/pipeline.py`, `menu/ingest/discover.py`, `menu/ingest/fetch.py`
  - `menu/nutrition/{derive,reports}.py`, `menu/nutrition/sources/{fdc,cofid,cnf}.py`
  - `menu/ingest/registry.py` (`ingredient_strip_patterns`), `menu/ingest/sites/king_arthur.py`, `menu/ingest/ingredients.py` (quote folding)
  - `pyproject.toml` (scripts, `fastembed`, the Gemini SDK), `.gitignore` (`.cache/` already covers models and embeddings), `AGENTS.md`
- New:
  - `menu/db/pages/`, `menu/db/rebuild.py`, `menu/db/metrics.py`
  - `menu/ingest/embeddings.py`, `menu/ingest/llm_review.py`, `menu/db/ingredients/suggestions.json`
  - `metrics/baseline-2026-10-04.json`
  - `tests/db/test_rebuild.py`, `tests/db/test_constraints.py`
  - `tests/ingest/test_embeddings.py`, `tests/ingest/test_llm_review.py`, `tests/ingest/fixtures/synonym_pairs.json`

## Verification
- **Constraint tests** (`tests/db/test_constraints.py`). Each of these is refused by the DB, not by code:
  - a line pointing at a missing term
  - two confirmed nutrition references from the same source for one ingredient
  - a confirmed reference with no role
  - an unknown dimension
- **Re-import stability:** confirm a mapping, re-import the fixture source, and the mapping still resolves to the same `source_food_id`.
- **Re-resolve without reparse:** seed a new alias for an auto term, run `--re-resolve`, and every line using that term aggregates under the new concept. The parser is never called (mocked to raise).
- **Distillation (phase 4).** Tests never download a model or call an API; the embedder and the LLM client are injected fakes.
  - The King Arthur strip pattern turns "1 tsp King Arthur Pure Vanilla Extract" into vanilla extract, and `raw_text` is unchanged.
  - Curly and straight apostrophes resolve to one term.
  - A raising parse stores a `failed` line.
  - Neighbour search over fixed vectors returns the expected pairs.
  - The mechanical checks drop a suggestion whose number isn't in the raw text.
  - An accepted suggestion becomes a term mapping after `--re-resolve`; a rejected one is never re-proposed.
  - The model evaluation (recall@5 on confirmed refs, accuracy on `synonym_pairs.json`) is a script whose results are recorded in this file, not a test.
- **Rebuild:** `menu-rebuild` on a fixture cache of a few saved pages produces the same metrics as a crawl of those pages.
- **Per phase:** the metrics diff against the previous snapshot is attached to the commit message, and any changed number is explained.
