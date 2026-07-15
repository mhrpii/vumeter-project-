/*
 * gpu_read.c - macOS GPU sensor okuyucu (HWMonitorSMC2 Graphics.swift mantigi)
 *
 * IOPCIDevice'lari tarar, GPU olani bulur, IOReportPerformanceStatistics /
 * PerformanceStatistics sozlugunu ceker ve icindeki anahtarlari basar:
 *   temp=..   power=..  util=..  coreclock=..  memclock=..  fan=..  vramused=..
 *
 * Okunamayan alan yazilmaz. Hic GPU/istatistik yoksa cikis bos olur.
 *
 * Derleme:
 *   clang -O2 -o gpu_read gpu_read.c -framework IOKit -framework CoreFoundation
 */
#include <stdio.h>
#include <string.h>
#include <IOKit/IOKitLib.h>
#include <CoreFoundation/CoreFoundation.h>

/* PerformanceStatistics anahtar adi -> cikti etiketi */
typedef struct { const char *key; const char *label; double divide; } KeyMap;

static KeyMap KEYS[] = {
    { "Temperature(C)",        "temp",      1.0 },
    { "Total Power(W)",        "power",     1.0 },
    { "Device Utilization %",  "util",      1.0 },
    { "GPU Activity(%)",       "util2",     1.0 },
    { "Core Clock(MHz)",       "coreclock", 1.0 },
    { "Memory Clock(MHz)",     "memclock",  1.0 },
    { "Fan Speed(RPM)",        "fan",       1.0 },
    { "vramUsedBytes",         "vramused",  1048576.0 },  /* -> MB */
    { "vramFreeBytes",         "vramfree",  1048576.0 },
    { "inUseVidMemoryBytes",   "vidused",   1048576.0 },
};
static const int NKEYS = sizeof(KEYS)/sizeof(KEYS[0]);

/* Bir CFNumber/CFBoolean'i double'a cevir */
static int cf_to_double(CFTypeRef v, double *out) {
    if (v == NULL) return 0;
    CFTypeID t = CFGetTypeID(v);
    if (t == CFNumberGetTypeID()) {
        return CFNumberGetValue((CFNumberRef)v, kCFNumberDoubleType, out);
    }
    return 0;
}

/* PerformanceStatistics dict icinden bilinen anahtarlari bas */
static int dump_stats(CFDictionaryRef stats) {
    int found = 0;
    for (int i = 0; i < NKEYS; i++) {
        CFStringRef k = CFStringCreateWithCString(NULL, KEYS[i].key, kCFStringEncodingUTF8);
        CFTypeRef v = CFDictionaryGetValue(stats, k);
        CFRelease(k);
        double d;
        if (cf_to_double(v, &d)) {
            printf("%s=%.2f\n", KEYS[i].label, d / KEYS[i].divide);
            found = 1;
        }
    }
    return found;
}

/* Bir IOPCIDevice (ve alt agaci) icinde PerformanceStatistics ara */
static int try_service(io_service_t service) {
    CFTypeRef stats = IORegistryEntrySearchCFProperty(
        service, kIOServicePlane,
        CFSTR("PerformanceStatistics"),
        kCFAllocatorDefault,
        kIORegistryIterateRecursively);
    if (stats == NULL) {
        stats = IORegistryEntrySearchCFProperty(
            service, kIOServicePlane,
            CFSTR("IOPerformanceStatistics"),
            kCFAllocatorDefault,
            kIORegistryIterateRecursively);
    }
    if (stats && CFGetTypeID(stats) == CFDictionaryGetTypeID()) {
        int f = dump_stats((CFDictionaryRef)stats);
        CFRelease(stats);
        return f;
    }
    if (stats) CFRelease(stats);
    return 0;
}

int main(void) {
    io_iterator_t iter = 0;
    CFMutableDictionaryRef match = IOServiceMatching("IOPCIDevice");
    kern_return_t ret = IOServiceGetMatchingServices(kIOMasterPortDefault, match, &iter);
    if (ret != KERN_SUCCESS || iter == 0) {
        return 0;
    }
    io_service_t service;
    int any = 0;
    while ((service = IOIteratorNext(iter)) != 0) {
        /* GPU'yu ayirt etmeye gerek yok: PerformanceStatistics sadece GPU'da olur.
           Ilk anlamli istatistigi bulunca bas ve dur. */
        if (try_service(service)) {
            any = 1;
            IOObjectRelease(service);
            break;
        }
        IOObjectRelease(service);
    }
    IOObjectRelease(iter);
    return any ? 0 : 0;
}
