"""Bagimsiz Sistem Monitoru penceresi (VintageVU'dan ayri surec olarak acilir).
LCD'deki dikey-bar tasarimin ayni; sysmon.py'den gercek donanim verisi okur."""
import sys
import pygame

try:
    import sysmon
except Exception:
    sysmon = None

WIDTH, HEIGHT = 1600, 900


def _font(size, bold=True):
    return pygame.font.SysFont("Menlo", size, bold=bold)

_font_cache = {}

def _font_cached(size, bold=True):
    key = (size, bold)
    if key not in _font_cache:
        _font_cache[key] = _font(size, bold)
    return _font_cache[key]


def temp_color(t):
    if t is None:
        return (60, 66, 60)
    if t < 65:
        return (60, 230, 90)
    if t < 80:
        return (245, 210, 60)
    return (235, 60, 40)


def draw(screen, d, fonts):
    W, H = screen.get_size()
    screen.fill((8, 10, 8))
    GREEN = (60, 230, 90)
    DARK = (20, 24, 20)
    GREY = (60, 66, 60)
    WHITE = (80, 220, 215)
    DIM = (90, 175, 175)

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
        # sysmon MB/s verir -> Mbps'e cevir (x8.389). Bar olcegi: 1000 Mbps tam.
        if mb_s is None:
            return ("--", "Mbps", 0)
        mbit = mb_s * 8.388608
        return (f"{mbit:.2f}", "Mbps", mbit / 1000.0)
    nd_txt, nd_unit, nd_frac = net_fmt(nd)
    nu_txt, nu_unit, nu_frac = net_fmt(nu)

    vram_frac = (vram_u / vram_t) if (vram_u is not None and vram_t) else 0
    vram_txt = f"{vram_u:.1f}" if vram_u is not None else "--"

    def col(t): return temp_color(t)
    # 21 bar: sicakliklar | kullanim | frekans | guc | fanlar | ag
    bars = [
        ("CPU",  f"{cpu_t:.0f}"  if cpu_t is not None else "--", "C", (cpu_t/100.0)  if cpu_t else 0, col(cpu_t)),
        ("Cek",  f"{cores:.0f}"  if cores is not None else "--", "C", (cores/100.0)  if cores else 0, col(cores)),
        ("GPU",  f"{gpu_e:.0f}"  if gpu_e is not None else "--", "C", (gpu_e/100.0)  if gpu_e else 0, col(gpu_e)),
        ("Jnc",  f"{gpu_j:.0f}"  if gpu_j is not None else "--", "C", (gpu_j/110.0)  if gpu_j else 0, col(gpu_j)),
        ("VMem", f"{gpu_m:.0f}"  if gpu_m is not None else "--", "C", (gpu_m/100.0)  if gpu_m else 0, col(gpu_m)),
        ("VRM",  f"{vrm:.0f}"    if vrm is not None else "--",   "C", (vrm/100.0)    if vrm else 0,   col(vrm)),
        ("PCH",  f"{pch:.0f}"    if pch is not None else "--",   "C", (pch/90.0)     if pch else 0,   col(pch)),
        ("Sys",  f"{mbsys:.0f}"  if mbsys is not None else "--", "C", (mbsys/80.0)   if mbsys else 0, col(mbsys)),
        ("CPU",  f"{use:.0f}"    if use is not None else "--",   "%", (use/100.0)    if use is not None else 0, GREEN),
        ("GPU",  f"{gpu_u:.0f}"  if gpu_u is not None else "--", "%", (gpu_u/100.0)  if gpu_u is not None else 0, GREEN),
        ("RAM",  f"{ram:.0f}"    if ram is not None else "--",   "%", (ram/100.0)    if ram is not None else 0, GREEN),
        ("VRAM", vram_txt,                                       "GB", vram_frac, GREEN),
        ("GHz",  f"{frq/1000:.1f}" if frq else "--",             "",  (frq/5700.0)   if frq else 0, GREEN),
        ("CGuc", f"{cpu_p:.0f}"  if cpu_p is not None else "--", "W", (cpu_p/250.0)  if cpu_p else 0, GREEN),
        ("GGuc", f"{gpu_p:.0f}"  if gpu_p is not None else "--", "W", (gpu_p/350.0)  if gpu_p else 0, GREEN),
        ("CFan", f"{cfan:.0f}"   if cfan else "0",               "rpm", (cfan/3000.0) if cfan else 0, GREEN),
        ("Pump", f"{pump:.0f}"   if pump else "0",               "rpm", (pump/3000.0) if pump else 0, GREEN),
    ] + [
        (f"S{i+1}", f"{sf:.0f}" if sf else "0", "rpm", (sf/3000.0) if sf else 0, GREEN)
        for i, sf in enumerate(sfans)
    ] + [
        ("GFan", f"{gfan:.0f}"   if gfan else "0",               "rpm", (gfan/3000.0) if gfan else 0, GREEN),
        ("Indir", nd_txt, nd_unit, nd_frac, GREEN),
        ("Yukle", nu_txt, nu_unit, nu_frac, GREEN),
    ]

    # Iki satirli grid: ust = sicaklik(8)+kullanim(4), alt = frekans/guc(3)+fan(4)+ag(2)
    rows = [
        (bars[:12], (8,)),        # ust satir, ayirici: sicaklik|kullanim
        (bars[12:], (3, 12)),     # alt satir, ayiricilar: guc(3)|fan(9)|ag(2)
    ]
    margin = 24
    row_h = H // 2
    for rowi, (rbars, seps) in enumerate(rows):
        n = len(rbars)
        slot_w = (W - 2*margin) // n
        vfont = _font_cached(max(16, min(34, int(slot_w * 0.32))))
        ufont = _font_cached(max(11, min(17, int(slot_w * 0.16))))
        lfont = _font_cached(max(12, min(20, int(slot_w * 0.20))))
        bar_w = max(8, int(slot_w * 0.20))
        ry0 = rowi * row_h
        bar_top = ry0 + 62
        bar_bottom = ry0 + row_h - 40
        bar_h_full = bar_bottom - bar_top

        for gidx in seps:
            gx = margin + gidx * slot_w
            pygame.draw.line(screen, (30, 36, 30), (gx, bar_top - 26), (gx, bar_bottom + 26), 1)
        if rowi == 1:
            pygame.draw.line(screen, (30, 36, 30), (int(W*0.02), ry0), (int(W*0.98), ry0), 1)

        for idx, (lbl, vtxt, unit, frac, color) in enumerate(rbars):
            frac = max(0.0, min(1.0, frac))
            slot_x = margin + idx * slot_w
            cx = slot_x + slot_w // 2
            x = cx - bar_w // 2
            pygame.draw.rect(screen, DARK, (x, bar_top, bar_w, bar_h_full))
            fh = int(bar_h_full * frac)
            pygame.draw.rect(screen, color, (x, bar_bottom - fh, bar_w, fh))
            pygame.draw.rect(screen, GREY, (x, bar_top, bar_w, bar_h_full), 1)
            vs = vfont.render(vtxt, True, color)
            if unit:
                us = ufont.render(unit, True, DIM)
                tot_w = vs.get_width() + 5 + us.get_width()
                x0 = cx - tot_w // 2
                screen.blit(vs, (x0, ry0 + 16))
                # birim, sayinin sagina taban hizali
                screen.blit(us, (x0 + vs.get_width() + 5,
                                 ry0 + 16 + vs.get_height() - us.get_height() - 3))
            else:
                screen.blit(vs, (cx - vs.get_width()//2, ry0 + 16))
            ls = lfont.render(lbl, True, WHITE)
            screen.blit(ls, (cx - ls.get_width()//2, bar_bottom + 8))


def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT), pygame.RESIZABLE)
    pygame.display.set_caption("Sistem Monitoru")
    clock = pygame.time.Clock()
    fonts = (_font(30), _font(15), _font(18))

    # App Nap engelle
    try:
        from Foundation import NSProcessInfo, NSActivityUserInitiated
        _opts = NSActivityUserInitiated | 0x00FFFFFF
        NSProcessInfo.processInfo().beginActivityWithOptions_reason_(_opts, "Sistem Monitoru")
    except Exception:
        pass

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
            draw(screen, mon.snapshot(), fonts)
        else:
            screen.fill((8, 10, 8))
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
