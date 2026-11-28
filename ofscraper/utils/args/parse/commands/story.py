from ofscraper.utils.args.parse.arguments.check import force
from ofscraper.utils.args.parse.group_bundles.check import (
    common_args_check,
    make_check_command,
)
from ofscraper.utils.args.parse.groups.check_content import username_group

story_check = make_check_command(
    "story_check",
    short_help="""\b
                Produces a media table from stories and highlights with filterable entries and quick downloads""",
    help="""
    The story_check subcommand gathers information on media content from stories and highlights.
    It presents this data in a table format with filtering options for focused searches.
    Allows unlocked media entries to be directly downloaded through the table.
    """,
    options=(username_group, force, common_args_check),
)
