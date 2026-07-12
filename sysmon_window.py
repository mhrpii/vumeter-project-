"""Bagimsiz Sistem Monitoru penceresi (VintageVU'dan ayri surec olarak acilir).
LCD surumundeki KART tasarimi + arka plan alan grafigi (son ~12sn); sysmon.py'den
gercek donanim verisi okur."""
import os
import sys
import pygame
from collections import deque as _deque

# Platforma gore sensor kaynagi:
#   Windows -> sysmon_win (LibreHardwareMonitor DLL, yonetici gerekir)
#   Linux   -> sysmon     (hwmon)
try:
    if sys.platform == "win32":
        import sysmon_win as sysmon
    else:
        import sysmon
except Exception as _e:
    print("sysmon yuklenemedi:", _e)
    sysmon = None

WIDTH, HEIGHT = 1600, 900

_font_cache = {}


def _font(size, bold=True):
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = pygame.font.SysFont("DejaVu Sans", size, bold=bold)
    return _font_cache[key]


def temp_color(t):
    if t is None:
        return (60, 66, 60)
    if t < 55:
        return (60, 230, 90)
    if t < 70:
        return (245, 210, 60)
    return (235, 60, 40)


# --- Grafik gecmisi (son ~12sn) ---
_sm_history = {}
_SM_HIST_LEN = 72


