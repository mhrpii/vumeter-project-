/*
 * disk_read.c - macOS NVMe disk sicaklik okuyucu (sudo GEREKMEZ)
 *
 * Yontem (HWMonitorSMC2 HWSMART.swift):
 *   1) IOServiceMatching("IONVMeBlockStorageDevice") ile NVMe bloklari bul
 *   2) Her biri icin kIONVMeSMARTUserClient plugin ac
 *   3) SMARTReadData -> nvme_smart_log.temperature (Kelvin) -> Celsius
 *
 * Cikti:  MODEL|TEMP|nvme
 * Derleme: clang -O2 -o disk_read disk_read.c -framework IOKit -framework CoreFoundation
 */
#include <stdio.h>
#include <string.h>
#include <IOKit/IOKitLib.h>
#include <IOKit/IOCFPlugIn.h>
#include <IOKit/storage/IOStorageDeviceCharacteristics.h>
#include <CoreFoundation/CoreFoundation.h>

#define kIONVMeSMARTUserClientTypeID \
    CFUUIDGetConstantUUIDWithBytes(NULL, 0xAA,0x0F,0xA6,0xF9,0xC2,0xD6,0x45,0x7F, \
                                   0xB1,0x0B,0x59,0xA1,0x32,0x53,0x29,0x2F)
#define kIONVMeSMARTInterfaceID \
    CFUUIDGetConstantUUIDWithBytes(NULL, 0xCC,0xD1,0xDB,0x19,0xFD,0x9A,0x4D,0xAF, \
                                   0xBF,0x95,0x12,0x45,0x4B,0x23,0x0A,0xB6)

struct nvme_smart_log {
    unsigned char critical_warning;
    unsigned char temperature[2];
    unsigned char avail_spare;
    unsigned char spare_thresh;
    unsigned char percent_used;
    unsigned char rsvd6[26];
    unsigned char data_units_read[16];
    unsigned char data_units_written[16];
    unsigned char host_reads[16];
    unsigned char host_writes[16];
    unsigned char ctrl_busy_time[16];
    unsigned int  power_cycles[4];
    unsigned int  power_on_hours[4];
    unsigned int  unsafe_shutdowns[4];
    unsigned int  media_errors[4];
    unsigned int  num_err_log_entries[4];
    unsigned int  warning_temp_time;
    unsigned int  critical_comp_time;
    unsigned short temp_sensor[8];
    unsigned int  thm[4];
    unsigned char rsvd232[280];
};

typedef struct IONVMeSMARTInterface {
    IUNKNOWN_C_GUTS;
    UInt16 version;
    UInt16 revision;
    IOReturn (*SMARTReadData)(void *interface, struct nvme_smart_log *data);
} IONVMeSMARTInterface;

/* ---- ATA SMART (SATA diskler) ---- */
#define kIOATASMARTUserClientTypeID \
    CFUUIDGetConstantUUIDWithBytes(NULL, 0x24,0x51,0x4B,0x7A,0x28,0x04,0x11,0xD6, \
                                   0x8A,0x02,0x00,0x30,0x65,0x70,0x48,0x66)
#define kIOATASMARTInterfaceID \
    CFUUIDGetConstantUUIDWithBytes(NULL, 0x08,0xAB,0xE2,0x1C,0x20,0xD4,0x11,0xD6, \
                                   0x8D,0xF6,0x00,0x03,0x93,0x5A,0x76,0xB2)

/* ATA SMART data: 512 bayt. vendorSpecific offset 2, attribute'lar 12'ser bayt. */
typedef struct ATASMARTData { unsigned char data[512]; } ATASMARTData;

typedef struct IOATASMARTInterface {
    IUNKNOWN_C_GUTS;
    UInt16 version;
    UInt16 revision;
    IOReturn (*SMARTEnableDisableOperations)(void *interface, Boolean enable);
    IOReturn (*SMARTEnableDisableAutosave)(void *interface, Boolean enable);
    IOReturn (*SMARTReturnStatus)(void *interface, Boolean *exceededCondition);
    IOReturn (*SMARTExecuteOffLineImmediate)(void *interface, Boolean extendedTest);
    IOReturn (*SMARTReadData)(void *interface, ATASMARTData *data);
    IOReturn (*SMARTValidateReadData)(void *interface, ATASMARTData *data);
    /* gerisi lazim degil */
} IOATASMARTInterface;

static void get_model(io_service_t service, char *out, size_t outlen) {
    out[0] = '\0';
    CFTypeRef dc = IORegistryEntrySearchCFProperty(
        service, kIOServicePlane,
        CFSTR(kIOPropertyDeviceCharacteristicsKey),
        kCFAllocatorDefault,
        kIORegistryIterateRecursively | kIORegistryIterateParents);
    if (dc && CFGetTypeID(dc) == CFDictionaryGetTypeID()) {
        CFStringRef name = CFDictionaryGetValue((CFDictionaryRef)dc, CFSTR("Product Name"));
        if (name && CFGetTypeID(name) == CFStringGetTypeID())
            CFStringGetCString(name, out, outlen, kCFStringEncodingUTF8);
    }
    if (dc) CFRelease(dc);
    /* bosluk kirp */
    char *p = out; while (*p == ' ') p++;
    if (p != out) memmove(out, p, strlen(p)+1);
    size_t l = strlen(out);
    while (l > 0 && out[l-1] == ' ') out[--l] = '\0';
    if (out[0] == '\0') snprintf(out, outlen, "NVMe");
}

