"""Curated seed data for the ingredient seeding: pure constants.

Nothing here touches the database — seed.py imports and re-exports
these names and does all the work, so modules and tests can keep
reading (and monkeypatching) seed.<NAME>.
"""

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
    # The King Arthur site strips its "King Arthur " brand prefix at
    # parse time (ingredient_strip_patterns), so the aliases below are
    # the brand-free names the parser sees. Bread flour folds into the
    # existing "flour" canonical rather than fragmenting a second one.
    # "semolina flour" needs no alias: the stripped name already parses
    # to its own canonical.
    ("unbleached all-purpose flour", "all-purpose flour", None),
    ("unbleached bread flour", "flour", "bread"),
    ("baker's special dry milk", "nonfat dry milk", None),
    ("artisan bread topping", "bread topping", "artisan"),
    ("the works bread topping", "bread topping", "the works"),
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

# Equipment and other non-ingredients the parser calls an ingredient
# ("12-hole mini bundt tin" parses as a food). Their terms are marked
# not_food: the lines stay stored, but are excluded from aggregation
# and nutrition.
NOT_FOOD: list[str] = [
    "12-hole mini bundt tin",
]

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


# FDC foods give both household portion weights (conversion) and
# per-100g nutrients (nutrition), so every FDC_IDS mapping serves
# both roles.
FOOD_REFS: dict[str, list[tuple[str, str, str]]] = {
    name: [("fdc", str(fdc_id), "conversion"), ("fdc", str(fdc_id), "nutrition")]
    for name, fdc_id in FDC_IDS.items()
}


# Portion weights come from the first of these sources the ingredient
# has a confirmed conversion reference in (seed overrides beat all).
CONVERSION_SOURCES: tuple[str, ...] = ("fdc", "cnf")

# A preparation that changes the food itself: "2 egg yolks" is not
# egg, "juice of 1 lemon" is not lemon, cooked rice is not raw.
# (canonical ingredient, keyword in the line's preparation, parsed
# name or alias variant) -> (source, source_food_id). Hand-seeded,
# so hand-seeded is the confirmation.
PREPARED_FORMS: dict[tuple[str, str], tuple[str, str]] = {
    ("egg", "yolk"): ("fdc", "172184"),
    ("egg", "white"): ("fdc", "172183"),
    ("lemon", "juice"): ("fdc", "167747"),
    ("lime", "juice"): ("fdc", "168156"),
    ("rice", "cooked"): ("fdc", "168878"),
}