def draw_card_graph(surf, cx0, cy0, cw, ch, hist, base_color):
    """Kartin TAMAMINI kaplayan alan grafigi. Tek renk (durum rengi),
    belirgin dolgu + net cizgi. Rakam ustte net kalir."""
    if len(hist) < 2:
        return
    n = len(hist)
    prev_clip = surf.get_clip()
    surf.set_clip(pygame.Rect(cx0, cy0, cw, ch))
    base_y = cy0 + ch
    step = cw / (_SM_HIST_LEN - 1)
    pts = []
    for i, v in enumerate(hist):
        x = cx0 + int((i + (_SM_HIST_LEN - n)) * step)
        y = base_y - int(ch * max(0.0, min(1.0, v)) * 0.92)
        pts.append((x, y))
    r0, g0, b0 = base_color
    fill = (r0//4 + 12, g0//4 + 14, b0//4 + 16)
    poly = pts + [(pts[-1][0], base_y), (pts[0][0], base_y)]
    pygame.draw.polygon(surf, fill, poly)
    bright = (r0//2 + 20, g0//2 + 22, b0//2 + 24)
    band_bottom = [(x, min(base_y, y + int(ch*0.18))) for (x, y) in reversed(pts)]
    pygame.draw.polygon(surf, bright, pts + band_bottom)
    pygame.draw.lines(surf, base_color, False, pts, 2)
    surf.set_clip(prev_clip)


def draw(screen, d):
    W, H = screen.get_size()
    screen.fill((10, 12, 14))
    GREEN = (60, 230, 90)

    cpu_t = d.get("cpu_pkg"); cores = d.get("cores_max")
    gpu_e = d.get("gpu_edge"); gpu_j = d.get("gpu_junction"); gpu_m = d.get("gpu_mem")
    vrm = d.get("mb_vrm"); pch = d.get("mb_pch"); mbsys = d.get("mb_system")
    use = d.get("cpu_usage"); gpu_u = d.get("gpu_usage")
    ram = d.get("ram_pct")
    vram_u = d.get("gpu_vram_used"); vram_t = d.get("gpu_vram_total")
    frq = d.get("cpu_freq")
    cpu_p = d.get("cpu_power"); gpu_p = d.get("gpu_power")
    cfan = d.get("fan_cpu"); pump = d.get("fan_pump")
    gfan = d.get("gpu_fan_rpm")
    sfans = [d.get(f"fan_sys{i}") for i in range(1, 7)]
    nd = d.get("net_down"); nu = d.get("net_up")

    def net_fmt(mb_s):
        if mb_s is None:
            return ("--", "Mbps", 0)
        mbit = mb_s * 8.388608
        return (f"{mbit:.2f}", "Mbps", mbit / 1000.0)
    nd_txt, nd_unit, nd_frac = net_fmt(nd)
    nu_txt, nu_unit, nu_frac = net_fmt(nu)

    vram_frac = (vram_u / vram_t) if (vram_u is not None and vram_t) else 0
    vram_txt = f"{vram_u:.1f}" if vram_u is not None else "--"

    def col(t): return temp_color(t)
    bars_top = [
        ("CPU",  f"{cpu_t:.0f}"  if cpu_t is not None else "--", "C", (cpu_t/100.0)  if cpu_t else 0, col(cpu_t)),
        ("Çkrdk",f"{cores:.0f}"  if cores is not None else "--", "C", (cores/100.0)  if cores else 0, col(cores)),
        ("GPU",  f"{gpu_e:.0f}"  if gpu_e is not None else "--", "C", (gpu_e/100.0)  if gpu_e else 0, col(gpu_e)),
        ("Jnc",  f"{gpu_j:.0f}"  if gpu_j is not None else "--", "C", (gpu_j/110.0)  if gpu_j else 0, col(gpu_j)),
        ("VMem", f"{gpu_m:.0f}"  if gpu_m is not None else "--", "C", (gpu_m/100.0)  if gpu_m else 0, col(gpu_m)),
        ("VRM",  f"{vrm:.0f}"    if vrm is not None else "--",   "C", (vrm/100.0)    if vrm else 0,   col(vrm)),
        ("PCH",  f"{pch:.0f}"    if pch is not None else "--",   "C", (pch/90.0)     if pch else 0,   col(pch)),
        ("Sys",  f"{mbsys:.0f}"  if mbsys is not None else "--", "C", (mbsys/80.0)   if mbsys else 0, col(mbsys)),
        ("CPU%", f"{use:.0f}"    if use is not None else "--",   "%", (use/100.0)    if use is not None else 0, GREEN),
        ("GPU%", f"{gpu_u:.0f}"  if gpu_u is not None else "--", "%", (gpu_u/100.0)  if gpu_u is not None else 0, GREEN),
        ("RAM",  f"{ram:.0f}"    if ram is not None else "--",   "%", (ram/100.0)    if ram is not None else 0, GREEN),
        ("VRAM", vram_txt,                                       "GB", vram_frac, GREEN),
    ]
    bars_bot = [
        ("GHz",  f"{frq/1000:.1f}" if frq else "--",             "",  (frq/5700.0)   if frq else 0, GREEN),
        ("C-W",  f"{cpu_p:.0f}"  if cpu_p is not None else "--", "W", (cpu_p/250.0)  if cpu_p else 0, GREEN),
        ("G-W",  f"{gpu_p:.0f}"  if gpu_p is not None else "--", "W", (gpu_p/350.0)  if gpu_p else 0, GREEN),
        ("CFan", f"{cfan:.0f}"   if cfan else "0",               "rpm", (cfan/3000.0) if cfan else 0, GREEN),
        ("Pump", f"{pump:.0f}"   if pump else "0",               "rpm", (pump/3000.0) if pump else 0, GREEN),
        ("GFan", f"{gfan:.0f}"   if gfan else "0",               "rpm", (gfan/3000.0) if gfan else 0, GREEN),
    ] + [
        (f"S{i+1}", f"{sf:.0f}" if sf else "0", "rpm", (sf/3000.0) if sf else 0, GREEN)
        for i, sf in enumerate(sfans)
    ] + [
        ("İndir", nd_txt, nd_unit, nd_frac, GREEN),
        ("Yükle", nu_txt, nu_unit, nu_frac, GREEN),
    ]

    margin = 20
    gap = 10
    half = H // 2

    def draw_row(bars, row_top, row_bottom):
        n = len(bars)
        card_w = (W - 2*margin - (n-1)*gap) // n
        card_top = row_top + 8
        card_h = (row_bottom - row_top) - 16
        _max_n = 14
        _ref_w = (W - 2*margin - (_max_n-1)*gap) // _max_n
        cardf = _font(int(_ref_w * 0.34))
        unitf = _font(max(14, int(_ref_w * 0.17)), bold=False)
        lblf = _font(max(15, int(_ref_w * 0.18)))
        for idx, (lbl, vtxt, unit, frac, color) in enumerate(bars):
            frac = max(0.0, min(1.0, frac))
            cx0 = margin + idx * (card_w + gap)
            ccx = cx0 + card_w // 2
            pygame.draw.rect(screen, (22, 27, 34), (cx0, card_top, card_w, card_h), border_radius=14)
            hkey = (lbl, unit)
            if hkey not in _sm_history:
                _sm_history[hkey] = _deque(maxlen=_SM_HIST_LEN)
            _sm_history[hkey].append(frac)
            draw_card_graph(screen, cx0, card_top, card_w, card_h, _sm_history[hkey], color)
            pygame.draw.rect(screen, (38, 46, 58), (cx0, card_top, card_w, card_h), 1, border_radius=14)
            vs = cardf.render(vtxt, True, color)
            screen.blit(vs, (ccx - vs.get_width()//2, card_top + int(card_h*0.14)))
            if unit:
                us = unitf.render(unit, True, (170, 182, 196))
                screen.blit(us, (ccx - us.get_width()//2, card_top + int(card_h*0.46)))
            ls = lblf.render(lbl, True, (210, 220, 232))
            screen.blit(ls, (ccx - ls.get_width()//2, card_top + int(card_h*0.62)))

    draw_row(bars_top, 0, half)
    draw_row(bars_bot, half, H)


def main():
    # mixer'siz init (ses aygiti acmasin -> LG OSD tetiklemesin)
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    pygame.display.init()
    pygame.font.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Sistem Monitoru")
    clock = pygame.time.Clock()

    mon = sysmon.SysMonitor() if sysmon is not None else None
    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False

        if mon is not None:
            draw(screen, mon.snapshot())
        else:
            screen.fill((10, 12, 14))
            f = _font(36)
            screen.blit(f.render("sysmon yuklenemedi", True, (200, 80, 80)), (60, HEIGHT//2))
        pygame.display.flip()
        clock.tick(30)

    if mon is not None:
        mon.stop()
    pygame.quit()
    sys.exit()


if __name__ == "__main__":
    main()
