import ofscraper.managers.manager as manager
import ofscraper.scripts.runner as runner
import ofscraper.utils.settings as settings


def final_script(summary=None):
    """Run post_script with the processed and unprocessed users per action,
    and the run record (run_record.py) as ``summary``."""
    name = "final script"
    script_path = settings.get_settings().post_script
    if not runner.script_ready(script_path, name):
        return

    def payload():
        model_manager = manager.Manager.current_model_manager
        if settings.get_settings().command == "metadata":
            scrape_paid_activity = "scrape_paid_metadata"
        else:
            scrape_paid_activity = "scrape_paid_download"
        return {
            "like_processed_users": list(model_manager.get_processed("like")),
            "like_unprocessed_users": list(model_manager.get_unprocessed("like")),
            "unlike_processed_users": list(model_manager.get_processed("unlike")),
            "unlike_unprocessed_users": list(model_manager.get_unprocessed("unlike")),
            "download_processed_users": list(model_manager.get_processed("download")),
            "download_unprocessed_users": list(
                model_manager.get_unprocessed("download")
            ),
            "scrape_paid_processed_users": list(
                model_manager.get_processed(scrape_paid_activity)
            ),
            "scrape_paid_unprocessed_users": list(
                model_manager.get_unprocessed(scrape_paid_activity)
            ),
            "summary": summary,
        }

    runner.run_user_script(
        script_path,
        payload,
        name=name,
        level_key="FINAL_SCRIPT_SUBPROCESS_LEVEL",
        timeout=1800,
    )
