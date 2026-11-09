import re

import cloup as click
from click import BadParameter

from ofscraper.utils.args.parse.group_bundles.db import db_args


def parse_ids(value, option):
    """The whole ids in an option's value, as ints: one id, or several split
    by commas or spaces as the main command's --post-id takes them (#363).
    The database stores ids as numbers, so they're compared as numbers."""
    ids = set()
    for part in re.split(r"[,\s]+", str(value).strip()):
        if not part:
            continue
        if not part.isdigit():
            raise BadParameter(f"{part!r} is not an id", param_hint=option)
        ids.add(int(part))
    return ids


@db_args
@click.pass_context
def db(ctx, *args, **kwargs):
    if ctx.params["post_id"]:
        ctx.params["post_id"] = parse_ids(ctx.params["post_id"], "--post-id")
    if ctx.params["media_id"]:
        ctx.params["media_id"] = parse_ids(ctx.params["media_id"], "--media-id")
    return ctx.params, ctx.info_name
