r"""

 _______  _______         _______  _______  _______  _______  _______  _______  _______
(  ___  )(  ____ \       (  ____ \(  ____ \(  ____ )(  ___  )(  ____ )(  ____ \(  ____ )
| (   ) || (    \/       | (    \/| (    \/| (    )|| (   ) || (    )|| (    \/| (    )|
| |   | || (__     _____ | (_____ | |      | (____)|| (___) || (____)|| (__    | (____)|
| |   | ||  __)   (_____)(_____  )| |      |     __)|  ___  ||  _____)|  __)   |     __)
| |   | || (                   ) || |      | (\ (   | (   ) || (      | (      | (\ (
| (___) || )             /\____) || (____/\| ) \ \__| )   ( || )      | (____/\| ) \ \__
(_______)|/              \_______)(_______/|/   \__/|/     \||/       (_______/|/   \__/

"""

import logging
import re

import ofscraper.utils.profiles.data as profile_data
from ofscraper.utils.markup import escape_text

log = logging.getLogger("shared")
currentData = None
currentProfile = None


def _console():
    # imported when printing: utils.console can't be the first ofscraper
    # module loaded (its console -> settings -> config.file import cycle), and
    # this module can be
    import ofscraper.utils.console as console

    return console.get_console()


def print_profiles() -> list:
    _console().print("\n\nCurrent Profiles\n")
    profile_fmt = "Profile: [cyan]{}"
    for name in profile_data.get_profile_names():
        _console().print(profile_fmt.format(escape_text(name)))
    _console().print("\n")
    print_current_profile()
    print_default_profile()



def print_current_profile():
    current_profile = profile_data.get_active_profile()
    _console().print("Active profile: [cyan]{}[/cyan]".format(escape_text(current_profile)))
def print_default_profile():
    current_profile = profile_data.get_current_config_profile()
    _console().print("Default config profile: [cyan]{}[/cyan]".format(escape_text(current_profile)))


def profile_name_fixer(input):
    input = str(input)
    return f"{re.sub('_profile','', input)}_profile" if input else None
