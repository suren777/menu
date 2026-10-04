# Grams as the common scale, and derived nutrition: plan

## Context

The same ingredient is measured inconsistently across recipes: "1 egg" in one, "50 ml egg" in another, "2 tbsp butter" next to "100 g butter". The data should be structured so every line sits on one scale. Recipes with no published nutrition should get it derived from their ingredients, using quantity and preparation. Like the rest of the ingredients prototype, this lives on the scratch DB and graduates to food-guru with the ingest.

### Findings from the scratch DB (2026-10-04)
- 7,689 recipes, 96,570 ingredient lines, 8,229 canonical ingredients.
- **1,226 ingredients appear in more than one dimension** (mass/volume/count). That covers butter, egg, table salt, granulated sugar, flour, garlic, olive oil, milk, water and onion. Only **4 densities and 1 unit weight** are seeded (`seed.DENSITIES_G_PER_ML`, `seed.UNIT_WEIGHTS_G`), so `aggregate` can fold almost none of them and they stay as separate lines ("250 ml + 100 g").
- **The usage is concentrated:** the top 100 canonical ingredients cover 57% of resolved lines, the top 300 cover 73%, the top 500 79% and the top 1,000 87%. Hand review by frequency is tractable.
- **FDC coverage:** 5,114 of 8,229 ingredients carry a parser `fdc_id` (63,481 of 92,422 resolved lines). The ids come from the parser's bundled `fdc_ingredients.csv.gz`, which holds SR Legacy (6,798), FNDDS (3,912) and Foundation (395) foods. The matches are fuzzy, as the `--fdc-report` work showed (almond/vanilla/rosewater extract share an id), so they are candidates, not facts.
- **FDC has the conversion data.** AGENTS.md says FDC has "no usable density". It has no density *field*, but SR Legacy and FNDDS have a `food_portion` table with gram weights per household measure. For example, "Egg, whole, raw, fresh" (fdc 171287) gives `1 cup = 243 g` (density 1.03 g/ml) and size-specific weights: small 38, medium 44, large 50, extra large 56, jumbo 63 g. The same record carries nutrients per 100 g.
- **Ground truth exists:** BBC (3,741 recipes) and King Arthur (2,468) publish `nutrition` in their JSON-LD.
- **Gaps:**
  - Ottolenghi publishes no `recipeYield`, so `servings` is NULL for all 559 of its recipes.
  - Smitten Kitchen publishes no nutrition.
  - 13,495 lines have no quantity.
- **Parser junk:**
  - "heaping", "scant" and "knob" are stored as count `original_unit`s.
  - "pinch" lands in the count dimension (187 butter/salt lines), where it should be a small volume.

## Approach

### 1. FDC reference data (`menu/nutrition/fdc.py`)
- Download the public-domain SR Legacy and FNDDS CSV releases once, as a one-off import command rather than at runtime. They are bulk files, not a crawl, so no politeness rules apply. Store them in `.cache/fdc/`, which is gitignored like the page cache.
- Load the slices we need into reference tables:
  - `fdc_food(fdc_id, data_type, description, category)`
  - `fdc_portion(fdc_id, amount, unit, modifier, gram_weight)`
  - `fdc_nutrient(fdc_id, nutrient, amount_per_100g)`
- Limit nutrients to a fixed set: energy kcal, protein, fat, saturated fat, carbohydrate, sugars, fibre, salt/sodium. That matches what BBC and King Arthur publish, so validation compares like with like.
- Don't call the FDC API at runtime: `DEMO_KEY` is rate-limited and the bulk files make the result reproducible.

### 2. Confirmed FDC mapping per canonical ingredient
- Add `ingredient.fdc_id_confirmed bool` (or a separate `fdc_mapping` source column: `parser` | `seed`). Only confirmed mappings feed nutrition. Parser ids stay suggestions, consistent with the `--fdc-report` rule (suggest, never auto-merge).
- Add `seed.FDC_IDS: dict[str, int]` for hand-reviewed mappings, worked through in order of line count.
- Add a report command, `menu-ingest <site> --fdc-review`. It lists the top-N unconfirmed ingredients with their parser candidate and FDC description, so you can review them quickly.

### 3. Grams on every line (`menu/ingest/units.py`, `menu/db/ingredients/actions.py`)
- Add `recipe_ingredient.grams NULL` and `grams_max NULL`. Keep the original quantity, unit and base-unit columns as they are, since `grams` is derived.
- Make conversion one function, `to_grams(line, ingredient, portions) -> float | None`:
  - **mass:** use as is.
  - **volume:** × density. The density comes from the FDC portion for the cup, tbsp or tsp, preferring a portion whose `modifier` matches the line's `preparation` ("chopped", "sliced", "sifted"). Otherwise use the ingredient's default.
  - **count:** × unit weight. The weight comes from the FDC portion matching the line's `size` (small/medium/large), falling back to the medium or default portion.
  - **named units** (clove, slice, stick, can, bunch): use the FDC portion with that name, or a seeded weight.
