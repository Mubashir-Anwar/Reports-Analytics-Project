"""Phase 2 canvas: the designer-only `locked` flag and ruler `guides` are kept on save and do not change the output."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import designer_render as dr  # noqa: E402

COLUMNS = ["Name"]
ROWS = [{"Name": "A"}, {"Name": "B"}]


def design(page=None, locked=None):
    element = {"type": "textField", "x": 0, "y": 0, "width": 100, "height": 16, "expression": "$F{Name}"}
    if locked is not None:
        element["locked"] = locked
    return {"page": page or {}, "bands": {"detail": {"height": 20, "elements": [element]}}}


def test_locked_flag_is_kept():
    assert dr.validate_design(design(locked=True), COLUMNS, set())["bands"]["detail"]["elements"][0]["locked"] is True
    assert dr.validate_design(design(), COLUMNS, set())["bands"]["detail"]["elements"][0]["locked"] is False
    assert dr.validate_design(design(locked="yes"), COLUMNS, set())["bands"]["detail"]["elements"][0]["locked"] is False


def test_guides_are_cleaned_sorted_and_deduplicated():
    page = {"guides": [200, 50.04, 200, "x", True, None, float("inf"), -1000, 99999, 120.55]}
    assert dr.validate_design(design(page), COLUMNS, set())["page"]["guides"] == [50.0, 120.5, 200.0]
    assert dr.validate_design(design({"guides": {"a": 1}}), COLUMNS, set())["page"]["guides"] == []
    assert len(dr.validate_design(design({"guides": list(range(100))}), COLUMNS, set())["page"]["guides"]) == 50


def test_locked_and_guides_do_not_change_the_output():
    plain = dr.validate_design(design(), COLUMNS, set())
    edited = dr.validate_design(design({"guides": [10, 20]}, locked=True), COLUMNS, set())
    assert dr.render_design_csv(plain, "T", COLUMNS, ROWS, {}) == dr.render_design_csv(edited, "T", COLUMNS, ROWS, {})
    assert dr.render_design_pdf(edited, "T", COLUMNS, ROWS, {}).startswith(b"%PDF")
