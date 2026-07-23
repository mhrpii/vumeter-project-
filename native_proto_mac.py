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
_dbg_frame = [0]
# NOT: trcc / HTTP API / tema klasoru ARTIK KULLANILMIYOR.
# Panele dogrudan USB ile yaziliyor (trcc_direct.py).
DEVICE_KEY = "0416:5408"   # (sadece bilgi amacli)
FPS = 30                            # native API'de daha yuksek denenebilir

# --- MAC ses: portaudio + OTOMATIK ses kaynagi (sabit degil) ---
# Once BlackHole/loopback (sistem sesini yakalar), yoksa varsayilan cikis/giris.
# Boylece her Mac'te calisir (Scarlett'e bagimli degil).
_MAC_AUDIO_CACHE = {"src": None}

def _sd_device_names():
    """portaudio giris aygitlarinin adlari (sounddevice ile)."""
    try:
        import sounddevice as _sdv
        return [d['name'] for d in _sdv.query_devices() if d['max_input_channels'] > 0]
    except Exception:
        return []


def _ensure_aggregate():
    """Tahoe+ ses yolu: VU-ScarlettLoop aggregate yoksa make_aggregate ile olustur.
    (Kullanici Audio MIDI'den silse bile acilista kendini onarir.)"""
    try:
        if any("VU-ScarlettLoop" in n for n in _sd_device_names()):
            return True
        # get_resource_path henuz tanimli degil (dosyanin asagisinda) ->
        # yolu dogrudan coz: script'in kendi dizini (.app icinde de dogru)
        binp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "make_aggregate")
        if os.path.exists(binp):
            out = subprocess.run([binp], capture_output=True, text=True,
                                 timeout=10).stdout.strip()
            print(f"[ses] aggregate kontrol: {out}")
            # CoreAudio'nun aygiti kaydetmesini BEKLE (yoklamali, en fazla 5sn):
            # 1sn sabit bekleme yetmiyordu -> kaynak cache'i Scarlett'e dusuyordu.
            for _ in range(10):
                time.sleep(0.5)
                chk = subprocess.run([binp], capture_output=True, text=True,
                                     timeout=10).stdout.strip()
                if chk == "VAR":
                    print("[ses] VU-ScarlettLoop hazir.")
                    return True
    except Exception as e:
        print(f"[ses] aggregate olusturulamadi: {type(e).__name__}")
    return False


def _detect_mac_audio_source():
    """cava (portaudio) icin uygun ses kaynagini otomatik bul.
    Mantik: cava, sesin CIKTIGI aygiti dinlemeli (o aygitin sesini gorsellestirir).
    Oncelik:
      1) VARSAYILAN CIKIS aygiti (gercek ses oradan cikar - en dogru)
      2) BlackHole/loopback (sistem sesini yakalayan sanal aygit)
      3) Scarlett/Focusrite/USB arayuz
      4) ilk ses aygiti"""
    if _MAC_AUDIO_CACHE["src"]:
        return _MAC_AUDIO_CACHE["src"]
    # TAHOE COZUMU: VU-ScarlettLoop aggregate (make_aggregate dogruladiysa
    # ismi DOGRUDAN kullan - sounddevice surec-ici onbellegi yeni aygiti
    # goremeyebiliyor, make_aggregate ayri surec oldugu icin her zaman taze).
    if _ensure_aggregate():
        _MAC_AUDIO_CACHE["src"] = "VU-ScarlettLoop"
        return "VU-ScarlettLoop"
    # (genel aggregate yedegi - kullanicinin kendi olusturdugu varsa)
    for n in _sd_device_names():
        if "Aggregate" in n or "ScarlettLoop" in n:
            _MAC_AUDIO_CACHE["src"] = n
            return n
    src = None
    default_out = None
    names = []
    try:
        out = subprocess.run(["system_profiler", "SPAudioDataType"],
                             capture_output=True, text=True, timeout=8).stdout
        cur = None
        for line in out.splitlines():
            raw = line.rstrip()
            s = raw.strip()
            # aygit adi: girintili, ':' ile biten, alt satirlarinda ozellikler
            if s.endswith(":") and "Devices" not in s and "Audio" not in s:
                cur = s[:-1].strip()
                if cur and cur not in names:
                    names.append(cur)
            # varsayilan cikis aygiti isareti
            if "Default Output Device: Yes" in s and cur:
                default_out = cur
    except Exception:
        pass

    def pick(keywords):
        for n in names:
            for kw in keywords:
                if kw.lower() in n.lower():
                    return n
        return None

    # Mantik: cava, sesin GERCEKTEN CIKTIGI aygiti dinlemeli.
    # - Varsayilan cikis bir ses ARAYUZU ise (Scarlett/Focusrite/USB): onu dinle
    #   (arayuzler hem cikis hem giris verir, cava dogrudan okur).
    # - Varsayilan cikis Multi-Output ya da dahili hoparlor ise: cava onu dinleyemez;
    #   BlackHole/loopback (sistem sesini yakalayan sanal aygit) dinlenmeli.
    is_interface = default_out and any(k in default_out
        for k in ("Scarlett", "Focusrite", "USB", "Interface"))
    is_multi_or_builtin = default_out and any(k in default_out
        for k in ("Multi-Output", "Multi-Cikis", "Built-in", "Dahili", "MacBook", "Hoparlor"))

    if is_interface:
        # Scarlett gibi arayuz dogrudan dinlenir
        src = default_out
    elif is_multi_or_builtin:
        # hoparlor/multi-output -> BlackHole gerekli
        src = (pick(["BlackHole", "Soundflower", "Loopback"]) or default_out)
    else:
        # bilinmeyen: once arayuz, sonra BlackHole, sonra varsayilan
        src = (pick(["Scarlett", "Focusrite"])
               or pick(["BlackHole", "Soundflower", "Loopback"])
               or default_out
               or (names[0] if names else None))
    if not src:
        src = "default"
    _MAC_AUDIO_CACHE["src"] = src
    return src

MAC_AUDIO_SOURCE = _detect_mac_audio_source()
CAVA_SOURCE_FALLBACK = MAC_AUDIO_SOURCE
CAVA_CONFIG = os.path.expanduser("~/.config/cava/config_native")

COLOR_THEMES = {
    "Klasik":    [(0, 170, 0), (255, 255, 0), (255, 0, 0)],
    "Neon":      [(0, 220, 220), (140, 80, 240), (240, 40, 180)],
    "Alev":      [(255, 220, 40), (255, 100, 0), (200, 30, 20)],
    "Camgobegi": [(0, 40, 80), (0, 200, 210), (200, 255, 255)],
}
COLOR_THEME_NAMES = list(COLOR_THEMES.keys())

# Tema/etiket adlarini ekranda Turkce goster (kod anahtari degismez)
_TR_LABELS = {
    "Camgobegi": "Camgöbeği",
    "Yesil-Sari-Kirmizi": "Yeşil-Sarı-Kırmızı",
    "Sari": "Sarı", "Kirmizi": "Kırmızı", "Yesil": "Yeşil",
    "Kizil": "Kızıl",
}
def _tr_label(s):
    return _TR_LABELS.get(s, s)
_HALF_N = NUM_BARS // 2

_state = {"theme_idx": 0, "running": True, "ch_layout": 1, "sens_mult": 1.0,
          "mode": "Spektrum", "led_theme_idx": 0, "vu_dial_idx": 0, "meter_page": 0,
          "brightness": 100, "brightness_changed": False, "last_sound": 0.0}

