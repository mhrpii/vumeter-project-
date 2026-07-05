#!/usr/bin/env python3
import subprocess
import numpy as np
import pygame
import sys
import math
import os
import shutil
import threading

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
NUM_BARS = 140
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
}

pygame.init()
pygame.font.init()
screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
pygame.display.set_caption("Vintage Audio Console Pro")
clock = pygame.time.Clock()

font_main = pygame.font.SysFont("serif", 24, bold=True)
font_hint = pygame.font.SysFont("Menlo", 16)
font_vu_label = pygame.font.SysFont("Menlo", 18)
font_vu_power = pygame.font.SysFont("Menlo", 22, bold=True)

# --- CAVA CONFIG ---
cava_conf_text = f"""
[general]
bars = {NUM_BARS}
framerate = {FPS}
global_gain = 60

[smoothing]
integral = 55
gravity = 150
[input]
method = portaudio
source = Scarlett Solo 4th Gen
[output]
method = raw
raw_target = /dev/stdout
data_format = ascii
ascii_max_int = 255
"""
conf_path = os.path.expanduser("~/.temp_cava_vu.conf")
with open(conf_path, "w") as f:
    f.write(cava_conf_text)

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

cava_path = find_cava()
if not cava_path:
    print("HATA: 'cava' bulunamadi. 'brew install cava' ile kurabilirsin.")
    sys.exit(1)

cava_cmd = ["script", "-q", "/dev/null", cava_path, "-p", conf_path]
cava_proc = subprocess.Popen(cava_cmd, stdout=subprocess.PIPE, text=True, bufsize=1)

_latest_line = {"value": "", "lock": threading.Lock()}

def _cava_reader():
    for raw_line in cava_proc.stdout:
        with _latest_line["lock"]:
            _latest_line["value"] = raw_line.strip()

threading.Thread(target=_cava_reader, daemon=True).start()

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
def draw_spectrum(W_CUR, H_CUR, stereo_bars, theme_name, cava_bars):
    # Ust yari: iki gercek VU fotografi
    disp_w = max(220, min(int(W_CUR * 0.30), 460))
    top_y = 20
    cx1 = int(W_CUR * 0.27)
    cx2 = int(W_CUR * 0.73)

    vol_l = np.mean(cava_bars[:int(HALF_BARS * 0.4)]) / 255.0 if len(cava_bars) > 0 else 0
    vol_r = np.mean(cava_bars[HALF_BARS:int(HALF_BARS + (HALF_BARS * 0.4))]) / 255.0 if len(cava_bars) > 0 else 0
    global left_angle, right_angle
    tgt_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    tgt_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    left_angle += (tgt_l - left_angle) * 0.22
    right_angle += (tgt_r - right_angle) * 0.22
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
    bar_w = max(1, half_usable // HALF_BARS - 2)
    left_start = MARGIN_X
    right_start = MARGIN_X + half_usable + CENTER_GAP

    for i in range(NUM_BARS):
        target_bar = int((stereo_bars[i] / 255.0) * BARS_MAX_HEIGHT * 0.9)
        smooth_bars[i] += (target_bar - smooth_bars[i]) * 0.85
        h = min(int(smooth_bars[i]), BARS_MAX_HEIGHT)

        if h >= peak_bars[i]:
            peak_bars[i] = h
            peak_timers[i] = FPS * 0.7
        else:
            if peak_timers[i] > 0: peak_timers[i] -= 1
            else:
                peak_bars[i] -= 3
                if peak_bars[i] < 0: peak_bars[i] = 0

        if i < HALF_BARS:
            x_pos = left_start + i * (bar_w + 2)
        else:
            x_pos = right_start + (i - HALF_BARS) * (bar_w + 2)

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


# ---------- GORUNUM: VU METRE (sadece iki gosterge) ----------
def draw_vu_only(W_CUR, H_CUR, cava_bars):
    screen.fill((15, 15, 15))
    global left_angle, right_angle
    vol_l = np.mean(cava_bars[:int(HALF_BARS * 0.4)]) / 255.0 if len(cava_bars) > 0 else 0
    vol_r = np.mean(cava_bars[HALF_BARS:int(HALF_BARS + (HALF_BARS * 0.4))]) / 255.0 if len(cava_bars) > 0 else 0
    if not math.isfinite(vol_l): vol_l = 0.0
    if not math.isfinite(vol_r): vol_r = 0.0
    tgt_l = max(0, min(180, 180 - (vol_l * 180 * 1.35)))
    tgt_r = max(0, min(180, 180 - (vol_r * 180 * 1.35)))
    left_angle += (tgt_l - left_angle) * 0.22
    right_angle += (tgt_r - right_angle) * 0.22

    disp_w = min(int(W_CUR * 0.42), 620)
    ow, oh = (1506, 1140)
    disp_h = int(disp_w * oh / ow)
    top_y = max(0, (H_CUR - disp_h) // 2)
    cx1 = int(W_CUR * 0.27)
    cx2 = int(W_CUR * 0.73)
    pl = draw_photo_vu(cx1, top_y, disp_w)
    pr = draw_photo_vu(cx2, top_y, disp_w)
    if pl: draw_photo_needle(pl[0], pl[1], pl[2], left_angle)
    if pr: draw_photo_needle(pr[0], pr[1], pr[2], right_angle)


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
            peak_timers[i] = FPS * 0.7
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


def draw_hint():
    txt = "1:Spektrum  2:LED  3:LED Nokta  4:VU  M:Monitor  TAB:Renk  Q:Cikis"
    surf = font_hint.render(txt, True, (120, 120, 130))
    screen.blit(surf, (10, 8))


running = True
while running:
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
            elif event.key == pygame.K_m:
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
                else:
                    state["theme_idx"] = (state["theme_idx"] + 1) % len(COLOR_THEME_NAMES)

    with _latest_line["lock"]:
        line = _latest_line["value"]

    cava_bars = [0] * NUM_BARS
    if line:
        try:
            cava_bars = [int(x) for x in line.split(';') if x]
            if len(cava_bars) < NUM_BARS:
                cava_bars += [0] * (NUM_BARS - len(cava_bars))
        except ValueError:
            cava_bars = [0] * NUM_BARS

    stereo_bars = cava_bars[:HALF_BARS] + cava_bars[HALF_BARS:][::-1]

    mode = state["mode"]
    if mode == "LED Spektrum":
        draw_led_spectrum(W_CURRENT, H_CURRENT, stereo_bars, LED_THEME_NAMES[state["led_theme_idx"]], "rect")
    elif mode == "LED Nokta":
        draw_led_spectrum(W_CURRENT, H_CURRENT, stereo_bars, LED_THEME_NAMES[state["led_theme_idx"]], "dot")
    elif mode == "VU Metre":
        draw_vu_only(W_CURRENT, H_CURRENT, cava_bars)
    else:  # Spektrum
        screen.fill((15, 15, 15))
        draw_spectrum(W_CURRENT, H_CURRENT, stereo_bars, COLOR_THEME_NAMES[state["theme_idx"]], cava_bars)

    draw_hint()
    pygame.display.flip()
    clock.tick(FPS)

cava_proc.terminate()
try: os.remove(conf_path)
except: pass
pygame.display.quit()
pygame.quit()
sys.exit()
