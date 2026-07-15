/*
 * smc_read.c - macOS SMC anahtar okuyucu (HWMonitorSMC2 + smc.c'den turetildi)
 *
 * Kullanim:  smc_read KEY1 [KEY2 ...]
 *   Her anahtar icin:  KEY=deger   (okunamayan:  KEY=NA)
 * Ornek:     smc_read TC0P PCPT TG0P F0Ac
 *            -> TC0P=51.0  PCPT=14.03  TG0P=58.0  F0Ac=776.0
 *
 * Decode: ui8/ui16/ui32, si8/si16/si32 (big-endian), spXY/fpXY (sabit nokta),
 *         flt (float32), fpe2. HWMonitorSMC2 decodeNumericValue mantigi.
 *
 * Derleme: clang -O2 -o smc_read smc_read.c -framework IOKit -framework CoreFoundation
 */
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <IOKit/IOKitLib.h>

#define KERNEL_INDEX_SMC      2
#define SMC_CMD_READ_BYTES    5
#define SMC_CMD_READ_KEYINFO  9
#define SMC_CMD_READ_INDEX    8

typedef struct { UInt32 dataSize; UInt32 dataType; char dataAttributes; } SMCKeyData_keyInfo_t;
typedef char SMCBytes_t[32];
typedef struct {
    UInt32 key;
    struct { char major, minor, build, reserved[1]; UInt16 release; } vers;
    struct { UInt16 version, length; UInt32 cpuPLimit, gpuPLimit, memPLimit; } pLimitData;
    SMCKeyData_keyInfo_t keyInfo;
    char result, status, data8;
    UInt32 data32;
    SMCBytes_t bytes;
} SMCKeyData_t;
typedef char UInt32Char_t[5];
typedef struct { UInt32Char_t key; UInt32 dataSize; UInt32Char_t dataType; SMCBytes_t bytes; } SMCVal_t;

static io_connect_t conn;

UInt32 _strtoul(char *str, int size, int base) {
    UInt32 total = 0; int i;
    for (i = 0; i < size; i++) {
        if (base == 16) total += str[i] << (size - 1 - i) * 8;
        else total += (unsigned char)(str[i] << (size - 1 - i) * 8);
    }
    return total;
}
void _ultostr(char *str, UInt32 val) {
    str[0] = '\0';
    sprintf(str, "%c%c%c%c",
            (unsigned int)val >> 24, (unsigned int)val >> 16,
            (unsigned int)val >> 8,  (unsigned int)val);
}

kern_return_t SMCOpen(void) {
    kern_return_t result;
    io_iterator_t iterator;
    io_object_t   device;
    CFMutableDictionaryRef matchingDictionary = IOServiceMatching("AppleSMC");
    result = IOServiceGetMatchingServices(kIOMasterPortDefault, matchingDictionary, &iterator);
    if (result != kIOReturnSuccess) return result;
    device = IOIteratorNext(iterator);
    IOObjectRelease(iterator);
    if (device == 0) return kIOReturnError;
    result = IOServiceOpen(device, mach_task_self(), 0, &conn);
    IOObjectRelease(device);
    return result;
}
kern_return_t SMCClose(void) { return IOServiceClose(conn); }

kern_return_t SMCCall(int index, SMCKeyData_t *in, SMCKeyData_t *out) {
    size_t inStructSize = sizeof(SMCKeyData_t);
    size_t outStructSize = sizeof(SMCKeyData_t);
    return IOConnectCallStructMethod(conn, index, in, inStructSize, out, &outStructSize);
}

kern_return_t SMCReadKey(UInt32Char_t key, SMCVal_t *val) {
    kern_return_t result;
    SMCKeyData_t in, out;
    memset(&in, 0, sizeof(SMCKeyData_t));
    memset(&out, 0, sizeof(SMCKeyData_t));
    memset(val, 0, sizeof(SMCVal_t));

    in.key = _strtoul(key, 4, 16);
    snprintf(val->key, 5, "%s", key);
    in.data8 = SMC_CMD_READ_KEYINFO;
    result = SMCCall(KERNEL_INDEX_SMC, &in, &out);
    if (result != kIOReturnSuccess) return result;

    val->dataSize = out.keyInfo.dataSize;
    _ultostr(val->dataType, out.keyInfo.dataType);
    in.keyInfo.dataSize = val->dataSize;
    in.data8 = SMC_CMD_READ_BYTES;
    result = SMCCall(KERNEL_INDEX_SMC, &in, &out);
    if (result != kIOReturnSuccess) return result;

    memcpy(val->bytes, out.bytes, sizeof(out.bytes));
    return kIOReturnSuccess;
}

