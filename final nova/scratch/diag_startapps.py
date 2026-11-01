import subprocess
import json

try:
    res = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", "Get-StartApps | Select-Object -Property Name, AppID | ConvertTo-Json -Compress"],
        capture_output=True,
        text=True,
        timeout=6,
    )
    if res.returncode == 0 and res.stdout.strip():
        data = json.loads(res.stdout.strip())
        print(f"Get-StartApps returned {len(data)} applications!")
        for app in data[:15]:
            print(f"  {app.get('Name')} -> {app.get('AppID')}")
except Exception as e:
    print(f"Get-StartApps error: {e}")

