import math

from app.geo import haversine_m
from app.georef import DroneMeta, box_anchor, pixel_to_ground


def _meta(**kw):
    base = dict(lat=39.9, lon=32.75, alt=100.0, heading=0.0, gimbal_pitch=-90.0, fov=90.0, sensor_w=1000, sensor_h=500)
    base.update(kw)
    return DroneMeta(**base)


def test_nadir_center_is_drone_position():
    lat, lon = pixel_to_ground(500, 250, _meta())
    assert haversine_m(lat, lon, 39.9, 32.75) < 0.01


def test_nadir_footprint_scale():
    # hFOV 90° @100 m => yarı genişlik 100 m. Sağ kenar, heading 0 => 100 m DOĞU
    lat, lon = pixel_to_ground(1000, 250, _meta())
    assert abs(haversine_m(lat, lon, 39.9, 32.75) - 100.0) < 0.5
    assert lon > 32.75 and abs(lat - 39.9) < 1e-6


def test_image_top_points_along_heading():
    # heading 90° (doğu): görüntü yukarısı doğu. Üst kenar, yarı yükseklik = 50 m
    lat, lon = pixel_to_ground(500, 0, _meta(heading=90.0))
    assert abs(haversine_m(lat, lon, 39.9, 32.75) - 50.0) < 0.5
    assert lon > 32.75


def test_oblique_hits_ground_ahead():
    # 45° eğim, alt=100 => merkez pikseli 100 m ileride (kuzey)
    lat, lon = pixel_to_ground(500, 250, _meta(gimbal_pitch=-45.0))
    assert abs(haversine_m(lat, lon, 39.9, 32.75) - 100.0) < 0.5
    assert lat > 39.9


def test_above_horizon_returns_none():
    assert pixel_to_ground(500, 0, _meta(gimbal_pitch=-10.0, fov=90.0)) is None


def test_box_anchor_modes():
    assert box_anchor(0, 0, 10, 20, -90) == (5, 10)  # nadir: merkez
    assert box_anchor(0, 0, 10, 20, -45) == (5, 20)  # eğik: alt-orta
