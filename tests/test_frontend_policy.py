import re
from pathlib import Path

FRONTEND_SRC = Path(__file__).resolve().parent.parent / "frontend" / "src"
NATIVE_DIALOG = re.compile(
    r"(?<![\w.])(?:(?:window|globalThis)\s*\.\s*)?"
    r"(?:alert|confirm|prompt)\s*\("
)


def test_frontend_never_uses_browser_native_dialogs():
    offences = []
    for path in sorted(FRONTEND_SRC.rglob("*")):
        if path.suffix not in {".ts", ".tsx"}:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if NATIVE_DIALOG.search(line):
                offences.append(f"{path.relative_to(FRONTEND_SRC.parent)}:{line_no}")
    assert not offences, "use an app-owned modal instead: " + ", ".join(offences)
