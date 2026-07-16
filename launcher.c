/* launcher.c - VU Meter LCD baslatici (gercek binary, bundle ikonu icin) */
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <string.h>
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>

int main(int argc, char *argv[]) {
    /* kendi yolumuzu bul: .../Contents/MacOS/launcher */
    char exe[PATH_MAX];
    uint32_t sz = sizeof(exe);
    if (_NSGetExecutablePath(exe, &sz) != 0) return 1;

    /* .../Contents/MacOS -> .../Contents/Resources/app */
    char *macos_dir = dirname(exe);          /* .../Contents/MacOS */
    char contents[PATH_MAX];
    snprintf(contents, sizeof(contents), "%s/..", macos_dir);
    char appdir[PATH_MAX];
    snprintf(appdir, sizeof(appdir), "%s/../Resources/app", macos_dir);

    /* app klasorune gec */
    if (chdir(appdir) != 0) {
        fprintf(stderr, "app klasoru bulunamadi: %s\n", appdir);
        return 1;
    }

    /* eski kopyalari kapat */
    system("pkill -f native_proto_mac 2>/dev/null; sleep 1");

    /* python3'u bul ve calistir */
    execlp("/usr/bin/env", "env", "python3", "native_proto_mac.py", "Spektrum", (char*)NULL);

    /* execlp basarisizsa */
    fprintf(stderr, "python3 baslatilamadi\n");
    return 1;
}
