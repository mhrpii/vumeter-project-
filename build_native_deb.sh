#!/bin/bash
# vumeter-lcd-native (.deb) build script - Yol 2: native 1920x462 + trcc HTTP API
# Eski vumeter-lcd ile CAKISMAZ, ayri paket. Ikisi bir arada durabilir.
set -e

VER="2.0.0"
ROOT="$HOME/vumeter-deb-build/vumeter-lcd-native_${VER}"
SRC="$HOME/İndirilenler/files"

echo "== Build agaci temizle + olustur =="
rm -rf "$ROOT"
mkdir -p "$ROOT/opt/vumeter-lcd-native"
mkdir -p "$ROOT/usr/bin"
mkdir -p "$ROOT/usr/share/applications"
mkdir -p "$ROOT/etc/xdg/autostart"
mkdir -p "$ROOT/DEBIAN"

echo "== Dosyalari kopyala =="
cp "$SRC/native_proto.py"   "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/control_window.py" "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/sysmon.py"        "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/vu_bg.png"        "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/vu_bg2.png"       "$ROOT/opt/vumeter-lcd-native/"
cp "$SRC/vu_bg3.png"       "$ROOT/opt/vumeter-lcd-native/"

# ONEMLI: dosya izinlerini duzelt (sysmon.py bazen -rw------- geliyor -> import PermissionError)
chmod 644 "$ROOT/opt/vumeter-lcd-native/"*.py "$ROOT/opt/vumeter-lcd-native/"*.png

echo "== control =="
cat > "$ROOT/DEBIAN/control" << 'EOF'
Package: vumeter-lcd-native
Version: 2.0.0
Section: sound
Priority: optional
Architecture: all
Depends: python3, python3-pygame, python3-numpy, python3-psutil, python3-pyqt5, python3-requests, cava, lm-sensors
Recommends: pipx
Maintainer: Mahir
Description: Thermalright LCD Ses Gorsellestirme (Native + HTTP API)
 Yol 2 mimarisi: native 1920x462 cizim + trcc REST API gonderim.
 trcc-shell yerine 'trcc serve' HTTP API kullanir. Daha net, akici, kararli.
 6 mod: Spektrum, LED Spektrum, VU Metre, Sistem Monitoru, Olcum Paneli.
 cava (PipeWire) ve trcc-linux gerektirir.
EOF

echo "== launcher (/usr/bin/vumeter-lcd-native) =="
cat > "$ROOT/usr/bin/vumeter-lcd-native" << 'EOF'
#!/bin/bash
export PATH="$HOME/.local/bin:$PATH"
cd /opt/vumeter-lcd-native
exec python3 /opt/vumeter-lcd-native/native_proto.py "$@"
EOF
chmod +x "$ROOT/usr/bin/vumeter-lcd-native"

echo "== .desktop (menu) =="
cat > "$ROOT/usr/share/applications/vumeter-lcd-native.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=Vumeter LCD Native
Comment=Native+API LCD ses gorsellestirme (Yol 2)
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
Comment=Acilista native LCD ses gorsellestirme
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
udevadm control --reload-rules 2>/dev/null || true
echo ""
echo "======================================================"
echo " vumeter-lcd-native (Yol 2: Native+API) kuruldu!"
echo ""
echo " ONEMLI: trcc-linux gerekli (pipx ile):"
echo "   pipx install git+https://github.com/Lexonight1/thermalright-trcc-linux.git"
echo "   trcc setup"
echo ""
echo " Calistirmak icin: vumeter-lcd-native"
echo " (uygulama otomatik 'trcc serve' baslatir)"
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
