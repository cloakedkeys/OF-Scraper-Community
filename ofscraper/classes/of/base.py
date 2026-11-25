import importlib
import logging
import re

from bs4 import BeautifulSoup

import ofscraper.utils.settings as settings

html_parser = "lxml" if importlib.util.find_spec("lxml") else "html.parser"
log = logging.getLogger("shared")
# (textlength, text_type) settings whose cut has been logged
_cut_logged = set()


class base:
    def __init__(self):
        pass

    def text_trunicate(self, text):
        text = str(text)
        if text is None:
            return "None"
        if len(text) == 0:
            return text
        length = int(settings.get_settings().text_length)
        if length == 0:
            return text
        sep = settings.get_settings().space_replacer or " "
        text_type = settings.get_settings().text_type
        if text_type == "letter":
            cut = len(text) > length
            text = "".join(list(text)[:length])
        else:
            # text has usually been through file_cleanup already, which swaps
            # spaces for space_replacer, so words are split on that. If the
            # original text already contained that character (e.g. "_" in a
            # caption with "_" as the replacer), it also counts as a word
            # break; the original spaces can't be told apart at this point.
            word_sep = sep if sep in text else " "
            words = [x for x in text.split(word_sep) if x]
            cut = len(words) > length
            text = word_sep.join(words[:length])
        # a cut can end on a separator; don't leave "Good_" or "Good "
        text = re.sub(f"(?:{re.escape(sep)}| )+$", "", text)
        # about the setting, not the text: said once per run, not per media
        if cut and (length, text_type) not in _cut_logged:
            _cut_logged.add((length, text_type))
            unit = "letters" if text_type == "letter" else "words"
            log.debug(
                f"{{text}} cut to {length} {unit} (text_type_default: {text_type})"
            )
        return text

    def file_cleanup(self, text):
        text = str(text)
        text = re.sub("<[^>]*>", "", text)
        # Fansly text is plain already (normalize._item_dict); removing the
        # codes again would drop an escaped "\[s:lg\]", shown as "[s:lg]"
        text = re.sub(r'[\n<>:"/\|?*:;]+', "", text)
        text = re.sub("-+", "_", text)
        text = re.sub(" +", " ", text)
        text = re.sub(" ", settings.get_settings().space_replacer, text)
        return text

    def db_cleanup(self, string):
        string = string or ""
        string = re.sub("<[^>]*>", "", string)
        # Fansly text is plain already (normalize._item_dict): removing the
        # codes again would turn an escaped "\*sale\*", shown as "*sale*",
        # into "sale"
        string = " ".join(string.split())
        string = BeautifulSoup(string, html_parser).get_text()
        return string
