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
import traceback

import httpx

import ofscraper.utils.console as console
import ofscraper.utils.settings as settings
from . import me

log = logging.getLogger("shared")


def print_sign_status():
    status = getstatus()
    if status == "UP":
        console.get_shared_console().print("Status - [bold green]🟢 UP[/bold green]")
    else:
        console.get_shared_console().print("Status - [bold red]🔴 DOWN[/bold red]")


def network_error_message(e: httpx.TransportError) -> str:
    try:
        host = e.request.url.host
    except RuntimeError:
        host = "the server"
    reason = (
        "timed out" if isinstance(e, httpx.TimeoutException) else "connection failed"
    )
    return f"Couldn't reach {host} ({reason}). Check your connection or VPN."


def getstatus():
    if settings.get_settings().anon:
        return "UP"
    try:
        me.scrape_user()
        return "UP"
    except httpx.TransportError as e:
        # timeouts and connection errors say nothing about the auth
        log.traceback_(e)
        log.traceback_(traceback.format_exc())
        log.error(network_error_message(e))
        return "NETWORK"
    except Exception as e:
        log.traceback_(e)
        log.traceback_(traceback.format_exc())
        return "DOWN"
