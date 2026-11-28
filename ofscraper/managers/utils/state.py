import inspect
import logging
from enum import Enum, auto
from typing import Dict, List, Set

log = logging.getLogger("shared")


class EActivity:
    """Defines all possible processing activities."""

    class PaidActivity(Enum):
        # A top-level activity
        SCRAPE_PAID_DOWNLOAD = auto()
        SCRAPE_PAID_METADATA = auto()

    # A nested enum for a group of related sub-activities
    class ScrapeActivity(Enum):
        LIKE = auto()
        UNLIKE = auto()
        DOWNLOAD = auto()
        TEXT = auto()
        METADATA = auto()


def _get_all_enum_members(namespace_class):
    """
    Correctly finds all members from enums nested within a namespace class.
    """
    all_members = []
    # inspect.getmembers finds all attributes of the class
    for _, member_class in inspect.getmembers(namespace_class):
        # Check if the attribute is a class itself and is a subclass of Enum
        if inspect.isclass(member_class) and issubclass(member_class, Enum):
            # If so, extend our list with all members from that enum
            all_members.extend(list(member_class))
    return all_members


class StateManager:
    """
    Manages the processing state for different activities.
    This class tracks what is in an activity's queue and what has been completed.
    """

    def __init__(self):
        """Initializes the manager with all defined activities."""
        # Create a flat list of all enum members
        all_activities = list(_get_all_enum_members(EActivity))

        # Use the flat list to build the queues dictionary
        self._queues: Dict[Enum, Dict[str, List[str]]] = {
            activity: {"queued": [], "processed": set()} for activity in all_activities
        }
        pass

    def set_queue(self, activity: EActivity, usernames: List[str]):
        """
        Sets the entire queue for a given activity, preserving order.
        This resets any previous progress for that activity.
        """
        # Directly assign the list to preserve its order
        self._queues[activity]["queued"] = list(usernames)
        self._queues[activity]["processed"] = set()  # Reset progress
        log.info(
            f"Queued {len(usernames)} users for the {activity.name} activity in their original order."
        )

    def add_to_queue(self, activity: EActivity, usernames: List[str]):
        """
        Adds new, unique usernames to an existing activity queue, preserving order.
        Does not reset progress.
        """
        # Get the existing queue and a set of its users for fast lookups
        current_queue = self._queues[activity]["queued"]
        existing_users = set(current_queue)

        added_count = 0
        for user in usernames:
            if user not in existing_users:
                current_queue.append(user)
                existing_users.add(user)
                added_count += 1

        if added_count > 0:
            log.info(
                f"Added {added_count} new, unique users to the {activity.name} queue."
            )

    def get_unprocessed(self, activity: EActivity) -> List[str]:
        """
        Returns an ordered list of users who are still queued but not yet processed.
        """
        queued = self._queues[activity]["queued"]
        processed = self._queues[activity]["processed"]
        # Return a new list preserving the original order
        return [user for user in queued if user not in processed]

    def _activities(self, group=None) -> List[Enum]:
        """The activities in ``group`` (an EActivity enum class), or all of
        them when it is None."""
        return [a for a in self._queues if group is None or isinstance(a, group)]

    def _queued_usernames(self, group=None) -> List[str]:
        """Unique usernames queued for the activities in ``group``, in the
        order they first appear."""
        return list(
            dict.fromkeys(
                username
                for activity in self._activities(group)
                for username in self._queues[activity]["queued"]
            )
        )

    def _reset_processed(self, group=None) -> None:
        for activity in self._activities(group):
            self._queues[activity]["processed"].clear()

    def _clear_queues(self, group=None) -> None:
        for activity in self._activities(group):
            self._queues[activity]["processed"].clear()
            self._queues[activity]["queued"] = []

    def get_all_queued_usernames(self) -> List[str]:
        """
        Returns a single list of all unique usernames currently in any activity queue,
        preserving the order of first appearance.
        """
        return self._queued_usernames()

    def get_paid_queued_usernames(self) -> List[str]:
        """
        Returns a single list of unique usernames from all PaidActivity queues,
        preserving the order of first appearance.
        """
        return self._queued_usernames(EActivity.PaidActivity)

    def get_scrape_queued_usernames(self) -> List[str]:
        """
        Returns a single list of unique usernames from all ScrapeActivity queues,
        preserving the order of first appearance.
        """
        return self._queued_usernames(EActivity.ScrapeActivity)

    def get_queued_usernames(self, activity: EActivity) -> List[str]:
        """
        Returns an ordered list of unique usernames for a given activity.
        If no activity is provided, it returns a combined list of all users
        queue
        """
        # This just returns the original list, already ordered and unique
        return self._queues[activity]["queued"]

    def mark_as_processed(self, username: str, activity: EActivity):
        """Marks a single user as processed for a given activity."""
        if username in self._queues[activity]["queued"]:
            self._queues[activity]["processed"].add(username)

    def get_processed(self, activity: EActivity) -> Set[str]:
        """Returns the set of users who have been processed for a given activity."""
        return self._queues[activity]["processed"]

    def reset_processed_status(self, activity: EActivity) -> None:
        """
        Resets the processed status for a single activity.
        All users in the queue for this activity will be marked as unprocessed.
        """
        if activity in self._queues:
            self._queues[activity]["processed"].clear()
            log.info(f"Processed status for {activity.name} has been reset.")

    def reset_all_processed_status(self) -> None:
        """
        Resets the processed status for ALL activities.
        """
        self._reset_processed()
        log.info("Processed status for all activities has been reset.")

    def reset_paid_processed_status(self) -> None:
        """
        Resets the processed status for all PaidActivity instances.
        """
        self._reset_processed(EActivity.PaidActivity)
        log.info("Processed status for all Paid activities has been reset.")

    def reset_scrape_processed_status(self) -> None:
        """
        Resets the processed status for all ScrapeActivity instances.
        """
        self._reset_processed(EActivity.ScrapeActivity)
        log.info("Processed status for all Scrape activities has been reset.")

    def clear_queue(self, activity: EActivity):
        """
        clears the  queue for a given activity.
        This resets any previous progress for that activity.
        """
        # Directly assign the list to preserve its order
        self._queues[activity]["queued"] = []
        self._queues[activity]["processed"] = set()  # Reset progress
        log.info(f"clear the queue for the {activity.name} activity")

    def clear_all_queues(self) -> None:
        """
        clears the  queue for all activities.
        This resets any previous progress for all activities.
        """
        self._clear_queues()
        log.info("clear the queue for the all activities")

    def clear_paid_queues(self) -> None:
        """
        Clears the 'queued' and 'processed' lists for all PaidActivity instances.
        """
        self._clear_queues(EActivity.PaidActivity)
        log.info("Cleared all Paid activity queues.")

    def clear_scrape_queues(self) -> None:
        """
        Clears the 'queued' and 'processed' lists for all ScrapeActivity instances.
        """
        self._clear_queues(EActivity.ScrapeActivity)
        log.info("Cleared all Scrape activity queues.")


ACTIVITY_MAP = {
    "scrape_paid_download": EActivity.PaidActivity.SCRAPE_PAID_DOWNLOAD,
    "like": EActivity.ScrapeActivity.LIKE,
    "unlike": EActivity.ScrapeActivity.UNLIKE,
    "download": EActivity.ScrapeActivity.DOWNLOAD,
    "text": EActivity.ScrapeActivity.TEXT,
    "metadata": EActivity.ScrapeActivity.METADATA,
    "scrape_paid_metadata": EActivity.PaidActivity.SCRAPE_PAID_METADATA,
}


def string_to_activity(s: str) -> EActivity:
    """
    Converts a single string to an EActivity enum member.

    Raises:
        ValueError: If the string is not a valid activity.
    """
    activity = ACTIVITY_MAP.get(s.lower())
    if not activity:
        raise ValueError(
            f"'{s}' is not a valid activity. Options are: {list(ACTIVITY_MAP.keys())}"
        )
    return activity
