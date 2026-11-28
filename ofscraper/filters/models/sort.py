import logging
import operator

import ofscraper.utils.settings as settings

# --sort value -> model attribute; unknown values sort by name
SORT_ATTRS = {
    "name": "name",
    "last-seen": "final_last_seen",
    "expired": "final_expired",
    "subscribed": "final_subscribed",
    "current-price": "final_current_price",
    "promo-price": "final_promo_price",
    "renewal-price": "final_renewal_price",
    "regular-price": "final_regular_price",
}


def sort_models_helper(models):
    sort = settings.get_settings().sort
    reverse = settings.get_settings().desc
    logging.getLogger("shared").debug(
        f"sorting details  [name:{sort} direction:{reverse}]"
    )
    key = operator.attrgetter(SORT_ATTRS.get(sort, "name"))
    return sorted(models, reverse=reverse, key=key)
