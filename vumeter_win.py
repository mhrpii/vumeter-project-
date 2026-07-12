#!/usr/bin/env python3
"""VintageAudioConsole - WINDOWS surumu.
Bilgisayarda CALAN sesi WASAPI loopback ile yakalar (cava gerekmez).
Cikis aygiti otomatik secilir ve degisirse otomatik takip edilir."""
import subprocess
import numpy as np
# --- console=False (pencere modu exe): stdout/stderr None olur -> print() patlar
class _NullIO:
    def write(self, *a, **k):
        pass

    def flush(self, *a, **k):
        pass


if sys.stdout is None:
    sys.stdout = _NullIO()
if sys.stderr is None:
    sys.stderr = _NullIO()

import pygame
import sys
import math
import os
import shutil
import threading
import time

# --- Sistem Monitoru alt-modu: "--sysmon" argumaniyla acilirsa sadece
#     monitor penceresini calistir ve cik (ayni .app ikinci pencere icin
#     kendini bu argumanla cagirir). pygame ana penceresi acilmadan once! ---
if "--sysmon" in sys.argv:
    try:
        import sysmon_window
        sysmon_window.main()
    except Exception as _e:
        print("sysmon_window hata:", _e)
    sys.exit(0)

# ============================================================
#  KLAVYE KISAYOLLARI:
#    1 = Spektrum gorunumu      2 = Klasik VU      3 = LED Spektrum
#    TAB = renk temasi degistir (Spektrum & LED Spektrum'da)
#    Q veya ESC = cikis
# ============================================================

def get_resource_path(relative_path):
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, relative_path)

WIDTH, HEIGHT = 1440, 900
FPS = 60
__version__ = "1.0.0"

NUM_BARS = 204   # 256'dan %20 azaltildi (cift sayi: kanal basina 102)
HALF_BARS = NUM_BARS // 2

# --- RENK PALETLERI ---
COLOR_THEMES = {
    "Yesil": [(0, 170, 0), (255, 255, 0), (255, 0, 0)],
    "Neon":  [(0, 255, 255), (255, 0, 255), (255, 255, 0)],
    "Mor":   [(120, 0, 200), (220, 60, 220), (255, 220, 255)],
}
COLOR_THEME_NAMES = list(COLOR_THEMES.keys())

LED_THEMES = {
    "Camgobegi": [(10, 90, 70), (40, 200, 190), (120, 255, 235)],
    "Yesil-Sari-Kirmizi": [(40, 220, 40), (250, 200, 30), (235, 45, 30)],
    "Mavi": [(20, 60, 200), (60, 140, 255), (170, 220, 255)],
    "Mor": [(120, 0, 200), (210, 60, 230), (255, 210, 255)],
}
LED_THEME_NAMES = list(LED_THEMES.keys())

# Durum (calisma sirasinda klavye ile degisir)
state = {
    "mode": "Spektrum",        # "Spektrum" | "Klasik VU" | "LED Spektrum"
    "theme_idx": 0,            # COLOR_THEME_NAMES index'i
    "led_theme_idx": 0,        # LED_THEME_NAMES index'i
    "vu_dial_idx": 0,          # VU_DIALS index'i (TAB ile VU Metre modunda)
    "ch_layout": 0,   # Windows: 0 = bas solda, tiz sagda (C ile degistir)            # C tusu: kanal dizilimi (0:L+R, 1:L+R', 2:L'+R, 3:L'+R')
    "quit": False,             # tepsi menusunden cikis istegi
    "last_sound": 0.0,         # son ses zamani (idle icin)
}

# pygame.init() ses mixer'ini da baslatir -> mixer bir ses aygiti acar ->
# PipeWire/KDE "ses aygiti degisti" OSD'sini tetikler (LG monitorde profil
# listesi belirir). Biz ses CALMIYORUZ (cava'dan okuyoruz), mixer'a gerek yok.
# --- COM'u EN BASTA STA baslat (Qt tepsi ikonu icin SART) ---
try:
    import ctypes as _ct
    _ct.windll.ole32.CoInitializeEx(None, 0x2)   # 0x2 = STA
except Exception:
    pass

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
pygame.display.init()
pygame.font.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
pygame.display.set_caption("Vintage Audio Console Pro")
clock = pygame.time.Clock()

font_main = pygame.font.SysFont("serif", 24, bold=True)
font_hint = pygame.font.SysFont("Menlo", 16)
font_vu_label = pygame.font.SysFont("Menlo", 18)
font_vu_power = pygame.font.SysFont("Menlo", 22, bold=True)

# --- CAVA CONFIG ---
# ============================================================
#  SES YAKALAMA (Windows): WASAPI loopback - bilgisayarda CALAN sesi dinler.
#  Linux'taki cava'nin yerini alir. Cikis aygiti (Scarlett/kulaklik/HDMI)
#  OTOMATIK secilir ve degisirse otomatik takip edilir.
# ============================================================
# soundcard'i BURADA import etmiyoruz: import aninda ana thread'de COM'u MTA
# baslatir, Qt ise tepsi ikonu icin STA ister -> tray TIKLANMAZ olur.
# Cozum: sadece ses thread'i icinde import edilir (asagida).
sc = None

SAMPLE_RATE = 48000
BLOCK = 8192            # FFT penceresi (buyuk = bas barlari AYRISIR, grup hareket biter)
# --- Bar ayarlari (Linux'taki cava'nin sabit sensitivity mantigi) ---
GAIN = 2.0              # bar yuksekligi (dusukse buyut, tavana yapisirsa kucult)
SCALE = 52.0            # log olcek carpani
# --- ZAMAN TABANLI hareket (kume kume atlamayi onler, hizi kontrol eder) ---
TAU_SPEC = 0.03         # FFT gurultu yumusatma (sn) - buyuk = daha yumusak
TAU_UP   = 0.09         # yukselme suresi (sn) - buyuk = daha YAVAS yukselir
FALL_PS  = 165.0        # dusme hizi (birim/sn, 255 uzerinden) - kucuk = YAVAS duser

_FREQ_MIN, _FREQ_MAX = 40.0, 16000.0
_freq_bins = np.fft.rfftfreq(BLOCK, 1.0 / SAMPLE_RATE)
_edges = np.logspace(np.log10(_FREQ_MIN), np.log10(_FREQ_MAX), HALF_BARS + 1)
# Bant matrisi: (HALF_BARS x bin) - tek matris carpimiyla tum bantlar (HIZLI)
_BM = np.zeros((HALF_BARS, len(_freq_bins)), dtype=np.float32)
for _i in range(HALF_BARS):
    _m = (_freq_bins >= _edges[_i]) & (_freq_bins < _edges[_i + 1])
    if not _m.any():
        _nearest = int(np.argmin(np.abs(_freq_bins - _edges[_i])))
        _m = np.zeros_like(_freq_bins, dtype=bool)
        _m[_nearest] = True
    _BM[_i, _m] = 1.0 / _m.sum()   # ortalama icin agirlik
