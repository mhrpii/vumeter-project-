"""Linux sistem monitoru veri toplayici -- native /sys/class/hwmon + RAPL + psutil.
Hicbir harici arac gerekmez (PowerLog/ioreg/SMC yok). Tum sensorler isimle bulunur,
boylece hwmon numaralari degisse bile calisir.

Kaynaklar:
  - coretemp  : CPU paket + cekirdek sicakliklari
  - amdgpu    : GPU edge/junction/mem sicaklik, guc, kullanim, VRAM, fan
  - nct6687   : anakart (System/VRM/PCH), CPU/Pump/System fanlari
  - RAPL      : CPU paket gucu (Watt)
  - psutil    : CPU kullanim/frekans, RAM, ag hizi
"""
import glob
import os
import threading
import time

try:
    import psutil
except Exception:
    psutil = None

_HWMON = "/sys/class/hwmon"
_RAPL = "/sys/class/powercap/intel-rapl:0/energy_uj"


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except Exception:
        return None


def _read_int(path):
    v = _read(path)
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _find_hwmon(name):
    """Isimle hwmon klasoru bul (numara degisse bile calisir)."""
    for hw in glob.glob(f"{_HWMON}/hwmon*"):
        if _read(f"{hw}/name") == name:
            return hw
    return None


def _labels(hw):
    """hwmon icindeki tempN_label/fanN_label -> {etiket: input_yolu} dict."""
    out = {}
    if not hw:
        return out
    for lbl in glob.glob(f"{hw}/temp*_label") + glob.glob(f"{hw}/fan*_label"):
        label = _read(lbl)
        if label:
            out[label] = lbl.replace("_label", "_input")
    return out


