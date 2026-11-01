import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

def enum_windows():
    results = []
    def callback(hwnd, extra):
        if user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            if length > 0:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value.strip()
                if title:
                    rect = wintypes.RECT()
                    user32.GetWindowRect(hwnd, ctypes.byref(rect))
                    w = rect.right - rect.left
                    h = rect.bottom - rect.top
                    if w > 50 and h > 50:
                        results.append((hwnd, title, (rect.left, rect.top, rect.right, rect.bottom)))
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return results

windows = enum_windows()
print(f"Found {len(windows)} visible top-level windows:")
for h, t, r in windows:
    print(f"  [{h}] '{t}' rect={r}")

