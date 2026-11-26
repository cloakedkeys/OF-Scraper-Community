import cloup as click
from click import UsageError

import ofscraper.commands.tools as tools
from ofscraper.utils.args.parse.group_bundles.advanced_common import advanced_args
from ofscraper.utils.args.parse.group_bundles.main import main_program_args
import ofscraper.utils.args.parse.arguments.utils.retry as retry_helper


@click.group(
    help="OF-Scraper Options",
    epilog=tools.help_section(),
    context_settings=dict(help_option_names=["-h", "--help"]),
    invoke_without_command=True,
    show_constraints=True,
)
@main_program_args
@advanced_args
@click.pass_context
def program(ctx, *args, **kwargs):
    ctx = retry_helper.retry_modifiy(ctx)
    from ofscraper.commands.scraper.multi_profile import validate_multi_profile_args

    error = validate_multi_profile_args(ctx.params)
    if error:
        raise UsageError(error, ctx=ctx)
    return ctx.params, ctx.info_name
