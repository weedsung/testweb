from __future__ import annotations

from pathlib import Path

source_path = Path(__file__).with_name("domain_pilot.py")
source = source_path.read_text(encoding="utf-8")
source = source.replace(
    r"(?<![a-z])ai(?![a-z])",
    r"(^|[^a-z])ai([^a-z]|$)",
)
code = compile(source, str(source_path), "exec")
exec(code, {"__name__": "__main__", "__file__": str(source_path)})
