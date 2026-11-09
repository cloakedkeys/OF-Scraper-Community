import re

import cloup as click
from click import BadParameter

from ofscraper.utils.args.parse.group_bundles.db import db_args

# an id is ASCII digits only: str.isdigit() also takes "²" and other digits
# int() can't read
ID = re.compile(r"[0-9]+")


def parse_ids(value, option):
    """The whole ids in an option's value, as ints: one id, or several split
    by commas or spaces as the main command's --post-id takes them (#363).
    The database stores ids as numbers, so they're compared as numbers. A
    value with no id in it is an error, not "no filter": with
    --unmark-permanent-skips that would un-mark every row."""
    ids = set()
    for part in re.split(r"[,\s]+", str(value).strip()):
        if not part:
            continue
        if not ID.fullmatch(part):
            raise BadParameter(f"{part!r} is not an id", param_hint=option)
        ids.add(int(part))
    if not ids:
        raise BadParameter("no id given", param_hint=option)
    return ids


@db_args
@click.pass_context
def db(ctx, *args, **kwargs):
    # None: the option wasn't given. Any value given, even an empty one, is
    # parsed
    if ctx.params["post_id"] is not None:
        ctx.params["post_id"] = parse_ids(ctx.params["post_id"], "--post-id")
    if ctx.params["media_id"] is not None:
        ctx.params["media_id"] = parse_ids(ctx.params["media_id"], "--media-id")
    return ctx.params, ctx.info_name
