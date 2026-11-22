"""
In-place editing of dimension labels on the 2D CAD canvases.

Shared by CrossSectionCADWidget and TopViewCADWidget. While painting, each
widget registers the rectangle of every dimension label it draws together
with the input key (from common.py) that the label shows. Double-clicking a
label then opens a small QLineEdit over it:

    * Enter or clicking outside  -> value is checked and handed to the owner
                                    through the widget's `cad_value_edited`
                                    signal (input key, value text)
    * Esc                        -> edit is cancelled

Only the fundamental parameters listed in EDITABLE_CAD_FIELDS can be edited.
Derived values (overhang, overall width, bracing spacing) are listed in
LOCKED_CAD_FIELDS and only show a "read-only" tooltip, because they are
solved from the fundamental ones and editing them directly would break the
layout equation:  overall width = (n - 1) x spacing + 2 x overhang.
"""

from PySide6.QtCore import Qt, QRectF, QPoint, QTimer
from PySide6.QtGui import QDoubleValidator
from PySide6.QtWidgets import QLineEdit, QToolTip

from osdagbridge.core.utils.common import (
    KEY_CARRIAGEWAY_WIDTH,
    KEY_MP_CB_SPACING,
    KEY_SPAN,
    KEY_TS_DECK_OVERHANG,
    KEY_TS_DECK_THICKNESS,
    KEY_TS_GIRDER_SPACING,
    KEY_TS_OVERALL_WIDTH,
)

# input key -> (CAD param name, factor from CAD units (mm) to input units, decimals)
EDITABLE_CAD_FIELDS = {
    KEY_CARRIAGEWAY_WIDTH: ("carriageway_width", 1 / 1000, 2),  # input in m
    KEY_TS_GIRDER_SPACING: ("girder_spacing",    1 / 1000, 2),  # input in m
    KEY_TS_DECK_THICKNESS: ("deck_thickness",    1,        0),  # input in mm
    KEY_SPAN:              ("span_length",       1 / 1000, 2),  # input in m
}

# Derived values: shown, but never editable from the CAD.
LOCKED_CAD_FIELDS = {
    KEY_TS_DECK_OVERHANG,
    KEY_TS_OVERALL_WIDTH,
    KEY_MP_CB_SPACING,
}

LOCKED_TOOLTIP = (
    "This is a derived value and cannot be edited here.\n"
    "Change Carriageway Width, Girder Spacing, Deck Thickness or Span instead."
)

_EDITOR_STYLE = """
    QLineEdit {
        background: #ffffff;
        border: 1.5px solid #90AF13;
        border-radius: 2px;
        padding: 0px 3px;
        font: 9pt 'Arial';
    }
"""


