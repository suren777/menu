# menu — agent conventions

Agent-facing conventions for the menu repo. Facts only — verify against the code before acting on them.

## What this repo is

Menu is a recipe **sourcing lab**: discovering recipe URLs, fetching pages politely, and extracting recipe data. It has no bot, no API and no deployment. Code that works here graduates to
[food-guru](https://github.com/surenislyaev/food-guru)'s `backend/app/ingest/` with minimal rework — so module layout, naming, tooling and code style deliberately mirror food-guru. When in doubt about how something should be implemented here, check how food-guru's ingest does it.

The boundary with food-guru: menu discovers, fetches and extracts **raw** JSON-LD; parsing/structuring recipes into a real data model is food-guru's job. The SQLite database in `menu/db/` is scratch storage for experiments, never the source of truth — don't build anything on its schema.

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
- **Bad URLs don't stop the crawl** — `pipeline.py` catches errors per URL (and per sub-sitemap), logs them, counts them and moves on; the sitemap is finalised either way. Mirrors food-guru's worker, which counts a failure and moves on.

## Adding a site

Adding a site is mostly config: add a frozen `SiteConfig` entry in `menu/ingest/sites/` and register it. Site-specific behaviour belongs there or in the site's module — the generic pipeline modules (`discover.py`, `fetch.py`, `extract.py`, `pipeline.py`) stay site-agnostic.

Known limitation to fix when the second site is added: `import_sitemap` assumes `sitemap_url` is a sitemap **index** (a sitemap whose `sitemap.xml` lists pages directly would have its page URLs stored as sub-sitemaps and then fail to parse as XML). Not a problem for BBC today; fix `import_sitemap` to detect a plain urlset when the next site lands.
