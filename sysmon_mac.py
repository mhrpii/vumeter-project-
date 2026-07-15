"""sysmon_mac.py — macOS (Hackintosh) sistem monitoru.

Sensor kaynaklari:
  - smc_read (kendi C aracimiz): SMC anahtarlari -> CPU sicaklik/guc/voltaj + fanlar
    Tek cagrida tum anahtarlar okunur (hizli). HWMonitorSMC2 decode mantigi.
  - psutil: CPU%, RAM%, frekans, ag (sudo'suz)
  - GPU: RX 6900 XT SMC'de expose DEGIL (Hackintosh) -> None (ileride IOKit ile).

native_proto_mac.py arayuzu: SysMonitor().snapshot() dict + .stop()
"""
import os
import subprocess
import threading
import time

try:
    import psutil
except ImportError:
    psutil = None

# smc_read araci ayni klasorde (native_proto_mac.py ile birlikte)
_HERE = os.path.dirname(os.path.abspath(__file__))
_SMC_BIN = os.path.join(_HERE, "smc_read")
_SMC_SRC = os.path.join(_HERE, "smc_read.c")
_GPU_BIN = os.path.join(_HERE, "gpu_read")
_GPU_SRC = os.path.join(_HERE, "gpu_read.c")
_DISK_BIN = os.path.join(_HERE, "disk_read")
_DISK_SRC = os.path.join(_HERE, "disk_read.c")
_IPG_BIN = os.path.join(_HERE, "ipg_read")
_IPG_SRC = os.path.join(_HERE, "ipg_read.c")


