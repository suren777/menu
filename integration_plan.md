# Multi-site crawl and ingest: plan for the integration_list.md sites

## Context

`integration_list.md` lists 10 recipe archives to add next to BBC Good Food. Each site was probed live on 2026-10-03, with our real User-Agent (`menu/0.1 …`) and about 1 request per second, for three things: robots.txt, sitemap structure, and the recipe data on a sample page. Most of the work is the one-time generic pipeline changes that a second site forces (AGENTS.md already flags one of them). After those, each site is mostly a `SiteConfig` entry.

## Findings per site

| Site | robots.txt (`*` / AI bots) | Access with our UA | Sitemap | Recipe data | Verdict |
|---|---|---|---|---|---|
| **King Arthur Baking** | allowed / no AI rules | 200 | index → `sitemap.xml?page=1..4` (≈4.6k `/recipes/` URLs, mixed with blog/author) | standard JSON-LD | ✅ **Tier 1** |
| **Ottolenghi** | allowed / no AI rules | 200 | Shopify index. Recipes are all in `sitemap_metaobject_pages_1.xml` (667, `/pages/recipes/…`) | JSON-LD with **raw control chars**: strict `json.loads` fails, so `extract.py` drops it silently today. No `recipeYield` | ✅ **Tier 1** |
| **Smitten Kitchen** | allowed / no AI rules | 200 | **plain urlset** (`sitemap.xml`, 1001 URLs) plus `sitemap-2.xml` (519), which isn't linked from the first | **no JSON-LD**: Jetpack **microdata** (`itemprop="recipeIngredient"`), flat, so a small BeautifulSoup reader extracts it | ✅ **Tier 1** (needs microdata + urlset support) |
| **Epicurious** | `*` allowed / **blocks ClaudeBot, CCBot, Google-Extended…** | 200 | index of 60 monthly sitemaps (`sitemap-YYYY-MM.xml`) | standard JSON-LD | ⚠️ **Tier 2**: works, but the publisher has opted out of AI crawling |
| **Serious Eats** | `*` allowed / blocks AI bots | **402** on sub-sitemaps | index → `sitemap_1.xml` (402) | n/a | ⛔ pay-per-crawl wall (Dotdash Meredith) |
| **Simply Recipes** | `*` allowed / blocks AI bots | **402** on sub-sitemaps | same as Serious Eats | n/a | ⛔ same wall |
| **Woks of Life** | `*` allowed / blocks anthropic-ai, GPTBot, CCBot | **403 "Security Verification"** (BigScoots) on recipe pages | Yoast index (`post-sitemap*.xml`) | n/a | ⛔ bot challenge plus AI opt-out |
| **Food52** | n/a | **429 Vercel Security Checkpoint**, even for robots.txt | n/a | n/a | ⛔ bot challenge |
| **Great British Chefs** | n/a | **502 for our UA, 200 for a browser UA** | n/a | n/a | ⛔ UA-based block |
| **NYT Cooking** | `*` allowed / blocks AI bots | paywalled; no `/sitemap.xml` (404) | n/a | n/a | ⛔ subscription content |

### Policy for the ⛔ rows
Don't work around them. That means no browser-UA spoofing, no headless browser to clear the challenges, and no paying to crawl through the 402 wall. Each of these is a deliberate access decision by the site, and anything that defeats one shouldn't graduate to food-guru. Keep them listed in `integration_list.md` as "blocked (reason, date probed)", so nobody probes them again blind. If one matters, the route is a licence/API conversation, not code.

### Decision needed: Epicurious (Tier 2)
Our UA technically passes `User-agent: *`. But Condé Nast's robots.txt explicitly opts out of AI and data crawlers, and food-guru is the destination for this data. **Recommendation:** treat an explicit AI-crawler opt-out as a no. That leaves 3 sites now, with Epicurious parked. The plan below is written so Epicurious is a single config file if you decide otherwise.

## Generic pipeline changes (one PR, before any new site)

Everything here stays in the site-agnostic modules and graduates to food-guru.