kern_return_t SMCReadKeyAtIndex(int index, UInt32Char_t keyOut) {
    kern_return_t result;
    SMCKeyData_t in, out;
    memset(&in, 0, sizeof(SMCKeyData_t));
    memset(&out, 0, sizeof(SMCKeyData_t));
    in.data8 = SMC_CMD_READ_INDEX;
    in.data32 = index;
    result = SMCCall(KERNEL_INDEX_SMC, &in, &out);
    if (result != kIOReturnSuccess) return result;
    _ultostr(keyOut, out.key);
    return kIOReturnSuccess;
}

int SMCKeyCount(void) {
    SMCVal_t v;
    UInt32Char_t k; snprintf(k, 5, "#KEY");
    if (SMCReadKey(k, &v) != kIOReturnSuccess) return 0;
    /* #KEY genelde ui32 big-endian */
    unsigned char *b = (unsigned char *)v.bytes;
    return (b[0]<<24)|(b[1]<<16)|(b[2]<<8)|b[3];
}

static int hexidx(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return c - 'a' + 10;
    if (c >= 'A' && c <= 'F') return c - 'A' + 10;
    return 0;
}

/* HWMonitorSMC2 decodeNumericValue mantigi */
double decode(SMCVal_t v) {
    const char *t = v.dataType;
    UInt32 n = v.dataSize;
    unsigned char *b = (unsigned char *)v.bytes;
    if (n == 0) return 0.0;

    /* flt : float32 (little-endian) */
    if (strncmp(t, "flt ", 4) == 0 && n == 4) {
        float f; memcpy(&f, b, 4); return (double)f;
    }
    /* ui8/si8/ui16/si16/ui32/si32 (big-endian) */
    if ((t[0]=='u'||t[0]=='s') && t[1]=='i') {
        int signd = (t[0]=='s');
        if (t[2]=='8' && n==1) {
            unsigned char e=b[0];
            if (signd && (e & 0x80)) { e &= 0x7F; return -(double)e; }
            return (double)e;
        }
        if (t[2]=='1' && t[3]=='6' && n==2) {
            unsigned int e = (b[0]<<8)|b[1];
            if (signd && (e & 0x8000)) { e &= 0x7FFF; return -(double)e; }
            return (double)e;
        }
        if (t[2]=='3' && t[3]=='2' && n==4) {
            unsigned int e = (b[0]<<24)|(b[1]<<16)|(b[2]<<8)|b[3];
            if (signd && (e & 0x80000000U)) { e &= 0x7FFFFFFF; return -(double)e; }
            return (double)e;
        }
    }
    /* spXY / fpXY : sabit nokta (2 bayt, big-endian) */
    if ((t[0]=='f'||t[0]=='s') && t[1]=='p' && n==2) {
        int i = hexidx(t[2]), f = hexidx(t[3]);
        int signd = (t[0]=='s');
        if ((i+f) != (signd ? 15 : 16)) return 0.0;
        unsigned int e = (b[0]<<8)|b[1];
        int minus = signd && (e & 0x8000);
        if (minus) e &= 0x7FFF;
        double val = ((double)e / (double)(1 << f));
        return minus ? -val : val;
    }
    /* fpe2 : (b0<<6)+(b1>>2) */
    if (strncmp(t, "fpe2", 4) == 0 && n==2) {
        return (double)((b[0]<<6) + (b[1]>>2));
    }
    return 0.0;
}

int main(int argc, const char *argv[]) {
    if (argc < 2) { fprintf(stderr, "kullanim: smc_read KEY [KEY...]  |  smc_read list\n"); return 1; }
    if (SMCOpen() != kIOReturnSuccess) { fprintf(stderr, "SMC acilamadi\n"); return 1; }

    /* LISTE modu: tum anahtarlari dok (ad, tip, deger) */
    if (strcmp(argv[1], "list") == 0) {
        int count = SMCKeyCount();
        fprintf(stderr, "toplam anahtar: %d\n", count);
        for (int idx = 0; idx < count; idx++) {
            UInt32Char_t key;
            if (SMCReadKeyAtIndex(idx, key) != kIOReturnSuccess) continue;
            SMCVal_t val;
            if (SMCReadKey(key, &val) == kIOReturnSuccess && val.dataSize > 0) {
                printf("%s [%s] = %.2f\n", key, val.dataType, decode(val));
            }
        }
        SMCClose();
        return 0;
    }
    for (int i = 1; i < argc; i++) {
        UInt32Char_t key;
        snprintf(key, 5, "%s", argv[i]);
        SMCVal_t val;
        kern_return_t r = SMCReadKey(key, &val);
        if (r == kIOReturnSuccess && val.dataSize > 0) {
            printf("%s=%.2f\n", argv[i], decode(val));
        } else {
            printf("%s=NA\n", argv[i]);
        }
    }
    SMCClose();
    return 0;
}
