"""Show concise numeric input without changing an untouched stored value."""

import math
import re

from PyQt6.QtWidgets import QLineEdit

from .numeric import number_text

_STRICT_ASCII_FLOAT_RE = re.compile(
    r'^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$'
)
_SIGN_MAP = str.maketrans({
    '\u2212': '-',
    '\uff0d': '-',
    '\uff0b': '+',
})


class NumberEdit(QLineEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._original_value = None
        self.textEdited.connect(self._edited)

    def _edited(self, _text):
        self._original_value = None

    def setText(self, text):
        self._original_value = None
        super().setText(text)

    def set_number(self, value):
        super().setText(number_text(value))
        self._original_value = value

    def value(self):
        if self._original_value is not None and self.text() == number_text(self._original_value):
            return self._original_value
        raw = self.text()
        normalized = raw.strip().translate(_SIGN_MAP)
        if not _STRICT_ASCII_FLOAT_RE.fullmatch(normalized):
            raise ValueError(f'请输入有效数值：{raw}')
        parsed = float(normalized)
        if not math.isfinite(parsed):
            raise ValueError(f'请输入有限数值：{raw}')
        return parsed

    def draft(self):
        original = self._original_value
        if original is not None and self.text() != number_text(original):
            original = None
        return self.text(), original

    def restore_draft(self, draft):
        text, original = draft
        super().setText(text)
        self._original_value = original
