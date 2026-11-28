from ofscraper.filters.models.utils.logs import apply_yes_no_filters

# (setting_key, log label, predicate for "yes", yes label, no label)
SUBTYPE_FILTERS = [
    (
        "renewal",
        "Renewal",
        lambda x: x.renewed,
        "active renewal filter",
        "disabled renewal filter",
    ),
    (
        "sub_status",
        "Sub status",
        lambda x: x.active,
        "active subscription filter",
        "expired subscription filter",
    ),
]


def subType(filterusername):
    return apply_yes_no_filters(filterusername, SUBTYPE_FILTERS)
