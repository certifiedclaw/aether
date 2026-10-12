"""core/known_folders.py -- real user folder locations on Windows.

Path.home() / "Desktop" is wrong whenever Windows redirects the folder (OneDrive
"Known Folder Move" is the default on many installs: the real Desktop is then
C:\\Users\\<you>\\OneDrive\\Desktop and the plain one is empty or missing). The
Shell API returns the path Explorer itself uses. Returns None off Windows or on
any failure so callers keep their existing fallback.
"""
from __future__ import annotations

import platform
import uuid
from pathlib import Path

_GUIDS = {
    "desktop":   "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures":  "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music":     "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos":    "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
}


def known_folder(name: str) -> Path | None:
    """Return the Windows Known Folder for name, or None (non-Windows/failure)."""
    if platform.system() != "Windows":
        return None
    guid = _GUIDS.get(name.lower())
    if not guid:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _GUID(ctypes.Structure):
            _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                        ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

        u = uuid.UUID(guid)
        g = _GUID(u.time_low, u.time_mid, u.time_hi_version,
                  (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        fn = ctypes.windll.shell32.SHGetKnownFolderPath
        fn.argtypes = [ctypes.POINTER(_GUID), wintypes.DWORD, wintypes.HANDLE,
                       ctypes.POINTER(ctypes.c_wchar_p)]
        if fn(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0 or not out.value:
            return None
        path = Path(out.value)
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path if path.exists() else None
    except Exception:
        return None


def desktop_dir() -> Path:
    """Best Desktop path on any OS (Known Folder > XDG > ~/Desktop)."""
    import os
    kf = known_folder("desktop")
    if kf:
        return kf
    xdg = os.environ.get("XDG_DESKTOP_DIR", "")
    if platform.system() == "Linux" and xdg and Path(xdg).exists():
        return Path(xdg)
    return Path.home() / "Desktop"
