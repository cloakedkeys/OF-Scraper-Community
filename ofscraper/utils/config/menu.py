import logging

import ofscraper.sites as sites
import ofscraper.utils.actions as actions
import ofscraper.utils.config.config as config_
import ofscraper.utils.config.file as config_file
import ofscraper.utils.config.utils.context as config_context
import ofscraper.utils.console as console_
import ofscraper.utils.profiles.data as profile_data
import ofscraper.utils.settings as settings

log = logging.getLogger("shared")


def update_config_helper(updated_config):
    previous_profile = profile_data.get_active_profile()
    previous_site = sites.current_site()
    current_config = config_file.open_config()
    config_.update_config_full(current_config, updated_config)
    profile_changed = profile_data.get_active_profile() != previous_profile
    if profile_changed:
        sites.clear_session_site()
    settings.update_settings()
    if profile_changed and sites.current_site() != previous_site:
        actions.remove_post_area()
        actions.remove_download_area()
        actions.remove_like_area()
    # the answer to a prompt, so shown even when --output quiets the shared console
    console_.get_console().print("`config.json` has been successfully edited.")


def run_config_section(prompt_fn):
    """ask one config menu section's prompts, then save and apply the answers"""
    with config_context.config_context():
        config_file.open_config()
        updated_config = prompt_fn()
    update_config_helper(updated_config)
