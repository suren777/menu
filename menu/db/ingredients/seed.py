"""Hand-seeded ingredient data: aliases, densities, unit weights, FDC ids.

Three things cannot come from the parser and are curated here by hand:

- Variant canonicalisation ("warm milk" -> "milk") is alias-driven;
  the parser keeps modifiers fused into the name.
- USDA FoodData Central has no directly usable density, so
  volume->mass aggregation needs these measured values.
- The parser's fdc_id is a suggestion and often wrong; FDC_IDS
  carries hand-reviewed mappings.

Rerunning is idempotent: missing rows are created, and an alias that
already exists but points elsewhere (typically a parse-time self-alias
of a variant name) is re-pointed to the canonical ingredient.
"""

from typing import TYPE_CHECKING

from sqlalchemy import select

from menu.db.database import Ingredient, IngredientAlias

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

# alias -> (canonical ingredient, variant)
ALIASES: list[tuple[str, str, str | None]] = [
    ("warm milk", "milk", "warm"),
    ("whole milk", "milk", "whole"),
    ("salted butter", "butter", "salted"),
    ("unsalted butter", "butter", "unsalted"),
    ("melted butter", "butter", "melted"),
    ("plain flour", "flour", "plain"),
    ("bread flour", "flour", "bread"),
    ("strong white bread flour", "flour", "bread"),
    ("egg white", "egg", "white"),
    ("egg yolk", "egg", "yolk"),
    ("instant dried yeast", "fast-action dried yeast", None),
    # The parser turns "rosewater or vanilla extract" into
    # "rosewater extract"; map it back to rosewater.
    ("rosewater extract", "rosewater", None),
    (
        "whole blanched almonds roughly chopped",
        "blanched almond",
        "whole, roughly chopped",
    ),
    # King Arthur recipes carry the brand in the ingredient name;
    # the 5-word names would otherwise all land in review. Bread
    # flour folds into the existing "flour" canonical rather than
    # fragmenting a second one.
    (
        "king arthur unbleached all-purpose flour",
        "all-purpose flour",
        None,
    ),
    ("king arthur unbleached bread flour", "flour", "bread"),
    ("king arthur semolina flour", "semolina flour", None),
    ("king arthur baker's special dry milk", "nonfat dry milk", None),
    ("king arthur artisan bread topping", "bread topping", "artisan"),
    ("king arthur the works bread topping", "bread topping", "the works"),
]

# Whitespace-normalised raw line -> canonical ingredient, for lines the
# parser merges wrongly. The real cached line "70g  milk or dark
# chocolate roughly chopped (optional)" (double space) parses as name
# "milk" with confidence 1.0 — no confidence threshold catches it, only
# curated data does. Lookup squashes the line's whitespace, so keys
# hold the single-spaced form. Checked before the parser's name is
# trusted.
LINE_OVERRIDES: dict[str, str] = {
    "70g milk or dark chocolate roughly chopped (optional)": "milk chocolate",
}

# g/ml, cooking-standard measured values
DENSITIES_G_PER_ML: dict[str, float] = {
    "milk": 1.03,
    "butter": 0.96,
    "flour": 0.55,
    "caster sugar": 0.85,
}

# g per piece
UNIT_WEIGHTS_G: dict[str, float] = {
    "egg": 50.0,
}

