"""trcc_direct.py — Thermalright Trofeo Vision 9.16 LCD'ye DOGRUDAN USB ile
kare gonderir. trcc / HTTP / PNG / disk YOK.

Protokol (trcc kaynagindan cikarildi - ly_lcd.py + protocol.py + display.py):
  Cihaz     : VID 0x0416, PID 0x5408 (LY)
  Endpoint  : yazma 0x01, okuma 0x81   (interface 0, config 1)

  HANDSHAKE : 2048 bayt yaz -> 512 bayt oku
              dogrula: resp[0]==3, resp[1]==0xFF, resp[8]==1
              PM  = 64 + resp[20]  (raw<=3 ise raw=1)
              SUB = resp[22] + 1

  PROFIL    : FBL 192 -> 1920x462, jpeg=True, widescreen=True,
              encode_base=180, sub istisnalari: 2/3/4 -> 0
              (bizim panel SUB=5 -> istisna yok -> 180 derece)

  KARE      : goruntu -> 180 derece dondur -> JPEG (kalite 95)
              -> 512'lik parcalar (16 bayt baslik + 496 veri)
              baslik: [0]=0x01 [1]=0xFF [2:6]=toplam(uint32 LE)
                      [6:8]=parca verisi(uint16) [8]=1(LY)
                      [9:11]=parca sayisi [11:13]=parca indeksi
              parca sayisi 4'un katina yuvarlanir (LY)
              USB'ye 4096'lik bloklar halinde yazilir, sonra 512 ACK okunur

Gereksinim:  pip install pyusb   (+ libusb-1.0.dll Windows'ta)
Windows'ta YONETICI gerekir.
"""
import io
import os
import struct
import sys
import time

import usb.core
import usb.util
try:
    import usb.backend.libusb1 as _libusb1
except Exception:
    _libusb1 = None

VID = 0x0416
PID = 0x5408

EP_WRITE = 0x01
EP_READ = 0x81
USB_CONFIG = 1
USB_IFACE = 0

WIDTH, HEIGHT = 1920, 462
ENCODE_ANGLE = 180          # FBL 192 + SUB 5 + orientation 0
JPEG_QUALITY = 92           # 95 orijinal; 92 daha kucuk paket = daha hizli

HANDSHAKE_HEADER = bytes([
    0x02, 0xFF, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
])
HANDSHAKE_PAYLOAD = HANDSHAKE_HEADER + bytes(2032)      # 2048
HANDSHAKE_READ = 512

CHUNK_SIZE = 512
CHUNK_HEADER = 16
CHUNK_DATA = 496
USB_WRITE_SIZE = 4096

TIMEOUT_HANDSHAKE = 1000
TIMEOUT_WRITE = 5000
TIMEOUT_READ = 1000


