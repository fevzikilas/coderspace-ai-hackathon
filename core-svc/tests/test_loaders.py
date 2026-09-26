"""Katı loader'lar: geçerli veri okunur; her bozukluk türü açık bir DatasetError ile REDDEDİLİR (sessiz onarım/atlama yok)."""
import json

import pytest

from app.config import Settings
from app.dataset import load_dataset
from app.loaders.image_meta import DatasetError, parse_image_meta
from app.loaders.tracks_csv import parse_tracks
from app.timeutil import clock_to_epoch, iso, parse_ts

DATE = "2025-06-01"

ENTRY = {
    "width_px": 960,
    "height_px": 540,
    "capture_time": "14:10",
    "corner_coordinates": {
        "top_left": [39.925651, 32.870729],
        "top_right": [39.925651, 32.872131],
        "bottom_left": [39.925045, 32.870729],
        "bottom_right": [39.925045, 32.872131],
    },
}


def _meta(**over):
    e = json.loads(json.dumps(ENTRY))
    for k, v in over.items():
        if k.startswith("corner_"):
            e["corner_coordinates"][k[len("corner_"):]] = v
        else:
            e[k] = v
    return json.dumps({"img_000860": e})


def test_clock_parsing_and_iso_roundtrip():
    ts = clock_to_epoch("14:10", DATE)
    assert iso(ts) == "2025-06-01T14:10:00Z"
    assert parse_ts("14:10", base_date=DATE) == ts
    assert parse_ts("2025-06-01T14:10:00Z") == ts
    with pytest.raises(ValueError):
        clock_to_epoch("25:00", DATE)
    with pytest.raises(ValueError):
        parse_ts("14:10")  # tarihsiz saat, base_date olmadan belirsiz


# ---------------------------------------------------------------------------------------------- image_meta.json
def test_image_meta_valid():
    m = parse_image_meta(json.dumps({"img_000860": ENTRY}), DATE)["img_000860"]
    assert (m.width_px, m.height_px, m.capture_time) == (960, 540, "14:10")
    assert iso(m.capture_ts) == "2025-06-01T14:10:00Z"
    assert m.corners.top_left == (39.925651, 32.870729) and m.corners.bottom_right == (39.925045, 32.872131)


@pytest.mark.parametrize(
    "text, needle",
    [
        # kullanıcının örnek dosyasındaki bozukluklar artık REDDEDİLİR
        ('"img_000860": {"width_px": 960}', "geçersiz JSON"),  # dış {} yok
        ('{"a": {"width_px": 1,},}', "geçersiz JSON"),  # sondaki virgül
        ('{"a": {"width_px": 1}', "geçersiz JSON"),  # eksik parantez
        ('{"a": 1, "a": 2}', "yinelenen anahtar"),
        ('{"a": NaN}', "geçersiz sayı sabiti"),
        ("[]", "dict"),  # liste biçimi artık yok
        ("{}", "en az bir görüntü"),
    ],
)
def test_image_meta_rejects_malformed_json_and_shape(text, needle):
    with pytest.raises(DatasetError, match=needle):
        parse_image_meta(text, DATE)


@pytest.mark.parametrize(
    "text, needle",
    [
        (_meta(width_px="960"), "width_px"),  # strict: string -> int dönüşümü yok
        (_meta(width_px=0), "width_px"),
        (_meta(height_px=540.5), "height_px"),
        (_meta(capture_time="14:70"), "capture_time"),
        (_meta(capture_time="2025-06-01T14:10:00Z"), "capture_time"),  # yalnızca HH:MM
        (_meta(corner_top_left=[99.0, 32.87]), "aralık dışı"),
        (_meta(corner_top_left=[39.9]), "top_left"),
        (_meta(corner_top_left=["a", "b"]), "top_left"),
        (_meta(corner_top_right=[39.925651, 32.870729]), "dejenere"),  # üst kenar 0 m
        (_meta(surprise=1), "surprise"),  # bilinmeyen alan yasak
    ],
)
def test_image_meta_rejects_invalid_values_with_field_path(text, needle):
    with pytest.raises(DatasetError, match=needle) as ei:
        parse_image_meta(text, DATE)
    assert "img_000860" in str(ei.value) or "geçersiz" in str(ei.value)  # alan yolu görünür


