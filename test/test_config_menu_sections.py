"""The config menu: each choice asks its section's prompts and saves the answers (#868)."""

from contextlib import nullcontext

import pytest

import ofscraper.main.open.load  # noqa: F401 (import order, see conftest)
import ofscraper.prompts.prompt_groups.config as config_prompts
import ofscraper.prompts.prompts as prompts
import ofscraper.utils.config.menu as config_menu
import ofscraper.utils.config.schema as schema
import ofscraper.utils.menu as menu
import ofscraper.utils.of_env.of_env as of_env

SECTION_CHOICES = [
    value for value in of_env.getattr("configPromptChoices").values() if value not in {"main", "quit"}
]


@pytest.fixture
def saved(monkeypatch):
    """the configs the menu saves, with no file touched"""
    saved = []
    monkeypatch.setattr(config_menu.config_context, "config_context", nullcontext)
    monkeypatch.setattr(config_menu.config_file, "open_config", lambda: {})
    monkeypatch.setattr(config_menu, "update_config_helper", saved.append)
    return saved


def test_every_menu_choice_has_a_section():
    assert set(menu.CONFIG_SECTION_PROMPTS) == set(SECTION_CHOICES)


@pytest.mark.parametrize("choice", SECTION_CHOICES)
def test_menu_choice_saves_what_its_section_returns(monkeypatch, saved, choice):
    prompt_name = menu.CONFIG_SECTION_PROMPTS[choice]
    monkeypatch.setattr(prompts, prompt_name, lambda: {"answered": prompt_name})

    menu.config_menu_helper(choice)

    assert saved == [{"answered": prompt_name}]


def test_unknown_menu_choice_does_nothing(saved):
    menu.config_menu_helper("not-a-section")
    assert saved == []


def test_response_type_asks_every_responsetype_key_and_applies_the_answers(monkeypatch):
    asked = []

    def batch(*questions, **kwargs):
        asked.extend(q["name"] for q in questions)
        return {q["name"]: f"{q['name']}-folder" for q in questions}

    monkeypatch.setattr(config_prompts.promptClasses, "batchConverter", batch)
    monkeypatch.setattr(config_prompts.config_file, "open_config", lambda: {})

    result = config_prompts.response_type()

    expected = list(schema.get_current_config_schema(False)["responsetype"])
    assert sorted(asked) == sorted(expected)  # streams included
    assert result["responsetype"] == {key: f"{key}-folder" for key in expected}
