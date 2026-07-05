#!/usr/bin/env python3
import argparse
import os
import math
import shutil
import subprocess
import sys
import multiprocessing as mp
from multiprocessing import shared_memory
import threading
import time

import numpy as np
try:
    import sysmon
except Exception:
    sysmon = None
import rumps

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
import pygame

try:
    import requests
except ImportError:
    print("HATA: 'requests' kutuphanesi gerekli -> pip3 install requests")
    sys.exit(1)

WIDTH, HEIGHT = 1920, 462
FPS = 30
NUM_BARS = 140

TRCC_API_BASE = "http://127.0.0.1:8080"
AUDIO_SOURCE = "Scarlett Solo 4th Gen"

IDLE_THRESHOLD_SECONDS = 5.0  # bu kadar sessizlik sonrasi bekleme ekranina gec

COLOR_THEMES = {
    "Yesil": [(0, 170, 0), (255, 255, 0), (255, 0, 0)],
    "Neon":  [(0, 255, 255), (255, 0, 255), (255, 255, 0)],
    "Mor":   [(120, 0, 200), (220, 60, 220), (255, 220, 255)],
}

# LED Spektrum'a ozel renk paletleri (alt -> ust geçis)
LED_THEMES = {
    "Camgobegi": [(10, 90, 70), (40, 200, 190), (120, 255, 235)],
    "Yesil-Sari-Kirmizi": [(40, 220, 40), (250, 200, 30), (235, 45, 30)],
    "Mavi": [(20, 60, 200), (60, 140, 255), (170, 220, 255)],
    "Mor": [(120, 0, 200), (210, 60, 230), (255, 210, 255)],
}

_running = {"value": True, "paused": False}
_procs = {"cava": None, "trcc": None, "shm": None, "counter": None}
_sender_ref = {"sender": None}
_latest_surface = {"surf": None, "lock": threading.Lock(), "dirty": False}
_state = {"theme": "Yesil", "brightness": 100, "mode": "Spektrum", "led_theme": "Camgobegi"}
left_needle = {"angle": 180.0}
right_needle = {"angle": 180.0}


