"""Cross-reference generated signals against opendbc signal names."""
import re

# Offline index: common Hyundai/Kia signals from commaai/opendbc
_OFFLINE_INDEX = {
    # WHL_SPD11
    "WHL_SPD_FL": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    "WHL_SPD_FR": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    "WHL_SPD_RL": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    "WHL_SPD_RR": {"file": "hyundai_kia_generic.dbc", "msg": "WHL_SPD11", "id": "0x386"},
    # MDPS12
    "CR_Mdps_StrColTq":   {"file": "hyundai_kia_generic.dbc", "msg": "MDPS12",    "id": "0x018"},
    "CF_Mdps_Stat":       {"file": "hyundai_kia_generic.dbc", "msg": "MDPS12",    "id": "0x018"},
    # SAS11
    "SAS_Angle":          {"file": "hyundai_kia_generic.dbc", "msg": "SAS11",     "id": "0x260"},
    "SAS_Speed":          {"file": "hyundai_kia_generic.dbc", "msg": "SAS11",     "id": "0x260"},
    # BRAKE11
    "CV_Brake_Act":       {"file": "hyundai_kia_generic.dbc", "msg": "BRAKE11",   "id": "0x02C"},
    "CF_Clu_Vanz":        {"file": "hyundai_kia_generic.dbc", "msg": "CLU11",     "id": "0x544"},
    # LKAS
    "CF_Lkas_Actuation":  {"file": "hyundai_kia_generic.dbc", "msg": "LKAS11",    "id": "0x050"},
    "CF_Lkas_ToiFlt":     {"file": "hyundai_kia_generic.dbc", "msg": "LKAS11",    "id": "0x050"},
}


def scan(state, repo_context: dict = None) -> dict:
    """
    Compare state.dbc_signals against offline index + optional repo DBC content.
    Returns {signal_name -> match_info_dict}.
    """
    matches = {}

    # Build a lookup of (msg_id, sig_name) -> opendbc entry
    index = dict(_OFFLINE_INDEX)

    # If repo has DBC content, parse signal names from it
    if repo_context:
        readme = repo_context.get("readme", "")
        _enrich_index_from_text(index, readme)

    for sig in state.dbc_signals:
        sname = sig.get("signal_name", "")
        # Direct name match
        if sname in index:
            matches[sname] = index[sname]
            continue
        # Partial match (case-insensitive)
        for key, val in index.items():
            if sname.upper() in key.upper() or key.upper() in sname.upper():
                matches[sname] = {**val, "partial": True}
                break

    return matches


def _enrich_index_from_text(index: dict, text: str):
    """Very simple: pull signal names from DBC-style SG_ lines in readme/DBC text."""
    for m in re.finditer(r"SG_\s+(\w+)\s*:", text):
        name = m.group(1)
        if name not in index:
            index[name] = {"file": "repo", "msg": "?", "id": "?"}