class TrccDirect:
    """Panele dogrudan USB baglantisi. connect() -> send_frame(surface) ..."""

    def __init__(self):
        self.dev = None
        self.pm = 0
        self.sub = 0
        self.ep_out = None      # cihazdan otomatik tespit edilir
        self.ep_in = None
        self._open = False

    # ---------- baglanti ----------
    def connect(self):
        # Windows'ta libusb DLL'ini bul (trcc kurulumundan da olabilir).
        # NOT: import fonksiyon icinde OLMAMALI - Python 'usb'yi yerel degisken
        # sayar ve Linux'ta (blok calismayinca) UnboundLocalError verir.
        backend = None
        if sys.platform == "win32":
            for dll in (
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "libusb-1.0.dll"),
                r"C:\Program Files\TRCC\libusb-1.0.dll",
            ):
                if os.path.exists(dll):
                    backend = _libusb1.get_backend(find_library=lambda x, d=dll: d)
                    if backend:
                        break

        self.dev = usb.core.find(idVendor=VID, idProduct=PID, backend=backend)
        if self.dev is None:
            raise RuntimeError(f"Panel bulunamadi ({VID:04X}:{PID:04X}). "
                               "Takili mi? Yonetici misin?")

        # kernel surucusunu ayir (Linux; Windows'ta NotImplementedError -> gecilir)
        for i in range(4):
            try:
                if self.dev.is_kernel_driver_active(i):
                    self.dev.detach_kernel_driver(i)
            except (usb.core.USBError, NotImplementedError):
                pass

        try:
            cfg = self.dev.get_active_configuration()
            if cfg.bConfigurationValue != USB_CONFIG:
                self.dev.set_configuration(USB_CONFIG)
        except usb.core.USBError:
            self.dev.set_configuration(USB_CONFIG)

        usb.util.claim_interface(self.dev, USB_IFACE)
        self._open = True

        # --- ENDPOINT'leri CIHAZDAN oku (sabit degil! trcc de boyle yapiyor) ---
        cfg = self.dev.get_active_configuration()
        intf = cfg[(USB_IFACE, 0)]
        for ep in intf:
            d = usb.util.endpoint_direction(ep.bEndpointAddress)
            if d == usb.util.ENDPOINT_OUT and self.ep_out is None:
                self.ep_out = ep.bEndpointAddress
            elif d == usb.util.ENDPOINT_IN and self.ep_in is None:
                self.ep_in = ep.bEndpointAddress
        if self.ep_out is None or self.ep_in is None:
            raise RuntimeError(f"Endpoint bulunamadi (out={self.ep_out}, in={self.ep_in})")
        print(f"[trcc_direct] Endpoint: OUT=0x{self.ep_out:02x}  IN=0x{self.ep_in:02x}")

        # --- HANDSHAKE ---
        self.dev.write(self.ep_out, HANDSHAKE_PAYLOAD, TIMEOUT_HANDSHAKE)
        resp = self.dev.read(self.ep_in, HANDSHAKE_READ, TIMEOUT_HANDSHAKE)

        if len(resp) < 37 or resp[0] != 3 or resp[1] != 0xFF or resp[8] != 1:
            raise RuntimeError(f"Handshake dogrulama basarisiz "
                               f"([0]={resp[0]}, [1]={resp[1]}, [8]={resp[8]})")

        raw = resp[20]
        if raw <= 3:
            raw = 1
        self.pm = 64 + raw
        self.sub = resp[22] + 1

        print(f"[trcc_direct] Baglandi: {WIDTH}x{HEIGHT}, PM={self.pm}, SUB={self.sub}")
        return True

    # ---------- kare gonderme ----------
    def send_jpeg(self, jpeg_bytes):
        """JPEG baytlarini LY chunk protokolüyle panele yaz."""
        if not self._open:
            raise RuntimeError("Once connect() cagir")

        total = len(jpeg_bytes)
        num_chunks = total // CHUNK_DATA + 1
        last_len = total % CHUNK_DATA

        buf = bytearray(num_chunks * CHUNK_SIZE)
        for i in range(num_chunks):
            off = i * CHUNK_SIZE
            dlen = last_len if i == num_chunks - 1 else CHUNK_DATA
            buf[off] = 0x01
            buf[off + 1] = 0xFF
            struct.pack_into("<I", buf, off + 2, total)
            struct.pack_into("<H", buf, off + 6, dlen)
            buf[off + 8] = 1                      # LY
            struct.pack_into("<H", buf, off + 9, num_chunks)
            struct.pack_into("<H", buf, off + 11, i)
            src = i * CHUNK_DATA
            buf[off + CHUNK_HEADER:off + CHUNK_HEADER + dlen] = jpeg_bytes[src:src + dlen]

        # parca sayisini 4'un katina tamamla (LY)
        padded = num_chunks + ((4 - num_chunks % 4) % 4)
        total_bytes = padded * CHUNK_SIZE
        send_buf = bytes(buf) + bytes(total_bytes - len(buf))

        pos = 0
        while pos < total_bytes:
            remaining = total_bytes - pos
            wsize = USB_WRITE_SIZE if remaining >= USB_WRITE_SIZE else min(2048, remaining)
            self.dev.write(self.ep_out, send_buf[pos:pos + wsize], TIMEOUT_WRITE)
            pos += USB_WRITE_SIZE

        # ACK
        try:
            self.dev.read(self.ep_in, HANDSHAKE_READ, TIMEOUT_READ)
        except usb.core.USBError:
            pass    # bazi kareler ACK vermeyebilir
        return True

    def send_surface(self, surface):
        """pygame Surface (1920x462) -> 180 derece dondur -> JPEG -> gonder."""
        import pygame
        rot = pygame.transform.rotate(surface, ENCODE_ANGLE)   # 180
        buf = io.BytesIO()
        pygame.image.save(rot, buf, "frame.jpg")               # pygame JPEG destekler
        return self.send_jpeg(buf.getvalue())

    def close(self):
        if self.dev is not None and self._open:
            try:
                usb.util.release_interface(self.dev, USB_IFACE)
            except Exception:
                pass
            try:
                usb.util.dispose_resources(self.dev)
            except Exception:
                pass
        self._open = False


# ---------------- test ----------------
if __name__ == "__main__":
    import math
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    import pygame
    pygame.display.init()
    pygame.font.init()

    d = TrccDirect()
    d.connect()

    surf = pygame.Surface((WIDTH, HEIGHT))
    font = pygame.font.SysFont("Arial", 72, bold=True)

    print("60 kare gonderiliyor (PANELE BAK)...")
    t0 = time.time()
    for i in range(60):
        surf.fill((6, 10, 18))
        t = font.render(f"DOGRUDAN USB  #{i}", True, (60, 230, 120))
        surf.blit(t, (WIDTH // 2 - t.get_width() // 2, 40))
        for j in range(70):
            h = int(130 + 120 * math.sin(i * 0.4 + j * 0.25))
            pygame.draw.rect(surf, (0, 160 + (j * 2) % 95, 255 - (j * 2) % 100),
                             (40 + j * 27, 440 - h, 18, h))
        d.send_surface(surf)
    dt = time.time() - t0
    print(f"60 kare / {dt:.2f} sn  ->  {60/dt:.1f} FPS")
    d.close()
