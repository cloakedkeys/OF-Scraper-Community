from ofscraper.utils.args.parse.group_bundles.check import (
    common_args_check,
    main_check,
    make_check_command,
)
from ofscraper.utils.args.parse.groups.check_content import username_group

paid_check = make_check_command(
    "paid_check",
    short_help="""\b
                Produces a media table from purchases with filterable entries and quick downloads""",
    help="""
    The paid_check subcommand gathers information on media content from purchases.
    It presents this data in a table format with filtering options for focused searches.
    Allows unlocked media entries to be directly downloaded through the table.
    """,
    options=(common_args_check, username_group, main_check),
)