_window = np.hanning(BLOCK).astype(np.float32)
# yuksek frekanslari hafif yukselt (cava'nin gorunumune yaklastirir)
_tilt = np.linspace(1.0, 2.4, HALF_BARS).astype(np.float32)


def _default_speaker_name():
    """Windows varsayilan CIKIS aygitinin adi (otomatik secim icin)."""
    if sc is None:
        return None
    try:
        return sc.default_speaker().name
    except Exception:
        return None


class LoopbackReader:
    """WASAPI loopback okuyucu. cava'nin yerini alir; ayni arayuz: snapshot()
    -> NUM_BARS deger (0..255). Ilk yari = SOL kanal, ikinci yari = SAG kanal.
    Varsayilan cikis aygitini otomatik bulur, aygit degisirse otomatik gecer."""

    def __init__(self):
        self.bars = [0] * NUM_BARS
        self.updates = 0          # tani: saniyede kac kez guncellendi
        self._sm = np.zeros(NUM_BARS, dtype=np.float32)
        self._lock = threading.Lock()
        self._running = True
        self._dev_name = None
        self._raw = np.zeros(NUM_BARS, dtype=np.float32)   # yumusatilmis ham spektrum
        self._last_t = time.perf_counter()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    # ---- ic: tek blok -> 204 bar ----
    def _bars_from_block(self, block):
        """Kayan pencere (BLOCK ornek) -> 204 bar. Vektorize: tek FFT cagrisi
        (2 kanal birden) + tek matris carpimi -> hizli, takilma yok."""
        if block.ndim == 1:
            stereo = np.stack([block, block], axis=1)
        elif block.shape[1] == 1:
            stereo = np.repeat(block, 2, axis=1)
        else:
            stereo = block[:, :2]
        # (BLOCK, 2) pencereli -> FFT sutun bazli (axis=0) -> (bin, 2)
        win = stereo * _window[:, None]
        spec = np.abs(np.fft.rfft(win, axis=0)).astype(np.float32)   # (bin, 2)
        vals = (_BM @ spec) * _tilt[:, None]      # (HALF_BARS, 2)
        peak = float(vals.max()) if vals.size else 0.0
        out = np.concatenate([vals[:, 0], vals[:, 1]]).astype(np.float32)
        return out, peak

    def _loop(self):
        # COM'u BU thread'de baslat + soundcard'i BURADA import et
        # (ana thread temiz kalsin ki Qt tepsi ikonu STA alabilsin)
        global sc
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # APARTMENTTHREADED
        except Exception as _e:
            print("COM init uyarisi:", _e)
        try:
            import soundcard as _sc
            sc = _sc
        except Exception as _e:
            print("soundcard yuklenemedi:", _e)
            return

        while self._running:
            name = _default_speaker_name()
            if name is None:
                time.sleep(1.0)
                continue
            self._dev_name = name
            try:
                mic = sc.get_microphone(name, include_loopback=True)
                with mic.recorder(samplerate=SAMPLE_RATE, channels=2,
                                  blocksize=512) as rec:
                    print(f"Ses kaynagi: {name} (WASAPI loopback)")
                    last_check = time.time()
                    ring = np.zeros((BLOCK, 2), dtype=np.float32)   # kayan pencere
                    CHUNK = 480   # ~10ms @48kHz -> daha sik guncelleme (akici)
                    while self._running:
                        data = rec.record(numframes=CHUNK)
                        if data is None or len(data) == 0:
                            continue
                        d = np.asarray(data, dtype=np.float32)
                        if d.ndim == 1:
                            d = np.stack([d, d], axis=1)
                        elif d.shape[1] == 1:
                            d = np.repeat(d, 2, axis=1)
                        d = d[:, :2]
                        k = min(len(d), BLOCK)
                        ring = np.roll(ring, -k, axis=0)
                        ring[-k:] = d[-k:]
                        raw, peak = self._bars_from_block(ring)

                        # ZAMAN TABANLI yumusatma: veriler kume kume gelse bile
                        # hareket gecen sureye gore olcuklenir -> duzgun ve kontrollu
                        _now = time.perf_counter()
                        dt = min(max(_now - self._last_t, 0.001), 0.1)
                        self._last_t = _now

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

                        # aygit degisti mi (2 saniyede bir kontrol)
                        if time.time() - last_check > 5.0:
                            last_check = time.time()
                            cur = _default_speaker_name()
                            if cur and cur != self._dev_name:
                                print(f"Ses aygiti degisti -> {cur}")
                                break   # yeni aygitla yeniden baglan
            except Exception as e:
                import traceback
                print(f"Ses akisi hatasi: {e}")
                traceback.print_exc()
                print("2sn sonra yeniden denenecek...")
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


cava = LoopbackReader()   # ayni isim: kodun geri kalani degismesin

left_angle = 180.0
right_angle = 180.0

smooth_bars = np.zeros(NUM_BARS)
peak_bars = np.zeros(NUM_BARS)
peak_timers = np.zeros(NUM_BARS)

VINTAGE_AMBER = (245, 215, 150)
VINTAGE_RED = (210, 45, 45)
VINTAGE_GREEN = (40, 110, 70)

# Arka plan resmi (klasik dial icin, opsiyonel)
try:
    bg_path = get_resource_path('vu_bg.png')
    raw_vu_image = pygame.image.load(bg_path)
except (pygame.error, FileNotFoundError):
    raw_vu_image = None

# VU kadranlari (LCD surumuyle ayni tablo):
# (dosya, pivot_x, pivot_y, ibre_RGB, (orij_w, orij_h), alt_kirpma_frac, olcek)
VU_DIALS = [
    ("vu_bg.png",  0.500, 0.820, (210, 30, 30), (2366, 1792), 0.0,  1.0),
    ("vu_bg2.png", 0.503, 0.867, (20, 20, 20),  (2624, 1620), 0.0,  1.15),
    ("vu_bg3.png", 0.503, 0.655, (20, 20, 20),  (2400, 1790), 0.10, 1.18),
]
_vu_dial_cache = {}   # {idx: ham (kirpilmis) surface}

def _load_vu_dial(idx):
    """Kadran gorselini yukle (cache'li). Alt kirpma uygulanir."""
    if idx in _vu_dial_cache:
        return _vu_dial_cache[idx]
    fname = VU_DIALS[idx][0]
    crop_b = VU_DIALS[idx][5]
    try:
        img = pygame.image.load(get_resource_path(fname))
        if crop_b > 0:
            w, h = img.get_size()
            new_h = int(h * (1 - crop_b))
            cropped = pygame.Surface((w, new_h))
            cropped.blit(img, (0, 0), area=pygame.Rect(0, 0, w, new_h))
            img = cropped
    except (pygame.error, FileNotFoundError):
        img = None
    _vu_dial_cache[idx] = img
    return img

