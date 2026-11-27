import ofscraper.managers.manager as manager
from ofscraper.scripts.after_action_script import run_after_action_script
from ofscraper.utils.system.subprocess import run


def after_like_action_script(username, media, posts=None, action=None):
    run_after_action_script(
        username,
        media,
        posts=posts,
        action=action,
        default_action="like",
        label="like",
        level_key="AFTER_LIKE_ACTION_SCRIPT_SUBPROCESS_LEVEL",
        runner=run,
        manager_mod=manager,
    )
