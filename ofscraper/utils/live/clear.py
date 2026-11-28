from typing import List, Union

import ofscraper.utils.console as console
import ofscraper.utils.live.updater as updater


def clear():
    # Not Console.clear_live(): that is Live's own bookkeeping, done by
    # Live.stop(). On rich 13.7 calling it again only set the console's live
    # display to None; since rich 14.1 it pops a stack of live displays, so it
    # raised on an empty stack and dropped a running display from it.
    console.get_shared_console().line(2)


# task group name -> the updater attribute holding its manager. Looked up when
# clearing, not at import, so a replaced manager (as in tests) is the one cleared.
TASK_CLEAR_MAP = {
    "activity": "activity",
    "api": "api",
    "download": "download",
    "metadata": "metadata",
    "like": "like",
    "userlist": "userlist",
}


def clear_all_tasks():
    """Hides all tasks from all progress managers."""
    for attr in TASK_CLEAR_MAP.values():
        getattr(updater, attr).clear()


def clear_tasks_by_name(names: Union[str, List[str]]):
    """
    Hides the tasks of each named progress group (see TASK_CLEAR_MAP), or of
    every group for "all". Accepts a single string or a list of strings.

    Args:
        names (Union[str, List[str]]): The name or names of the tasks to clear.

    Raises:
        ValueError: If any provided name is not a valid task type.
    """
    # 1. Normalize the input to always be a list
    if isinstance(names, str):
        names_to_clear = [names]
    else:
        # Assumes 'names' is a list or other iterable
        names_to_clear = names

    # 2. Loop through the list and process each name
    for name in names_to_clear:
        task_name = name.lower()
        if task_name == "all":
            clear_all_tasks()
        elif task_name in TASK_CLEAR_MAP:
            getattr(updater, TASK_CLEAR_MAP[task_name]).clear()
        else:
            # Raise an error on the first invalid name found
            raise ValueError(
                f"'{name}' is not a valid task type. "
                f"Valid options are: {[*TASK_CLEAR_MAP, 'all']}"
            )
