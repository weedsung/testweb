from __future__ import annotations

from pathlib import Path

source_path = Path(__file__).with_name("domain_pilot.py")
source = source_path.read_text(encoding="utf-8")

source = source.replace(
    r"(?<![a-z])ai(?![a-z])",
    r"(^|[^a-z])ai([^a-z]|$)",
)
source = source.replace(
    'r"stocks", r"shares",',
    'r"stock", r"stocks", r"share price", r"shares",',
)
source = source.replace(
    'r"oscar", r"grammy",',
    'r"academy awards", r"oscar awards", r"oscar nominations", r"grammy",',
)
source = source.replace('r"vote", r"voting",\n', '')
source = source.replace('r"投票", ', '')
source = source.replace('r"パン", ', 'r"食パン", r"パン屋", r"菓子パン", ')

old = '''def alternation(items: list[str]) -> str:\n    return "(?:" + "|".join(items) + ")"'''
new = '''def alternation(items: list[str]) -> str:\n    bounded = []\n    for item in items:\n        if item.isascii() and not item.startswith("("):\n            bounded.append(r"(^|[^a-z0-9])(?:" + item + r")([^a-z0-9]|$)")\n        else:\n            bounded.append(item)\n    return "(?:" + "|".join(bounded) + ")"'''
if old not in source:
    raise RuntimeError("alternation function not found")
source = source.replace(old, new)

code = compile(source, str(source_path), "exec")
exec(code, {"__name__": "__main__", "__file__": str(source_path)})