# ==================== AYAR KAYDETME (kalici) ====================
SETTINGS_PATH = os.path.expanduser("~/.config/vumeter/settings.json")
_SETTINGS_KEYS = ["theme_idx", "ch_layout", "sens_mult", "mode",
                  "led_theme_idx", "vu_dial_idx", "meter_page",
                  "sysmon_page", "brightness"]

def _hid_idle_seconds():
    """Kullanici kac saniyedir hareketsiz (fare/klavye). macOS HIDIdleTime."""
    try:
        out = subprocess.run(["ioreg", "-c", "IOHIDSystem", "-d", "4"],
                             capture_output=True, text=True, timeout=3).stdout
        for line in out.splitlines():
            if "HIDIdleTime" in line:
                ns = int(line.split("=")[-1].strip())
                return ns / 1e9
    except Exception:
        pass
    return 0.0


def load_settings():
    """Acilista kayitli ayarlari state'e uygula (yoksa varsayilan)."""
    try:
        import json
        with open(SETTINGS_PATH) as f:
            data = json.load(f)
        for k in _SETTINGS_KEYS:
            if k in data:
                _state[k] = data[k]
    except Exception:
        pass

def save_settings():
    """Mevcut ayarlari diske yaz (degistikce cagrilir)."""
    try:
        import json
        os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
        data = {k: _state.get(k) for k in _SETTINGS_KEYS if k in _state}
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f)
    except Exception:
        pass

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
# Mac: sysmon_mac (iStats+psutil), Linux: sysmon (lm-sensors). Once Mac'i dene.
try:
    import sys as _sysm
    if _sysm.platform == "darwin":
        import sysmon_mac as _sysmon_mod
    else:
        import sysmon as _sysmon_mod
except Exception:
    try:
        import sysmon as _sysmon_mod
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


# ==================== HAVA DURUMU (sol ust kose) ====================
_weather = {"temp": None, "ts": 0.0}
_WEATHER_REFRESH = 900          # 15 dk
# Ankara koordinati (Open-Meteo - anahtarsiz, hizli, guvenilir)
_WEATHER_LAT = 39.93
_WEATHER_LON = 32.86


def _weather_worker():
    """Arka planda hava sicakligini ceker (Open-Meteo, anahtarsiz).
    Internet yoksa/yavassa uygulamayi ETKILEMEZ - deger bos kalir."""
    import urllib.request
    import json as _json
    url = (f"https://api.open-meteo.com/v1/forecast"
           f"?latitude={_WEATHER_LAT}&longitude={_WEATHER_LON}"
           f"&current=temperature_2m&timezone=Europe%2FIstanbul")
    while _state.get("running", True):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "vumeter/1.0"})
            with urllib.request.urlopen(req, timeout=8) as r:
                data = _json.loads(r.read().decode("utf-8", "ignore"))
            t = data.get("current", {}).get("temperature_2m")
            if t is not None:
                _weather["temp"] = int(round(float(t)))
                _weather["ts"] = time.time()
        except Exception:
            pass        # sessizce gec (internet yok / API kapali vs.)
        for _ in range(_WEATHER_REFRESH):
            if not _state.get("running", True):
                return
            time.sleep(1)


def draw_weather(surf):
    """Sol UST koseye kucuk soluk sicaklik yaz (tum modlarda).
    Sol kose = tiz bolge -> barlar kisa kalir, cakismaz."""
    t = _weather.get("temp")
    if t is None:
        return
    f = _sm_font(22)
    txt = f"{t}°"
    s = f.render(txt, True, (150, 165, 180))
    s.set_alpha(190)
    surf.blit(s, (18, 12))


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


