import logging

import ofscraper.scripts.runner as runner
import ofscraper.utils.settings as settings

# the answer for a skip that lasts: the item is saved as done and isn't
# asked about again. It's truthy, so it skips like True (skip this run)
SKIP_PERMANENT = "permanent"


def retry_permanent_skips():
    """True when --retry-permanent-skips is on: the run offers the files the
    script skipped for good to it again (#354)."""
    return bool(getattr(settings.get_settings(), "retry_permanent_skips", False))


async def skip_download_script(total, ele):
    """False to download, True to skip in this run (the script printed
    `false` or nothing), SKIP_PERMANENT to skip for good (it printed
    `skip-permanent`)."""
    log = logging.getLogger("shared")
    name = "download skip script"
    script_path = settings.get_settings().skip_download_script
    if not runner.script_ready(script_path, name, skip_in_dry_run=False):
        return False

    def payload():
        return {
            "username": ele.username,
            "model_id": ele.model_id,
            "post_id": ele.post_id,
            "media_id": ele.id,
            "media": ele.media,
            "post": ele.post.post,
            "total_size": total,
        }

    result = await runner.async_run_user_script(
        script_path,
        payload,
        name=name,
        level_key="SKIP_DOWNLOAD_SCRIPT_SUBPROCESS_LEVEL",
        subject=f"{ele.username} (media_id: {ele.id}, post_id: {ele.post_id})",
    )
    if result is None:
        return False  # a script that fails lets the download proceed

    answer = (result.stdout or "").strip()
    if answer.lower() == "skip-permanent":
        log.info(f"Download skip script instructed to SKIP download for good for {ele.username} (media_id: {ele.id}).")
        return SKIP_PERMANENT
    should_skip = answer.lower() == "false" or answer == ""
    if should_skip:
        log.info(f"Download skip script instructed to SKIP download for {ele.username} (media_id: {ele.id}).")
    else:
        log.info(f"Download skip script instructed to PROCEED with download for {ele.username} (media_id: {ele.id}).")
    return should_skip
