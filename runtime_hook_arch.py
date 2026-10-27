# runtime_hook_arch.py
# Форсируем platform.machine() -> 'AMD64' внутри x64-процесса,
# чтобы pywebview не искал ARM64-библиотеки на Windows on ARM / под эмуляцией.
import platform
import struct
import sys

if sys.platform == "win32" and struct.calcsize("P") * 8 == 64:
    _orig_machine = platform.machine

    def _patched_machine():
        return "AMD64"

    platform.machine = _patched_machine