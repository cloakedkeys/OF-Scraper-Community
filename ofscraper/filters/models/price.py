import logging
import operator

import ofscraper.utils.settings as settings
from ofscraper.filters.models.utils.logs import filter_users

log = logging.getLogger("shared")

# (setting_key, model attribute, log label)
PAID_FREE_FILTERS = [
    ("current_price", "final_current_price", "current price"),
    ("renewal_price", "final_renewal_price", "renewal price"),
    ("regular_price", "final_regular_price", "regular price"),
    ("promo_price", "final_promo_price", "promo price"),
]

# (setting_key, model attribute, comparison, log label)
MIN_MAX_FILTERS = [
    ("promo_price_min", "final_promo_price", operator.ge, "promo price min"),
    ("promo_price_max", "final_promo_price", operator.le, "promo price max"),
    ("regular_price_min", "final_regular_price", operator.ge, "regular price min"),
    ("regular_price_max", "final_regular_price", operator.le, "regular price max"),
    ("renewal_price_min", "final_renewal_price", operator.ge, "renewal price min"),
    ("renewal_price_max", "final_renewal_price", operator.le, "renewal price max"),
    ("current_price_min", "final_current_price", operator.ge, "current price min"),
    ("current_price_max", "final_current_price", operator.le, "current price max"),
]


def _is_paid(price):
    # final_regular_price can be None when the API leaves the price out
    return price is not None and price > 0


def pricePaidFreeFilterHelper(filterusername):
    current = settings.get_settings()
    for key, attr, label in PAID_FREE_FILTERS:
        value = getattr(current, key)
        log.debug(f"{label} filter: {value}")
        if value == "paid":
            filterusername = filter_users(
                filterusername,
                lambda x, a=attr: _is_paid(getattr(x, a)),
                f"paid {label} filter",
            )
        elif value == "free":
            filterusername = filter_users(
                filterusername,
                lambda x, a=attr: getattr(x, a) == 0,
                f"free {label} filter",
            )
    return priceMinMaxFilters(filterusername)


def priceMinMaxFilters(filterusername):
    current = settings.get_settings()
    for key, attr, op, label in MIN_MAX_FILTERS:
        bound = getattr(current, key)
        log.debug(f"{label} filter: {bound}")
        # 0 is a valid bound (--current-price-max 0 keeps free accounts)
        if bound is None:
            continue
        filterusername = filter_users(
            filterusername,
            lambda x, a=attr, o=op, b=bound: (
                getattr(x, a) is not None and o(getattr(x, a), b)
            ),
            f"{label} filter",
        )
    return filterusername
