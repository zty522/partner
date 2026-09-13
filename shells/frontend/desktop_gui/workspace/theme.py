"""Calm product tokens shared by all desktop components."""

LIGHT = {
    "canvas": "#F5F6F8", "surface": "#FFFFFF", "raised": "#FAFBFC",
    "text": "#172033", "muted": "#667085", "line": "#E6E8ED",
    "accent": "#3E5FD2", "accent_soft": "#F0F3FF", "danger": "#C4324A",
    "project": "#3157D5", "learning": "#087F73", "evolution": "#A35C00",
}


def stylesheet(t=LIGHT) -> str:
    return f"""
    * {{ font-family:'Microsoft YaHei UI','Noto Sans CJK SC','Segoe UI'; font-size:13px; }}
    QMainWindow, QWidget#Canvas {{ background:{t['canvas']}; color:{t['text']}; }}
    QFrame#Sidebar, QFrame#Inspector {{ background:{t['surface']}; }}
    QFrame#Sidebar {{ border-right:1px solid {t['line']}; }}
    QFrame#Inspector {{ border-left:1px solid {t['line']}; }}
    QFrame#Card {{ background:{t['surface']}; border:1px solid {t['line']}; border-radius:14px; }}
    QFrame#Card:hover {{ border:1px solid #D4D9E3; background:#FEFEFF; }}
    QFrame#Selected {{ background:{t['accent_soft']}; border:1px solid #C8D1F5; border-radius:12px; }}
    QLabel#Brand {{ font-size:19px; font-weight:700; color:{t['text']}; }}
    QLabel#Title {{ font-size:22px; font-weight:700; color:{t['text']}; }}
    QLabel#Heading {{ font-size:14px; font-weight:650; color:{t['text']}; }}
    QLabel#Muted {{ color:{t['muted']}; }} QLabel#Meta {{ color:{t['muted']}; font-size:11px; }}
    QPushButton {{ border:0; border-radius:8px; padding:8px 11px; color:{t['muted']}; background:transparent; }}
    QPushButton:hover {{ background:#F0F2F5; color:{t['text']}; }}
    QPushButton#Primary {{ color:white; background:{t['accent']}; font-weight:650; padding:10px 16px; }}
    QPushButton#Primary:hover {{ background:#2749BB; }}
    QPushButton#Secondary {{ border:1px solid {t['line']}; color:{t['text']}; background:{t['surface']}; }}
    QPushButton#Quiet {{ color:{t['muted']}; padding:8px 7px; }}
    QTextEdit, QLineEdit {{ color:{t['text']}; background:{t['surface']}; border:1px solid #D0D5DD;
                            border-radius:10px; padding:10px; selection-background-color:{t['accent']}; }}
    QTextEdit:focus, QLineEdit:focus {{ border:1px solid {t['accent']}; }}
    QComboBox, QSpinBox {{ color:{t['text']}; background:{t['surface']}; border:1px solid #D0D5DD;
                          border-radius:8px; padding:8px 10px; min-height:18px; }}
    QComboBox:focus, QSpinBox:focus {{ border:1px solid {t['accent']}; }}
    QComboBox::drop-down {{ border:0; width:26px; }}
    QTabWidget::pane {{ border:1px solid {t['line']}; border-radius:10px; background:{t['surface']}; }}
    QTabBar::tab {{ padding:9px 16px; color:{t['muted']}; border-bottom:2px solid transparent; }}
    QTabBar::tab:selected {{ color:{t['accent']}; border-bottom:2px solid {t['accent']}; font-weight:650; }}
    QCheckBox {{ color:{t['text']}; spacing:8px; }}
    QFrame#Divider {{ color:{t['line']}; background:{t['line']}; max-height:1px; }}
    QScrollArea {{ border:0; background:transparent; }}
    QScrollBar:vertical {{ width:7px; background:transparent; }}
    QScrollBar::handle:vertical {{ background:#C9CED8; min-height:28px; border-radius:3px; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
    QSplitter::handle {{ background:{t['line']}; width:1px; }}
    """
