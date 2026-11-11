"""The "Setting ... scan range" log lines and the timeline's full-rescan hint."""

import arrow

import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.settings as settings

CLI_HINT = (
    "Full rescan: add --after 2000"
    " (or --after 2000 --force-all to also re-download files)"
)
# the menu has no flag to append; its full rescan is the redownload prompt,
# which sets --after 2000 together with --force-all
MENU_HINT = (
    'Full rescan: answer Yes to "Would you like to redownload all files"'
    " (this also re-downloads files)"
)

# set for each run by prepare(); the hint is shown once per run
_menu = False
_hint_shown = False


def set_menu_mode(menu):
    global _menu
    _menu = bool(menu)


def reset_hint():
    global _hint_shown
    _hint_shown = False


def range_text(username, area, after):
    fmt = of_env.getattr("API_DATE_FORMAT")
    return (
        f"Setting {area} scan range for {username} from "
        f"{arrow.get(after).format(fmt)} to {range_end()}"
    )


def range_end():
    # with no --before the cutoff is padded past now; show "now" instead
    current = settings.get_settings()
    before = getattr(current, "before", None)
    if before and getattr(current, "before_original", before):
        return arrow.get(before).format(of_env.getattr("API_DATE_FORMAT"))
    return "now"


def full_scan_hint(after):
    """The hint line to show for this timeline scan, or None.

    Only a partial (incremental) scan of a run that downloads gets it, and
    only once per run.
    """
    global _hint_shown
    current = settings.get_settings()
    if _hint_shown:
        return None
    if "download" not in (getattr(current, "actions", None) or []):
        return None
    # a start the user chose (--after, or the menu's redownload) is not
    # an incremental scan
    if getattr(current, "after", None) is not None:
        return None
    if float(after or 0) <= arrow.get("2000").float_timestamp:
        return None
    _hint_shown = True
    return f"Hint: {MENU_HINT if _menu else CLI_HINT}"
