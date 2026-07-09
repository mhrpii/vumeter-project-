"""Kontrol penceresi - native_proto.py icin koyu temali PyQt5 arayuz.
Tepsi ikonuna tiklaninca acilir. Modlar, temalar, kadranlar, parlaklik tek ekranda.
"""
import os

# Bu modul native_proto tarafindan import edilir; _state ve sabitler oradan gelir.


def build_control_window(state, color_themes, led_themes, vu_dial_count,
                         on_brightness, led_cache_clear, vu_cache_clear):
    from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                                 QLabel, QSlider, QFrame, QButtonGroup, QGridLayout)
    from PyQt5.QtCore import Qt

    # ---- Renkler (LCD estetigi: koyu + yesil) ----
    BG = "#0d0f0d"; PANEL = "#161a16"; GREEN = "#3ce65a"; GREEN_D = "#1a3a1f"
    TXT = "#d0e8d0"; DIM = "#7aa07a"; BORDER = "#2a352a"

    QSS = f"""
    QWidget#root {{ background: {BG}; }}
    QLabel {{ color: {TXT}; font-family: 'DejaVu Sans'; }}
    QLabel#header {{ color: {GREEN}; font-size: 15px; font-weight: bold; }}
    QLabel#section {{ color: {DIM}; font-size: 12px; font-weight: bold; }}
    QPushButton {{
        background: {PANEL}; color: {TXT}; border: 1px solid {BORDER};
        border-radius: 6px; padding: 10px 14px; font-size: 13px;
        font-family: 'DejaVu Sans';
    }}
    QPushButton:hover {{ border: 1px solid {GREEN}; }}
    QPushButton:checked {{ background: {GREEN_D}; border: 2px solid {GREEN}; color: {GREEN}; font-weight: bold; }}
    QPushButton#quit {{ background: #2a1616; border: 1px solid #5a2a2a; color: #e88; }}
    QPushButton#quit:hover {{ border: 1px solid #e55; }}
    QSlider::groove:horizontal {{ height: 6px; background: {BORDER}; border-radius: 3px; }}
    QSlider::handle:horizontal {{ background: {GREEN}; width: 16px; margin: -6px 0; border-radius: 8px; }}
    QSlider::sub-page:horizontal {{ background: {GREEN}; border-radius: 3px; }}
    QFrame#sep {{ background: {BORDER}; max-height: 1px; }}
    """

    MODES = ["Spektrum", "LED Spektrum", "VU Metre", "Sistem Monitoru", "Olcum Paneli"]

    w = QWidget(); w.setObjectName("root")
    w.setWindowTitle("Vumeter LCD - Kontrol")
    w.setStyleSheet(QSS)
    w.setMinimumWidth(440)
    root = QVBoxLayout(w); root.setContentsMargins(18, 18, 18, 18); root.setSpacing(12)

    # Baslik
    hdr = QLabel("VİNTAGE SES KONSOLU"); hdr.setObjectName("header")
    hdr.setAlignment(Qt.AlignCenter); root.addWidget(hdr)

    # --- MOD butonlari ---
    sec1 = QLabel("MOD"); sec1.setObjectName("section"); root.addWidget(sec1)
    mode_grid = QGridLayout(); mode_grid.setSpacing(8)
    mode_group = QButtonGroup(w); mode_group.setExclusive(True)
    mode_btns = {}
    for i, m in enumerate(MODES):
        b = QPushButton(m); b.setCheckable(True)
        b.setChecked(state["mode"] == m)
        mode_group.addButton(b); mode_btns[m] = b
        mode_grid.addWidget(b, i // 3, i % 3)
    root.addLayout(mode_grid)

    sep = QFrame(); sep.setObjectName("sep"); sep.setFrameShape(QFrame.HLine); root.addWidget(sep)

    # --- Moda ozel alt panel (dinamik) ---
    sub_label = QLabel("SECENEKLER"); sub_label.setObjectName("section"); root.addWidget(sub_label)
    sub_container = QWidget(); sub_layout = QHBoxLayout(sub_container)
    sub_layout.setContentsMargins(0, 0, 0, 0); sub_layout.setSpacing(8)
    root.addWidget(sub_container)

    _sub_btns = []

    def clear_sub():
        for b in _sub_btns:
            b.setParent(None)
        _sub_btns.clear()

    def build_sub():
        clear_sub()
        m = state["mode"]
        if m == "Spektrum":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, tn in enumerate(color_themes):
                b = QPushButton(tn); b.setCheckable(True)
                b.setChecked(state["theme_idx"] == i)
                def mk(idx):
                    def _f(): state["theme_idx"] = idx
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                sub_layout.addWidget(b); _sub_btns.append(b)
        elif m == "LED Spektrum":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, tn in enumerate(led_themes):
                b = QPushButton(tn); b.setCheckable(True)
                b.setChecked(state["led_theme_idx"] == i)
                def mk(idx):
                    def _f():
                        state["led_theme_idx"] = idx; led_cache_clear()
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                sub_layout.addWidget(b); _sub_btns.append(b)
        elif m == "VU Metre":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i in range(vu_dial_count):
                b = QPushButton(f"Kadran {i+1}"); b.setCheckable(True)
                b.setChecked(state["vu_dial_idx"] == i)
                def mk(idx):
                    def _f():
                        state["vu_dial_idx"] = idx; vu_cache_clear()
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                sub_layout.addWidget(b); _sub_btns.append(b)
        elif m == "Olcum Paneli":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, pn in enumerate(("Seviyeler", "Analiz")):
                b = QPushButton(pn); b.setCheckable(True)
                b.setChecked(state["meter_page"] == i)
                def mk(idx):
                    def _f(): state["meter_page"] = idx
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                sub_layout.addWidget(b); _sub_btns.append(b)
        else:  # Sistem Monitoru - secenek yok
            lbl = QLabel("Bu modun ek secenegi yok."); lbl.setStyleSheet(f"color:{DIM};")
            sub_layout.addWidget(lbl); _sub_btns.append(lbl)

    def on_mode_click(m):
        def _f():
            state["mode"] = m
            build_sub()
        return _f
    for m, b in mode_btns.items():
        b.clicked.connect(on_mode_click(m))
    build_sub()

    sep2 = QFrame(); sep2.setObjectName("sep"); sep2.setFrameShape(QFrame.HLine); root.addWidget(sep2)

    # --- Parlaklik ---
    br_row = QHBoxLayout()
    br_lbl = QLabel("PARLAKLIK"); br_lbl.setObjectName("section")
    br_val = QLabel(f"%{state['brightness']}"); br_val.setStyleSheet(f"color:{GREEN}; font-weight:bold;")
    br_row.addWidget(br_lbl); br_row.addStretch(); br_row.addWidget(br_val)
    root.addLayout(br_row)

    slider = QSlider(Qt.Horizontal); slider.setMinimum(10); slider.setMaximum(100)
    slider.setValue(state["brightness"]); slider.setSingleStep(5)
    def on_slider(v):
        v = int(round(v / 5) * 5)
        state["brightness"] = v
        br_val.setText(f"%{v}")
        on_brightness(v)
    slider.valueChanged.connect(on_slider)
    root.addWidget(slider)

    sep3 = QFrame(); sep3.setObjectName("sep"); sep3.setFrameShape(QFrame.HLine); root.addWidget(sep3)

    # --- Cikis ---
    quit_btn = QPushButton("Çıkış"); quit_btn.setObjectName("quit")
    def do_quit():
        state["running"] = False
    quit_btn.clicked.connect(do_quit)
    root.addWidget(quit_btn)

    # mod butonu degisince alt panel checked durumunu guncelle
    def refresh_mode_checks():
        for m, b in mode_btns.items():
            b.setChecked(state["mode"] == m)
    w._refresh = refresh_mode_checks

    return w
