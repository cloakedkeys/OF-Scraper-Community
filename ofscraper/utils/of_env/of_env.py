from ofscraper.utils.of_env import get_all_configs
import os


from ofscraper.utils.of_env import get_all_configs
import os
import json
import logging


def _as_type_of(text, current):
    """
    Convert an environment variable's text to the type of the configured
    value, the way the loaders in values/ convert their own variables.
    """
    if isinstance(current, bool):
        return text.strip().lower() in ("true", "1")
    if isinstance(current, (int, float)):
        return type(current)(text)
    if isinstance(current, (list, tuple, set, frozenset)):
        # JSON is what an env file writes for a list; otherwise comma-separated
        if text.strip().startswith("["):
            items = json.loads(text)
        else:
            items = [s.strip() for s in text.split(",") if s.strip()]
        return type(current)(items)
    if isinstance(current, dict):
        return json.loads(text)
    return text


def getattr(val, default=None):
    """
    Retrieves a configuration value.
    Order of precedence: Environment Variable > Custom Config > Default Config.
    Returns default if the key isn't set anywhere.
    """
    # Load all settings if not already loaded
    config_dict = get_all_configs()
    # 1. Check Environment Variable
    env_val = os.environ.get(val)
    if env_val is not None:
        if val not in config_dict:
            return env_val
        try:
            return _as_type_of(env_val, config_dict[val])
        except (TypeError, ValueError) as E:
            logging.getLogger("shared").warning(
                f"{val}: can't use the environment value {env_val!r} ({E}), "
                f"using {config_dict[val]!r}"
            )
            return config_dict[val]
    if val in config_dict:  # Check if key exists in the aggregated settings
        return config_dict.get(val)
    return default


def getkeys(forced=False):
    return get_all_configs(forced=forced)
