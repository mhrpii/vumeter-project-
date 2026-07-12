#!/bin/bash
# vumeter-lcd-native (.deb) build script
# v3.0.0: DOGRUDAN USB (trcc / HTTP API / PNG / tema klasoru YOK)
set -e

VER="3.0.0"
ROOT="$HOME/vumeter-deb-build/vumeter-lcd-native_${VER}"
SRC="$HOME/İndirilenler/files"

echo "== Build agaci temizle + olustur =="
rm -rf "$ROOT"
mkdir -p "$ROOT/opt/vumeter-lcd-native"
mkdir -p "$ROOT/usr/bin"
mkdir -p "$ROOT/usr/share/applications"
mkdir -p "$ROOT/etc/xdg/autostart"
mkdir -p "$ROOT/lib/udev/rules.d"
mkdir -p "$ROOT/DEBIAN"

echo "== Dosyalari kopyala =="
cp "$SRC/native_proto.py"   "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/trcc_direct.py"    "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/control_window.py" "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/sysmon.py"         "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/vu_bg.png"         "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/vu_bg2.png"        "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/vu_bg3.png"        "$ROOT/opt/vumeter-lcd-native/"

# dosya izinleri (sysmon.py bazen -rw------- geliyor -> import PermissionError)
chmod 644 "$ROOT/opt/vumeter-lcd-native/"*.py "$ROOT/opt/vumeter-lcd-native/"*.png

echo "== udev kurali (panele root'suz USB erisimi) =="
cat > "$ROOT/lib/udev/rules.d/99-trcc-lcd.rules" << 'EOF'
# Thermalright Trofeo Vision LCD - kullanici erisimi (dogrudan USB icin)
SUBSYSTEM=="usb", ATTRS{idVendor}=="0416", ATTRS{idProduct}=="5408", MODE="0666", TAG+="uaccess"
EOF
chmod 644 "$ROOT/lib/udev/rules.d/99-trcc-lcd.rules"

echo "== control =="
cat > "$ROOT/DEBIAN/control" << 'EOF'
Package: vumeter-lcd-native
Version: 3.0.0
Section: sound
Priority: optional
Architecture: all
Depends: python3, python3-pygame, python3-numpy, python3-psutil, python3-pyqt5, python3-usb, cava, lm-sensors
Maintainer: Mahir
Description: Thermalright LCD Ses Gorsellestirme (Dogrudan USB)
 Panele DOGRUDAN USB ile yazar - trcc, HTTP API, PNG ve tema klasoru YOK.
 USB protokolu tersine cevrildi (handshake + LY chunk protokolu + JPEG).
 Aninda acilis, ~24 FPS kararli, USB kilitlenme sorunu cozuldu.
 6 mod: Spektrum, Spektrum 2, LED Spektrum, LED Nokta, VU Metre,
 Olcum Paneli + Sistem Monitoru (dairesel gauge tasarimi).
 cava (PipeWire) gerektirir. trcc GEREKMEZ.
EOF

echo "== launcher (/usr/bin/vumeter-lcd-native) =="
cat > "$ROOT/usr/bin/vumeter-lcd-native" << 'EOF'
#!/bin/bash
cd /opt/vumeter-lcd-native
exec python3 /opt/vumeter-lcd-native/native_proto.py "$@"
EOF
chmod +x "$ROOT/usr/bin/vumeter-lcd-native"

echo "== .desktop (menu) =="
cat > "$ROOT/usr/share/applications/vumeter-lcd-native.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=Vumeter LCD Native
Comment=LCD ses gorsellestirme (dogrudan USB)
Exec=vumeter-lcd-native
Icon=multimedia-volume-control
Terminal=false
Categories=AudioVideo;Audio;
EOF

echo "== .desktop (autostart) =="
cat > "$ROOT/etc/xdg/autostart/vumeter-lcd-native.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=Vumeter LCD Native
Comment=Acilista LCD ses gorsellestirme
Exec=bash -c "sleep 20; exec vumeter-lcd-native --autostart"
Icon=multimedia-volume-control
Terminal=false
Categories=AudioVideo;Audio;
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
EOF

echo "== postinst =="
cat > "$ROOT/DEBIAN/postinst" << 'EOF'
#!/bin/bash
set -e
# RAPL guc sensoru okuma izni
cat > /etc/udev/rules.d/99-powercap.rules << 'RULE'
SUBSYSTEM=="powercap", ACTION=="add", RUN+="/bin/chmod -R a+r /sys/devices/virtual/powercap"
RULE
chmod -R a+r /sys/class/powercap/intel-rapl* 2>/dev/null || true

# udev kurallarini yukle (LCD USB erisimi + powercap)
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

echo ""
echo "======================================================"
echo " vumeter-lcd-native 3.0.0 (DOGRUDAN USB) kuruldu!"
echo ""
echo " trcc ARTIK GEREKMIYOR - panele dogrudan yaziliyor."
echo ""
echo " ONEMLI: Panel takiliysa BIR KEZ cikarip takin"
echo " (udev kuralinin uygulanmasi icin)."
echo ""
echo " Calistirmak icin: vumeter-lcd-native"
echo "======================================================"
echo ""
exit 0
EOF
chmod +x "$ROOT/DEBIAN/postinst"

echo "== prerm (temiz kapanma) =="
cat > "$ROOT/DEBIAN/prerm" << 'EOF'
#!/bin/bash
pkill -f native_proto.py 2>/dev/null || true
exit 0
EOF
chmod +x "$ROOT/DEBIAN/prerm"

echo "== paketle =="
dpkg-deb --build --root-owner-group "$ROOT" "$HOME/vumeter-deb-build/vumeter-lcd-native_${VER}.deb"

echo ""
echo "TAMAM: $HOME/vumeter-deb-build/vumeter-lcd-native_${VER}.deb"
echo "Kurmak icin: sudo dpkg -i $HOME/vumeter-deb-build/vumeter-lcd-native_${VER}.deb"
