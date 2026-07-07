"""
locate(chip_part) — resolve the local Smart Manual SQLite DB path for a chip.

No fallback: the Smart Manual VS Code extension must have already downloaded
the chip's database to local disk, or this raises FileNotFoundError.
"""

import os
from pathlib import Path


def locate(chip_part: str) -> Path:
    """Return the path to the Smart Manual DB for chip_part.

    Raises FileNotFoundError if the extension hasn't downloaded it locally.
    Works cross-platform: Windows (APPDATA), macOS/Linux (~/.config).
    """
    # Cross-platform: resolve VS Code's globalStorage directory
    if "APPDATA" in os.environ:
        # Windows: C:\Users\<user>\AppData\Roaming
        base = Path(os.environ["APPDATA"])
    else:
        # macOS/Linux: ~/.config
        base = Path.home() / ".config"

    db_path = (
        base
        / "Code"
        / "User"
        / "globalStorage"
        / "renesaselectronicscorporation.renesas-smart-manual"
        / "downloads"
        / chip_part
        / f"{chip_part}_en"
    )
    if not db_path.is_file():
        raise FileNotFoundError(
            f"Smart Manual DB not found for chip_part={chip_part!r} at {db_path}. "
            "Open the chip's Hardware User Manual once in the Renesas Smart Manual "
            "VS Code extension to download it locally."
        )
    return db_path


if __name__ == "__main__":
    import sqlite3
    import sys

    chip = sys.argv[1] if len(sys.argv) > 1 else "RA6M4"
    path = locate(chip)
    print(f"Resolved: {path}")

    con = sqlite3.connect(str(path))
    con.execute("SELECT 1")
    con.close()
    print("Checkpoint: SELECT 1 succeeded")
