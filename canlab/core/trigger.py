"""
Trigger engine: match incoming CAN frames against user-defined rules.

Rule dict schema:
  {
    "id":       "0A6",           # hex ID to match ('' = any)
    "byte":     2,               # byte index (0-7)
    "op":       ">",             # >, <, ==, !=, &
    "value":    128,             # threshold / mask
    "label":    "WHL_SPD > 128",
    "enabled":  True,
  }
"""


def evaluate_rule(rule: dict, arb_id: int, data: bytes) -> bool:
    if not rule.get("enabled", True):
        return False

    # ID filter
    rid = rule.get("id", "").strip()
    if rid:
        try:
            if int(rid, 16) != arb_id:
                return False
        except ValueError:
            return False

    byte_idx = int(rule.get("byte", 0))
    if byte_idx >= len(data):
        return False
    byte_val = data[byte_idx]

    op  = rule.get("op", "==")
    thr = int(rule.get("value", 0))

    if   op == ">":  return byte_val > thr
    elif op == "<":  return byte_val < thr
    elif op == "==": return byte_val == thr
    elif op == "!=": return byte_val != thr
    elif op == "&":  return bool(byte_val & thr)
    elif op == ">=": return byte_val >= thr
    elif op == "<=": return byte_val <= thr
    return False


def check_triggers(rules: list, arb_id: int, data: bytes) -> list:
    """Return list of triggered rule dicts."""
    return [r for r in rules if evaluate_rule(r, arb_id, data)]