def _arc_dots_m(surf, cx, cy, radius, deg_start, deg_end, width, color_fn):
    """Yayi sik dolu dairelerle ciz (modul seviyesi, surf parametreli)."""
    if deg_end <= deg_start:
        return
    r = max(2, width // 2)
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


def draw_card_gauge_m(surf, cx, cy, radius, frac, base_color=None):
    """Dairesel ilerleme halkasi (modul seviyesi). Gradient, puruzsuz."""
    frac = max(0.0, min(1.0, frac))
    _arc_dots_m(surf, cx, cy, radius, 150, 390, 11, lambda t: (36, 45, 58))
    if frac <= 0.005:
        return
    end_deg = 150 + 240 * frac
    _arc_dots_m(surf, cx, cy, radius, 150, end_deg, 11, _sm_grad_rgb)


def _short_disk_name(model):
    """Uzun disk modelini kisa okunakli isme cevir."""
    m = model.strip()
    # bilinen markalar -> kisa ad
    mapping = [
        ("WD_BLACK SN850X", "WD_BLACK SN850X"),
        ("Samsung SSD 9100 PRO", "Samsung 9100 PRO"),
        ("ADATA SX8200PNP", "ADATA SX8200PNP"),
        ("KIOXIA-EXCERIA PLUS G4", "KIOXIA EXCERIA G4"),
        ("WDS500G3X0C", "WD Blue SN570"),
        ("MTFDDAK256TBN", "Micron 5400 256G"),
        ("ST4000DM004", "Seagate BarraCuda 4TB"),
        ("ST1000LM048", "Seagate BarraCuda 1TB"),
        ("ST500LT012", "Seagate 500GB"),
    ]
    for pat, short in mapping:
        if m.startswith(pat) or pat in m:
            return short
    return m[:20]



def _kapasite_str(gb):
    """996GB -> 1TB, 512GB, 4TB gibi yuvarlanmis kapasite etiketi."""
    if gb >= 900:
        tb = gb / 1000.0
        # 0.5 adimlarla yuvarla (1TB, 2TB, 4TB)
        t = round(tb * 2) / 2
        return (f"{t:.1f}".rstrip("0").rstrip(".")) + "TB"
    # GB: standart boyutlara yuvarla
    for std in (120, 128, 240, 250, 256, 480, 500, 512, 750):
        if abs(gb - std) < 20:
            return f"{std}GB"
    return f"{gb:.0f}GB"

def draw_sysmon_disks(surf, disks, usage=None):
    """SAYFA 2: 9 diskin sicakliklari (ust: NVMe, alt: SATA).
    usage: {model: yuzde} - kartin sol kenarina dikey doluluk bari (fiziksel disk tamami)."""
    usage = usage or {}
    surf.fill((8, 10, 8))

    def temp_color(t):
        if t is None: return (60, 66, 60)
        if t < 50: return (60, 230, 90)
        if t < 65: return (245, 210, 60)
        return (235, 60, 40)

    # baslik SAG UST (hava sol ustte, cakismaz)
    tf = _sm_font(22)
    ts = tf.render("DİSK ISISI", True, (150, 165, 180))
    surf.blit(ts, (WIDTH - ts.get_width() - 18, 10))

    nvme = [x for x in disks if x[2] == "nvme"]
    sata = [x for x in disks if x[2] == "sata"]

    def draw_disk_row(items, row_top, row_h, tag):
        if not items:
            return
        margin = 16
        gap = 8
        n = max(len(items), 1)
        card_w = (WIDTH - 2*margin - (n-1)*gap) // n
        for idx, (model, temp, typ) in enumerate(items):
            cx0 = margin + idx * (card_w + gap)
            ccx = cx0 + card_w // 2
            col = temp_color(temp)
            frac = max(0.0, min(1.0, temp / 90.0))
            # kart
            pygame.draw.rect(surf, (22, 27, 34), (cx0, row_top, card_w, row_h), border_radius=12)
            pygame.draw.rect(surf, (35, 43, 54), (cx0, row_top, card_w, row_h), 1, border_radius=12)
            # gauge halka
            gcx = ccx; gcy = row_top + int(row_h * 0.40)
            gr = int(min(card_w, row_h) * 0.42)
            gcol = _sm_grad_rgb(frac)
            draw_card_gauge_m(surf, gcx, gcy, gr, frac, gcol)
            # sicaklik rakami
            vf = _sm_font(int(gr * 0.9))
            vs = vf.render(f"{temp}", True, gcol)
            vx = gcx - vs.get_width()//2
            vy = gcy - vs.get_height()//2
            surf.blit(vs, (vx, vy))
            # C birimi: rakamin SAG USTUNE (derece isareti gibi)
            uf = _sm_font(max(11, int(gr * 0.32)))
            us = uf.render("°C", True, (170, 182, 196))
            surf.blit(us, (vx + vs.get_width() + 2, vy - 2))
            # disk adi - kisa ve okunakli isim
            _u = usage.get(model)
            pct = (_u[0] if isinstance(_u, tuple) else (_u or 0.0))
            _gb = (_u[1] if isinstance(_u, tuple) and len(_u) > 1 else None)
            name = _short_disk_name(model)
            # isimde kapasite yoksa gercek veriden ekle (tutarlilik)
            if _gb and not any(x in name for x in ("GB", "TB", "G ")):
                name = f"{name} {_kapasite_str(_gb)}"
            # karta sigacak en buyuk fontu bul (18'den asagi)
            nsize = 24
            nf = _sm_font(nsize)
            while nf.size(name)[0] > card_w - 8 and nsize > 12:
                nsize -= 1
                nf = _sm_font(nsize)
            ns = nf.render(name, True, (225, 232, 242))
            surf.blit(ns, (ccx - ns.get_width()//2, row_top + int(row_h*0.80)))
            # DIKEY DOLULUK BARI (sol ic kenar): fiziksel diskin tamamina gore.

            if True:
                bw = max(6, int(card_w * 0.055))
                bx = cx0 + 7
                bt = row_top + int(row_h * 0.10)
                bh = int(row_h * 0.62)
                pygame.draw.rect(surf, (14, 18, 24), (bx, bt, bw, bh), border_radius=3)
                p = max(0.0, min(100.0, pct))
                fh = int(bh * p / 100.0)
                if p < 70:   bcol = (60, 210, 90)
                elif p < 90: bcol = (245, 205, 60)
                else:        bcol = (235, 70, 45)
                if fh > 0:
                    pygame.draw.rect(surf, bcol, (bx, bt + bh - fh, bw, fh), border_radius=3)
                pygame.draw.rect(surf, (40, 48, 60), (bx, bt, bw, bh), 1, border_radius=3)
                pf2 = _sm_font(nsize)   # disk adiyla ayni boyut (karta sigan)
                psb = pf2.render(f"%{p:.0f}", True, bcol)
                surf.blit(psb, (bx + bw//2 - psb.get_width()//2 + 2, bt + bh + 4))

    half = HEIGHT // 2
    draw_disk_row(nvme, 34, half - 40, "nvme")
    draw_disk_row(sata, half + 6, half - 40, "sata")


def _core_heat_rgb(temp):
    """Cekirdek sicakligina gore renk: mavi(soguk)->yesil->sari->kirmizi(sicak)."""
    if temp <= 0:
        return (30, 34, 42)   # uyuyan cekirdek: koyu gri
    # 30C mavi, 50C yesil, 70C sari, 90C+ kirmizi
    t = max(30.0, min(95.0, temp))
    if t < 50:
        f = (t - 30) / 20.0   # mavi -> yesil
        return (int(40 + f*20), int(120 + f*110), int(200 - f*110))
    elif t < 70:
        f = (t - 50) / 20.0   # yesil -> sari
        return (int(60 + f*195), int(230 - f*20), int(90 - f*80))
    else:
        f = (t - 70) / 25.0   # sari -> kirmizi
        return (int(255), int(210 - f*160), int(10))


def draw_sysmon_cores(surf, ipg):
    """SAYFA 3: 24 cekirdek isi haritasi (Intel Power Gadget).
    Her cekirdek renkli kutu: sicakliga gore renk + frekans/sicaklik yazi."""
    surf.fill((8, 10, 8))
    cores = ipg.get("cores") or []

    # baslik SAG UST
    tf = _sm_font(22)
    ts = tf.render("ÇEKİRDEK ISI HARİTASI", True, (150, 165, 180))
    surf.blit(ts, (WIDTH - ts.get_width() - 18, 8))

    # paket ozeti SOL UST (hava'nin altina)
    pf = _sm_font(20)
    pw = ipg.get("pkg_power"); pt = ipg.get("pkg_temp"); tdp = ipg.get("tdp")
    summary = []
    if pt: summary.append(f"CPU {pt:.0f}°C")
    if pw: summary.append(f"{pw:.0f}W")
    if tdp: summary.append(f"TDP {tdp:.0f}W")
    if summary:
        ps = pf.render("   ".join(summary), True, (200, 210, 220))
        surf.blit(ps, (18, 40))

    if not cores:
        f = _sm_font(30)
        surf.blit(f.render("Intel Power Gadget verisi yok", True, (200, 120, 60)),
                  (60, HEIGHT//2))
        return

    n = len(cores)
    # 24 cekirdek -> 12 sutun x 2 satir
    cols = 12
    rows = (n + cols - 1) // cols
    margin_x = 16
    top = 72
    gap = 6
    cell_w = (WIDTH - 2*margin_x - (cols-1)*gap) // cols
    avail_h = HEIGHT - top - 12
    cell_h = (avail_h - (rows-1)*gap) // rows

    for idx, (cnum, freq, temp) in enumerate(cores):
        r = idx // cols
        c = idx % cols
        x = margin_x + c * (cell_w + gap)
        y = top + r * (cell_h + gap)
        col = _core_heat_rgb(temp)
        # kutu
        pygame.draw.rect(surf, col, (x, y, cell_w, cell_h), border_radius=8)
        # cekirdek no (sol ust, kucuk)
        nf = _sm_font(13)
        ns = nf.render(f"C{cnum}", True, (255, 255, 255) if temp > 55 else (200, 210, 220))
        surf.blit(ns, (x + 4, y + 3))
        if temp > 0:
            # sicaklik (buyuk, ortada)
            tf2 = _sm_font(int(cell_h * 0.34))
            tcol = (255, 255, 255) if temp > 55 else (20, 24, 20)
            tsr = tf2.render(f"{temp:.0f}°", True, tcol)
            surf.blit(tsr, (x + cell_w//2 - tsr.get_width()//2, y + int(cell_h*0.28)))
            # frekans (alt, kucuk)
            ff = _sm_font(13)
            fs = ff.render(f"{freq/1000:.1f}G", True, tcol)
            surf.blit(fs, (x + cell_w//2 - fs.get_width()//2, y + int(cell_h*0.68)))
        else:
            # uyuyan cekirdek
            sf = _sm_font(14)
            ss = sf.render("uyku", True, (90, 100, 110))
            surf.blit(ss, (x + cell_w//2 - ss.get_width()//2, y + cell_h//2 - 8))


def draw_sysmon(surf, fps):
    """NATIVE yatay sistem monitoru. 3 sayfa: 0=sensorler, 1=disk, 2=cekirdek."""
    surf.fill((8, 10, 8))
    mon = _get_sysmon()
    if mon is None:
        f = _sm_font(40)
        surf.blit(f.render("Sensor okunamadi", True, (200, 80, 80)), (60, HEIGHT//2 - 20))
        return
    d = mon.snapshot()
    # SAYFA 1: disk sicakliklari
    if _state.get("sysmon_page", 0) == 1:
        draw_sysmon_disks(surf, d.get("disks") or [], d.get("disk_usage"))
        return
    # SAYFA 2: cekirdek isi haritasi (Intel Power Gadget)
    if _state.get("sysmon_page", 0) == 2:
        draw_sysmon_cores(surf, d.get("ipg") or {})
        return
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
    gmhz = d.get("gpu_mem_clock"); vcore = d.get("cpu_voltage")   # GMem->GBellek, VRM->VCore
    gclk = d.get("gpu_core_clock")   # GEdge -> GClock (GPU cekirdek freq)
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
        ("GClock",f"{gclk/1000:.1f}" if gclk else "--", "GHz", (gclk/3000.0) if gclk else 0, GREEN),
        ("GBellek", f"{gmhz/1000:.1f}" if gmhz else "--", "GHz", (gmhz/3000.0) if gmhz else 0, GREEN),
        ("VCore", f"{vcore:.2f}"       if vcore else "--", "V",   (vcore/1.5)   if vcore else 0, GREEN),
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
            draw_card_gauge_m(surf, ccx, gauge_cy, gauge_r, frac, gcol)
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
def _find_cava_bin():
    """Mac'te cava'yi bul (Homebrew: /opt/homebrew Apple Silicon, /usr/local Intel)."""
    import shutil
    for c in ("/opt/homebrew/bin/cava", "/usr/local/bin/cava", shutil.which("cava")):
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return "cava"


def write_cava_config(bars=NUM_BARS, fps=60, autosens=0):
    _src = _detect_mac_audio_source()
    # VU-ScarlettLoop (aggregate): stereo modda CoreAudio 4->2 karisimi
    # loopback'i DAHIL EDIYOR (mono taze aggregate'te kanal 1'e esleniyor - sessiz).
    # Diger kaynaklarda mono karisim (eski davranis) korunur.
    _ch = 2 if "ScarlettLoop" in _src or "Aggregate" in _src else 1
    """Mac: portaudio + sabit Scarlett kaynagi. autosens dinamik (idle'da 0, muzikte 1)."""
    os.makedirs(os.path.dirname(CAVA_CONFIG), exist_ok=True)
    with open(CAVA_CONFIG, "w") as f:
        f.write(f"""[general]
bars = {bars}
framerate = {fps}
autosens = {autosens}
sensitivity = 100

[input]
method = portaudio
source = {_src}
channels = {_ch}

[output]
method = raw
data_format = ascii
ascii_max_range = 255
""")


def _find_scarlett_monitor():
    """Mac: pactl yok -> otomatik ses kaynagi."""
    return _detect_mac_audio_source()


def _wait_for_source(timeout=90):
    """Mac: pactl yok -> beklemeye gerek yok."""
    return True


def wait_until_ready(source_timeout=90, settle_timeout=25):
    """Mac: PipeWire/pactl yok -> beklemeye gerek yok."""
    return True



# ==================== SOUNDDEVICE OKUYUCU (cava'siz - Tahoe cozumu) ====================
# Tahoe'da cava mono-karisim hilesi loopback'e ulasamiyor; sounddevice ile
# Scarlett'in loopback kanallari (3-4) DOGRUDAN okunur. Stereo geri gelir.
class SoundReader:
    """CavaReader ile ayni arayuz: snapshot() -> 204 bar (0-255), raw_mean.
    Kaynak: Scarlett loopback (kanal 3-4), NumPy rFFT + log-frekans binleme."""
    RATE = 44100
    BLOCK = 1024          # callback adimi (~43/sn - akici)
    FFTN = 2048           # FFT penceresi (kayan tampon - cozunurluk korunur)

    def __init__(self):
        import sounddevice as sd
        self._sd = sd
        self.bars = [0] * NUM_BARS
        self.raw_mean = 0.0
        self._lock = threading.Lock()
        self._warmup = 20
        self._ag_peak = 0.0
        self._ag_mult = 1.0
        self._half = NUM_BARS // 2
        # log-frekans bin kenarlari (40Hz - 16kHz)
        self._freqs = np.fft.rfftfreq(self.FFTN, 1.0 / self.RATE)
        self._ringL = np.zeros(self.FFTN, dtype=np.float32)
        self._ringR = np.zeros(self.FFTN, dtype=np.float32)
        # bar merkez frekanslari (log araliklarin geometrik ortasi) - interpolasyonla
        # ornekleme: bos bin sorunu YOK (dusuk frekansta 22 bin bos kaliyordu)
        edges = np.logspace(np.log10(40), np.log10(16000), self._half + 1)
        self._centers = np.sqrt(edges[:-1] * edges[1:])
        self._win = np.hanning(self.FFTN).astype(np.float32)
        self._smooth = np.zeros(NUM_BARS, dtype=np.float32)
        self._fall = np.zeros(NUM_BARS, dtype=np.float32)   # yercekimi hiz tamponu
        self._dev_idx = self._find_scarlett_idx()
        self._stream = None
        self._start_stream()

    def _find_scarlett_idx(self):
        try:
            for i, d in enumerate(self._sd.query_devices()):
                if 'Scarlett' in d['name'] and d['max_input_channels'] >= 4:
                    return i
        except Exception:
            pass
        return None

    def _cb(self, indata, nframes, t, status):
        try:
            l = indata[:, 2].astype(np.float32)
            r = indata[:, 3].astype(np.float32)
            # kayan tampon: yeni blogu sona ekle (overlap FFT - akici + cozunurluklu)
            n = len(l)
            self._ringL = np.roll(self._ringL, -n); self._ringL[-n:] = l
            self._ringR = np.roll(self._ringR, -n); self._ringR[-n:] = r
            bl = self._fft_bars(self._ringL)
            br = self._fft_bars(self._ringR)
            raw = np.concatenate([bl, br])
            self.raw_mean = float(raw.mean())
            # warmup
            if self._warmup > 0:
                self._warmup -= 1
                if self._warmup > 12:
                    raw = raw * 0
                else:
                    raw = raw * ((12 - self._warmup) / 12.0)
            # yazilimsal auto-gain (CavaReader ile ayni desen)
            cur_peak = float(raw.max()) if raw.size else 0.0
            if cur_peak > self._ag_peak:
                self._ag_peak = cur_peak
            else:
                self._ag_peak *= 0.995
            if self._ag_peak > 12:
                target = max(1.0, min(8.0, 230.0 / max(1.0, self._ag_peak)))
            else:
                target = 1.0
            self._ag_mult += (target - self._ag_mult) * 0.05
            hedef = np.clip(raw * self._ag_mult, 0, 255)
            # MONSTERCAT yayilimi (cava'nin organik sirri): her bar komsularina
            # 1.5 boluculu dalga yayar - spektrum "akiskan" gorunur.
            # (monstercat yayilimi kapatildi - keskin barlar)
            # YUKSELIS: hizli tutunma / DUSUS: yercekimi (ivmeli suzulme)
            up = hedef >= self._smooth
            self._smooth[up] = hedef[up] * 0.7 + self._smooth[up] * 0.3
            self._fall[up] = 0.0
            dn = ~up
            self._fall[dn] += 2.2          # ivme (kare basina artan hiz)
            self._smooth[dn] = np.maximum(hedef[dn], self._smooth[dn] - self._fall[dn])
            with self._lock:
                self.bars = self._smooth.astype(int).tolist()
        except Exception:
            pass

    def _fft_bars(self, x):
        sp = np.abs(np.fft.rfft(x * self._win))
        # merkez frekanslarda interpolasyon (surekli spektrum, bos bar yok)
        out = np.interp(self._centers, self._freqs, sp).astype(np.float32)
        # dB-benzeri sikistirma + taban olcek (deneysel: muzikte ~30-120 bandi)
        out = np.log1p(out * 8.0) * 34.0
        return out

    def _start_stream(self):
        if self._dev_idx is None:
            return
        try:
            self._stream = self._sd.InputStream(
                device=self._dev_idx, channels=4, samplerate=self.RATE,
                blocksize=self.BLOCK, callback=self._cb)
            self._stream.start()
        except Exception as e:
            print(f"[SoundReader] akis baslatilamadi: {type(e).__name__}: {e}")

    def snapshot(self):
        with self._lock:
            return list(self.bars)

    def stop(self):
        try:
            if self._stream:
                self._stream.stop(); self._stream.close()
        except Exception:
            pass


class CavaReader:
    """Guclendirilmis: profil degisimi + uzun sessizlik + PipeWire kurtarma.
    Senaryo: muzik uzun sure durunca PipeWire monitor hatti olebiliyor;
    o zaman cava veri alamaz ve program kapat-ac ile duzelmez (reboot gerekirdi).
    Bu surum: uzun sifir -> cava yenile -> hala olmuyorsa PipeWire tazele."""
    def __init__(self):
        self.bars = [0] * NUM_BARS
        self._lock = threading.Lock()
        self.proc = None
        self._active_source = None
        self._zero_since = None
        self._cur_autosens = 0   # HEP 0: auto-gain yazilimsal (restart/donma yok)
        self.raw_mean = 0.0      # ham ortalama (idle karari icin)
        self._ag_peak = 0.0      # yazilimsal auto-gain: sonumlenen tepe izleyici
        self._ag_mult = 1.0      # yazilimsal auto-gain: yumusak carpan
        self._sleep_paused = False  # uyku-dostu durdurma bayragi
        self._warmup = 0   # restart sonrasi yumusak baslangic sayaci
        self._last_data = time.time()
        self._pw_reset_done = False
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _wake_aggregate(self):
        """Aggregate'i uyandir: sounddevice ile 1sn dinle (kanitlanmis tetik).
        Taze/uykudaki aggregate cava'ya sifir verir; sounddevice acilisi
        CoreAudio akisini aktive eder, ardindan cava normal okur."""
        try:
            srcname = _MAC_AUDIO_CACHE.get("src") or ""
            if "ScarlettLoop" not in srcname and "Aggregate" not in srcname:
                return
            import sounddevice as _sdv
            idx = None
            for i, d in enumerate(_sdv.query_devices()):
                if srcname in d["name"] and d["max_input_channels"] > 0:
                    idx = i
                    break
            if idx is None:
                return
            _n = max(2, min(4, _sdv.query_devices(idx)["max_input_channels"]))
            with _sdv.InputStream(device=idx, channels=_n, blocksize=4096):
                time.sleep(1.0)
            print("[ses] aggregate uyandirildi (sounddevice tetigi)")
        except Exception as e:
            print(f"[ses] uyandirma atlandi: {type(e).__name__}")

    def _start(self):
        _wait_for_source()
        self._active_source = _find_scarlett_monitor()
        write_cava_config(autosens=self._cur_autosens)
        self._wake_aggregate()
        self.proc = subprocess.Popen([_find_cava_bin(), "-p", CAVA_CONFIG], stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, bufsize=1)
        self._zero_since = None
        self._warmup = 30   # ilk 18 kare 0 (kalibrasyon/tavan patlamasi gizli) + 12 kademeli

    def pause_for_sleep(self):
        """Kullanici uzun suredir hareketsiz + muzik yok: cava'yi tamamen durdur.
        Scarlett serbest kalir -> coreaudiod uyku engelini birakir -> Mac uyur."""
        if self._sleep_paused:
            return
        self._sleep_paused = True
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
        self.proc = None
        with self._lock:
            self.bars = [0] * NUM_BARS

    def resume_from_sleep(self):
        """Kullanici geri geldi (fare/klavye): cava'yi hemen baslat."""
        if not self._sleep_paused:
            return
        self._sleep_paused = False
        # _loop dongusu proc None gorunce yeniden baslatir; warmup'i kisa tut
        self._warmup = 6

    def set_autosens(self, val):
        """Idle'da 0 (gurultu sismesin), muzikte 1 (otomatik seviye).
        Sadece degistiginde cava'yi yeniden baslatir."""
        val = 1 if val else 0
        if val == self._cur_autosens:
            return
        self._cur_autosens = val
        # config'i yeni autosens ile yaz + cava restart
        try:
            write_cava_config(autosens=val)
        except Exception:
            pass
        self._restart_cava()

    def _restart_cava(self):
        try:
            if self.proc:
                self.proc.terminate()
        except Exception:
            pass
        self.proc = None
        self._zero_since = None

    def _reset_pipewire(self):
        """Son care: PipeWire monitor hatti oldugunde tazele (reboot yerine)."""
        try:
            subprocess.run(["systemctl", "--user", "restart",
                            "pipewire", "pipewire-pulse", "wireplumber"],
                           timeout=10)
            time.sleep(3)
        except Exception:
            pass

    def _loop(self):
        while _state["running"]:
            if self._sleep_paused:
                time.sleep(0.5)
                continue
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
                    time.sleep(1)
                    self._restart_cava()
                    continue
                parts = line.strip().rstrip(";").split(";")
                if len(parts) >= NUM_BARS:
                    vals = [int(p) for p in parts[:NUM_BARS]]
                    with self._lock:
                        self.bars = vals
                    if max(vals) <= 1:
                        # sessizlik / veri yok
                        if self._zero_since is None:
                            self._zero_since = time.time()
                        else:
                            elapsed = time.time() - self._zero_since
                            # 1.5sn: kaynak degistiyse (profil) yenile
                            if elapsed > 1.5:
                                cur = _find_scarlett_monitor()
                                if cur != self._active_source:
                                    self._restart_cava()
                                    continue
                            # Mac DUZELTMESI: sessizlik ARIZA DEGIL (muzik durmus/
                            # sakin pasaj olabilir - idle ekrani bunun icin var).
                            # Eski 5sn kurali cava'yi olduruyordu -> restart+warmup
                            # 10-20sn boslugu yaratip muzikte VINTAGE'a sebep oluyordu.
                            # Restart SADECE cok uzun sifirda (120sn) tek deneme.
                            if elapsed > 120.0 and not self._pw_reset_done:
                                self._restart_cava()
                                self._pw_reset_done = "cava"
                                continue
                            # 12sn: PipeWire reset DEVRE DISI (LG monitor profil taramasi
                            # sorununa yol aciyordu). cava yenileme yeterli. Gerekirse geri al:
                            # elle "systemctl --user restart pipewire pipewire-pulse wireplumber"
                            # if elapsed > 12.0 and self._pw_reset_done == "cava":
                            #     self._reset_pipewire()
                            #     self._pw_reset_done = "pw"
                            #     self._restart_cava()
                            #     continue
                    else:
                        # gercek veri geldi -> her seyi sifirla
                        self._zero_since = None
                        self._pw_reset_done = False
                        self._last_data = time.time()
            except Exception:
                continue

    def snapshot(self):
        with self._lock:
            raw = list(self.bars)
        # restart sonrasi YUMUSAK BASLANGIC:
        # ilk 20 kare TAM 0 (autosens kalibrasyon sicramasi tamamen gizli),
        # sonraki 15 kare kademeli 0->1 (yumusak devreye gir).
        if self._warmup > 0:
            self._warmup -= 1
            if self._warmup > 12:
                raw = [0] * len(raw)              # kalibrasyon/tavan patlamasi gizli (18 kare)
            else:
                wf = (12 - self._warmup) / 12.0   # kademeli 0.0 -> 1.0 (12 kare)
                raw = [int(v * wf) for v in raw]
        # YAZILIMSAL AUTO-GAIN (cava autosens yerine; restart yok = donma yok):
        # sonumlenen tepe izlenir; tepe dusukse carpan buyur (kisik muzik dolar),
        # yuksekse 1'e yaklasir (tasmaz). Self-noise cava'dan 0 cikar -> 0x carpan=0.
        cur_peak = max(raw) if raw else 0
        # HAM ortalama (gain'den BAGIMSIZ) - idle karari icin disari acik.
        # Gain'li ortalama kullanilirsa sakin pasajlarda yanlis VINTAGE gorunur.
        self.raw_mean = (sum(raw) / len(raw)) if raw else 0.0
        if cur_peak > self._ag_peak:
            self._ag_peak = float(cur_peak)     # ani yukselisi hemen izle
        else:
            self._ag_peak *= 0.995              # yavas sonumlen (~birkac sn)
        if self._ag_peak > 12:                  # gercek sinyal varsa
            target = max(1.0, min(8.0, 230.0 / max(1.0, self._ag_peak)))
        else:
            target = 1.0                        # sinyal yok: notr
        self._ag_mult += (target - self._ag_mult) * 0.05   # yumusak (pompalamaz)
        # canli hassasiyet carpani (menudeki slider) x auto-gain + 0-255 clamp
        m = _state.get("sens_mult", 1.0) * self._ag_mult
        if m != 1.0:
            out = []
            for v in raw:
                v = int(v * m)
                if v > 255: v = 255
                elif v < 0: v = 0
                out.append(v)
            return out
        return raw


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
        _spec_smooth[i] += (target - _spec_smooth[i]) * 0.6
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
_VLOG_PATH = os.path.expanduser("~/Library/Logs/vumeter_lcd.log")

def _vlog(msg):
    """Konsola + log dosyasina yaz (.app'ten calisirken teshis icin)."""
    print(msg)
    try:
        with open(_VLOG_PATH, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(msg) + "\n")
    except Exception:
        pass


def sender_process_main(shm_name, frame_counter, w, h, brightness=None):
    """Ayri surec: shared memory'den kareyi al -> DOGRUDAN USB ile panele yaz.
    trcc / HTTP / PNG / disk YOK. (trcc_direct.py protokolu kullanir)

    PARLAKLIK: panelin protokolunde donanimsal parlaklik komutu YOK
    (trcc kaynagi: "the device protocol has no separate brightness command").
    trcc de yazilimsal karartma yapiyordu -> biz de ayni sekilde kareyi
    gonderemeden once karartiyoruz (brightness: mp.Value, 10-100)."""
    import pygame as pg
    # mixer'siz init (ses aygiti acmasin - LG OSD tetiklemesin)
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    pg.display.init()

    from trcc_direct import TrccDirect

    def _baglan_donene_kadar():
        """Basarana kadar baglanmayi dene (akilli reset connect icinde).
        Uyku/kilit sonrasi USB'nin kendine gelmesini bekler."""
        deneme = 0
        while True:
            d = TrccDirect()
            try:
                d.connect()
                if deneme > 0:
                    _vlog(f"[sender] panel geri geldi (deneme {deneme+1}).")
                return d
            except Exception as e:
                deneme += 1
                if deneme <= 3 or deneme % 12 == 0:
                    _vlog(f"[sender] baglanti bekleniyor ({deneme}): {type(e).__name__}: {e}")
                try:
                    d.close()
                except Exception:
                    pass
                # DERIN KILIT ACICI (sahada kanitlandi): her 3 basarisiz denemede
                # cifte-reset uygula - uyku kilidini fiziksel replug gibi acar.
                if deneme % 3 == 0:
                    try:
                        _vlog(f"[sender] deep reset deneniyor (deneme {deneme})...")
                        d.deep_reset()
                    except Exception:
                        pass
                # BELLEK SIZINTISI ONLEME: libusb kaynaklarini acikca birak
                # (binlerce deneme birikince GB'larca RAM yiyordu)
                try:
                    import usb.util as _uu
                    if getattr(d, "dev", None) is not None:
                        _uu.dispose_resources(d.dev)
                        d.dev = None
                except Exception:
                    pass
                import gc as _gc
                _gc.collect()
                time.sleep(5.0)

    dev = _baglan_donene_kadar()

    shm = shared_memory.SharedMemory(name=shm_name)
    print("[sender] dogrudan USB akisi basladi.")
    last = -1
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
                try:
                    # SIZINTISIZ KARE YOLU: kalici numpy dizisi + kalici Surface.
                    # Her karede YENI nesne YOK (frombuffer/copy/rotate kaldirildi)
                    # -> SDL tarafinda birikim yapisal olarak imkansiz.
                    import numpy as _np
                    if not hasattr(sender_process_main, "_arr"):
                        sender_process_main._arr = _np.empty((h, w, 3), dtype=_np.uint8)
                        sender_process_main._surf = pg.Surface((w, h))
                    _arr = sender_process_main._arr
                    _src = _np.frombuffer(shm.buf, dtype=_np.uint8,
                                          count=w * h * 3).reshape(h, w, 3)
                    # 180 derece donme: numpy ters dilimleme (rotate Surface gereksiz)
                    _np.copyto(_arr, _src[::-1, ::-1])
                    # yazilimsal parlaklik: numpy carpani (yeni Surface gerekmez)
                    if brightness is not None:
                        b = brightness.value
                        if b < 100:
                            b = max(10, min(100, b))
                            _np.multiply(_arr, b / 100.0, out=_arr, casting="unsafe")
                    dev.send_array(_arr)
                    win_sent += 1
                    now = time.time()
                    if now - win_t0 >= 10.0:
                        print(f"[sender] panele giden: {win_sent/(now-win_t0):.1f} FPS")
                        win_t0 = now
                        win_sent = 0
                        # BELLEK: kare yolunda biriken Surface/buffer dongulerini supur
                        # (sender saatler icinde GB'larca sismesin - 10 sn'de bir ucuz)
                        import gc as _gc
                        _gc.collect()
                    errs = 0   # basarili gonderim: hata sayacini sifirla
                except Exception as e:
                    errs += 1
                    if errs <= 3 or errs % 50 == 0:
                        _vlog(f"[sender] HATA #{errs}: {type(e).__name__}: {e}")
                    if errs >= 5:
                        # USB olmus olabilir (uyku sonrasi tipik). Yeniden kur:
                        _vlog("[sender] USB toparlaniyor: kapat + akilli reset ile yeniden baglan...")
                        try:
                            dev.close()
                        except Exception:
                            pass
                        dev = _baglan_donene_kadar()
                        errs = 0
                    time.sleep(0.05)
            else:
                time.sleep(0.002)
    finally:
        shm.close()
        try:
            dev.close()
        except Exception:
            pass


# ==================== TRAY MENU (PyQt5) ====================
_tray_ref = None; _menu_ref = None


def build_tray():
    """Sistem tepsisi ikonu + menu. pygame ile ayni process, processEvents ile."""
    # Qt platformu: Linux=xcb, Mac=cocoa, Windows=windows (otomatik sec)
    if sys.platform == "darwin":
        os.environ["QT_QPA_PLATFORM"] = "cocoa"
    elif sys.platform.startswith("linux"):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    from PyQt5.QtWidgets import QApplication, QSystemTrayIcon, QMenu, QAction, QActionGroup
    from PyQt5.QtGui import QIcon, QPixmap, QPainter, QColor

    app = QApplication.instance() or QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    # macOS: Dock'ta gorunme (sadece menu cubugu tray) -> Python roket ikonu gizlenir
    if sys.platform == "darwin":
        try:
            # NSApplicationActivationPolicyAccessory = 1 (Dock'ta yok, menu cubugu var)
            import ctypes, ctypes.util
            objc = ctypes.cdll.LoadLibrary(ctypes.util.find_library("objc"))
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.objc_msgSend.restype = ctypes.c_void_p
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
            NSApplication = objc.objc_getClass(b"NSApplication")
            sharedApp = objc.objc_msgSend(NSApplication, objc.sel_registerName(b"sharedApplication"))
            # setActivationPolicy: 1 (accessory)
            objc.objc_msgSend.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_long]
            objc.objc_msgSend(sharedApp, objc.sel_registerName(b"setActivationPolicy:"), 1)
        except Exception:
            pass

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
        a = QAction(theme_icon(COLOR_THEMES[tn]), _tr_label(tn), menu)
        a.triggered.connect(mk_spek(i)); spek.addAction(a)

    # LED Spektrum -> LED temalari
    leds = menu.addMenu("LED Spektrum")
    def mk_led(idx):
        def _f():
            _state["mode"] = "LED Spektrum"; _state["led_theme_idx"] = idx
            _led_texture_cache["surf"] = None
        return _f
    for i, tn in enumerate(LED_THEME_NAMES):
        a = QAction(theme_icon(LED_THEMES[tn]), _tr_label(tn), menu)
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
    olcp = menu.addMenu("Ölçüm Paneli")
    pg_group = QActionGroup(menu); pg_group.setExclusive(True)
    def mk_page(idx):
        def _f(): _state["mode"] = "Olcum Paneli"; _state["meter_page"] = idx
        return _f
    for i, pn in enumerate(("Seviyeler", "Analiz")):
        a = QAction(pn, menu, checkable=True); a.setChecked(_state.get("meter_page",0)==i)
        a.triggered.connect(mk_page(i)); pg_group.addAction(a); olcp.addAction(a)

    # Sistem Monitoru (2 sayfa: Sensorler / Diskler)
    smon_menu = menu.addMenu("Sistem Monitörü")
    def mk_sysmon_page(p):
        def _f():
            _state["mode"] = "Sistem Monitoru"
            _state["sysmon_page"] = p
        return _f
    sp_group = QActionGroup(menu); sp_group.setExclusive(True)
    for pi, pn in [(0, "Sensörler"), (1, "Disk Sıcaklığı"), (2, "Çekirdek Isısı")]:
        a = QAction(pn, menu, checkable=True)
        a.setChecked(_state.get("mode")=="Sistem Monitoru" and _state.get("sysmon_page",0)==pi)
        a.triggered.connect(mk_sysmon_page(pi)); sp_group.addAction(a); smon_menu.addAction(a)
    menu.addSeparator()

    # Parlaklik (API'ye baglanir)
    br_menu = menu.addMenu("Parlaklık")
    br_group = QActionGroup(menu); br_group.setExclusive(True)
    def mk_br(p):
        def _f(): _state["brightness"] = p; _state["brightness_changed"] = True
        return _f
    for p in (100, 75, 50, 25):
        a = QAction(f"%{p}", menu, checkable=True); a.setChecked(_state["brightness"]==p)
        a.triggered.connect(mk_br(p)); br_group.addAction(a); br_menu.addAction(a)

    menu.addSeparator()
    qa = QAction("Çıkış", menu)
    def do_quit():
        _state["running"] = False
        try:
            tray.hide()
        except Exception:
            pass
        try:
            from PyQt5.QtWidgets import QApplication as _QA
            _QA.quit()
        except Exception:
            pass
    qa.triggered.connect(do_quit); menu.addAction(qa)

    # KONTROL PENCERESI - tray'e tiklaninca acilir
    ctrl_win = [None]
    def _brightness_cb(v):
        _state["brightness"] = v; _state["brightness_changed"] = True
    def _led_clear():
        _led_texture_cache["surf"] = None
    def _vu_clear():
        _vu_scaled_cache.clear()
    def _position_near_tray(w):
        """Pencereyi tray ikonunun altina, SAG kenari tray'e hizali yerlestir
        (menu de oradan aciliyor -> tutarli)."""
        try:
            from PyQt5.QtWidgets import QApplication as _QApp
            scr = _QApp.primaryScreen().availableGeometry()
            geo = tray.geometry()
            if geo.width() > 0:
                # pencerenin SAG kenari tray ikonunun SOL kenarina hizali
                # (biraz sola -> menu sagda acilinca cakismaz, yan yana durur)
                x = geo.left() - w.width() + geo.width()
                # not: tray genisligi kadar sola cek
                x = geo.right() - w.width() - 40
                y = geo.bottom() + 6
            else:
                # tray konumu yoksa: sag ust kose
                x = scr.right() - w.width() - 12
                y = scr.top() + 30
            # ekran disina tasmasin
            x = max(scr.left() + 6, min(x, scr.right() - w.width() - 6))
            y = max(scr.top() + 6, y)
            w.move(int(x), int(y))
        except Exception:
            pass

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
            _position_near_tray(w)   # tray'in altina tasi
        except Exception as e:
            print(f"Kontrol penceresi hatasi: {e}")
    def on_tray_activated(reason):
        # Trigger (sol tik) veya DoubleClick -> pencere ac
        from PyQt5.QtWidgets import QSystemTrayIcon as _QSTI
        if reason in (_QSTI.Trigger, _QSTI.DoubleClick):
            open_control()
    tray.activated.connect(on_tray_activated)

    # Not: "Kontrol Paneli Ac" menu ogesi kaldirildi -
    # sol tik zaten kontrol panelini aciyor (tekrar gereksiz).

    tray.setContextMenu(menu); tray.show()
    return app


# ==================== ANA DONGU ====================
def main():
    # Kayitli ayarlari yukle (hassasiyet, kanal, tema, parlaklik, mod...)
    load_settings()
    # Acilista "yeni ses vardi" say + TAMPON -> cava/warmup otururken (ilk ~14s)
    # ne idle (VINTAGE) ne autosens=0 tetiklensin. 8s idle esigi + 6s tampon.
    _state["last_sound"] = time.time() + 6.0
    # argv: mod adi ve/veya --autostart / --no-tray bayraklari
    args = sys.argv[1:]
    autostart = "--autostart" in args
    args = [a for a in args if not a.startswith("--")]
    if args:
        _state["mode"] = args[0]   # komut satiri modu kayitli modu ezer
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
        pass  # kaynak bekleme cava yolunda yapilir (asagida)

    wait_until_ready()
    cava = CavaReader()

    # B+: autostart'ta cava GERCEKTEN veri uretene kadar bekle (atlama onleme)
    if autostart:
        wait_for_cava_data(cava, timeout=12)

    # tray menu - C: RETRY'li (acilista xcb/masaustu gec hazir olabilir -> menusuz acilma)
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
    # hava durumu thread'i (arka planda, uygulamayi bloklamaz)
    threading.Thread(target=_weather_worker, daemon=True).start()

    bright_val = mp.Value('i', int(_state.get("brightness", 100)))
    send_proc = mp.Process(target=sender_process_main,
                          args=(shm.name, frame_counter, WIDTH, HEIGHT, bright_val),
                          daemon=True)
    send_proc.start()

    print(f"Baslatildi. Native {WIDTH}x{HEIGHT}, {FPS} FPS, Spektrum. Ctrl+C ile cik.")

    def render_loop():
        """LCD render dongusu - AYRI THREAD (Qt menusu acilinca donmasin diye)."""
        frames = 0; t0 = time.time()
        while _state["running"]:
            frame_start = time.time()
            snap = cava.snapshot()
            _dbg_frame[0] += 1
            if snap and _dbg_frame[0] % 30 == 0:
                import sys as _sd
                _sd.stderr.flush()
            mode = _state["mode"]
            # IDLE: uzun sure ses yoksa bekleme ekrani (siyah kalmasin)
            # last_sound SADECE gercek muzikte guncellensin (ortalama > 25).
            # max bazli olursa self-noise (max yuksek, ort dusuk) last_sound'u
            # gunceller -> silence hic 30'a ulasmaz -> autosens hep acik -> kisir dongu.
            # HAM ortalama ile karar (gain'den bagimsiz): sakin pasajlarda
            # gain dusukken bile gercek muzik ham veride gorunur.
            # Esik 10: Scarlett self-noise ustunde, sakin muzik altinda degil.
            if snap and getattr(cava, "raw_mean", 0) > 2:
                _state["last_sound"] = time.time()
            idle = (time.time() - _state.get("last_sound", 0)) > 15.0
            # AUTOSENS dinamik: kisa sessizliklerde (sarki arasi) ACIK kalir ki
            # muzik gelince ANINDA bar gelsin (restart yok). Sadece UZUN sessizlikte
            # (30s+, gurultu 38s'de sismeden once) kapanir -> Scarlett self-noise sismez.
            # autosens gecisi KALDIRILDI: cava hep autosens=0, otomatik seviye
            # yazilimsal auto-gain'de (CavaReader.snapshot) -> restart/donma YOK.

            # UYKU-DOSTU CAVA YONETIMI (Mac uyuyabilsin):
            # Kullanici 120s+ hareketsiz VE muzik yoksa -> cava durur (Scarlett
            # serbest, coreaudiod uyku engelini birakir -> Mac uyur).
            # Kullanici donunce (fare/klavye) -> cava ANINDA baslar; muzik acmak
            # icin zaten once harekete gecersin -> sifir gecikme.
            # (kontrolu her ~2 saniyede bir yap - ioreg cagrisinin maliyeti dusuk kalsin)
            if frames % 60 == 0:
                try:
                    _hid = _hid_idle_seconds()
                    _sil = time.time() - _state.get("last_sound", 0)
                    if _hid > 120.0 and _sil > 30.0:
                        cava.pause_for_sleep()
                    elif _hid < 5.0:
                        cava.resume_from_sleep()
                except Exception:
                    pass
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

            # HAVA SICAKLIGI (tum modlarda sol ust kose)
            draw_weather(surf)

            # surface -> shared memory (ham RGB)
            raw = pygame.image.tostring(surf, "RGB")
            shm.buf[:len(raw)] = raw
            frame_counter.value = frames
            frames += 1

            # parlaklik degistiyse sender'a bildir (yazilimsal karartma)
            if _state.get("brightness_changed"):
                _state["brightness_changed"] = False
                try:
                    bright_val.value = int(_state.get("brightness", 100))
                except Exception:
                    pass

            # FPS sinirla
            dt = 1.0 / FPS
            slp = dt - (time.time() - frame_start)
            if slp > 0:
                time.sleep(slp)

            if frames % 60 == 0:
                fps_now = frames / (time.time() - t0)
                sys.stderr.write(f"\r{frames} kare, {fps_now:.1f} FPS  ")

    # Render thread'i baslat (LCD surekli guncellensin)
    render_thread = threading.Thread(target=render_loop, daemon=True)
    render_thread.start()

    # Ctrl+C (SIGINT): Qt exec_ C++ dongusunde Python signal'lari islenmez.
    # Cozum: bir QTimer her 200ms Python'a kontrol verir -> bekleyen SIGINT islenir.
    import signal as _signal
    def _sigint_handler(sig, frame):
        try:
            send_proc.kill()
        except Exception:
            pass
        try:
            os.system("pkill -f 'cava -p' 2>/dev/null")
        except Exception:
            pass
        os._exit(0)
    try:
        _signal.signal(_signal.SIGINT, _sigint_handler)
        # QTimer: Qt exec_ icindeyken periyodik olarak Python'a don (signal islensin)
        from PyQt5.QtCore import QTimer as _QTimer
        _sigint_timer = _QTimer()
        _sigint_timer.start(200)          # her 200ms
        _sigint_timer.timeout.connect(lambda: None)   # Python'a kontrol ver (signal islensin)
    except Exception:
        pass

    try:
        if qt_app is not None:
            qt_app.exec_()          # Qt olay dongusu ANA THREAD'de (menu donmaz)
        else:
            while _state["running"]:
                time.sleep(0.2)     # tray yoksa bekle
    except KeyboardInterrupt:
        print("\nCikiliyor...")
        _state["running"] = False
    finally:
        _state["running"] = False
        # Cikista paneli TEMIZLE: siyah kareyi shared memory'ye yaz, sender USB'ye gonderir
        try:
            black = pygame.Surface((WIDTH, HEIGHT)); black.fill((0, 0, 0))
            shm.buf[:WIDTH*HEIGHT*3] = pygame.image.tostring(black, "RGB")
            frame_counter.value = frame_counter.value + 1
            time.sleep(0.35)          # sender siyah kareyi yollasin
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
            cava.proc.terminate()
        except Exception:
            pass
        # sender process'i de durdur
        try:
            send_proc.terminate()
        except Exception:
            pass
        print("Temiz kapandi.")
        # process'i KESIN sonlandir (render/hava thread'leri, tray kalmasin)
        os._exit(0)


if __name__ == "__main__":
    main()
