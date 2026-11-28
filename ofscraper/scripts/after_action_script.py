import logging

import ofscraper.managers.manager as manager
import ofscraper.scripts.runner as runner
import ofscraper.utils.settings as settings

log = logging.getLogger("shared")


def run_after_action_script(
    username,
    media,
    posts=None,
    action=None,
    *,
    default_action,
    label,
    level_key,
):
    action = action or default_action
    name = f"after {label} action script"
    script_path = settings.get_settings().after_action_script
    if not runner.script_ready(script_path, name):
        return

    userdata = manager.Manager.current_model_manager.get_model(username)
    if not userdata:
        log.info(f"Could not retrieve user data for {username}. Aborting {name}.")
        return
    if not posts:
        posts = list({ele.post.id: ele.post for ele in (media or [])}.values())

    def payload():
        is_dict = isinstance(userdata, dict)
        return {
            "username": userdata["username"] if is_dict else userdata.name,
            "action": action,
            "model_id": userdata["id"] if is_dict else userdata.id,
            "media": [x.media for x in (media or [])],
            "posts": [x.post for x in (posts or [])],
            "userdata": userdata if is_dict else getattr(userdata, "model", None),
        }

    runner.run_user_script(
        script_path,
        payload,
        name=name,
        level_key=level_key,
        subject=username,
        timeout=1800,
    )
