"""Reference data sources, one module per source.

Each module is a one-off import command (`menu-fdc-import`, later
`menu-cofid-import`...) that loads its source's bulk release into the
common ref_* tables, so the rest of the code never cares which source
a value came from. Attribution is data: every import stamps a
food_source row with the version and citation food-guru must carry.
"""