/* object'ten kIOBlockStorageDeviceClass parent'ina cikip SMART ac */
static int read_nvme_from(io_service_t object, int *tempC) {
    /* NVMe SMART Capable mi? (dogrudan bu dugumde ya da recursive) */
    CFTypeRef cap = IORegistryEntrySearchCFProperty(
        object, kIOServicePlane, CFSTR("NVMe SMART Capable"),
        kCFAllocatorDefault, kIORegistryIterateRecursively | kIORegistryIterateParents);
    int capable = 0;
    if (cap) {
        if (CFGetTypeID(cap) == CFBooleanGetTypeID())
            capable = CFBooleanGetValue((CFBooleanRef)cap);
        CFRelease(cap);
    }
    if (!capable) return 0;

    IOCFPlugInInterface **plugin = NULL;
    SInt32 score = 0;
    kern_return_t kr = IOCreatePlugInInterfaceForService(
        object, kIONVMeSMARTUserClientTypeID, kIOCFPlugInInterfaceID,
        &plugin, &score);
    if (kr != kIOReturnSuccess || plugin == NULL) {
        return 0;
    }

    IONVMeSMARTInterface **smart = NULL;
    HRESULT hr = (*plugin)->QueryInterface(
        plugin, CFUUIDGetUUIDBytes(kIONVMeSMARTInterfaceID), (LPVOID *)&smart);
    if (hr != 0 || smart == NULL) {
        IODestroyPlugInInterface(plugin);
        return 0;
    }

    struct nvme_smart_log log;
    memset(&log, 0, sizeof(log));
    IOReturn r = (*smart)->SMARTReadData(smart, &log);
    /* once interface'i birak, sonra plugin'i yok et (HWMonitor sirasi) */
    (*smart)->Release(smart);
    IODestroyPlugInInterface(plugin);
    if (r != kIOReturnSuccess) return 0;

    unsigned int kelvin = log.temperature[0] | (log.temperature[1] << 8);
    if (kelvin == 0) return 0;
    *tempC = (int)kelvin - 273;
    return 1;
}

/* SATA/ATA diskten sicaklik oku (attribute 0xC2 = 194) */
static int read_ata_temp(io_service_t object, int *tempC) {
    /* SMART capable mi? ("SMART Capable" = Yes) */
    CFTypeRef cap = IORegistryEntrySearchCFProperty(
        object, kIOServicePlane, CFSTR("SMART Capable"),
        kCFAllocatorDefault, kIORegistryIterateRecursively | kIORegistryIterateParents);
    int ata_capable = 0;
    if (cap) {
        if (CFGetTypeID(cap) == CFBooleanGetTypeID())
            ata_capable = CFBooleanGetValue((CFBooleanRef)cap);
        CFRelease(cap);
    }
    if (!ata_capable) return 0;

    IOCFPlugInInterface **plugin = NULL;
    SInt32 score = 0;
    if (IOCreatePlugInInterfaceForService(object, kIOATASMARTUserClientTypeID,
            kIOCFPlugInInterfaceID, &plugin, &score) != kIOReturnSuccess || !plugin)
        return 0;

    IOATASMARTInterface **smart = NULL;
    HRESULT hr = (*plugin)->QueryInterface(
        plugin, CFUUIDGetUUIDBytes(kIOATASMARTInterfaceID), (LPVOID *)&smart);
    if (hr != 0 || !smart) { IODestroyPlugInInterface(plugin); return 0; }

    int found = 0;
    if ((*smart)->SMARTEnableDisableOperations(smart, true) == kIOReturnSuccess) {
        ATASMARTData d;
        memset(&d, 0, sizeof(d));
        IOReturn r = (*smart)->SMARTReadData(smart, &d);
        if (r == kIOReturnSuccess) {
            /* vendorSpecific: offset 2'den itibaren 12'ser bayt attribute.
               ID @0, current @3, raw @5. ID 194 (0xC2) = temperature. */
            for (int off = 2; off + 12 <= 362; off += 12) {
                unsigned char id = d.data[off];
                if (id == 194 || id == 190) {   /* 194=temp, 190=airflow temp */
                    *tempC = d.data[off + 5];   /* rawValue[0] */
                    if (*tempC > 0 && *tempC < 120) { found = 1; break; }
                }
            }
        }
        (*smart)->SMARTEnableDisableOperations(smart, false);
    }
    (*smart)->Release(smart);
    IODestroyPlugInInterface(plugin);
    return found;
}

int main(void) {
    /* IOMedia'dan basla: her disk medyasi -> parent block storage -> SMART */
    io_iterator_t iter = 0;
    CFMutableDictionaryRef match = IOServiceMatching("IONVMeBlockStorageDevice");
    if (IOServiceGetMatchingServices(kIOMasterPortDefault, match, &iter) != KERN_SUCCESS)
        return 0;
    io_service_t service;
    while ((service = IOIteratorNext(iter)) != 0) {
        char model[128];
        get_model(service, model, sizeof(model));
        int t = 0;
        if (read_nvme_from(service, &t))
            printf("%s|%d|nvme\n", model, t);
        IOObjectRelease(service);
    }
    IOObjectRelease(iter);

    /* SATA/AHCI diskler: IOAHCIBlockStorageDevice */
    iter = 0;
    match = IOServiceMatching("IOAHCIBlockStorageDevice");
    if (IOServiceGetMatchingServices(kIOMasterPortDefault, match, &iter) == KERN_SUCCESS) {
        io_service_t s2;
        while ((s2 = IOIteratorNext(iter)) != 0) {
            char model[128];
            get_model(s2, model, sizeof(model));
            int t = 0;
            if (read_ata_temp(s2, &t))
                printf("%s|%d|sata\n", model, t);
            IOObjectRelease(s2);
        }
        IOObjectRelease(iter);
    }
    return 0;
}
