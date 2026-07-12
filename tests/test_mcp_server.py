"""Tests for the MCP server logic functions (#9). Transport is not exercised;
the tool logic is validated directly."""
import os

import pytest

import mcp_server

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "canlab", "sample_data", "sample_kona_drive.csv")


def test_load_and_list():
    info = mcp_server.load_log(SAMPLE)
    assert info["frame_count"] > 0
    assert info["unique_ids"]
    ids = mcp_server.list_ids()
    assert ids and ids[0]["count"] >= ids[-1]["count"]


def test_detectors_run():
    mcp_server.load_log(SAMPLE)
    cc = mcp_server.detect_counters_checksums()
    assert isinstance(cc, dict)
    stats = mcp_server.byte_stats(mcp_server.list_ids()[0]["id"])
    assert stats["frames"] > 0


def test_requires_load_first():
    mcp_server._SESSION["df"] = None
    with pytest.raises(ValueError):
        mcp_server.list_ids()


def test_fastmcp_registration():
    pytest.importorskip("mcp")
    from mcp.server.fastmcp import FastMCP
    m = FastMCP("canlab-test")
    mcp_server._register(m)   # must not raise
