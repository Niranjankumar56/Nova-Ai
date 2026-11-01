import ctypes
from ctypes import wintypes

user32 = ctypes.windll.user32
hwnd = user32.GetForegroundWindow()
length = user32.GetWindowTextLengthW(hwnd)
buf = ctypes.create_unicode_buffer(length + 1)
user32.GetWindowTextW(hwnd, buf, length + 1)
class_buf = ctypes.create_unicode_buffer(256)
user32.GetClassNameW(hwnd, class_buf, 256)
print(f"HWND: {hwnd}, Title: '{buf.value}', Class: '{class_buf.value}'")

