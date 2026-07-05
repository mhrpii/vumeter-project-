#!/bin/bash
# build_app.sh -- VintageAudioConsole.app olusturur
#
# KULLANIM:
#   1) Bu dosyayi, vumeter_trcc_mac.py, AppIcon.icns ve
#      VintageAudioConsole.spec ile AYNI klasore koy (orn. ~/Downloads)
#   2) chmod +x build_app.sh && ./build_app.sh
#
set -e

echo "==> PyInstaller kontrol ediliyor..."
pip3 show pyinstaller >/dev/null 2>&1 || pip3 install pyinstaller --break-system-packages

echo "==> Gerekli python kutuphaneleri kontrol ediliyor..."
pip3 install numpy pygame requests --break-system-packages --break-system-packages >/dev/null 2>&1 || true

echo "==> cava binary'si bulunuyor (app icine gomulecek)..."
CAVA_PATH="$(command -v cava || true)"
if [ -z "$CAVA_PATH" ]; then
  echo "HATA: 'cava' bulunamadi. Once 'brew install cava' calistir."
  exit 1
fi
cp "$CAVA_PATH" ./cava
echo "    cava kopyalandi: $CAVA_PATH -> ./cava"

echo "==> Eski build/dist klasorleri temizleniyor..."
rm -rf build dist

echo "==> PyInstaller calistiriliyor..."
pyinstaller VintageAudioConsole.spec

echo ""
echo "=================================================="
echo "TAMAMLANDI: dist/VintageAudioConsole.app"
echo "=================================================="
echo ""
echo "Uygulamalar klasorune kopyalamak icin:"
echo "  cp -R dist/VintageAudioConsole.app /Applications/"
echo ""
echo "Ilk acilista macOS 'Gelistirici dogrulanamadi' uyarisi verebilir."
echo "Bu durumda: Sistem Ayarlari > Gizlilik ve Guvenlik > 'Aç' butonuna"
echo "bas, veya terminalde:"
echo "  xattr -cr dist/VintageAudioConsole.app"
echo "calistir."
