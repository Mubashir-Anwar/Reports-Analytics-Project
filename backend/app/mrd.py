"""The .mrd single-file format (report definition JSON inside XML)."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from defusedxml import ElementTree as SafeET  # parsing uploads: no DTDs, entities or external references


def render_report_definition_xml(definition: dict[str, Any]) -> bytes:
    """Package the complete saved definition as an importable XML template."""
    root = ET.Element("mongoReportDefinition", {
        "version": "1",
        "reportId": str(definition.get("id", "")),
        "title": str(definition.get("title", "")),
        "folder": str(definition.get("folder", "General")),
    })
    payload = ET.SubElement(root, "definition", {"encoding": "json"})
    payload.text = json.dumps(definition, ensure_ascii=False, separators=(",", ":"))
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def parse_report_definition_xml(raw_xml: str) -> dict[str, Any]:
    if len(raw_xml.encode("utf-8")) > 5_000_000:
        raise ValueError("The report definition XML is larger than 5 MB")
    root = SafeET.fromstring(raw_xml, forbid_dtd=True)
    if root.tag != "mongoReportDefinition" or root.attrib.get("version") != "1":
        raise ValueError("This is not a supported Mongo report definition XML file")
    payload = root.find("definition")
    if payload is None or payload.attrib.get("encoding") != "json" or not payload.text:
        raise ValueError("No saved configuration was found inside the XML report definition")
    definition = json.loads(payload.text)
    if not isinstance(definition, dict):
        raise ValueError("No report definition object was found in the XML file")
    return definition
