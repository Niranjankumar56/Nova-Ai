from pathlib import Path
from actions.file_processor import _process_archive

test_archive = Path("security_test.zip")

import zipfile
with zipfile.ZipFile(test_archive, "w") as z:
    z.writestr("../../nova_security_test.txt", "TEST")

result = _process_archive(
    test_archive,
    "extract",
    {"destination": "security_test_output"},
)

print(result)

test_archive.unlink(missing_ok=True)

output_dir = Path("security_test_output")
if output_dir.exists():
    import shutil
    shutil.rmtree(output_dir, ignore_errors=True)

outside_file = Path("nova_security_test.txt")
if outside_file.exists():
    outside_file.unlink()
