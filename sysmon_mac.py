"""sysmon_mac.py — macOS sistem monitoru (iStats + psutil).

native_proto_mac.py bunu 'import sysmon' yerine kullanir. Ayni arayuz:
    mon = SysMonitor()
    d = mon.snapshot()   # dict
    mon.stop()

Mac Pro 7,1 (Intel Xeon W) icin. Sensor kaynaklari:
  - iStats (Ruby gem): CPU sicaklik + cekirdek sicakliklari + fanlar (SMC)
  - psutil: CPU%, RAM%, frekans, ag, disk (sudo'suz)
  - CPU guc: powermetrics sudo istedigi icin ATLANDI (arka planda sudo yok)

Mac'te olmayan degerler None doner -> gauge bos gorunur ama cokmez.
"""
import subprocess
import threading
import time
import shutil

try:
    import psutil
except ImportError:
    psutil = None


def _find_istats():
    for c in ("/usr/local/bin/istats", "/opt/homebrew/bin/istats",
              shutil.which("istats")):
        if c and shutil.os.path.isfile(c):
            return c
    # gem bin yolu (rbenv/system ruby)
    for c in ("/Library/Ruby/Gems/2.6.0/bin/istats",
              shutil.os.path.expanduser("~/.gem/ruby/*/bin/istats")):
        if shutil.os.path.isfile(c):
            return c
    return "istats"


_ISTATS = _find_istats()


def _istats_val(*args):
    """istats <args> --value-only calistir, ilk sayiyi dondur (yoksa None)."""
    try:
        out = subprocess.run([_ISTATS, *args, "--value-only"],
                             capture_output=True, text=True, timeout=4)
        for line in out.stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    return float(line)
                except ValueError:
                    continue
    except Exception:
        pass
    return None


def _istats_fans():
    """Tum fan hizlarini liste olarak dondur."""
    fans = []
    try:
        out = subprocess.run([_ISTATS, "fan", "speed", "--value-only"],
                             capture_output=True, text=True, timeout=4)
        for line in out.stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    fans.append(int(float(line)))
                except ValueError:
                    continue
    except Exception:
        pass
    return fans


def _istats_scan(key):
    """Tek SMC anahtari oku (istats scan KEY --value-only). enable gerekir."""
    try:
        out = subprocess.run([_ISTATS, "scan", key, "--value-only"],
                             capture_output=True, text=True, timeout=4)
        for line in out.stdout.splitlines():
            line = line.strip()
            if line:
                try:
                    return float(line)
                except ValueError:
                    continue
    except Exception:
        pass
    return None


class SysMonitor:
    """Arka planda periyodik olarak Mac sensorlerini okur (iStats yavas olabilir,
    o yuzden ayri thread + onbellek). snapshot() son degerleri dondurur."""

    def __init__(self, interval=2.0):
        self._interval = interval
        self._data = {}
        self._lock = threading.Lock()
        self._running = True
        self._net_last = None
        self._net_last_t = None
        # cekirdek sicaklik anahtarlarini bir kez etkinlestir (sessizce)
        try:
            subprocess.run([_ISTATS, "enable", "TC0P", "TC0D", "TC0H",
                            "TC1C", "TC2C", "TC3C", "TC4C", "TC5C", "TC6C", "TC7C"],
                           capture_output=True, timeout=6)
        except Exception:
            pass
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _read_net(self):
        """psutil ile ag hizi (MB/s down/up)."""
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
            # --- iStats: sicakliklar ---
            d["cpu_pkg"] = _istats_val("cpu", "temp")          # CPU proximity/temp
            # cekirdek sicakliklari (enable'li) -> en yuksegi cores_max
            cores = []
            for key in ("TC1C", "TC2C", "TC3C", "TC4C", "TC5C", "TC6C", "TC7C"):
                v = _istats_scan(key)
                if v is not None and v > 0:
                    cores.append(v)
            d["cores_max"] = max(cores) if cores else None
            # CPU Die (TC0D) -> ikinci bir sicaklik olarak "mb_system" yerine kullan
            d["mb_system"] = _istats_scan("TC0D")

            # --- iStats: fanlar ---
            fans = _istats_fans()
            # Mac Pro 7,1: 5 fan. Sirayla fan_cpu, fan_pump, sys1..3
            fan_keys = ["fan_cpu", "fan_pump", "fan_sys1", "fan_sys2", "fan_sys3"]
            for i, fk in enumerate(fan_keys):
                d[fk] = fans[i] if i < len(fans) else None

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

            # Mac'te okuyamadiklarimiz (None kalir -> gauge bos):
            # gpu sicaklik/kullanim/vram, VRM, PCH, CPU/GPU guc (powermetrics sudo ister)

            with self._lock:
                self._data = d
            time.sleep(self._interval)

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def stop(self):
        self._running = False
