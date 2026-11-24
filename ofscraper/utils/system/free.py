import pathlib
import shutil

import ofscraper.utils.settings as settings
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.paths.common as common_paths


# config.json's save_location, keyed on the file and its modification time:
# the check runs often, and the file is read again only once it changes
_save_location = {}


def _get_save_location():
    config = pathlib.Path(common_paths.get_config_path())
    try:
        key = (str(config), config.stat().st_mtime_ns)
    except OSError:
        key = (str(config), None)
    if key not in _save_location:
        _save_location.clear()
        _save_location[key] = common_paths.get_save_location()
    return _save_location[key]


def get_check_path():
    """OFSC_DISK_SPACE_CHECK_PATH when set, else the disk that holds
    save_location. A save location not made yet is measured on its nearest
    existing folder, which is on the same disk; a link is followed first, even
    to a folder that's missing, so the disk is the target's."""
    path = of_env.getattr("DISK_SPACE_CHECK_PATH")
    if path:
        return path
    path = pathlib.Path(_get_save_location()).resolve()
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def get_free():
    total, used, free = shutil.disk_usage(get_check_path())
    return free


def space_checker(func):
    def inner(*args, **kwargs):
        if not check_free_size():
            raise Exception("Space min has been reached")
        return func(*args, **kwargs)

    return inner


def check_free_size():
    space_limit = settings.get_settings().system_free_min
    if space_limit > 0 and space_limit > get_free():
        return False
    return True
