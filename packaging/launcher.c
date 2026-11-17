// Foni.app's main program. It runs the Python inside the app bundle (embedded), so macOS sees
// one process called "Foni": that's the name in Activity Monitor and in Privacy & Security.
//
//   Foni.app/Contents/Resources/python   a private copy of Python with Foni's packages
//   Foni.app/Contents/Resources/app/foni Foni's own code
//
// Output goes to ~/.foni/foni.log.
#define PY_SSIZE_T_CLEAN
#include <Python.h>

#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

int main(int argc, char **argv) {
    const char *home = getenv("HOME");
    if (!home) return 1;

    char exe[PATH_MAX], real[PATH_MAX];
    uint32_t size = sizeof exe;
    if (_NSGetExecutablePath(exe, &size) != 0 || !realpath(exe, real)) return 1;
    char *p = strstr(real, "/Contents/MacOS/");
    if (!p) return 1;
    *p = 0;  // real is now the .app folder

    char res[PATH_MAX], pyhome[PATH_MAX], pyexe[PATH_MAX], appdir[PATH_MAX];
    snprintf(res, sizeof res, "%s/Contents/Resources", real);
    snprintf(pyhome, sizeof pyhome, "%s/python", res);
    snprintf(pyexe, sizeof pyexe, "%s/python/bin/python3", res);
    snprintf(appdir, sizeof appdir, "%s/app", res);

    char logdir[PATH_MAX], logfile[PATH_MAX];
    snprintf(logdir, sizeof logdir, "%s/.foni", home);
    snprintf(logfile, sizeof logfile, "%s/.foni/foni.log", home);
    mkdir(logdir, 0755);
    freopen(logfile, "a", stdout);
    freopen(logfile, "a", stderr);
    setvbuf(stdout, NULL, _IOLBF, 0);

    setenv("PATH", "/usr/bin:/bin:/usr/sbin:/sbin", 1);
    setenv("PYTHONUNBUFFERED", "1", 1);
    setenv("PYTHONDONTWRITEBYTECODE", "1", 1);  // never write into the signed app
    unsetenv("PYTHONHOME");
    unsetenv("PYTHONPATH");

    PyStatus status;
    PyPreConfig preconfig;
    PyPreConfig_InitIsolatedConfig(&preconfig);
    preconfig.utf8_mode = 1;  // text is UTF-8 everywhere (Greek, emoji), whatever the Mac's locale
    status = Py_PreInitialize(&preconfig);
    if (PyStatus_Exception(status)) Py_ExitStatusException(status);

    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);  // ignore any Python setup elsewhere on this Mac
    status = PyConfig_SetBytesString(&config, &config.home, pyhome);
    if (PyStatus_Exception(status)) goto fail;
    status = PyConfig_SetBytesString(&config, &config.program_name, pyexe);
    if (PyStatus_Exception(status)) goto fail;
    status = PyConfig_SetBytesString(&config, &config.executable, pyexe);
    if (PyStatus_Exception(status)) goto fail;
    // Put Foni's code in front of Python's own paths, then run it like `python -m foni`.
    char command[PATH_MAX + 200];
    snprintf(command, sizeof command,
             "import sys, runpy\nsys.path.insert(0, %c%s%c)\nrunpy.run_module('foni', run_name='__main__')\n",
             '"', appdir, '"');
    status = PyConfig_SetBytesString(&config, &config.run_command, command);
    if (PyStatus_Exception(status)) goto fail;
    config.site_import = 1;
    config.write_bytecode = 0;
    config.buffered_stdio = 0;  // the log fills in as things happen

    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) goto fail;
    PyConfig_Clear(&config);
    return Py_RunMain();

fail:
    PyConfig_Clear(&config);
    Py_ExitStatusException(status);
}
