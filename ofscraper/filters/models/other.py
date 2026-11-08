"""
other filters
"""

import logging

from ofscraper.filters.models.utils.logs import trace_log_user
import ofscraper.utils.settings as settings
from ofscraper.utils.username_match import matches_username, username_keys


def otherFilters(filterusername):
    log = logging.getLogger("shared")
    log.debug(f"Excluded usernames: {settings.get_settings().excluded_username}")
    if len(settings.get_settings().excluded_username) > 0:
        excluded = username_keys(settings.get_settings().excluded_username)
        filterusername = list(
            filter(
                lambda x: not matches_username(x.name, excluded),
                filterusername,
            )
        )
        log.debug(f"'excluded usernames' filter username count: {len(filterusername)}")
        trace_log_user(filterusername, "Excluded usernames")

    return filterusername


def is_excluded_username(username):
    """
    True if --excluded-username names this model, in any case.
    """
    excluded = username_keys(settings.get_settings().excluded_username)
    return matches_username(username, excluded)
