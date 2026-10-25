# -*- coding: utf-8 -*-
"""
Shared look of the Attribute Table Studio tools.

Colours come from the Geometry and Table Compare palette (light panel
#F5F5F5 / cards #FAFAFA, dark panel #353535 / cards #3C3C3C, neutral grey
headers, blue primary actions) and switch automatically with the QGIS
light / dark palette.  :class:`Metrics` derives every control size from the
current font so all buttons, inputs and icons in a row line up at any
display scaling.
"""

from qgis.PyQt.QtGui import QColor, QFont, QFontMetrics, QPalette
from qgis.PyQt.QtWidgets import QApplication


def mix(a, b, t):
    """Blend colour *a* towards *b* by *t* (0..1)."""
    a, b = QColor(a), QColor(b)
    return QColor(
        int(a.red() * (1 - t) + b.red() * t),
        int(a.green() * (1 - t) + b.green() * t),
        int(a.blue() * (1 - t) + b.blue() * t),
    )


class Theme:
    """Resolved colours for the current appearance."""

    # (row background, accent) per Compare status
    LIGHT_STATUS = {
        "Added":        ("#D8ECFF", "#1E88E5"),
        "Deleted":      ("#FFD9D9", "#E53935"),
        "Modified":     ("#FFF2BF", "#F9A825"),
        "Unchanged":    ("#F1F1F1", "#9E9E9E"),
        "ChangedField": ("#FF9FCB", "#D81B7A"),
        "AcceptedValue": ("#D4F0A0", "#558B2F"),
        "RejectedValue": ("#E2D6F5", "#7E57C2"),
    }
    DARK_STATUS = {
        "Added":        ("#1F4568", "#64B5F6"),
        "Deleted":      ("#6B2A2A", "#EF5350"),
        "Modified":     ("#5E5120", "#FFCA28"),
        "Unchanged":    ("#383838", "#9E9E9E"),
        "ChangedField": ("#9C2A68", "#F06292"),
        "AcceptedValue": ("#4F6B1E", "#9CCC65"),
        "RejectedValue": ("#4E3B70", "#B39DDB"),
    }

    def __init__(self, palette=None):
        pal = palette or QApplication.instance().palette()
        role = QPalette.ColorRole
        win = pal.color(role.Window)
        txt = pal.color(role.WindowText)
        self.dark = dark = win.lightnessF() < 0.5

        self.text = txt.name()
        # headings: the brighter of WindowText / Text in dark themes (Night
        # Mapping draws labels #aaa but input text #ddd)
        strong = pal.color(role.Text)
        if dark and strong.lightnessF() > txt.lightnessF():
            self.text_strong = strong.name()
        else:
            self.text_strong = txt.name()
        self.text_dim = mix(txt, win, 0.38).name()
        self.text_faint = mix(txt, win, 0.55).name()
        self.panel = "#353535" if dark else "#F5F5F5"
        self.card = "#3C3C3C" if dark else "#FAFAFA"
        self.card_hover = "#4A4A4A" if dark else "#E9E9E9"
        self.card_pressed = "#525252" if dark else "#DEDEDE"
        self.border = "#555555" if dark else "#CCCCCC"
        self.border_soft = "#4A4A4A" if dark else "#DDDDDD"
        self.base = "#2B2B2B" if dark else "#FFFFFF"
        self.base_alt = "#303030" if dark else "#F7F7F7"
        self.header = "#454545" if dark else "#E4E4E4"
        self.status_bg = "#353535" if dark else "#F0F0F0"
        self.selected = "#1565C0" if dark else "#BBDEFB"
        self.selected_text = "#FFFFFF" if dark else "#0D2A4A"
        self.accent = "#1976D2" if dark else "#2196F3"
        self.accent_hover = "#1565C0" if dark else "#1976D2"
        self.accent_text = "#64B5F6" if dark else "#1565C0"
        self.accent_soft = "#203A55" if dark else "#E3F0FD"
        self.success = "#66BB6A" if dark else "#2E7D32"
        self.warning = "#FFB74D" if dark else "#E65100"
        self.danger = "#EF5350" if dark else "#C62828"
        self.old_colour = "#64B5F6" if dark else "#1565C0"
        self.new_colour = "#81C784" if dark else "#2E7D32"
        self.disabled_bg = "#555555" if dark else "#BDBDBD"
        self.disabled_fg = "#9A9A9A" if dark else "#EEEEEE"
        self.row_text = "#F2F2F2" if dark else "#1B1B1B"

        # Attribute Explorer data colours: text orange, numbers green,
        # booleans purple, NULL grey; dates use one blue that reads on both
        # the light and the dark table background
        self.data_text = "#E0A27A" if dark else "#A0430A"
        self.data_number = "#B5CEA8" if dark else "#2E7D32"
        self.data_temporal = "#3A80EA"
        self.data_bool = "#C9A0DC" if dark else "#7B1FA2"
        self.data_null = "#7A7A7A" if dark else "#9E9E9E"
        self.bar = "#4C9BE8" if dark else "#42A5F5"
        self.bar_hover = "#82BDF5" if dark else "#1E88E5"
        self.big_number = "#5AA9F0" if dark else "#1E88E5"

        status = self.DARK_STATUS if dark else self.LIGHT_STATUS
        self.status = {k: (QColor(v[0]), QColor(v[1]))
                       for k, v in status.items()}
        # Compare review decisions: accepted lime, rejected lavender
        self.accepted_bg = self.status["AcceptedValue"][0]
        self.rejected_bg = self.status["RejectedValue"][0]

    def bg(self, key):
        return self.status[key][0]

    def accent_of(self, key):
        return self.status[key][1]


