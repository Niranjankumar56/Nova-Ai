import winreg

for root_name, root in [("HKLM", winreg.HKEY_LOCAL_MACHINE), ("HKCU", winreg.HKEY_CURRENT_USER)]:
    for flag_name, flag in [("KEY_READ", winreg.KEY_READ), ("WOW64_64", winreg.KEY_READ | winreg.KEY_WOW64_64KEY)]:
        try:
            with winreg.OpenKey(root, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths", 0, flag) as key:
                count = winreg.QueryInfoKey(key)[0]
                print(f"{root_name} ({flag_name}): SUCCESS, {count} apps registered.")
        except Exception as e:
            print(f"{root_name} ({flag_name}): {e}")

