#!/bin/bash
# create_dmg_masaustu.command — VU Meter MASAUSTU dagitim paketi (.dmg)
cd "$(dirname "$0")" || exit 1
echo "=================================================="
echo "  VU METER MASAUSTU - DMG Olusturucu"
echo "=================================================="

DMG_NAME="VU_Meter_Masaustu.dmg"
VOL_NAME="VU Meter Masaustu"
STAGING="$(mktemp -d)/VU_Meter_Masaustu"
mkdir -p "$STAGING"

echo "[*] Dosyalar toplaniyor..."
FILES="vumeter_mac_desktop.py control_window_desktop.py sysmon_window.py sysmon_mac.py smc_read.c gpu_read.c disk_read.c ipg_read.c kur_masaustu.command app_icon_1024.png vu_bg.png vu_bg2.png vu_bg3.png README_MAC.md"
for f in $FILES; do
    if [ -e "$f" ]; then
        cp "$f" "$STAGING/" && echo "    + $f"
    else
        echo "    - $f (yok, atlandi)"
    fi
done

if [ -d "mac_deps" ]; then
    cp -R "mac_deps" "$STAGING/"
    echo "    + mac_deps/ ($(ls mac_deps | wc -l | tr -d ' ') dosya)"
fi

chmod +x "$STAGING/kur_masaustu.command" 2>/dev/null

echo ""
echo "[*] .dmg paketleniyor..."
rm -f "$DMG_NAME"
hdiutil create -volname "$VOL_NAME" -srcfolder "$(dirname "$STAGING")" -ov -format UDZO "$DMG_NAME" >/dev/null 2>&1

if [ -f "$DMG_NAME" ]; then
    SIZE=$(du -h "$DMG_NAME" | cut -f1)
    echo "[OK] Olusturuldu: $DMG_NAME ($SIZE)"
    echo ""
    echo "=================================================="
    echo "  $DMG_NAME hazir!"
    echo "  Baska Mac'e ver -> cift tikla -> klasoru cikar"
    echo "  -> kur_masaustu.command"
    echo "  (LCD panel GEREKMEZ - pencerede calisir)"
    echo "=================================================="
else
    echo "[!] DMG olusturulamadi."
fi
rm -rf "$(dirname "$STAGING")"
read -p "Kapatmak icin Enter..."
