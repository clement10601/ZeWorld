"""文字檢查：遊戲語言是繁體中文時，抓出模型混進來的簡體字。"""
from pathlib import Path

_lines = (Path(__file__).resolve().parent / "data" / "zh_simplified_only.txt").read_text(encoding="utf-8").splitlines()
SIMPLIFIED_ONLY = frozenset("".join(l for l in _lines if not l.startswith("#")))


# 模型把指示或自己的分析寫進台詞時常見的字眼
LEAK_MARKERS = ("改寫", "原句", "台詞", "新事實", "authored", "Traditional Chinese")


def leaks_instructions(line):
    return any(m in line for m in LEAK_MARKERS)


def is_traditional(language):
    lang = (language or "").lower()
    return lang.startswith(("zh-hant", "zh-tw", "zh-hk")) or "繁體" in lang


def simplified_chars(text, language):
    """language 是繁中時回傳 text 裡的簡體專用字（排序去重），否則回傳空清單。"""
    if not is_traditional(language):
        return []
    return sorted({c for c in text if c in SIMPLIFIED_ONLY})
