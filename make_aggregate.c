/*
 * make_aggregate.c - VU Meter icin otomatik Aggregate Device olusturucu.
 * "VU-ScarlettLoop" varsa VAR der; yoksa Scarlett'i alt aygit yapip olusturur.
 * Derleme: clang -o make_aggregate make_aggregate.c -framework CoreAudio -framework CoreFoundation
 */
#include <CoreAudio/CoreAudio.h>
#include <CoreFoundation/CoreFoundation.h>
#include <stdio.h>
#include <string.h>

#define AGG_NAME "VU-ScarlettLoop"
#define AGG_UID  "com.vumeter.scarlettloop"

static int get_device_name(AudioDeviceID dev, char *buf, size_t buflen) {
    CFStringRef name = NULL;
    UInt32 size = sizeof(name);
    AudioObjectPropertyAddress addr = {
        kAudioObjectPropertyName,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    if (AudioObjectGetPropertyData(dev, &addr, 0, NULL, &size, &name) != noErr || !name)
        return 0;
    Boolean ok = CFStringGetCString(name, buf, buflen, kCFStringEncodingUTF8);
    CFRelease(name);
    return ok ? 1 : 0;
}

static CFStringRef get_device_uid(AudioDeviceID dev) {
    CFStringRef uid = NULL;
    UInt32 size = sizeof(uid);
    AudioObjectPropertyAddress addr = {
        kAudioDevicePropertyDeviceUID,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    if (AudioObjectGetPropertyData(dev, &addr, 0, NULL, &size, &uid) != noErr)
        return NULL;
    return uid;
}

int main(void) {
    AudioObjectPropertyAddress addr = {
        kAudioHardwarePropertyDevices,
        kAudioObjectPropertyScopeGlobal,
        kAudioObjectPropertyElementMain
    };
    UInt32 size = 0;
    if (AudioObjectGetPropertyDataSize(kAudioObjectSystemObject, &addr, 0, NULL, &size) != noErr) {
        printf("HATA_LISTE\n"); return 2;
    }
    int count = size / sizeof(AudioDeviceID);
    AudioDeviceID devs[128];
    if (count > 128) count = 128;
    size = count * sizeof(AudioDeviceID);
    if (AudioObjectGetPropertyData(kAudioObjectSystemObject, &addr, 0, NULL, &size, devs) != noErr) {
        printf("HATA_LISTE\n"); return 2;
    }

    CFStringRef scarlett_uid = NULL;
    char name[256];
    for (int i = 0; i < count; i++) {
        if (!get_device_name(devs[i], name, sizeof(name))) continue;
        if (strcmp(name, AGG_NAME) == 0) {
            printf("VAR\n");
            if (scarlett_uid) CFRelease(scarlett_uid);
            return 0;
        }
        if (!scarlett_uid && strstr(name, "Scarlett") != NULL) {
            CFStringRef uid = get_device_uid(devs[i]);
            if (uid) {
                char ub[256];
                if (CFStringGetCString(uid, ub, sizeof(ub), kCFStringEncodingUTF8) &&
                    strstr(ub, "com.vumeter") == NULL) {
                    scarlett_uid = uid;
                } else {
                    CFRelease(uid);
                }
            }
        }
    }

    if (!scarlett_uid) {
        printf("SCARLETT_YOK\n");
        return 1;
    }

    CFMutableDictionaryRef desc = CFDictionaryCreateMutable(
        kCFAllocatorDefault, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceNameKey), CFSTR(AGG_NAME));
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceUIDKey), CFSTR(AGG_UID));

    CFMutableDictionaryRef sub = CFDictionaryCreateMutable(
        kCFAllocatorDefault, 0,
        &kCFTypeDictionaryKeyCallBacks, &kCFTypeDictionaryValueCallBacks);
    CFDictionarySetValue(sub, CFSTR(kAudioSubDeviceUIDKey), scarlett_uid);
    const void *subs[1] = { sub };
    CFArrayRef subArr = CFArrayCreate(kCFAllocatorDefault, subs, 1, &kCFTypeArrayCallBacks);
    CFDictionarySetValue(desc, CFSTR(kAudioAggregateDeviceSubDeviceListKey), subArr);

    AudioDeviceID aggID = 0;
    OSStatus st = AudioHardwareCreateAggregateDevice(desc, &aggID);

    CFRelease(subArr); CFRelease(sub); CFRelease(desc); CFRelease(scarlett_uid);

    if (st != noErr || aggID == 0) {
        printf("HATA_OLUSTURMA %d\n", (int)st);
        return 3;
    }
    printf("OLUSTURULDU\n");
    return 0;
}