- Fix the fuzzy units before conversion:
  - "pinch", "dash" and "knob" become fixed amounts (pinch ≈ 0.36 ml, knob of butter ≈ 15 g), seeded.
  - "heaping" and "scant" become qualifiers on the real unit, not units.
- Part-of and yield lines:
  - "2 egg yolks" maps to the yolk food, not whole egg.
  - "juice of 1 lemon" and "1 orange zested and juiced" use a seeded yield (juice per lemon ≈ 30 ml).
- Precedence: a seeded override beats an FDC portion, which beats NULL. Never guess. A line without a conversion keeps `grams = NULL` and shows up in the coverage report.
- Move `seed.DENSITIES_G_PER_ML` and `seed.UNIT_WEIGHTS_G` to the override layer. Derive `ingredient.density_g_per_ml` and `unit_weight_g` from FDC when no override exists.
- Switch `aggregate` to grams when every line of an ingredient has them. The existing per-dimension fold stays as the fallback.

### 4. Preparation that changes the food
- `preparation` normally changes density only ("chopped", "softened", "beaten"). Handle that in step 3.
- When the state changes the food itself, map it to a different FDC food: cooked/raw rice, pasta and grains; drained/dried beans; fried onions; desiccated/fresh coconut; toasted nuts.
- Add `seed.PREPARED_FORMS: dict[tuple[str, str], int]` mapping (canonical ingredient, preparation keyword) → fdc_id, and resolve it when computing nutrition.
- **Deferred:** cooking-method effects described in the instructions (frying oil absorption, water loss, vitamin loss) using USDA retention and yield factors. Revisit only if step 6 shows systematic error on fried or reduced dishes.

### 5. Derived nutrition (`menu/nutrition/derive.py`)
- `derive_nutrition(recipe_id) -> RecipeNutrition`: for each counted line (skipping alternatives and optional lines), take grams × nutrient per 100 g from the confirmed fdc_id or prepared form, then sum.
- Ranges use the midpoint for nutrition. The shopping list's upper end is about buying, not eating.
- Lines with no quantity ("oil for frying", "salt to serve") contribute nothing and are counted.
- Per serving means total ÷ `servings`. When `servings` is NULL (all of Ottolenghi), store totals only.
- Store a coverage figure with each result: the share of the recipe's grams that came from mapped lines and the number of unconverted lines. Low-coverage results are marked, not trusted.
- Store the results in a `recipe_nutrition` table keyed by recipe, with `source` = `derived` | `published`. Published values are parsed from the JSON-LD `nutrition` block ("350 calories", "12 g").

### 6. Validation against published nutrition
- For BBC and King Arthur recipes with published nutrition and coverage ≥ 0.9, compare derived vs published kcal, protein, fat and carbohydrate per serving.
- The report shows:
  - the error distribution per site
  - the worst recipes
  - the ingredients most often present in high-error recipes, which are the mapping and conversion bugs to fix first
- This is the feedback loop for steps 2–4: improve the seeds, re-run, and the error should fall.

### 7. Boundary and docs
- Widen the AGENTS.md exception from "ingredient-line parsing" to "ingredient structuring and nutrition prototype". It still graduates to food-guru with the ingest.
- Correct the AGENTS.md FDC sentence: density and unit weight come from FDC `food_portion`, with hand-seeded overrides.

## Order of work
1. FDC import and reference tables (step 1).
2. Grams conversion and fuzzy-unit fixes (step 3). Work through the top 100 ingredients for confirmed mappings first (step 2) so the conversion has data.
3. Derived nutrition and validation (steps 5–6). Use the error report to choose what to seed next.
4. Prepared forms (step 4), guided by the validation report.

## Critical files
- New:
  - `menu/nutrition/{__init__,fdc,derive}.py`
  - `menu/db/nutrition/{repository,actions}.py`
  - `tests/nutrition/…`
  - `tests/nutrition/fixtures/fdc_sample.csv` (a small hand-picked slice of the FDC CSVs; tests never download)
- Modified:
  - `menu/db/database.py`
  - `menu/ingest/units.py`
  - `menu/db/ingredients/{actions,seed}.py`
  - `menu/ingest/__main__.py`
  - `AGENTS.md`

## Verification
- `tests/ingest/test_units.py`:
  - 1 large egg → 50 g, 1 medium egg → 44 g, 1 cup egg → 243 g
  - 2 tbsp butter → grams via the FDC portion
  - a pinch of salt → grams
  - "heaping tbsp" keeps tbsp as the unit
  - a seeded override beats FDC
  - no data → `grams` NULL
- Aggregation: a recipe with "1 egg" and one with "50 ml egg" aggregate to a single egg line in grams.
- `tests/nutrition/`:
  - derived nutrition for a fixture recipe, with hand-computed expected values from the fixture slice
  - coverage is reported when a line is unconverted
  - a published-nutrition parser over the real BBC and King Arthur `nutrition` blocks, saved as fixtures
- `uv run pytest`, `uv run mypy menu tests`, `uv run ruff check .`
- Manual check: run the validation report on the cached sites and record the median kcal error per site in this file as a baseline.
