import pytest
from InquirerPy.validator import EmptyInputValidator
from prompt_toolkit.document import Document
from prompt_toolkit.validation import ValidationError

from ofscraper.const.test_constants import *
from ofscraper.prompts.prompt_validators import (
    dateplaceholdervalidator,
    emptyListValidator,
    jsonValidator,
    namevalitator,
)


def test_emptyinput_error():
    document = Document()
    document._text = ""
    with pytest.raises(ValidationError):
        EmptyInputValidator().validate(document)


def test_emptyinput_noerror():
    """
    Assert your python code raises no exception.
    """
    document = Document()
    document._text = "t"
    try:
        EmptyInputValidator().validate(document)
    except ValidationError as exc:
        raise AssertionError(f"'{document._text} {exc}")


def test_empty_list_error():
    document = Document()
    document._text = []
    with pytest.raises(ValidationError):
        emptyListValidator().validate(document)


def test_empty_list_noerror():
    """
    Assert your python code raises no exception.
    """
    document = Document()
    document._text = ["t"]
    try:
        emptyListValidator().validate(document)
    except ValidationError as exc:
        raise AssertionError(f"'{document._text} {exc}")


def test_jsonvalidator_error():
    document = Document()
    document._text = INVALID_JSON
    with pytest.raises(ValidationError):
        jsonValidator().validate(document)


def test_json_noerror():
    """
    Assert your python code raises no exception.
    """
    document = Document()
    document._text = VALID_JSON
    try:
        jsonValidator().validate(document)
    except ValidationError as exc:
        raise AssertionError(f"'{document._text} {exc}")


def test_namevalidator_error():
    document = Document()
    document._text = INVALID_NAME
    with pytest.raises(ValidationError):
        namevalitator().validate(document)


def test_namevalidator_noerror():
    """
    Assert your python code raises no exception.
    """
    document = Document()
    document._text = VALID_NAME
    try:
        namevalitator().validate(document)
    except ValidationError as exc:
        raise AssertionError(f"'{document._text} {exc}")


###
# Date formatting is very flexiable
###
def test_dateinvalidformat():
    document = Document()
    document._text = DATE_INVALID
    with pytest.raises(ValidationError):
        dateplaceholdervalidator().validate(document)


def test_datevalidformat():
    document = Document()
    document._text = DATE_VALID
    try:
        dateplaceholdervalidator().validate(document)
    except ValidationError as exc:
        raise AssertionError(f"'{document._text} {exc}")


def test_datesillyvalidformat():
    document = Document()
    document._text = DATE_SILLY
    try:
        dateplaceholdervalidator().validate(document)
    except ValidationError as exc:
        raise AssertionError(f"'{document._text} {exc}")