def draw_dial_vu(cx, top_y, disp_w, current_angle):
    """Aktif VU kadranini (cx ortali) ciz + ibre. Kadran yoksa None."""
    idx = state.get("vu_dial_idx", 0) % len(VU_DIALS)
    img0 = _load_vu_dial(idx)
    if img0 is None:
        return None
    _fn, pvx, pvy, needle_col, _orig, crop_b, _sc = VU_DIALS[idx]
    if crop_b > 0:
        pvy = pvy / (1 - crop_b)
    ow, oh = img0.get_size()
    disp_h = int(disp_w * oh / ow)
    key = (idx, disp_w, disp_h)
    if key not in _vu_scaled_cache:
        _vu_scaled_cache[key] = pygame.transform.smoothscale(img0, (disp_w, disp_h))
    img = _vu_scaled_cache[key]
    x0 = cx - disp_w // 2
    screen.blit(img, (x0, top_y))
    piv_x = x0 + int(disp_w * pvx)
    piv_y = top_y + int(disp_h * pvy)
    if not math.isfinite(current_angle):
        current_angle = 180.0
    ang = 137 - (180 - current_angle) / 180.0 * 101
    rad = math.radians(ang)
    needle_len = int(disp_w * 0.37)
    nx = piv_x + needle_len * math.cos(rad)
    ny = piv_y - needle_len * math.sin(rad)
    pygame.draw.line(screen, needle_col, (piv_x, piv_y), (int(nx), int(ny)), 6)
    pygame.draw.circle(screen, needle_col, (piv_x, piv_y), 9)
    return disp_h


def _lerp(c1, c2, f):
    return (int(c1[0] + (c2[0]-c1[0])*f),
            int(c1[1] + (c2[1]-c1[1])*f),
            int(c1[2] + (c2[2]-c1[2])*f))


def gradient_color(theme_name, ratio):
    stops = COLOR_THEMES.get(theme_name, COLOR_THEMES["Yesil"])
    if ratio < 0.45:
        return _lerp(stops[0], stops[1], ratio / 0.45)
    elif ratio < 0.88:
        return _lerp(stops[1], stops[1], 0)
    else:
        return _lerp(stops[1], stops[2], (ratio - 0.88) / 0.12)


# Gercek vu_bg.png fotografini birebir kullanan VU gostergesi
_vu_scaled_cache = {}

def draw_photo_vu(cx, top_y, disp_w):
    """Foto'yu (cx ortali) top_y'den baslayarak disp_w genisliginde ciz.
    Donus deger: (pivot_x, pivot_y, img_w, img_h) -- ibre cizimi icin."""
    if raw_vu_image is None:
        return None
    ow, oh = raw_vu_image.get_size()
    disp_h = int(disp_w * oh / ow)
    key = (disp_w, disp_h)
    if key not in _vu_scaled_cache:
        _vu_scaled_cache[key] = pygame.transform.smoothscale(raw_vu_image, (disp_w, disp_h))
    img = _vu_scaled_cache[key]
    x0 = cx - disp_w // 2
    screen.blit(img, (x0, top_y))
    # Pivot: tam orijinal foto'da (660,920)/(1506,1140) oransal
    piv_x = x0 + int(disp_w * (745 / 1506))
    piv_y = top_y + int(disp_h * (920 / 1140))
    return (piv_x, piv_y, disp_w, disp_h)


def draw_photo_needle(piv_x, piv_y, disp_w, current_angle):
    # Skala: -20(sol,137 derece) .. +5(sag,36 derece)
    ang = 137 - (180 - current_angle) / 180.0 * 101
    rad = math.radians(ang)
    if not math.isfinite(current_angle):
        current_angle = 180.0
    needle_len = int(disp_w * 0.37)
    nx = piv_x + needle_len * math.cos(rad)
    ny = piv_y - needle_len * math.sin(rad)
    pygame.draw.line(screen, (210, 30, 30), (piv_x, piv_y), (int(nx), int(ny)), 6)
    pygame.draw.circle(screen, (210, 30, 30), (piv_x, piv_y), 9)


# ---------- GORUNUM 1: SPEKTRUM (ust: VU dial'lar, alt: barlar) ----------
_idle_font_cache = {}


