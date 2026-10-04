# Normalised ingredient model: review and prototype plan

## Context

Shopping lists show duplicates ("warm milk" and "milk") and mix unit systems (lb, kg, tbsp, ml) because each ingredient is stored as one free-text string. The user wants to know whether the data structure is sound and which recipe edge cases it must handle, then prototype a fix in **menu**. menu is a lab, and the code will later move to food-guru.

### Current schema (`menu/db/database.py`)
- `recipe_urls(id, url UNIQUE, name, data JSON)` and `sitemap(id, url UNIQUE, site, completed)`.
- Ingredients exist only as `data.recipeIngredient`, a list of free-text strings. Nothing is parsed, so it can't be aggregated, converted or deduplicated. This is the root cause of the shopping-list bugs.
- Scale gaps:
  - `recipe_urls` has no `site` column and no `fetched_at`.
  - `name` has no index.
  - There's no recipe↔ingredient relation, so "recipes using X" means a full JSON scan.
  - `create_all` has no migrations. That's acceptable only because the DB is scratch (AGENTS.md).

### Edge cases seen in the cached BBC pages (`.cache/*.html`)
| Case | Example | Needs |
|---|---|---|
| State/prep fused with the name | `240ml warm milk`, `25g butter softened`, `2 eggs beaten`, `115g melted ghee` | `preparation` separate from the canonical ingredient |
| Variety vs ingredient | `whole milk` / `milk`, `salted butter` / `unsalted butter` / `butter` | alias → canonical ingredient, keeping a variant |
| Same ingredient twice in one recipe | cordon bleu: flour, butter and emmental each appear twice (two sections) | sum per ingredient; keep `position` and `section` |
| Alternatives | `rosewater or vanilla extract`, `milk or melted butter`, `2 tsp vanilla or 1 tsp essential oil` | count only the first option; store the others as alternatives |
| "Plus extra" | `250g butter plus extra for the tin`, `bread flour plus 20g for the yukone` | main quantity plus a note; a second quantity is optional |
| Optional | `45g desiccated coconut (optional)` | `optional` flag; optionally left off the shopping list |
| No quantity | `oil for proving`, `sea salt flakes to serve`, `pink and yellow food colouring` | quantity NULL; listed as "to taste/as needed", never summed |
| Count units | `2 eggs`, `4 slices ham`, `½ lemon zested`, `12 biscuits` | `count` dimension; `slice` stays its own unit |
| Fractions | `½ tsp`, `½ lemon` | parse unicode fractions and ranges (`2-3`) |
| Size modifier | `5 medium eggs`, `1 large onion` | `size` field; doesn't change identity |
| Cross-dimension | `1 tbsp milk` plus `250ml milk` vs `100g butter` plus `1 tbsp butter` | volume↔mass only with a per-ingredient density, otherwise kept as separate lines |
| Ambiguous units | cup (US 236.6 ml / metric 250 ml), pint (US / imperial), "stick" butter | pick the system from the site's locale |
| One purchase, several uses | `1 orange zested and juiced`, `egg yolks` plus `egg white` | maps to the whole item (orange, egg) |
| Yield | `12`, `Serves 12`, `Makes 1 loaf` | parse it into `servings` for scaling |

## Approach

### 1. Parsing: use `ingredient-parser-nlp` (strangetom/ingredient-parser)
- It returns `name`, `size`, `amount[]`, `preparation`, `comment`, `purpose` and `foundation_foods`. Amounts come with `quantity`/`quantity_max` (ranges), flags (APPROXIMATE, RANGE, SINGULAR) and units as `pint.Unit`.
- It has a `volumetric_units_system` option for US vs imperial cups and pints, which BBC needs.
- `foundation_foods=True` links names to USDA FoodData Central, which gives a canonical identity for dedupe.
- **Step 0: confirm it installs and passes on Python 3.14** (`uv add ingredient-parser-nlp`). If it doesn't, fall back to a rule-based parser plus `pint`, behind the same interface.
- Wrap it as a pure function, `menu/ingest/ingredients.py::parse_line(text, site) -> ParsedLine` (our own frozen dataclass), so the rest of the code doesn't depend on the library.

### 2. Units: one canonical system (`menu/ingest/units.py`)
- Store every quantity in its base unit: **mass → g, volume → ml, count → piece** (plus named count units such as slice and clove).
- Use `pint` (pulled in by the parser) for the conversion factors. Never write factors by hand.
- Volume↔mass conversion only uses `ingredient.density_g_per_ml` when it's known. Count→mass only uses `ingredient.unit_weight_g` (e.g. egg ≈ 50 g). Without those, separate lines are kept and shown as "250 ml + 100 g".
- Convert to metric or imperial **only when displaying**, never in storage.
- Add `SiteConfig.unit_system: Literal["us", "imperial", "metric"]` (BBC = imperial) in `menu/ingest/registry.py`, so site-specific behaviour stays in config as AGENTS.md requires.

