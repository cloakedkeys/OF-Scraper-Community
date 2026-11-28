import logging

import ofscraper.utils.settings as settings
from ofscraper.filters.models.utils.filtering import yes_no_filter

log = logging.getLogger("shared")


def subType(filterusername):
    current = settings.get_settings()

    log.debug(f"Renewal: {current.renewal}")
    filterusername = yes_no_filter(
        filterusername,
        current.renewal,
        lambda x: x.renewed,
        "active renewal filter",
        "disabled renewal filter",
    )

    log.debug(f"Sub status: {current.sub_status}")
    return yes_no_filter(
        filterusername,
        current.sub_status,
        lambda x: x.active,
        "active subscription filter",
        "expired subscription filter",
    )
