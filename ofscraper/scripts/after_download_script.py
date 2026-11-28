import pathlib
from typing import Union

import ofscraper.scripts.runner as runner
import ofscraper.utils.settings as settings


async def after_download_script(final_path: Union[str, pathlib.Path]):
    """Run after_download_script with the file's path on stdin, as plain text."""
    final_path = str(final_path)
    name = "after download script"
    script_path = settings.get_settings().after_download_script
    if not runner.script_ready(script_path, name, skip_in_dry_run=False):
        return
    await runner.async_run_user_script(
        script_path,
        lambda: final_path,
        name=name,
        level_key="AFTER_DOWNLOAD_SCRIPT_SUBPROCESS_LEVEL",
        subject=f"'{final_path}'",
        as_json=False,
        timeout=600,
    )
