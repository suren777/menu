# Menu — recipe sourcing lab

Menu is where recipe **sourcing** is developed and experimented with:
discovering recipe URLs, fetching pages politely, and extracting recipe
data from them. Code that works here graduates to
[food-guru](https://github.com/surenislyaev/food-guru)'s
`backend/app/ingest/` with minimal rework — which is why the module
layout, tooling and code style deliberately match food-guru's.

There is no bot, no REST API and no deployment here. Menu is a lab, not
a service.

## How it works

The ingest pipeline lives in `menu/ingest/` and runs four stages:

1. **Discover** — `discover.py` walks a site's sitemaps to find
   candidate recipe URLs, filtering them with the site's URL pattern.
2. **Fetch** — `fetch.py` downloads pages with a disk cache (`.cache/`)
   so repeat runs don't hit the sites again, and respects a per-site
   politeness delay between requests.
3. **Extract** — `extract.py` pulls recipe data out of pages. JSON-LD
   (`application/ld+json`, including `@graph` documents) is the primary
   source; sites that embed their schema behind a test-id script tag
   (like BBC Good Food's `page-schema`) are handled via the site config.
4. **Store** — results land in a local SQLite database (`menu/db/`).
   This is **scratch storage for experiments only, never the source of
   truth** — food-guru owns the real data model.

Adding a site is mostly config: add a frozen `SiteConfig` entry in
`menu/ingest/sites/`, plus a site-specific helper or two if the site
needs a non-standard extraction path. BBC Good Food is the first entry.

## Usage

```bash
uv sync                                  # install dependencies
uv run python -m menu.ingest bbc_good_food   # run the full pipeline
```

Useful during development: `uv run pytest`, `uv run mypy menu tests`,
`uv run ruff check .`.

## Tooling (kept in lockstep with food-guru)

- `uv` for package management, Python 3.14+
- Pydantic v2, SQLAlchemy 2
- `ruff` for linting, `black` + `isort` for formatting
- strict `mypy`

## The old database

`database.db` at the project root (~14.7k cleaned BBC Good Food
recipes) is kept on disk but gitignored. It's useful for checking whether a new extractor
produces the same results as the old one. Nothing new should be built
on top of its schema — port code to food-guru's models instead.

## Moving code to food-guru

Code here is written to be copy-and-adjust, not rewritten:

- Same stage names as food-guru's ingest (`discover`, `fetch`,
  `extract`) and same tooling, so a module moves over with little more
  than an import-path change.
- Site-specific logic is isolated in `menu/ingest/sites/`, so the
  generic pipeline modules move over without site baggage.
- The SQLite scratch store is intentionally throwaway — anything that
  matters belongs in food-guru's data layer.