class CadLabelEditorMixin:
    """
    Mix into a QWidget that paints CAD labels. The widget must:
      * define `cad_value_edited = Signal(str, str)`
      * hold its CAD state in `self.params`
      * call `_init_label_editor()` in __init__
      * call `_reset_label_zones()` at the start of paintEvent
      * call `register_label_zone(rect, input_key)` for each label it draws
    """

    def _init_label_editor(self):
        # Off by default: the same widget is reused as a read-only preview
        # inside the Additional Inputs dialog.
        self.label_editing_enabled = False
        self._label_zones = []        # [(QRectF, input_key), ...] for the last paint
        self._label_editor = None     # active QLineEdit, if any
        self._label_editor_key = None

    # ------------------------------------------------------------------ zones
    def _reset_label_zones(self):
        self._label_zones = []

    def register_label_zone(self, rect, input_key):
        if rect is not None and input_key:
            self._label_zones.append((QRectF(rect), input_key))

    def _label_zone_at(self, pos):
        # Search last-drawn first so labels painted on top win.
        for rect, key in reversed(self._label_zones):
            # A few px of slack makes small labels easier to hit.
            if rect.adjusted(-3, -3, 3, 3).contains(pos):
                return rect, key
        return None, None

    # ------------------------------------------------------------- Qt events
    def mousePressEvent(self, event):
        # The canvas itself never takes keyboard focus, so a click on it would
        # not end the edit through focus-out. Commit explicitly instead.
        if self._label_editor is not None:
            self._close_label_editor(commit=True)
        super().mousePressEvent(event)

    def update_label_hover(self, pos):
        """Show a hand cursor + hint over editable labels (call from mouseMoveEvent)."""
        if not getattr(self, "label_editing_enabled", False):
            return
        _, key = self._label_zone_at(pos)
        if key in EDITABLE_CAD_FIELDS:
            self.setCursor(Qt.PointingHandCursor)
            self.setToolTip("Double-click to edit")
        elif key in LOCKED_CAD_FIELDS:
            self.setCursor(Qt.ForbiddenCursor)
            self.setToolTip("Derived value (read-only)")
        else:
            self.unsetCursor()
            self.setToolTip("")

    def mouseDoubleClickEvent(self, event):
        if not getattr(self, "label_editing_enabled", False):
            return super().mouseDoubleClickEvent(event)

        pos = event.position() if hasattr(event, "position") else event.pos()
        rect, key = self._label_zone_at(pos)

        if key in EDITABLE_CAD_FIELDS:
            self._open_label_editor(rect, key)
            event.accept()
            return

        if key in LOCKED_CAD_FIELDS:
            QToolTip.showText(self.mapToGlobal(QPoint(int(pos.x()), int(pos.y()))),
                              LOCKED_TOOLTIP, self)
            event.accept()
            return

        super().mouseDoubleClickEvent(event)

    # ---------------------------------------------------------------- editor
    def _current_display_value(self, input_key):
        param, factor, decimals = EDITABLE_CAD_FIELDS[input_key]
        value = self.params.get(param)
        if value is None:
            return ""
        return f"{float(value) * factor:.{decimals}f}"

    def _open_label_editor(self, rect, input_key):
        self._close_label_editor(commit=False)

        editor = QLineEdit(self)
        editor.setObjectName(f"cad_label_editor.{input_key}")
        editor.setStyleSheet(_EDITOR_STYLE)
        validator = QDoubleValidator(0.0, 1e9, EDITABLE_CAD_FIELDS[input_key][2] or 0, editor)
        validator.setNotation(QDoubleValidator.StandardNotation)
        editor.setValidator(validator)
        editor.setText(self._current_display_value(input_key))
        editor.setAlignment(Qt.AlignCenter)

        width = max(70, int(rect.width()) + 10)
        height = max(22, int(rect.height()) + 4)
        cx, cy = rect.center().x(), rect.center().y()
        editor.setGeometry(int(cx - width / 2), int(cy - height / 2), width, height)

        editor.returnPressed.connect(lambda: self._close_label_editor(commit=True))
        # Clicking anywhere else takes focus away -> commit (same as Enter).
        editor.editingFinished.connect(lambda: self._close_label_editor(commit=True))
        editor.installEventFilter(self)

        self._label_editor = editor
        self._label_editor_key = input_key
        editor.show()
        editor.setFocus(Qt.MouseFocusReason)
        editor.selectAll()

    def _close_label_editor(self, commit):
        editor, key = self._label_editor, self._label_editor_key
        if editor is None:
            return
        # Clear state first: returnPressed and editingFinished can both fire.
        self._label_editor = None
        self._label_editor_key = None

        text = editor.text().strip()
        editor.removeEventFilter(self)
        editor.hide()
        editor.deleteLater()

        if not commit or not text:
            return
        try:
            value = float(text)
        except ValueError:
            return
        if value <= 0:
            return
        if text == self._current_display_value(key):
            return  # nothing changed

        # The owner validates against the design limits, writes the value into
        # the basic/additional input dictionaries and triggers the redraw.
        self.cad_value_edited.emit(key, text)

    def _handle_label_editor_key(self, obj, event):
        """Esc cancels the edit. Returns True when the event was consumed."""
        if obj is self._label_editor and event.type() == event.Type.KeyPress \
                and event.key() == Qt.Key_Escape:
            QTimer.singleShot(0, lambda: self._close_label_editor(commit=False))
            return True
        return False
