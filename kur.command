#!/bin/bash
#
# kur.command — VU Meter LCD Mac kurulum + Applications'a .app kur
#
cd "$(dirname "$0")" || exit 1

echo "=================================================="
echo "  VU Meter LCD — Mac Kurulum"
echo "=================================================="
echo ""

# macOS surumu tespit (12 ve oncesi Homebrew Tier 3 - cava kurulamaz)
OSVER="$(sw_vers -productVersion 2>/dev/null | cut -d. -f1)"
ESKI_MACOS=0
if [ -n "$OSVER" ] && [ "$OSVER" -le 12 ]; then
    ESKI_MACOS=1
    echo "[i] macOS $OSVER tespit edildi (eski surum)."
    echo "    Homebrew bu surumde cava'yi kaynaktan derleyemeyebilir."
    echo "    Sorun cikarsa README'deki 'macOS 12 elle kurulum' bolumune bak."
    echo ""
fi

# --- 1) Homebrew ---
if ! command -v brew >/dev/null 2>&1; then
    echo "[!] Homebrew yok. Kur: https://brew.sh"
    read -p "Enter ile devam..."
else
    echo "[OK] Homebrew bulundu."
fi

# --- 2) cava + libusb ---
echo ""
echo "[*] cava + libusb kuruluyor..."
ARCH="$(uname -m)"
install_cava_from_deps() {
    # mac_deps/ icindeki hazir cava + dylib'leri yerine koy (Intel Mac icin)
    if [ ! -d "mac_deps" ] || [ ! -f "mac_deps/cava" ]; then
        return 1
    fi
    if [ "$ARCH" != "x86_64" ]; then
        echo "    [!] mac_deps Intel (x86_64) icin; bu makine $ARCH — kullanilamaz."
        return 1
    fi
    echo "    [*] mac_deps/ icinden hazir cava + kutuphaneler kuruluyor..."
    # dylib hedef klasorleri
    mkdir -p /usr/local/opt/portaudio/lib /usr/local/opt/fftw/lib /usr/local/opt/iniparser/lib 2>/dev/null
    cp mac_deps/libportaudio.2.dylib  /usr/local/opt/portaudio/lib/ 2>/dev/null
    cp mac_deps/libfftw3.3.dylib      /usr/local/opt/fftw/lib/      2>/dev/null
    cp mac_deps/libiniparser.4.dylib  /usr/local/opt/iniparser/lib/ 2>/dev/null
    # dylib'leri /usr/local/lib'e de koy (yedek arama yolu)
    cp mac_deps/*.dylib /usr/local/lib/ 2>/dev/null
    # cava binary
    cp mac_deps/cava /usr/local/bin/cava 2>/dev/null
    chmod +x /usr/local/bin/cava 2>/dev/null
    if /usr/local/bin/cava -v >/dev/null 2>&1; then
        echo "    [OK] cava mac_deps'ten kuruldu ($(cava -v 2>/dev/null | head -1))."
        return 0
    fi
    return 1
}

if command -v cava >/dev/null 2>&1 && cava -v >/dev/null 2>&1; then
    echo "[OK] cava zaten kurulu ($(command -v cava))."
elif install_cava_from_deps; then
    :   # mac_deps'ten kuruldu
elif command -v brew >/dev/null 2>&1; then
    echo "[*] cava brew ile deneniyor..."
    if ! brew install cava 2>/dev/null; then
        echo "[!] cava brew ile kurulamadi (eski macOS + gcc derleme sorunu olabilir)."
        echo "    mac_deps/ klasoru yoksa: README 'macOS 12 elle kurulum' bolumunu izle."
    fi
else
    echo "[!] cava kurulamadi (Homebrew yok, mac_deps yok)."
fi
command -v brew >/dev/null 2>&1 && { brew list libusb >/dev/null 2>&1 || brew install libusb 2>/dev/null; }

# --- 3) BlackHole kontrolu (ses yakalama icin) ---
echo ""
echo "[*] Ses yakalama (BlackHole) kontrol ediliyor..."
if system_profiler SPAudioDataType 2>/dev/null | grep -qi "BlackHole"; then
    echo "[OK] BlackHole kurulu."
else
    echo "[i] BlackHole kurulu degil - SORUN DEGIL: Scarlett gibi loopback'li
    ses karti varsa gerek YOK (otomatik aggregate ile calisir).
    Loopback'siz aygitlar (dahili hoparlor vb.) icin alternatif:"
    echo "    macOS, hoparlorden calan sesi dogrudan yakalatmaz; BlackHole (sanal"
    echo "    ses aygiti) gerekir."
    echo "    1) Indir: https://existential.audio/blackhole/ (2ch) — .pkg installer"
    echo "       ya da https://github.com/ExistentialAudio/BlackHole/releases"
    echo "    2) Kur (.pkg cift tikla)."
    echo "    3) Audio MIDI Setup -> + -> Create Multi-Output Device"
    echo "       -> Built-in Output + BlackHole 2ch isaretle"
    echo "    4) Sistem ses cikisini 'Multi-Output Device' yap."
    echo "    (Detay: README 'Ses kurulumu' bolumu)"
fi

# --- 4) Python kutuphaneleri ---
echo ""
echo "[*] Python kutuphaneleri kuruluyor..."
PYBIN="$(command -v python3)"
[ -z "$PYBIN" ] && { echo "[!] python3 yok: xcode-select --install"; read -p "Enter..."; }
"$PYBIN" -m pip install --user pygame PyQt5 numpy psutil pyusb Pillow 2>&1 | tail -2
echo "[OK] Python kutuphaneleri hazir."

# --- 5) C araclarini derle ---
echo ""
echo "[*] C sensor araclari derleniyor..."
compile() {
    if [ -f "$2" ]; then
        clang -O2 -o "$1" "$2" "${@:3}" 2>/dev/null \
            && echo "    [OK] $1" || echo "    [!] $1 derlenemedi"
    fi
}
compile smc_read  smc_read.c  -framework IOKit -framework CoreFoundation
compile gpu_read  gpu_read.c  -framework IOKit -framework CoreFoundation
compile disk_read disk_read.c -framework IOKit -framework CoreFoundation
if [ -d "/Library/Frameworks/IntelPowerGadget.framework" ]; then
    compile ipg_read ipg_read.c -F/Library/Frameworks -framework IntelPowerGadget
else
    echo "    [!] Intel Power Gadget yok — cekirdek isi haritasi calismaz (opsiyonel)."
fi
compile make_aggregate make_aggregate.c -framework CoreAudio -framework CoreFoundation
if [ -x "make_aggregate" ]; then
    AGGOUT=$(./make_aggregate)
    echo "    [*] Aggregate Device (Tahoe ses yolu): $AGGOUT"
fi

# --- 6) .app bundle olustur ---
echo ""
echo "[*] Uygulama (.app) olusturuluyor..."
APP="/Applications/VU Meter LCD.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
mkdir -p "$APP/Contents/Resources/app"
cp *.py                                 "$APP/Contents/Resources/app/" 2>/dev/null
cp *.c                                  "$APP/Contents/Resources/app/" 2>/dev/null
cp smc_read gpu_read disk_read ipg_read make_aggregate "$APP/Contents/Resources/app/" 2>/dev/null
cp *.png                                "$APP/Contents/Resources/app/" 2>/dev/null

cat > "$APP/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>VU Meter LCD</string>
    <key>CFBundleDisplayName</key><string>VU Meter LCD</string>
    <key>CFBundleIdentifier</key><string>com.mhrpii.vumeterlcd</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>appicon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>10.13</string>
    <key>LSUIElement</key><true/>
</dict>
</plist>
PLIST

cat > "$APP/Contents/MacOS/launcher" << 'LAUNCH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
cd "$DIR" || exit 1
pkill -f native_proto_mac 2>/dev/null
sleep 1
PY="$(command -v python3)"
exec "$PY" native_proto_mac.py "Spektrum"
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

if [ -f "app_icon_1024.png" ]; then
    TMP="$(mktemp -d)"; ICONSET="$TMP/appicon.iconset"; mkdir -p "$ICONSET"
    for sz in 16 32 64 128 256 512; do
        sips -z $sz $sz app_icon_1024.png --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1
        d=$((sz*2)); sips -z $d $d app_icon_1024.png --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
    done
    cp app_icon_1024.png "$ICONSET/icon_512x512@2x.png"
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/appicon.icns" 2>/dev/null
    # ikonu zorla uygula (onbellek atlatma)
    command -v fileicon >/dev/null 2>&1 && fileicon set "$APP" app_icon_1024.png >/dev/null 2>&1
fi
touch "$APP"

echo "[OK] Uygulama kuruldu: $APP"
echo ""
echo "=================================================="
echo "  Kurulum tamamlandi!"
echo ""
echo "  - Paneli tak, Launchpad/Spotlight'ta 'VU Meter LCD' ac"
echo "  - Ses barlari icin: BlackHole + Multi-Output Device gerekli"
echo "    (yukaridaki nota / README'ye bak)"
echo "  - Ses gelmezse: sistem cikisi 'Multi-Output Device' olmali"
echo "=================================================="
echo ""
read -p "Kapatmak icin Enter..."
