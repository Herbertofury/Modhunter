"""Short-lived Windows archive lease; no hashing or persistent permissions."""
from contextlib import contextmanager
import os
from pathlib import Path


@contextmanager
def archive_read_lock(path):
    """Hold Windows deny-write/delete sharing through verification and consumption.

    Non-Windows callers retain their explicit metadata/rehash checks. This is
    not advertised as immutable-handle protection on those platforms.
    """
    if os.name != "nt":
        yield "metadata-only"
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # GENERIC_READ, FILE_SHARE_READ (no write/delete), OPEN_EXISTING.
    handle = create(str(Path(path)), 0x80000000, 0x00000001, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        error = ctypes.get_last_error()
        raise OSError(error, "Archive cannot be held read-only for verified installation; close other writers and review again")
    try:
        yield "windows-deny-write-delete"
    finally:
        if not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())
