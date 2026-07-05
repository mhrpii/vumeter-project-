#!/bin/bash
# vumeter-desktop .deb kurulum scripti
# Kullanim: bash build_desktop_deb.sh > /tmp/deb-desktop.txt 2>&1
set -x

SRC=~/İndirilenler/files
ROOT=~/vumeter-deb-build/vumeter-desktop_1.0.0

# ---- 1) Paket agaci ----
rm -rf "$ROOT"
mkdir -p "$ROOT/DEBIAN" \
         "$ROOT/opt/vumeter-desktop" \
         "$ROOT/usr/bin" \
         "$ROOT/usr/share/applications"

# ---- 2) Uygulama dosyalari ----
for f in vumeter_linux.py sysmon.py sysmon_window.py vu_bg.png vu_bg2.png vu_bg3.png; do
    cp "$SRC/$f" "$ROOT/opt/vumeter-desktop/" || { echo "EKSIK DOSYA: $f"; exit 1; }
done

# ---- 3) DEBIAN/control ----
cat > "$ROOT/DEBIAN/control" << 'EOF'
Package: vumeter-desktop
Version: 1.0.0
Section: sound
Priority: optional
Architecture: all
Depends: python3, python3-pygame, python3-numpy, python3-psutil, python3-pyqt5, cava
Maintainer: Mahir
Description: Masaustu Ses Gorsellestirme ve Sistem Monitoru
 Vintage Audio Console masaustu surumu: spektrum (2 tip), LED spektrum,
 LED nokta, VU metre (3 kadran), olcum paneli ve 21 barli sistem monitoru.
 Tepsi ikonu ile mod/tema/kadran secimi. cava (PipeWire) gerektirir.
EOF

# ---- 4) Baslatici ----
cat > "$ROOT/usr/bin/vumeter-desktop" << 'EOF'
#!/bin/bash
cd /opt/vumeter-desktop
exec python3 /opt/vumeter-desktop/vumeter_linux.py "$@"
EOF
chmod 755 "$ROOT/usr/bin/vumeter-desktop"

# ---- 5) Uygulama menusu girisi ----
cat > "$ROOT/usr/share/applications/vumeter-desktop.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=Vumeter Desktop
Comment=Vintage Audio Console - masaustu ses gorsellestirme
Exec=vumeter-desktop
Icon=multimedia-volume-control
Terminal=false
Categories=AudioVideo;Audio;
EOF

# ---- 6) postinst ----
cat > "$ROOT/DEBIAN/postinst" << 'EOF'
#!/bin/bash
set -e
echo ""
echo "======================================================"
echo " vumeter-desktop kuruldu!"
echo ""
echo " Calistirmak icin: vumeter-desktop"
echo " (veya uygulama menusunden 'Vumeter Desktop')"
echo ""
echo " Tuslar: 1-6 modlar, TAB tema/kadran, W monitor, Q cikis"
echo " Tepsi ikonu: sag tik -> mod/tema/kadran/cikis"
echo "======================================================"
exit 0
EOF
chmod 755 "$ROOT/DEBIAN/postinst"

# ---- 7) Izinler + derle + kur ----
chmod 644 "$ROOT/opt/vumeter-desktop/"*
chmod 644 "$ROOT/usr/share/applications/vumeter-desktop.desktop"

dpkg-deb --build --root-owner-group "$ROOT" ~/vumeter-deb-build/vumeter-desktop_1.0.0.deb
sudo dpkg -i ~/vumeter-deb-build/vumeter-desktop_1.0.0.deb

# ---- 8) Dogrulama ----
dpkg -s vumeter-desktop | grep -E '^(Package|Version|Status)'
ls -la /opt/vumeter-desktop/
which vumeter-desktop && echo "KURULUM OK"
