"""Kontrol penceresi - native_proto.py icin koyu temali PyQt5 arayuz.
Tepsi ikonuna tiklaninca acilir. Modlar, temalar, kadranlar, parlaklik tek ekranda.
"""
import os

# Bu modul native_proto tarafindan import edilir; _state ve sabitler oradan gelir.


def build_control_window(state, color_themes, led_themes, vu_dial_count,
                         on_brightness, led_cache_clear, vu_cache_clear):
    from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QCheckBox,
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

    # Tema/etiket adlarini ekranda Turkce goster (kod anahtari degismez)
    _TR_LABELS = {
        "Camgobegi": "Camgöbeği",
        "Yesil-Sari-Kirmizi": "Yeşil-Sarı-Kırmızı",
        "Sari": "Sarı", "Kirmizi": "Kırmızı", "Yesil": "Yeşil",
        "Turuncu": "Turuncu", "Mavi": "Mavi", "Mor": "Mor",
        "Kizil": "Kızıl", "Camgobek": "Camgöbeği",
        "Olcum Paneli": "Ölçüm Paneli",
        "Sistem Monitoru": "Sistem Monitörü",
        "Disk Sicakliklari": "Disk Sıcaklıkları",
        "Cekirdek Isi Haritasi": "Çekirdek Isı Haritası",
        "Sensorler": "Sensörler",
    }
    def _tr(s):
        return _TR_LABELS.get(s, s)

    def _save_settings():
        # state'i dogrudan diske yaz (import edilen modul ayri instance olabilir,
        # o yuzden aldigimiz gercek state sozlugunu kaydediyoruz)
        try:
            import json, os
            path = os.path.expanduser("~/.config/vumeter/settings.json")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            keys = ["theme_idx", "ch_layout", "sens_mult", "mode",
                    "led_theme_idx", "vu_dial_idx", "meter_page",
                    "sysmon_page", "brightness"]
            data = {k: state.get(k) for k in keys if k in state}
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception:
            pass

    MODES = ["Spektrum", "LED Spektrum", "VU Metre", "Sistem Monitoru", "Olcum Paneli"]

    w = QWidget(); w.setObjectName("root")
    w.setWindowTitle("Vumeter LCD - Kontrol")
    w.setStyleSheet(QSS)
    w.setMinimumWidth(300)
    w.setMaximumWidth(340)
    root = QVBoxLayout(w); root.setContentsMargins(14, 14, 14, 14); root.setSpacing(10)

    # Baslik
    hdr = QLabel("VİNTAGE SES KONSOLU"); hdr.setObjectName("header")
    hdr.setAlignment(Qt.AlignCenter); root.addWidget(hdr)

    # --- MOD butonlari ---
    sec1 = QLabel("MOD"); sec1.setObjectName("section"); root.addWidget(sec1)
    mode_grid = QGridLayout(); mode_grid.setSpacing(8)
    mode_group = QButtonGroup(w); mode_group.setExclusive(True)
    mode_btns = {}
    for i, m in enumerate(MODES):
        b = QPushButton(_tr(m)); b.setCheckable(True)
        b.setChecked(state["mode"] == m)
        mode_group.addButton(b); mode_btns[m] = b
        mode_grid.addWidget(b, i // 2, i % 2)
    root.addLayout(mode_grid)

    sep = QFrame(); sep.setObjectName("sep"); sep.setFrameShape(QFrame.HLine); root.addWidget(sep)

    # --- Moda ozel alt panel (dinamik) ---
    sub_label = QLabel("SECENEKLER"); sub_label.setObjectName("section"); root.addWidget(sub_label)
    sub_container = QWidget(); sub_layout = QGridLayout(sub_container)
    sub_layout.setContentsMargins(0, 0, 0, 0); sub_layout.setSpacing(8)
    _grid_pos = [0]  # eklenen buton sayaci (2 sutun grid icin)
    def _grid_add(btn):
        sub_layout.addWidget(btn, _grid_pos[0] // 2, _grid_pos[0] % 2)
        _grid_pos[0] += 1
    root.addWidget(sub_container)

    _sub_btns = []

    def clear_sub():
        _grid_pos[0] = 0
        for b in _sub_btns:
            b.setParent(None)
        _sub_btns.clear()


    def build_sub():
        clear_sub()
        m = state["mode"]
        if m == "Spektrum":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, tn in enumerate(color_themes):
                b = QPushButton(_tr(tn)); b.setCheckable(True)
                b.setChecked(state["theme_idx"] == i)
                def mk(idx):
                    def _f(): state["theme_idx"] = idx; _save_settings()
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                _grid_add(b); _sub_btns.append(b)
        elif m == "LED Spektrum":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, tn in enumerate(led_themes):
                b = QPushButton(_tr(tn)); b.setCheckable(True)
                b.setChecked(state["led_theme_idx"] == i)
                def mk(idx):
                    def _f():
                        state["led_theme_idx"] = idx; led_cache_clear(); _save_settings()
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                _grid_add(b); _sub_btns.append(b)
        elif m == "VU Metre":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i in range(vu_dial_count):
                b = QPushButton(f"Kadran {i+1}"); b.setCheckable(True)
                b.setChecked(state["vu_dial_idx"] == i)
                def mk(idx):
                    def _f():
                        state["vu_dial_idx"] = idx; vu_cache_clear(); _save_settings()
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                _grid_add(b); _sub_btns.append(b)
        elif m == "Olcum Paneli":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, pn in enumerate(("Seviyeler", "Analiz")):
                b = QPushButton(pn); b.setCheckable(True)
                b.setChecked(state["meter_page"] == i)
                def mk(idx):
                    def _f(): state["meter_page"] = idx; _save_settings()
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                _grid_add(b); _sub_btns.append(b)
        elif m == "Sistem Monitoru":
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, pn in enumerate(("Sensörler", "Disk Sıcaklığı", "Çekirdek Isısı")):
                b = QPushButton(pn); b.setCheckable(True)
                b.setChecked(state.get("sysmon_page", 0) == i)
                def mk(idx):
                    def _f(): state["sysmon_page"] = idx; _save_settings()
                    return _f
                b.clicked.connect(mk(i)); grp.addButton(b)
                _grid_add(b); _sub_btns.append(b)
        else:
            lbl = QLabel("Bu modun ek secenegi yok."); lbl.setStyleSheet(f"color:{DIM};")
            sub_layout.addWidget(lbl, 0, 0, 1, 2); _sub_btns.append(lbl)

    def on_mode_click(m):
        def _f():
            state["mode"] = m; _save_settings()
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
        _save_settings()
    slider.valueChanged.connect(on_slider)
    root.addWidget(slider)

    sep_h = QFrame(); sep_h.setObjectName("sep"); sep_h.setFrameShape(QFrame.HLine); root.addWidget(sep_h)

    # --- Hassasiyet (canli carpan, cava restart yok) ---
    hs_row = QHBoxLayout()
    hs_lbl = QLabel("HASSASIYET"); hs_lbl.setObjectName("section")
    _sm0 = state.get("sens_mult", 1.0)
    hs_val = QLabel(f"{_sm0:.1f}x"); hs_val.setStyleSheet(f"color:{GREEN}; font-weight:bold;")
    hs_row.addWidget(hs_lbl); hs_row.addStretch(); hs_row.addWidget(hs_val)
    root.addLayout(hs_row)

    # slider 5..50 -> carpan 0.5x..5.0x (10'a bolerek)
    hs_slider = QSlider(Qt.Horizontal); hs_slider.setMinimum(5); hs_slider.setMaximum(50)
    hs_slider.setValue(int(_sm0 * 10)); hs_slider.setSingleStep(1)
    def on_hs(v):
        mult = v / 10.0
        state["sens_mult"] = mult
        hs_val.setText(f"{mult:.1f}x")
        _save_settings()
    hs_slider.valueChanged.connect(on_hs)
    root.addWidget(hs_slider)

    sep_c = QFrame(); sep_c.setObjectName("sep"); sep_c.setFrameShape(QFrame.HLine); root.addWidget(sep_c)

    # --- Kanal duzeni (tiz/bas sag/sol) ---
    ch_lbl = QLabel("KANAL DUZENI"); ch_lbl.setObjectName("section"); root.addWidget(ch_lbl)
    ch_grid = QGridLayout(); ch_grid.setSpacing(8)
    _ch_names = ["L + R", "L + R ters", "L ters + R", "Ikisi ters"]
    ch_group = QButtonGroup(w); ch_group.setExclusive(True)
    for i, cn in enumerate(_ch_names):
        b = QPushButton(cn); b.setCheckable(True)
        b.setChecked(state.get("ch_layout", 1) == i)
        def mk(idx):
            def _f(): state["ch_layout"] = idx; _save_settings()
            return _f
        b.clicked.connect(mk(i)); ch_group.addButton(b)
        ch_grid.addWidget(b, i // 2, i % 2)
    root.addLayout(ch_grid)

    sep3 = QFrame(); sep3.setObjectName("sep"); sep3.setFrameShape(QFrame.HLine); root.addWidget(sep3)

    # --- Otomatik Baslat (LaunchAgent) ---
    import os as _os
    _LA_PLIST = _os.path.expanduser("~/Library/LaunchAgents/com.vumeter.lcd.plist")
    def _autostart_aktif():
        return _os.path.exists(_LA_PLIST)
    def _autostart_ac():
        app = "/Applications/VU Meter LCD.app"
        la_dir = _os.path.expanduser("~/Library/LaunchAgents")
        _os.makedirs(la_dir, exist_ok=True)
        plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.vumeter.lcd</string>
    <key>ProgramArguments</key>
    <array><string>{app}/Contents/MacOS/launcher</string></array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><false/>
</dict>
</plist>
"""
        try:
            with open(_LA_PLIST, "w") as f:
                f.write(plist)
            _os.system(f'launchctl unload "{_LA_PLIST}" 2>/dev/null')
            _os.system(f'launchctl load "{_LA_PLIST}" 2>/dev/null')
        except Exception:
            pass
    def _autostart_kapat():
        try:
            _os.system(f'launchctl unload "{_LA_PLIST}" 2>/dev/null')
            if _os.path.exists(_LA_PLIST):
                _os.remove(_LA_PLIST)
        except Exception:
            pass

    auto_cb = QCheckBox("Mac açılınca otomatik başlat")
    auto_cb.setChecked(_autostart_aktif())
    def _on_auto(state):
        if state:
            _autostart_ac()
        else:
            _autostart_kapat()
    auto_cb.toggled.connect(_on_auto)
    root.addWidget(auto_cb)

    # --- Cikis ---
    quit_btn = QPushButton("Çıkış"); quit_btn.setObjectName("quit")
    def do_quit():
        state["running"] = False
        try:
            from PyQt5.QtWidgets import QApplication
            QApplication.quit()
        except Exception:
            pass
    quit_btn.clicked.connect(do_quit)
    root.addWidget(quit_btn)

    # mod butonu degisince alt panel checked durumunu guncelle
    def refresh_mode_checks():
        for m, b in mode_btns.items():
            b.setChecked(state["mode"] == m)
    w._refresh = refresh_mode_checks

    return w
