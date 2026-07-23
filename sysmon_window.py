"""Bagimsiz Sistem Monitoru penceresi (VintageVU'dan ayri surec olarak acilir).
LCD surumundeki KART tasarimi + arka plan alan grafigi (son ~12sn); sysmon.py'den
gercek donanim verisi okur."""
import os
import sys
import pygame
import math
from collections import deque as _deque

# Platforma gore sensor kaynagi:
#   Windows -> sysmon_win (LibreHardwareMonitor DLL, yonetici gerekir)
#   Linux   -> sysmon     (hwmon)
try:
    if sys.platform == "win32":
        import sysmon_win as sysmon
    elif sys.platform == "darwin":
        import sysmon_mac as sysmon
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


# Kart aciklamalari (etiket -> kucuk aciklama satiri)
_CARD_DESC = {
    "CPU": "İşlemci sıcaklığı", "Çkrdk": "En sıcak çekirdek",
    "GPU": "GPU kenar ısısı", "Jnc": "GPU en sıcak nokta",
    "VMem": "GPU bellek ısısı", "GBellek": "GPU bellek saati",
    "VRM": "Güç katı ısısı", "VCore": "CPU voltajı",
    "PCH": "Yonga seti ısısı", "Sys": "Anakart ısısı",
    "CPU%": "İşlemci kullanımı", "GPU%": "GPU kullanımı",
    "RAM": "Bellek kullanımı", "VRAM": "GPU bellek dolu",
    "GHz": "CPU hızı", "C-W": "CPU gücü", "G-W": "GPU gücü",
    "CFan": "CPU fan devri", "Pump": "Pompa devri", "GFan": "GPU fan devri",
    "İndir": "Ağ indirme", "Yükle": "Ağ yükleme",
}


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
    """SAYFA 2: 9 diskin sicakliklari (ust: NVMe, alt: SATA)."""
    surf.fill((8, 10, 8))
    WIDTH, HEIGHT = surf.get_size()
    SCALE = max(0.35, WIDTH / 1920.0)   # LCD (1920) referansli olcek
    usage = usage or {}

    def temp_color(t):
        if t is None: return (60, 66, 60)
        if t < 50: return (60, 230, 90)
        if t < 65: return (245, 210, 60)
        return (235, 60, 40)

    # baslik SAG UST (hava sol ustte, cakismaz)
    tf = _font(max(9, int(22 * SCALE)))
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
            gr = int(min(card_w, row_h) * 0.38)   # bar ile teget olmasin
            gcol = _sm_grad_rgb(frac)
            draw_card_gauge_m(surf, gcx, gcy, gr, frac, gcol)
            # sicaklik rakami
            vf = _font(int(gr * 0.9))
            vs = vf.render(f"{temp}", True, gcol)
            vx = gcx - vs.get_width()//2
            vy = gcy - vs.get_height()//2
            surf.blit(vs, (vx, vy))
            # C birimi: rakamin SAG USTUNE (derece isareti gibi - LCD ile ayni)
            uf = _font(max(10, int(gr * 0.32)))
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
            nf = _font(nsize)
            while nf.size(name)[0] > card_w - 8 and nsize > 12:
                nsize -= 1
                nf = _font(nsize)
            ns = nf.render(name, True, (225, 232, 242))
            surf.blit(ns, (ccx - ns.get_width()//2, row_top + int(row_h*0.80)))
            # DIKEY DOLULUK BARI (sol ic kenar) - fiziksel disk tamami

            bw = max(5, int(card_w * 0.055))
            bx = cx0 + 6
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
            pf2 = _font(nsize)
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
    WIDTH, HEIGHT = surf.get_size()
    SCALE = max(0.35, WIDTH / 1920.0)   # LCD (1920) referansli olcek
    cores = ipg.get("cores") or []

    # baslik SAG UST
    tf = _font(max(9, int(22 * SCALE)))
    ts = tf.render("ÇEKİRDEK ISI HARİTASI", True, (150, 165, 180))
    surf.blit(ts, (WIDTH - ts.get_width() - 18, 8))

    # paket ozeti SOL UST (hava'nin altina)
    pf = _font(max(9, int(20 * SCALE)))
    pw = ipg.get("pkg_power"); pt = ipg.get("pkg_temp"); tdp = ipg.get("tdp")
    summary = []
    if pt: summary.append(f"CPU {pt:.0f}°C")
    if pw: summary.append(f"{pw:.0f}W")
    if tdp: summary.append(f"TDP {tdp:.0f}W")
    if summary:
        ps = pf.render("   ".join(summary), True, (200, 210, 220))
        surf.blit(ps, (18, 40))

    if not cores:
        f = _font(max(9, int(30 * SCALE)))
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
        nf = _font(max(8, int(min(cell_w, cell_h) * 0.16)))
        ns = nf.render(f"C{cnum}", True, (255, 255, 255) if temp > 55 else (200, 210, 220))
        surf.blit(ns, (x + 4, y + 3))
        if temp > 0:
            # sicaklik (buyuk, ortada)
            tf2 = _font(max(10, int(min(cell_w, cell_h) * 0.34)))
            tcol = (255, 255, 255) if temp > 55 else (20, 24, 20)
            tsr = tf2.render(f"{temp:.0f}°", True, tcol)
            surf.blit(tsr, (x + cell_w//2 - tsr.get_width()//2, y + int(cell_h*0.28)))
            # frekans (alt, kucuk)
            ff = _font(max(8, int(min(cell_w, cell_h) * 0.16)))
            fs = ff.render(f"{freq/1000:.1f}G", True, tcol)
            surf.blit(fs, (x + cell_w//2 - fs.get_width()//2, y + int(cell_h*0.68)))
        else:
            # uyuyan cekirdek
            sf = _font(max(8, int(min(cell_w, cell_h) * 0.17)))
            ss = sf.render("uyku", True, (90, 100, 110))
            surf.blit(ss, (x + cell_w//2 - ss.get_width()//2, y + cell_h//2 - 8))



_page = [0]   # 0=kartlar, 1=disk, 2=cekirdek (1/2/3 tuslari)


def draw(screen, d):
    if _page[0] == 1:
        draw_sysmon_disks(screen, d.get("disks") or [], d.get("disk_usage"))
        return
    if _page[0] == 2:
        draw_sysmon_cores(screen, d.get("ipg") or {})
        return
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
        (("GBellek" if sys.platform == "darwin" else "VMem"),
         (f"{d.get('gpu_mem_clock'):.0f}" if (sys.platform == "darwin" and d.get('gpu_mem_clock')) else (f"{gpu_m:.0f}" if gpu_m is not None else "--")),
         ("MHz" if sys.platform == "darwin" else "C"),
         ((d.get('gpu_mem_clock', 0) or 0)/2000.0 if sys.platform == "darwin" else ((gpu_m/100.0) if gpu_m else 0)),
         (GREEN if sys.platform == "darwin" else col(gpu_m))),
        (("VCore" if sys.platform == "darwin" else "VRM"),
         (f"{d.get('cpu_voltage'):.2f}" if (sys.platform == "darwin" and d.get('cpu_voltage')) else (f"{vrm:.0f}" if vrm is not None else "--")),
         ("V" if sys.platform == "darwin" else "C"),
         ((d.get('cpu_voltage', 0) or 0)/1.5 if sys.platform == "darwin" else ((vrm/100.0) if vrm else 0)),
         (GREEN if sys.platform == "darwin" else col(vrm))),
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
            desc = _CARD_DESC.get(lbl) or ("Kasa fanı" if lbl.startswith("S") and lbl[1:].isdigit() else None)
            if desc:
                _dc = (60, 200, 210)   # camgobegi
                descf = _font(max(13, int(_ref_w * 0.16)), bold=False)
                words = desc.split()
                # tek satirda sigmiyorsa iki satira bol (kelimeden)
                one = descf.render(desc, True, _dc)
                if one.get_width() <= card_w - 8 or len(words) < 2:
                    screen.blit(one, (ccx - one.get_width()//2, card_top + int(card_h*0.80)))
                else:
                    mid = len(words) // 2 + (len(words) % 2)
                    l1 = descf.render(" ".join(words[:mid]), True, _dc)
                    l2 = descf.render(" ".join(words[mid:]), True, _dc)
                    screen.blit(l1, (ccx - l1.get_width()//2, card_top + int(card_h*0.78)))
                    screen.blit(l2, (ccx - l2.get_width()//2, card_top + int(card_h*0.88)))

    draw_row(bars_top, 0, half)
    draw_row(bars_bot, half, H)


def main(start_page=0):
    _page[0] = start_page
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
                if event.key == pygame.K_1:
                    _page[0] = 0
                elif event.key == pygame.K_2:
                    _page[0] = 1
                elif event.key == pygame.K_3:
                    _page[0] = 2
                elif event.key in (pygame.K_q, pygame.K_ESCAPE):
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
