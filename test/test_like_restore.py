"""The corrective second like/unlike request decides the result (#212)."""

import contextlib

import pytest

import ofscraper.commands.scraper.actions.like.like as like


class Response:
    def __init__(self, favorite):
        self.favorite = favorite

    def json_(self):
        if self.favorite == "bad":
            raise ValueError("not JSON")
        return {"isFavorite": self.favorite, "id": 123}


class Session:
    """Replies to each request in turn with the next isFavorite value."""

    def __init__(self, *favorites):
        self.favorites = list(favorites)
        self.calls = 0

    @contextlib.contextmanager
    def requests(self, *args, **kwargs):
        self.calls += 1
        yield Response(self.favorites.pop(0))


class Post:
    id = 123

    def __init__(self):
        self.results = []

    def mark_like_attempt(self):
        pass

    def mark_post_liked(self, success=True):
        self.results.append(success)

    def mark_post_unliked(self, success=True):
        self.results.append(success)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(like.time, "sleep", lambda *_: None)


# (first reply, second reply) for a like: the first toggle unliked an already-liked post
@pytest.mark.parametrize("second", ["bad", False])
def test_like_fails_when_the_second_request_does_not_relike(second):
    post, session = Post(), Session(False, second)
    assert like._toggle_like_requests(session, post, 456) == 3
    assert post.results == [False]
    assert session.calls == 2


def test_like_restored():
    post, session = Post(), Session(False, True)
    assert like._toggle_like_requests(session, post, 456) == 2
    assert post.results == [True]


@pytest.mark.parametrize("second", ["bad", True])
def test_unlike_fails_when_the_second_request_does_not_unlike(second):
    post, session = Post(), Session(True, second)
    assert like._toggle_unlike_requests(session, post, 456) == 3
    assert post.results == [False]
    assert session.calls == 2


def test_unlike_restored():
    post, session = Post(), Session(True, False)
    assert like._toggle_unlike_requests(session, post, 456) == 2
    assert post.results == [True]


class MarkedPost(Post):
    """Records which of the two marks was set."""

    def mark_post_liked(self, success=True):
        self.results.append(("liked", success))

    def mark_post_unliked(self, success=True):
        self.results.append(("unliked", success))


@pytest.mark.parametrize(
    "toggle, replies, result, marks",
    [
        (like._toggle_like_requests, [True], 1, [("liked", True)]),
        (like._toggle_like_requests, ["bad"], 3, [("liked", False)]),
        (like._toggle_like_requests, [False, True], 2, [("liked", True)]),
        (like._toggle_unlike_requests, [False], 1, [("unliked", True)]),
        (like._toggle_unlike_requests, ["bad"], 3, [("unliked", False)]),
        (like._toggle_unlike_requests, [True, False], 2, [("unliked", True)]),
    ],
)
def test_toggle_result_and_mark(toggle, replies, result, marks):
    post, session = MarkedPost(), Session(*replies)
    assert toggle(session, post, 456) == result
    assert post.results == marks
    assert session.calls == len(replies)


@pytest.mark.parametrize("liked, replies", [(1, [True]), (0, [False])])
def test_toggle_takes_any_truthy_wanted_state(liked, replies):
    # compared by value, not identity: 1 and 0 mean liked and unliked
    post, session = MarkedPost(), Session(*replies)
    assert like._toggle_requests(session, post, 456, liked) == 1
    assert session.calls == 1


@pytest.mark.parametrize("process", [like.process_like, like.process_unlike])
def test_like_screen_is_set_up_once(monkeypatch, process):
    screens = []

    @contextlib.contextmanager
    def setup_live(name):
        screens.append(name)
        yield

    @contextlib.contextmanager
    def like_session(**kwargs):
        yield Session()

    class Progress:
        def __getattr__(self, name):
            return lambda *a, **k: None

    monkeypatch.setattr(like.progress_utils, "setup_live", setup_live)
    monkeypatch.setattr(like.progress_updater, "like", Progress())
    monkeypatch.setattr(like.progress_updater, "activity", Progress())
    monkeypatch.setattr(
        like.manager,
        "Manager",
        type("M", (), {"session": type("S", (), {"get_like_session": staticmethod(like_session)})}),
    )
    process(posts=[], model_id=456, username="creator1")
    assert screens == ["like"]
