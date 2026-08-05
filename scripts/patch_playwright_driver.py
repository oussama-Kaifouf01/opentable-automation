from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE_BUNDLE = (
    ROOT
    / ".venv"
    / "Lib"
    / "site-packages"
    / "playwright"
    / "driver"
    / "package"
    / "lib"
    / "coreBundle.js"
)

REPLACEMENTS = {
    "url: pageError.location.url": 'url: pageError.location?.url || ""',
    "line: pageError.location.lineNumber": "line: pageError.location?.lineNumber || 0",
    "column: pageError.location.columnNumber": "column: pageError.location?.columnNumber || 0",
}


def main() -> int:
    if not CORE_BUNDLE.exists():
        print(f"Could not find Playwright driver bundle: {CORE_BUNDLE}")
        return 1

    source = CORE_BUNDLE.read_text(encoding="utf-8")
    updated = source
    matched = 0
    for before, after in REPLACEMENTS.items():
        matched += updated.count(before)
        updated = updated.replace(before, after)

    if updated != source:
        CORE_BUNDLE.write_text(updated, encoding="utf-8")
        print(f"Patched {matched} Playwright driver pageError location references.")

    verified = CORE_BUNDLE.read_text(encoding="utf-8")
    remaining = [before for before in REPLACEMENTS if before in verified]
    patched = [after for after in REPLACEMENTS.values() if after in verified]
    if remaining:
        print(f"Playwright driver patch verification failed: {remaining}")
        return 1
    if len(patched) != len(REPLACEMENTS):
        print("Could not find the expected Playwright driver code to verify the patch.")
        return 1

    if updated == source:
        print("Playwright driver patch already applied.")

    print("Playwright driver patch verification: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
