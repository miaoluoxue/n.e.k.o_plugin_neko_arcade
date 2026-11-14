"""Local secret protection for plugin configuration."""

from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from typing import Tuple


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
    ]


def _blob(data: bytes) -> Tuple[_DataBlob, ctypes.Array]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(
        len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
    ), buffer


def _dpapi(value: str, *, decrypt: bool) -> str:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    source, source_buffer = _blob(
        base64.b64decode(value) if decrypt else value.encode("utf-8")
    )
    output = _DataBlob()
    flags = 0x1  # CRYPTPROTECT_UI_FORBIDDEN
    if decrypt:
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None, flags,
            ctypes.byref(output),
        )
    else:
        ok = crypt32.CryptProtectData(
            ctypes.byref(source), None, None, None, None, flags,
            ctypes.byref(output),
        )
    if not ok:
        raise OSError(ctypes.get_last_error(), "DPAPI operation failed")
    try:
        raw = ctypes.string_at(output.pbData, output.cbData)
    finally:
        kernel32.LocalFree(ctypes.cast(output.pbData, ctypes.c_void_p))
    _ = source_buffer
    return raw.decode("utf-8") if decrypt else base64.b64encode(raw).decode("ascii")


def protect_secret(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if os.name == "nt":
        try:
            return "dpapi:" + _dpapi(value, decrypt=False)
        except OSError:
            pass
    return "b64:" + base64.b64encode(value.encode("utf-8")).decode("ascii")


def unprotect_secret(value: str) -> str:
    value = str(value or "")
    if value.startswith("dpapi:"):
        return _dpapi(value[len("dpapi:"):], decrypt=True)
    if value.startswith("b64:"):
        return base64.b64decode(value[len("b64:"):]).decode("utf-8")
    return value
