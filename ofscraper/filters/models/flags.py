from ofscraper.filters.models.utils.logs import apply_yes_no_filters


def _has_free_trial(x):
    return (
        x.final_current_price == 0 or x.final_promo_price == 0
    ) and x.final_regular_price > 0


# (setting_key, log label, predicate for "yes", yes label, no label)
FLAG_FILTERS = [
    (
        "promo",
        "Promo flag",
        lambda x: x.lowest_promo_claim is not None,
        "promo flag yes",
        "promo flag no",
    ),
    (
        "all_promo",
        "All promo flag",
        lambda x: x.lowest_promo_all is not None,
        "all promo flag yes",
        "all promo flag no",
    ),
    (
        "last_seen",
        "Last seen flag",
        lambda x: x.last_seen is not None,
        "last seen flag yes",
        "last seen flag no",
    ),
    (
        "free_trial",
        "Free trial flag",
        _has_free_trial,
        "free trial flag yes",
        "free trial flag no",
    ),
]


def promoFilterHelper(filterusername):
    return apply_yes_no_filters(filterusername, FLAG_FILTERS)
