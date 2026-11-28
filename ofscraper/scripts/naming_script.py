import logging

import arrow

import ofscraper.scripts.runner as runner
import ofscraper.utils.config.data as config_data
import ofscraper.utils.settings as settings

# isort: split
# posts imports this module through filters -> db -> placeholder, so look
# Post up when the script runs, not while posts is still loading
import ofscraper.classes.of.posts as posts_
from ofscraper.classes.of.media import Media


async def naming_script(dir, file, ele, formats=None):
    """formats: the (dir_format, file_format) the file was named with, which
    a type_options entry can set; file_options' when not given"""
    log = logging.getLogger("shared")
    name = "naming script"
    script_path = settings.get_settings().naming_script
    if not runner.script_ready(script_path, name):
        return

    if isinstance(ele, Media):
        details = _media_details
    elif isinstance(ele, posts_.Post):
        details = _post_details
    else:
        log.info(f"Unsupported type for naming script: {type(ele)}")
        return

    def payload():
        return {
            "dir": str(dir),
            "file": str(file),
            "dir_format": formats[0] if formats else config_data.get_dirformat(),
            "file_format": formats[1] if formats else config_data.get_fileformat(),
            "metadata": config_data.get_metadata(),
            "username": ele.username,
            "model_id": ele.model_id,
            "value": ele.value.capitalize(),
            "response_type": ele.modified_responsetype,
            "label": ele.label_string,
            **details(ele),
        }

    result = await runner.async_run_user_script(
        script_path,
        payload,
        name=name,
        level_key="NAMING_SCRIPT_SUBPROCESS_LEVEL",
    )
    return result.stdout.strip() if result and result.stdout else None


def _media_details(ele):
    return {
        "media": ele.media,
        "post": ele.post.post,
        "post_id": ele.post_id,
        "media_id": ele.id,
        "media_type": ele.mediatype.capitalize(),
        # undated: the same fallback as the {date} placeholder
        "date": arrow.get(ele.postdate or ele.date or 0).format(config_data.get_date()),
        "download_type": ele.downloadtype,
    }


def _post_details(ele):
    return {
        "media": None,
        "post": ele.post,
        "post_id": ele.id,
        "media_id": None,
        "media_type": None,
        "date": arrow.get(ele.date or 0).format(config_data.get_date()),
        "download_type": None,
    }