def get_resource_path(relative_path):
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(sys._MEIPASS, relative_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(script_dir, relative_path)


def find_cava():
    candidates = [
        get_resource_path("cava"),
        "/opt/homebrew/bin/cava",
        "/usr/local/bin/cava",
        shutil.which("cava"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def find_trcc():
    candidates = [
        os.path.expanduser("~/.local/bin/trcc"),
        os.path.expanduser("~/.local/pipx/venvs/trcc-linux/bin/trcc"),
        shutil.which("trcc"),
    ]
    for c in candidates:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    return None


def _free_port(port):
    """Verilen portu tutan TUM surecleri oldur (oksuz/eski trcc serve dahil)."""
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"],
                             capture_output=True, text=True, timeout=3)
        pids = [p for p in out.stdout.split() if p.strip()]
        for pid in pids:
            try:
                subprocess.run(["kill", "-9", pid], timeout=2)
            except Exception:
                pass
        if pids:
            time.sleep(0.8)
        return bool(pids)
    except Exception:
        return False


def ensure_trcc_serve(api_base, port):
    # Saglikli calisan bir trcc serve var mi?
    try:
        r = requests.get(f"{api_base}/devices", timeout=1.5)
        if r.status_code == 200:
            return None
    except requests.RequestException:
        pass

    # Buraya geldiysek ya hic yok ya da port dolu ama yanit vermiyor.
    # Her ihtimale karsi portu temizle.
    _free_port(port)

    trcc_path = find_trcc()
    if not trcc_path:
        print("[uyari] 'trcc' bulunamadi -- otomatik baslatilamadi.")
        return None

    proc = subprocess.Popen(
        [trcc_path, "serve", "--port", str(port)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # Eger ilk denemede "address already in use" ile aninda olduyse,
    # portu bir kez daha zorla temizleyip tekrar dene.
    time.sleep(1.0)
    if proc.poll() is not None:  # surec aninda oldu (port dolu)
        _free_port(port)
        proc = subprocess.Popen(
            [trcc_path, "serve", "--port", str(port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    for _ in range(30):
        time.sleep(0.5)
        try:
            r = requests.get(f"{api_base}/devices", timeout=1.0)
            if r.status_code == 200:
                return proc
        except requests.RequestException:
            continue
    return proc


def write_cava_conf(audio_source, num_bars, fps):
    conf_text = f"""
[general]
bars = {num_bars}
framerate = {fps}
global_gain = 60

[smoothing]
integral = 55
gravity = 150

[input]
method = portaudio
source = {audio_source}

[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_int = 255
"""
    conf_path = os.path.expanduser("~/.temp_cava_vu_trcc.conf")
    with open(conf_path, "w") as f:
        f.write(conf_text)
    return conf_path


def gradient_color(theme_name, ratio):
    stops = COLOR_THEMES.get(theme_name, COLOR_THEMES["Yesil"])
    if ratio < 0.45:
        f = ratio / 0.45
        c1, c2 = stops[0], stops[1]
    elif ratio < 0.88:
        f = (ratio - 0.45) / 0.43
        c1, c2 = stops[1], stops[1]
    else:
        f = (ratio - 0.88) / 0.12
        c1, c2 = stops[1], stops[2]
    r = int(c1[0] + (c2[0] - c1[0]) * f)
    g = int(c1[1] + (c2[1] - c1[1]) * f)
    b = int(c1[2] + (c2[2] - c1[2]) * f)
    return (max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b)))


def sender_process_main(shm_name, frame_counter, w, h, api_base, device_key, theme_dir):
    """Ayri bir process'te calisir -- GIL'den tamamen bagimsiz. Shared memory'den
    ham RGB veriyi okur (pickle yok, dogrudan kopya), PNG'e yazip TRCC'ye gonderir."""
    import pygame as _pg
    import requests as _requests
    _pg.init()
    shm = shared_memory.SharedMemory(name=shm_name)
    session = _requests.Session()
    png_path = os.path.join(theme_dir, "00.png")
    url = f"{api_base.rstrip('/')}/devices/{device_key}/display/theme"
    last_seen = -1
    try:
        while True:
            cur = frame_counter.value
            if cur == -1:
                break
            if cur != last_seen:
                last_seen = cur
                raw = bytes(shm.buf[:w * h * 3])
                try:
                    surf = _pg.image.frombuffer(raw, (w, h), "RGB")
                    _pg.image.save(surf, png_path)
                    session.post(url, json={"path": "live_frame"}, timeout=2.0)
                except Exception:
                    pass
            else:
                time.sleep(0.005)
    finally:
        shm.close()


class TRCCSender:
    def __init__(self, api_base, device_key):
        self.api_base = api_base.rstrip("/")
        self.device_key = device_key
        self.session = requests.Session()
        self.failed_in_a_row = 0
        self.success_count = 0
        self.ready = False
        self.theme_dir = None

    def setup(self):
        try:
            r = self.session.get(f"{self.api_base}/devices", timeout=5)
            r.raise_for_status()
            products = r.json().get("products", [])
            if not products:
                return False
            if not self.device_key:
                self.device_key = products[0]["key"]

            r = self.session.post(f"{self.api_base}/devices/{self.device_key}/connect", timeout=10)
            r.raise_for_status()

            self.session.post(
                f"{self.api_base}/devices/{self.device_key}/display/fit-mode",
                json={"mode": "stretch"}, timeout=5,
            )

            self.theme_dir = os.path.expanduser(
                "~/Library/Application Support/trcc-user/live_frame"
            )
            os.makedirs(self.theme_dir, exist_ok=True)
            config_path = os.path.join(self.theme_dir, "trcc.json")
            if not os.path.isfile(config_path):
                with open(config_path, "w") as f:
                    f.write('{"name": "live_frame", "elements": []}\n')

            self.ready = True
            return True
        except requests.RequestException as e:
            print(f"[hata] TRCC API kurulumu basarisiz ({e}).")
            return False

    def send_surface(self, surface):
        if not self.ready:
            return
        png_path = os.path.join(self.theme_dir, "00.png")
        pygame.image.save(surface, png_path)
        try:
            resp = self.session.post(
                f"{self.api_base}/devices/{self.device_key}/display/theme",
                json={"path": "live_frame"}, timeout=2.0,
            )
            self.success_count += 1
            if resp.status_code < 400:
                self.failed_in_a_row = 0
            else:
                self.failed_in_a_row += 1
        except requests.RequestException:
            self.failed_in_a_row += 1

    def set_brightness(self, value):
        if not self.ready:
            return
        try:
            self.session.post(
                f"{self.api_base}/devices/{self.device_key}/display/brightness",
                json={"percent": value}, timeout=3.0,
            )
        except requests.RequestException as e:
            print(f"[uyari] parlaklik ayarlanamadi: {e}")


def draw_idle_screen(screen, font, t):
    screen.fill((8, 8, 10))
    pulse = int(120 + 80 * abs(((t * 0.6) % 2.0) - 1.0))
    color = (pulse, int(pulse * 0.85), int(pulse * 0.6))
    text = font.render("VINTAGE AUDIO CONSOLE", True, color)
    screen.blit(text, (WIDTH // 2 - text.get_width() // 2, HEIGHT // 2 - text.get_height() // 2))


def draw_classic_vu(screen, font_label, font_power, vol_l, vol_r):
    screen.fill((4, 4, 4))
    radius = 195
    cy = HEIGHT - 75
    cx1 = WIDTH // 4
    cx2 = WIDTH - WIDTH // 4

    target_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    target_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    left_needle["angle"] += (target_l - left_needle["angle"]) * 0.22
    right_needle["angle"] += (target_r - right_needle["angle"]) * 0.22

    db_marks = [
        (180, "-40"), (165, "-30"), (140, "-20"),
        (105, "-10"), (75, "-5"), (45, "0"), (15, "+3dB"),
    ]

    for cx, angle in ((cx1, left_needle["angle"]), (cx2, right_needle["angle"])):
        for deg, label in db_marks:
            rad = math.radians(deg)
            x1 = cx + radius * math.cos(rad)
            y1 = cy - radius * math.sin(rad)
            x2 = cx + (radius - 18) * math.cos(rad)
            y2 = cy - (radius - 18) * math.sin(rad)
            pygame.draw.line(screen, (255, 255, 255), (x1, y1), (x2, y2), 2)
            lx = cx + (radius - 38) * math.cos(rad)
            ly = cy - (radius - 38) * math.sin(rad)
            lbl = font_label.render(label, True, (220, 230, 255))
            screen.blit(lbl, (lx - lbl.get_width() // 2, ly - lbl.get_height() // 2))

        pwr = font_power.render("POWER OUTPUT", True, (200, 220, 255))
        screen.blit(pwr, (cx - pwr.get_width() // 2, cy - 50))

        rad = math.radians(angle)
        nx = cx + (radius - 25) * math.cos(rad)
        ny = cy - (radius - 25) * math.sin(rad)
        pygame.draw.line(screen, (235, 245, 255), (cx, cy), (nx, ny), 2)

    peak_lbl = font_label.render("PEAK", True, (180, 200, 240))
    screen.blit(peak_lbl, (WIDTH // 2 - peak_lbl.get_width() // 2, cy - radius // 2))




_led_texture_cache = {"surf": None, "width": None, "palette": None, "style": None}


def _lerp(c1, c2, f):
    return (
        int(c1[0] + (c2[0] - c1[0]) * f),
        int(c1[1] + (c2[1] - c1[1]) * f),
        int(c1[2] + (c2[2] - c1[2]) * f),
    )


def _get_led_texture(led_bar_width, max_h, palette_name, style="rect"):
    cache = _led_texture_cache
    if (cache["surf"] is not None and cache["width"] == led_bar_width
            and cache["palette"] == palette_name and cache["style"] == style):
        return cache["surf"]

    stops = LED_THEMES.get(palette_name, LED_THEMES["Camgobegi"])
    LED_SEG_H = 16
    LED_SEG_GAP = 4
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
        color = (min(255, max(0, color[0])), min(255, max(0, color[1])), min(255, max(0, color[2])))
        y_top = max_h - y_off - LED_SEG_H
        if style == "dot":
            cx_dot = led_bar_width // 2
            cy_dot = max(0, y_top) + LED_SEG_H // 2
            r_dot = max(2, min(led_bar_width, LED_SEG_H) // 2)
            pygame.draw.circle(tex, color, (cx_dot, cy_dot), r_dot)
        else:
            pygame.draw.rect(tex, color, (0, max(0, y_top), led_bar_width, LED_SEG_H))
        y_off += step

    cache["surf"] = tex
    cache["width"] = led_bar_width
    cache["palette"] = palette_name
    cache["style"] = style
    return tex


# ---- VU METRE (gercek amber foto + hareketli ibre) ----
_raw_vu_image = None
_vu_loaded = False
_vu_scaled_cache = {}
_vu_angles = {"l": 180.0, "r": 180.0}

def _ensure_vu_image():
    global _raw_vu_image, _vu_loaded
    if _vu_loaded:
        return _raw_vu_image
    _vu_loaded = True
    path = get_resource_path("vu_bg.png")
    if os.path.isfile(path):
        try:
            _raw_vu_image = pygame.image.load(path)
        except Exception:
            _raw_vu_image = None
    return _raw_vu_image


def _draw_one_vu(screen, cx, top_y, disp_w, current_angle):
    img0 = _ensure_vu_image()
    if img0 is None:
        return
    ow, oh = img0.get_size()
    disp_h = int(disp_w * oh / ow)
    key = (disp_w, disp_h)
    if key not in _vu_scaled_cache:
        _vu_scaled_cache[key] = pygame.transform.smoothscale(img0, (disp_w, disp_h))
    img = _vu_scaled_cache[key]
    x0 = cx - disp_w // 2
    screen.blit(img, (x0, top_y))
    # Pivot: (660,920)/(1506,1140) oransal
    piv_x = x0 + int(disp_w * (745 / 1506))
    piv_y = top_y + int(disp_h * (920 / 1140))
    # Aci: -20(137 derece) .. +5(36 derece)
    if not math.isfinite(current_angle):
        current_angle = 180.0
    ang = 137 - (180 - current_angle) / 180.0 * 101
    rad = math.radians(ang)
    needle_len = int(disp_w * 0.37)
    nx = piv_x + needle_len * math.cos(rad)
    ny = piv_y - needle_len * math.sin(rad)
    pygame.draw.line(screen, (210, 30, 30), (piv_x, piv_y), (int(nx), int(ny)), 6)
    pygame.draw.circle(screen, (210, 30, 30), (piv_x, piv_y), 9)


def draw_vu_meter(screen, cava_bars, HALF_N):
    screen.fill((0, 0, 0))
    n = len(cava_bars)
    seg = max(1, int(HALF_N * 0.4))
    left_slice = cava_bars[:seg] if n >= seg else cava_bars
    right_slice = cava_bars[HALF_N:HALF_N + seg] if n >= HALF_N + seg else [0]
    vol_l = (float(np.mean(left_slice)) / 255.0) if len(left_slice) else 0.0
    vol_r = (float(np.mean(right_slice)) / 255.0) if len(right_slice) else 0.0
    if not math.isfinite(vol_l): vol_l = 0.0
    if not math.isfinite(vol_r): vol_r = 0.0
    tgt_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    tgt_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    _vu_angles["l"] += (tgt_l - _vu_angles["l"]) * 0.22
    _vu_angles["r"] += (tgt_r - _vu_angles["r"]) * 0.22
    # LCD 1920x462: iki gosterge yan yana, ekrani kaplasin
    disp_w = min(int(WIDTH * 0.46), int(HEIGHT * 1006 / 462) if HEIGHT else 900)
    disp_w = int(min(disp_w, 1000) * 0.70)  # ~%30 kucult
    # dikeyde ortala
    ow, oh = (1506, 1140)
    disp_h = int(disp_w * oh / ow)
    top_y = max(0, (HEIGHT - disp_h) // 2)
    cx1 = int(WIDTH * 0.25)
    cx2 = int(WIDTH * 0.75)
    _draw_one_vu(screen, cx1, top_y, disp_w, _vu_angles["l"])
    _draw_one_vu(screen, cx2, top_y, disp_w, _vu_angles["r"])


# ---- OLCUM PANELI (APx555 tarzi -- cava verisinden dürüst hesaplar) ----
_meter_smooth = {"rms_l": 0.0, "rms_r": 0.0, "peak": 0.0, "peak_l": 0.0, "peak_r": 0.0,
                 "freq_l": 0.0, "freq_r": 0.0, "centroid": 0.0, "bass": 0.0,
                 "bal_l": 0.0, "bal_r": 0.0, "bal_pct": 50.0}
_meter_fonts = {}

def _meter_font(size, bold=True):
    key = (size, bold)
    if key not in _meter_fonts:
        _meter_fonts[key] = pygame.font.SysFont("Menlo", size, bold=bold)
    return _meter_fonts[key]


def _bar_to_hz(bar_index, total_bars):
    # cava bantlari ~50Hz..16kHz logaritmik. Yaklasik Hz dondur.
    if total_bars <= 1:
        return 0.0
    f_min, f_max = 50.0, 16000.0
    t = bar_index / (total_bars - 1)
    return f_min * (f_max / f_min) ** t


def _level_color(ratio):
    # Cogu seviye yesil; sadece tepeye yakin uyari renkleri.
    # 0..0.85 yesil (sabit), 0.85..0.95 yesil->sari, 0.95..1.0 sari->kirmizi
    r = max(0.0, min(1.0, ratio))
    GREEN = (60, 230, 90)
    if r < 0.85:
        return GREEN
    elif r < 0.95:
        f = (r - 0.85) / 0.10
        return (int(60 + (245-60)*f), int(230 + (210-230)*f), int(90 + (40-90)*f))
    else:
        f = (r - 0.95) / 0.05
        return (int(245 + (235-245)*f), int(210 + (40-210)*f), int(40 + (35-40)*f))


def _sm_font(size, bold=True):
    return _meter_font(size, bold)


def _sm_font(size, bold=True):
    return _meter_font(size, bold)


def draw_sysmon(screen, fps):
    screen.fill((8, 10, 8))
    mon = _get_sysmon()
    if mon is None:
        f = _meter_font(40)
        screen.blit(f.render("Sensor okunamadi", True, (200, 80, 80)), (60, HEIGHT//2 - 20))
        return
    d = mon.snapshot()

    GREEN = (60, 230, 90)
    DARK = (20, 24, 20)
    GREY = (60, 66, 60)
    WHITE = (80, 220, 215)
    DIM = (90, 175, 175)

    def temp_color(t):
        if t is None: return GREY
        if t < 65: return (60, 230, 90)
        if t < 80: return (245, 210, 60)
        return (235, 60, 40)

    cpu_t = d.get("cpu_pkg")
    gpu_t = d.get("gpu_temp")
    use = d.get("cpu_usage")
    gpu_u = d.get("gpu_usage")
    ram = d.get("ram_pct")
    gpu_p = d.get("gpu_power")
    frq = d.get("cpu_freq_real") or d.get("cpu_freq")
    cpu_p = d.get("cpu_power")
    gfan = d.get("gpu_fan_rpm")
    sfans = d.get("sys_fans") or []
    sfan1 = sfans[0] if len(sfans) > 0 else None
    sfan2 = sfans[1] if len(sfans) > 1 else None
    nd = d.get("net_down"); nu = d.get("net_up")
    def net_fmt(mbps):
        if mbps is None:
            return ("--", "kB/s", 0)
        if mbps < 1.0:
            return (f"{mbps*1024:.0f}", "kB/s", mbps/100.0)
        return (f"{mbps:.1f}", "MB/s", mbps/100.0)
    nd_txt, nd_unit, nd_frac = net_fmt(nd)
    nu_txt, nu_unit, nu_frac = net_fmt(nu)

    # Her cubuk: (etiket, deger_metni, birim, dolu_oran 0..1, renk)
    def col(t): return temp_color(t)
    bars = [
        ("CPU", f"{cpu_t:.0f}" if cpu_t else "--", "C", (cpu_t/100.0) if cpu_t else 0, col(cpu_t)),
        ("GPU", f"{gpu_t:.0f}" if gpu_t is not None else "--", "C", (gpu_t/100.0) if gpu_t else 0, col(gpu_t)),
        ("CPU", f"{use:.0f}" if use is not None else "--", "%", (use/100.0) if use is not None else 0, GREEN),
        ("GPU", f"{gpu_u:.0f}" if gpu_u is not None else "--", "%", (gpu_u/100.0) if gpu_u is not None else 0, GREEN),
        ("RAM", f"{ram:.0f}" if ram is not None else "--", "%", (ram/100.0) if ram is not None else 0, GREEN),
        ("GHz", f"{frq/1000:.1f}" if frq else "--", "", (frq/5700.0) if frq else 0, GREEN),
        ("CGuc", f"{cpu_p:.0f}" if cpu_p is not None else "--", "W", (cpu_p/125.0) if cpu_p else 0, GREEN),
        ("GGuc", f"{gpu_p:.0f}" if gpu_p is not None else "--", "W", (gpu_p/350.0) if gpu_p else 0, GREEN),
        ("GFan", f"{gfan:.0f}" if gfan else "0", "rpm", (gfan/3000.0) if gfan else 0, GREEN),
        ("Fan1", f"{sfan1:.0f}" if sfan1 else "--", "rpm", (sfan1/3000.0) if sfan1 else 0, GREEN),
        ("Fan2", f"{sfan2:.0f}" if sfan2 else "--", "rpm", (sfan2/3000.0) if sfan2 else 0, GREEN),
        ("Indir", nd_txt, nd_unit, nd_frac, GREEN),
        ("Yukle", nu_txt, nu_unit, nu_frac, GREEN),
    ]

    n = len(bars)
    margin = 30
    # Her cubuga ayrilan yuva genisligi
    slot_w = (WIDTH - 2*margin) // n
    bar_w = max(8, int(slot_w * 0.25))  # cubuk yuvanin %25'i (incelt)
    # Dikey alan
    top_val_y = 20      # deger yazisi
    bar_top = 70        # bar tepe
    bar_bottom = HEIGHT - 52  # bar dip (altta etiket)
    bar_h_full = bar_bottom - bar_top

    vfont = _sm_font(42)
    ufont = _sm_font(20)
    lfont = _sm_font(22)

    for idx, (lbl, vtxt, unit, frac, color) in enumerate(bars):
        frac = max(0.0, min(1.0, frac))
        slot_x = margin + idx * slot_w
        cx = slot_x + slot_w // 2       # yuva merkezi
        x = cx - bar_w // 2             # cubuk sol kenari (ortali)
        # arka plan (bos) cubuk
        pygame.draw.rect(screen, DARK, (x, bar_top, bar_w, bar_h_full))
        # dolu kisim (alttan yukari)
        fh = int(bar_h_full * frac)
        pygame.draw.rect(screen, color, (x, bar_bottom - fh, bar_w, fh))
        # cerceve
        pygame.draw.rect(screen, GREY, (x, bar_top, bar_w, bar_h_full), 1)
        # deger (yuva merkezine ortali, buyuk)
        vs = vfont.render(vtxt, True, color)
        screen.blit(vs, (cx - vs.get_width()//2, 24))
        if unit:
            us = ufont.render(unit, True, DIM)
            screen.blit(us, (cx - us.get_width()//2, 24 + vs.get_height() - 2))
        # etiket (altta, ortali)
        ls = lfont.render(lbl, True, WHITE)
        screen.blit(ls, (cx - ls.get_width()//2, bar_bottom + 10))


_sysmon_instance = None

def _get_sysmon():
    global _sysmon_instance
    if _sysmon_instance is None and sysmon is not None:
        try:
            _sysmon_instance = sysmon.SysMonitor()
        except Exception:
            pass
    return _sysmon_instance


def draw_meter_panel(screen, cava_bars, HALF_N, fps):
    screen.fill((8, 10, 8))
    n = len(cava_bars)
    left = np.array(cava_bars[:HALF_N], dtype=float) if n >= HALF_N else np.zeros(HALF_N)
    right = np.array(cava_bars[HALF_N:HALF_N*2], dtype=float) if n >= HALF_N*2 else np.zeros(HALF_N)

    # RMS (0..255 -> normalize 0..1)
    rms_l = float(np.sqrt(np.mean(left**2))) / 255.0 if left.size else 0.0
    rms_r = float(np.sqrt(np.mean(right**2))) / 255.0 if right.size else 0.0
    # Tepe (kanal bazli)
    peak_l = (float(left.max()) / 255.0) if left.size else 0.0
    peak_r = (float(right.max()) / 255.0) if right.size else 0.0
    peak = max(peak_l, peak_r)
    # Bas orani: dusuk bantlarin enerjisinin toplama orani
    if allb_sum_ready := (left.size and right.size):
        _all = left + right
        _tot = float(_all.sum())
        _bass = float(_all[:max(1, HALF_N // 4)].sum())
        bass_ratio = (_bass / _tot) if _tot > 1 else 0.0
    else:
        bass_ratio = 0.0
    # Stereo denge: her kanalin toplam enerjisi (normalize)
    eng_l = float(left.sum()) if left.size else 0.0
    eng_r = float(right.sum()) if right.size else 0.0
    eng_max = max(eng_l, eng_r, 1.0)
    bal_l = eng_l / eng_max  # 0..1, baskin kanal ~1.0
    bal_r = eng_r / eng_max
    # Yuzde fark (sag-sol dengesi): 50 = esit, <50 sol agir, >50 sag agir
    bal_pct = (eng_r / (eng_l + eng_r) * 100) if (eng_l + eng_r) > 1 else 50.0
    # Baskin frekans (en yuksek bar)
    freq_l = _bar_to_hz(int(np.argmax(left)), HALF_N) if left.size and left.max() > 5 else 0.0
    freq_r = _bar_to_hz(int(np.argmax(right)), HALF_N) if right.size and right.max() > 5 else 0.0
    # Spektral merkez (enerji agirlikli ortalama frekans)
    allb = (left + right)
    if allb.sum() > 1:
        idx = np.arange(HALF_N)
        cen_idx = float((idx * allb).sum() / allb.sum())
        centroid = _bar_to_hz(cen_idx, HALF_N)
    else:
        centroid = 0.0

    # Yumusatma
    s = _meter_smooth
    a = 0.3
    s["rms_l"] += (rms_l - s["rms_l"]) * a
    s["rms_r"] += (rms_r - s["rms_r"]) * a
    s["peak"] += (peak - s["peak"]) * a
    s["peak_l"] += (peak_l - s["peak_l"]) * a
    s["peak_r"] += (peak_r - s["peak_r"]) * a
    s["bass"] += (bass_ratio - s["bass"]) * a
    s["bal_l"] += (bal_l - s["bal_l"]) * a
    s["bal_r"] += (bal_r - s["bal_r"]) * a
    s["bal_pct"] += (bal_pct - s["bal_pct"]) * a
    s["freq_l"] += (freq_l - s["freq_l"]) * a
    s["freq_r"] += (freq_r - s["freq_r"]) * a
    s["centroid"] += (centroid - s["centroid"]) * a

    GREEN = (60, 230, 90)
    GREY = (70, 75, 70)
    DARK = (18, 22, 18)

    def to_db(v):
        if v <= 0.0001:
            return -60.0
        return max(-60.0, 20.0 * math.log10(v))

    # 2x2 grid
    pw = WIDTH // 2
    ph = HEIGHT // 2
    panels = [
        ("RMS SEVIYE", [("Ch1", s["rms_l"], f"{to_db(s['rms_l']):.1f}", "dB"),
                        ("Ch2", s["rms_r"], f"{to_db(s['rms_r']):.1f}", "dB")], False),
        ("FREKANS", [("Ch1", min(1.0, s["freq_l"]/16000), f"{s['freq_l']/1000:.3f}", "kHz"),
                     ("Ch2", min(1.0, s["freq_r"]/16000), f"{s['freq_r']/1000:.3f}", "kHz")], False),
        ("STEREO DENGE", [("Sol", s["bal_l"], f"{100-s['bal_pct']:.0f}", "%"),
                          ("Sag", s["bal_r"], f"{s['bal_pct']:.0f}", "%")], False),
        ("MERKEZ", [("Frk", min(1.0, s["centroid"]/16000), f"{s['centroid']/1000:.3f}", "kHz"),
                    ("Bas", s["bass"], f"{s['bass']*100:.0f}", "%")], False),
    ]

    for pi, (title, rows, colored) in enumerate(panels):
        px = (pi % 2) * pw
        py = (pi // 2) * ph
        # baslik
        tfont = _meter_font(22)
        screen.blit(tfont.render(title, True, (210, 215, 210)), (px + 12, py + 8))
        # satirlar (Ch1/Ch2)
        row_h = (ph - 44) // 2
        for ri, (label, val, num, unit) in enumerate(rows):
            ry = py + 40 + ri * row_h
            lblf = _meter_font(26)
            screen.blit(lblf.render(label, True, (235, 240, 235)), (px + 14, ry + row_h//2 - 16))
            # bar alani -- sag tarafta sayiya yer birak (barlar sayinin altina girmesin)
            bar_x = px + 110
            num_area = 230  # sayi+birim icin ayrilan sag bosluk
            bar_w_full = pw - 110 - num_area - 16
            bar_h = 16
            bar_y = ry + row_h // 2 - bar_h // 2
            pygame.draw.rect(screen, DARK, (bar_x, bar_y, bar_w_full, bar_h))
            fill_w = int(bar_w_full * max(0.0, min(1.0, val)))
            fill_color = _level_color(val) if colored else GREEN
            pygame.draw.rect(screen, fill_color, (bar_x, bar_y, fill_w, bar_h))
            pygame.draw.rect(screen, GREY, (bar_x + fill_w, bar_y, bar_w_full - fill_w, bar_h))
            # sayi (barin saginda, ayri alanda)
            if num:
                numf = _meter_font(38)
                unitf = _meter_font(22)
                ns = numf.render(num, True, GREEN)
                us = unitf.render(unit, True, GREEN)
                num_cy = ry + row_h // 2
                us_x = px + pw - us.get_width() - 14
                ns_x = us_x - ns.get_width() - 8
                screen.blit(ns, (ns_x, num_cy - ns.get_height() // 2))
                screen.blit(us, (us_x, num_cy - us.get_height() // 2 + 4))


def draw_led_spectrum(screen, cava_bars, smooth_bars, peak_bars, peak_timers, fps,
                       HALF_N, LEFT_START, RIGHT_START, BARS_BOTTOM_Y, BARS_MAX_HEIGHT, led_style="rect"):
    screen.fill((0, 0, 0))
    LED_BAR_GAP = 10
    MERGE = 1  # her MERGE kadar komsu bar'i birlestirip daha genis tek blok yap
    num_bars = len(cava_bars)
    base_bar_width = max(2, (WIDTH - 2 * 20 - 40) // num_bars - LED_BAR_GAP)
    merged_bar_width = base_bar_width * MERGE + LED_BAR_GAP * (MERGE - 1)

    texture = _get_led_texture(merged_bar_width, BARS_MAX_HEIGHT, _state["led_theme"], led_style)

    i = 0
    while i < num_bars:
        group = range(i, min(i + MERGE, num_bars))
        h_max = 0
        for j in group:
            target = int((cava_bars[j] / 255.0) * BARS_MAX_HEIGHT * 0.62)
            smooth_bars[j] += (target - smooth_bars[j]) * 0.2
            hj = min(int(smooth_bars[j]), BARS_MAX_HEIGHT)
            if hj >= peak_bars[j]:
                peak_bars[j] = hj
                peak_timers[j] = fps * 0.7
            else:
                if peak_timers[j] > 0:
                    peak_timers[j] -= 1
                else:
                    peak_bars[j] = max(0, peak_bars[j] - 3)
            h_max = max(h_max, hj)

        if i < HALF_N:
            x_pos = LEFT_START + (i // MERGE) * (merged_bar_width + LED_BAR_GAP)
        else:
            offset = i - HALF_N
            x_pos = RIGHT_START + (offset // MERGE) * (merged_bar_width + LED_BAR_GAP)

        if h_max > 1:
            src_rect = pygame.Rect(0, BARS_MAX_HEIGHT - h_max, merged_bar_width, h_max)
            screen.blit(texture, (x_pos, BARS_BOTTOM_Y - h_max), area=src_rect)

        peak_h = max(int(peak_bars[j]) for j in group)
        if peak_h > 2:
            pygame.draw.rect(screen, (180, 255, 245), (x_pos, BARS_BOTTOM_Y - peak_h, merged_bar_width, 3))

        i += MERGE


def audio_loop(args):
    pygame.init()
    pygame.font.init()
    screen = pygame.Surface((WIDTH, HEIGHT))
    idle_font = pygame.font.SysFont("Menlo", 40, bold=True)
    vu_label_font = pygame.font.SysFont("Arial", 28, bold=True)
    vu_power_font = pygame.font.SysFont("Arial", 30, bold=True)

    cava_path = find_cava()
    if not cava_path:
        print("HATA: 'cava' bulunamadi.")
        return

    _procs["trcc"] = ensure_trcc_serve(args.api_base, args.api_base.rsplit(":", 1)[-1])

    conf_path = write_cava_conf(args.source, NUM_BARS, args.fps)
    cava_cmd = ["script", "-q", "/dev/null", cava_path, "-p", conf_path]
    cava_proc = subprocess.Popen(cava_cmd, stdout=subprocess.PIPE, text=True, bufsize=1)
    _procs["cava"] = cava_proc

    latest = {"value": "", "lock": threading.Lock()}

    def _reader():
        for raw_line in cava_proc.stdout:
            with latest["lock"]:
                latest["value"] = raw_line.strip()

    threading.Thread(target=_reader, daemon=True).start()

    sender = TRCCSender(args.api_base, args.device_key)
    sender.setup()
    _sender_ref["sender"] = sender
    if not sender.ready:
        pass
    if sender.ready:
        shm_size = WIDTH * HEIGHT * 3
        shm = shared_memory.SharedMemory(create=True, size=shm_size)
        frame_counter = mp.Value("q", 0, lock=False)
        send_proc = mp.Process(
            target=sender_process_main,
            args=(shm.name, frame_counter, WIDTH, HEIGHT,
                  args.api_base, sender.device_key, sender.theme_dir),
            daemon=True,
        )
        send_proc.start()
        _procs["shm"] = shm
        _procs["counter"] = frame_counter
        _procs["sender_proc"] = send_proc
    if not sender.ready:
        cava_proc.terminate()
        try:
            os.remove(conf_path)
        except OSError:
            pass
        return

    MARGIN_X = 20
    CENTER_GAP = 40
    HALF_N = NUM_BARS // 2
    USABLE_WIDTH = WIDTH - 2 * MARGIN_X - CENTER_GAP
    HALF_USABLE_WIDTH = USABLE_WIDTH // 2
    BAR_WIDTH = max(1, HALF_USABLE_WIDTH // HALF_N - 2)
    LEFT_START = MARGIN_X
    RIGHT_START = MARGIN_X + HALF_USABLE_WIDTH + CENTER_GAP
    BARS_BOTTOM_Y = HEIGHT
    BARS_MAX_HEIGHT = HEIGHT

    smooth_bars = np.zeros(NUM_BARS)
    peak_bars = np.zeros(NUM_BARS)
    peak_timers = np.zeros(NUM_BARS)

    # macOS App Nap'i engelle: uygulama odaksizken/az aktifken sistem
    # CPU'yu kisip dongumuzu yavaslatiyor (VU Metre gibi hafif modlarda donma).
    _activity_token = None
    try:
        from Foundation import NSProcessInfo, NSActivityUserInitiated
        _opts = NSActivityUserInitiated | 0x00FFFFFF  # IdleSystemSleepDisabled vb.
        _activity_token = NSProcessInfo.processInfo().beginActivityWithOptions_reason_(
            _opts, "Vintage Audio Console canli gorsellestirme"
        )
    except Exception:
        pass

    clock = pygame.time.Clock()
    frame_interval = 1.0 / args.fps
    send_interval = 1.0 / 30.0  # LCD'ye gonderim hizi (mod'a gore degisecek)
    last_send = 0.0
    last_audio_time = time.time()
    t0 = time.time()
    led_frame_skip = 0
    led_frame_skip = 0

    while _running["value"]:
        pygame.event.pump()  # macOS event kuyrugunu bosalt -- App Nap/askiya almayi onler
        if _running["paused"]:
            time.sleep(0.3)
            continue

        with latest["lock"]:
            line = latest["value"]

        cava_bars = [0] * NUM_BARS
        if line:
            try:
                cava_bars = [int(x) for x in line.split(";") if x]
                if len(cava_bars) < NUM_BARS:
                    cava_bars += [0] * (NUM_BARS - len(cava_bars))
            except ValueError:
                cava_bars = [0] * NUM_BARS

        cava_bars = cava_bars[:HALF_N] + cava_bars[HALF_N:][::-1]
        total_energy = sum(cava_bars)
        now = time.time()
        if total_energy > 30:
            last_audio_time = now

        _sensor_mode = _state["mode"] in ("Sistem Monitoru", "Olcum Paneli")
        if (now - last_audio_time > IDLE_THRESHOLD_SECONDS) and not _sensor_mode:
            draw_idle_screen(screen, idle_font, now - t0)
        elif _state["mode"] == "LED Spektrum":
            draw_led_spectrum(screen, cava_bars, smooth_bars, peak_bars, peak_timers,
                               args.fps, HALF_N, LEFT_START, RIGHT_START,
                               BARS_BOTTOM_Y, BARS_MAX_HEIGHT, "rect")
        elif _state["mode"] == "LED Nokta":
            draw_led_spectrum(screen, cava_bars, smooth_bars, peak_bars, peak_timers,
                               args.fps, HALF_N, LEFT_START, RIGHT_START,
                               BARS_BOTTOM_Y, BARS_MAX_HEIGHT, "dot")
        elif _state["mode"] == "VU Metre":
            draw_vu_meter(screen, cava_bars, HALF_N)
        elif _state["mode"] == "Olcum Paneli":
            draw_meter_panel(screen, cava_bars, HALF_N, args.fps)
        elif _state["mode"] == "Sistem Monitoru":
            draw_sysmon(screen, args.fps)
        else:
            screen.fill((10, 10, 12))
            theme_name = _state["theme"]
            for i in range(NUM_BARS):
                target = int((cava_bars[i] / 255.0) * BARS_MAX_HEIGHT * 0.75)
                smooth_bars[i] += (target - smooth_bars[i]) * 0.85
                h = min(int(smooth_bars[i]), BARS_MAX_HEIGHT)

                if h >= peak_bars[i]:
                    peak_bars[i] = h
                    peak_timers[i] = args.fps * 0.7
                else:
                    if peak_timers[i] > 0:
                        peak_timers[i] -= 1
                    else:
                        peak_bars[i] = max(0, peak_bars[i] - 3)

                if i < HALF_N:
                    x_pos = LEFT_START + i * (BAR_WIDTH + 2)
                else:
                    x_pos = RIGHT_START + (i - HALF_N) * (BAR_WIDTH + 2)

                if h > 2:
                    for y_off in range(0, h, 4):
                        ratio = y_off / BARS_MAX_HEIGHT
                        seg = gradient_color(theme_name, ratio)
                        pygame.draw.rect(screen, seg, (x_pos, BARS_BOTTOM_Y - y_off - 4, BAR_WIDTH, 4))

                peak_h = int(peak_bars[i])
                if peak_h > 2:
                    pygame.draw.rect(screen, (245, 215, 150), (x_pos, BARS_BOTTOM_Y - peak_h, BAR_WIDTH, 2))

        # LED Spektrum'da daha dusuk gonderim hizi (11), digerlerinde 30
        cur_send_interval = 1.0 / 30.0
        if now - last_send >= cur_send_interval:
            shm = _procs.get("shm")
            counter = _procs.get("counter")
            if shm is not None and counter is not None:
                raw = pygame.image.tostring(screen, "RGB")
                shm.buf[:len(raw)] = raw
                counter.value += 1
            last_send = now

        clock.tick(args.fps)

    cava_proc.terminate()
    try:
        os.remove(conf_path)
    except OSError:
        pass
    pygame.quit()


class VintageAudioApp(rumps.App):
    def __init__(self, args):
        icon_path = get_resource_path("menubar_icon.png")
        if os.path.isfile(icon_path):
            super().__init__("VAC", icon=icon_path, title=None)
        else:
            super().__init__("VAC", title="🎚")
        self.args = args

        # GORUNUM menusu: her gorunum kendi renk alt menusuyle birlikte
        view_menu = rumps.MenuItem("Gorunum")

        # Spektrum + altinda renk temalari
        spektrum_item = rumps.MenuItem("Spektrum", callback=self.set_mode)
        for name in COLOR_THEMES:
            spektrum_item.add(rumps.MenuItem(name, callback=self.set_theme))
        view_menu.add(spektrum_item)

        # LED Spektrum + altinda LED renkleri
        led_item = rumps.MenuItem("LED Spektrum", callback=self.set_mode)
        for name in LED_THEMES:
            led_item.add(rumps.MenuItem(name, callback=self.set_led_theme))
        view_menu.add(led_item)

        led_dot_item = rumps.MenuItem("LED Nokta", callback=self.set_mode)
        for name in LED_THEMES:
            led_dot_item.add(rumps.MenuItem(name, callback=self.set_led_theme_dot))
        view_menu.add(led_dot_item)

        vu_item = rumps.MenuItem("VU Metre", callback=self.set_mode)
        view_menu.add(vu_item)

        meter_item = rumps.MenuItem("Olcum Paneli", callback=self.set_mode)
        view_menu.add(meter_item)

        sysmon_item = rumps.MenuItem("Sistem Monitoru", callback=self.set_mode)
        view_menu.add(sysmon_item)

        bright_menu = rumps.MenuItem("Parlaklik")
        for val in (25, 50, 75, 100):
            item = rumps.MenuItem(f"%{val}", callback=self.set_brightness)
            bright_menu.add(item)

        self.quit_button = None  # rumps varsayilan Quit'i kaldir (bizim 'Cikis' var)
        self.menu = [view_menu, bright_menu]

        self._register_sleep_wake()
        self.audio_thread = threading.Thread(target=audio_loop, args=(args,), daemon=True)
        self.audio_thread.start()

    def set_theme(self, sender_item):
        _state["theme"] = sender_item.title
        _state["mode"] = "Spektrum"
        for item in self.menu["Gorunum"]["Spektrum"].values():
            item.state = (item.title == sender_item.title)

    def set_mode(self, sender_item):
        _state["mode"] = sender_item.title
        for key in ("Spektrum", "LED Spektrum", "LED Nokta", "VU Metre", "Olcum Paneli", "Sistem Monitoru"):
            self.menu["Gorunum"][key].state = (key == sender_item.title)

    def set_led_theme(self, sender_item):
        _state["led_theme"] = sender_item.title
        _state["mode"] = "LED Spektrum"
        for item in self.menu["Gorunum"]["LED Spektrum"].values():
            item.state = (item.title == sender_item.title)

    def set_led_theme_dot(self, sender_item):
        _state["led_theme"] = sender_item.title
        _state["mode"] = "LED Nokta"
        for item in self.menu["Gorunum"]["LED Nokta"].values():
            item.state = (item.title == sender_item.title)

    def set_brightness(self, sender_item):
        val = int(sender_item.title.strip("%"))
        s = _sender_ref["sender"]
        if s is not None:
            s.set_brightness(val)
        for item in self.menu["Parlaklik"].values():
            item.state = (item is sender_item)

    def _register_sleep_wake(self):
        try:
            from Foundation import NSObject
            import objc
            from AppKit import NSWorkspace

            class _SleepObserver(NSObject):
                def workspaceWillSleep_(self, note):
                    _running["paused"] = True

                def workspaceDidWake_(self, note):
                    _running["paused"] = False

            self._observer = _SleepObserver.alloc().init()
            nc = NSWorkspace.sharedWorkspace().notificationCenter()
            nc.addObserver_selector_name_object_(
                self._observer, "workspaceWillSleep:",
                "NSWorkspaceWillSleepNotification", None,
            )
            nc.addObserver_selector_name_object_(
                self._observer, "workspaceDidWake:",
                "NSWorkspaceDidWakeNotification", None,
            )
        except Exception as e:
            print(f"[uyari] uyku/uyanma bildirimleri kurulamadi: {e}")

    @rumps.clicked("Cikis")
    def quit_app(self, _):
        _running["value"] = False
        sender = _sender_ref["sender"]
        if sender is not None and sender.ready:
            try:
                blank = pygame.Surface((WIDTH, HEIGHT))
                blank.fill((0, 0, 0))
                sender.send_surface(blank)
            except Exception:
                pass
        for name, proc in _procs.items():
            if proc is not None and hasattr(proc, "kill"):
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            subprocess.Popen(["pkill", "-9", "-f", "cava"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
        os._exit(0)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default=AUDIO_SOURCE)
    parser.add_argument("--api-base", default=TRCC_API_BASE)
    parser.add_argument("--device-key", default=None)
    parser.add_argument("--fps", type=int, default=FPS)
    args = parser.parse_args()

    VintageAudioApp(args).run()


if __name__ == "__main__":
    mp.freeze_support()  # PyInstaller + multiprocessing icin SART (macOS spawn)
    try:
        mp.set_start_method("fork")  # PyInstaller ile daha uyumlu, arg cakismasi olmaz
    except RuntimeError:
        pass
    main()
