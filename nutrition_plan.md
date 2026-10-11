# Grams as the common scale, and derived nutrition: plan

## Context

The same ingredient is measured inconsistently across recipes: "1 egg" in one, "50 ml egg" in another, "2 tbsp butter" next to "100 g butter". The data should be structured so every line sits on one scale. Recipes with no published nutrition should get it derived from their ingredients, using quantity and preparation. Like the rest of the ingredients prototype, this lives on the scratch DB and graduates to food-guru with the ingest.

### Findings from the scratch DB (2026-10-04)
- 7,689 recipes, 96,570 ingredient lines, 8,229 canonical ingredients.
- **1,226 ingredients appear in more than one dimension** (mass/volume/count). That covers butter, egg, table salt, granulated sugar, flour, garlic, olive oil, milk, water and onion. Only **4 densities and 1 unit weight** are seeded (`seed.DENSITIES_G_PER_ML`, `seed.UNIT_WEIGHTS_G`), so `aggregate` can fold almost none of them and they stay as separate lines ("250 ml + 100 g").
- **The usage is concentrated:** the top 100 canonical ingredients cover 57% of resolved lines, the top 300 cover 73%, the top 500 79% and the top 1,000 87%. Hand review by frequency is tractable.
- **Parser FDC ids:** 5,114 of 8,229 ingredients carry a parser `fdc_id` (63,481 of 92,422 resolved lines). The ids come from the parser's bundled `fdc_ingredients.csv.gz`, which holds SR Legacy (6,798), FNDDS (3,912) and Foundation (395) foods. The matches are fuzzy, as the `--fdc-report` work showed (almond/vanilla/rosewater extract share an id), so they are candidates, not facts.
- **FDC has the conversion data.** AGENTS.md says FDC has "no usable density". It has no density *field*, but SR Legacy and FNDDS have a `food_portion` table with gram weights per household measure. For example, "Egg, whole, raw, fresh" (fdc 171287) gives `1 cup = 243 g` (density 1.03 g/ml) and size-specific weights: small 38, medium 44, large 50, extra large 56, jumbo 63 g. The same record carries nutrients per 100 g.
- **Ground truth exists:** BBC (3,741 recipes) and King Arthur (2,468) publish `nutrition` in their JSON-LD.
- **Gaps:**
  - Ottolenghi publishes no `recipeYield`, so `servings` is NULL for all 559 of its recipes.
  - Smitten Kitchen publishes no nutrition.
  - 13,495 lines have no quantity.
- **Parser junk:**
  - "heaping", "scant" and "knob" are stored as count `original_unit`s.
  - "pinch" lands in the count dimension (187 butter/salt lines), where it should be a small volume.
