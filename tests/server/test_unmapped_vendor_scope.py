"""A permission scope must not stand in for an unverified measurement field."""

from mirobody.server.routers.records_router import _device_metric
from mirobody.standardize import devices


def test_honor_distance_scope_does_not_claim_an_elevation_field():
    rows = [r for r in devices.vendor_fields("honor") if r.type == "distance.read"]
    assert len(rows) == 2
    assert all(not row.metric for row in rows)
    elevation = next(row for row in rows if row.loinc == "101687-2")
    assert "【未映射：厂商字段未核实】" in elevation.note
    assert _device_metric("honor", "distance.read") == ("distance.read", None)
