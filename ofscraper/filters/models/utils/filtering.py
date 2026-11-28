import logging

from ofscraper.filters.models.utils.logs import trace_log_user

log = logging.getLogger("shared")


def filter_users(users, predicate, label):
    """Keep the users ``predicate`` accepts, then log the count and trace them."""
    users = list(filter(predicate, users))
    log.debug(f"{label} username count: {len(users)}")
    trace_log_user(users, label)
    return users


def yes_no_filter(users, value, yes, yes_label, no_label, no=None):
    """Filter on a yes/no setting.

    A truthy ``value`` keeps the users ``yes`` accepts. ``False`` keeps the
    users ``no`` accepts, by default the ones ``yes`` rejects. ``None`` leaves
    the list alone.
    """
    if value:
        return filter_users(users, yes, yes_label)
    if value is False:
        return filter_users(users, no or (lambda x: not yes(x)), no_label)
    return users
