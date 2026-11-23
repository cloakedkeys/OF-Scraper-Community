import os


def load_general_paths_config():
    """
    Loads general paths configuration from environment variables with default values.

    Returns:
        A dictionary containing all loaded general paths configuration settings.
    """
    config = {}

    # SKIP_FILENAME_RETRIVAL: When on, skips looking up previously recorded filenames in the database during filename generation.
    # Default: False
    config["SKIP_FILENAME_RETRIVAL"] = os.getenv(
        "OFSC_SKIP_FILENAME_RETRIVAL", "False"
    ).lower() in ("true", "1")

    # SKIP_MEDIADIR_RETRIVAL: When on, skips looking up previously recorded media folders in the database during filename generation.
    # Default: False
    config["SKIP_MEDIADIR_RETRIVAL"] = os.getenv(
        "OFSC_SKIP_MEDIADIR_RETRIVAL", "False"
    ).lower() in ("true", "1")

    # --- General Paths Configuration ---
    # BATCH_TEMPFILE_CLEANUP: When on, removes leftover temporary files during batch cleanup.
    # Default: False
    config["BATCH_TEMPFILE_CLEANUP"] = os.getenv(
        "OFSC_BATCH_TEMPFILE_CLEANUP", "False"
    ).lower() in ("true", "1")

    return config
