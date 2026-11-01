import re

def parse_query_qualifiers(query: str):
    q = query.strip()
    
    # 1. Ordinal
    ord_match = re.search(r"\b(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\b", q, re.I)
    ordinal = None
    if ord_match:
        ord_word = ord_match.group(1).lower()
        ord_map = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3, "fourth": 4, "4th": 4, "fifth": 5, "5th": 5, "last": -1}
        ordinal = ord_map.get(ord_word)
        q = re.sub(rf"\b{re.escape(ord_match.group(1))}\b", "", q, flags=re.I).strip()
    
    # 2. Section / Container ("in profile", "in header", "in settings", "in dialog")
    sec_match = re.search(r"\b(?:in|under|inside)\s+(?:the\s+)?([a-zA-Z0-9_\s]+?)(?:\s+section|\s+panel|\s+tab|\s+window|\s+box|$)", q, re.I)
    section = None
    if sec_match:
        section = sec_match.group(1).strip().lower()
        q = re.sub(rf"\b(?:in|under|inside)\s+(?:the\s+)?{re.escape(sec_match.group(1))}(?:\s+section|\s+panel|\s+tab|\s+window|\s+box)?\b", "", q, flags=re.I).strip()
    
    # 3. Spatial ("at the top", "at the bottom", "on the left", "on the right")
    spatial = None
    if re.search(r"\b(at\s+(the\s+)?top|top\s+side)\b", q, re.I):
        spatial = "top"
        q = re.sub(r"\b(at\s+(the\s+)?top|top\s+side)\b", "", q, flags=re.I).strip()
    elif re.search(r"\b(at\s+(the\s+)?bottom|bottom\s+side)\b", q, re.I):
        spatial = "bottom"
        q = re.sub(r"\b(at\s+(the\s+)?bottom|bottom\s+side)\b", "", q, flags=re.I).strip()
    elif re.search(r"\b(on\s+(the\s+)?left|left\s+side)\b", q, re.I):
        spatial = "left"
        q = re.sub(r"\b(on\s+(the\s+)?left|left\s+side)\b", "", q, flags=re.I).strip()
    elif re.search(r"\b(on\s+(the\s+)?right|right\s+side)\b", q, re.I):
        spatial = "right"
        q = re.sub(r"\b(on\s+(the\s+)?right|right\s+side)\b", "", q, flags=re.I).strip()

    # Clean target
    clean_target = re.sub(r"\b(button|icon|option|item|link|field)\b", "", q, flags=re.I).strip()
    if not clean_target:
        clean_target = q

    return {
        "raw_query": query,
        "target": clean_target,
        "ordinal": ordinal,
        "section": section,
        "spatial": spatial,
    }

queries = [
    "Click the second Edit button",
    "Click Edit in the Profile section",
    "Click Save at the top",
    "Open the menu on the left",
    "Click Edit",
]

for qu in queries:
    res = parse_query_qualifiers(qu)
    print(f"'{qu}' -> {res}")