def test_image_meta_missing_corner_and_bad_image_id():
    bad = json.loads(_meta())
    del bad["img_000860"]["corner_coordinates"]["bottom_right"]
    with pytest.raises(DatasetError, match="bottom_right"):
        parse_image_meta(json.dumps(bad), DATE)
    with pytest.raises(DatasetError, match="img_000860/.."):
        parse_image_meta(json.dumps({"img_000860/..": ENTRY}), DATE)


def test_image_meta_error_message_reports_all_problems_up_to_limit():
    many = {f"i{n}": dict(ENTRY, width_px=-1) for n in range(20)}
    with pytest.raises(DatasetError) as ei:
        parse_image_meta(json.dumps(many), DATE)
    assert "20 doğrulama hatası" in str(ei.value) and "ve 12 hata daha" in str(ei.value)


# ---------------------------------------------------------------------------------------------- tracks.csv
HDR = "track_id,time,lat,lon\n"


def test_tracks_csv_valid_and_midnight_rollover():
    tr = parse_tracks(HDR + "T0001,10:15,39.988691,32.880750\nT0001,10:20,39.978233,32.885015\nT0001,10:25,39.978232,32.884969\n", DATE)
    assert [iso(p[0])[11:16] for p in tr["T0001"]] == ["10:15", "10:20", "10:25"]
    assert tr["T0001"][1][1:] == (39.978233, 32.885015)
    t2 = parse_tracks(HDR + "A,23:55,1,1\nA,00:00,1,1.1\nA,00:05,1,1.2\n", DATE)
    ts = [p[0] for p in t2["A"]]
    assert ts == sorted(ts) and ts[-1] - ts[0] == 600


@pytest.mark.parametrize(
    "text, needle",
    [
        ("", "dosya boş"),
        ("id,t,la,lo\nA,10:00,1,1\n", "başlık"),
        ("track_id,time,lat,lon,extra\nA,10:00,1,1,x\n", "başlık"),
        ("﻿track_id,time,lat,lon\nA,10:00,1,1\n", "başlık"),  # BOM
        (HDR + "A,10:00,1\n", "4 hücre"),
        (HDR + "A,10:00,1,1\n\nA,10:05,1,1\n", "4 hücre"),  # ortada boş satır
        (HDR + "A,zz,1,1\n", "HH:MM"),
        (HDR + "A,10:5,1,1\n", "HH:MM"),
        (HDR + "A,10:00,x,1\n", "sayı olmalı"),
        (HDR + "A,10:00,1e3,1\n", "sayı olmalı"),
        (HDR + "A,10:00,999,1\n", "aralık dışı"),
        (HDR + ",10:00,1,1\n", "track_id"),
        (HDR + "A,10:00,1,1\nA,10:00,1,2\n", "tekrarlanıyor"),
        (HDR + "A,10:10,1,1\nA,10:05,1,2\n", "geriye gidiyor"),
        (HDR, "en az bir iz noktası"),
    ],
)
def test_tracks_csv_rejects(text, needle):
    with pytest.raises(DatasetError, match=needle):
        parse_tracks(text, DATE)


def test_tracks_csv_error_lists_line_numbers():
    with pytest.raises(DatasetError) as ei:
        parse_tracks(HDR + "A,10:00,1,1\nA,zz,1,1\nB,10:05,999,1\n", DATE)
    msg = str(ei.value)
    assert "2 hatalı satır" in msg and "satır 3" in msg and "satır 4" in msg


# ---------------------------------------------------------------------------------------------- load_dataset
def test_load_dataset_is_fail_fast(tmp_path):
    assert load_dataset(Settings(data_dir="")).images == {}  # DATA_DIR yok: eski/demo akışı için boş
    with pytest.raises(DatasetError, match="DATA_DIR bulunamadı"):
        load_dataset(Settings(data_dir=str(tmp_path / "yok")))
    with pytest.raises(DatasetError, match="image_meta.json yok"):
        load_dataset(Settings(data_dir=str(tmp_path)))
    (tmp_path / "image_meta.json").write_text(json.dumps({"img_1": ENTRY}))
    (tmp_path / "tracks.csv").write_text(HDR + "A,10:00,1,1\nA,zz,1,1\n")
    with pytest.raises(DatasetError, match="tracks.csv"):
        load_dataset(Settings(data_dir=str(tmp_path)))
    (tmp_path / "tracks.csv").write_text(HDR + "A,10:00,1,1\n")
    ds = load_dataset(Settings(data_dir=str(tmp_path)))
    assert list(ds.images) == ["img_1"] and list(ds.tracks) == ["A"]
