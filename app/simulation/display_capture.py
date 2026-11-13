"""The primary display's picture as shown (DXGI Desktop Duplication), for when a GDI screenshot comes out black.

NucBox K10 (Intel UHD, Vulkan), 2026-11-13: RDR2 in exclusive full screen, and in a window covering the whole
screen, showed its menu on the screen while every GDI screenshot was black (the game's frames go to the display
without passing the desktop GDI reads). Desktop Duplication reads what the display shows.
"""
import ctypes
from ctypes import wintypes
from typing import Optional, Tuple

BGRA = 87                           # DXGI_FORMAT_B8G8R8A8_UNORM
WAIT_TIMEOUT = 0x887A0027 - (1 << 32)


class _GUID(ctypes.Structure):
    _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    h = text.replace("-", "")
    return _GUID(int(h[:8], 16), int(h[8:12], 16), int(h[12:16], 16),
                 (ctypes.c_ubyte * 8)(*bytes.fromhex(h[16:])))


IID_DXGI_DEVICE = _guid("54ec77fa-1377-44e6-8c32-88fd5f44c84c")
IID_DXGI_OUTPUT1 = _guid("00cddea8-939b-4b83-a340-a685226666cc")
IID_TEXTURE2D = _guid("6f15aaf2-d208-4e89-9ab4-489535d34f9c")


class _TexDesc(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint) for n in ("Width", "Height", "MipLevels", "ArraySize", "Format", "Count",
                                              "Quality", "Usage", "BindFlags", "CPUAccessFlags", "MiscFlags")]


class _Mapped(ctypes.Structure):
    _fields_ = [("pData", ctypes.c_void_p), ("RowPitch", ctypes.c_uint), ("DepthPitch", ctypes.c_uint)]


def _call(obj, index, argtypes=(), *args, restype=ctypes.HRESULT):
    """Method `index` of a COM object's vtable (HRESULT failures raise OSError)."""
    vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents
    return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])(obj, *args)


def _release(*objs) -> None:
    for o in objs:
        if o:
            _call(o, 2, restype=wintypes.ULONG)


def capture(tries: int = 4) -> Optional[Tuple[int, int, bytes]]:
    """(width, height, top-down BGRA rows) of the primary display, or None when it cannot be read."""
    d3d11 = ctypes.WinDLL("d3d11")
    device, context = ctypes.c_void_p(), ctypes.c_void_p()
    dxgi_dev, adapter, output, output1, dup = (ctypes.c_void_p() for _ in range(5))
    level = ctypes.c_uint()
    P = ctypes.POINTER
    d3d11.D3D11CreateDevice.restype = ctypes.HRESULT
    d3d11.D3D11CreateDevice(None, 1, None, 0, None, 0, 7, ctypes.byref(device), ctypes.byref(level),
                            ctypes.byref(context))      # hardware driver, D3D11_SDK_VERSION
    try:
        _call(device, 0, (P(_GUID), P(ctypes.c_void_p)), ctypes.byref(IID_DXGI_DEVICE), ctypes.byref(dxgi_dev))
        _call(dxgi_dev, 7, (P(ctypes.c_void_p),), ctypes.byref(adapter))               # GetAdapter
        _call(adapter, 7, (ctypes.c_uint, P(ctypes.c_void_p)), 0, ctypes.byref(output))  # EnumOutputs(0)
        _call(output, 0, (P(_GUID), P(ctypes.c_void_p)), ctypes.byref(IID_DXGI_OUTPUT1), ctypes.byref(output1))
        _call(output1, 22, (ctypes.c_void_p, P(ctypes.c_void_p)), device, ctypes.byref(dup))   # DuplicateOutput
        for _ in range(tries):
            info, resource = (ctypes.c_ubyte * 64)(), ctypes.c_void_p()
            try:
                _call(dup, 8, (ctypes.c_uint, ctypes.c_void_p, P(ctypes.c_void_p)), 500, info,
                      ctypes.byref(resource))                                           # AcquireNextFrame
            except OSError as e:
                if getattr(e, "winerror", None) == WAIT_TIMEOUT:
                    continue
                raise
            try:
                if int.from_bytes(bytes(info[:8]), "little") == 0:  # LastPresentTime 0: no picture in this one yet
                    continue
                return _read(device, context, resource)
            finally:
                _release(resource)
                _call(dup, 14)                                                          # ReleaseFrame
        return None
    finally:
        _release(dup, output1, output, adapter, dxgi_dev, context, device)


def _read(device, context, resource) -> Optional[Tuple[int, int, bytes]]:
    P = ctypes.POINTER
    texture, staging = ctypes.c_void_p(), ctypes.c_void_p()
    _call(resource, 0, (P(_GUID), P(ctypes.c_void_p)), ctypes.byref(IID_TEXTURE2D), ctypes.byref(texture))
    try:
        desc = _TexDesc()
        _call(texture, 10, (P(_TexDesc),), ctypes.byref(desc), restype=None)            # GetDesc
        if desc.Format != BGRA:
            return None                     # an HDR display: not read here
        desc.MipLevels = desc.ArraySize = desc.Count = 1
        desc.Quality, desc.Usage, desc.BindFlags, desc.CPUAccessFlags, desc.MiscFlags = 0, 3, 0, 0x20000, 0
        _call(device, 5, (P(_TexDesc), ctypes.c_void_p, P(ctypes.c_void_p)), ctypes.byref(desc), None,
              ctypes.byref(staging))                                                    # CreateTexture2D (staging)
        _call(context, 47, (ctypes.c_void_p, ctypes.c_void_p), staging, texture, restype=None)  # CopyResource
        mapped = _Mapped()
        _call(context, 14, (ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_uint, P(_Mapped)), staging,
              0, 1, 0, ctypes.byref(mapped))                                            # Map, D3D11_MAP_READ
        try:
            w, h, pitch = desc.Width, desc.Height, mapped.RowPitch
            raw = ctypes.string_at(mapped.pData, pitch * h)
            rows = raw if pitch == w * 4 else b"".join(raw[y * pitch:y * pitch + w * 4] for y in range(h))
        finally:
            _call(context, 15, (ctypes.c_void_p, ctypes.c_uint), staging, 0, restype=None)    # Unmap
        return w, h, rows
    finally:
        _release(staging, texture)


def mostly_black(bgra: bytes, share: float = 0.95, step: int = 97) -> bool:
    """Nearly every sampled pixel black: a game the GDI screenshot could not see (a title bar may stay)."""
    pixels = len(bgra) // 4
    if not pixels:
        return True
    seen = dark = 0
    for i in range(0, pixels, step):
        b, g, r = bgra[i * 4], bgra[i * 4 + 1], bgra[i * 4 + 2]
        seen += 1
        dark += max(b, g, r) < 16
    return dark >= share * seen