# Hand-reviewed canonical ingredient -> USDA FDC id, worked top-down
# by line count (the --fdc-review report shows what is left). Only
# confirmed mappings feed nutrition: the parser's fdc_id is a
# suggestion and often wrong — baking powder resolved to "Baobab
# powder", rapeseed oil to "Sunflower oil". Where the seed and the
# parser disagree, the seed wins. Rapeseed oil and vanilla bean paste
# are deliberately absent: no honest FDC food exists for them yet.
FDC_IDS: dict[str, int] = {
    "butter": 173430,  # Butter, without salt
    "egg": 171287,  # Egg, whole, raw, fresh
    "granulated sugar": 169655,  # Sugars, granulated
    "table salt": 173468,  # Salt, table
    "all-purpose flour": 168936,  # Wheat flour, all-purpose, enriched
    "garlic": 169230,  # Garlic, raw
    "olive oil": 2710186,  # Olive oil
    "milk": 2705385,  # Milk, whole
    "baking powder": 172803,  # Leavening agents, baking powder
    "flour": 168936,
    "vegetable oil": 172370,  # Oil, vegetable, soybean, refined
    "water": 2710707,  # Water, tap
    "lemon": 2709168,  # Lemon, raw
    "onion": 170000,  # Onions, raw
    "instant yeast": 175043,  # Yeast, baker's, active dry
    "ground cinnamon": 171320,
    "caster sugar": 169655,
    "confectioners' sugar": 2710259,
    "light brown sugar": 168833,  # Sugars, brown
    "baking soda": 175040,
    "black pepper": 170931,
    "vanilla extract": 173471,
    "heavy cream": 2705597,  # Cream, heavy
    "honey": 169640,
    "salt": 173468,
    "lemon juice": 167747,
    "dark brown sugar": 168833,
    "double cream": 2705597,
    "red onion": 170000,
    "carrot": 170393,
    "ginger": 169231,
    "spring onion": 170005,
    "lime": 168155,
    "icing sugar": 169656,  # Sugars, powdered
    "sunflower oil": 2710192,
    "parsley": 2709796,
    "coriander": 2709782,  # Cilantro, raw
    "maple syrup": 169661,
    "kosher salt": 173468,
    "ground cumin": 170923,
    "nonfat dry milk": 171272,
    "golden caster sugar": 169655,
    "walnut": 170187,  # Nuts, walnuts, english
    "cornflour": 169698,  # Cornstarch
    "tomato purée": 170460,  # Tomato puree, without salt added
    "self-raising flour": 168895,
    "fine sea salt": 173468,
    "orange": 2709171,
    "light brown soft sugar": 168833,
    "buttermilk": 2705393,
    "parmesan": 171247,  # Cheese, parmesan, grated
    "bay leaf": 170917,
    "ground ginger": 170926,
    "ground nutmeg": 171326,
    "cream cheese": 173418,
    "smoked paprika": 171329,  # Spices, paprika
    "extra virgin olive oil": 171413,  # Oil, olive, salad or cooking
    "pecan": 170182,  # Nuts, pecans
    "tomato": 2709719,  # Tomatoes, raw
    "sesame seed": 2707586,
    "dark chocolate": 2710336,  # Dark chocolate candy
    "sour cream": 171257,  # Cream, sour, cultured
    "chicken stock": 172884,  # Soup, stock, chicken, home-prepared
    "ground turmeric": 172231,
    "chopped tomato": 2709719,
    "cumin seed": 170923,
    "espresso powder": 2710378,  # Coffee, espresso
    "chive": 169994,
    "vegetable stock": 171583,  # Soup, vegetable broth, ready to serve
    "active dry yeast": 175043,
    "dried oregano": 171328,
    "shallot": 170499,
    "thyme": 173470,  # Thyme, fresh
    "coarse sparkling sugar": 169655,
    "ground coriander": 170922,  # Spices, coriander seed
    "celery": 169988,
    "dijon mustard": 2710085,  # Mustard
    "red chilli": 170106,  # Peppers, hot chili, red, raw
    "soy sauce": 2707442,
}


def _canonical_ingredient(name: str, session: Session) -> Ingredient:
    ingredient = session.scalar(select(Ingredient).where(Ingredient.name == name))
    if ingredient is None:
        ingredient = Ingredient(name=name)
        session.add(ingredient)
        session.flush()
    return ingredient


def seed_ingredient_data(session: Session) -> None:
    """Create the seed rows; safe to rerun."""
    for alias, canonical, variant in ALIASES:
        target = _canonical_ingredient(canonical, session)
        existing = session.scalar(
            select(IngredientAlias).where(IngredientAlias.alias == alias)
        )
        if existing is None:
            session.add(
                IngredientAlias(
                    alias=alias, ingredient_id=target.id, variant=variant
                )
            )
        elif existing.ingredient_id != target.id:
            existing.ingredient_id = target.id
            existing.variant = variant

    # Density and unit-weight ingredients are created even when
    # nothing points at them yet: caster sugar is no alias target, so
    # a fresh crawl would otherwise meet it before the next startup
    # and miss its density for a run.
    for name, density in DENSITIES_G_PER_ML.items():
        ingredient = _canonical_ingredient(name, session)
        ingredient.density_g_per_ml = density

    for name, weight in UNIT_WEIGHTS_G.items():
        ingredient = _canonical_ingredient(name, session)
        ingredient.unit_weight_g = weight

    # FDC mappings are hand-reviewed (the --fdc-review report shows
    # what is left): the seed wins over the parser's suggestion.
    for name, fdc_id in FDC_IDS.items():
        ingredient = _canonical_ingredient(name, session)
        ingredient.fdc_id = fdc_id
        ingredient.fdc_id_confirmed = True
