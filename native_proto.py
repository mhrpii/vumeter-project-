#!/usr/bin/env python3
"""YOL 2 PROTOTIP: Native 1920x462 yatay cizim + trcc HTTP API gonderim.
Sadece SPEKTRUM modu - iskeleti kanitlamak icin. Mac mimarisi referans.

Akis: cava -> ana dongu native yatay ciz -> shared_memory -> ayri sender
process -> trcc API display/theme -> panel (native, net, akici).
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

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # penceresiz
import pygame

# ==================== SABITLER ====================
WIDTH, HEIGHT = 1920, 462          # NATIVE YATAY (panel kendi donduruyor)
NUM_BARS = 204
API_BASE = "http://127.0.0.1:8080"
DEVICE_KEY = "0416:5408"
THEME_DIR = os.path.expanduser("~/.trcc-user/live_frame")
FPS = 30                            # native API'de daha yuksek denenebilir

CAVA_SOURCE_FALLBACK = "alsa_output.usb-Focusrite_Scarlett_Solo_4th_Gen_S1TTKRP5739DF7-00.HiFi__Line1__sink.monitor"
CAVA_CONFIG = os.path.expanduser("~/.config/cava/config_native")

COLOR_THEMES = {
    "Klasik":    [(0, 170, 0), (255, 255, 0), (255, 0, 0)],
    "Neon":      [(0, 220, 220), (140, 80, 240), (240, 40, 180)],
    "Alev":      [(255, 220, 40), (255, 100, 0), (200, 30, 20)],
    "Camgobegi": [(0, 40, 80), (0, 200, 210), (200, 255, 255)],
}
COLOR_THEME_NAMES = list(COLOR_THEMES.keys())
_HALF_N = NUM_BARS // 2

_state = {"theme_idx": 0, "running": True, "ch_layout": 1,
          "mode": "Spektrum", "led_theme_idx": 0, "vu_dial_idx": 0}

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


# ==================== SISTEM MONITORU (native yatay, Mac tarzi) ====================
try:
    import sysmon as _sysmon_mod
except Exception:
    _sysmon_mod = None
_sysmon_instance = None
_sm_font_cache = {}


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
        if t < 65: return (60, 230, 90)
        if t < 80: return (245, 210, 60)
        return (235, 60, 40)

    cpu_t = d.get("cpu_pkg"); gpu_j = d.get("gpu_junction") or d.get("gpu_temp")
    vrm = d.get("mb_vrm"); pch = d.get("mb_pch")
    use = d.get("cpu_usage"); gpu_u = d.get("gpu_usage")
    ram = d.get("ram_pct"); frq = d.get("cpu_freq")
    cpu_p = d.get("cpu_power"); gpu_p = d.get("gpu_power")
    pump = d.get("fan_pump"); sfan = d.get("fan_sys1")
    nd = d.get("net_down"); nu = d.get("net_up")

    def net_fmt(mbps):
        if mbps is None: return ("--", "kB/s", 0)
        if mbps < 1.0: return (f"{mbps*1024:.0f}", "kB/s", mbps/100.0)
        return (f"{mbps:.1f}", "MB/s", mbps/100.0)
    nd_txt, nd_unit, nd_frac = net_fmt(nd)
    nu_txt, nu_unit, nu_frac = net_fmt(nu)

    def col(t): return temp_color(t)
    bars = [
        ("CPU",  f"{cpu_t:.0f}"  if cpu_t is not None else "--", "C",  (cpu_t/100.0)  if cpu_t else 0, col(cpu_t)),
        ("GPU",  f"{gpu_j:.0f}"  if gpu_j is not None else "--", "C",  (gpu_j/110.0)  if gpu_j else 0, col(gpu_j)),
        ("VRM",  f"{vrm:.0f}"    if vrm is not None else "--",   "C",  (vrm/100.0)    if vrm else 0,   col(vrm)),
        ("PCH",  f"{pch:.0f}"    if pch is not None else "--",   "C",  (pch/90.0)     if pch else 0,   col(pch)),
        ("CPU%", f"{use:.0f}"    if use is not None else "--",   "%",  (use/100.0)    if use is not None else 0, GREEN),
        ("GPU%", f"{gpu_u:.0f}"  if gpu_u is not None else "--", "%",  (gpu_u/100.0)  if gpu_u is not None else 0, GREEN),
        ("RAM",  f"{ram:.0f}"    if ram is not None else "--",   "%",  (ram/100.0)    if ram is not None else 0, GREEN),
        ("GHz",  f"{frq/1000:.1f}" if frq else "--",            "",   (frq/5700.0)   if frq else 0, GREEN),
        ("C-W",  f"{cpu_p:.0f}"  if cpu_p is not None else "--", "W",  (cpu_p/250.0)  if cpu_p else 0, GREEN),
        ("G-W",  f"{gpu_p:.0f}"  if gpu_p is not None else "--", "W",  (gpu_p/350.0)  if gpu_p else 0, GREEN),
        ("Pump", f"{pump:.0f}"   if pump else "0",               "rpm",(pump/3000.0)  if pump else 0, GREEN),
        ("SFan", f"{sfan:.0f}"   if sfan else "0",               "rpm",(sfan/3000.0)  if sfan else 0, GREEN),
        ("Indir",nd_txt, nd_unit, nd_frac, GREEN),
        ("Yukle",nu_txt, nu_unit, nu_frac, GREEN),
    ]
    n = len(bars); margin = 30
    slot_w = (WIDTH - 2*margin) // n
    bar_w = max(8, int(slot_w * 0.25))
    bar_top = 70; bar_bottom = HEIGHT - 52
    bar_h_full = bar_bottom - bar_top
    vfont = _sm_font(42); ufont = _sm_font(20); lfont = _sm_font(22)
    for idx, (lbl, vtxt, unit, frac, color) in enumerate(bars):
        frac = max(0.0, min(1.0, frac))
        slot_x = margin + idx * slot_w
        cx = slot_x + slot_w // 2
        x = cx - bar_w // 2
        pygame.draw.rect(surf, DARK, (x, bar_top, bar_w, bar_h_full))
        fh = int(bar_h_full * frac)
        pygame.draw.rect(surf, color, (x, bar_bottom - fh, bar_w, fh))
        pygame.draw.rect(surf, GREY, (x, bar_top, bar_w, bar_h_full), 1)
        vs = vfont.render(vtxt, True, color)
        surf.blit(vs, (cx - vs.get_width()//2, 24))
        if unit:
            us = ufont.render(unit, True, DIM)
            surf.blit(us, (cx - us.get_width()//2, 24 + vs.get_height() - 2))
        ls = lfont.render(lbl, True, WHITE)
        surf.blit(ls, (cx - ls.get_width()//2, bar_bottom + 10))


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
def _find_scarlett_monitor():
    try:
        out = subprocess.run(["pactl", "list", "short", "sources"],
                             capture_output=True, text=True, timeout=3).stdout
        cand = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            name = parts[1]
            if "Scarlett" in name and name.endswith(".monitor"):
                cand.append(("RUNNING" in line, name))
        if cand:
            cand.sort(key=lambda c: (not c[0]))
            return cand[0][1]
    except Exception:
        pass
    return CAVA_SOURCE_FALLBACK


def write_cava_config(bars=NUM_BARS, fps=60):
    os.makedirs(os.path.dirname(CAVA_CONFIG), exist_ok=True)
    src = _find_scarlett_monitor()
    with open(CAVA_CONFIG, "w") as f:
        f.write(f"""[general]
