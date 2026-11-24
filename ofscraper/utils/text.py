import asyncio
import copy
import html
import logging
import pathlib
import textwrap
import traceback

import aiofiles

import ofscraper.classes.placeholder as placeholder
import ofscraper.sites as sites
import ofscraper.utils.config.data as config_data
import ofscraper.utils.of_env.of_env as of_env
import ofscraper.utils.paths.paths as paths
import ofscraper.utils.settings as settings
from ofscraper.scripts.after_download_script import after_download_script
from ofscraper.utils.string import html_lines, parse_safe


async def get_text(username, values):
    # One at a time from choosing a file's name to writing it: two posts can
    # resolve to the same name, and the second has to see the first's file.
    lock = asyncio.Lock()
    tasks = [get_text_process(username, value, lock) for value in values]

    await asyncio.gather(*tasks, return_exceptions=True)


def file_text(text):
    """What a post's text file holds: the caption as it reads on the site.
    An OnlyFans caption is HTML, so a <br> or the start or end of a
    paragraph, div, list item or heading is a line break, the other tags are removed and
    entities are decoded, as stash_sync.clean_text does. A Fansly
    caption is plain text already. Each of the caption's lines is wrapped on
    its own, so its line breaks stay."""
    if not sites.is_fansly():
        text = html.unescape(html_lines(text))
        text = "\n".join(line.strip() for line in text.splitlines())
    width = of_env.getattr("MAX_TEXT_LENGTH")
    lines = []
    for line in text.strip().splitlines():
        lines.extend(textwrap.wrap(line, width=width) or [""])
    return "\n".join(lines)


def _older_file_text(text):
    """What a version before #751 wrote for the same caption: the raw text,
    HTML included, wrapped as one paragraph. A file holding this is the
    post's own, and is rewritten where it is."""
    return "\n".join(textwrap.wrap(text, width=of_env.getattr("MAX_TEXT_LENGTH")))


def _holds(path, *contents):
    """True when the text file at path holds exactly one of contents."""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8") in contents
    except (OSError, UnicodeError):
        return False


# {post_id} and its alias {postid}
_POST_ID = {"post_id", "postid"}


def _own_path(path, ele, *contents):
    """Where a post's text goes. A file template can give two posts the same
    name, e.g. captions that start with the same textlength words. A file
    with other content at path is another post's, so this post's text goes
    to name_<post_id>, as media go to name_<media_id>. filename_collision
    "overwrite" keeps the old behaviour: the name is used as it is. When
    file_format or dir_format has {post_id} (or {postid}), the name is the
    post's own: a number in the caption or the date that only looks like
    the post id doesn't count."""
    path = pathlib.Path(path)
    if (
        str(config_data.get_filename_collision()).lower() == "overwrite"
        or not path.exists()
        or _POST_ID & parse_safe(config_data.get_fileformat())
        or _POST_ID & parse_safe(config_data.get_dirformat())
        or _holds(path, *contents)
    ):
        return path
    own = path.with_name(f"{path.stem}_{ele.id}{path.suffix}")
    # shortened as the name itself is, unless truncation is turned off
    if settings.get_settings().trunicate:
        own = pathlib.Path(paths.truncate(own))
    return own


async def get_text_process(username, ele, lock=None):
    async with lock or asyncio.Lock():
        await _write_text(ele)


async def _write_text(ele):
    log = logging.getLogger("shared")
    dupe = (
        settings.get_settings().force_all or settings.get_settings().force_model_unique
    )

    if bool(ele.text) is False:
        return

    try:
        content = file_text(ele.text)
        # a caption of only markup, e.g. "<p></p>", has no text to save
        if not content:
            return

        new_ele = copy.deepcopy(ele)
        new_ele.mediatype = "Text"

        placeholderObj = await placeholder.Textholders(new_ele, "txt").init()

        older = _older_file_text(new_ele.text)
        filepath = _own_path(placeholderObj.filepath, new_ele, content, older)

        # Check for existing file BEFORE marking the attempt. A file at the
        # post's own name_<post_id> is kept as it is, as one at the name
        # itself is, even with other content (an older or hand-edited
        # caption): --force-all rewrites it. The exception is a file holding
        # what an older version wrote for this caption, which is rewritten.
        if (
            filepath.exists()
            and not dupe
            and (_holds(filepath, content) or not _holds(filepath, older))
        ):
            # Returning without marking attempt will accurately log it as "skipped" in stats
            return

        # Now that we know we are writing to disk, officially mark the attempt
        ele.mark_text_download_attempt()

        # captions often contain emoji, which Windows' default code page
        # (cp1252) can't encode before Python 3.15
        async with aiofiles.open(filepath, "w", encoding="utf-8") as p:
            await p.write(content)

        await after_download_script(filepath)

        # Verify it actually wrote to disk to log success/failure
        if filepath.exists():
            ele.mark_text_downloaded(True)
        else:
            ele.mark_text_downloaded(False)

    except Exception as E:
        log.traceback_(f"{E}")
        log.traceback_(f"{traceback.format_exc()}")
        # If an error happens, ensure it's marked as an attempt so it logs as "failed"
        ele.mark_text_download_attempt()
        ele.mark_text_downloaded(False)
        return
