from textual.widgets import Label, SelectionList
from textual.widgets.selection_list import Selection

from ofscraper.classes.table.fields.selectfield import SelectField

# (value, id suffix) for each post type, in the order the list shows them
RESPONSE_TYPES = (
    ("Timeline", "timeline"),
    ("Archived", "archived"),
    ("Pinned", "pinned"),
    ("Streams", "stream"),
    ("Stories", "stories"),
    ("Highlights", "highlights"),
    ("Messages", "message"),
)


class ResponseField(SelectField):
    DEFAULT_CSS = """
    ResponseField {
        column-span: 4;
        height: auto;
    }
    """

    def __init__(self, name: str, label: str | None = None) -> None:
        name = name.lower()
        super().__init__(name=name, classes="container")
        self.filter_name = name
        self.label_text = label or "Post Type (checked = shown)"
        self._selections = {
            value: Selection(
                value,
                value=value,
                disabled=False,
                id=f"{self.filter_name}_{suffix}",
                initial_state=True,
            )
            for value, suffix in RESPONSE_TYPES
        }

    def compose(self):
        yield Label(self.label_text)
        yield SelectionList(*self._selections.values())

    def update_table_val(self, val):
        self.query_one(SelectionList).deselect_all()
        selection = self._selections.get(self._normalize(val))
        if selection is not None:
            self.query_one(SelectionList).select(selection)

    @staticmethod
    def _normalize(value):
        normalized = str(value or "").strip().capitalize()
        aliases = {
            "Stream": "Streams",
            "Message": "Messages",
            "Highlight": "Highlights",
            "Story": "Stories",
        }
        return aliases.get(normalized, normalized)

    def compare(self, value):
        return self._normalize(value) in self.query_one(SelectionList).selected