bars = {bars}
framerate = {fps}
autosens = 1
sensitivity = 100

[input]
method = pulse
source = {src}

[output]
method = raw
data_format = ascii
ascii_max_range = 255
""")


class CavaReader:
    def __init__(self):
        self.bars = [0] * NUM_BARS
        self._lock = threading.Lock()
        self.proc = None
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _start(self):
        write_cava_config()
        self.proc = subprocess.Popen(["cava"], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)

    def _loop(self):
        while _state["running"]:
            if self.proc is None or self.proc.poll() is not None:
                with self._lock:
                    self.bars = [0] * NUM_BARS
                try:
                    self._start()
                except Exception:
                    time.sleep(2); continue
            try:
                line = self.proc.stdout.readline()
                if not line:
                    time.sleep(0.5); continue
                parts = line.strip().rstrip(";").split(";")
                if len(parts) >= NUM_BARS:
                    with self._lock:
                        self.bars = [int(p) for p in parts[:NUM_BARS]]
            except Exception:
                continue

    def snapshot(self):
        with self._lock:
            return list(self.bars)


# ==================== NATIVE YATAY SPEKTRUM ====================
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
def sender_process_main(shm_name, frame_counter, w, h, api_base, key, theme_dir):
    import requests as rq
    import pygame as pg
    pg.init()
    shm = shared_memory.SharedMemory(name=shm_name)
    session = rq.Session()
    png_path = os.path.join(theme_dir, "00.png")
    url = f"{api_base}/devices/{key}/display/theme"
    last = -1
    try:
        while True:
            cur = frame_counter.value
            if cur == -1:
                break
            if cur != last:
                last = cur
                raw = bytes(shm.buf[:w*h*3])
                try:
                    surf = pg.image.frombuffer(raw, (w, h), "RGB")
                    pg.image.save(surf, png_path)
                    session.post(url, json={"path": "live_frame"}, timeout=2.0)
                except Exception:
                    pass
            else:
                time.sleep(0.003)
    finally:
        shm.close()


def api_setup():
    """serve baslat + connect + fit-mode + theme klasoru."""
    import requests as rq
    session = rq.Session()
    # serve calisiyor mu
    try:
        session.get(f"{API_BASE}/health", timeout=2)
    except Exception:
        subprocess.Popen(["trcc", "serve", "--port", "8080"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)
    # theme klasoru - ONCE TEMIZLE (eski trcc.json/config1.dc saat/tarih elementleri kalmasin)
    os.makedirs(THEME_DIR, exist_ok=True)
    import glob
    for old_cfg in glob.glob(os.path.join(THEME_DIR, "*.json")) + glob.glob(os.path.join(THEME_DIR, "*.dc")):
        try: os.remove(old_cfg)
        except OSError: pass
    cfg = os.path.join(THEME_DIR, "config.json")
    with open(cfg, "w") as f:
        f.write('{"name": "live_frame", "elements": []}\n')
    # connect
    try:
        r = session.post(f"{API_BASE}/devices/{DEVICE_KEY}/connect", timeout=10)
        print("connect:", r.json().get("message", r.text)[:60])
        session.post(f"{API_BASE}/devices/{DEVICE_KEY}/display/fit-mode",
                    json={"mode": "stretch"}, timeout=5)
        session.post(f"{API_BASE}/devices/{DEVICE_KEY}/display/brightness",
                    json={"percent": 100}, timeout=5)
        return True
    except Exception as e:
        print("API setup HATA:", e)
        return False


# ==================== ANA DONGU ====================
def main():
    # argv'den mod sec (test icin): python3 native_proto.py "LED Spektrum"
    if len(sys.argv) > 1:
        _state["mode"] = sys.argv[1]
    pygame.init()
    surf = pygame.Surface((WIDTH, HEIGHT))
    print(f"MOD: {_state['mode']}")

    if not api_setup():
        print("API kurulamadi, cikiliyor.")
        return

    cava = CavaReader()

    # shared memory + sender process
    shm = shared_memory.SharedMemory(create=True, size=WIDTH*HEIGHT*3)
    frame_counter = mp.Value("q", 0, lock=False)
    send_proc = mp.Process(target=sender_process_main,
                          args=(shm.name, frame_counter, WIDTH, HEIGHT,
                                API_BASE, DEVICE_KEY, THEME_DIR), daemon=True)
    send_proc.start()

    print(f"Baslatildi. Native {WIDTH}x{HEIGHT}, {FPS} FPS, Spektrum. Ctrl+C ile cik.")
    frames = 0; t0 = time.time()
    try:
        while _state["running"]:
            frame_start = time.time()
            snap = cava.snapshot()
            mode = _state["mode"]
            if mode == "LED Spektrum":
                draw_led_spectrum(surf, snap, FPS)
            elif mode == "VU Metre":
                draw_vu_meter(surf, snap)
            elif mode == "Sistem Monitoru":
                draw_sysmon(surf, FPS)
            else:
                draw_spectrum(surf, snap, COLOR_THEME_NAMES[_state["theme_idx"]], FPS)

            # surface -> shared memory (ham RGB)
            raw = pygame.image.tostring(surf, "RGB")
            shm.buf[:len(raw)] = raw
            frame_counter.value = frames
            frames += 1

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
        frame_counter.value = -1
        time.sleep(0.3)
        try:
            shm.close(); shm.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    main()
