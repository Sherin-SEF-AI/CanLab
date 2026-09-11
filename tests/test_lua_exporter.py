"""The generated Wireshark dissector: portable Lua, and correct bit extraction.

Two load-time failures are pinned here because they are invisible until
Wireshark refuses the script: ``bit32`` was removed in Lua 5.3 (Wireshark 4.4
ships 5.4), and ``Field.new`` must be called at load time. The extractor is
also checked against cantools, so the dissector decodes what the DBC says.
"""
import math
import re

import cantools
import pytest

from canlab.core.dbc_manager import signals_to_dbc_string
from canlab.core.lua_exporter import signals_to_lua_dissector

lupa = pytest.importorskip("lupa", reason="lupa provides a Lua runtime")


def sig(**kw):
    base = {"message_id": "1A0", "message_name": "MSG", "signal_name": "Sig",
            "start_bit": 0, "length": 16, "byte_order": "little",
            "value_type": "unsigned", "scale": 1.0, "offset": 0.0,
            "min_val": 0, "max_val": 0, "unit": "", "description": ""}
    base.update(kw)
    return base


class _Tvb:
    """The slice of the Wireshark tvb API the extractor uses."""

    def __init__(self, payload):
        self._payload = bytes(payload)

    def __call__(self, offset, length):
        chunk = self._payload[offset:offset + length]
        return _Range(chunk)

    def len(self):
        return len(self._payload)


class _Range:
    def __init__(self, chunk):
        self._chunk = chunk

    def uint(self):
        return int.from_bytes(self._chunk, "big")


def _lua_extractor(source: str):
    """Load the generated extractor into a real Lua runtime."""
    runtime = lupa.LuaRuntime(unpack_returned_tuples=True)
    start = source.index("local function get_bit")
    end = source.index("function canlab_proto.dissector")
    runtime.execute(source[start:end])
    return runtime.globals().canlab_extract_bits


def test_no_bit32_and_no_53_only_operators():
    source = signals_to_lua_dissector([sig()])
    code = "\n".join(ln for ln in source.splitlines() if not ln.strip().startswith("--"))
    assert "bit32" not in code, "bit32 was removed in Lua 5.3"
    body = code[code.index("local function get_bit"):]
    for op in ("<<", ">>", "&", "|", "~"):
        assert op not in body, f"{op!r} is a syntax error on Lua 5.1/5.2"


def test_field_new_is_called_at_load_time():
    source = signals_to_lua_dissector([sig()])
    dissector_at = source.index("function canlab_proto.dissector")
    assert "Field.new" in source[:dissector_at]
    assert "Field.new" not in source[dissector_at:], \
        "Wireshark rejects Field.new inside a dissector"


def test_generated_script_parses_as_lua():
    source = signals_to_lua_dissector([sig(), sig(signal_name="Other", start_bit=16,
                                                  length=8, byte_order="big")])
    runtime = lupa.LuaRuntime()
    assert runtime.eval(f"load({_quote(source)})") is not None, "generated Lua does not parse"


def _quote(source: str) -> str:
    return "[==[\n" + source + "\n]==]"


@pytest.mark.parametrize("definition,payload", [
    (sig(start_bit=0, length=16, byte_order="little"), bytes([0xD2, 0x04, 0, 0, 0, 0, 0, 0])),
    (sig(start_bit=7, length=16, byte_order="big"), bytes([0x04, 0xD2, 0, 0, 0, 0, 0, 0])),
    (sig(start_bit=8, length=8, byte_order="little"), bytes([0, 0xFE, 0, 0, 0, 0, 0, 0])),
    (sig(start_bit=4, length=12, byte_order="big", value_type="signed"),
     bytes([0x0A, 0xBC, 0, 0, 0, 0, 0, 0])),
    (sig(start_bit=0, length=32, byte_order="little"),
     bytes([0x78, 0x56, 0x34, 0x12, 0, 0, 0, 0])),
])
def test_extractor_agrees_with_cantools(definition, payload):
    extract = _lua_extractor(signals_to_lua_dissector([definition]))
    db = cantools.database.load_string(signals_to_dbc_string([definition]),
                                       database_format="dbc")
    expected = db.decode_message(0x1A0, payload)["Sig"]
    got = extract(_Tvb(payload), definition["start_bit"], definition["length"],
                  definition["byte_order"],
                  definition["value_type"] == "signed")
    assert math.isclose(float(got), float(expected)), (got, expected)


def test_scale_and_offset_are_applied_in_the_script():
    definition = sig(scale=0.1, offset=-40.0)
    source = signals_to_lua_dissector([definition])
    assert "* 0.1 + -40.0" in source


def test_identifier_collisions_are_disambiguated():
    source = signals_to_lua_dissector([
        sig(signal_name="Speed-L", start_bit=0, length=8),
        sig(signal_name="Speed_L", start_bit=8, length=8),
    ])
    locals_declared = re.findall(r"local (f_1A0_\w+) = ProtoField", source)
    assert len(locals_declared) == len(set(locals_declared)), locals_declared


def test_strings_are_escaped():
    source = signals_to_lua_dissector([sig(unit='k"m', description='say "hi"')])
    assert '\\"' in source
    runtime = lupa.LuaRuntime()
    assert runtime.eval(f"load({_quote(source)})") is not None


def test_multiplexed_signals_are_gated_on_the_selector():
    source = signals_to_lua_dissector([
        sig(signal_name="Mode", start_bit=0, length=8, mux_role="M"),
        sig(signal_name="A", start_bit=8, length=8, mux_value=0),
        sig(signal_name="B", start_bit=8, length=8, mux_value=1),
    ])
    assert "local mux = canlab_extract_bits" in source
    assert "if mux == 0 then" in source and "if mux == 1 then" in source


def test_extended_ids_are_emitted_in_full():
    source = signals_to_lua_dissector([sig(message_id="18FEF100")])
    assert "if can_id == 0x18FEF100 then" in source


def test_oversized_signal_is_refused():
    with pytest.raises(ValueError, match="bits"):
        signals_to_lua_dissector([sig(length=64)])
