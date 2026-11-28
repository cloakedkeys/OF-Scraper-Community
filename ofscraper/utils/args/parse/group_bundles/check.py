import functools

import cloup as click
from cloup import constraint
from cloup.constraints import mutually_exclusive

from ofscraper.utils.args.helpers.hide_args import hide_check_mode
from ofscraper.utils.args.parse.arguments.check import (
    check_mode_media_sort,
    force,
    text_only_option,
    text_option,
)
from ofscraper.utils.args.parse.arguments.metadata_filters import (
    check_media_id_filter_option,
    check_post_id_filter_option,
    downloaded_option,
    lock_option,
    posted_after,
    posted_before,
)
from ofscraper.utils.args.parse.arguments.post_content import download_type_option
from ofscraper.utils.args.parse.group_bundles.advanced_common import advanced_args
from ofscraper.utils.args.parse.group_bundles.utils.check import check_mode_changes
from ofscraper.utils.args.parse.groups.download import download_options
from ofscraper.utils.args.parse.groups.file import file_options
from ofscraper.utils.args.parse.groups.logging import logging_options
from ofscraper.utils.args.parse.groups.media_filter import (
    length_max,
    length_min,
    max_size_option,
    media_desc_option,
    media_type_option,
    min_size_option,
    quality_option,
)
from ofscraper.utils.args.parse.groups.program import program_options


def main_check(func):
    @force
    @text_option
    @text_only_option
    @constraint(mutually_exclusive, ["text", "text_only"])
    @functools.wraps(func)
    @click.pass_context
    def wrapper(ctx, *args, **kwargs):
        return func(ctx, *args, **kwargs)

    return wrapper


def make_check_command(name, short_help, help, options):
    """A check-mode command (post_check, msg_check, paid_check, story_check).

    ``options`` are the command's own option decorators, outermost first;
    every check command then takes the advanced options and has ``before``
    set by check_mode_changes. Running it returns ``(params, command name)``.
    """

    def params(ctx, *args, **kwargs):
        return ctx.params, ctx.info_name

    callback = advanced_args(check_mode_changes(params))
    for option in reversed(options):
        callback = option(callback)
    command = click.command(
        name, short_help=short_help, help=help, show_constraints=True
    )(callback)
    hide_check_mode(command)
    return command


def common_args_check(func):
    @program_options
    @logging_options
    @download_options
    @click.option_group(
        "Download Filter Options",
        quality_option,
        help="options for selecting which media is downloaded",
    )
    @click.option_group(
        "Table Filter Options",
        media_type_option,
        check_mode_media_sort,
        length_max,
        length_min,
        lock_option,
        downloaded_option,
        check_media_id_filter_option,
        check_post_id_filter_option,
        media_desc_option,
        posted_after,
        posted_before,
        download_type_option,
        help="Filters for controlling the initial table view",
    )
    @click.option_group(
        "Download Filter Options",
        min_size_option,
        max_size_option,
        help="Filters for controlling which media to download",
    )
    @file_options
    @functools.wraps(func)
    @click.pass_context
    def wrapper(ctx, *args, **kwargs):
        return func(ctx, *args, **kwargs)

    return wrapper
