from __future__ import annotations

from rs_service.geo.datum import gcj02_to_wgs84, point_in_ring, simplify_ring, wgs84_to_gcj02


def test_roundtrip_is_sub_meter() -> None:
    lon, lat = 120.1551, 30.2741  # 杭州西湖
    glon, glat = wgs84_to_gcj02(lon, lat)
    assert abs(glon - lon) > 1e-4, "GCJ-02 偏移应当存在"
    blon, blat = gcj02_to_wgs84(glon, glat)
    assert abs(blon - lon) < 1e-7 and abs(blat - lat) < 1e-7


def test_simplify_keeps_corners() -> None:
    ring = [(0.0, 0.0), (0.0, 1.0), (0.5, 1.0001), (1.0, 1.0), (1.0, 0.0), (0.0, 0.0)]
    out = simplify_ring(ring, 0.01)
    assert out[0] == out[-1]
    assert len(out) < len(ring) + 1
    assert (0.0, 0.0) in out and (1.0, 1.0) in out


def test_point_in_ring() -> None:
    ring = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)]
    assert point_in_ring(1.0, 1.0, ring)
    assert not point_in_ring(3.0, 1.0, ring)
