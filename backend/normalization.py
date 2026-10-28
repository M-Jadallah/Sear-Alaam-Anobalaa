"""Search-only Arabic normalization with original character positions."""
import unicodedata

ALEFS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي"})


def normalize_with_map(text: str) -> tuple[str, list[tuple[int, int]]]:
    chars, positions = [], []
    for index, original in enumerate(text):
        for char in unicodedata.normalize("NFKC", original).translate(ALEFS).lower():
            if unicodedata.category(char).startswith("M") or char == "ـ":
                continue
            value = char if char.isalnum() else " "
            if value == " " and (not chars or chars[-1] == " "):
                continue
            chars.append(value)
            positions.append((index, index + 1))
    if chars and chars[-1] == " ":
        chars.pop()
        positions.pop()
    return "".join(chars), positions


def normalize_arabic(text: str) -> str:
    return normalize_with_map(text)[0]


def fts_query(query: str, phrase: bool = False) -> str:
    tokens = normalize_arabic(query).split()[:30]
    if phrase:
        return '"' + " ".join(tokens) + '"' if tokens else ""
    return " AND ".join('"' + token + '"' for token in tokens)
