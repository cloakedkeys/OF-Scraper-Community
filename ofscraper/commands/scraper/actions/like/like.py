r"""

 _______  _______         _______  _______  _______  _______  _______  _______  _______
(  ___  )(  ____ \       (  ____ \(  ____ \(  ____ )(  ___  )(  ____ )(  ____ \(  ____ )
| (   ) || (    \/       | (    \/| (    \/| (    )|| (   ) || (    )|| (    \/| (    )|
| |   | || (__     _____ | (_____ | |      | (____)|| (___) || (____)|| (__    | (____)|
| |   | ||  __)   (_____)(_____  )| |      |     __)|  ___  ||  _____)|  __)   |     __)
| |   | || (                   ) || |      | (\ (   | (   ) || (      | (      | (\ (
| (___) || )             /\____) || (____/\| ) \ \__| )   ( || )      | (____/\| ) \ \__
(_______)|/              \_______)(_______/|/   \__/|/     \||/       (_______/|/   \__/

"""

import logging
import random
import time

import ofscraper.managers.manager as manager
import ofscraper.utils.live.screens as progress_utils
import ofscraper.utils.live.updater as progress_updater
import ofscraper.utils.of_env.of_env as of_env
from ofscraper.classes.of.posts import Post

log = logging.getLogger("shared")
warning_str = "\n[red]Current Like rate can eat through the ~1000 like limit in ~1 hour\nIncreasing the rate could lead to getting logged out[/red]"
like_str = "Performing Like Action on {name}" + warning_str
unlike_str = "Performing Unlike Action on {name}" + warning_str


def process_like(posts=None, model_id=None, username=None, **kwargs):
    with progress_utils.setup_live("like"):
        progress_updater.activity.update_task(
            description=like_str.format(name=username), visible=True
        )
        logging.getLogger("shared").warning(like_str.format(name=username))
        like(model_id, username, posts)


def process_unlike(posts=None, model_id=None, username=None, **kwargs):
    with progress_utils.setup_live("like"):
        progress_updater.activity.update_task(
            description=unlike_str.format(name=username), visible=True
        )
        logging.getLogger("shared").warning(unlike_str.format(name=username))
        unlike(model_id, username, posts)


def get_posts_for_unlike(post):
    return filter_for_favorited(post)


def get_posts_for_like(post):
    return filter_for_unfavorited(post)


def filter_for_unfavorited(posts: list) -> list:
    output = list(filter(lambda x: not x.favorited, posts))

    log.debug(f"[bold]Number of unliked post[/bold] {len(output)}")
    return output


def filter_for_favorited(posts: list) -> list:
    output = list(filter(lambda x: x.favorited, posts))

    log.debug(f"[bold]Number of liked post[/bold] {len(output)}")
    return output


def like(model_id, username, posts: list[Post]):
    return _like(model_id, username, posts, True)


def unlike(model_id, username, posts: list):
    return _like(model_id, username, posts, False)


def _like(model_id, username, posts: list, like_action: bool):
    like_str = (
        "Posts toggled from unlike to like...\n"
        if like_action
        else "Posts toggled from like to unlike...\n"
    )

    # the caller (process_like / process_unlike) has the like screen up
    with manager.Manager.session.get_like_session(
        sem_count=1,
        retries=of_env.getattr("API_LIKE_NUM_TRIES"),
    ) as c:
        task = progress_updater.like.add_overall_task(
            "checked posts...\n", total=len(posts)
        )
        task2 = progress_updater.like.add_overall_task(like_str, total=None)
        max_duration = of_env.getattr("MAX_SLEEP_DURATION_LIKE")
        min_duration = of_env.getattr("MIN_SLEEP_DURATION_LIKE")

        for post in posts:
            out = _toggle_requests(c, post, model_id, like_action)
            sleep_duration = random.uniform(min_duration, max_duration)
            if out == 1:
                progress_updater.like.update_overall_task(task2, advance=1)
            progress_updater.like.update_overall_task(task, advance=1)
            time.sleep(sleep_duration)
        progress_updater.like.remove_overall_task(task)
        progress_updater.like.remove_overall_task(task2)


def _toggle_like_requests(c, post: Post, model_id):
    return _toggle_requests(c, post, model_id, True)


def _toggle_unlike_requests(c, post: Post, model_id):
    return _toggle_requests(c, post, model_id, False)


def _toggle_requests(c, post: Post, model_id, liked: bool):
    """Toggle ``post`` until it is liked (``liked``) or unliked.

    The favorite endpoint flips the state, so a post that comes back in the
    wrong state was already in the wanted one and is toggled again.
    Returns 1 when toggled, 2 when toggled back, 3 when it failed.
    """
    if liked:
        # pauses between the two toggles
        sleep_duration = random.uniform(
            of_env.getattr("MIN_SLEEP_DURATION_LIKE"),
            of_env.getattr("MAX_SLEEP_DURATION_LIKE"),
        )
        mark, state, verb = post.mark_post_liked, "liked", "liking"
    else:
        sleep_duration = of_env.getattr("DOUBLE_TOGGLE_SLEEP_DURATION_LIKE")
        mark, state, verb = post.mark_post_unliked, "unliked", "unliking"
    post.mark_like_attempt()

    favorited, id = _like_request(c, post.id, model_id)
    if favorited is None:
        mark(success=False)
        return 3
    if bool(favorited) == liked:
        log.debug(f"ID: {id} changed to {state}")
        mark()
        return 1
    log.debug(f"ID: {id} was already {state}; {verb} it again")
    time.sleep(sleep_duration)
    favorited, _ = _like_request(c, id, model_id)
    if favorited is None or bool(favorited) != liked:
        mark(success=False)
        return 3
    log.debug(f"ID: {id} restored to {state}")
    mark()
    return 2


def _like_request(c, id, model_id):

    with c.requests(
        of_env.getattr("favoriteEP").format(id, model_id),
        method="post",
        retries=of_env.getattr("LIKE_MAX_RETRIES"),
    ) as r:
        try:
            return r.json_()["isFavorite"], r.json_()["id"]
        except Exception:
            # an unreadable response: the callers treat None as a failed toggle
            return None, id
