"""Masaustu surumu kontrol penceresi (tepsi ikonuna SOL tik ile acilir).
Modlar, temalar, kadranlar tek ekranda - her defasinda sag tik menu gerekmez."""


def build_control_window(state, color_themes, led_themes, vu_dial_count,
                         led_cache_clear, vu_cache_clear, open_sysmon, on_quit):
    from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
                                 QLabel, QFrame, QButtonGroup, QGridLayout)
    from PyQt5.QtCore import Qt

    GREEN = "#3ce65a"; GREEN_D = "#14361f"; BG = "#0d1117"; CARD = "#161b22"
    w = QWidget()
    w.setWindowTitle("Vintage Audio Console - Kontrol")
    w.setStyleSheet(f"""
    QWidget {{ background: {BG}; color: #c8d2de; font-family: 'DejaVu Sans'; font-size: 14px; }}
    QPushButton {{ background: {CARD}; border: 1px solid #232b36; border-radius: 8px;
                   padding: 10px; color: #c8d2de; }}
    QPushButton:hover {{ border: 1px solid {GREEN}; }}
    QPushButton:checked {{ background: {GREEN_D}; border: 2px solid {GREEN}; color: {GREEN}; font-weight: bold; }}
    QPushButton#quit {{ background: #2a1616; border: 1px solid #5a2a2a; color: #e88; }}
    QPushButton#quit:hover {{ border: 1px solid #e55; }}
    QLabel#hdr {{ color: {GREEN}; font-weight: bold; font-size: 13px; }}
    """)
    root = QVBoxLayout(w); root.setContentsMargins(16, 16, 16, 16); root.setSpacing(10)

    MODES = ["Spektrum", "Spektrum 2", "LED Spektrum", "LED Nokta", "VU Metre", "Olcum Paneli"]

    root.addWidget(_hdr("MOD"))
    mode_grid = QGridLayout(); mode_grid.setSpacing(8)
    mode_group = QButtonGroup(w); mode_group.setExclusive(True)
    mode_btns = {}
    for i, m in enumerate(MODES):
        b = QPushButton(m); b.setCheckable(True)
        b.setChecked(state["mode"] == m)
        mode_group.addButton(b); mode_btns[m] = b
        mode_grid.addWidget(b, i // 3, i % 3)
    root.addLayout(mode_grid)

    root.addWidget(_sep())
    opt_hdr = _hdr("SEÇENEKLER"); root.addWidget(opt_hdr)
    opt_box = QVBoxLayout(); opt_box.setSpacing(6)
    opt_container = QWidget(); opt_container.setLayout(opt_box)
    root.addWidget(opt_container)

    def clear_layout(lay):
        while lay.count():
            it = lay.takeAt(0)
            wdg = it.widget()
            if wdg is not None:
                wdg.deleteLater()
            elif it.layout() is not None:
                clear_layout(it.layout())

    def rebuild_options():
        clear_layout(opt_box)
        m = state["mode"]
        if m in ("Spektrum", "Spektrum 2"):
            opt_hdr.setText("RENK TEMASI")
            grid = QGridLayout(); grid.setSpacing(6)
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, tn in enumerate(color_themes):
                b = QPushButton(tn); b.setCheckable(True)
                b.setChecked(state["theme_idx"] == i)
                grp.addButton(b)
                def mk(idx):
                    def _f(): state["theme_idx"] = idx
                    return _f
                b.clicked.connect(mk(i))
                grid.addWidget(b, i // 2, i % 2)
            opt_box.addLayout(grid)
        elif m in ("LED Spektrum", "LED Nokta"):
            opt_hdr.setText("LED TEMASI")
            grid = QGridLayout(); grid.setSpacing(6)
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i, tn in enumerate(led_themes):
                b = QPushButton(tn); b.setCheckable(True)
                b.setChecked(state["led_theme_idx"] == i)
                grp.addButton(b)
                def mk(idx):
                    def _f():
                        state["led_theme_idx"] = idx; led_cache_clear()
                    return _f
                b.clicked.connect(mk(i))
                grid.addWidget(b, i // 2, i % 2)
            opt_box.addLayout(grid)
        elif m == "VU Metre":
            opt_hdr.setText("KADRAN")
            row = QHBoxLayout(); row.setSpacing(6)
            grp = QButtonGroup(w); grp.setExclusive(True)
            for i in range(vu_dial_count):
                b = QPushButton(f"Kadran {i+1}"); b.setCheckable(True)
                b.setChecked(state["vu_dial_idx"] == i)
                grp.addButton(b)
                def mk(idx):
                    def _f():
                        state["vu_dial_idx"] = idx; vu_cache_clear()
                    return _f
                b.clicked.connect(mk(i))
                row.addWidget(b)
            opt_box.addLayout(row)
        else:
            opt_hdr.setText("SEÇENEKLER")
            lbl = QLabel("Bu mod için ek seçenek yok.")
            lbl.setStyleSheet("color: #6b7684;")
            opt_box.addWidget(lbl)

    def on_mode_click(m):
        def _f():
            state["mode"] = m
            rebuild_options()
        return _f
    for m, b in mode_btns.items():
        b.clicked.connect(on_mode_click(m))

    rebuild_options()

    root.addWidget(_sep())
    # alt satir: Sistem Monitoru + Cikis
    bottom = QHBoxLayout(); bottom.setSpacing(8)
    smon_lbl = QLabel("SİSTEM MONİTÖRÜ"); smon_lbl.setObjectName("hdr")
    root_smon = QGridLayout(); root_smon.setSpacing(6)
    for _i, (_t, _p) in enumerate((("Sensörler", 0), ("Disk Isıları", 1), ("Çekirdek Isıları", 2))):
        _b = QPushButton(_t)
        def _mk(pg):
            def _f(): open_sysmon(pg)
            return _f
        _b.clicked.connect(_mk(_p))
        root_smon.addWidget(_b, 0, _i)
    smon_b = QPushButton("Sistem Monitörü")  # eski buton gizli uyumluluk icin
    smon_b.setVisible(False)
    smon_b.clicked.connect(lambda: open_sysmon())
    root.addWidget(smon_lbl)
    root.addLayout(root_smon)
    bottom.addWidget(smon_b)
    quit_b = QPushButton("Çıkış"); quit_b.setObjectName("quit")
    quit_b.clicked.connect(lambda: on_quit())
    bottom.addWidget(quit_b)
    root.addLayout(bottom)

    def _refresh():
        for m, b in mode_btns.items():
            b.setChecked(state["mode"] == m)
        rebuild_options()
    w._refresh = _refresh

    w.resize(420, 380)
    return w


def _hdr(text):
    from PyQt5.QtWidgets import QLabel
    lbl = QLabel(text); lbl.setObjectName("hdr")
    return lbl


def _sep():
    from PyQt5.QtWidgets import QFrame
    line = QFrame(); line.setFrameShape(QFrame.HLine)
    line.setStyleSheet("background: #232b36; max-height: 1px;")
    return line
