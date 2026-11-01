import re
from pathlib import Path
from typing import List

_WELL_KNOWN_FOLDERS = {
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "desktop": Path.home() / "Desktop",
    "pictures": Path.home() / "Pictures",
    "videos": Path.home() / "Videos",
    "music": Path.home() / "Music",
}

_EXT_MAP = {
    "pdf": [".pdf"],
    "word": [".docx", ".doc"],
    "docx": [".docx"],
    "doc": [".doc"],
    "excel": [".xlsx", ".xls", ".csv"],
    "spreadsheet": [".xlsx", ".xls", ".csv"],
    "xlsx": [".xlsx"],
    "csv": [".csv"],
    "powerpoint": [".pptx", ".ppt"],
    "presentation": [".pptx", ".ppt"],
    "pptx": [".pptx"],
    "text": [".txt", ".log", ".md"],
    "image": [".png", ".jpg", ".jpeg", ".bmp", ".gif"],
}

def resolve_file_or_folder(query: str) -> dict:
    q = query.strip()
    q = re.sub(r"^(?:open|show\s+me|find|view|explore|launch)\s+(?:the\s+)?", "", q, flags=re.I).strip()
    q_lower = q.lower()

    # 1. Exact folder match
    for f_name, f_path in _WELL_KNOWN_FOLDERS.items():
        if q_lower in (f_name, f"{f_name} folder", f"the {f_name} folder", f"my {f_name}", f"my {f_name} folder"):
            if f_path.exists():
                return {"type": "folder", "path": f_path, "multiple": False}

    # 2. Check direct path
    direct = Path(q.strip('"').strip("'")).expanduser()
    if direct.exists():
        return {"type": "folder" if direct.is_dir() else "file", "path": direct, "multiple": False}

    # 3. Detect target directory hint
    search_dirs = [Path.home() / "Downloads", Path.home() / "Documents", Path.home() / "Desktop", Path.cwd()]
    if "in downloads" in q_lower or "from downloads" in q_lower:
        search_dirs = [Path.home() / "Downloads"]
        q_lower = q_lower.replace("in downloads", "").replace("from downloads", "").strip()
    elif "in documents" in q_lower or "from documents" in q_lower:
        search_dirs = [Path.home() / "Documents"]
        q_lower = q_lower.replace("in documents", "").replace("from documents", "").strip()
    elif "in desktop" in q_lower or "on desktop" in q_lower:
        search_dirs = [Path.home() / "Desktop"]
        q_lower = q_lower.replace("in desktop", "").replace("on desktop", "").strip()

    # 4. Detect "latest" qualifier
    is_latest = "latest" in q_lower or "recent" in q_lower
    if is_latest:
        q_lower = q_lower.replace("latest", "").replace("recent", "").replace("the", "").strip()

    # 5. Detect extension filter
    exts = []
    for k, v in _EXT_MAP.items():
        if k in q_lower:
            exts.extend(v)
            break
    if not exts and "." in q_lower:
        exts = ["." + q_lower.split(".")[-1].strip()]

    # Clean filename query
    clean_name = q_lower
    for word in ("open", "my", "the", "file", "document", "folder", "spreadsheet"):
        clean_name = " ".join([w for w in clean_name.split() if w != word])

    candidates: List[Path] = []
    for sdir in search_dirs:
        if not sdir.exists():
            continue
        try:
            for item in sdir.iterdir():
                if item.is_file():
                    if exts and item.suffix.lower() not in exts:
                        continue
                    if not clean_name or clean_name in item.name.lower() or item.stem.lower() in clean_name:
                        candidates.append(item)
        except Exception:
            pass

    if not candidates:
        return {"type": "none", "path": None, "candidates": []}

    # Sort candidates
    if is_latest:
        candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return {"type": "file", "path": candidates[0], "multiple": False}

    if len(candidates) == 1:
        return {"type": "file", "path": candidates[0], "multiple": False}

    exact = [c for c in candidates if c.name.lower() == clean_name or c.stem.lower() == clean_name]
    if len(exact) == 1:
        return {"type": "file", "path": exact[0], "multiple": False}

    return {"type": "multiple", "path": None, "candidates": candidates[:5]}

test_queries = [
    "Open the Documents folder",
    "Open the PDF in Downloads",
    "Open the latest Excel file",
    "Open non_existent_document_xyz.pdf",
]
for tq in test_queries:
    res = resolve_file_or_folder(tq)
    print(f"'{tq}' -> {res['type']}: {res.get('path') or [c.name for c in res.get('candidates', [])]}")
