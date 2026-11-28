import logging

import ofscraper.utils.settings as settings
from ofscraper.filters.models.utils.filtering import yes_no_filter

log = logging.getLogger("shared")


def _regular_price(x):
    # an unknown (None) regular price counts as 0, like the other prices'
    # fallback when the API leaves them out
    return x.final_regular_price or 0


def _free_trial(x):
    free_now = x.final_current_price == 0 or x.final_promo_price == 0
    return free_now and _regular_price(x) > 0


def _no_free_trial(x):
    paid_now = x.final_current_price > 0 and x.final_promo_price > 0
    return paid_now or _regular_price(x) == 0


def promoFilterHelper(filterusername):
    current = settings.get_settings()

    log.debug(f"Promo flag: {current.promo}")
    filterusername = yes_no_filter(
        filterusername,
        current.promo,
        lambda x: x.lowest_promo_claim is not None,
        "promo flag yes",
        "promo flag no",
    )

    log.debug(f"All promo flag: {current.all_promo}")
    filterusername = yes_no_filter(
        filterusername,
        current.all_promo,
        lambda x: x.lowest_promo_all is not None,
        "all promo flag yes",
        "all promo flag no",
    )

    log.debug(f"Last seen flag: {current.last_seen}")
    filterusername = yes_no_filter(
        filterusername,
        current.last_seen,
        lambda x: x.last_seen is not None,
        "last seen flag yes",
        "last seen flag no",
    )

    log.debug(f"Free trial flag: {current.free_trial}")
    return yes_no_filter(
        filterusername,
        current.free_trial,
        _free_trial,
        "free trial flag yes",
        "free trial flag no",
        no=_no_free_trial,
    )
