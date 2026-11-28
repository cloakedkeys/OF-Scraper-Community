import logging

import ofscraper.utils.settings as settings
from ofscraper.utils.logs.utils.trace import is_trace

log = logging.getLogger("shared")


def trace_log_user(responseArray, title_str):
    if not is_trace():
        return
    log.trace({title_str.strip().capitalize()})
    for count, ele in enumerate(responseArray):
        log.trace(f"[userdata raw] count: {count} data:{ele}")


def filter_users(users, predicate, label):
    """Keep the users ``predicate`` accepts, then log the count and trace them."""
    users = list(filter(predicate, users))
    log.debug(f"{label} username count: {len(users)}")
    trace_log_user(users, label)
    return users


def apply_yes_no_filters(users, table):
    """Apply ``(setting_key, label, predicate, yes_label, no_label)`` rows.

    A truthy setting keeps the users ``predicate`` accepts, a setting that is
    exactly ``False`` keeps the rest, and ``None`` leaves the list alone.
    """
    current = settings.get_settings()
    for key, label, predicate, yes_label, no_label in table:
        value = getattr(current, key)
        log.debug(f"{label}: {value}")
        if value:
            users = filter_users(users, predicate, yes_label)
        elif value is False:
            users = filter_users(users, lambda x, p=predicate: not p(x), no_label)
    return users
