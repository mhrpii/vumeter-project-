#!/usr/bin/env python3
"""Linux LCD (Thermalright Trofeo) ses gorsellestirme + sistem monitoru.
- pygame offscreen (pencere yok, 1920x462 -> 90° dondur -> LCD)
- trcc shell alt-sureci (kalici baglanti, hizli send-image)
- cava (PipeWire) ses spektrum kaynagi
- Sistem Monitoru: sysmon.py (native /sys/class/hwmon + RAPL)
- Klavye kontrol (terminal stdin): 1=Spektrum, 5=Sistem Monitoru, TAB=tema, Q=cikis
"""
import argparse
import math
import os
import subprocess
import sys
import threading
import time

import pygame

import sysmon  # native Linux sensorleri

# ==================== SABITLER ====================
WIDTH, HEIGHT = 462, 1920          # PORTRE cizim: LCD duz gosterir (dondurme yok, net)
FRAME_PATH = "/tmp/lcd_frame.png"
__version__ = "1.3.0"
DEVICE_KEY = "0416:5408"
CAVA_CONFIG = os.path.expanduser("~/.config/cava/config")

def _blit_rot(surf, text_surf, cx, cy):
    """Yaziyi -90 dondurup (cx,cy) merkezine yerlestir.
    LCD ekrani ters cevirdigi icin, boylece yazi duz gorunur."""
    rot = pygame.transform.rotate(text_surf, -90)
    surf.blit(rot, (cx - rot.get_width()//2, cy - rot.get_height()//2))


# Renk temalari (Spektrum icin)
COLOR_THEMES = {
    "Klasik":    [(0, 170, 0), (255, 255, 0), (255, 0, 0)],       # yesil-sari-kirmizi
    "Neon":      [(0, 220, 220), (140, 80, 240), (240, 40, 180)],
    "Alev":      [(255, 220, 40), (255, 100, 0), (200, 30, 20)],
    "Camgobegi": [(0, 40, 80), (0, 200, 210), (200, 255, 255)],   # koyu lacivert->parlak camgobegi->beyaz
}
COLOR_THEME_NAMES = list(COLOR_THEMES.keys())

# LED tema paletleri (Mac ile ayni)
LED_THEMES = {
    "Camgobegi": [(10, 90, 70), (40, 200, 190), (120, 255, 235)],
    "Yesil-Sari-Kirmizi": [(40, 220, 40), (250, 200, 30), (235, 45, 30)],
    "Mavi": [(20, 60, 200), (60, 140, 255), (170, 220, 255)],
    "Mor": [(120, 0, 200), (210, 60, 230), (255, 210, 255)],
}
LED_THEME_NAMES = list(LED_THEMES.keys())
_led_texture_cache = {"surf": None, "width": None, "palette": None, "style": None}
_tray_ref = None
_menu_ref = None


def gradient_color(theme_name, ratio):
    """0..1 arasi ratio ile 3 renkli palette'de gecis."""
    palette = COLOR_THEMES.get(theme_name, COLOR_THEMES["Klasik"])
    if ratio <= 0.5:
        c1, c2 = palette[0], palette[1]; f = ratio * 2
    else:
        c1, c2 = palette[1], palette[2]; f = (ratio - 0.5) * 2
    return (int(c1[0]+(c2[0]-c1[0])*f), int(c1[1]+(c2[1]-c1[1])*f), int(c1[2]+(c2[2]-c1[2])*f))


NUM_BARS = 140

# ==================== DURUM ====================
_state = {
    "mode": "Spektrum",     # Spektrum | Sistem Monitoru (adim 1)
    "theme_idx": 0,
    "led_theme_idx": 0,
    "vu_dial_idx": 0,
    "ch_layout": 1,   # GOZLE SECILDI (6 Tem): 1 = L+R' -> iki yarida bas sagda. C tusu ile degisir.
    "brightness": 100,
    "brightness_changed": False,
    "vu_dial_idx": 0,
    "ch_layout": 1,   # GOZLE SECILDI (6 Tem): 1 = L+R' -> iki yarida bas sagda. C tusu ile degisir.
    "meter_page": 0,
    "brightness": 100,
    "brightness_changed": False,
    "running": True,
}


# ==================== YARDIMCI: TRCC shell ====================
class TRCCSender:
    """trcc shell alt-sureci: baglanti bir kez kurulur, komutlar stdin'den gonderilir.
    Shell olurse otomatik yenilenir; ayrica her REFRESH_EVERY karede preemptive yenile."""
    REFRESH_EVERY = 50   # her N send-image'de bir shell'i sifirla (BrokenPipe olmadan)
    def __init__(self, device_key):
        self.key = device_key
        self.proc = None
        self._sent = 0
        self._spawn()

    def _spawn(self):
        # Eski surec varsa zorla oldur (temiz cikis yapmaya calisma)
        old = self.proc
        if old is not None:
            try: old.kill()
            except Exception: pass
            try: old.wait(timeout=0.5)
            except Exception: pass
        # Yeni shell
        self.proc = subprocess.Popen(
            ["trcc", "shell"],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            text=True, bufsize=1,
        )
        time.sleep(0.3)  # shell hazir olsun
        try:
            self.proc.stdin.write(f"device connect {self.key}\n")
            self.proc.stdin.write("device select 1\n")
            self.proc.stdin.flush()
        except Exception:
            pass
        time.sleep(0.7)  # connect tamamlansin
        self._sent = 0

    def set_brightness(self, pct):
        try:
            self.proc.stdin.write(f"display set-brightness {self.key} {pct}\n")
            self.proc.stdin.flush()
        except Exception:
            pass

    def send(self, path):
        try:
            self.proc.stdin.write(f"display send-image {self.key} {path}\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            # Nadir: shell olduyse bir kez yenile
            sys.stderr.write("\n[TRCC shell yenileniyor]\n")
            try:
                self._spawn()
                self.proc.stdin.write(f"display send-image {self.key} {path}\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    def close(self):
        try:
            if self.proc: self.proc.stdin.write("exit\n"); self.proc.stdin.flush()
            self.proc.wait(timeout=3)
        except Exception:
            try: self.proc.kill()
            except: pass


# ==================== CAVA ====================
CAVA_SOURCE = "alsa_output.usb-Focusrite_Scarlett_Solo_4th_Gen_S1TTKRP5739DF7-00.HiFi__Line1__sink.monitor"

def _wait_for_source(timeout=90):
    """Acilis yarisina karsi: Scarlett PipeWire kaynagi gorunene kadar bekle.
    Kaynak gelirse True, sure dolarsa False (yine de denenir)."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            out = subprocess.run(["pactl", "list", "short", "sources"],
                                 capture_output=True, text=True, timeout=3).stdout
            if CAVA_SOURCE in out:
                return True
        except Exception:
            pass
        if not _state.get("running", True):
            return False
        time.sleep(2)
    return False


def write_cava_config(bars=NUM_BARS, fps=60):
    """PipeWire monitor kaynagi ile cava config yaz."""
    os.makedirs(os.path.dirname(CAVA_CONFIG), exist_ok=True)
    src = CAVA_SOURCE
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
bit_format = 8bit
""")


class CavaReader:
    """cava'yi alt surec baslat, satir satir 14 sayilik dizi oku."""
    def __init__(self):
        self.bars = [0] * NUM_BARS
        self._lock = threading.Lock()
        self.proc = None
        self._t = threading.Thread(target=self._read_loop, daemon=True)
        self._t.start()

    def _start_cava(self):
        """Kaynak hazir olana kadar bekle, sonra cava'yi baslat."""
        _wait_for_source()
        write_cava_config()
        self.proc = subprocess.Popen(
            ["cava"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )

    def _read_loop(self):
        while _state["running"]:
            # cava calismiyorsa (ilk acilis / oldu / kaynak gitti) baslat
            if self.proc is None or self.proc.poll() is not None:
                with self._lock:
                    self.bars = [0] * NUM_BARS   # bekleme sirasinda bos ekran/idle
                try:
                    self._start_cava()
                except Exception:
                    time.sleep(3)
                    continue
            try:
                line = self.proc.stdout.readline()
                if not line:
                    # cava kapandi (or. Scarlett USB'den cekildi) -> dongu basinda yeniden baslar
                    time.sleep(2)
                    continue
                parts = line.strip().rstrip(";").split(";")
                if len(parts) >= NUM_BARS:
                    vals = [int(p) for p in parts[:NUM_BARS]]
                    with self._lock:
                        self.bars = vals
            except Exception:
                continue

    def snapshot(self):
        with self._lock:
            return list(self.bars)

    def stop(self):
        try: self.proc.terminate()
        except: pass


# ==================== CIZIM: SPEKTRUM (Mac ile ayni: 140 ince bar) ====================
# Global spektrum durumu (smooth/peak) -- baslangic sifir
import numpy as np
_spec_smooth = np.zeros(NUM_BARS)
_spec_peak = np.zeros(NUM_BARS)
_spec_ptimers = np.zeros(NUM_BARS)

# Spektrum layout sabitleri (Mac ile ayni)
# PORTRE spektrum sabitleri: barlar dikey eksende (H=1920) dizilir,
# yatay eksende (W=462) dolar. Sol yarim ustte, sag yarim altta.
_MARGIN_Y = 20
_CENTER_GAP = 40
_HALF_N = NUM_BARS // 2
_USABLE_H = HEIGHT - 2 * _MARGIN_Y - _CENTER_GAP   # dikey kullanilabilir (1920 boyunca)
_HALF_USABLE = _USABLE_H // 2
_BAR_THICK = max(1, _HALF_USABLE // _HALF_N - 2)    # her barin dikey kalinligi
_TOP_START = _MARGIN_Y                              # sol yarim baslangici (ust)
_BOT_START = _MARGIN_Y + _HALF_USABLE + _CENTER_GAP # sag yarim baslangici (alt)
_BARS_MAX_LEN = WIDTH                               # barin uzayabilecegi max (462)


def _lerp(c1, c2, f):
    return (int(c1[0] + (c2[0]-c1[0])*f),
            int(c1[1] + (c2[1]-c1[1])*f),
            int(c1[2] + (c2[2]-c1[2])*f))


def _get_led_texture(bar_thick, max_len, palette_name, style="rect"):
    """PORTRE: YATAY LED texture (soldan-saga segmentler). bar_thick=dikey kalinlik,
    max_len=yatay uzunluk (462). LCD cevirince dikey LED barlari olur."""
    cache = _led_texture_cache
    if (cache["surf"] is not None and cache["width"] == bar_thick
            and cache["palette"] == palette_name and cache["style"] == style):
        return cache["surf"]
    stops = LED_THEMES.get(palette_name, LED_THEMES["Camgobegi"])
    LED_SEG_L = 22   # segment uzunlugu (yatay)
    LED_SEG_GAP = 6
    step = LED_SEG_L + LED_SEG_GAP
    tex = pygame.Surface((max_len, bar_thick))   # YATAY: genis x ince
    tex.fill((0, 0, 0))
    x_off = 0
    while x_off < max_len:
        ratio = x_off / max_len
        if ratio < 0.5:
            color = _lerp(stops[0], stops[1], ratio / 0.5)
        else:
            color = _lerp(stops[1], stops[2], (ratio - 0.5) / 0.5)
        color = (min(255, max(0, color[0])), min(255, max(0, color[1])), min(255, max(0, color[2])))
        if style == "dot":
            cx_dot = x_off + LED_SEG_L // 2
            cy_dot = bar_thick // 2
            r_dot = max(2, min(bar_thick, LED_SEG_L) // 2)
            pygame.draw.circle(tex, color, (cx_dot, cy_dot), r_dot)
        else:
            pygame.draw.rect(tex, color, (x_off, 0, LED_SEG_L, bar_thick))
        x_off += step
    cache["surf"] = tex; cache["width"] = bar_thick
    cache["palette"] = palette_name; cache["style"] = style
    return tex


def draw_led_spectrum(surf, cava_bars, fps, led_style="rect"):
    """PORTRE LED spektrum: barlar dikey eksende dizili, yatay (soldan-saga) LED dolar."""
    surf.fill((0, 0, 0))
    LED_BAR_GAP = 10
    MERGE = 1
    num_bars = len(cava_bars)
    if num_bars < NUM_BARS:
        cava_bars = list(cava_bars) + [0] * (NUM_BARS - num_bars)
        num_bars = NUM_BARS
    base_thick = max(2, (HEIGHT - 2*20 - 40) // num_bars - LED_BAR_GAP)
    merged_thick = base_thick * MERGE + LED_BAR_GAP * (MERGE - 1)
    palette = LED_THEME_NAMES[_state["led_theme_idx"]]
    texture = _get_led_texture(merged_thick, _BARS_MAX_LEN, palette, led_style)

    i = 0
    while i < num_bars:
        group = range(i, min(i + MERGE, num_bars))
        L_max = 0
        for j in group:
            target = (cava_bars[j] / 255.0) * _BARS_MAX_LEN * 1.0
            _spec_smooth[j] += (target - _spec_smooth[j]) * 0.85
            Lj = min(int(_spec_smooth[j]), _BARS_MAX_LEN)
            if Lj >= _spec_peak[j]:
                _spec_peak[j] = Lj
                _spec_ptimers[j] = fps * 0.7
            else:
                if _spec_ptimers[j] > 0:
                    _spec_ptimers[j] -= 1
                else:
                    _spec_peak[j] = max(0, _spec_peak[j] - 3)
            L_max = max(L_max, Lj)
        if i < _HALF_N:
            y_pos = _TOP_START + (i // MERGE) * (merged_thick + LED_BAR_GAP)
        else:
            offset = i - _HALF_N
            y_pos = _BOT_START + (offset // MERGE) * (merged_thick + LED_BAR_GAP)
        if L_max > 1:
            src_rect = pygame.Rect(0, 0, L_max, merged_thick)
            surf.blit(texture, (0, y_pos), area=src_rect)
        peak_L = max(int(_spec_peak[j]) for j in group)
        if peak_L > 2:
            pygame.draw.rect(surf, (180, 255, 245), (peak_L, y_pos, 3, merged_thick))
        i += MERGE


# ==================== VU METRE (analog gosterge) ====================

# VU kadranlari: (dosya, pivot_x, pivot_y, ibre_RGB, (orij_w, orij_h))
# (dosya, pivot_x, pivot_y, ibre_RGB, orig_boyut, alt_kirpma_frac, olcek)
VU_DIALS = [
    ("vu_bg.png",  0.500, 0.820, (210, 30, 30), (2366, 1792), 0.0,  1.0),
    ("vu_bg2.png", 0.503, 0.867, (20, 20, 20),  (2624, 1620), 0.0,  1.15),
    ("vu_bg3.png", 0.503, 0.655, (20, 20, 20),  (2400, 1790), 0.10, 1.18),  # alt beyaz bant kirp + buyut
]
_vu_dial_cache = {}   # {idx: scaled_surface}


# VU kadranlari: (dosya, pivot_x, pivot_y, ibre_RGB, (orij_w, orij_h))
# (dosya, pivot_x, pivot_y, ibre_RGB, orig_boyut, alt_kirpma_frac, olcek)
VU_DIALS = [
    ("vu_bg.png",  0.500, 0.820, (210, 30, 30), (2366, 1792), 0.0,  1.0),
    ("vu_bg2.png", 0.503, 0.867, (20, 20, 20),  (2624, 1620), 0.0,  1.15),
    ("vu_bg3.png", 0.503, 0.655, (20, 20, 20),  (2400, 1790), 0.10, 1.18),  # alt beyaz bant kirp + buyut
]
_vu_dial_cache = {}   # {idx: scaled_surface}

_raw_vu_image = None
_vu_loaded = False
_vu_scaled_cache = {}
_vu_angles = {"l": 180.0, "r": 180.0}
_VU_PNG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "vu_bg.png")


def _load_vu_dial(idx):
    """Verilen kadran indeksinin ham gorselini yukle (cache'li). Alt kirpma uygulanir."""
    if idx in _vu_dial_cache:
        return _vu_dial_cache[idx]
    fname = VU_DIALS[idx][0]
    crop_b = VU_DIALS[idx][5] if len(VU_DIALS[idx]) > 5 else 0.0
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), fname)
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


def _make_one_vu(disp_w, current_angle):
    """Aktif VU kadranini YATAY surface'e ciz + ibre, -90 dondur (portre icin)."""
    idx = _state["vu_dial_idx"]
    img0 = _load_vu_dial(idx)
    if img0 is None:
        return None
    _fname, pvx, pvy, needle_col, _orig = VU_DIALS[idx][:5]
    crop_b = VU_DIALS[idx][5] if len(VU_DIALS[idx]) > 5 else 0.0
    scale = VU_DIALS[idx][6] if len(VU_DIALS[idx]) > 6 else 1.0
    disp_w = int(disp_w * scale)
    # Kirpma sonrasi pivot_y'yi yeni yukseklige gore olcekle
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
    return pygame.transform.rotate(tmp, -90)


def draw_vu_meter(surf, cava_bars):
    """PORTRE VU Metre: iki analog gosterge, dikey eksende alt alta (LCD cevirince yan yana)."""
    surf.fill((0, 0, 0))
    n = len(cava_bars)
    left_slice = cava_bars[:_HALF_N] if n >= _HALF_N else cava_bars
    right_slice = cava_bars[_HALF_N:] if n >= _HALF_N*2 else cava_bars
    # Spektrum gibi: anlik en yuksek bar (peak) -> ritmi birebir yakalar
    def vu_level(sl):
        if not len(sl): return 0.0
        arr = np.asarray(sl, dtype=float)
        peak = float(arr.max())
        mean = float(arr.mean())
        # peak+mean karisimi, dusuk carpan -> sona vurmaz, orta bolgede oynar
        v = (peak * 0.45 + mean * 0.55) / 255.0
        return min(1.0, v * 0.97)
    vol_l = vu_level(left_slice)
    vol_r = vu_level(right_slice)
    if not math.isfinite(vol_l): vol_l = 0.0
    if not math.isfinite(vol_r): vol_r = 0.0
    tgt_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    tgt_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    # Hafif yumusatma (spektrumun 0.85'i gibi hizli) -- ritmi kaybetmeden titremeyi onler
    _vu_angles["l"] += (tgt_l - _vu_angles["l"]) * 0.8
    _vu_angles["r"] += (tgt_r - _vu_angles["r"]) * 0.8

    # disp_w: gosterge genisligi. Portrede genislik W=462'ye sigmali (dondurulunce yukseklik olur)
    disp_w = int(WIDTH * 1.15)   # 462*1.15; dondurulunce disp_h portre genisligine oturur
    ow, oh = (2366, 1792)
    disp_h = int(disp_w * oh / ow)

    vu_l = _make_one_vu(disp_w, _vu_angles["l"])
    vu_r = _make_one_vu(disp_w, _vu_angles["r"])
    if vu_l is None:
        return
    # Dondurulmus surface boyutu: (disp_h, disp_w) -- portre'ye yerlestir
    rw, rh = vu_l.get_size()
    x = (WIDTH - rw) // 2
    # iki gosterge dikey eksende: ust ceyrek ve alt ceyrek merkezli
    y1 = HEIGHT // 4 - rh // 2
    y2 = 3 * HEIGHT // 4 - rh // 2
    surf.blit(vu_l, (x, y1))
    surf.blit(vu_r, (x, y2))

IDLE_THRESHOLD = 5.0  # bu kadar saniye sessizlik sonrasi bekleme ekrani

def draw_idle_screen(surf, t):
    """PORTRE bekleme ekrani: nabiz gibi yanip sonen yazi (LCD icin dondurulmus)."""
    surf.fill((8, 8, 10))
    pulse = int(120 + 80 * abs(((t * 0.6) % 2.0) - 1.0))
    color = (pulse, int(pulse * 0.85), int(pulse * 0.6))
    font = pygame.font.SysFont("DejaVu Sans", 60, bold=True)
    # Iki satir (uzun yazi portreye sigsin diye)
    _blit_rot(surf, font.render("VINTAGE AUDIO", True, color), WIDTH // 2 - 40, HEIGHT // 2)
    _blit_rot(surf, font.render("CONSOLE", True, color), WIDTH // 2 + 40, HEIGHT // 2)



def draw_spectrum(surf, cava_bars, theme_name, fps):
    """PORTRE 140 bar spektrum: barlar dikey eksende dizili, YATAY (soldan saga) dolar.
    LCD cevirince: klasik dikey spektrum (Mac gibi)."""
    surf.fill((10, 10, 12))
    n = len(cava_bars)
    for i in range(NUM_BARS):
        v = cava_bars[i] if i < n else 0
        target = (v / 255.0) * _BARS_MAX_LEN * 0.92
        _spec_smooth[i] += (target - _spec_smooth[i]) * 0.85
        L = min(int(_spec_smooth[i]), _BARS_MAX_LEN)   # barin uzunlugu (yatay)
        if L >= _spec_peak[i]:
            _spec_peak[i] = L
            _spec_ptimers[i] = fps * 0.7
        else:
            if _spec_ptimers[i] > 0:
                _spec_ptimers[i] -= 1
            else:
                _spec_peak[i] = max(0, _spec_peak[i] - 3)
        # sol yarim ustte, sag yarim altta (dikey eksende yerlesim)
        if i < _HALF_N:
            y_pos = _TOP_START + i * (_BAR_THICK + 2)
        else:
            y_pos = _BOT_START + (i - _HALF_N) * (_BAR_THICK + 2)
        # Gradient ile soldan-saga boyama (her 4px bir segment)
        if L > 2:
            for x_off in range(0, L, 4):
                ratio = x_off / _BARS_MAX_LEN
                seg = gradient_color(theme_name, ratio)
                pygame.draw.rect(surf, seg, (x_off, y_pos, 4, _BAR_THICK))
        # Tepe noktasi (krem beyaz) - barin ucunda
        peak_L = int(_spec_peak[i])
        if peak_L > 2:
            pygame.draw.rect(surf, (245, 215, 150), (peak_L, y_pos, 2, _BAR_THICK))


# ==================== CIZIM: SISTEM MONITORU (test_lcd_sysmon'dan) ====================

# ==================== OLCUM PANELI (APx555 tarzi, PORTRE) ====================
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
    """cava bantlari ~50Hz..16kHz logaritmik. Yaklasik Hz dondur."""
    if total_bars <= 1:
        return 0.0
    f_min, f_max = 50.0, 16000.0
    t = bar_index / (total_bars - 1)
    return f_min * (f_max / f_min) ** t



# ---- Sistem ses bilgisi (pactl, periyodik cache) ----
_sysaudio_cache = {"rate": "--", "fmt": "--", "ch": "--", "vol": "--", "dev": "--", "t": 0.0}

def _refresh_sysaudio():
    """pactl ile ses cihazi bilgisini oku (saniyede bir yeter)."""
    import subprocess, time as _t
    now = _t.time()
    if now - _sysaudio_cache["t"] < 1.5:
        return _sysaudio_cache
    _sysaudio_cache["t"] = now
    try:
        sink = subprocess.run(["pactl", "get-default-sink"],
                              capture_output=True, text=True, timeout=1).stdout.strip()
        # Ornekleme + format
        info = subprocess.run(["pactl", "list", "sinks"],
                              capture_output=True, text=True, timeout=1).stdout
        # ilgili sink blogunu bul
        spec = ""
        for block in info.split("Sink #"):
            if sink and sink in block:
                for line in block.splitlines():
                    if "Sample Specification" in line:
                        spec = line.split(":", 1)[1].strip()
                        break
                break
        if spec:
            parts = spec.split()  # ornek: s32le 2ch 48000Hz
            if len(parts) >= 3:
                _sysaudio_cache["fmt"] = parts[0]
                _sysaudio_cache["ch"] = parts[1]
                hz = parts[2].replace("Hz", "")
                try:
                    _sysaudio_cache["rate"] = f"{int(hz)//1000}kHz"
                except Exception:
                    _sysaudio_cache["rate"] = parts[2]
        # Ses seviyesi %
        vol = subprocess.run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"],
                             capture_output=True, text=True, timeout=1).stdout
        import re as _re
        m = _re.search(r"(\d+)%", vol)
        if m:
            _sysaudio_cache["vol"] = m.group(1) + "%"
        # Cihaz kisa adi
        if "Focusrite" in sink or "Scarlett" in sink:
            _sysaudio_cache["dev"] = "Scarlett Solo 4th Gen"
        elif sink:
            _sysaudio_cache["dev"] = sink.split(".")[-2][:24] if "." in sink else sink[:24]
    except Exception:
        pass
    return _sysaudio_cache


def draw_meter_panel(surf, cava_bars):
    """PORTRE Olcum Paneli: 4 blok dikey eksende alt alta (LCD cevirince 2x2).
    Her blok: baslik + 2 satir (etiket + yatay bar + sayi/birim). Yazilar dondurulur."""
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
        _all = left + right
        _tot = float(_all.sum())
        _bass = float(_all[:max(1, _HALF_N // 4)].sum())
        bass_ratio = (_bass / _tot) if _tot > 1 else 0.0
    else:
        bass_ratio = 0.0
    eng_l = float(left.sum()) if left.size else 0.0
    eng_r = float(right.sum()) if right.size else 0.0
    eng_max = max(eng_l, eng_r, 1.0)
    bal_l = eng_l / eng_max
    bal_r = eng_r / eng_max
    bal_pct = (eng_r / (eng_l + eng_r) * 100) if (eng_l + eng_r) > 1 else 50.0
    freq_l = _bar_to_hz(int(np.argmax(left)), _HALF_N) if left.size and left.max() > 5 else 0.0
    freq_r = _bar_to_hz(int(np.argmax(right)), _HALF_N) if right.size and right.max() > 5 else 0.0
    allb = (left + right)
    if allb.sum() > 1:
        idx = np.arange(_HALF_N)
        cen_idx = float((idx * allb).sum() / allb.sum())
        centroid = _bar_to_hz(cen_idx, _HALF_N)
    else:
        centroid = 0.0

    s = _meter_smooth
    a = 0.3
    for k, v in (("rms_l", rms_l), ("rms_r", rms_r), ("peak", peak),
                 ("peak_l", peak_l), ("peak_r", peak_r), ("bass", bass_ratio),
                 ("bal_l", bal_l), ("bal_r", bal_r), ("bal_pct", bal_pct),
                 ("freq_l", freq_l), ("freq_r", freq_r), ("centroid", centroid)):
        s[k] += (v - s[k]) * a

    GREEN = (60, 230, 90)
    GREY = (70, 75, 70)
    DARK = (18, 22, 18)
    LBL = (0, 210, 210)   # camgobegi
    TITLE = (0, 210, 210)   # camgobegi

    def to_db(v):
        if v <= 0.0001:
            return -60.0
        return max(-60.0, 20.0 * math.log10(v))

    page = _state.get("meter_page", 0)

    if page == 0:
        panels = [
        ("RMS SEVIYE", [("Ch1", s["rms_l"], f"{to_db(s['rms_l']):.1f}", "dB"),
                        ("Ch2", s["rms_r"], f"{to_db(s['rms_r']):.1f}", "dB")]),
        ("FREKANS", [("Ch1", min(1.0, s["freq_l"]/16000), f"{s['freq_l']/1000:.3f}", "kHz"),
                     ("Ch2", min(1.0, s["freq_r"]/16000), f"{s['freq_r']/1000:.3f}", "kHz")]),
        ("STEREO DENGE", [("Sol", s["bal_l"], f"{100-s['bal_pct']:.0f}", "%"),
                          ("Sag", s["bal_r"], f"{s['bal_pct']:.0f}", "%")]),
        ("MERKEZ", [("Frk", min(1.0, s["centroid"]/16000), f"{s['centroid']/1000:.3f}", "kHz"),
                    ("Bas", s["bass"], f"{s['bass']*100:.0f}", "%")]),
        ]
    else:
        # ---- SAYFA 2: ses analizi ----
        # Dinamik aralik (peak - rms, dB cinsinden pozitif fark)
        dr_l = max(0.0, to_db(s["peak_l"]) - to_db(s["rms_l"]))
        dr_r = max(0.0, to_db(s["peak_r"]) - to_db(s["rms_r"]))
        # Crest faktoru (peak/rms orani, ~1..4)
        crest_l = (s["peak_l"] / s["rms_l"]) if s["rms_l"] > 0.001 else 0.0
        crest_r = (s["peak_r"] / s["rms_r"]) if s["rms_r"] > 0.001 else 0.0
        # Stereo genislik: sol-sag enerji farkinin mutlak orani (0=mono, 1=cok genis)
        _el = float(left.sum()); _er = float(right.sum())
        width = abs(_el - _er) / (_el + _er) if (_el + _er) > 1 else 0.0
        # Tiz enerjisi: yuksek bantlarin orani (bass'in tersi mantigi)
        if left.size and right.size:
            _allb2 = left + right
            _tot2 = float(_allb2.sum())
            _treble = float(_allb2[_HALF_N*3//4:].sum())
            treble_ratio = (_treble / _tot2) if _tot2 > 1 else 0.0
        else:
            treble_ratio = 0.0

        # yumusatma (sayfa2 alanlari)
        for k, v in (("dr", (dr_l+dr_r)/2), ("crest", (crest_l+crest_r)/2),
                     ("width", width), ("treble", treble_ratio)):
            s.setdefault(k, 0.0)
            s[k] += (v - s[k]) * a

        panels = [
            ("DINAMIK", [("Arl", min(1.0, s["dr"]/40), f"{s['dr']:.1f}", "dB"),
                         ("Crs", min(1.0, s["crest"]/4), f"{s['crest']:.2f}", "x")]),
            ("STEREO", [("Gen", min(1.0, s["width"]), f"{s['width']*100:.0f}", "%"),
                        ("Bal", s["bal_r"], f"{s['bal_pct']:.0f}", "%")]),
            ("ENERJI", [("Tiz", min(1.0, s["treble"]*3), f"{s['treble']*100:.0f}", "%"),
                        ("Bas", s["bass"], f"{s['bass']*100:.0f}", "%")]),
        ]  # SISTEM 4. panel yerine asagida metin (bos bar yok)

    # PORTRE: 4 blok dikey eksende (H boyunca) alt alta. Her blok W genisligini kullanir.
    # LCD cevirince: 2x2 grid (ust-alt ikili, sol-sag ikili) gibi gorunur.
    N = len(panels)
    block_h = HEIGHT // 4          # HER ZAMAN 4 bolme (sayfa2'de 4. ceyrek sisteme kalir)
    tfont = _meter_font(30)
    lblf = _meter_font(30)
    numf = _meter_font(40)
    unitf = _meter_font(22)

    for pi, (title, rows) in enumerate(panels):
        by = pi * block_h
        row_h = (block_h - 70) // 2
        # Grup basligi: bu blogun 2 barinin DIKEY ortasina hizali (cevirince bar altinda ortada)
        group_cy = by + 60 + row_h  # iki satirin ortasi (grup merkezi)
        ts = tfont.render(title, True, TITLE)
        # Baslik LCD'de EN ALTTA olsun (deger tarafinin da altinda)
        _blit_rot(surf, ts, 25, group_cy)
        for ri, (label, val, num, unit) in enumerate(rows):
            ry = by + 60 + ri * row_h
            cy = ry + row_h // 2
            # Etiket (Ch1/Ch2/Sol/Sag...) - cevirince barin USTUNDE (yer degisti)
            _blit_rot(surf, lblf.render(label, True, LBL), 70, cy)
            # Yatay bar (cevirince dikey bar)
            bar_x = 130
            num_area = 120
            bar_w_full = WIDTH - bar_x - num_area
            bar_h = 26
            bar_y = cy - bar_h // 2
            v = max(0.0, min(1.0, val))
            pygame.draw.rect(surf, DARK, (bar_x, bar_y, bar_w_full, bar_h))
            fill_w = int(bar_w_full * v)
            pygame.draw.rect(surf, GREEN, (bar_x, bar_y, fill_w, bar_h))
            pygame.draw.rect(surf, GREY, (bar_x + fill_w, bar_y, bar_w_full - fill_w, bar_h))
            # Sayi + birim - cevirince barin ALTINDA (yer degisti)
            _blit_rot(surf, numf.render(num, True, GREEN), WIDTH - 60, cy)
            _blit_rot(surf, unitf.render(unit, True, GREEN), WIDTH - 100, cy)

    # Sayfa 2: 4. ceyrek (alt) komple SISTEM bilgisi - ferah metin
    if page == 1:
        si = _refresh_sysaudio()
        CAM = (0, 210, 210)
        titlef = _meter_font(30)
        bigf = _meter_font(38)
        smallf = _meter_font(28)
        # 4. ceyrek alani
        sys_by = 3 * (HEIGHT // 4)
        qh = HEIGHT // 4
        # Baslik SISTEM (en altta, diger basliklarla ayni x=25 hizasinda)
        gcy = sys_by + qh // 2
        _blit_rot(surf, titlef.render("SISTEM", True, CAM), 25, gcy)
        # 4 bilgi satiri, ceyrek boyunca esit dagit (LCD'de yatay dizilir)
        # _blit_rot: x=dikey konum (LCD yatay), cy=yatay konum (LCD dikey)
        # Satirlari x ekseninde (LCD'de yukaridan asagi) esit aralikli koy
        info_x = [WIDTH - 90, WIDTH - 140, WIDTH - 200, WIDTH - 250]
        c1 = sys_by + qh // 2
        _blit_rot(surf, bigf.render(f"{si['rate']}", True, GREEN), info_x[0], c1)
        _blit_rot(surf, smallf.render(f"{si['fmt']} {si['ch']}", True, CAM), info_x[1], c1)
        _blit_rot(surf, bigf.render(f"Ses: {si['vol']}", True, GREEN), info_x[2], c1)
        _blit_rot(surf, smallf.render(si["dev"], True, CAM), info_x[3], c1)



def draw_sysmon(surf, d):
    """PORTRE sistem monitoru: 14 bar dikey (LCD cevirince yan yana + yukari dolan).
    Portrede: barlar alt alta, her biri yatay dolar; yazilar -90 dondurulur."""
    W, H = surf.get_size()  # 462 x 1920
    surf.fill((8, 10, 8))
    GREEN = (60, 230, 90); DARK = (20, 24, 20); GREY = (60, 66, 60)
    WHITE = (80, 220, 215); DIM = (90, 175, 175)

    def col(t):
        if t is None: return GREY
        if t < 65: return GREEN
        if t < 80: return (245, 210, 60)
        return (235, 60, 40)

    def net_fmt(mbps):
        if mbps is None: return ("--", "kB/s", 0)
        if mbps < 1.0: return (f"{mbps*1024:.0f}", "kB/s", mbps/100.0)
        return (f"{mbps:.1f}", "MB/s", mbps/100.0)

    cpu_t = d.get("cpu_pkg"); gpu_j = d.get("gpu_junction")
    vrm = d.get("mb_vrm"); pch = d.get("mb_pch")
    use = d.get("cpu_usage"); gpu_u = d.get("gpu_usage")
    ram = d.get("ram_pct"); frq = d.get("cpu_freq")
    cpu_p = d.get("cpu_power"); gpu_p = d.get("gpu_power")
    pump = d.get("fan_pump"); sfan = d.get("fan_sys1")
    nd = d.get("net_down"); nu = d.get("net_up")
    nd_t, nd_u, nd_f = net_fmt(nd)
    nu_t, nu_u, nu_f = net_fmt(nu)

    bars = [
        ("CPU",  f"{cpu_t:.0f}"  if cpu_t is not None else "--", "C",  (cpu_t/100.0)  if cpu_t else 0, col(cpu_t)),
        ("GPU",  f"{gpu_j:.0f}"  if gpu_j is not None else "--", "C",  (gpu_j/110.0)  if gpu_j else 0, col(gpu_j)),
        ("VRM",  f"{vrm:.0f}"    if vrm is not None else "--",   "C",  (vrm/100.0)    if vrm else 0,   col(vrm)),
        ("PCH",  f"{pch:.0f}"    if pch is not None else "--",   "C",  (pch/90.0)     if pch else 0,   col(pch)),
        ("CPU%", f"{use:.0f}"    if use is not None else "--",   "%",  (use/100.0)    if use is not None else 0, GREEN),
        ("GPU%", f"{gpu_u:.0f}"  if gpu_u is not None else "--", "%",  (gpu_u/100.0)  if gpu_u is not None else 0, GREEN),
        ("RAM",  f"{ram:.0f}"    if ram is not None else "--",   "%",  (ram/100.0)    if ram is not None else 0, GREEN),
        ("GHz",  f"{frq/1000:.1f}" if frq else "--",             "",   (frq/5700.0)   if frq else 0, GREEN),
        ("C-W",  f"{cpu_p:.0f}"  if cpu_p is not None else "--", "W",  (cpu_p/250.0)  if cpu_p else 0, GREEN),
        ("G-W",  f"{gpu_p:.0f}"  if gpu_p is not None else "--", "W",  (gpu_p/350.0)  if gpu_p else 0, GREEN),
        ("Pump", f"{pump:.0f}"   if pump else "0",               "rpm",(pump/3000.0)  if pump else 0, GREEN),
        ("SFan", f"{sfan:.0f}"   if sfan else "0",               "rpm",(sfan/3000.0)  if sfan else 0, GREEN),
        ("Indir",nd_t, nd_u, nd_f, GREEN),
        ("Yukle",nu_t, nu_u, nu_f, GREEN),
    ]

    n = len(bars)
    margin = 24
    slot_h = (H - 2*margin) // n        # her bar dikey yer (1920 boyunca)
    bar_thick = int(slot_h * 0.42)      # bar kalinligi
    # Portrede yatay: sol=etiket, orta=bar, deger bar sonunda
    lbl_x = 6
    bar_left = 70
    val_w = 88                          # deger + birim icin sag alan
    bar_max_w = W - bar_left - val_w

    vfont = pygame.font.SysFont("DejaVu Sans", 40, bold=True)
    ufont = pygame.font.SysFont("DejaVu Sans", 20, bold=True)
    lfont = pygame.font.SysFont("DejaVu Sans", 26, bold=True)

    for i, (lbl, vtxt, unit, frac, color) in enumerate(bars):
        frac = max(0.0, min(1.0, frac))
        sy = margin + i * slot_h
        cy = sy + slot_h // 2
        by = cy - bar_thick // 2
        # bar arka plan + dolu (soldan saga)
        pygame.draw.rect(surf, DARK, (bar_left, by, bar_max_w, bar_thick))
        pygame.draw.rect(surf, color, (bar_left, by, int(bar_max_w * frac), bar_thick))
        pygame.draw.rect(surf, GREY, (bar_left, by, bar_max_w, bar_thick), 1)
        # etiket (sol, dondurulmus)
        _blit_rot(surf, lfont.render(lbl, True, WHITE), lbl_x + 14, cy)
        # deger (sag, dondurulmus)
        _blit_rot(surf, vfont.render(vtxt, True, color), W - val_w//2 - 6, cy)
        # birim (degerin altinda - portrede degerin sagi)
        if unit:
            _blit_rot(surf, ufont.render(unit, True, DIM), W - 16, cy)


# ==================== KLAVYE (terminal stdin) ====================

def run_tray_icon():
    """KDE sistem tepsisi ikonu + menu (PyQt5, xcb). Wayland'da XWayland uzerinden."""
    os.environ["QT_QPA_PLATFORM"] = "xcb"  # Wayland'da tepsi ikonu icin XWayland
    from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QActionGroup
    from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
    from PyQt5.QtCore import QTimer

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    def make_icon():
        pm = QPixmap(64, 64)
        pm.fill(QColor(20, 22, 26))
        p = QPainter(pm)
        p.setBrush(QColor(60, 220, 120)); p.setPen(QColor(60, 220, 120))
        x = 6
        for h in [20, 38, 28, 48, 34, 44, 24]:
            p.drawRect(x, 58 - h, 7, h); x += 9
        p.end()
        return QIcon(pm)

    global _tray_ref, _menu_ref
    tray = QSystemTrayIcon(make_icon())
    tray.setToolTip("Vumeter LCD")
    menu = QMenu()
    _tray_ref = tray; _menu_ref = menu  # GC korumasi

    # Mac tarzi: her mod alt menusunde temalar, tiklayinca mod+tema secilir
    mode_actions = {}  # {mod: ana QAction} - isaret guncelleme icin

    # Spektrum -> renk temalari
    spek = menu.addMenu("Spektrum")
    def mk_spek_theme(idx):
        def _f():
            _state["mode"] = "Spektrum"; _state["theme_idx"] = idx
        return _f
    for i, tn in enumerate(COLOR_THEME_NAMES):
        a = QAction(tn, menu); a.triggered.connect(mk_spek_theme(i)); spek.addAction(a)
    mode_actions["Spektrum"] = spek.menuAction()

    # LED Spektrum -> LED temalari
    leds = menu.addMenu("LED Spektrum")
    def mk_led_theme(idx, mode):
        def _f():
            _state["mode"] = mode; _state["led_theme_idx"] = idx
            _led_texture_cache["surf"] = None
        return _f
    for i, tn in enumerate(LED_THEME_NAMES):
        a = QAction(tn, menu); a.triggered.connect(mk_led_theme(i, "LED Spektrum")); leds.addAction(a)
    mode_actions["LED Spektrum"] = leds.menuAction()

    # LED Nokta -> LED temalari
    ledn = menu.addMenu("LED Nokta")
    for i, tn in enumerate(LED_THEME_NAMES):
        a = QAction(tn, menu); a.triggered.connect(mk_led_theme(i, "LED Nokta")); ledn.addAction(a)
    mode_actions["LED Nokta"] = ledn.menuAction()

    # VU Metre -> kadranlar
    vum = menu.addMenu("VU Metre")
    def mk_vu_dial(idx):
        def _f():
            _state["mode"] = "VU Metre"; _state["vu_dial_idx"] = idx
            _vu_scaled_cache.clear()
        return _f
    for i in range(len(VU_DIALS)):
        a = QAction(f"Kadran {i+1}", menu); a.triggered.connect(mk_vu_dial(i)); vum.addAction(a)
    mode_actions["VU Metre"] = vum.menuAction()

    # Sistem Monitoru -> alt menu yok, direkt
    olcp = menu.addMenu("Olcum Paneli")
    pg_group = QActionGroup(menu); pg_group.setExclusive(True)
    def mk_meter_page(idx):
        def _f():
            _state["mode"] = "Olcum Paneli"; _state["meter_page"] = idx
        return _f
    for i, pn in enumerate(("Seviyeler", "Analiz")):
        a = QAction(pn, menu, checkable=True)
        a.setChecked(_state.get("meter_page", 0) == i)
        a.triggered.connect(mk_meter_page(i))
        pg_group.addAction(a); olcp.addAction(a)

    smon = QAction("Sistem Monitoru", menu)
    smon.triggered.connect(lambda: _state.__setitem__("mode", "Sistem Monitoru"))
    menu.addAction(smon)
    menu.addSeparator()

    def next_theme():
        m = _state["mode"]
        if m == "Spektrum":
            _state["theme_idx"] = (_state["theme_idx"] + 1) % len(COLOR_THEME_NAMES)
        elif m in ("LED Spektrum", "LED Nokta"):
            _state["led_theme_idx"] = (_state["led_theme_idx"] + 1) % len(LED_THEME_NAMES)
            _led_texture_cache["surf"] = None
        elif m == "VU Metre":
            _state["vu_dial_idx"] = (_state["vu_dial_idx"] + 1) % len(VU_DIALS)
            _vu_scaled_cache.clear()

    br_menu = menu.addMenu("Parlaklik")
    br_group = QActionGroup(menu); br_group.setExclusive(True)
    def mk_br(p):
        def _f():
            _state["brightness"] = p; _state["brightness_changed"] = True
        return _f
    for p in (100, 75, 50, 25):
        a = QAction(f"%{p}", menu, checkable=True)
        a.setChecked(_state["brightness"] == p)
        a.triggered.connect(mk_br(p))
        br_group.addAction(a); br_menu.addAction(a)

    menu.addSeparator()

    # Hakkinda
    def show_about():
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel, QPushButton
        from PyQt5.QtCore import Qt
        import datetime
        dlg = QDialog()
        dlg.setWindowTitle("Hakkinda")
        dlg.setFixedWidth(420)
        dlg.setStyleSheet("""
            QDialog { background: #16181d; }
            QLabel { color: #e6e8ea; }
            QPushButton {
                background: #2b7a4b; color: white; border: none;
                border-radius: 6px; padding: 8px 24px; font-weight: bold;
            }
            QPushButton:hover { background: #35935b; }
        """)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(28, 24, 28, 22)
        lay.setSpacing(6)

        title = QLabel("VINTAGE AUDIO CONSOLE")
        title.setStyleSheet("font-size: 22px; font-weight: 800; color: #4ce08a; letter-spacing: 1px;")
        lay.addWidget(title)

        ver = QLabel(f"Surum {__version__}")
        ver.setStyleSheet("font-size: 13px; color: #9aa0a6; margin-bottom: 8px;")
        lay.addWidget(ver)

        desc = QLabel("Thermalright Trofeo Vision LCD icin\nses gorsellestirme ve sistem monitoru")
        desc.setStyleSheet("font-size: 14px; color: #cdd2d6; line-height: 1.4;")
        lay.addWidget(desc)

        modes = QLabel("Spektrum  •  LED  •  VU Metre  •  Sistem Monitoru")
        modes.setStyleSheet("font-size: 12px; color: #6cc98d; margin-top: 10px; margin-bottom: 10px;")
        lay.addWidget(modes)

        line = QLabel()
        line.setFixedHeight(1); line.setStyleSheet("background: #2a2e35;")
        lay.addWidget(line)

        tech = QLabel("cava (PipeWire) + trcc-linux\nDebian 13 / KDE Wayland")
        tech.setStyleSheet("font-size: 11px; color: #7a8087; margin-top: 8px;")
        lay.addWidget(tech)

        author = QLabel(f"<span style='color:#9aa0a6'>Gelistiren:</span> <b style='color:#e6e8ea'>pii</b>"
                        f"<span style='color:#7a8087'>  •  {datetime.date.today().strftime('%d.%m.%Y')}</span>")
        author.setTextFormat(Qt.RichText)
        author.setStyleSheet("font-size: 12px; margin-top: 6px;")
        lay.addWidget(author)

        btn = QPushButton("Tamam")
        btn.clicked.connect(dlg.accept)
        lay.addWidget(btn, alignment=Qt.AlignRight)
        lay.setContentsMargins(28, 24, 28, 22)

        dlg.exec_()
    ab = QAction("Hakkinda", menu)
    ab.triggered.connect(show_about)
    menu.addAction(ab)

    qa = QAction("Cikis", menu)
    def do_quit():
        _state["running"] = False; tray.hide(); app.quit()
    qa.triggered.connect(do_quit); menu.addAction(qa)

    tray.setContextMenu(menu)


    tray.show()

    def sync():
        if not _state["running"]:
            app.quit()
    timer = QTimer(); timer.timeout.connect(sync); timer.start(500)
    app.exec_()


def keyboard_loop():
    """Terminal stdin'den harf harf oku (raw mode)."""
    import tty, termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while _state["running"]:
            ch = sys.stdin.read(1)
            if not ch: continue
            if ch == "1":
                _state["mode"] = "Spektrum"; print("\rMod: Spektrum              ")
            elif ch == "2":
                _state["mode"] = "LED Spektrum"; print("\rMod: LED Spektrum          ")
            elif ch == "3":
                _state["mode"] = "LED Nokta"; print("\rMod: LED Nokta             ")
            elif ch == "4":
                _state["mode"] = "VU Metre"; print("\rMod: VU Metre              ")
            elif ch == "5":
                _state["mode"] = "Sistem Monitoru"; print("\rMod: Sistem Monitoru       ")
            elif ch == "6":
                _state["mode"] = "Olcum Paneli"; print("\rMod: Olcum Paneli          ")
            elif ch in ("c", "C"):
                _state["ch_layout"] = (_state["ch_layout"] + 1) % 4
                _names = ("0: L+R", "1: L+R'", "2: L'+R", "3: L'+R'")
                print(f"\rKanal dizilimi -> {_names[_state['ch_layout']]}          ")
            elif ch == "\t":
                # Moda gore ilgili tema listesinde ilerle
                if _state["mode"] == "Spektrum":
                    _state["theme_idx"] = (_state["theme_idx"] + 1) % len(COLOR_THEME_NAMES)
                    print(f"\rTema: {COLOR_THEME_NAMES[_state['theme_idx']]}          ")
                elif _state["mode"] in ("LED Spektrum", "LED Nokta"):
                    _state["led_theme_idx"] = (_state["led_theme_idx"] + 1) % len(LED_THEME_NAMES)
                    _led_texture_cache["surf"] = None
                    print(f"\rTema: {LED_THEME_NAMES[_state['led_theme_idx']]}          ")
                elif _state["mode"] == "VU Metre":
                    _state["vu_dial_idx"] = (_state["vu_dial_idx"] + 1) % len(VU_DIALS)
                    _vu_scaled_cache.clear()
                    print(f"\rVU kadran: {_state['vu_dial_idx']+1}/{len(VU_DIALS)}          ")
                elif _state["mode"] == "Olcum Paneli":
                    _state["meter_page"] = (_state["meter_page"] + 1) % 2
                    print(f"\rOlcum sayfa: {_state['meter_page']+1}/2          ")
                elif _state["mode"] == "VU Metre":
                    _state["vu_dial_idx"] = (_state["vu_dial_idx"] + 1) % len(VU_DIALS)
                    _vu_scaled_cache.clear()
                    print(f"\rVU kadran: {_state['vu_dial_idx']+1}/{len(VU_DIALS)}          ")
                else:
                    print("\r(Bu modda tema yok)                    ")
            elif ch in ("w", "W"):
                # Parlaklik dongusu: 100 -> 75 -> 50 -> 25 -> 100
                levels = [100, 75, 50, 25]
                cur = _state["brightness"]
                idx = levels.index(cur) if cur in levels else 0
                _state["brightness"] = levels[(idx + 1) % len(levels)]
                _state["brightness_changed"] = True
                print(f"\rParlaklik: %{_state['brightness']}          ")
            elif ch in ("q", "Q", "\x03"):  # Ctrl-C de sayilir
                _state["running"] = False
                print("\rCikiliyor...")
                break
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


# ==================== ANA DONGU ====================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fps", type=int, default=8, help="LCD kare hizi")
    parser.add_argument("--debug", action="store_true", help="Frame/FPS bilgisi goster")
    parser.add_argument("--tray", action="store_true", help="Sistem tepsisi ikonu ile calis (terminal gizli)")
    args = parser.parse_args()

    print(f"""
LCD Ses Gorsellestirme + Sistem Monitoru (Linux)
================================================
  1     -> Spektrum
  2     -> LED Spektrum
  3     -> LED Nokta
  4     -> VU Metre
  5     -> Sistem Monitoru
 6     -> Olcum Paneli
  TAB   -> Renk temasi / VU kadran (aktif moda gore)
  W     -> Parlaklik (100/75/50/25)
  Q     -> Cikis

Ilk mod: Spektrum. FPS: 12 (bar) / 10 (panel) / 5 (sysmon)
""")

    # Baslamadan once zombi trcc sureclerini temizle (USB kilidini onle)
    # DIKKAT: kendi surecimizi (vumeter_trcc_linux) oldurmemek icin spesifik pattern
    subprocess.run(["pkill", "-9", "-f", "trcc shell"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "-f", "trcc daemon"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "-f", "trcc serve"], stderr=subprocess.DEVNULL)
    subprocess.run(["pkill", "-9", "-f", "trcc api"], stderr=subprocess.DEVNULL)
    try: os.remove(f"/run/user/{os.getuid()}/trcc.sock")
    except Exception: pass
    time.sleep(1.0)

    pygame.init()
    surf = pygame.Surface((WIDTH, HEIGHT))

    sender = TRCCSender(DEVICE_KEY)
    cava = CavaReader()
    mon = sysmon.SysMonitor()

    # Temiz kapanma: sinyal gelince shell'e exit gonder, USB'yi duzgun birak
    import signal as _signal
    def _clean_exit(*a):
        _state["running"] = False
    _signal.signal(_signal.SIGTERM, _clean_exit)
    def _fps_for(m):
        # NOT: panel 24+ FPS'te uzun surede kilitlenebiliyor (Tem 2026 tespiti).
        # 12 FPS haftalarca kanitlanmis kararli deger.
        if m == "Sistem Monitoru": return 5
        if m == "Olcum Paneli": return 10
        return 12

    def render_loop():
        frames = 0; t0 = time.time()
        last_sound = time.time()
        try:
            while _state["running"]:
                t = time.time()
                mode = _state["mode"]
                dt = 1.0 / _fps_for(mode)
                # Ses seviyesi kontrolu (idle icin) - sadece ses modlari
                snap = cava.snapshot()
                # Bar modlari icin kanal duzeni (masaustunde GOZLE dogrulanan formul):
                # sol_yari + sag_yari[::-1] -> iki yarida da bas ayni ucta.
                # VU ve Olcum Paneli HAM veri kullanir (frekans hesaplari icin gerekli).
                if len(snap) >= _HALF_N * 2:
                    _L = snap[:_HALF_N]; _R = snap[_HALF_N:_HALF_N*2]
                    _lay = _state.get("ch_layout", 0)
                    if _lay == 0:   snap_bars = _L + _R
                    elif _lay == 1: snap_bars = _L + _R[::-1]
                    elif _lay == 2: snap_bars = _L[::-1] + _R
                    else:           snap_bars = _L[::-1] + _R[::-1]
                else:
                    snap_bars = snap
                if mode not in ("Sistem Monitoru", "Olcum Paneli"):
                    if snap and max(snap) > 8:
                        last_sound = t
                    if t - last_sound > IDLE_THRESHOLD:
                        draw_idle_screen(surf, t - t0)
                        fpath = f"/tmp/lcd_frame_idle{int(t*2) % 3}.png"
                        pygame.image.save(surf, fpath)
                        sender.send(fpath)
                        slp = dt - (time.time() - t)
                        if slp > 0: time.sleep(slp)
                        continue
                if mode == "Spektrum":
                    draw_spectrum(surf, snap_bars, COLOR_THEME_NAMES[_state["theme_idx"]], _fps_for(mode))
                elif mode == "LED Spektrum":
                    draw_led_spectrum(surf, snap_bars, _fps_for(mode), "rect")
                elif mode == "LED Nokta":
                    draw_led_spectrum(surf, snap_bars, _fps_for(mode), "dot")
                elif mode == "VU Metre":
                    draw_vu_meter(surf, cava.snapshot())
                elif mode == "Olcum Paneli":
                    draw_meter_panel(surf, cava.snapshot())
                elif mode == "Sistem Monitoru":
                    draw_sysmon(surf, mon.snapshot())
                else:
                    surf.fill((10, 10, 12))
                if _state.get("brightness_changed"):
                    sender.set_brightness(_state["brightness"])
                    _state["brightness_changed"] = False
                if mode == "Sistem Monitoru":
                    fpath = f"/tmp/lcd_frame_s{frames % 3}.png"
                elif mode == "Olcum Paneli":
                    fpath = f"/tmp/lcd_frame_m{frames % 3}.png"
                else:
                    fpath = f"/tmp/lcd_frame_{frames % 3}.png"
                _t_save = time.time()
                pygame.image.save(surf, fpath)
                _t_send = time.time()
                sender.send(fpath)
                _t_done = time.time()
                # Takilma dedektoru: kare 0.5 sn'yi asarsa suclu kismi logla
                if _t_done - t > 0.5:
                    try:
                        with open("/tmp/lcd_stall.log", "a") as _lf:
                            _lf.write(f"{time.strftime('%H:%M:%S')} TOPLAM={_t_done-t:.2f}s "
                                      f"cizim={_t_save-t:.2f}s png={_t_send-_t_save:.2f}s "
                                      f"usb={_t_done-_t_send:.2f}s mod={mode}\n")
                    except Exception:
                        pass
                frames += 1
                if args.debug and frames % 100 == 0:
                    fps_now = frames / (time.time() - t0)
                    sys.stderr.write(f"\r[{frames} frame, {fps_now:.1f} FPS, mod: {mode}]  ")
                    sys.stderr.flush()
                slp = dt - (time.time() - t)
                if slp > 0: time.sleep(slp)
        finally:
            _state["running"] = False
            mon.stop(); cava.stop(); sender.close()
            pygame.quit()

    if args.tray:
        rt = threading.Thread(target=render_loop, daemon=True); rt.start()
        try:
            run_tray_icon()
        except Exception as e:
            import traceback
            sys.stderr.write(f"[TRAY] HATA: {e}\n{traceback.format_exc()}")
            keyboard_loop()
        _state["running"] = False
        time.sleep(0.5)
        print("\nCikildi.")
    else:
        kb = threading.Thread(target=keyboard_loop, daemon=True); kb.start()
        render_loop()
        print("\nCikildi.")


if __name__ == "__main__":
    main()
