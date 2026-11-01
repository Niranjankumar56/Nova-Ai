import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32

hdesk = user32.OpenInputDesktop(0, False, 0x01ff)
print("OpenInputDesktop handle:", hdesk)
if hdesk:
    res = user32.SetThreadDesktop(hdesk)
    print("SetThreadDesktop res:", res)

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
                    results.append((hwnd, title, (rect.left, rect.top, rect.right, rect.bottom)))
        return True
    
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    user32.EnumDesktopWindows(hdesk, WNDENUMPROC(callback), 0)
    return results

windows = enum_windows()
print(f"Found {len(windows)} visible desktop windows:")
for h, t, r in windows[:10]:
    print(f"  [{h}] '{t}' rect={r}")