### 1. `import_sitemap` handles plain urlsets (the known AGENTS.md limitation)
- `discover.py`: `request_xml` returns its root kind as well (`sitemapindex` vs `urlset`, read from the root tag's local name). For example: `fetch_sitemap(url) -> Sitemap(kind, locs)`.
- `pipeline.import_sitemap`: for an index, store the children as today. For a urlset, store **the sitemap URL itself** as the single `Sitemap` row, so `process_sitemap` → `discover_urls` reads its page URLs as normal.
- `SiteConfig.sitemap_url: str` becomes `sitemap_urls: tuple[str, ...]`, because Smitten Kitchen needs `sitemap.xml` + `sitemap-2.xml`. This also lets Ottolenghi point straight at its metaobject sitemap and skip the products and collections sitemaps. BBC becomes a 1-tuple.
- Tests: urlset and index fixtures in `tests/ingest/fixtures/`, plus an `import_sitemap` test for each kind.

### 2. robots.txt compliance in `fetch.py`
- Add an in-process `urllib.robotparser.RobotFileParser` per host, fetched once through the shared session and cached next to `_politeness`. It's single-process, for the same reason as the politeness delay.
- `fetch_page` raises a new `DisallowedError(FetchError)` for disallowed URLs. The pipeline already counts and skips these per URL.
- Effective delay = `max(site.politeness_delay, robots crawl_delay)`.
- If robots.txt is unreachable, treat it as allow-all on 4xx and disallow-all on 5xx (RFC 9309).
- Sitemap fetches in `discover.py` go through the same check.

### 3. Circuit breaker: a block must not be counted as 40k "bad URLs"
Today a site-wide block (402/403/429 challenge) would fail every URL. Each sitemap would still be **finalised**, so the queue would end up marked done with nothing stored.
- `fetch.py` raises `BlockedError(FetchError)` on 401/402/403, and on 429 once the retries are exhausted.
- `process_sitemap` tracks consecutive `BlockedError`s. At a threshold (e.g. 5) it re-raises `CrawlBlocked`, **without finalising** the sitemap.
- `crawl_sitemap` stops the whole site run on `CrawlBlocked`, logs it, and prints the report. Unfinished sitemaps stay queued for a later run.
- Ordinary per-URL errors (404, timeouts, parse failures) keep today's log-count-continue behaviour.

### 4. `extract.py` fixes
- `json.loads(..., strict=False)` in both JSON-LD paths. Ottolenghi embeds literal newlines and tabs in strings. This is a pure widening: valid JSON parses the same.
- **Microdata fallback with BeautifulSoup.** Add `extract_microdata_recipe(soup)`:
  - find the element whose `itemtype` contains `schema.org/Recipe`;
  - collect its `itemprop` descendants, preferring the `content`/`datetime` attribute and falling back to the stripped text;
  - return them as a Recipe-shaped dict (`@type`, `name`, `recipeYield`, `recipeIngredient` as a list, …), the same shape as JSON-LD, so the scratch store and ingredient pipeline don't change.
  - Tested on a real Smitten Kitchen page: name, yield, totalTime and all 18 ingredient lines come out. Order: test-id → JSON-LD → microdata. Microdata is generic schema.org, so the fallback belongs here, not in a site module.
  - Flat items only: a nested `itemscope` is skipped, not recursed into. Smitten Kitchen has none. If a later site nests items, that's the point to reconsider extruct (it was tested and works on 3.14, but pulls in lxml and needs the raw HTML rather than the soup).
  - `recipe-scrapers` (15.12, which wraps extruct) was also tried and isn't adopted. It returns structured fields rather than raw schema.org, which crosses the menu/food-guru boundary. It also fails on Ottolenghi-style control characters just as we do today, and it has no Smitten Kitchen scraper (generic microdata works). It does extract BBC correctly, so it's useful as a dev-only cross-check (see Verification).
  - Jetpack has no `itemprop="recipeInstructions"`. The steps sit in the h-recipe `.e-instructions` block, so the reader also fills `recipeInstructions` from it when the itemprop is missing. That isn't needed for the ingredient work, but food-guru will want it.

### 5. Docs
- AGENTS.md:
  - drop the "known limitation" paragraph;
  - add the robots, circuit-breaker and access-policy rules to **Scraping rules**;
  - note microdata in the extract description.
- `integration_list.md`: add a status column (tier / blocked reason / date probed).

## Site PRs (one per site, in this order)

Each one adds a frozen `SiteConfig` in `menu/ingest/sites/<name>.py` and registers it in `sites/__init__.py`. Each also needs saved-page fixtures, trimmed to the structured-data block, plus `tests/ingest/fixtures/<site>_lines.json` with ingredient lines taken verbatim from the cached pages, following `bbc_lines.json`. Run with `uv run menu-ingest <site>`.

### A. King Arthur Baking: config only, which proves the multi-site path
```python
KING_ARTHUR = SiteConfig(
    name="king_arthur",
    base_url="https://www.kingarthurbaking.com",
    sitemap_urls=("https://www.kingarthurbaking.com/sitemap.xml",),
    url_pattern=r"^https://www\.kingarthurbaking\.com/recipes/[^/]+-recipe$",
    politeness_delay=1.0,
    unit_system="us",
)
```
- Check the pattern against all 4 sitemap pages before the crawl. Count matches against the ≈4.6k `/recipes/` URLs, and look for `/recipes/collections/…`-style non-recipes.
- Ingredient edge cases to add to the fixture and seed:
  - **Dual units**, as in `3 cups (360g) King Arthur Unbleached All-Purpose Flour`. Prefer the metric amount when `ingredient-parser-nlp` returns several, so the line doesn't depend on cup interpretation. This is a `ParsedLine` rule, not KAB-specific.
  - **Brand prefix** "King Arthur …": add aliases to `seed.py` (e.g. "king arthur unbleached all-purpose flour" → "all-purpose flour"). If the list grows, add a site-level `brand_prefixes` setting that strips the prefix before canonicalisation.
  - **Footnote asterisks** and parenthesised choices: `milk, (skim, 1%, 2% or whole, your choice)*`.
  - `recipeYield` is a list (`['16', '1 loaf']`). Check that the servings parser takes the numeric serving count.
- Crawl ≈ 4.6k pages × 1 s ≈ 80 min, cached afterwards.

### B. Ottolenghi
```python
OTTOLENGHI = SiteConfig(
    name="ottolenghi",
    base_url="https://ottolenghi.co.uk",
    sitemap_urls=("https://ottolenghi.co.uk/sitemap_metaobject_pages_1.xml",),
    url_pattern=r"^https://ottolenghi\.co\.uk/pages/recipes/[^/]+$",
    politeness_delay=1.0,
    unit_system="imperial",
)
```
- Depends on generic changes #1 (a urlset as the entry point) and #4 (`strict=False`).
- The Shopify sitemap filenames could change. If the metaobject sitemap 404s, fall back to the index and let `url_pattern` filter.
- Edge cases:
  - no `recipeYield`, so `servings` stays NULL and scaling must cope;
  - `seeds from 10 cardamom pods`;
  - `2 ¼ tsp cumin seeds, plus 1 tsp extra left whole` (the existing "plus extra" handling);
  - heavy spice and herb vocabulary, so expect review-queue and alias seeding work.

### C. Smitten Kitchen
```python
SMITTEN_KITCHEN = SiteConfig(
    name="smitten_kitchen",
    base_url="https://smittenkitchen.com",
    sitemap_urls=(
        "https://smittenkitchen.com/sitemap.xml",
        "https://smittenkitchen.com/sitemap-2.xml",
    ),
    url_pattern=r"^https://smittenkitchen\.com/\d{4}/\d{2}/[^/]+/$",
    politeness_delay=3.0,
    unit_system="us",
)
```
- Depends on #1 (plain urlsets and multiple entry sitemaps) and #4 (microdata).
- It's a one-person blog, so the delay is higher: ≈1.5k posts × 3 s ≈ 75 min.
- Not every dated post is a recipe. Extraction already decides that ("a page is a recipe iff it yields a Recipe object"), so posts without a recipe count as fetched, not stored.
- Edge cases:
  - word quantities ("Half a red onion");
  - prose yields (`Servings: 3 to 4`), where the range rule takes the upper end;
  - US volume units throughout.

### D. Epicurious (only if you approve Tier 2)
JSON-LD works as it is. `sitemap_urls=("https://www.epicurious.com/sitemap.xml",)`, `url_pattern=r"^https://www\.epicurious\.com/recipes/food/views/[^/]+$"`, `unit_system="us"`.
- The 60 monthly sitemaps only reach back about 5 years, so the older Gourmet/Bon Appétit archive may not be in them. Check before assuming full coverage.
- Edge case: abbreviations with periods (`2 Tbsp.`).

## Verification
- After each PR: `uv run pytest`, `uv run mypy menu tests`, `uv run ruff check .`.
- Optional cross-check for a new site: run `uv run --no-project --with recipe-scrapers` over its saved fixtures with `scrape_html(..., supported_only=False)`, and compare name, yield and ingredient lines with our `extract_recipe_data`. Don't add it as a dependency.
- Before a full site crawl, do a smoke run capped to one sub-sitemap. Check that stored/fetched is plausible, then look at the review queue (`ingredient_id IS NULL`) for the new site.
- `uv run menu-ingest <site> --reparse` after alias seeding, to re-resolve without re-fetching.

## Order and size
1. Generic PR (#1–#5): the biggest piece, with no new site.
2. King Arthur: config plus aliases.
3. Ottolenghi: config plus aliases.
4. Smitten Kitchen: config plus aliases.
5. (Epicurious, pending the decision.)
