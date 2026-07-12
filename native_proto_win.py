#!/usr/bin/env python3
"""WINDOWS LCD: Native 1920x462 yatay cizim + trcc HTTP API gonderim.

Akis: WASAPI loopback -> ana dongu native ciz -> shared_memory -> ayri
sender process -> DOGRUDAN USB (trcc_direct) -> panel.

trcc / HTTP / PNG / disk YOK -> aninda acilir, yuksek FPS, antivirus etkisi yok.

GEREKSINIM:
  - trcc kurulu (Program Files/TRCC)
  - YONETICI olarak calistirilmali (USB + sensorler icin)
  - pip: soundcard numpy pygame PyQt5 requests pythonnet pycaw
  - sysmon_win.py + LibreHardwareMonitorLib.dll (sensorler icin)

Olculen: 37 FPS (API gonderim ~15ms) -> 30 FPS rahat.
"""
import os
import sys
import time
import json
import subprocess
import threading
import math
import numpy as np
import multiprocessing as mp
from multiprocessing import shared_memory

# --- console=False (pencere modu exe): stdout/stderr None olur -> print() patlar
#     Cozum: ciktilari LOG DOSYASINA yaz (hata ayiklama icin sart).
_LOG_PATH = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                         "trcc-user", "vumeter_lcd.log")


class _LogIO:
    """stdout/stderr yerine gecer: log dosyasina yazar (console yoksa)."""

    def __init__(self, path):
        self.path = path
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
        except Exception:
            pass
        self._f = None
        try:
            self._f = open(path, "a", encoding="utf-8", buffering=1)
        except Exception:
            self._f = None

    def write(self, s):
        if self._f:
            try:
                self._f.write(s)
            except Exception:
                pass

    def flush(self):
        if self._f:
            try:
                self._f.flush()
            except Exception:
                pass


if sys.stdout is None:
    sys.stdout = _LogIO(_LOG_PATH)
if sys.stderr is None:
    sys.stderr = sys.stdout

# exe'de calisiyorsak log basligi at
if getattr(sys, "frozen", False):
    print(f"\n===== VumeterLCD basladi: {time.strftime('%Y-%m-%d %H:%M:%S')} =====")

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # penceresiz
import pygame

# ==================== SABITLER ====================
WIDTH, HEIGHT = 1920, 462          # NATIVE YATAY (panel kendi donduruyor)
NUM_BARS = 204
API_BASE = "http://127.0.0.1:8080"
DEVICE_KEY = "0416:5408"
THEME_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "trcc-user", "live_frame")
FPS = 25                            # post ~6ms olculdu -> 25 FPS rahat
                                    # (1920x462 kare = 1.8MB, USB transfer ~70ms)
                                    # 12 FPS = kuyruk birikmez, donma olmaz

# --- WASAPI loopback ses ayarlari (vumeter_win.py ile ayni) ---
SAMPLE_RATE = 48000
BLOCK = 8192            # FFT penceresi (bas barlari ayrisir)
GAIN = 2.0              # bar yuksekligi
SCALE = 52.0
TAU_SPEC = 0.03         # FFT gurultu yumusatma (sn)
TAU_UP   = 0.09         # yukselme suresi (sn)
FALL_PS  = 165.0        # dusme hizi (birim/sn)

COLOR_THEMES = {
    "Klasik":    [(0, 170, 0), (255, 255, 0), (255, 0, 0)],
    "Neon":      [(0, 220, 220), (140, 80, 240), (240, 40, 180)],
    "Alev":      [(255, 220, 40), (255, 100, 0), (200, 30, 20)],
    "Camgobegi": [(0, 40, 80), (0, 200, 210), (200, 255, 255)],
}
COLOR_THEME_NAMES = list(COLOR_THEMES.keys())
_HALF_N = NUM_BARS // 2

_state = {"theme_idx": 0, "running": True, "ch_layout": 3,   # Windows LCD: 3 uygun
          "mode": "Spektrum", "led_theme_idx": 0, "vu_dial_idx": 0, "meter_page": 0,
          "brightness": 100, "brightness_changed": False, "last_sound": 0.0}

LED_THEMES = {
    "Camgobegi": [(10, 90, 70), (40, 200, 190), (120, 255, 235)],
    "Yesil-Sari-Kirmizi": [(40, 220, 40), (250, 200, 30), (235, 45, 30)],
    "Mavi": [(20, 60, 200), (60, 140, 255), (170, 220, 255)],
    "Mor": [(120, 0, 200), (210, 60, 230), (255, 210, 255)],
}
LED_THEME_NAMES = list(LED_THEMES.keys())
_led_texture_cache = {"surf": None, "width": None, "palette": None, "style": None}
_led_smooth = np.zeros(NUM_BARS)
_led_peak = np.zeros(NUM_BARS)
_led_ptimers = np.zeros(NUM_BARS)


def _lerp(c1, c2, f):
    return (int(c1[0]+(c2[0]-c1[0])*f), int(c1[1]+(c2[1]-c1[1])*f), int(c1[2]+(c2[2]-c1[2])*f))


