import logging
import operator

import ofscraper.utils.settings as settings
from ofscraper.filters.models.utils.filtering import filter_users

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
    # same accounts as main's `> 0` checks and its truthiness check on the
    # regular price, which is the only price that can be None
    return price is not None and price > 0


def _paid_free_filter(users, value, attr, label):
    if value == "paid":
        return filter_users(
            users, lambda x: _is_paid(getattr(x, attr)), f"paid {label} filter"
        )
    if value == "free":
        return filter_users(
            users, lambda x: getattr(x, attr) == 0, f"free {label} filter"
        )
    return users


def pricePaidFreeFilterHelper(filterusername):
    current = settings.get_settings()
    for key, attr, label in PAID_FREE_FILTERS:
        value = getattr(current, key)
        log.debug(f"{label} filter: {value}")
        filterusername = _paid_free_filter(filterusername, value, attr, label)
    return priceMinMaxFilters(filterusername)


def _min_max_filter(users, bound, attr, op, label):
    # 0 is a real bound (--current-price-max 0 keeps free accounts)
    if bound is None:
        return users
    return filter_users(
        users,
        lambda x: op(getattr(x, attr), bound),
        f"{label} filter",
    )


def priceMinMaxFilters(filterusername):
    current = settings.get_settings()
    for key, attr, op, label in MIN_MAX_FILTERS:
        bound = getattr(current, key)
        log.debug(f"{label} filter: {bound}")
        filterusername = _min_max_filter(filterusername, bound, attr, op, label)
    return filterusername
