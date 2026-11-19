import logging

import ofscraper.utils.console as console_
import ofscraper.utils.settings as settings

CDRM_DEPRECATED = (
    "Key mode cdrm is deprecated and will be removed: its service, "
    "cdrm-project.com, no longer works, so DRM videos can't be decrypted with it. "
    "Use key mode manual with your own CDM files: "
    "https://cloakedkeys.gitbook.io/ofsc/cdm-options"
)


def check_cdm():
    console = console_.get_shared_console()
    # failures are shown even when --output quiets the shared console
    alert = console_.get_console()
    log = logging.getLogger("shared")

    import ofscraper.sites as sites

    if sites.is_fansly():
        # Fansly video has no DRM, so there is no CDM to check
        return True
    keymode = settings.get_settings().key_mode
    console.print(f"[yellow]Key Mode: {keymode}\n\n[/yellow]")
    if keymode == "manual":
        console.print(
            "[yellow]WARNING:Make sure you have all the correct settings for choosen cdm\nhttps://cloakedkeys.gitbook.io/ofsc/cdm-options\n\n[/yellow]"
        )
        return True
    elif keymode == "cdrm":
        alert.print(f"[red]{CDRM_DEPRECATED}\n[/red]")
        # debug: the console already shows it
        log.debug(CDRM_DEPRECATED)
    # cdrm's service no longer works, so there's nothing to probe
    return False