def _get_led_texture(led_bar_width, max_h, palette_name, style="rect"):
    cache = _led_texture_cache
    if (cache["surf"] is not None and cache["width"] == led_bar_width
            and cache["palette"] == palette_name and cache["style"] == style):
        return cache["surf"]
    stops = LED_THEMES.get(palette_name, LED_THEMES["Camgobegi"])
    LED_SEG_H = 16; LED_SEG_GAP = 4
    step = LED_SEG_H + LED_SEG_GAP
    tex = pygame.Surface((led_bar_width, max_h))
    tex.fill((0, 0, 0))
    y_off = 0
    while y_off < max_h:
        ratio = y_off / max_h
        if ratio < 0.5:
            color = _lerp(stops[0], stops[1], ratio / 0.5)
        else:
            color = _lerp(stops[1], stops[2], (ratio - 0.5) / 0.5)
        color = (min(255,max(0,color[0])), min(255,max(0,color[1])), min(255,max(0,color[2])))
        y_top = max_h - y_off - LED_SEG_H
        if style == "dot":
            cx = led_bar_width // 2; cy = max(0, y_top) + LED_SEG_H // 2
            r = max(2, min(led_bar_width, LED_SEG_H) // 2)
            pygame.draw.circle(tex, color, (cx, cy), r)
        else:
            pygame.draw.rect(tex, color, (0, max(0, y_top), led_bar_width, LED_SEG_H))
        y_off += step
    cache["surf"] = tex; cache["width"] = led_bar_width
    cache["palette"] = palette_name; cache["style"] = style
    return tex


def _apply_layout(cava_bars):
    """Kanal dizilimi (varyant 1 = L + R[::-1])."""
    L = cava_bars[:_HALF_N]; R = cava_bars[_HALF_N:_HALF_N*2]
    lay = _state.get("ch_layout", 1)
    if lay == 0:   return L + R
    elif lay == 1: return L + R[::-1]
    elif lay == 2: return L[::-1] + R
    else:          return L[::-1] + R[::-1]


# ==================== OLCUM PANELI (native yatay 2x2) ====================
_meter_smooth = {"rms_l": 0.0, "rms_r": 0.0, "peak": 0.0, "peak_l": 0.0, "peak_r": 0.0,
                 "freq_l": 0.0, "freq_r": 0.0, "centroid": 0.0, "bass": 0.0,
                 "bal_l": 0.0, "bal_r": 0.0, "bal_pct": 50.0}
_meter_fonts = {}


def _meter_font(size, bold=True):
    key = (size, bold)
    if key not in _meter_fonts:
        _meter_fonts[key] = pygame.font.SysFont("DejaVu Sans", size, bold=bold)
    return _meter_fonts[key]


def _bar_to_hz(bar_index, total_bars):
    if total_bars <= 1:
        return 0.0
    f_min, f_max = 50.0, 16000.0
    t = bar_index / (total_bars - 1)
    return f_min * (f_max / f_min) ** t


def draw_meter_panel(surf, cava_bars):
    """NATIVE yatay Olcum Paneli: 2x2 grid, ses analizi (2 sayfa)."""
    surf.fill((8, 10, 8))
    n = len(cava_bars)
    left = np.array(cava_bars[:_HALF_N], dtype=float) if n >= _HALF_N else np.zeros(_HALF_N)
    right = np.array(cava_bars[_HALF_N:_HALF_N*2], dtype=float) if n >= _HALF_N*2 else np.zeros(_HALF_N)

    rms_l = float(np.sqrt(np.mean(left**2))) / 255.0 if left.size else 0.0
    rms_r = float(np.sqrt(np.mean(right**2))) / 255.0 if right.size else 0.0
    peak_l = (float(left.max()) / 255.0) if left.size else 0.0
    peak_r = (float(right.max()) / 255.0) if right.size else 0.0
    peak = max(peak_l, peak_r)
    if left.size and right.size:
        _all = left + right; _tot = float(_all.sum())
        _bass = float(_all[:max(1, _HALF_N // 4)].sum())
        bass_ratio = (_bass / _tot) if _tot > 1 else 0.0
    else:
        bass_ratio = 0.0
    eng_l = float(left.sum()) if left.size else 0.0
    eng_r = float(right.sum()) if right.size else 0.0
    eng_max = max(eng_l, eng_r, 1.0)
    bal_l = eng_l / eng_max; bal_r = eng_r / eng_max
    bal_pct = (eng_r / (eng_l + eng_r) * 100) if (eng_l + eng_r) > 1 else 50.0
    freq_l = _bar_to_hz(int(np.argmax(left)), _HALF_N) if left.size and left.max() > 5 else 0.0
    freq_r = _bar_to_hz(int(np.argmax(right)), _HALF_N) if right.size and right.max() > 5 else 0.0
    allb = (left + right)
    if allb.sum() > 1:
        idx = np.arange(_HALF_N)
        centroid = _bar_to_hz(float((idx * allb).sum() / allb.sum()), _HALF_N)
    else:
        centroid = 0.0

    s = _meter_smooth; a = 0.3
    for k, v in (("rms_l", rms_l), ("rms_r", rms_r), ("peak", peak),
                 ("peak_l", peak_l), ("peak_r", peak_r), ("bass", bass_ratio),
                 ("bal_l", bal_l), ("bal_r", bal_r), ("bal_pct", bal_pct),
                 ("freq_l", freq_l), ("freq_r", freq_r), ("centroid", centroid)):
        s[k] += (v - s[k]) * a

    GREEN = (60, 230, 90); GREY = (70, 75, 70); DARK = (18, 22, 18)
    LBL = (0, 210, 210); TITLE = (0, 210, 210)

    def to_db(v):
        if v <= 0.0001: return -60.0
        return max(-60.0, 20.0 * math.log10(v))

    page = _state.get("meter_page", 0)
    if page == 0:
        panels = [
            ("RMS SEVIYE", [("Ch1", s["rms_l"], f"{to_db(s['rms_l']):.1f}", "dB"),
                            ("Ch2", s["rms_r"], f"{to_db(s['rms_r']):.1f}", "dB")]),
            ("FREKANS", [("Ch1", min(1.0, s["freq_l"]/16000), f"{s['freq_l']/1000:.2f}", "kHz"),
                         ("Ch2", min(1.0, s["freq_r"]/16000), f"{s['freq_r']/1000:.2f}", "kHz")]),
            ("STEREO DENGE", [("Sol", s["bal_l"], f"{100-s['bal_pct']:.0f}", "%"),
                              ("Sag", s["bal_r"], f"{s['bal_pct']:.0f}", "%")]),
            ("MERKEZ", [("Frk", min(1.0, s["centroid"]/16000), f"{s['centroid']/1000:.2f}", "kHz"),
                        ("Bas", s["bass"], f"{s['bass']*100:.0f}", "%")]),
        ]
    else:
        dr_l = max(0.0, to_db(s["peak_l"]) - to_db(s["rms_l"]))
        dr_r = max(0.0, to_db(s["peak_r"]) - to_db(s["rms_r"]))
        crest_l = (s["peak_l"] / s["rms_l"]) if s["rms_l"] > 0.001 else 0.0
        crest_r = (s["peak_r"] / s["rms_r"]) if s["rms_r"] > 0.001 else 0.0
        _el = float(left.sum()); _er = float(right.sum())
        width = abs(_el - _er) / (_el + _er) if (_el + _er) > 1 else 0.0
        if left.size and right.size:
            _allb2 = left + right; _tot2 = float(_allb2.sum())
            _treble = float(_allb2[_HALF_N*3//4:].sum())
            treble_ratio = (_treble / _tot2) if _tot2 > 1 else 0.0
        else:
            treble_ratio = 0.0
        for k, v in (("dr", (dr_l+dr_r)/2), ("crest", (crest_l+crest_r)/2),
                     ("width", width), ("treble", treble_ratio)):
            s.setdefault(k, 0.0); s[k] += (v - s[k]) * a
        panels = [
            ("DINAMIK", [("Arl", min(1.0, s["dr"]/40), f"{s['dr']:.1f}", "dB"),
                         ("Crs", min(1.0, s["crest"]/4), f"{s['crest']:.2f}", "x")]),
            ("STEREO", [("Gen", min(1.0, s["width"]), f"{s['width']*100:.0f}", "%"),
                        ("Bal", s["bal_r"], f"{s['bal_pct']:.0f}", "%")]),
            ("ENERJI", [("Tiz", min(1.0, s["treble"]*3), f"{s['treble']*100:.0f}", "%"),
                        ("Bas", s["bass"], f"{s['bass']*100:.0f}", "%")]),
            ("TEPE", [("Ch1", s["peak_l"], f"{to_db(s['peak_l']):.1f}", "dB"),
                      ("Ch2", s["peak_r"], f"{to_db(s['peak_r']):.1f}", "dB")]),
        ]

    # NATIVE 2x2 grid
    pw = WIDTH // 2; ph = HEIGHT // 2
    tfont = _meter_font(30); lblf = _meter_font(26)
    numf = _meter_font(38); unitf = _meter_font(20)
    for pi, (title, rows) in enumerate(panels):
        gx = (pi % 2) * pw; gy = (pi // 2) * ph
        ts = tfont.render(title, True, TITLE)
        surf.blit(ts, (gx + 20, gy + 8))
        for ri, (label, val, num, unit) in enumerate(rows):
            ry = gy + 50 + ri * ((ph - 55) // 2)
            cy = ry + ((ph - 55) // 2) // 2
            ls = lblf.render(label, True, LBL)
            surf.blit(ls, (gx + 20, cy - ls.get_height()//2))
            bar_x = gx + 90
            num_area = 150
            bar_w_full = pw - 90 - num_area
            bar_h = 24
            bar_y = cy - bar_h // 2
            v = max(0.0, min(1.0, val))
            pygame.draw.rect(surf, DARK, (bar_x, bar_y, bar_w_full, bar_h))
            fill_w = int(bar_w_full * v)
            pygame.draw.rect(surf, GREEN, (bar_x, bar_y, fill_w, bar_h))
            pygame.draw.rect(surf, GREY, (bar_x, bar_y, bar_w_full, bar_h), 1)
            ns = numf.render(num, True, GREEN)
            surf.blit(ns, (gx + pw - num_area + 10, cy - ns.get_height()//2))
            us = unitf.render(unit, True, GREEN)
            surf.blit(us, (gx + pw - 40, cy - us.get_height()//2 + 4))


# ==================== SISTEM MONITORU (native yatay, Mac tarzi) ====================
try:
    import sysmon_win as _sysmon_mod   # Windows: LibreHardwareMonitor
except Exception:
    _sysmon_mod = None
_sysmon_instance = None
_sm_font_cache = {}
from collections import deque as _deque
_sm_history = {}   # etiket -> deque(son ~60 kare frac degeri)  (12sn @ 5fps)
_SM_HIST_LEN = 60


def _sm_grad_rgb(t):
    """0..1 -> yesil->sari->kirmizi gecis (grafik icin)."""
    t = max(0.0, min(1.0, t))
    if t < 0.5:
        f = t / 0.5
        return (int(58 + (242-58)*f), int(212 - (212-201)*f), int(110 - (110-76)*f))
    else:
        f = (t - 0.5) / 0.5
        return (int(242 + (235-242)*f), int(201 - (201-60)*f), int(76 - (76-40)*f))


def _get_sysmon():
    global _sysmon_instance
    if _sysmon_instance is None and _sysmon_mod is not None:
        try:
            _sysmon_instance = _sysmon_mod.SysMonitor()
        except Exception:
            pass
    return _sysmon_instance


def _sm_font(size, bold=True):
    key = (size, bold)
    if key not in _sm_font_cache:
        _sm_font_cache[key] = pygame.font.SysFont("DejaVu Sans", size, bold=bold)
    return _sm_font_cache[key]


def draw_sysmon(surf, fps):
    """NATIVE yatay sistem monitoru (Mac tarzi: dikey cubuklar yan yana)."""
    surf.fill((8, 10, 8))
    mon = _get_sysmon()
    if mon is None:
        f = _sm_font(40)
        surf.blit(f.render("Sensor okunamadi", True, (200, 80, 80)), (60, HEIGHT//2 - 20))
        return
    d = mon.snapshot()
    GREEN = (60, 230, 90); DARK = (20, 24, 20); GREY = (60, 66, 60)
    WHITE = (80, 220, 215); DIM = (90, 175, 175)

    def temp_color(t):
        if t is None: return GREY
        if t < 55: return (60, 230, 90)
        if t < 70: return (245, 210, 60)
        return (235, 60, 40)

    cpu_t = d.get("cpu_pkg"); cores = d.get("cores_max")
    gpu_j = d.get("gpu_junction") or d.get("gpu_temp"); gpu_e = d.get("gpu_edge"); gpu_m = d.get("gpu_mem")
    vrm = d.get("mb_vrm"); pch = d.get("mb_pch"); mbsys = d.get("mb_system")
    use = d.get("cpu_usage"); gpu_u = d.get("gpu_usage")
    ram = d.get("ram_pct"); frq = d.get("cpu_freq")
    cpu_p = d.get("cpu_power"); gpu_p = d.get("gpu_power")
    vram_u = d.get("gpu_vram_used"); vram_t = d.get("gpu_vram_total")
    cfan = d.get("fan_cpu"); pump = d.get("fan_pump"); gfan = d.get("gpu_fan_rpm")
    s1 = d.get("fan_sys1"); s2 = d.get("fan_sys2"); s3 = d.get("fan_sys3")
    s4 = d.get("fan_sys4"); s5 = d.get("fan_sys5"); s6 = d.get("fan_sys6")
    nd = d.get("net_down"); nu = d.get("net_up")

    def net_fmt(mbps):
        if mbps is None: return ("--", "kB/s", 0)
        if mbps < 1.0: return (f"{mbps*1024:.0f}", "kB/s", mbps/100.0)
        return (f"{mbps:.2f}", "MB/s", mbps/100.0)
    nd_txt, nd_unit, nd_frac = net_fmt(nd)
    nu_txt, nu_unit, nu_frac = net_fmt(nu)

    def col(t): return temp_color(t)
    vram_frac = (vram_u/vram_t) if (vram_u and vram_t) else 0
    # SATIR 1 (13): sicakliklar + AKTIF fanlar (birbiriyle ilgili: isi + sogutma)
    bars_top = [
        ("CPU",  f"{cpu_t:.0f}"  if cpu_t is not None else "--", "C",  (cpu_t/100.0)  if cpu_t else 0, col(cpu_t)),
        ("Çkrdk",f"{cores:.0f}"  if cores is not None else "--", "C",  (cores/100.0)  if cores else 0, col(cores)),
        ("GPU",  f"{gpu_j:.0f}"  if gpu_j is not None else "--", "C",  (gpu_j/110.0)  if gpu_j else 0, col(gpu_j)),
        ("GEdge",f"{gpu_e:.0f}"  if gpu_e is not None else "--", "C",  (gpu_e/100.0)  if gpu_e else 0, col(gpu_e)),
        ("GMem", f"{gpu_m:.0f}"  if gpu_m is not None else "--", "C",  (gpu_m/100.0)  if gpu_m else 0, col(gpu_m)),
        ("VRM",  f"{vrm:.0f}"    if vrm is not None else "--",   "C",  (vrm/100.0)    if vrm else 0,   col(vrm)),
        ("PCH",  f"{pch:.0f}"    if pch is not None else "--",   "C",  (pch/90.0)     if pch else 0,   col(pch)),
        ("Sys",  f"{mbsys:.0f}"  if mbsys is not None else "--", "C",  (mbsys/90.0)   if mbsys else 0, col(mbsys)),
        ("CFan", f"{cfan:.0f}"   if cfan else "0",               "",   (cfan/3000.0)  if cfan else 0, GREEN),
        ("Pump", f"{pump:.0f}"   if pump else "0",               "",   (pump/3000.0)  if pump else 0, GREEN),
        ("GFan", f"{gfan:.0f}"   if gfan else "0",               "",   (gfan/3000.0)  if gfan else 0, GREEN),
        ("S1",   f"{s1:.0f}"     if s1 else "0",                 "",   (s1/3000.0)    if s1 else 0, GREEN),
        ("GHz",  f"{frq/1000:.1f}" if frq else "--",             "",   (frq/5700.0)   if frq else 0, GREEN),
    ]
    # SATIR 2 (13): kullanim + guc + pasif fanlar + ag
    bars_bot = [
        ("CPU%", f"{use:.0f}"    if use is not None else "--",   "%",  (use/100.0)    if use is not None else 0, GREEN),
        ("GPU%", f"{gpu_u:.0f}"  if gpu_u is not None else "--", "%",  (gpu_u/100.0)  if gpu_u is not None else 0, GREEN),
        ("RAM",  f"{ram:.0f}"    if ram is not None else "--",   "%",  (ram/100.0)    if ram is not None else 0, GREEN),
        ("VRAM", f"{vram_u:.1f}" if vram_u is not None else "--","G",  vram_frac, GREEN),
        ("C-W",  f"{cpu_p:.0f}"  if cpu_p is not None else "--", "W",  (cpu_p/250.0)  if cpu_p else 0, GREEN),
        ("G-W",  f"{gpu_p:.0f}"  if gpu_p is not None else "--", "W",  (gpu_p/350.0)  if gpu_p else 0, GREEN),
        ("S2",   f"{s2:.0f}"     if s2 else "0",                 "",   (s2/3000.0)    if s2 else 0, GREEN),
        ("S3",   f"{s3:.0f}"     if s3 else "0",                 "",   (s3/3000.0)    if s3 else 0, GREEN),
        ("S4",   f"{s4:.0f}"     if s4 else "0",                 "",   (s4/3000.0)    if s4 else 0, GREEN),
        ("S5",   f"{s5:.0f}"     if s5 else "0",                 "",   (s5/3000.0)    if s5 else 0, GREEN),
        ("S6",   f"{s6:.0f}"     if s6 else "0",                 "",   (s6/3000.0)    if s6 else 0, GREEN),
        ("Indir",nd_txt, nd_unit, nd_frac, GREEN),
        ("Yukle",nu_txt, nu_unit, nu_frac, GREEN),
    ]
    margin = 16
    vfont = _sm_font(30); ufont = _sm_font(15); lfont = _sm_font(17)

    def _arc_dots(cx, cy, radius, deg_start, deg_end, width, color_fn):
        """Yayi SIK DOLU DAIRELERLE ciz -> kenarlar puruzsuz/yuvarlak (tirtik yok).
        color_fn(t) -> o konumun rengi (t: 0..1 yay boyunca)."""
        if deg_end <= deg_start:
            return
        r = max(2, width // 2)
        # adim: daire caplari ust uste binsin (puruzsuz)
        arc_len = math.radians(deg_end - deg_start) * radius
        steps = max(3, int(arc_len / max(1, r * 0.55)))
        for i in range(steps + 1):
            f = i / steps
            deg = deg_start + (deg_end - deg_start) * f
            a = math.radians(deg)
            x = cx + radius * math.cos(a)
            y = cy + radius * math.sin(a)
            col = color_fn((deg - 150) / 240.0)
            pygame.draw.circle(surf, col, (int(x), int(y)), r)

    def draw_card_gauge(cx, cy, radius, frac, base_color):
        """Dairesel ilerleme halkasi (240 derece, alttan acik).
        Halka BOYUNCA gradient (yesil->sari->kirmizi), kenarlar puruzsuz."""
        frac = max(0.0, min(1.0, frac))
        # arka halka (bos, koyu) - tek renk
        _arc_dots(cx, cy, radius, 150, 390, 11, lambda t: (36, 45, 58))
        if frac <= 0.005:
            return
        # dolu kisim: konuma gore gradient renk
        end_deg = 150 + 240 * frac
        _arc_dots(cx, cy, radius, 150, end_deg, 11, _sm_grad_rgb)

    def draw_row(bars, row_top, row_bottom):
        n = len(bars)
        gap = 8
        card_w = (WIDTH - 2*margin - (n-1)*gap) // n
        card_x0 = margin
        card_top = row_top + 6
        card_h = (row_bottom - row_top) - 12
        # Font boyutu EN DAR satira (max kart) gore sabit -> ust/alt ayni buyuklukte
        _max_n = 13
        _ref_w = (WIDTH - 2*margin - (_max_n-1)*gap) // _max_n
        cardf = _sm_font(int(_ref_w * 0.32))   # (kullanilmiyor - gauge fontu asagida)
        # Gauge ici rakam: TUM kartlarda ayni boyut. En uzun deger "3267" (4 hane)
        # dar karta sigacak sekilde bir kez hesaplanir.
        _gr = int(min(_ref_w, (row_bottom - row_top) - 16) * 0.42)
        _gsize = int(_ref_w * 0.33)
        _gauge_vfont = _sm_font(_gsize)
        while _gauge_vfont.size("3267")[0] > int(_gr * 1.55) and _gsize > 10:
            _gsize -= 1
            _gauge_vfont = _sm_font(_gsize)
        unitf2 = _sm_font(max(14, int(_ref_w * 0.16)), bold=False)
        lblf2 = _sm_font(max(14, int(_ref_w * 0.17)))
        for idx, (lbl, vtxt, unit, frac, color) in enumerate(bars):
            frac = max(0.0, min(1.0, frac))
            cx0 = card_x0 + idx * (card_w + gap)
            ccx = cx0 + card_w // 2
            # YUMUSAK renk: frac'a gore surekli yesil->sari->kirmizi gecis
            gcol = _sm_grad_rgb(frac)
            # kart arka plani
            pygame.draw.rect(surf, (22, 27, 34), (cx0, card_top, card_w, card_h), border_radius=12)
            pygame.draw.rect(surf, (35, 43, 54), (cx0, card_top, card_w, card_h), 1, border_radius=12)
            # DAIRESEL HALKA (frac kadar dolu, yumusak renk)
            gauge_cy = card_top + int(card_h * 0.44)
            gauge_r = int(min(card_w, card_h) * 0.47)   # YAY genis + kalin
            draw_card_gauge(ccx, gauge_cy, gauge_r, frac, gcol)
            # rakam - TUM kartlarda AYNI boyut (en uzun "3267"e gore sabit)
            vs = _gauge_vfont.render(vtxt, True, gcol)
            surf.blit(vs, (ccx - vs.get_width()//2, gauge_cy - vs.get_height()//2))
            # birim (halka altinda kucuk)
            if unit:
                us = unitf2.render(unit, True, (170, 182, 196))
                surf.blit(us, (ccx - us.get_width()//2, card_top + int(card_h*0.72)))
            # etiket (en altta)
            ls = lblf2.render(lbl, True, (210, 220, 232))
            surf.blit(ls, (ccx - ls.get_width()//2, card_top + int(card_h*0.86)))

    half = HEIGHT // 2
    draw_row(bars_top, 0, half)
    draw_row(bars_bot, half, HEIGHT)


# ==================== VU METRE (native yatay, 3 kadran) ====================
# (dosya, pivot_x, pivot_y, ibre_RGB, orig_boyut, alt_kirpma, olcek)
VU_DIALS = [
    ("vu_bg.png",  0.500, 0.820, (210, 30, 30), (2366, 1792), 0.0,  1.0),
    ("vu_bg2.png", 0.503, 0.867, (20, 20, 20),  (2624, 1620), 0.0,  1.15),
    ("vu_bg3.png", 0.503, 0.655, (20, 20, 20),  (2400, 1790), 0.10, 1.18),
]
_vu_dial_cache = {}
_vu_scaled_cache = {}
_vu_angles = {"l": 180.0, "r": 180.0}
_VU_DIR = os.path.dirname(os.path.abspath(__file__))


def _load_vu_dial(idx):
    if idx in _vu_dial_cache:
        return _vu_dial_cache[idx]
    fname = VU_DIALS[idx][0]
    crop_b = VU_DIALS[idx][5] if len(VU_DIALS[idx]) > 5 else 0.0
    path = os.path.join(_VU_DIR, fname)
    img = None
    if os.path.isfile(path):
        try:
            img = pygame.image.load(path)
            if crop_b > 0:
                w, h = img.get_size()
                new_h = int(h * (1 - crop_b))
                cropped = pygame.Surface((w, new_h))
                cropped.blit(img, (0, 0), area=pygame.Rect(0, 0, w, new_h))
                img = cropped
        except Exception:
            img = None
    _vu_dial_cache[idx] = img
    return img


def _make_one_vu_native(disp_w, current_angle):
    """NATIVE: kadran + ibre, rotate YOK (yatay dogrudan)."""
    idx = _state["vu_dial_idx"]
    img0 = _load_vu_dial(idx)
    if img0 is None:
        return None
    _fname, pvx, pvy, needle_col, _orig = VU_DIALS[idx][:5]
    crop_b = VU_DIALS[idx][5] if len(VU_DIALS[idx]) > 5 else 0.0
    scale = VU_DIALS[idx][6] if len(VU_DIALS[idx]) > 6 else 1.0
    disp_w = int(disp_w * scale)
    if crop_b > 0:
        pvy = pvy / (1 - crop_b)
    ow, oh = img0.get_size()
    disp_h = int(disp_w * oh / ow)
    key = (idx, disp_w, disp_h)
    if key not in _vu_scaled_cache:
        _vu_scaled_cache[key] = pygame.transform.smoothscale(img0, (disp_w, disp_h))
    img = _vu_scaled_cache[key]
    tmp = pygame.Surface((disp_w, disp_h))
    tmp.fill((0, 0, 0))
    tmp.blit(img, (0, 0))
    piv_x = int(disp_w * pvx)
    piv_y = int(disp_h * pvy)
    if not math.isfinite(current_angle):
        current_angle = 180.0
    ang = 137 - (180 - current_angle) / 180.0 * 101
    rad = math.radians(ang)
    needle_len = int(disp_w * 0.37)
    nx = piv_x + needle_len * math.cos(rad)
    ny = piv_y - needle_len * math.sin(rad)
    pygame.draw.line(tmp, needle_col, (piv_x, piv_y), (int(nx), int(ny)), 4)
    pygame.draw.circle(tmp, needle_col, (piv_x, piv_y), 8)
    return tmp   # rotate YOK


def draw_vu_meter(surf, cava_bars):
    """NATIVE yatay VU: iki gosterge yan yana (Mac gibi)."""
    surf.fill((0, 0, 0))
    n = len(cava_bars)
    left_slice = cava_bars[:_HALF_N] if n >= _HALF_N else cava_bars
    right_slice = cava_bars[_HALF_N:] if n >= _HALF_N*2 else cava_bars
    def vu_level(sl):
        if not len(sl): return 0.0
        arr = np.asarray(sl, dtype=float)
        peak = float(arr.max()); mean = float(arr.mean())
        v = (peak * 0.45 + mean * 0.55) / 255.0
        return min(1.0, v * 0.97)
    vol_l = vu_level(left_slice); vol_r = vu_level(right_slice)
    if not math.isfinite(vol_l): vol_l = 0.0
    if not math.isfinite(vol_r): vol_r = 0.0
    tgt_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    tgt_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    _vu_angles["l"] += (tgt_l - _vu_angles["l"]) * 0.9
    _vu_angles["r"] += (tgt_r - _vu_angles["r"]) * 0.9

    # NATIVE: gosterge yuksekligi ekrana (462) sigsin
    disp_h_target = int(HEIGHT * 0.95)
    idx = _state["vu_dial_idx"]
    ow, oh = VU_DIALS[idx][4]
    crop_b = VU_DIALS[idx][5] if len(VU_DIALS[idx]) > 5 else 0.0
    if crop_b > 0:
        oh = int(oh * (1 - crop_b))
    disp_w = int(disp_h_target * ow / oh)

    vu_l = _make_one_vu_native(disp_w, _vu_angles["l"])
    vu_r = _make_one_vu_native(disp_w, _vu_angles["r"])
    if vu_l is None:
        # gorsel yoksa uyari
        f = pygame.font.SysFont("DejaVu Sans", 40, bold=True)
        surf.blit(f.render("vu_bg.png bulunamadi", True, (200,80,80)), (60, HEIGHT//2-20))
        return
    rw, rh = vu_l.get_size()
    y = (HEIGHT - rh) // 2
    cx1 = int(WIDTH * 0.25); cx2 = int(WIDTH * 0.75)
    surf.blit(vu_l, (cx1 - rw//2, y))
    surf.blit(vu_r, (cx2 - rw//2, y))


def draw_led_spectrum(surf, cava_bars, fps, led_style="rect"):
    """NATIVE yatay LED spektrum (Mac gibi)."""
    surf.fill((0, 0, 0))
    bars = _apply_layout(cava_bars)
    LED_BAR_GAP = 10; MERGE = 1
    num_bars = NUM_BARS
    base_w = max(2, (WIDTH - 2*20 - 40) // num_bars - LED_BAR_GAP)
    merged_w = base_w
    palette = LED_THEME_NAMES[_state["led_theme_idx"]]
    max_h = HEIGHT
    texture = _get_led_texture(merged_w, max_h, palette, led_style)
    for i in range(num_bars):
        v = bars[i] if i < len(bars) else 0
        target = int((v / 255.0) * max_h * 0.62)
        _led_smooth[i] += (target - _led_smooth[i]) * 0.2
        hj = min(int(_led_smooth[i]), max_h)
        if hj >= _led_peak[i]:
            _led_peak[i] = hj; _led_ptimers[i] = fps * 0.7
        else:
            if _led_ptimers[i] > 0: _led_ptimers[i] -= 1
            else: _led_peak[i] = max(0, _led_peak[i] - 3)
        if i < _HALF_N:
            x = 20 + i * (merged_w + LED_BAR_GAP)
        else:
            x = _RIGHT_START + (i - _HALF_N) * (merged_w + LED_BAR_GAP)
        if hj > 1:
            src = pygame.Rect(0, max_h - hj, merged_w, hj)
            surf.blit(texture, (x, HEIGHT - hj), area=src)
        pk = int(_led_peak[i])
        if pk > 2:
            pygame.draw.rect(surf, (180, 255, 245), (x, HEIGHT - pk, merged_w, 3))


def gradient_color(theme_name, ratio):
    palette = COLOR_THEMES.get(theme_name, COLOR_THEMES["Klasik"])
    if ratio <= 0.5:
        c1, c2 = palette[0], palette[1]; f = ratio * 2
    else:
        c1, c2 = palette[1], palette[2]; f = (ratio - 0.5) * 2
    return (int(c1[0]+(c2[0]-c1[0])*f), int(c1[1]+(c2[1]-c1[1])*f), int(c1[2]+(c2[2]-c1[2])*f))


# ==================== KAYNAK BULUCU ====================
# ==================== SES: WASAPI LOOPBACK (Windows) ====================
# ONEMLI: soundcard'i BURADA import ETMIYORUZ! Import edildiginde ana thread'de
# COM'u MTA modunda baslatiyor; Qt ise tepsi ikonu icin STA istiyor -> catisma
# ("OleInitialize failed: RPC_E_CHANGED_MODE") -> tray ikonu OLU kalir.
# Cozum: soundcard SADECE ses thread'i icinde import edilir (asagida).
sc = None   # ses thread'inde doldurulur

_HALF = NUM_BARS // 2
_freq_bins = np.fft.rfftfreq(BLOCK, 1.0 / SAMPLE_RATE)
_edges = np.logspace(np.log10(40.0), np.log10(16000.0), _HALF + 1)
_BM = np.zeros((_HALF, len(_freq_bins)), dtype=np.float32)
for _i in range(_HALF):
    _m = (_freq_bins >= _edges[_i]) & (_freq_bins < _edges[_i + 1])
    if not _m.any():
        _n = int(np.argmin(np.abs(_freq_bins - _edges[_i])))
        _m = np.zeros_like(_freq_bins, dtype=bool)
        _m[_n] = True
    _BM[_i, _m] = 1.0 / _m.sum()
_window = np.hanning(BLOCK).astype(np.float32)
_tilt = np.linspace(1.0, 2.4, _HALF).astype(np.float32)


def _default_speaker_name():
    if sc is None:
        return None
    try:
        return sc.default_speaker().name
    except Exception:
        return None


class LoopbackReader:
    """WASAPI loopback: bilgisayarda CALAN sesi yakalar (cava'nin yerini alir).
    Ayni arayuz: snapshot() -> NUM_BARS deger (0..255). Cikis aygiti otomatik
    secilir, degisirse otomatik gecer."""

    def __init__(self):
        self.bars = [0] * NUM_BARS
        self.updates = 0
        self._sm = np.zeros(NUM_BARS, dtype=np.float32)
        self._raw = np.zeros(NUM_BARS, dtype=np.float32)
        self._last_t = time.perf_counter()
        self._lock = threading.Lock()
        self._running = True
        self._dev_name = None
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _bars_from_ring(self, ring):
        win = ring * _window[:, None]
        spec = np.abs(np.fft.rfft(win, axis=0)).astype(np.float32)
        vals = (_BM @ spec) * _tilt[:, None]
        return np.concatenate([vals[:, 0], vals[:, 1]]).astype(np.float32)

    def _loop(self):
        # Windows: COM'u BU thread'de baslat + soundcard'i BURADA import et
        # (ana thread temiz kalsin ki Qt tepsi ikonu STA alabilsin)
        global sc
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)
        except Exception as e:
            print("COM init uyarisi:", e)
        try:
            import soundcard as _sc
            sc = _sc
        except Exception as e:
            print("soundcard yuklenemedi:", e)
            return

        while self._running:
            name = _default_speaker_name()
            if name is None:
                time.sleep(1.0)
                continue
            self._dev_name = name
            try:
                mic = sc.get_microphone(name, include_loopback=True)
                with mic.recorder(samplerate=SAMPLE_RATE, channels=2, blocksize=512) as rec:
                    print(f"Ses kaynagi: {name} (WASAPI loopback)")
                    ring = np.zeros((BLOCK, 2), dtype=np.float32)
                    CHUNK = 480
                    last_check = time.time()
                    while self._running:
                        d = rec.record(numframes=CHUNK)
                        if d is None or len(d) == 0:
                            continue
                        d = np.asarray(d, dtype=np.float32)
                        if d.ndim == 1:
                            d = np.stack([d, d], axis=1)
                        d = d[:, :2]
                        k = min(len(d), BLOCK)
                        ring = np.roll(ring, -k, axis=0)
                        ring[-k:] = d[-k:]

                        raw = self._bars_from_ring(ring)

                        # ZAMAN TABANLI yumusatma (kume kume gelse de duzgun)
                        now = time.perf_counter()
                        dt = min(max(now - self._last_t, 0.001), 0.1)
                        self._last_t = now
                        a_spec = 1.0 - math.exp(-dt / TAU_SPEC)
                        self._raw += (raw - self._raw) * a_spec
                        vals = np.clip(np.log1p(self._raw * GAIN) * SCALE, 0, 255)
                        a_up = 1.0 - math.exp(-dt / TAU_UP)
                        fall = FALL_PS * dt
                        rise = vals > self._sm
                        self._sm[rise] += (vals[rise] - self._sm[rise]) * a_up
                        self._sm[~rise] = np.maximum(vals[~rise], self._sm[~rise] - fall)

                        with self._lock:
                            self.bars = [int(v) for v in self._sm]
                        self.updates += 1

                        # aygit degisti mi (5 sn'de bir)
                        if time.time() - last_check > 5.0:
                            last_check = time.time()
                            cur = _default_speaker_name()
                            if cur and cur != self._dev_name:
                                print(f"Ses aygiti degisti -> {cur}")
                                break
            except Exception as e:
                print(f"Ses akisi hatasi: {e}; 2sn sonra yeniden...")
                with self._lock:
                    self.bars = [0] * NUM_BARS
                self._sm[:] = 0
                self._raw[:] = 0
                time.sleep(2.0)

    def snapshot(self):
        with self._lock:
            return list(self.bars)

    def stop(self):
        self._running = False


# Linux'taki isimlerle uyumluluk (kodun geri kalani degismesin)
CavaReader = LoopbackReader


def wait_for_cava_data(cava, timeout=8):
    """Ses okuyucu gercekten veri uretene kadar bekle."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        snap = cava.snapshot()
        if snap and max(snap) > 2:
            print("ses verisi geldi, render basliyor.")
            return True
        time.sleep(0.25)
    print("ses beklemesi zaman asimi (sessizlik olabilir - yine de baslaniyor).")
    return False


def wait_until_ready(source_timeout=90, settle_timeout=25):
    """Windows'ta ses kaynagi beklemeye gerek yok (WASAPI hep hazir)."""
    return True


# ==================== SPEKTRUM YERLESIMI (Linux'tan) ====================
_spec_smooth = np.zeros(NUM_BARS)
_spec_peak = np.zeros(NUM_BARS)
_spec_ptimers = np.zeros(NUM_BARS)

# Yatay yerlesim: barlar yatay eksende (1920 boyunca) dizili, dikey (462) dolar
_MARGIN_X = 16
_CENTER_GAP = 30
_USABLE_W = WIDTH - 2 * _MARGIN_X - _CENTER_GAP
_HALF_USABLE = _USABLE_W // 2
_BAR_W = max(1, _HALF_USABLE // _HALF_N - 1)
_LEFT_START = _MARGIN_X
_RIGHT_START = _MARGIN_X + _HALF_USABLE + _CENTER_GAP
_MAX_H = HEIGHT - 12
_BOTTOM_Y = HEIGHT - 6

_idle_font_cache = {}


def draw_idle_screen(surf, t):
    """NATIVE yatay bekleme ekrani (muzik yokken siyah kalmasin)."""
    surf.fill((8, 8, 10))
    key = 90
    if key not in _idle_font_cache:
        _idle_font_cache[key] = pygame.font.SysFont("DejaVu Sans", key, bold=True)
    font = _idle_font_cache[key]
    pulse = int(140 + 60 * abs(((t * 0.4) % 2.0) - 1.0))
    color = (pulse, int(pulse * 0.85), int(pulse * 0.6))
    ts = font.render("VİNTAGE SES KONSOLU", True, color)
    surf.blit(ts, (WIDTH // 2 - ts.get_width() // 2, HEIGHT // 2 - ts.get_height() // 2))


def draw_spectrum(surf, cava_bars, theme_name, fps):
    """NATIVE yatay: barlar dikey durur, asagidan yukari dolar (Mac gibi NET)."""
    surf.fill((10, 10, 12))
    # kanal dizilimi (varyant 1 = L + R[::-1])
    L = cava_bars[:_HALF_N]; R = cava_bars[_HALF_N:_HALF_N*2]
    lay = _state.get("ch_layout", 1)
    if lay == 0:   bars = L + R
    elif lay == 1: bars = L + R[::-1]
    elif lay == 2: bars = L[::-1] + R
    else:          bars = L[::-1] + R[::-1]

    for i in range(NUM_BARS):
        v = bars[i] if i < len(bars) else 0
        target = (v / 255.0) * _MAX_H * 0.92
        _spec_smooth[i] += (target - _spec_smooth[i]) * 0.85
        H = min(int(_spec_smooth[i]), _MAX_H)
        if H >= _spec_peak[i]:
            _spec_peak[i] = H
            _spec_ptimers[i] = fps * 0.7
        else:
            if _spec_ptimers[i] > 0:
                _spec_ptimers[i] -= 1
            else:
                _spec_peak[i] = max(0, _spec_peak[i] - 3)
        if i < _HALF_N:
            x = _LEFT_START + i * (_BAR_W + 1)
        else:
            x = _RIGHT_START + (i - _HALF_N) * (_BAR_W + 1)
        if H > 2:
            for yo in range(0, H, 4):
                ratio = yo / _MAX_H
                seg = gradient_color(theme_name, ratio)
                pygame.draw.rect(surf, seg, (x, _BOTTOM_Y - yo - 4, _BAR_W, 4))
        pk = int(_spec_peak[i])
        if pk > 2:
            pygame.draw.rect(surf, (245, 215, 150), (x, _BOTTOM_Y - pk, _BAR_W, 2))


# ==================== API SENDER (ayri process) ====================
def sender_process_main(shm_name, frame_counter, w, h, *_unused):
    """Ayri surec: shared memory'den kareyi al -> DOGRUDAN USB ile panele yaz.
    trcc / HTTP / PNG / disk YOK. (trcc_direct.py protokolu kullanir)"""
    if sys.stdout is None:
        sys.stdout = _NullIO()
    if sys.stderr is None:
        sys.stderr = _NullIO()

    import pygame as pg
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pg.display.init()

    from trcc_direct import TrccDirect
    dev = TrccDirect()
    try:
        dev.connect()
    except Exception as e:
        print(f"[sender] PANEL BAGLANTI HATASI: {e}")
        print("[sender] trcc calisiyor olabilir -> 'trcc kill' yapip tekrar dene")
        return

    shm = shared_memory.SharedMemory(name=shm_name)
    print("[sender] dogrudan USB akisi basladi.")
    last = -1
    sent = 0
    errs = 0
    win_t0 = time.time()
    win_sent = 0
    try:
        while True:
            cur = frame_counter.value
            if cur == -1:
                break
            if cur != last:
                last = cur
                raw = bytes(shm.buf[:w * h * 3])
                try:
                    surf = pg.image.frombuffer(raw, (w, h), "RGB")
                    dev.send_surface(surf)
                    sent += 1
                    win_sent += 1
                    now = time.time()
                    if now - win_t0 >= 5.0:
                        print(f"[sender] panele giden: {win_sent/(now-win_t0):.1f} FPS")
                        win_t0 = now
                        win_sent = 0
                except Exception as e:
                    errs += 1
                    if errs <= 3 or errs % 50 == 0:
                        print(f"[sender] HATA #{errs}: {type(e).__name__}: {e}")
                    time.sleep(0.05)
            else:
                time.sleep(0.002)
    finally:
        shm.close()
        try:
            dev.close()
        except Exception:
            pass
        print(f"[sender] bitti. gonderilen: {sent}, hata: {errs}")


def api_setup():
    """DOGRUDAN USB modunda trcc'ye ihtiyac YOK.
    Sadece trcc'nin USB'yi tutmadigindan emin oluruz."""
    # Calisan trcc varsa kapat (USB'yi birakmasi icin - ayni anda ikisi olmaz)
    try:
        subprocess.run(["trcc", "kill"], timeout=8,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        print("trcc kapatildi (USB serbest).")
    except Exception:
        pass   # trcc kurulu degilse sorun yok - zaten gerekmiyor
    time.sleep(0.5)
    return True


def build_tray():
    """Sistem tepsisi ikonu + menu. pygame ile ayni process, processEvents ile."""
    from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QActionGroup
    from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    def make_icon():
        pm = QPixmap(64, 64); pm.fill(QColor(20, 22, 26))
        p = QPainter(pm)
        p.setBrush(QColor(60, 220, 120)); p.setPen(QColor(60, 220, 120))
        x = 6
        for h in [20, 38, 28, 48, 34, 44, 24]:
            p.drawRect(x, 58 - h, 7, h); x += 9
        p.end()
        return QIcon(pm)

    def theme_icon(stops):
        """Tema paletinden 3 renkli yatay serit ikonu uret."""
        pm2 = QPixmap(48, 48); pm2.fill(QColor(0, 0, 0, 0))
        pp = QPainter(pm2)
        n = len(stops)
        seg = 48 // n
        for i, c in enumerate(stops):
            pp.setBrush(QColor(c[0], c[1], c[2])); pp.setPen(QColor(c[0], c[1], c[2]))
            pp.drawRect(i * seg, 8, seg, 32)
        pp.end()
        return QIcon(pm2)

    global _tray_ref, _menu_ref
    tray = QSystemTrayIcon(make_icon()); tray.setToolTip("Vumeter LCD (Native+API)")
    menu = QMenu(); _tray_ref = tray; _menu_ref = menu

    # Spektrum -> renk temalari
    spek = menu.addMenu("Spektrum")
    def mk_spek(idx):
        def _f(): _state["mode"] = "Spektrum"; _state["theme_idx"] = idx
        return _f
    for i, tn in enumerate(COLOR_THEME_NAMES):
        a = QAction(theme_icon(COLOR_THEMES[tn]), tn, menu)
        a.triggered.connect(mk_spek(i)); spek.addAction(a)

    # LED Spektrum -> LED temalari
    leds = menu.addMenu("LED Spektrum")
    def mk_led(idx):
        def _f():
            _state["mode"] = "LED Spektrum"; _state["led_theme_idx"] = idx
            _led_texture_cache["surf"] = None
        return _f
    for i, tn in enumerate(LED_THEME_NAMES):
        a = QAction(theme_icon(LED_THEMES[tn]), tn, menu)
        a.triggered.connect(mk_led(i)); leds.addAction(a)

    # VU Metre -> kadranlar
    vum = menu.addMenu("VU Metre")
    def mk_vu(idx):
        def _f():
            _state["mode"] = "VU Metre"; _state["vu_dial_idx"] = idx
            _vu_scaled_cache.clear()
        return _f
    for i in range(len(VU_DIALS)):
        a = QAction(f"Kadran {i+1}", menu); a.triggered.connect(mk_vu(i)); vum.addAction(a)

    # Olcum Paneli -> sayfalar
    olcp = menu.addMenu("Olcum Paneli")
    pg_group = QActionGroup(menu); pg_group.setExclusive(True)
    def mk_page(idx):
        def _f(): _state["mode"] = "Olcum Paneli"; _state["meter_page"] = idx
        return _f
    for i, pn in enumerate(("Seviyeler", "Analiz")):
        a = QAction(pn, menu, checkable=True); a.setChecked(_state.get("meter_page",0)==i)
        a.triggered.connect(mk_page(i)); pg_group.addAction(a); olcp.addAction(a)

    # Sistem Monitoru
    smon = QAction("Sistem Monitoru", menu)
    smon.triggered.connect(lambda: _state.__setitem__("mode", "Sistem Monitoru"))
    menu.addAction(smon)
    menu.addSeparator()

    # Parlaklik (API'ye baglanir)
    br_menu = menu.addMenu("Parlaklik")
    br_group = QActionGroup(menu); br_group.setExclusive(True)
    def mk_br(p):
        def _f(): _state["brightness"] = p; _state["brightness_changed"] = True
        return _f
    for p in (100, 75, 50, 25):
        a = QAction(f"%{p}", menu, checkable=True); a.setChecked(_state["brightness"]==p)
        a.triggered.connect(mk_br(p)); br_group.addAction(a); br_menu.addAction(a)

    menu.addSeparator()
    qa = QAction("Cikis", menu)
    def do_quit(): _state["running"] = False
    qa.triggered.connect(do_quit); menu.addAction(qa)

    # KONTROL PENCERESI - tray'e tiklaninca acilir
    ctrl_win = [None]
    def _brightness_cb(v):
        _state["brightness"] = v; _state["brightness_changed"] = True
    def _led_clear():
        _led_texture_cache["surf"] = None
    def _vu_clear():
        _vu_scaled_cache.clear()
    def open_control():
        try:
            import control_window
            if ctrl_win[0] is None:
                ctrl_win[0] = control_window.build_control_window(
                    _state, COLOR_THEME_NAMES, LED_THEME_NAMES, len(VU_DIALS),
                    _brightness_cb, _led_clear, _vu_clear)
            w = ctrl_win[0]
            if hasattr(w, "_refresh"): w._refresh()
            w.show(); w.raise_(); w.activateWindow()
        except Exception as e:
            print(f"Kontrol penceresi hatasi: {e}")
    def on_tray_activated(reason):
        # Trigger (sol tik) veya DoubleClick -> pencere ac
        from PyQt5.QtWidgets import QSystemTrayIcon as _QSTI
        if reason in (_QSTI.Trigger, _QSTI.DoubleClick):
            open_control()
    tray.activated.connect(on_tray_activated)

    # Menuye de "Kontrol Paneli" ekle (en uste)
    ctrl_action = menu.actions()[0] if menu.actions() else None
    open_act = QAction("Kontrol Paneli Ac", menu)
    open_act.triggered.connect(open_control)
    menu.insertAction(ctrl_action, open_act)
    menu.insertSeparator(ctrl_action)

    tray.setContextMenu(menu); tray.show()
    return app


# ==================== ANA DONGU ====================
def _parse_args():
    """Komut satiri: python native_proto_win.py "Spektrum" --layout 0
    --layout: kanal dizilimi (0: bas sol/tiz sag, 1: L+R', 2: L'+R, 3: L'+R')"""
    args = sys.argv[1:]
    if "--layout" in args:
        i = args.index("--layout")
        try:
            lay = int(args[i + 1])
            if 0 <= lay <= 3:
                _state["ch_layout"] = lay
                names = ["0: bas sol, tiz sag", "1: L+R'", "2: L'+R", "3: L'+R'"]
                print(f"Kanal dizilimi -> {names[lay]}")
        except (IndexError, ValueError):
            print("--layout icin 0-3 arasi bir sayi ver")
        del args[i:i + 2]
    return args


def main():
    # --- COM'u EN BASTA STA olarak baslat (Qt tepsi ikonu icin SART) ---
    # Baska bir sey (SDL/pygame) once MTA baslatirsa Qt'nin OleInitialize'i
    # RPC_E_CHANGED_MODE ile patlar -> tepsi ikonu gorunur ama TIKLANMAZ.
    try:
        import ctypes
        hr = ctypes.windll.ole32.CoInitializeEx(None, 0x2)   # 0x2 = STA
        if hr in (0, 1):
            print("COM: STA (tepsi ikonu icin hazir)")
        else:
            print(f"COM zaten baslatilmis (hr=0x{hr & 0xffffffff:08x})")
    except Exception as e:
        print("COM init uyarisi:", e)

    # argv: mod adi + --layout N + --autostart / --no-tray bayraklari
    args = _parse_args()          # --layout N'yi isler ve listeden cikarir
    autostart = "--autostart" in args
    args = [a for a in args if not a.startswith("--")]
    if args:
        _state["mode"] = args[0]
    # pygame.init() TUM modulleri (ses mixer dahil) baslatir -> mixer bir ses
    # aygiti acar -> PipeWire/KDE "ses aygiti degisti" OSD'sini tetikler (LG'de
    # profil listesi belirir). Biz ses CALMIYORUZ (cava'dan okuyoruz), mixer'a
    # gerek yok. Sadece gerekli modulleri baslat, mixer'i ATLA:
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")  # ses surucusu yukleme
    pygame.display.init()
    pygame.font.init()
    surf = pygame.Surface((WIDTH, HEIGHT))
    print(f"MOD: {_state['mode']}" + (" (autostart)" if autostart else ""))

    # A+B: autostart'ta sistemin oturmasini bekle (acilis yarisi atlamasini onler)
    if autostart:
        print("Autostart: sistemin oturmasi bekleniyor...")
        wait_until_ready()

    if not api_setup():
        print("API kurulamadi, cikiliyor.")
        return

    cava = CavaReader()

    # B+: autostart'ta cava GERCEKTEN veri uretene kadar bekle (atlama onleme)
    if autostart:
        wait_for_cava_data(cava, timeout=12)

    # tray menu - RETRY'li (acilista masaustu gec hazir olabilir)
    qt_app = None
    if not (len(sys.argv) > 1 and sys.argv[1] == "--no-tray"):
        tray_attempts = 5 if autostart else 1
        for attempt in range(tray_attempts):
            try:
                qt_app = build_tray()
                print(f"Tray menu aktif (deneme {attempt+1}).")
                break
            except Exception as e:
                if attempt < tray_attempts - 1:
                    print(f"Tray deneme {attempt+1} basarisiz ({e}), 3sn sonra tekrar...")
                    time.sleep(3)
                else:
                    print(f"Tray baslatilamadi ({e}), argv modu (menusuz).")

    # shared memory + sender process
    shm = shared_memory.SharedMemory(create=True, size=WIDTH*HEIGHT*3)
    frame_counter = mp.Value("q", 0, lock=False)
    send_proc = mp.Process(target=sender_process_main,
                          args=(shm.name, frame_counter, WIDTH, HEIGHT), daemon=True)
    send_proc.start()

    print(f"Baslatildi. Native {WIDTH}x{HEIGHT}, {FPS} FPS, {_state['mode']}. Ctrl+C ile cik.")
    frames = 0; t0 = time.time()
    try:
        while _state["running"]:
            frame_start = time.time()
            snap = cava.snapshot()
            mode = _state["mode"]
            # IDLE: uzun sure ses yoksa bekleme ekrani (siyah kalmasin)
            if snap and max(snap) > 2:
                _state["last_sound"] = time.time()
            idle = (time.time() - _state.get("last_sound", 0)) > 8.0
            # Sistem Monitoru sesten BAGIMSIZ - idle'a dusmez, her zaman gosterilir
            if mode == "Sistem Monitoru":
                draw_sysmon(surf, FPS)
            elif idle:
                draw_idle_screen(surf, time.time() - t0)
            elif mode == "LED Spektrum":
                draw_led_spectrum(surf, snap, FPS)
            elif mode == "VU Metre":
                draw_vu_meter(surf, snap)
            elif mode == "Olcum Paneli":
                draw_meter_panel(surf, snap)
            else:
                draw_spectrum(surf, snap, COLOR_THEME_NAMES[_state["theme_idx"]], FPS)

            # yazilimsal parlaklik (dogrudan USB'de donanim komutu yok;
            # trcc de piksel bazinda karartiyor)
            _b = _state.get("brightness", 100)
            if _b < 100:
                _dark = pygame.Surface((WIDTH, HEIGHT))
                _dark.fill((0, 0, 0))
                _dark.set_alpha(int((100 - _b) * 2.55))
                surf.blit(_dark, (0, 0))

            # surface -> shared memory (ham RGB)
            raw = pygame.image.tostring(surf, "RGB")
            shm.buf[:len(raw)] = raw
            frame_counter.value = frames
            frames += 1

            # tray olaylarini isle
            if qt_app is not None:
                qt_app.processEvents()
            # parlaklik: DOGRUDAN USB'de donanim komutu yok -> yazilimsal
            # karartma yapiyoruz (trcc de ayni sekilde yapiyor: piksel bazinda)
            _state["brightness_changed"] = False

            # FPS sinirla
            dt = 1.0 / FPS
            slp = dt - (time.time() - frame_start)
            if slp > 0:
                time.sleep(slp)

            if frames % 60 == 0:
                fps_now = frames / (time.time() - t0)
                sys.stderr.write(f"\r{frames} kare, {fps_now:.1f} FPS  ")
    except KeyboardInterrupt:
        print("\nCikiliyor...")
    finally:
        _state["running"] = False
        # Cikista paneli TEMIZLE: siyah kareyi shared memory'ye yaz, sender gondersin
        try:
            black = pygame.Surface((WIDTH, HEIGHT))
            black.fill((0, 0, 0))
            shm.buf[:WIDTH*HEIGHT*3] = pygame.image.tostring(black, "RGB")
            frames += 1
            frame_counter.value = frames
            time.sleep(0.4)          # sender'in gondermesini bekle
        except Exception:
            pass
        frame_counter.value = -1
        time.sleep(0.3)
        try:
            shm.close(); shm.unlink()
        except Exception:
            pass
        # cava'yi durdur (arka planda kalmasin)
        try:
            cava.stop()
        except Exception:
            pass
        # ---- trcc serve'i ACIK BIRAK (KRITIK PERFORMANS KURALI) ----
        # trcc serve ILK tema yuklemesinde LHM'yi arar ve ~60 sn bekler.
        # Serve'i oldurursek her acilista bu cezayi tekrar oderiz.
        # Acik birakirsak: ilk acilis 60 sn, sonraki acilislar ANINDA.
        # (Serve'i kapatmak icin: trcc kill)
        sp = _state.get("_serve_proc")
        if sp is not None:
            print("trcc serve acik birakiliyor (sonraki acilis hizli olsun).")
            print("  Tamamen kapatmak icin: trcc kill")
        print("Temiz kapandi.")


if __name__ == "__main__":
    # PyInstaller + multiprocessing (Windows SPAWN): SART!
    # Olmazsa sender sureci tum uygulamayi bastan baslatir (sonsuz dongu).
    mp.freeze_support()
    main()
