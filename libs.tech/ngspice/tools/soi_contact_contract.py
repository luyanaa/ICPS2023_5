#!/usr/bin/env python3
"""Joint SOI contact contract over the DRC/LVS/PEX/ERC tool results.

The four tools do not share one rejection level for an illegal or ambiguous SOI
contact: LVS is a topology/netlist comparison that can legitimately complete
while ERC flags the same contact as illegal and PEX refuses to emit a parasitic
network.  Reading a single "LVS MATCH" as "the contact is physically legal" is
therefore wrong.  This gate binds their actual result artifacts into one
verdict:

    PASS_SOI = DRC_PASS and LVS_MATCH and ERC_PASS and PEX_COMPLETE

Every leg that is missing or not decisively passing makes the whole verdict
FAIL_SOI (absent evidence is never treated as pass).  This is a research-PDK
screening contract, not a foundry sign-off.

Artifact inputs (all optional at the CLI, each absent leg is BLOCKED):

  --drc-report   KLayout .lyrdb report; PASS requires zero markers
  --lvs-log      text log of the LVS run; MATCH requires
                 "ICPS2023_5 LVS MATCH (mismatches=0)"
  --pex-output   PEX SPICE netlist; COMPLETE requires the file to exist and
                 contain a .SUBCKT (PEX emits no file when it fails closed)
  --erc-report   check_postlayout_erc.py JSON report; ERC_PASS requires both
                 checks.gds_geometry.status and checks.illegal_contacts.status
                 to be PASS
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

MATCH_TOKEN = "ICPS2023_5 LVS MATCH (mismatches=0)"
NO_MATCH_TOKEN = "ICPS2023_5 LVS NO MATCH"

TOOL_SEMANTICS = {
    "drc": "geometric rule deck; default contact size is the legacy >=4 um rule, strict=true requires 5x5 um",
    "lvs": "netlist/topology comparison; completing or matching does not itself certify contact legality",
    "erc": "illegal/ambiguous CNT screen with an explicit contact_policy; owns the contact-size verdict",
    "pex": "fail-closed extraction; an illegal or ambiguous landing produces no parasitic network",
}


def _read_drc_markers(path: Path) -> list[str]:
    root = ElementTree.parse(path).getroot()
    items = root.find("items")
    if items is None:
        raise ValueError("DRC report has no items collection: %s" % path)
    return [item.findtext("category", "") for item in items.findall("item")]


def evaluate_contract(*, drc_report: Path | None = None,
                      lvs_log_text: str | None = None,
                      pex_output: Path | None = None,
                      erc_report: dict[str, Any] | None = None) -> dict[str, Any]:
    """Bind the four actual tool results into one SOI contact verdict."""
    legs: dict[str, dict[str, Any]] = {}
    reasons: list[str] = []

    if drc_report is None:
        legs["drc"] = {"status": "BLOCKED", "reason": "DRC report was not supplied"}
        reasons.append("drc: BLOCKED (no report)")
    else:
        markers = _read_drc_markers(Path(drc_report))
        legs["drc"] = ({"status": "PASS", "markers": [], "report": str(drc_report)}
                       if not markers else
                       {"status": "FAIL", "markers": markers, "report": str(drc_report)})
        if markers:
            reasons.append("drc: FAIL (%d markers: %s)"
                           % (len(markers), sorted(set(markers))))

    if lvs_log_text is None:
        legs["lvs"] = {"status": "BLOCKED", "reason": "LVS log was not supplied"}
        reasons.append("lvs: BLOCKED (no log)")
    elif MATCH_TOKEN in lvs_log_text:
        legs["lvs"] = {"status": "MATCH",
                       "note": "topology match; this alone is not a contact-legality verdict"}
    elif NO_MATCH_TOKEN in lvs_log_text:
        legs["lvs"] = {"status": "NO_MATCH"}
        reasons.append("lvs: NO_MATCH")
    else:
        legs["lvs"] = {"status": "ERROR", "reason": "log has no LVS verdict token"}
        reasons.append("lvs: ERROR (no verdict token in log)")

    if pex_output is None:
        legs["pex"] = {"status": "BLOCKED", "reason": "PEX output was not supplied"}
        reasons.append("pex: BLOCKED (no output)")
    else:
        path = Path(pex_output)
        text = path.read_text(encoding="utf-8") if path.is_file() else None
        if text is not None and ".SUBCKT" in text:
            legs["pex"] = {"status": "COMPLETE", "output": str(path)}
        else:
            legs["pex"] = {"status": "FAIL", "output": str(path),
                           "reason": ("netlist missing" if text is None else
                                      "netlist has no .SUBCKT; extraction did not complete")}
            reasons.append("pex: FAIL (%s)" % legs["pex"]["reason"])

    if erc_report is None:
        legs["erc"] = {"status": "BLOCKED", "reason": "ERC report was not supplied"}
        reasons.append("erc: BLOCKED (no report)")
    else:
        checks = erc_report.get("checks", {}) if isinstance(erc_report, dict) else {}
        geometry = checks.get("gds_geometry", {}).get("status")
        contacts = checks.get("illegal_contacts", {}).get("status")
        overall = erc_report.get("status") if isinstance(erc_report, dict) else None
        if geometry == "PASS" and contacts == "PASS":
            legs["erc"] = {"status": "PASS", "overall_status": overall,
                           "contact_policy": erc_report.get("contact_policy", "strict"),
                           "note": "illegal_contacts and gds_geometry both PASS"}
        elif contacts == "FAIL":
            legs["erc"] = {"status": "FAIL", "overall_status": overall,
                           "illegal_contacts": checks.get("illegal_contacts", {})}
            reasons.append("erc: FAIL (illegal CNT present)")
        else:
            legs["erc"] = {"status": "BLOCKED", "overall_status": overall,
                           "gds_geometry": geometry, "illegal_contacts": contacts}
            reasons.append("erc: BLOCKED (geometry/contact verdict unavailable)")

    passed = (legs["drc"]["status"] == "PASS" and
              legs["lvs"]["status"] == "MATCH" and
              legs["pex"]["status"] == "COMPLETE" and
              legs["erc"]["status"] == "PASS")
    return {
        "schema_version": 1,
        "tool": "soi_contact_contract.py",
        "qualification": "research_PDK_screening_contract_not_foundry_signoff",
        "formula": "PASS_SOI = DRC_PASS and LVS_MATCH and ERC_PASS and PEX_COMPLETE",
        "result": "PASS_SOI" if passed else "FAIL_SOI",
        "legs": legs,
        "reasons": reasons,
        "tool_semantics": TOOL_SEMANTICS,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drc-report", type=Path)
    parser.add_argument("--lvs-log", type=Path)
    parser.add_argument("--pex-output", type=Path)
    parser.add_argument("--erc-report", type=Path)
    parser.add_argument("--out", default="-", help="JSON output path; '-' writes to stdout")
    args = parser.parse_args(argv)
    try:
        lvs_text = (args.lvs_log.read_text(encoding="utf-8")
                    if args.lvs_log is not None and args.lvs_log.is_file() else None)
        erc = (json.loads(args.erc_report.read_text(encoding="utf-8"))
               if args.erc_report is not None and args.erc_report.is_file() else None)
        report = evaluate_contract(
            drc_report=args.drc_report if args.drc_report is not None and args.drc_report.is_file() else None,
            lvs_log_text=lvs_text,
            pex_output=args.pex_output,
            erc_report=erc,
        )
    except (OSError, ValueError, json.JSONDecodeError, ElementTree.ParseError) as error:
        print("soi_contact_contract: %s" % error, file=sys.stderr)
        return 1
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.out == "-":
        sys.stdout.write(rendered)
    else:
        Path(args.out).expanduser().write_text(rendered, encoding="utf-8")
    return 0 if report["result"] == "PASS_SOI" else 2


if __name__ == "__main__":
    raise SystemExit(main())
