#!/bin/bash
# kur_masaustu.command — VU Meter MASAUSTU Mac kurulum ("VU Meter Masaustu.app")
# LCD panel GEREKMEZ - gorselleştirme pencerede calisir.
cd "$(dirname "$0")" || exit 1
echo "=================================================="
echo "  VU METER MASAUSTU - Mac Kurulum"
echo "=================================================="

# --- 1) Python3 kontrol ---
if ! command -v python3 >/dev/null 2>&1; then
    echo "[!] python3 bulunamadi. Xcode Command Line Tools kurun:"
    echo "    xcode-select --install"
    read -p "Enter..."; exit 1
fi
echo "[OK] python3: $(python3 --version)"

# --- 2) Python paketleri ---
echo "[*] Python paketleri kontrol ediliyor (pygame, numpy, PyQt5, psutil)..."
python3 -m pip install --quiet --user pygame numpy PyQt5 psutil 2>/dev/null
echo "[OK] paketler hazir"

# --- 3) cava kontrol (mac_deps'ten offline kurulum destekli) ---
if ! command -v cava >/dev/null 2>&1 && [ ! -x /usr/local/bin/cava ]; then
    if [ -d "mac_deps" ] && [ -f "mac_deps/cava" ]; then
        echo "[*] cava mac_deps'ten kuruluyor..."
        sudo cp mac_deps/cava /usr/local/bin/cava 2>/dev/null || cp mac_deps/cava /usr/local/bin/cava
        sudo cp mac_deps/libportaudio*.dylib mac_deps/libfftw3*.dylib mac_deps/libiniparser*.dylib /usr/local/lib/ 2>/dev/null
        chmod +x /usr/local/bin/cava
        echo "[OK] cava kuruldu (mac_deps)"
    else
        echo "[!] cava yok. 'brew install cava' ile kurun ya da mac_deps klasorunu ekleyin."
        read -p "Enter..."; exit 1
    fi
else
    echo "[OK] cava mevcut"
fi

# --- 4) C sensor araclarini derle (sysmon icin) ---
echo "[*] Sensor araclari derleniyor..."
for tool in smc_read gpu_read disk_read ipg_read; do
    if [ -f "${tool}.c" ] && [ ! -x "${tool}" ]; then
        case "$tool" in
            ipg_read) clang -o "$tool" "${tool}.c" -framework IntelPowerGadget -F /Library/Frameworks 2>/dev/null ;;
            *)        clang -o "$tool" "${tool}.c" -framework IOKit -framework CoreFoundation 2>/dev/null ;;
        esac
        [ -x "$tool" ] && echo "    + $tool derlendi" || echo "    - $tool derlenemedi (o sensor bos gorunur)"
    fi
done

# --- 5) Uygulama dosyalari ---
APP="/Applications/VU Meter Masaustu.app"
echo "[*] Uygulama (.app) olusturuluyor: $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/app"

# gereken dosyalar
FILES="vumeter_mac_desktop.py control_window_desktop.py sysmon_window.py sysmon_mac.py smc_read gpu_read disk_read ipg_read smc_read.c gpu_read.c disk_read.c ipg_read.c vu_bg.png vu_bg2.png vu_bg3.png"
for f in $FILES; do
    [ -e "$f" ] && cp "$f" "$APP/Contents/Resources/app/" 2>/dev/null
done
echo "[OK] dosyalar kopyalandi"

# --- 6) Info.plist ---
cat > "$APP/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>VU Meter Masaustu</string>
    <key>CFBundleDisplayName</key><string>VU Meter Masaüstü</string>
    <key>CFBundleIdentifier</key><string>com.vumeter.desktop</string>
    <key>CFBundleVersion</key><string>1.0</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundleIconFile</key><string>appicon</string>
    <key>NSMicrophoneUsageDescription</key><string>Ses gorselleştirme icin sistem sesini dinler.</string>
    <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
PLIST

# --- 7) Launcher ---
cat > "$APP/Contents/MacOS/launcher" << 'LAUNCH'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources/app" && pwd)"
cd "$DIR" || exit 1
pkill -f vumeter_mac_desktop 2>/dev/null
sleep 1
PY="$(command -v python3)"
exec "$PY" vumeter_mac_desktop.py
LAUNCH
chmod +x "$APP/Contents/MacOS/launcher"

# --- 8) Ikon (camgobegi tonlu - LCD'den ayirt edilsin) ---
if [ -f "app_icon_1024.png" ]; then
    TMP="$(mktemp -d)"; ICONSET="$TMP/appicon.iconset"; mkdir -p "$ICONSET"
    for sz in 16 32 64 128 256 512; do
        sips -z $sz $sz app_icon_1024.png --out "$ICONSET/icon_${sz}x${sz}.png" >/dev/null 2>&1
        d=$((sz*2)); sips -z $d $d app_icon_1024.png --out "$ICONSET/icon_${sz}x${sz}@2x.png" >/dev/null 2>&1
    done
    cp app_icon_1024.png "$ICONSET/icon_512x512@2x.png"
    iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/appicon.icns" 2>/dev/null
fi
touch "$APP"

echo ""
echo "=================================================="
echo "[OK] Kurulum tamam: $APP"
echo "     Launchpad'den 'VU Meter Masaustu' ile acin."
echo "     Kisayollar: 1-6 mod, TAB tema, C kanal, W sistem monitoru, Q cikis"
echo "=================================================="
read -p "Kapatmak icin Enter..."