def _ensure_built(binp, srcp, name):
    """Ikili yoksa ama kaynak varsa clang ile derle (elle derleme gerekmesin)."""
    if os.path.isfile(binp) and os.access(binp, os.X_OK):
        return True
    if not os.path.isfile(srcp):
        return False
    try:
        print(f"[sysmon_mac] {name} derleniyor (ilk calistirma)...")
        r = subprocess.run(
            ["clang", "-O2", "-o", binp, srcp,
             "-framework", "IOKit", "-framework", "CoreFoundation"],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and os.path.isfile(binp):
            os.chmod(binp, 0o755)
            print(f"[sysmon_mac] {name} derlendi.")
            return True
        else:
            print(f"[sysmon_mac] {name} derleme basarisiz: {r.stderr[:200]}")
    except FileNotFoundError:
        print("[sysmon_mac] clang yok (xcode-select --install gerekli)")
    except Exception as e:
        print(f"[sysmon_mac] {name} derleme hatasi: {e}")
    return False

# Okunacak SMC anahtarlari (bu donanimda gecerli olanlar)
_SMC_KEYS = [
    "TC0P",  # CPU proximity
    "TC0D",  # CPU die
    "TC0H",  # CPU heatsink
    "TC1C", "TC2C", "TC3C", "TC4C", "TC5C", "TC6C", "TC7C",  # cekirdekler
    "PCPT",  # CPU toplam guc (W)
    "PCPC",  # CPU core guc (W)
    "VC0C",  # CPU voltaj
    "F0Ac", "F1Ac", "F2Ac", "F3Ac", "F4Ac",  # 5 fan
]


def _read_smc():
    """smc_read ile tum anahtarlari tek cagrida oku -> {key: float}."""
    result = {}
    if not os.path.isfile(_SMC_BIN):
        return result
    try:
        out = subprocess.run([_SMC_BIN] + _SMC_KEYS,
                             capture_output=True, text=True, timeout=4)
        for line in out.stdout.splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                v = v.strip()
                if v != "NA":
                    try:
                        result[k.strip()] = float(v)
                    except ValueError:
                        pass
    except Exception:
        pass
    return result


def _read_gpu():
    """gpu_read ile GPU istatistiklerini oku -> {label: float}."""
    result = {}
    if not os.path.isfile(_GPU_BIN):
        return result
    try:
        out = subprocess.run([_GPU_BIN], capture_output=True, text=True, timeout=4)
        for line in out.stdout.splitlines():
            line = line.strip()
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    result[k.strip()] = float(v.strip())
                except ValueError:
                    pass
    except Exception:
        pass
    return result


def _ensure_ipg_built():
    """ipg_read'i IntelPowerGadget framework'u ile derle."""
    if os.path.isfile(_IPG_BIN) and os.access(_IPG_BIN, os.X_OK):
        return True
    if not os.path.isfile(_IPG_SRC):
        return False
    try:
        r = subprocess.run(
            ["clang", "-O2", "-o", _IPG_BIN, _IPG_SRC,
             "-F/Library/Frameworks", "-framework", "IntelPowerGadget"],
            capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and os.path.isfile(_IPG_BIN):
            os.chmod(_IPG_BIN, 0o755)
            return True
    except Exception:
        pass
    return False


def _read_ipg():
    """ipg_read ile Intel verisi: paket + 24 cekirdek -> dict."""
    result = {"cores": []}
    if not os.path.isfile(_IPG_BIN):
        return result
    try:
        out = subprocess.run([_IPG_BIN], capture_output=True, text=True, timeout=4)
        cores = {}
        for line in out.stdout.splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            # "c0_f=4784  c0_t=46" iki deger olabilir
            for part in line.split():
                if "=" not in part:
                    continue
                k, v = part.split("=", 1)
                try:
                    fv = float(v)
                except ValueError:
                    continue
                if k.startswith("c") and ("_f" in k or "_t" in k):
                    # cN_f / cN_t
                    num = int(k[1:k.index("_")])
                    cores.setdefault(num, {})
                    cores[num]["freq" if k.endswith("_f") else "temp"] = fv
                else:
                    result[k] = fv
        # cekirdekleri sirali listeye cevir
        n = int(result.get("num_cores", len(cores)))
        result["cores"] = [
            (i, cores.get(i, {}).get("freq", 0), cores.get(i, {}).get("temp", 0))
            for i in range(n)
        ]
    except Exception:
        pass
    return result


def _read_disks():
    """disk_read ile tum disklerin sicakligini oku -> [(model, temp, tip), ...]."""
    disks = []
    if not os.path.isfile(_DISK_BIN):
        return disks
    try:
        out = subprocess.run([_DISK_BIN], capture_output=True, text=True, timeout=6)
        for line in out.stdout.splitlines():
            line = line.strip()
            parts = line.split("|")
            if len(parts) == 3:
                model, temp, typ = parts
                try:
                    disks.append((model, int(temp), typ))
                except ValueError:
                    pass
    except Exception:
        pass
    return disks


class SysMonitor:
    def __init__(self, interval=1.5):
        _ensure_built(_SMC_BIN, _SMC_SRC, "smc_read")     # SMC okuyucu
        _ensure_built(_GPU_BIN, _GPU_SRC, "gpu_read")     # GPU okuyucu
        _ensure_built(_DISK_BIN, _DISK_SRC, "disk_read")  # disk sicaklik okuyucu
        _ensure_ipg_built()   # Intel Power Gadget okuyucu
        self._disks = []
        self._disk_last = 0.0
        self._ipg = {"cores": []}
        self._ipg_last = 0.0
        self._interval = interval
        self._data = {}
        self._lock = threading.Lock()
        self._running = True
        self._net_last = None
        self._net_last_t = None
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _read_net(self):
        if psutil is None:
            return None, None
        try:
            now = time.time()
            io = psutil.net_io_counters()
            if self._net_last is not None:
                dt = now - self._net_last_t
                if dt > 0:
                    down = (io.bytes_recv - self._net_last.bytes_recv) / dt / 1048576.0
                    up = (io.bytes_sent - self._net_last.bytes_sent) / dt / 1048576.0
                    self._net_last = io
                    self._net_last_t = now
                    return down, up
            self._net_last = io
            self._net_last_t = now
        except Exception:
            pass
        return None, None

    def _loop(self):
        while self._running:
            d = {}
            smc = _read_smc()

            # --- sicakliklar ---
            d["cpu_pkg"] = smc.get("TC0P")          # CPU proximity
            cores = [smc.get(f"TC{i}C") for i in range(1, 8)]
            cores = [c for c in cores if c and c > 0]
            d["cores_max"] = max(cores) if cores else None
            d["mb_system"] = smc.get("TC0D")        # CPU die (ikinci sicaklik)
            d["mb_pch"] = smc.get("TC0H")           # CPU heatsink (ucuncu)

            # --- guc ---
            d["cpu_power"] = smc.get("PCPT")        # CPU toplam guc (W)

            # --- fanlar (5 fan: F0-F4) ---
            fan_keys_out = ["fan_cpu", "fan_pump", "fan_sys1", "fan_sys2", "fan_sys3"]
            for i, fk in enumerate(fan_keys_out):
                v = smc.get(f"F{i}Ac")
                d[fk] = int(v) if v is not None else None

            # --- psutil: kullanim / RAM / frekans / ag ---
            if psutil is not None:
                try:
                    d["cpu_usage"] = psutil.cpu_percent(interval=None)
                    d["ram_pct"] = psutil.virtual_memory().percent
                    fq = psutil.cpu_freq()
                    d["cpu_freq"] = int(fq.current) if fq else None
                except Exception:
                    pass
                nd, nu = self._read_net()
                d["net_down"] = nd
                d["net_up"] = nu

            # --- GPU (RX 6900 XT): IOKit PerformanceStatistics ---
            g = _read_gpu()
            if g:
                d["gpu_junction"] = g.get("temp")        # GPU sicaklik
                d["gpu_edge"] = g.get("temp")            # (tek sicaklik var)
                d["gpu_power"] = g.get("power")          # GPU guc (W)
                # kullanim: once "util", yoksa "util2" (GPU Activity)
                gu = g.get("util")
                if not gu:
                    gu = g.get("util2")
                d["gpu_usage"] = gu
                # VRAM: vidused (kullanilan MB) + free -> toplam
                used = g.get("vidused") or g.get("vramused")
                free = g.get("vramfree")
                if used is not None:
                    d["gpu_vram_used"] = used / 1024.0   # MB -> GB
                if used is not None and free is not None:
                    d["gpu_vram_total"] = (used + free) / 1024.0  # GB
                # GPU fan (varsa)
                d["gpu_fan_rpm"] = int(g["fan"]) if g.get("fan") else None

            # diskler yavas isinir -> 10 sn'de bir oku (disk_read ~1sn surer)
            now = time.time()
            if now - self._disk_last > 10.0:
                self._disks = _read_disks()
                self._disk_last = now
            d["disks"] = self._disks

            # Intel Power Gadget: 24 cekirdek (2 sn'de bir)
            if now - self._ipg_last > 2.0:
                self._ipg = _read_ipg()
                self._ipg_last = now
            d["ipg"] = self._ipg
            # Intel'in daha dogru paket degerleriyle CPU'yu guncelle
            if self._ipg.get("pkg_power"):
                d["cpu_power"] = self._ipg["pkg_power"]
            if self._ipg.get("pkg_temp"):
                d["cpu_pkg"] = self._ipg["pkg_temp"]

            with self._lock:
                self._data = d
            time.sleep(self._interval)

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def stop(self):
        self._running = False
