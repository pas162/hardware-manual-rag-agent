"""
Regression tests for ingest/parser_tables.py table-header parsing (R1).

Row fixtures below are taken verbatim from pdfplumber.extract_tables() output
on the real RA6M4 UM PDF (data/pdfs/r01uh0890ej0160-ra6m4.pdf), pages 91 and 93.
"""

from ingest.parser_tables import _clean_cell_text, _rows_to_dicts

# Table 3.1 (§3.3.1, p.91) — single-row header, clean parse today and after the fix.
# Regression guard: multi-level-header handling must not break the common case.
TABLE_3_1_ROWS = [
    ["Table 3.1 Selection of operating modes by the mode-setting pin", None, None, None],
    ["Mode-setting pin (MD)", "Operating mode", "On-chip Flash", "External bus"],
    ["1", "Single-chip mode", "Enable", "Disable"],
    ["0", "SCI / USB boot mode", "Enable", "Disable"],
]

# Table 4.1 (§4.2, p.93) — three-level header (parent / child / sub-child) with
# merged cells. Before the fix, the parser took row[1] (the parent-label row) as
# the header and treated everything after as data, producing an unusable record.
TABLE_4_1_ROWS = [
    ["Table 4.1 Capacity of the code flash memory, data flash memory, and SRAM0",
     None, None, None, None, None, None],
    ["Code flash memory", None, None, "Data flash memory", None, "SRAM0", None],
    ["Capacity", "Address", None, "Capacity", "Address", "Capacity", "Address"],
    ["", "Linear mode", "Dual mode\n(BANKSEL.BANKSWP[2:0] =\n111b)", "", "", "", ""],
    ["1 MB", "0x0000_0000 -\n0x000F_FFFF", "Upper side bank:\n0x0020_0000 - 0x0027_FFFF",
     "8 KB", "0x0800_0000 -\n0x0800_1FFF", "256 KB", "0x2000_0000 -\n0x2003_FFFF"],
    [None, None, "Lower side bank:\n0x0000_0000 - 0x0007_FFFF", None, None, None, None],
    ["768 KB", "0x0000_0000 -\n0x000B_FFFF", "Upper side bank:\n0x0020_0000 - 0x0025_FFFF",
     None, None, None, None],
    [None, None, "Lower side bank:\n0x0000_0000 - 0x0005_FFFF", None, None, None, None],
]


def test_clean_cell_text_joins_bullet_lines_with_semicolons():
    text = "0 0 0:\nSunday\n0 0 1: Monday\n0 1 0:\nTuesday"
    assert _clean_cell_text(text) == "0 0 0: Sunday; 0 0 1: Monday; 0 1 0: Tuesday"


def test_clean_cell_text_single_line_passthrough():
    assert _clean_cell_text("Enable") == "Enable"


def test_clean_cell_text_wrapped_sentence_stays_joined_with_space():
    text = "Do not compare register va\nlue with corresponding time"
    assert _clean_cell_text(text) == "Do not compare register va lue with corresponding time"


def test_general_table_data_cells_are_bullet_cleaned():
    rows = [
        ["Table 9.9 Day of week codes", None],
        ["Code", "Meaning"],
        ["0x0", "0 0 0:\nSunday\n0 0 1: Monday"],
    ]
    _, data_rows = _rows_to_dicts(rows)
    assert data_rows[0]["Meaning"] == "0 0 0: Sunday; 0 0 1: Monday"


def test_table_3_1_single_row_header_regression():
    """Simple header must stay a single row — must NOT be swallowed by multi-level logic."""
    header, rows = _rows_to_dicts(TABLE_3_1_ROWS)

    assert header == ["Mode-setting pin (MD)", "Operating mode", "On-chip Flash", "External bus"]
    assert len(rows) == 2
    assert rows[0] == {
        "Mode-setting pin (MD)": "1",
        "Operating mode": "Single-chip mode",
        "On-chip Flash": "Enable",
        "External bus": "Disable",
    }
    assert rows[1]["Mode-setting pin (MD)"] == "0"
    assert rows[1]["Operating mode"] == "SCI / USB boot mode"


def test_table_4_1_multilevel_header_flattens_and_forward_fills():
    """Three-level header flattens to 'Parent - Child - Grandchild'; merged data cells forward-fill."""
    header, rows = _rows_to_dicts(TABLE_4_1_ROWS)

    assert "Code flash memory - Capacity" in header
    assert "Code flash memory - Address - Linear mode" in header
    assert any(h.startswith("Data flash memory - Capacity") for h in header)
    assert any(h.startswith("SRAM0 - Address") for h in header)

    # First data row: fully populated, no forward-fill needed.
    first = rows[0]
    assert first["Code flash memory - Capacity"] == "1 MB"
    assert "0x0000_0000" in first["Code flash memory - Address - Linear mode"]

    # Continuation row (None-heavy) must forward-fill capacity/address from the row above.
    second = rows[1]
    assert second["Code flash memory - Capacity"] == "1 MB"
    assert "Lower side bank" in second["Code flash memory - Address - Dual mode\n(BANKSEL.BANKSWP[2:0] =\n111b)"]

    # A later block (768 KB entry) must not leak the previous block's values.
    third = rows[2]
    assert third["Code flash memory - Capacity"] == "768 KB"