### 3. Schema (new tables in `menu/db/database.py`, same Mapped/mapped_column style)
- `ingredient`: `id`, `name UNIQUE` (canonical, singular, lowercase: "milk"), `fdc_id NULL`, `density_g_per_ml NULL`, `unit_weight_g NULL`.
- `ingredient_alias`: `alias UNIQUE` → `ingredient_id`, plus a `variant` string ("whole", "unsalted"). By default the shopping list rolls variants up to the parent, with an option to keep them separate.
- `recipe_ingredient`:
  - Identity and position: `id`, `recipe_id FK recipe_urls`, `position`, `section NULL`, `raw_text` (always kept).
  - Matched ingredient: `ingredient_id NULL` (NULL means unresolved, so the row can be reviewed later).
  - Quantity: `quantity NULL`, `quantity_max NULL`, `dimension` ('mass'|'volume'|'count'|NULL), `base_unit`, `original_quantity_text`, `original_unit`.
  - Descriptors: `preparation NULL` ("warm", "melted", "softened, beaten"), `size NULL`, `note NULL`.
  - Flags: `optional bool`, `alternative_of FK self NULL`, `parse_confidence float`.
  - Index on `(ingredient_id)` and `(recipe_id, position)`.
- `recipe_urls`: add `site` and `servings NULL` (from `recipeYield`).
- Repositories and actions follow the existing pattern in `menu/db/recipe_urls/repository.py` (dataclass model + `to_model` + plain functions over a session). New: `menu/db/ingredients/{repository,actions}.py`.

### 4. Pipeline hook
- In `menu/ingest/pipeline.py`, after `add_recipe`, call `parse_line` for each `recipeIngredient` and insert `recipe_ingredient` rows.
- Errors are handled per line, logged and counted, and never stop the crawl (AGENTS.md "bad URLs don't stop the crawl").
- Also add a backfill command that re-parses stored `data` without re-fetching.

### 5. Shopping-list aggregation (prototype query/function)
- `aggregate(recipe_ids, servings_scale)` groups by `(ingredient_id, dimension)`, never by text.
- It sums `quantity` in base units, converts across dimensions when density or unit weight allow, and skips alternatives and (optionally) optional items.
- Rows with no quantity are listed once as "as needed". Unresolved rows (`ingredient_id NULL`) show `raw_text`.

### 6. Boundary note
- Update AGENTS.md: structured ingredients are now a menu prototype that will move to food-guru, and they are still built on scratch DB storage.

### 7. Follow-ups from the parser-library review (2026-10-03)
Probed over the 105 lines in `tests/ingest/fixtures/bbc_lines.json` with `ingredient-parser-nlp` 2.8.0 (latest on PyPI). Other libraries considered: the NYT phrase parser is abandoned and `ingredient-parser` succeeds it; `parse-ingredient` and `@magrinj/parse-ingredients` are JS and can't graduate to food-guru's Python ingest.

- **Flag lost conjunctions for review.** `pink and yellow food colouring gels` parses to `pink food colouring gel`: "yellow" is dropped and the name no longer contains " and ", so `name_needs_review` passes it. Extend the check: flag a line when the raw text (minus comment/purpose) has " and "/" or " but the parsed name doesn't, unless a seeded alias or `LINE_OVERRIDES` covers it. Add the line to the parse edge-case table.
- **`fdc_id` suggests aliases; it must not merge.** Shared ids catch real variants (`milk` / `warm milk`, `butter` / `unsalted butter`) but also lump distinct ingredients (`almond` / `vanilla` / `rosewater extract`; `golden caster sugar` / `light brown soft sugar`). Add a report, not an auto-merge: canonical ingredients sharing an `fdc_id` with no alias between them, listed as candidates to hand-seed into `seed.py`.
- **`parse_confidence` is not a review signal.** Every line scored ≥0.86, mangled ones included. Keep storing it for later comparison, and keep the review queue on the heuristics.

## Critical files
- New: `menu/ingest/ingredients.py`, `menu/ingest/units.py`, `menu/db/ingredients/{repository,actions}.py`, `tests/ingest/test_ingredients.py`, `tests/ingest/test_units.py`, `tests/db/ingredients/…`
- Modified: `menu/db/database.py`, `menu/ingest/registry.py`, `menu/ingest/sites/bbc_good_food.py`, `menu/ingest/pipeline.py`, `pyproject.toml`, `AGENTS.md`

## Verification
- `tests/ingest/test_ingredients.py`: a parametrised table over every ingredient line in the edge-case table above, taken from the cached BBC pages and saved as a fixture, with no live requests. It asserts canonical name, preparation, base quantity, dimension, optional and alternative.
- `tests/ingest/test_units.py`: tbsp→ml, lb→g, oz→g, imperial vs US cup/pint, and volume→mass with and without density.
- Aggregation test: the cordon bleu recipe gives one flour line (100 g + 2 tbsp → grams via density) and one emmental line of 150 g. Basbousa plus Shokupan gives one milk line (warm milk + whole milk).
- `uv run pytest`, `uv run mypy menu tests`, `uv run ruff check .`
- Manual check: run the ingest on the cached pages, then `sqlite3 database.db` to list `ingredient_id IS NULL` rows and the `parse_confidence` distribution.