def draw_idle_screen(surf, t):
    """Bekleme ekrani (muzik yokken) - pulse efektli VINTAGE SES KONSOLU."""
    W, H = surf.get_size()
    surf.fill((8, 8, 10))
    key = max(48, int(W * 0.06))
    if key not in _idle_font_cache:
        _idle_font_cache[key] = pygame.font.SysFont("DejaVu Sans", key, bold=True)
    font = _idle_font_cache[key]
    pulse = int(140 + 60 * abs(((t * 0.4) % 2.0) - 1.0))
    color = (pulse, int(pulse * 0.85), int(pulse * 0.6))
    ts = font.render("VİNTAGE SES KONSOLU", True, color)
    surf.blit(ts, (W // 2 - ts.get_width() // 2, H // 2 - ts.get_height() // 2))


def draw_spectrum(W_CUR, H_CUR, stereo_bars, theme_name, cava_bars):
    # Ust yari: iki gercek VU fotografi
    disp_w = max(220, min(int(W_CUR * 0.30), 460))
    top_y = 20
    cx1 = int(W_CUR * 0.27)
    cx2 = int(W_CUR * 0.73)

    global left_angle, right_angle
    def _vu_level(sl):
        if not len(sl): return 0.0
        arr = np.asarray(sl, dtype=float)
        v = (arr.max() * 0.45 + arr.mean() * 0.55) / 255.0
        return min(1.0, v * 0.97)
    vol_l = _vu_level(cava_bars[:HALF_BARS]) if len(cava_bars) > 0 else 0.0
    vol_r = _vu_level(cava_bars[HALF_BARS:HALF_BARS*2]) if len(cava_bars) > 0 else 0.0
    if not math.isfinite(vol_l): vol_l = 0.0
    if not math.isfinite(vol_r): vol_r = 0.0
    tgt_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    tgt_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    left_angle += (tgt_l - left_angle) * 0.6
    right_angle += (tgt_r - right_angle) * 0.6
    pl = draw_photo_vu(cx1, top_y, disp_w)
    pr = draw_photo_vu(cx2, top_y, disp_w)
    if pl: draw_photo_needle(pl[0], pl[1], pl[2], left_angle)
    if pr: draw_photo_needle(pr[0], pr[1], pr[2], right_angle)

    # Alt yari: spektrum barlari
    disp_h = pl[3] if pl else 200
    bars_top = top_y + disp_h + 15
    BARS_BOTTOM_Y = H_CUR
    BARS_MAX_HEIGHT = max(80, H_CUR - bars_top)

    MARGIN_X = 30
    CENTER_GAP = 50
    usable = W_CUR - 2 * MARGIN_X - CENTER_GAP
    half_usable = usable // 2
    bar_w = max(1, half_usable // HALF_BARS - 1)
    left_start = MARGIN_X
    right_start = MARGIN_X + half_usable + CENTER_GAP

    for i in range(NUM_BARS):
        target_bar = int((stereo_bars[i] / 255.0) * BARS_MAX_HEIGHT * 0.9)
        smooth_bars[i] += (target_bar - smooth_bars[i]) * 0.85
        h = min(int(smooth_bars[i]), BARS_MAX_HEIGHT)

        if h >= peak_bars[i]:
            peak_bars[i] = h
            peak_timers[i] = FPS * 0.45
        else:
            if peak_timers[i] > 0: peak_timers[i] -= 1
            else:
                peak_bars[i] -= 4
                if peak_bars[i] < 0: peak_bars[i] = 0

        if i < HALF_BARS:
            x_pos = left_start + i * (bar_w + 1)
        else:
            x_pos = right_start + (i - HALF_BARS) * (bar_w + 1)

        if h > 2:
            for y_offset in range(0, h, 4):
                ratio = y_offset / BARS_MAX_HEIGHT
                seg = gradient_color(theme_name, ratio)
                pygame.draw.rect(screen, seg, (x_pos, BARS_BOTTOM_Y - y_offset - 4, bar_w, 4))

        peak_h = int(peak_bars[i])
        if peak_h > 2:
            pygame.draw.rect(screen, VINTAGE_AMBER, (x_pos, BARS_BOTTOM_Y - peak_h, bar_w, 2))


# ---------- GORUNUM 2: KLASIK VU ----------
def draw_vintage_dial(cx, cy, radius, name, current_angle):
    dial_surface = pygame.Surface((radius * 2, radius), pygame.SRCALPHA)
    temp_surf = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    if raw_vu_image:
        pygame.draw.circle(temp_surf, (255, 255, 255, 255), (radius, radius), radius)
        scaled_bg = pygame.transform.smoothscale(raw_vu_image, (radius * 2, radius * 2))
        temp_surf.blit(scaled_bg, (0, 0), special_flags=pygame.BLEND_RGBA_MIN)
    else:
        pygame.draw.circle(temp_surf, VINTAGE_AMBER, (radius, radius), radius)
    dial_surface.blit(temp_surf, (0, 0), (0, 0, radius * 2, radius))
    screen.blit(dial_surface, (cx - radius, cy - radius))

    pygame.draw.arc(screen, VINTAGE_RED, (cx-radius+4, cy-radius+4, radius*2-8, radius*2-8), 0, math.radians(45), 10)
    pygame.draw.arc(screen, VINTAGE_GREEN, (cx-radius+4, cy-radius+4, radius*2-8, radius*2-8), math.radians(45), math.pi, 5)

    for fine_angle in range(0, 181, 5):
        rad = math.radians(fine_angle)
        length = int(radius * 0.05) if fine_angle % 15 == 0 else int(radius * 0.025)
        t_color = (180, 20, 20) if fine_angle < 45 else (40, 40, 40)
        x1 = cx + radius * math.cos(rad); y1 = cy - radius * math.sin(rad)
        x2 = cx + (radius - length) * math.cos(rad); y2 = cy - (radius - length) * math.sin(rad)
        pygame.draw.line(screen, t_color, (x1, y1), (x2, y2), 2 if length > 10 else 1)

    lbl_name = font_main.render(name, True, (240, 245, 250))
    lbl_shadow = font_main.render(name, True, (15, 15, 15))
    screen.blit(lbl_shadow, (cx - lbl_name.get_width()//2 + 2, cy + int(radius * 0.08) + 2))
    screen.blit(lbl_name, (cx - lbl_name.get_width()//2, cy + int(radius * 0.08)))

    angle_rad = math.radians(current_angle)
    nx = cx + (radius - 10) * math.cos(angle_rad)
    ny = cy - (radius - 10) * math.sin(angle_rad)
    pygame.draw.line(screen, (20, 20, 20), (cx, cy), (nx, ny), 3)
    pygame.draw.circle(screen, (40, 40, 40), (cx, cy), 20)
    pygame.draw.circle(screen, (15, 15, 15), (cx, cy), 9)


# ---------- GORUNUM: SPEKTRUM 2 (tam ekran, sadece barlar) ----------
def draw_spectrum_bars(W_CUR, H_CUR, stereo_bars, theme_name):
    """Kadransiz tam ekran spektrum: barlar ekranin tamamini kullanir."""
    bars_top = 36                      # ipucu satirina yer birak
    BARS_BOTTOM_Y = H_CUR
    BARS_MAX_HEIGHT = max(80, H_CUR - bars_top)

    MARGIN_X = 30
    CENTER_GAP = 50
    usable = W_CUR - 2 * MARGIN_X - CENTER_GAP
    half_usable = usable // 2
    bar_w = max(1, half_usable // HALF_BARS - 1)
    left_start = MARGIN_X
    right_start = MARGIN_X + half_usable + CENTER_GAP

    for i in range(NUM_BARS):
        target_bar = int((stereo_bars[i] / 255.0) * BARS_MAX_HEIGHT * 0.9)
        smooth_bars[i] += (target_bar - smooth_bars[i]) * 0.85
        h = min(int(smooth_bars[i]), BARS_MAX_HEIGHT)

        if h >= peak_bars[i]:
            peak_bars[i] = h
            peak_timers[i] = FPS * 0.45
        else:
            if peak_timers[i] > 0: peak_timers[i] -= 1
            else:
                peak_bars[i] -= 4
                if peak_bars[i] < 0: peak_bars[i] = 0

        if i < HALF_BARS:
            x_pos = left_start + i * (bar_w + 1)
        else:
            x_pos = right_start + (i - HALF_BARS) * (bar_w + 1)

        if h > 2:
            for y_offset in range(0, h, 4):
                ratio = y_offset / BARS_MAX_HEIGHT
                seg = gradient_color(theme_name, ratio)
                pygame.draw.rect(screen, seg, (x_pos, BARS_BOTTOM_Y - y_offset - 4, bar_w, 4))

        peak_h = int(peak_bars[i])
        if peak_h > 2:
            pygame.draw.rect(screen, VINTAGE_AMBER, (x_pos, BARS_BOTTOM_Y - peak_h, bar_w, 2))


# ---------- GORUNUM: VU METRE (sadece iki gosterge) ----------
def draw_vu_only(W_CUR, H_CUR, cava_bars):
    screen.fill((15, 15, 15))
    global left_angle, right_angle
    def _vu_level(sl):
        if not len(sl): return 0.0
        arr = np.asarray(sl, dtype=float)
        v = (arr.max() * 0.45 + arr.mean() * 0.55) / 255.0
        return min(1.0, v * 0.97)
    vol_l = _vu_level(cava_bars[:HALF_BARS]) if len(cava_bars) > 0 else 0.0
    vol_r = _vu_level(cava_bars[HALF_BARS:HALF_BARS*2]) if len(cava_bars) > 0 else 0.0
    if not math.isfinite(vol_l): vol_l = 0.0
    if not math.isfinite(vol_r): vol_r = 0.0
    tgt_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    tgt_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    left_angle += (tgt_l - left_angle) * 0.6
    right_angle += (tgt_r - right_angle) * 0.6

    idx = state.get("vu_dial_idx", 0) % len(VU_DIALS)
    scale = VU_DIALS[idx][6]
    # Olcek uygula ama iki kadran ust uste binmesin diye W*0.45 ile sinirla
    disp_w = min(int(min(int(W_CUR * 0.42), 620) * scale), int(W_CUR * 0.45))
    img0 = _load_vu_dial(idx)
    ow, oh = img0.get_size() if img0 else (1506, 1140)
    disp_h = int(disp_w * oh / ow)
    top_y = max(0, (H_CUR - disp_h) // 2)
    cx1 = int(W_CUR * 0.27)
    cx2 = int(W_CUR * 0.73)
    if draw_dial_vu(cx1, top_y, disp_w, left_angle) is None:
        # kadran gorseli yoksa eski tek-kadran yollu cizim
        pl = draw_photo_vu(cx1, top_y, disp_w)
        pr = draw_photo_vu(cx2, top_y, disp_w)
        if pl: draw_photo_needle(pl[0], pl[1], pl[2], left_angle)
        if pr: draw_photo_needle(pr[0], pr[1], pr[2], right_angle)
    else:
        draw_dial_vu(cx2, top_y, disp_w, right_angle)


# ---------- GORUNUM 3: LED SPEKTRUM ----------
_led_tex = {"surf": None, "w": None, "palette": None, "max_h": None, "style": None}

def _get_led_texture(led_bar_width, max_h, palette_name, style="rect"):
    c = _led_tex
    if c["surf"] is not None and c["w"] == led_bar_width and c["palette"] == palette_name and c["max_h"] == max_h and c["style"] == style:
        return c["surf"]
    stops = LED_THEMES.get(palette_name, LED_THEMES["Camgobegi"])
    LED_SEG_H = 10; LED_SEG_GAP = 3
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
        y_top = max_h - y_off - LED_SEG_H
        if style == "dot":
            cxd = led_bar_width // 2
            cyd = max(0, y_top) + LED_SEG_H // 2
            rd = max(2, min(led_bar_width, LED_SEG_H) // 2)
            pygame.draw.circle(tex, color, (cxd, cyd), rd)
        else:
            pygame.draw.rect(tex, color, (0, max(0, y_top), led_bar_width, LED_SEG_H))
        y_off += step
    c.update(surf=tex, w=led_bar_width, palette=palette_name, max_h=max_h, style=style)
    return tex


def draw_led_spectrum(W_CUR, H_CUR, stereo_bars, palette_name, style="rect"):
    screen.fill((0, 0, 0))
    MARGIN_X = 20
    CENTER_GAP = 40
    LED_BAR_GAP = 10
    usable = W_CUR - 2 * MARGIN_X - CENTER_GAP
    half_usable = usable // 2
    bar_w = max(2, half_usable // HALF_BARS - LED_BAR_GAP)
    left_start = MARGIN_X
    right_start = MARGIN_X + half_usable + CENTER_GAP
    BARS_BOTTOM_Y = H_CUR
    BARS_MAX_HEIGHT = H_CUR

    texture = _get_led_texture(bar_w, BARS_MAX_HEIGHT, palette_name, style)

    for i in range(NUM_BARS):
        target = int((stereo_bars[i] / 255.0) * BARS_MAX_HEIGHT * 0.62)
        smooth_bars[i] += (target - smooth_bars[i]) * 0.15
        h = min(int(smooth_bars[i]), BARS_MAX_HEIGHT)

        if h >= peak_bars[i]:
            peak_bars[i] = h
            peak_timers[i] = FPS * 0.45
        else:
            if peak_timers[i] > 0: peak_timers[i] -= 1
            else:
                peak_bars[i] = max(0, peak_bars[i] - 3)

        if i < HALF_BARS:
            x_pos = left_start + i * (bar_w + LED_BAR_GAP)
        else:
            x_pos = right_start + (i - HALF_BARS) * (bar_w + LED_BAR_GAP)

        if h > 1:
            src = pygame.Rect(0, BARS_MAX_HEIGHT - h, bar_w, h)
            screen.blit(texture, (x_pos, BARS_BOTTOM_Y - h), area=src)

        peak_h = int(peak_bars[i])
        if peak_h > 2:
            pygame.draw.rect(screen, (180, 255, 245), (x_pos, BARS_BOTTOM_Y - peak_h, bar_w, 3))


# ==================== OLCUM PANELI (APx555 tarzi, YATAY) ====================
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

# ---- Sistem ses bilgisi (Windows: soundcard, periyodik cache) ----
_sysaudio_cache = {"rate": "--", "fmt": "--", "ch": "--", "vol": "--", "dev": "--", "t": 0.0}

def _clean_dev_name(name):
    """'Hoparlör (Focusrite USB Audio)' -> 'Focusrite USB Audio' (kisa ve anlamli)."""
    if not name:
        return "--"
    n = name.strip()
    # parantez icindeki asil cihaz adini al
    if "(" in n and ")" in n:
        inner = n[n.index("(") + 1:n.rindex(")")].strip()
        if len(inner) >= 4:
            n = inner
    # gereksiz ekleri at
    for junk in ("High Definition Audio Device", "Audio Device", "Device"):
        n = n.replace(junk, "").strip(" -,")
    return n[:20] if len(n) > 20 else n


def _win_volume():
    """Windows ana ses seviyesi (%) - pycaw. Yeni pycaw'da GetSpeakers() bir
    sarmalayici doner; gercek cihaz ._dev icinde (ya da .EndpointVolume hazir)."""
    try:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        dev = AudioUtilities.GetSpeakers()

        # Yol 1: yeni pycaw hazir arayuz veriyorsa
        vol = getattr(dev, "EndpointVolume", None)
        if vol is None:
            # Yol 2: ic cihaz uzerinden aktive et
            from ctypes import cast, POINTER
            from comtypes import CLSCTX_ALL
            inner = getattr(dev, "_dev", None) or dev
            iface = inner.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            vol = cast(iface, POINTER(IAudioEndpointVolume))

        if vol.GetMute():
            return "SESSIZ"
        return f"{int(round(vol.GetMasterVolumeLevelScalar() * 100))}%"
    except Exception:
        return "--"


def _refresh_sysaudio():
    import time as _t
    now = _t.time()
    if now - _sysaudio_cache["t"] < 1.5:
        return _sysaudio_cache
    _sysaudio_cache["t"] = now
    try:
        _sysaudio_cache["dev"] = _clean_dev_name(getattr(cava, "_dev_name", None))
        _sysaudio_cache["rate"] = f"{SAMPLE_RATE // 1000}kHz"
        _sysaudio_cache["fmt"] = "f32"
        _sysaudio_cache["ch"] = "2ch"
        _sysaudio_cache["vol"] = _win_volume()
    except Exception:
        pass
    return _sysaudio_cache


def draw_meter_panel(W_CUR, H_CUR, cava_bars):
    """YATAY Olcum Paneli - HEPSI BIR ARADA: 2 satir x 4 sutun.
    Ust satir: Seviyeler (RMS/FREKANS/DENGE/MERKEZ)
    Alt satir: Analiz (DINAMIK/STEREO/ENERJI/SISTEM)"""
    screen.fill((8, 10, 8))
    n = len(cava_bars)
    left = np.array(cava_bars[:HALF_BARS], dtype=float) if n >= HALF_BARS else np.zeros(HALF_BARS)
    right = np.array(cava_bars[HALF_BARS:HALF_BARS*2], dtype=float) if n >= HALF_BARS*2 else np.zeros(HALF_BARS)

    rms_l = float(np.sqrt(np.mean(left**2))) / 255.0 if left.size else 0.0
    rms_r = float(np.sqrt(np.mean(right**2))) / 255.0 if right.size else 0.0
    peak_l = (float(left.max()) / 255.0) if left.size else 0.0
    peak_r = (float(right.max()) / 255.0) if right.size else 0.0
    peak = max(peak_l, peak_r)
    if left.size and right.size:
        _all = left + right
        _tot = float(_all.sum())
        _bass = float(_all[:max(1, HALF_BARS // 4)].sum())
        bass_ratio = (_bass / _tot) if _tot > 1 else 0.0
    else:
        bass_ratio = 0.0
    eng_l = float(left.sum()) if left.size else 0.0
    eng_r = float(right.sum()) if right.size else 0.0
    eng_max = max(eng_l, eng_r, 1.0)
    bal_l = eng_l / eng_max
    bal_r = eng_r / eng_max
    bal_pct = (eng_r / (eng_l + eng_r) * 100) if (eng_l + eng_r) > 1 else 50.0
    freq_l = _bar_to_hz(int(np.argmax(left)), HALF_BARS) if left.size and left.max() > 5 else 0.0
    freq_r = _bar_to_hz(int(np.argmax(right)), HALF_BARS) if right.size and right.max() > 5 else 0.0
    allb = (left + right)
    if allb.sum() > 1:
        idx = np.arange(HALF_BARS)
        cen_idx = float((idx * allb).sum() / allb.sum())
        centroid = _bar_to_hz(cen_idx, HALF_BARS)
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
    LBL = (0, 210, 210)
    TITLE = (0, 210, 210)

    def to_db(v):
        if v <= 0.0001:
            return -60.0
        return max(-60.0, 20.0 * math.log10(v))

    # Analiz istatistikleri (eski sayfa 2)
    dr_l = max(0.0, to_db(s["peak_l"]) - to_db(s["rms_l"]))
    dr_r = max(0.0, to_db(s["peak_r"]) - to_db(s["rms_r"]))
    crest_l = (s["peak_l"] / s["rms_l"]) if s["rms_l"] > 0.001 else 0.0
    crest_r = (s["peak_r"] / s["rms_r"]) if s["rms_r"] > 0.001 else 0.0
    _el = float(left.sum()); _er = float(right.sum())
    width = abs(_el - _er) / (_el + _er) if (_el + _er) > 1 else 0.0
    if left.size and right.size:
        _allb2 = left + right
        _tot2 = float(_allb2.sum())
        _treble = float(_allb2[HALF_BARS*3//4:].sum())
        treble_ratio = (_treble / _tot2) if _tot2 > 1 else 0.0
    else:
        treble_ratio = 0.0
    for k, v in (("dr", (dr_l+dr_r)/2), ("crest", (crest_l+crest_r)/2),
                 ("width", width), ("treble", treble_ratio)):
        s.setdefault(k, 0.0)
        s[k] += (v - s[k]) * a

    row_top = [
        ("RMS SEVIYE", [("Ch1", s["rms_l"], f"{to_db(s['rms_l']):.1f}", "dB"),
                        ("Ch2", s["rms_r"], f"{to_db(s['rms_r']):.1f}", "dB")]),
        ("FREKANS", [("Ch1", min(1.0, s["freq_l"]/16000), f"{s['freq_l']/1000:.3f}", "kHz"),
                     ("Ch2", min(1.0, s["freq_r"]/16000), f"{s['freq_r']/1000:.3f}", "kHz")]),
        ("STEREO DENGE", [("Sol", s["bal_l"], f"{100-s['bal_pct']:.0f}", "%"),
                          ("Sag", s["bal_r"], f"{s['bal_pct']:.0f}", "%")]),
        ("MERKEZ", [("Frk", min(1.0, s["centroid"]/16000), f"{s['centroid']/1000:.3f}", "kHz"),
                    ("Bas", s["bass"], f"{s['bass']*100:.0f}", "%")]),
    ]
    row_bot = [
        ("DINAMIK", [("Arl", min(1.0, s["dr"]/40), f"{s['dr']:.1f}", "dB"),
                     ("Crs", min(1.0, s["crest"]/4), f"{s['crest']:.2f}", "x")]),
        ("STEREO", [("Gen", min(1.0, s["width"]), f"{s['width']*100:.0f}", "%"),
                    ("Bal", s["bal_r"], f"{s['bal_pct']:.0f}", "%")]),
        ("ENERJI", [("Tiz", min(1.0, s["treble"]*3), f"{s['treble']*100:.0f}", "%"),
                    ("Bas", s["bass"], f"{s['bass']*100:.0f}", "%")]),
        None,  # SISTEM metin blogu
    ]

    col_w = W_CUR // 4
    row_h = H_CUR // 2
    numf = _meter_font(max(18, int(row_h * 0.085)))
    unitf = _meter_font(max(12, int(row_h * 0.045)))
    lblf = _meter_font(max(13, int(row_h * 0.055)))
    tfont = _meter_font(max(15, int(row_h * 0.060)))

    def _ctext(f, txt, color, cx, cy):
        ts = f.render(txt, True, color)
        screen.blit(ts, (cx - ts.get_width() // 2, cy - ts.get_height() // 2))

    def _draw_block(bx0, by0, title, rows):
        ccx = bx0 + col_w // 2
        bar_top = by0 + int(row_h * 0.26)
        bar_h_full = int(row_h * 0.46)
        bar_bottom = bar_top + bar_h_full
        bar_w = max(18, int(col_w * 0.10))
        _ctext(tfont, title, TITLE, ccx, by0 + int(row_h * 0.90))
        for ri, (label, val, num, unit) in enumerate(rows):
            bcx = ccx + (-1 if ri == 0 else 1) * int(col_w * 0.22)
            bx = bcx - bar_w // 2
            v = max(0.0, min(1.0, val))
            fill_h = int(bar_h_full * v)
            pygame.draw.rect(screen, DARK, (bx, bar_top, bar_w, bar_h_full))
            pygame.draw.rect(screen, GREY, (bx, bar_top, bar_w, bar_h_full - fill_h))
            pygame.draw.rect(screen, GREEN, (bx, bar_bottom - fill_h, bar_w, fill_h))
            _ctext(numf, num, GREEN, bcx, by0 + int(row_h * 0.10))
            _ctext(unitf, unit, GREEN, bcx, by0 + int(row_h * 0.19))
            _ctext(lblf, label, LBL, bcx, bar_bottom + int(row_h * 0.055))

    # Izgara cizgileri
    for c in range(1, 4):
        pygame.draw.line(screen, (30, 36, 30), (c * col_w, int(H_CUR*0.04)), (c * col_w, int(H_CUR*0.96)), 1)
    pygame.draw.line(screen, (30, 36, 30), (int(W_CUR*0.02), row_h), (int(W_CUR*0.98), row_h), 1)

    for pi, blk in enumerate(row_top):
        _draw_block(pi * col_w, 0, blk[0], blk[1])
    for pi, blk in enumerate(row_bot):
        if blk is None:
            continue
        _draw_block(pi * col_w, row_h, blk[0], blk[1])

    # SISTEM blogu (alt-sag)
    si = _refresh_sysaudio()
    CAM = (0, 210, 210)
    bigf = _meter_font(max(18, int(row_h * 0.080)))
    smallf = _meter_font(max(13, int(row_h * 0.052)))
    ccx = 3 * col_w + col_w // 2
    _ctext(tfont, "SISTEM", TITLE, ccx, row_h + int(row_h * 0.90))
    cy0 = row_h + int(row_h * 0.20)
    dy = int(row_h * 0.17)
    _ctext(bigf, f"{si['rate']}", GREEN, ccx, cy0)
    _ctext(smallf, f"{si['fmt']} {si['ch']}", CAM, ccx, cy0 + dy)
    _ctext(bigf, f"Ses: {si['vol']}", GREEN, ccx, cy0 + 2*dy)
    _ctext(smallf, si["dev"], CAM, ccx, cy0 + 3*dy)


# ==================== SISTEM TEPSISI (PyQt5 + pygame hibrit) ====================
# Qt exec_() KULLANILMAZ: pygame ana dongude her karede qt_app.processEvents()
# cagrilir. Boylece pencere ve tepsi ayni thread'de sorunsuz calisir.
_tray_refs = {}   # GC korumasi

def setup_tray():
    """Tepsi ikonunu kur; QApplication dondurur. PyQt5 yoksa None (uygulama tepsisiz devam eder)."""
    try:
        from PyQt5.QtWidgets import (QApplication, QSystemTrayIcon, QMenu,
                                     QAction)
        from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor
    except Exception:
        return None
    try:
        app = QApplication.instance() or QApplication(sys.argv)
        app.setQuitOnLastWindowClosed(False)

        def make_icon():
            pm = QPixmap(64, 64)
            pm.fill(QColor(20, 22, 26))
            p = QPainter(pm)
            p.setBrush(QColor(0, 210, 210)); p.setPen(QColor(0, 210, 210))  # camgobegi (LCD=yesil)
            x = 6
            for h in [20, 38, 28, 48, 34, 44, 24]:
                p.drawRect(x, 58 - h, 7, h); x += 9
            p.end()
            return QIcon(pm)

        def theme_icon(stops):
            """Tema paletinden 3 renkli yatay serit ikonu."""
            pm2 = QPixmap(48, 48); pm2.fill(QColor(0, 0, 0, 0))
            pp = QPainter(pm2)
            n = len(stops); seg = 48 // n
            for i, c in enumerate(stops):
                pp.setBrush(QColor(c[0], c[1], c[2])); pp.setPen(QColor(c[0], c[1], c[2]))
                pp.drawRect(i * seg, 8, seg, 32)
            pp.end()
            return QIcon(pm2)

        tray = QSystemTrayIcon(make_icon())
        tray.setToolTip("Vintage Audio Console")
        menu = QMenu()
        _tray_refs["tray"] = tray; _tray_refs["menu"] = menu; _tray_refs["app"] = app

        def set_state(**kw):
            def _f():
                for k, v in kw.items():
                    state[k] = v
                if "vu_dial_idx" in kw:
                    _vu_scaled_cache.clear()
            return _f

        # Spektrum / Spektrum 2 -> renk temalari (renk onizleme ikonlu)
        for mname, mkey in (("Spektrum", "Spektrum"), ("Spektrum 2 (Bar)", "Spektrum 2")):
            sub = menu.addMenu(mname)
            for i, tn in enumerate(COLOR_THEME_NAMES):
                a = QAction(theme_icon(COLOR_THEMES[tn]), tn, menu)
                a.triggered.connect(set_state(mode=mkey, theme_idx=i))
                sub.addAction(a)

        # LED modlari -> LED temalari (renk onizleme ikonlu)
        for mname in ("LED Spektrum", "LED Nokta"):
            sub = menu.addMenu(mname)
            for i, tn in enumerate(LED_THEME_NAMES):
                a = QAction(theme_icon(LED_THEMES[tn]), tn, menu)
                a.triggered.connect(set_state(mode=mname, led_theme_idx=i))
                sub.addAction(a)

        # VU Metre -> kadranlar
        vum = menu.addMenu("VU Metre")
        for i in range(len(VU_DIALS)):
            a = QAction(f"Kadran {i+1}", menu)
            a.triggered.connect(set_state(mode="VU Metre", vu_dial_idx=i))
            vum.addAction(a)

        # Olcum Paneli
        olc = QAction("Olcum Paneli", menu)
        olc.triggered.connect(set_state(mode="Olcum Paneli"))
        menu.addAction(olc)

        menu.addSeparator()

        # Sistem Monitoru (ayri pencere)
        def open_sysmon():
            try:
                if getattr(sys, "frozen", False):
                    subprocess.Popen([sys.executable, "--sysmon"])
                else:
                    subprocess.Popen([sys.executable, os.path.abspath(__file__), "--sysmon"])
            except Exception:
                pass
        smon = QAction("Sistem Monitoru", menu)
        smon.triggered.connect(open_sysmon)
        menu.addAction(smon)

        # Hakkinda (LCD ile ayni tasarim)
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

            ver = QLabel(f"Surum {__version__} (Masaustu)")
            ver.setStyleSheet("color: #9aa0a6; font-size: 12px;")
            lay.addWidget(ver)
            lay.addSpacing(10)

            desc = QLabel("Masaustu ses gorsellestirme\nve sistem monitoru")
            desc.setStyleSheet("font-size: 14px;")
            lay.addWidget(desc)
            lay.addSpacing(6)

            mods = QLabel("Spektrum  \u2022  Spektrum 2  \u2022  LED  \u2022  VU Metre  \u2022  Olcum Paneli  \u2022  Monitor")
            mods.setStyleSheet("color: #4ce08a; font-size: 12px;")
            lay.addWidget(mods)
            lay.addSpacing(10)

            line = QLabel()
            line.setStyleSheet("background: #2a2d33; min-height: 1px; max-height: 1px;")
            lay.addWidget(line)
            lay.addSpacing(8)

            tech = QLabel("cava (PipeWire) + pygame + PyQt5\nDebian 13 / KDE Wayland")
            tech.setStyleSheet("color: #9aa0a6; font-size: 12px;")
            lay.addWidget(tech)
            lay.addSpacing(10)

            dev = QLabel(f"Gelistiren: <b>pii</b>  \u2022  {datetime.date.today().strftime('%d.%m.%Y')}")
            dev.setStyleSheet("color: #9aa0a6; font-size: 12px;")
            lay.addWidget(dev)
            lay.addSpacing(8)

            btn = QPushButton("Tamam")
            btn.clicked.connect(dlg.accept)
            lay.addWidget(btn, alignment=Qt.AlignRight)

            _tray_refs["about"] = dlg  # GC korumasi
            dlg.exec_()
        hak = QAction("Hakkinda", menu)
        hak.triggered.connect(show_about)
        menu.addAction(hak)

        menu.addSeparator()
        cik = QAction("Cikis", menu)
        cik.triggered.connect(set_state(quit=True))
        menu.addAction(cik)

        # SOL TIK -> kontrol penceresi (her seferinde sag tik menu gerekmesin)
        _ctrl_win = [None]
        def open_control():
            try:
                import control_window_desktop as cwd
                if _ctrl_win[0] is None:
                    _ctrl_win[0] = cwd.build_control_window(
                        state, COLOR_THEME_NAMES, LED_THEME_NAMES, len(VU_DIALS),
                        lambda: _led_tex.update(surf=None),
                        lambda: _vu_scaled_cache.clear(),
                        open_sysmon,
                        lambda: state.__setitem__("quit", True))
                    _tray_refs["ctrl"] = _ctrl_win[0]
                cw = _ctrl_win[0]
                if hasattr(cw, "_refresh"): cw._refresh()
                cw.show(); cw.raise_(); cw.activateWindow()
            except Exception as e:
                print(f"Kontrol penceresi hatasi: {e}")

        def on_tray_activated(reason):
            from PyQt5.QtWidgets import QSystemTrayIcon as _QSTI
            if reason in (_QSTI.Trigger, _QSTI.DoubleClick):
                open_control()
        tray.activated.connect(on_tray_activated)

        # Menuye de en uste "Kontrol Paneli Ac" ekle
        first = menu.actions()[0] if menu.actions() else None
        open_act = QAction("Kontrol Paneli Ac", menu)
        open_act.triggered.connect(open_control)
        menu.insertAction(first, open_act)
        menu.insertSeparator(first)

        tray.setContextMenu(menu)
        tray.show()
        return app
    except Exception:
        return None


def draw_hint():
    txt = "1:Spektrum  2:LED  3:LED Nokta  4:VU  5:Olcum  6:Bar  W:Monitor  C:Kanal  TAB:Renk/Kadran  Q:Cikis"
    surf = font_hint.render(txt, True, (120, 120, 130))
    screen.blit(surf, (10, 8))


qt_app = setup_tray()   # PyQt5 yoksa None -> tepsisiz devam

running = True
_dbg = {"t": time.time(), "frames": 0, "au": 0}
while running:
    if qt_app is not None:
        qt_app.processEvents()
        if state.get("quit"):
            running = False
            break
    W_CURRENT, H_CURRENT = screen.get_size()

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_q, pygame.K_ESCAPE):
                running = False
            elif event.key == pygame.K_1:
                state["mode"] = "Spektrum"
            elif event.key == pygame.K_2:
                state["mode"] = "LED Spektrum"
            elif event.key == pygame.K_3:
                state["mode"] = "LED Nokta"
            elif event.key == pygame.K_4:
                state["mode"] = "VU Metre"
            elif event.key == pygame.K_5:
                state["mode"] = "Olcum Paneli"
            elif event.key == pygame.K_6:
                state["mode"] = "Spektrum 2"
            elif event.key == pygame.K_c:
                state["ch_layout"] = (state["ch_layout"] + 1) % 4
                _names = ("0: L+R", "1: L+R'", "2: L'+R", "3: L'+R'")
                print(f"Kanal dizilimi -> {_names[state['ch_layout']]}")
            elif event.key == pygame.K_w:
                # Sistem Monitoru'nu ayri pencerede ac.
                # .app icinde sys.executable bundle calistirilabiliridir;
                # ona "--sysmon" verince monitor modunda acilir.
                # Script modunda ise bu dosyayi --sysmon ile cagiririz.
                try:
                    if getattr(sys, "frozen", False):
                        subprocess.Popen([sys.executable, "--sysmon"])
                    else:
                        subprocess.Popen([sys.executable, os.path.abspath(__file__), "--sysmon"])
                except Exception:
                    pass
            elif event.key == pygame.K_TAB:
                if state["mode"] in ("LED Spektrum", "LED Nokta"):
                    state["led_theme_idx"] = (state["led_theme_idx"] + 1) % len(LED_THEME_NAMES)
                elif state["mode"] == "VU Metre":
                    state["vu_dial_idx"] = (state["vu_dial_idx"] + 1) % len(VU_DIALS)
                    _vu_scaled_cache.clear()
                else:
                    state["theme_idx"] = (state["theme_idx"] + 1) % len(COLOR_THEME_NAMES)

    cava_bars = cava.snapshot()
    if len(cava_bars) < NUM_BARS:
        cava_bars = cava_bars + [0] * (NUM_BARS - len(cava_bars))

    # Kanal dizilimi C tusuyla canli secilir (varsayilan 1 = L+R')
    _L = cava_bars[:HALF_BARS]; _R = cava_bars[HALF_BARS:HALF_BARS*2]
    _lay = state.get("ch_layout", 1)
    if _lay == 0:   stereo_bars = _L + _R
    elif _lay == 1: stereo_bars = _L + _R[::-1]
    elif _lay == 2: stereo_bars = _L[::-1] + _R
    else:           stereo_bars = _L[::-1] + _R[::-1]

    mode = state["mode"]
    # IDLE: uzun sure ses yoksa bekleme ekrani. Sistem Monitoru haric
    # (o ayri pencerede zaten). Diger modlar sessizlikte idle'a duser.
    if cava_bars and max(cava_bars) > 2:
        state["last_sound"] = time.time()
    idle = (time.time() - state.get("last_sound", 0)) > 8.0
    if idle:
        draw_idle_screen(screen, time.time())
    elif mode == "LED Spektrum":
        draw_led_spectrum(W_CURRENT, H_CURRENT, stereo_bars, LED_THEME_NAMES[state["led_theme_idx"]], "rect")
    elif mode == "LED Nokta":
        draw_led_spectrum(W_CURRENT, H_CURRENT, stereo_bars, LED_THEME_NAMES[state["led_theme_idx"]], "dot")
    elif mode == "VU Metre":
        draw_vu_only(W_CURRENT, H_CURRENT, cava_bars)
    elif mode == "Olcum Paneli":
        draw_meter_panel(W_CURRENT, H_CURRENT, cava_bars)
    elif mode == "Spektrum 2":
        screen.fill((15, 15, 15))
        draw_spectrum_bars(W_CURRENT, H_CURRENT, stereo_bars, COLOR_THEME_NAMES[state["theme_idx"]])
    else:  # Spektrum
        screen.fill((15, 15, 15))
        draw_spectrum(W_CURRENT, H_CURRENT, stereo_bars, COLOR_THEME_NAMES[state["theme_idx"]], cava_bars)

    draw_hint()
    pygame.display.flip()
    clock.tick(FPS)


try:
    cava.stop()
except Exception:
    pass
pygame.display.quit()
pygame.quit()
sys.exit()
