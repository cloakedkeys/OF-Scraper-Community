from ofscraper.utils.args.parse.group_bundles.check import (
    common_args_check,
    main_check,
    make_check_command,
)
from ofscraper.utils.args.parse.groups.check_content import (
    content_check_options,
    url_group,
)

post_check = make_check_command(
    "post_check",
    short_help="""\b
                Produces a media table from posts with filterable entries and quick downloads""",
    help="""The post_check subcommand gathers information on media content from posts.
    It presents this data in a table format with filtering options for focused searches.
    Allows unlocked media entries to be directly downloaded through the table.""",
    options=(url_group, main_check, common_args_check, content_check_options),
)
