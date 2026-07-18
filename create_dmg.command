#!/bin/bash
#
# create_dmg.sh — VU Meter LCD dagitim .dmg olusturur
#
# Bu script'in bulundugu klasordeki GEREKLI dosyalari (patch/test haric)
# gecici bir klasore toplar, sonra hdiutil ile .dmg uretir.
#
# Kullanim: Mac'te bu script'e cift tikla (ya da: bash create_dmg.sh)
# Cikti: VU_Meter_LCD.dmg (ayni klasorde)
#
cd "$(dirname "$0")" || exit 1

APP_NAME="VU Meter LCD"
DMG_NAME="VU_Meter_LCD"
STAGING="$(mktemp -d)/VU Meter LCD"
mkdir -p "$STAGING"

echo "=== VU Meter LCD .dmg olusturuluyor ==="
echo ""

# --- Gerekli dosyalari kopyala ---
echo "[*] Dosyalar toplaniyor..."

# Python calisma dosyalari
for f in native_proto_mac.py sysmon_mac.py control_window.py trcc_direct.py; do
    [ -f "$f" ] && cp "$f" "$STAGING/" && echo "    + $f"
done

# C kaynak dosyalari (hedef Mac'te derlenecek)
for f in smc_read.c gpu_read.c disk_read.c ipg_read.c; do
    [ -f "$f" ] && cp "$f" "$STAGING/" && echo "    + $f"
done

# Kurulum + dokuman + ikon
for f in kur.command README_MAC.md app_icon_1024.png; do
    [ -f "$f" ] && cp "$f" "$STAGING/" && echo "    + $f"
done

# mac_deps/ (hazir cava + dylib'ler - macOS 12 icin)
if [ -d "mac_deps" ]; then
    cp -R mac_deps "$STAGING/"
    echo "    + mac_deps/ ($(ls mac_deps | wc -l | tr -d ' ') dosya)"
fi

# kur.command calistirilabilir olsun
chmod +x "$STAGING/kur.command" 2>/dev/null

# --- KURULUM oku (kisa yonlendirme) ---
cat > "$STAGING/1_OKU_ONCE.txt" << 'TXT'
VU Meter LCD — Kurulum

1) Bu klasorun TAMAMINI Masaustu'ne (ya da bir yere) KOPYALA.
   (.dmg icinden dogrudan calistirma — once disari cikar.)

2) Kopyaladigin klasorde "kur.command" dosyasina CIFT TIKLA.
   - Gelistirici uyarisi cikarsa: Sag tik -> Ac -> Ac
   - Bagimliliklari kurar, C araclarini derler, uygulamayi /Applications'a koyar.

3) Ses icin: BlackHole + Multi-Output Device gerekli (README_MAC.md'ye bak).

4) Paneli tak, Launchpad/Spotlight'ta "VU Meter LCD" ac.

Detayli aciklama: README_MAC.md
TXT

# --- Applications kisayolu (opsiyonel, gorsel) ---
ln -s /Applications "$STAGING/../Applications" 2>/dev/null

# --- .dmg olustur ---
echo ""
echo "[*] .dmg paketleniyor..."
rm -f "${DMG_NAME}.dmg"
hdiutil create -volname "$APP_NAME" \
    -srcfolder "$(dirname "$STAGING")" \
    -ov -format UDZO \
    "${DMG_NAME}.dmg" >/dev/null 2>&1

if [ -f "${DMG_NAME}.dmg" ]; then
    SIZE="$(du -h "${DMG_NAME}.dmg" | cut -f1)"
    echo "[OK] Olusturuldu: ${DMG_NAME}.dmg ($SIZE)"
    echo ""
    echo "=================================================="
    echo "  ${DMG_NAME}.dmg hazir!"
    echo "  Baska Mac'e ver -> cift tikla -> klasoru cikar -> kur.command"
    echo "=================================================="
else
    echo "[!] .dmg olusturulamadi."
fi

echo ""
read -p "Kapatmak icin Enter..."
