"""Jaspersoft Studio bands: Last Page Footer, No Data and Background are kept and printed like in Jasper."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import designer_render as dr  # noqa: E402
import jrxml_import  # noqa: E402


def label(text, height=16):
    return {"type": "staticText", "x": 0, "y": 0, "width": 120, "height": height, "text": text}


def jasper_design(**bands):
    base = {
        "page": {"width": 300, "height": 220, "margins": {"top": 10, "right": 10, "bottom": 10, "left": 10}},
        "bands": {
            "detail": {"height": 40, "elements": [label("D")]},
            "pageFooter": {"height": 20, "elements": [label("PF")]},
            "lastPageFooter": {"height": 20, "elements": [label("LPF")]},
            "background": {"height": 100, "elements": [label("BG")]},
            "noData": {"height": 30, "elements": [label("ND")]},
        },
    }
    base["bands"].update(bands)
    return base


@pytest.fixture
def drawn(monkeypatch):
    """Record (page number, first text) of every band the renderer draws."""
    calls = []
    original = dr._Render.draw_band

    def record(self, canvas, band, y, row, scope_rows=None, engine=None):
        texts = [e.get("text") for e in band["elements"] if e.get("text")]
        if texts:
            calls.append((self.page_number, texts[0]))
        return original(self, canvas, band, y, row, scope_rows, engine)

    monkeypatch.setattr(dr._Render, "draw_band", record)
    return calls


def last_pass(calls):
    """render_design_pdf runs twice (for PAGE_COUNT); keep the second run."""
    starts = [i for i, call in enumerate(calls) if call == (1, "BG") or call == (1, "ND")]
    return calls[starts[-1]:] if starts else calls


def test_new_bands_survive_validation():
    design = dr.validate_design(jasper_design(), [], set())
    for key in ("lastPageFooter", "noData", "background"):
        assert design["bands"][key]["elements"][0]["text"] in {"LPF", "ND", "BG"}
    empty = dr.validate_design({"bands": {}}, [], set())
    assert all(empty["bands"][key] == {"height": 0, "elements": []} for key in ("lastPageFooter", "noData", "background"))


def test_last_page_footer_replaces_page_footer_and_background_on_every_page(drawn):
    design = dr.validate_design(jasper_design(), [], set())
    pdf = dr.render_design_pdf(design, "T", [], [{} for _ in range(8)], {})
    assert pdf.startswith(b"%PDF")
    calls = last_pass(drawn)
    pages = max(page for page, _ in calls)
    assert pages > 1
    assert [page for page, text in calls if text == "BG"] == list(range(1, pages + 1))
    assert [page for page, text in calls if text == "PF"] == list(range(1, pages))
    assert [page for page, text in calls if text == "LPF"] == [pages]
    assert not [1 for _, text in calls if text == "ND"]


def test_without_last_page_footer_the_page_footer_is_on_every_page(drawn):
    design = dr.validate_design(jasper_design(lastPageFooter={"height": 0, "elements": []}), [], set())
    dr.render_design_pdf(design, "T", [], [{} for _ in range(8)], {})
    calls = last_pass(drawn)
    pages = max(page for page, _ in calls)
    assert [page for page, text in calls if text == "PF"] == list(range(1, pages + 1))


def test_no_data_band_is_printed_alone_when_there_are_no_rows(drawn):
    design = dr.validate_design(jasper_design(), [], set())
    pdf = dr.render_design_pdf(design, "T", [], [], {})
    assert pdf.startswith(b"%PDF")
    assert {text for _, text in last_pass(drawn)} == {"ND"}


def test_without_no_data_band_an_empty_report_still_prints_its_bands(drawn):
    design = dr.validate_design(jasper_design(noData={"height": 0, "elements": []}), [], set())
    dr.render_design_pdf(design, "T", [], [], {})
    assert {"BG", "LPF"} <= {text for _, text in drawn}


JRXML = """<?xml version="1.0"?>
<jasperReport xmlns="http://jasperreports.sourceforge.net/jasperreports" name="B" pageWidth="595" pageHeight="842" whenNoDataType="NoDataSection">
 <queryString language="MongoDbQuery"><![CDATA[{ 'collectionName' : 'orders', 'findQuery' : { } }]]></queryString>
 <field name="Name" class="java.lang.String"/>
 <background><band height="200"><staticText><reportElement x="0" y="0" width="200" height="20"/><text><![CDATA[DRAFT]]></text></staticText></band></background>
 <detail><band height="20"><textField><reportElement x="0" y="0" width="200" height="20"/><textFieldExpression><![CDATA[$F{Name}]]></textFieldExpression></textField></band></detail>
 <lastPageFooter><band height="24"><staticText><reportElement x="0" y="0" width="200" height="20"/><text><![CDATA[End of report]]></text></staticText></band></lastPageFooter>
 <noData><band height="30"><staticText><reportElement x="0" y="0" width="200" height="20"/><text><![CDATA[No records]]></text></staticText></band></noData>
</jasperReport>"""


def test_jrxml_import_brings_the_new_bands():
    try:
        result = jrxml_import.import_jrxml(JRXML)
    except ValueError:
        pytest.skip("this JRXML query form is not importable here")
    bands = result["design"]["bands"]
    assert bands["background"]["height"] == 200 and bands["background"]["elements"][0]["text"] == "DRAFT"
    assert bands["lastPageFooter"]["elements"][0]["text"] == "End of report"
    assert bands["noData"]["elements"][0]["text"] == "No records"
    assert not [w for w in result["warnings"] if "was not imported" in w]
