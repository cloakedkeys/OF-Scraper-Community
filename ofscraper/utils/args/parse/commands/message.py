from ofscraper.utils.args.parse.group_bundles.check import (
    common_args_check,
    main_check,
    make_check_command,
)
from ofscraper.utils.args.parse.groups.check_content import url_group

message_check = make_check_command(
    "msg_check",
    short_help="""\b
                Produces a media table from messages with filterable entries and quick downloads""",
    help="""The msg_check subcommand gathers information on media content from messages.
    It presents this data in a table format with filtering options for focused searches.
    Allows unlocked media entries to be directly downloaded through the table.""",
    options=(common_args_check, url_group, main_check),
)