class Metrics:
    """Control sizes derived from the font (identical in every tool)."""

    def __init__(self, font=None):
        font = font or QApplication.font()
        fm = QFontMetrics(font)
        self.font_h = fm.height()
        # one height for inputs, buttons and square icon buttons ...
        self.control_h = max(24, self.font_h + 8)
        # ... and a taller one for Compare's action buttons (Validate Data,
        # Compare OLD -> NEW) and Swap
        self.primary_h = max(28, self.font_h + 12)
        self.compact_h = self.control_h                 # filter cells
        self.icon = 16
        self.gap = 6                                    # inside a group
        self.group_gap = 14                             # between groups
        self.margin = 10
        self.radius = 4
        bold = QFont(font)
        bold.setBold(True)
        self.bold_fm = QFontMetrics(bold)
        self.fm = fm
        # heading sizes follow the QGIS font size (never smaller than body)
        base = font.pointSizeF() if font.pointSizeF() > 0 else 9.0
        self.heading_pt = round(base + 1.5, 1)
        self.display_pt = round(base + 3.0, 1)


def stylesheet(t, m):
    """Style sheet applied to the root widget of every tool."""
    return f"""
        QWidget#gtsPanel {{ background: {t.panel}; }}
        QFrame#gtsCard {{ background: {t.card}; border: 1px solid {t.border};
            border-radius: {m.radius}px; }}
        QFrame#gtsBand {{ background: {t.panel}; border: 1px solid {t.border};
            border-radius: {m.radius}px; }}
        QFrame#gtsSurface {{ background: {t.base}; border: 1px solid {t.border};
            border-radius: {m.radius}px; }}
        QLabel#gtsTitle {{ font-weight: 700; color: {t.text_strong}; }}
        QLabel#gtsHeading {{ font-weight: 700; font-size: {m.heading_pt}pt;
            color: {t.text_strong}; }}
        QLabel#gtsDisplay {{ font-weight: 700; font-size: {m.display_pt}pt;
            color: {t.text_strong}; }}
        QLabel#gtsDim {{ color: {t.text_dim}; }}
        QLabel#gtsStrong {{ font-weight: 700; color: {t.text_strong}; }}
        QLabel#gtsLink {{ color: {t.accent_text}; }}
        QWidget#gtsPanel QLabel:disabled, QWidget#gtsPanel QCheckBox:disabled {{
            background: transparent; color: {t.text_faint}; }}

        QToolButton#gtsButton, QToolButton#gtsSquare {{
            background: {t.card}; color: {t.text};
            border: 1px solid {t.border}; border-radius: {m.radius}px;
            padding: 0px 9px; }}
        QToolButton#gtsSquare {{ padding: 0px; }}
        QToolButton#gtsSquare[popupMode="1"] {{ padding-right: 13px; }}
        QToolButton#gtsSquare::menu-button {{ width: 13px;
            border-left: 1px solid {t.border}; }}
        QFrame#gtsVSep {{ color: {t.border}; }}
        QLabel#gtsEditStatus {{ color: {t.text_dim}; }}
        QLabel#gtsEditStatus[level="editing"] {{ color: {t.warning}; }}
        QLabel#gtsEditStatus[level="editing-clean"] {{ color: {t.warning}; }}
        QLabel#gtsEditStatus[level="busy"] {{ color: {t.accent_text}; }}
        QLabel#gtsEditStatus[level="error"] {{ color: {t.danger}; font-weight: 700; }}
        QLabel#gtsQuickTitle {{ font-weight: 700; color: {t.text_strong}; }}
        QToolButton#gtsButton:hover, QToolButton#gtsSquare:hover {{
            background: {t.card_hover}; }}
        QToolButton#gtsButton:pressed, QToolButton#gtsSquare:pressed {{
            background: {t.card_pressed}; }}
        QToolButton#gtsButton:checked, QToolButton#gtsSquare:checked {{
            background: {t.accent_soft}; border-color: {t.accent}; }}
        QToolButton#gtsButton:disabled, QToolButton#gtsSquare:disabled {{
            color: {t.text_faint}; border-color: {t.border_soft}; }}
        QToolButton#gtsButton[popupMode="2"] {{ padding-right: 20px; }}
        QToolButton#gtsButton::menu-indicator {{
            subcontrol-origin: padding; subcontrol-position: center right;
            right: 6px; }}
        QToolButton#gtsButton[gtsCompact="true"] {{ padding: 0px; }}
        QToolButton#gtsButton[gtsCompact="true"][popupMode="2"] {{
            padding: 0px 14px 0px 0px; }}
        QToolButton#gtsPrimary {{ background: {t.accent}; color: white;
            border: 1px solid {t.accent}; border-radius: {m.radius}px;
            padding: 0px 14px; font-weight: 700; }}
        QToolButton#gtsPrimary:hover {{ background: {t.accent_hover};
            border-color: {t.accent_hover}; }}
        QToolButton#gtsPrimary:disabled {{ background: {t.disabled_bg};
            border-color: {t.disabled_bg}; color: {t.disabled_fg}; }}
        QToolButton#gtsFlat {{ background: transparent; border: none;
            color: {t.accent_text}; padding: 0px 2px; }}
        QToolButton#gtsFlat:hover {{ text-decoration: underline; }}

        QFrame#gtsStatus {{ background: {t.status_bg};
            border: 1px solid {t.border};
            border-left: 5px solid {t.text_dim}; }}
        QFrame#gtsStatus[level="success"] {{ border-left-color: {t.success}; }}
        QFrame#gtsStatus[level="warning"] {{ border-left-color: {t.warning}; }}
        QFrame#gtsStatus[level="error"] {{ border-left-color: {t.danger}; }}
        QFrame#gtsStatus[level="busy"] {{ border-left-color: {t.accent}; }}
        QProgressBar#gtsProgress {{ border: 1px solid {t.border};
            border-radius: 3px; background: {t.base}; text-align: center;
            max-height: 14px; font-size: 8pt; }}
        QProgressBar#gtsProgress::chunk {{ background: {t.accent};
            border-radius: 2px; }}
        QProgressBar#gtsProgressWide {{ border: 1px solid {t.border};
            border-radius: 3px; background: {t.base}; text-align: center;
            color: {t.text}; }}
        QProgressBar#gtsProgressWide::chunk {{ background: {t.accent};
            border-radius: 2px; }}
        QFrame#gtsDivider {{ background: {t.border}; border: none;
            max-height: 1px; min-height: 1px; }}
        QFrame#gtsChip {{ background: {t.accent_soft};
            border: 1px solid {t.accent}; border-radius: 11px; }}
        QFrame#gtsChip QLabel {{ background: transparent; border: none;
            color: {t.text_strong}; }}
        QFrame#gtsChip QToolButton {{ background: transparent; border: none;
            border-radius: 8px; }}
        QFrame#gtsChip QToolButton:hover {{ background: {t.card_hover}; }}

        QFrame#gtsFilterCell {{ background: {t.base};
            border: 1px solid {t.border}; border-radius: 3px; }}
        QFrame#gtsFilterCell[state="active"] {{ border: 1px solid {t.accent}; }}
        QFrame#gtsFilterCell[state="error"] {{ border: 1px solid {t.danger}; }}
        QFrame#gtsFilterCell QLineEdit {{ background: transparent; border: none;
            padding: 0px 2px; color: {t.text}; }}
        QFrame#gtsFilterCell QLineEdit:disabled {{ color: {t.text_dim}; }}
        QToolButton#gtsOp {{ background: transparent; border: none;
            color: {t.accent_text}; font-weight: 700; padding: 0px 4px;
            min-width: 14px; }}
        QToolButton#gtsOp:hover {{ background: {t.card_hover};
            border-radius: 3px; }}
        QToolButton#gtsOp::menu-indicator {{ image: none; width: 0px; }}

        QTableView#gtsTable {{ background: {t.base};
            alternate-background-color: {t.base_alt};
            gridline-color: {t.border_soft}; border: 1px solid {t.border};
            border-radius: {m.radius}px; outline: 0; }}
        QTableView#gtsTable QHeaderView::section {{ background: {t.header};
            color: {t.text_strong}; padding: 0px 8px; border: none;
            border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border};
            font-weight: 700; }}
        QTableView#gtsTable QHeaderView::section:horizontal {{
            padding: 0px 22px 0px 8px; }}
        QTableView#gtsTable QHeaderView::section:vertical {{
            font-weight: 400; color: {t.text_dim}; padding: 0px 4px; }}
        QTableView#gtsTable QHeaderView::down-arrow,
        QTableView#gtsTable QHeaderView::up-arrow {{
            image: none; width: 0px; height: 0px; }}
        QTableView#gtsTable QTableCornerButton::section {{
            background: {t.header}; border: none;
            border-right: 1px solid {t.border}; border-bottom: 1px solid {t.border}; }}
        QListView#gtsFormList {{ background: {t.base}; border: 1px solid {t.border};
            border-radius: {m.radius}px; outline: 0; }}
        QListView#gtsFormList::item {{ padding: 3px 6px; color: {t.text}; }}
        QListView#gtsFormList::item:hover {{ background: {t.base_alt}; }}
        QListView#gtsFormList::item:selected {{ background: {t.selected};
            color: {t.selected_text}; }}
    """