class SysMonitor:
    def __init__(self):
        self.data = {
            "cpu_pkg": None, "cores_max": None, "cores_avg": None,
            "cpu_usage": None, "cpu_freq": None, "cpu_power": None,
            "ram_pct": None, "ram_used": None, "ram_total": None,
            "gpu_edge": None, "gpu_junction": None, "gpu_mem": None,
            "gpu_power": None, "gpu_usage": None,
            "gpu_vram_used": None, "gpu_vram_total": None, "gpu_fan_rpm": None,
            "mb_system": None, "mb_vrm": None, "mb_pch": None,
            "fan_cpu": None, "fan_pump": None,
            "fan_sys1": None, "fan_sys2": None, "fan_sys3": None,
            "fan_sys4": None, "fan_sys5": None, "fan_sys6": None,
            "net_down": None, "net_up": None,
        }
        self._lock = threading.Lock()
        self._running = True
        self._net_prev = None
        self._rapl_prev = None  # (energy_uj, time)

        # hwmon yollarini bir kez bul
        self._hw_core = _find_hwmon("coretemp")
        self._hw_gpu = _find_hwmon("amdgpu")
        self._hw_nct = _find_hwmon("nct6687")
        self._core_labels = _labels(self._hw_core)
        self._nct_labels = _labels(self._hw_nct)

        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    # ---------- CPU sicaklik (coretemp) ----------
    def _read_cpu_temp(self):
        pkg = self._core_labels.get("Package id 0")
        if pkg:
            v = _read_int(pkg)
            if v:
                self.data["cpu_pkg"] = v / 1000.0
        core_vals = []
        for label, path in self._core_labels.items():
            if label.startswith("Core "):
                v = _read_int(path)
                if v:
                    core_vals.append(v / 1000.0)
        if core_vals:
            self.data["cores_max"] = max(core_vals)
            self.data["cores_avg"] = sum(core_vals) / len(core_vals)

    # ---------- CPU guc (RAPL) ----------
    def _read_cpu_power(self):
        e = _read_int(_RAPL)
        now = time.time()
        if e is None:
            return
        if self._rapl_prev is not None:
            de = e - self._rapl_prev[0]
            dt = now - self._rapl_prev[1]
            if dt > 0:
                if de < 0:  # sayac tasmasi, atla
                    de = 0
                self.data["cpu_power"] = (de / 1e6) / dt  # microjoule -> watt
        self._rapl_prev = (e, now)

    # ---------- GPU (amdgpu) ----------
    def _read_gpu(self):
        hw = self._hw_gpu
        if not hw:
            return
        gl = _labels(hw)
        for key, label in (("gpu_edge", "edge"), ("gpu_junction", "junction"), ("gpu_mem", "mem")):
            p = gl.get(label)
            if p:
                v = _read_int(p)
                if v:
                    self.data[key] = v / 1000.0
        # guc (power1_average, microwatt)
        pw = _read_int(f"{hw}/power1_average") or _read_int(f"{hw}/power1_input")
        if pw:
            self.data["gpu_power"] = pw / 1e6
        # fan
        f = _read_int(f"{hw}/fan1_input")
        if f is not None:
            self.data["gpu_fan_rpm"] = f
        # kullanim + VRAM (device altinda)
        dev = os.path.realpath(f"{hw}/device")
        busy = _read_int(f"{dev}/gpu_busy_percent")
        if busy is not None:
            self.data["gpu_usage"] = busy
        vu = _read_int(f"{dev}/mem_info_vram_used")
        vt = _read_int(f"{dev}/mem_info_vram_total")
        if vu is not None:
            self.data["gpu_vram_used"] = vu / (1024**3)
        if vt is not None:
            self.data["gpu_vram_total"] = vt / (1024**3)

    # ---------- Anakart + fanlar (nct6687) ----------
    def _read_motherboard(self):
        nl = self._nct_labels
        for key, label in (("mb_system", "System"), ("mb_vrm", "VRM MOS"), ("mb_pch", "PCH")):
            p = nl.get(label)
            if p:
                v = _read_int(p)
                if v:
                    self.data[key] = v / 1000.0
        for key, label in (("fan_cpu", "CPU Fan"), ("fan_pump", "Pump Fan"),
                           ("fan_sys1", "System Fan #1"), ("fan_sys2", "System Fan #2"),
                           ("fan_sys3", "System Fan #3"), ("fan_sys4", "System Fan #4"),
                           ("fan_sys5", "System Fan #5"), ("fan_sys6", "System Fan #6")):
            p = nl.get(label)
            if p:
                v = _read_int(p)
                if v is not None:
                    self.data[key] = v

    # ---------- psutil (kullanim, RAM, ag) ----------
    def _read_psutil(self):
        if psutil is None:
            return
        try:
            self.data["cpu_usage"] = psutil.cpu_percent(interval=None)
            freq = psutil.cpu_freq()
            if freq:
                self.data["cpu_freq"] = freq.current
            vm = psutil.virtual_memory()
            self.data["ram_pct"] = vm.percent
            self.data["ram_used"] = vm.used / (1024**3)
            self.data["ram_total"] = vm.total / (1024**3)
            now = time.time()
            # Sadece fiziksel arayuzler: lo/docker/veth/br/virbr/vnet disla
            pernic = psutil.net_io_counters(pernic=True)
            _skip = ("lo", "docker", "veth", "br-", "virbr", "vnet")
            rx = sum(v.bytes_recv for k, v in pernic.items()
                     if not k.startswith(_skip))
            tx = sum(v.bytes_sent for k, v in pernic.items()
                     if not k.startswith(_skip))
            class _IO: pass
            io = _IO(); io.bytes_recv = rx; io.bytes_sent = tx
            if self._net_prev is not None:
                dt = now - self._net_prev[2]
                if dt > 0:
                    down = (io.bytes_recv - self._net_prev[0]) / dt
                    up = (io.bytes_sent - self._net_prev[1]) / dt
                    self.data["net_down"] = max(0.0, down / (1024**2))
                    self.data["net_up"] = max(0.0, up / (1024**2))
            self._net_prev = (io.bytes_recv, io.bytes_sent, now)
        except Exception:
            pass

    def _loop(self):
        if psutil is not None:
            try:
                psutil.cpu_percent(interval=None)
            except Exception:
                pass
        while self._running:
            with self._lock:
                self._read_cpu_temp()
                self._read_cpu_power()
                self._read_gpu()
                self._read_motherboard()
                self._read_psutil()
            time.sleep(1.0)

    def snapshot(self):
        with self._lock:
            return dict(self.data)

    def stop(self):
        self._running = False


if __name__ == "__main__":
    import json
    m = SysMonitor()
    time.sleep(2.5)
    print(json.dumps(m.snapshot(), indent=2, default=str))
    m.stop()