- **Regional mix:** BBC and Ottolenghi are UK sites; King Arthur and Smitten Kitchen are US sites. UK staples (caster sugar, double cream, golden syrup, self-raising flour) are thin in FDC. Ottolenghi leans on Middle Eastern and Asian ingredients (sumac, za'atar, miso, gochujang, tamarind) that national US/UK tables cover unevenly.

## Sources

No single table covers every ingredient, so the model is **multi-source with a per-value provenance**. Licences were checked on 2026-10-04 (links at the end). Each one must be re-confirmed on its own licence page before its data is imported. Food-guru is a product, so non-commercial and share-alike terms are out.

### Candidate sources

| Source | Region | Gives | Licence / terms | Use |
|---|---|---|---|---|
| **USDA FDC** (SR Legacy, FNDDS, Foundation) | US | nutrients + **portion weights** (cup/tbsp/size) | public domain | **primary for conversion**; nutrients for US sites |
| **CoFID** (McCance & Widdowson, gov.uk download) | UK | nutrients, ~2,900 UK foods | Open Government Licence. The separate CoFID *API* is CC BY-NC-SA, so don't use the API | **primary nutrients for UK sites** |
| **Canadian Nutrient File** | Canada | nutrients + **conversion factors to household measures** | free if Health Canada is cited and values aren't modified | second source of portion weights |
| **Fineli** (THL) | Finland | nutrients, ~5,000 foods | CC BY 4.0 | European gap-filler |
| **CIQUAL** (ANSES) | France | nutrients, ~3,000 foods | free download; licence text to confirm | European gap-filler |
| **NEVO** (RIVM) | Netherlands | nutrients | free. Values must stay unchanged and be cited with their version in any output ("Based on data from NEVO online version …"). Additions are allowed if marked | gap-filler, only if the citation duty is acceptable |
| **BLS 4.0** (Max Rubner Institut) | Germany | nutrients for 7,140 foods and dishes, up to 138 nutrients | free since 16 Dec 2025; exact licence text to confirm | strong European candidate once the licence is confirmed |
| **Frida** (DTU) | Denmark | nutrients, ~1,000 foods | free download; licence to confirm | low priority |
| **Standard Tables of Food Composition in Japan**, 8th ed. 2023 (MEXT) | Japan | nutrients, Japanese staples (miso, mirin, dashi, tofu, seaweeds) | Government of Japan Standard Terms of Use (attribution) | **Asian gap-filler** |
| **Korean Food Composition Table** (RDA) | Korea | nutrients, ~2,700+ foods (gochujang, doenjang, kimchi) | free online access; licence to confirm (also listed on data.go.kr) | Asian gap-filler, once the licence is confirmed |
| **ASEAN FCT / Thai FCD** (INMU, Mahidol) | Southeast Asia | nutrients compiled from six ASEAN national tables | access via the regional centre; terms unclear | candidate only |
| **IFCT 2017** (ICMR-NIN) | India | 528 key foods, 151 components | open access but **PDF and app only**, no machine-readable release; terms unclear | not importable as is |
| **China Food Composition Table** (China CDC) | China | nutrients | copyrighted; access by request | **excluded** |
| **FAO/INFOODS Density Database v2.0** | international | **density** for 638 foods | licence not confirmed | density gap-filler, only once confirmed |
| Open Food Facts | global, branded | nutrients of branded products | ODbL (share-alike) | **excluded**: share-alike licence and noisy data |
| Edamam / Nutritionix / Spoonacular | — | parsing + nutrition as a service | paid; storing results is restricted | **excluded**: food-guru must own its data |
| Site-published nutrition (BBC, King Arthur) | — | per-recipe totals | the site's | **validation only**, never an ingredient source |
| Hand seeds (`seed.py`) | — | overrides | ours | always wins |

### Rules
- **Import a source only when coverage needs it.** Start with FDC + CoFID + CNF. Add the others one at a time when the coverage report (step 6) shows unmapped high-frequency ingredients that a source would cover. The likely next one is the Japanese tables for Ottolenghi's Asian ingredients.
- **Conversion and nutrition are separate lookups.** Portion weights and densities come mainly from FDC and CNF (seed > FDC > CNF > INFOODS). The European and Asian tables are mostly per-100 g nutrient tables without household measures. An ingredient can take its weight from FDC and its nutrients from CoFID.
- **Nutrient definitions differ between tables.** Sources disagree on energy conversion factors, carbohydrate (by difference vs available), fibre method and whether values are per edible portion. Store each value with its source and the nutrient's definition code. Map to our fixed nutrient set through an explicit per-source table, and never compare or mix across definitions silently. The Japanese tables give values per 100 g edible portion, with a refuse percentage, which is useful for "1 onion" but has to be applied explicitly.
- **Attribution is data.** Every source row keeps a version and citation string, so food-guru can print the required notices. This is mandatory for NEVO and CNF and good practice for the rest.

## Approach

### 1. Reference data import (`menu/nutrition/sources/`)
- One module per source (`fdc.py`, `cofid.py`, `cnf.py`, later `jp_mext.py` …), each with a one-off import command. Download the bulk release once into `.cache/sources/<source>/`, which is gitignored like the page cache. These are bulk files, not a crawl, so no politeness rules apply. Don't call any API at runtime: `DEMO_KEY` and other API limits make results unreproducible.
- Common reference tables, so the rest of the code doesn't care which source a value came from:
  - `food_source(id, name, version, licence, citation)`
  - `ref_food(id, source_id, source_food_id, description, category, refuse_pct NULL)`
  - `ref_portion(ref_food_id, amount, unit, modifier, gram_weight)` — from FDC and CNF
  - `ref_density(ref_food_id, density_g_per_ml)` — from INFOODS, if added
  - `ref_nutrient(ref_food_id, nutrient, amount_per_100g, definition)`
  - `nutrient_map(source_id, source_nutrient_code, nutrient, definition)`
- Limit nutrients to a fixed set: energy kcal, protein, fat, saturated fat, carbohydrate, sugars, fibre, salt/sodium. That matches what BBC and King Arthur publish, so validation compares like with like.

### 2. Confirmed mapping per canonical ingredient
- Replace the single `ingredient.fdc_id` with `ingredient_food_ref(ingredient_id, ref_food_id, role, confirmed)`, where role = `conversion` | `nutrition`. One ingredient can have one confirmed reference per role per region preference.
- The parser's `fdc_id` seeds an *unconfirmed* candidate. Only confirmed references feed conversion and nutrition, consistent with the `--fdc-report` rule (suggest, never auto-merge).
- Add `seed.FOOD_REFS: dict[str, list[tuple[str, str, str]]]` (ingredient → (source, source_food_id, role)) for hand-reviewed mappings, worked through in order of line count.
- Add a report command, `menu-ingest <site> --ref-review`. It lists the top-N ingredients without a confirmed reference, with the parser candidate plus the best text matches from each imported source.
- Add `SiteConfig.nutrition_sources: tuple[str, ...]`, the preferred order next to `unit_system`. For example BBC/Ottolenghi = `("cofid", "fdc", …)` and King Arthur/Smitten Kitchen = `("fdc", "cnf", …)`. Seeds always win.

### 3. Grams on every line (`menu/ingest/units.py`, `menu/db/ingredients/grams.py`)
- Add `recipe_ingredient.grams NULL` and `grams_max NULL`. Keep the original quantity, unit and base-unit columns as they are, since `grams` is derived. Also store `grams_source` (`seed` | the ref source) for provenance.
- Make conversion one function, `to_grams(line, ingredient, portions) -> float | None`:
  - **mass:** use as is.
  - **volume:** × density. The density comes from a portion for the cup, tbsp or tsp, preferring one whose `modifier` matches the line's `preparation` ("chopped", "sliced", "sifted"). Then use `ref_density`, then the ingredient's default.
  - **count:** × unit weight. The weight comes from the portion matching the line's `size` (small/medium/large), falling back to the medium or default portion.
  - **named units** (clove, slice, stick, can, bunch): use the portion with that name, or a seeded weight.
- Fix the fuzzy units before conversion:
  - "pinch", "dash" and "knob" become fixed amounts (pinch ≈ 0.36 ml, knob of butter ≈ 15 g), seeded.
  - "heaping" and "scant" become qualifiers on the real unit, not units.
- Part-of and yield lines:
  - "2 egg yolks" maps to the yolk food, not whole egg.
  - "juice of 1 lemon" and "1 orange zested and juiced" use a seeded yield (juice per lemon ≈ 30 ml).
- Precedence: seed > FDC portion > CNF conversion factor > INFOODS density > NULL. Never guess. A line without a conversion keeps `grams = NULL` and shows up in the coverage report.
- Move `seed.DENSITIES_G_PER_ML` and `seed.UNIT_WEIGHTS_G` to the override layer. Derive `ingredient.density_g_per_ml` and `unit_weight_g` from the reference data when no override exists.
- Switch `aggregate` to grams when every line of an ingredient has them. The existing per-dimension fold stays as the fallback.

### 4. Preparation that changes the food
- `preparation` normally changes density only ("chopped", "softened", "beaten"). Handle that in step 3.
- When the state changes the food itself, map it to a different reference food: cooked/raw rice, pasta and grains; drained/dried beans; fried onions; desiccated/fresh coconut; toasted nuts.
- Add `seed.PREPARED_FORMS: dict[tuple[str, str], tuple[str, str]]` mapping (canonical ingredient, preparation keyword) → (source, source_food_id), and resolve it when computing nutrition.
- **Deferred:** cooking-method effects described in the instructions (frying oil absorption, water loss, vitamin loss) using USDA retention and yield factors. Revisit only if step 6 shows systematic error on fried or reduced dishes.

### 5. Derived nutrition (`menu/nutrition/derive.py`)
- `derive_nutrition(recipe_id) -> RecipeNutrition`: for each counted line (skipping alternatives and optional lines), take grams × nutrient per 100 g from the first confirmed nutrition reference in the site's `nutrition_sources` order (or a prepared form), then sum.
- Ranges use the midpoint for nutrition. The shopping list's upper end is about buying, not eating.
- Lines with no quantity ("oil for frying", "salt to serve") contribute nothing and are counted.
- Per serving means total ÷ `servings`. When `servings` is NULL (all of Ottolenghi), store totals only.
- Store a coverage figure with each result:
  - the share of the recipe's grams that came from mapped lines
  - the number of unconverted lines
  - the sources used
  - whether nutrient definitions were mixed

  Low-coverage results are marked, not trusted.
- Store the results in a `recipe_nutrition` table keyed by recipe, with `source` = `derived` | `published`. Published values are parsed from the JSON-LD `nutrition` block ("350 calories", "12 g").

### 6. Validation and coverage reports
- **Validation:** for BBC and King Arthur recipes with published nutrition and coverage ≥ 0.9, compare derived vs published kcal, protein, fat and carbohydrate per serving. Show:
  - the error distribution per site **and per nutrition source**. BBC probably computes from UK tables, so CoFID should score closer there, which is a sanity check on the source order.
  - the worst recipes
  - the ingredients most often present in high-error recipes
- **Coverage:** list the high-frequency ingredients with no confirmed reference in any imported source, grouped by site. This decides which regional source to import next.
- This is the feedback loop for steps 2–4: improve the seeds or add a source, re-run, and the error should fall.

### 7. Boundary and docs
- Widen the AGENTS.md exception from "ingredient-line parsing" to "ingredient structuring and nutrition prototype". It still graduates to food-guru with the ingest.
- Correct the AGENTS.md FDC sentence: density and unit weight come from FDC `food_portion` (and CNF conversion factors), with hand-seeded overrides.
- Add an AGENTS.md rule next to "Access policy is not negotiable": only reference sources whose licence allows commercial use without share-alike, and keep each source's citation.

## Order of work
1. Common reference tables + FDC import (step 1), then CoFID and CNF.
2. Grams conversion and fuzzy-unit fixes (step 3). Confirm references for the top 100 ingredients first (step 2) so the conversion has data.
3. Derived nutrition and validation (steps 5–6). Use the error and coverage reports to choose what to seed or which source to import next.
4. Prepared forms (step 4), guided by the validation report.
5. Regional sources (Japan MEXT, then Fineli/BLS/Korea RDA once their licences are confirmed), driven by the coverage report.

## Critical files
- New:
  - `menu/nutrition/{__init__,derive}.py`
  - `menu/nutrition/sources/{__init__,fdc,cofid,cnf}.py`
  - `menu/db/nutrition/{repository,actions}.py`
  - `tests/nutrition/…`
  - `tests/nutrition/fixtures/<source>_sample.*` (small hand-picked slices of each source; tests never download)
- Modified:
  - `menu/db/database.py`
  - `menu/ingest/units.py`
  - `menu/ingest/registry.py` (`nutrition_sources`)
  - `menu/db/ingredients/{actions,seed}.py`
  - `menu/ingest/__main__.py`
  - `AGENTS.md`

## Verification
- `tests/ingest/test_units.py`:
  - 1 large egg → 50 g, 1 medium egg → 44 g, 1 cup egg → 243 g
  - 2 tbsp butter → grams via a portion
  - a pinch of salt → grams
  - "heaping tbsp" keeps tbsp as the unit
  - a seed beats FDC, which beats CNF
  - no data → `grams` NULL
- Aggregation: a recipe with "1 egg" and one with "50 ml egg" aggregate to a single egg line in grams.
- `tests/nutrition/`:
  - each source importer maps its fixture slice into the common tables, including nutrient definitions
  - derived nutrition for a fixture recipe, with hand-computed expected values
  - the site's source order is respected: a BBC recipe picks CoFID nutrients over FDC
  - coverage is reported when a line is unconverted or definitions are mixed
  - a published-nutrition parser over the real BBC and King Arthur `nutrition` blocks, saved as fixtures
- `uv run pytest`, `uv run mypy menu tests`, `uv run ruff check .`
- Manual check: run the validation report on the cached sites and record the median kcal error per site and per source in this file as a baseline.

## Status and baseline (2026-10-04)

Steps 1–7 are implemented for FDC, CoFID and CNF (`menu-fdc-import`, `menu-cofid-import`, `menu-cnf-import`). Conversion precedence is seed > FDC > CNF (`seed.CONVERSION_SOURCES`).

Imported:
- CoFID 2021: 2,886 foods, 21,265 nutrient values, no portions. Code 13-669 is printed twice in the workbook (roasted aubergine and watercress); the import keeps watercress.
- CNF 2015: 5,690 foods, 19,340 portions, 43,969 nutrient values. 165 measures have no leading amount ("dough for 1") and are skipped.

After `--reparse` of all four sites and `--derive-nutrition`:
- 56,889 of 96,494 lines have grams (FDC 11,445 via portions; seed 10,009; the rest are mass lines).
- Mean derivation coverage is 0.57; 1,677 recipes reach ≥ 0.9.
- Only 79 ingredients have a confirmed nutrition reference, all FDC. No CoFID or CNF food is confirmed yet, so every validated recipe ran "via fdc".

Validation baseline (coverage ≥ 0.9, median relative error per serving):

| Site | Recipes | kcal | protein | fat | carbohydrate |
|---|---|---|---|---|---|
| King Arthur (via FDC) | 775 | 14% | 24% | 11% | 14% |
| BBC Good Food (via FDC) | 585 | 53% | 66% | 48% | 46% |

Next, driven by the reports:
- **BBC's error is about four times King Arthur's.** Check servings first: ranged yields take the upper end ("serves 4-6" → 6), which pulls the per-serving value down, and 65 BBC recipes have servings = 0. Then confirm CoFID references for the UK staples, which this report can then score against FDC.
- **Outliers above 1,000%** (King Arthur tortillas, muffins; BBC confit garlic, which is mostly oil that's not eaten) point at unit or yield errors on single lines; check them by hand.
- **Coverage report top gaps:** BBC: rapeseed oil, red pepper, cocoa powder, bicarbonate of soda, cucumber. King Arthur: dutch-process cocoa, cornstarch, almond extract, vegetable shortening. These are the next `seed.FOOD_REFS` entries; `--ref-review` now shows CoFID and CNF candidates too.

## Source links (checked 2026-10-04)
- FDC: live record check via `api.nal.usda.gov/fdc/v1/food/171287`
- CoFID: https://www.gov.uk/government/publications/composition-of-foods-integrated-dataset-cofid · https://fnnbri.quadram.ac.uk/?p=34
- CNF: https://www.canada.ca/en/health-canada/services/food-nutrition/healthy-eating/nutrient-data/frequently-asked-questions-canadian-nutrient-file.html
- Fineli: https://www.suomi.fi/services/eservice/fineli-finnish-food-composition-database-the-national-institute-for-health-and-welfare-thl/ba003f23-14e6-46b9-985c-30d654e35ef2
- CIQUAL: https://www.anses.fr/en/node/79557
- NEVO: https://www.rivm.nl/sites/default/files/2025-11/Conditions-of-use-NEVO-online-2025-dataset_0.pdf
- BLS 4.0: https://heise.de/-11123877
- Frida: https://www.food.dtu.dk/english/About-us/Facilities-and-infrastructure/Food-Data-database-on-nutrients-in-food
- Japan MEXT: https://data.e-gov.go.jp/data/en/dataset/mext_20160325_0012/resource/0efafec4-1da0-43fe-9a75-effce26ca643 · https://food.chemlinked.com/news/food-news/japan-updates-food-composition-standard-tables
- Korea RDA: https://koreanfood.rda.go.kr/eng/fctFoodSrchEng/main
- ASEAN / Thai FCD: https://inmu.mahidol.ac.th/aseanfoods/composition_data.html
- IFCT 2017: https://www.fao.org/food-composition/tables-and-databases/detail/(country--date)-title-9/en
- China: https://www.fao.org/infoods/infoods/tables-and-databases/china/en/
- INFOODS density: https://openknowledge.fao.org/handle/20.500.14283/ap805e
