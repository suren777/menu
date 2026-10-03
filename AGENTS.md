# menu — agent conventions

Agent-facing conventions for the menu repo. Facts only — verify against the code before acting on them.

## What this repo is

Menu is a recipe **sourcing lab**: discovering recipe URLs, fetching pages politely, and extracting recipe data. It has no bot, no API and no deployment. Code that works here graduates to
[food-guru](https://github.com/surenislyaev/food-guru)'s `backend/app/ingest/` with minimal rework — so module layout, naming, tooling and code style deliberately mirror food-guru. When in doubt about how something should be implemented here, check how food-guru's ingest does it.

The boundary with food-guru: menu discovers, fetches and extracts **raw** structured recipe data (JSON-LD, or schema.org microdata when a site publishes no JSON-LD); parsing/structuring recipes into a real data model is food-guru's job. The ingredient-line parsing below is the one deliberate exception: a menu prototype that moves to food-guru with the ingest code. The SQLite database in `menu/db/` is scratch storage for experiments, never the source of truth — don't build anything on its schema (the ingredient tables included).

## Python 3.14

This repo runs on Python 3.14 (`requires-python = ">=3.14"`, migrated from 3.11 in Oct 2026). Assume newer-than-training-data syntax; read `pyproject.toml` before pinning anything.

## Commands

```sh
uv sync                    # install/refresh dependencies
uv run pytest              # tests
uv run mypy menu tests     # type check (strict)
uv run ruff check .        # lint (rule set matches food-guru's)
```

## Scraping rules

- **Politeness delay** — `fetch.py` spaces requests to a site's host by the site's `politeness_delay`. The delay lives in process memory (`_politeness`), so the crawl must stay **single-process**: parallel workers would each hammer the site at full speed. Same reason food-guru's worker is single-process.
- **Disk cache** — fetched pages are cached under `.cache/` (gitignored, safe to delete) and reused on re-runs. A failed fetch is never cached, so a transient error can't poison later runs.
- **Test against saved pages** — don't write tests that hit live sites. Save the page HTML and test the parsing against the saved fixture.
- **Bad URLs don't stop the crawl** — `pipeline.py` catches errors per URL (and per sub-sitemap), logs them, counts them and moves on; the sitemap is finalised either way (a site-wide block is the one exception, below). Mirrors food-guru's worker, which counts a failure and moves on. The same rule holds per ingredient line: `store_recipe_ingredients` logs and skips a line that fails to parse.
- **robots.txt is honoured** — `fetch.py` fetches each site's robots.txt once through the shared session and caches the parser (single process, like the politeness delay). A disallowed URL raises `DisallowedError` before anything is fetched; the robots crawl-delay joins the site's `politeness_delay`, whichever is larger. A 4xx (no robots.txt) allows all, a 5xx disallows all (RFC 9309).
- **Blocks stop the crawl** — 401/402/403 raise `BlockedError`, and a 429 is waited out once (honouring Retry-After) before it counts as a block too. Five consecutive blocked URLs raise `CrawlBlockedError`, abandon the sitemap unfinalised (its URLs stay queued for a later run) and stop the run — a site shutting us out is not a dead link.
- **Access policy is not negotiable** — sites that demand browser UAs, bot challenges or payment (see `integration_list.md`) have made a deliberate access decision. No UA spoofing, no headless-browser challenge clearing, no paying through the wall; if a site matters, the route is a licence conversation, not code.

## Ingredients prototype

Ingredient lines are parsed and normalised in menu: `menu/ingest/ingredients.py` wraps `ingredient-parser-nlp` behind a `ParsedLine` dataclass, and `menu/ingest/units.py` converts quantities to base units (mass → g, volume → ml, count → piece, plus named count units such as slice) with pint — never hand-written factors. Lines are stored in `recipe_ingredient` with canonical `ingredient` / `ingredient_alias` rows; `recipe_urls` gained `site` and `servings`. The pipeline hooks this after `add_recipe`, and `uv run menu-ingest bbc_good_food --reparse` re-parses stored recipes without re-fetching. Like everything here it lives on the scratch DB and graduates to food-guru with the rest of the ingest.

- **Canonicalisation is alias-driven** — the parser keeps modifiers fused into the name ("warm milk"), so `ingredient_alias` maps variants to canonical `ingredient` rows ("milk"); aliases are hand-seeded where needed. Singularisation uses inflect (`canonical_name`), with leave-alone rules for words ending in "us"/"ss" — the old strip-a-trailing-s rule produced junk like "asparagu".
- **Seeding runs at startup** — `main()` seeds the ingredient data right after `initialise()`, before crawling or reparsing, so a fresh DB never parses unseeded. `--reparse` afterwards prunes ingredients no line and no inbound alias points at (`prune_orphan_ingredients`).
- **The review queue is real** — names the parser likely mangled (`name_needs_review`: ≥5 words, or containing " and "/" or ") stay unresolved (`ingredient_id` NULL) instead of becoming canonical ingredients. A line whose raw text has a conjunction the parsed name lost (`line_needs_review`) is flagged too — "pink and yellow food colouring gels" needs both gels, so an "and" captured as an alternative still loses one from the shopping list. A seeded alias wins over the heuristics, and `seed.LINE_OVERRIDES` fixes lines the parser merges outright ("70g milk or dark chocolate roughly chopped (optional)" parses as name "milk"; override → milk chocolate). `menu-ingest <site> --fdc-report` lists canonical ingredients sharing an fdc_id with no alias between them — hand-seeding candidates, never an auto-merge.
- **Variants are recorded** — a line stores which alias variant it resolved through; `aggregate(..., keep_variants=True)` splits the list back out ("milk (whole)"). Range quantities buy the upper end ("2-3 onions" → 3).
- **Tests use the real lines** — `tests/ingest/fixtures/bbc_lines.json` holds the ingredient lines taken verbatim from the cached pages (double spaces, no commas); the parse edge-case table is hand-written examples.

- **Cross-dimension aggregation is opt-in** — volume→mass needs `ingredient.density_g_per_ml` and count→mass needs `unit_weight_g`, both hand-seeded (USDA FoodData Central has no usable density). Without them, lines stay separate ("250 ml + 100 g").

## Adding a site

Adding a site is mostly config: add a frozen `SiteConfig` entry in `menu/ingest/sites/` and register it. Site-specific behaviour belongs there or in the site's module — the generic pipeline modules (`discover.py`, `fetch.py`, `extract.py`, `pipeline.py`) stay site-agnostic. `sitemap_urls` takes several sitemap indexes; `import_sitemap` detects a plain urlset and stores the sitemap URL itself as the single row.
