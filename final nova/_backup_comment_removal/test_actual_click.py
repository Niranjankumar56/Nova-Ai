import os
import sys

sys.path.insert(0, os.path.abspath("."))
from actions.computer_control import computer_control

print("--- Testing screen_qa ---")
res_qa = computer_control({"action": "screen_qa", "query": "What buttons are clickable?"})
print("screen_qa output:\n", res_qa)

print("\n--- Testing read_screen ---")
res_read = computer_control({"action": "read_screen"})
print("read_screen output:\n", res_read)

print("\n--- Testing hover over Minimize ---")
res_hover = computer_control({"action": "hover", "description": "Minimize"})
print("hover output:\n", res_hover)

print("\n--- Testing keyboard shortcuts ---")
res_hk = computer_control({"action": "hotkey", "keys": "ctrl+c"})
print("hotkey output:\n", res_hk)

res_press = computer_control({"action": "press", "key": "esc"})
print("press output:\n", res_press)

print("\n--- Testing scroll ---")
res_scroll = computer_control({"action": "scroll", "direction": "down", "amount": 2})
print("scroll output:\n", res_scroll)

print("\n--- Testing missing target rejection ---")
res_miss = computer_control({"action": "click", "description": "NonExistentButton123456789"})
print("missing target output:\n", res_miss)
