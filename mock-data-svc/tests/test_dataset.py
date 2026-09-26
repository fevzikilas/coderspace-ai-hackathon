import json

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.loaders.field_reports import extract_coords, parse_reports
from app.loaders.strict import DatasetError
from app.loaders.zones import parse_zones, slugify
from app.main import create_app

# Şema örneklerinin TEMİZ JSON hali (kullanıcının verdiği örnek dosyalar bozuk JSON'du: sondaki virgül, dizisiz nesne akışı,
# string içinde ham satır sonu — bunlar artık REDDEDİLİR, bkz. test_user_sample_defects_are_rejected).
ZONES_SAMPLE = json.dumps({
    "base": {"name": "Merkez Us", "lat": 39.92184, "lon": 32.85306},
    "zones": [{"name": "Kuzey Yolu", "center": [39.950586, 32.853060]}, {"name": "Dogu Yolu", "center": [39.921840, 32.890542]}],
})
REPORTS_SAMPLE = json.dumps([
    {"time": "13:05", "source": "official", "text": "39.9374N 32.8483E civarinda 1 kamyon goruldu, yukleri tespit edilemedi."},
    {"time": "11:55", "source": "third_party", "text": "Planli tatbikat nedeniyle gun icinde bolgede dost unsurlar bulunacak."},
])
ZONES_SAMPLE_RAW = """{"base": {"name": "Merkez Us", "lat": 39.92184, "lon": 32.85306},
 "zones": [{"name": "Kuzey Yolu", "center": [39.950586, 32.853060]},

 ]}"""
REPORTS_SAMPLE_RAW = """{"time": "13:05", "source": "official",
 "text": "1 kamyon
          goruldu."},

{"time": "11:55", "source": "third_party", "text": "x"}
"""


def test_user_sample_defects_are_rejected():
    for raw, fn in ((ZONES_SAMPLE_RAW, parse_zones), (REPORTS_SAMPLE_RAW, lambda t: parse_reports(t, "2025-06-01"))):
        with pytest.raises(DatasetError, match="geçersiz JSON"):
            fn(raw)


@pytest.mark.parametrize(
    "text, needle",
    [
        ('{"zones": []}', "base"),
        ('{"base": {"name": "B", "lat": 1, "lon": 1}, "zones": []}', "zones"),  # en az bir bölge
        ('{"base": {"name": "B", "lat": 1, "lon": 1, "x": 1}, "zones": [{"name": "A", "center": [1, 2]}]}', "x"),  # bilinmeyen alan
        ('{"base": {"name": "B", "lat": 91, "lon": 1}, "zones": [{"name": "A", "center": [1, 2]}]}', "lat"),
        ('{"base": {"name": "B", "lat": 1, "lon": 1}, "zones": [{"name": "A", "center": [1]}]}', "center"),
        ('{"base": {"name": "B", "lat": 1, "lon": 1}, "zones": [{"name": "A", "center": [1, 2], "id": "a"}]}', "id"),
        ('{"base": {"name": "B", "lat": "1", "lon": 1}, "zones": [{"name": "A", "center": [1, 2]}]}', "lat"),  # string sayı yok
        ('{"base": {"name": "B", "lat": 1, "lon": 1}, "zones": [{"name": "Kuzey Yolu", "center": [1, 2]}, {"name": "kuzey  yolu", "center": [1, 3]}]}', "aynı zone_id"),
    ],
)
def test_zones_strict_rejections(text, needle):
    with pytest.raises(DatasetError, match=needle):
        parse_zones(text)


@pytest.mark.parametrize(
    "text, needle",
    [
        ('{"reports": []}', "list"),  # sarmalayıcı biçimi artık yok
        ('[{"time": "13:05", "source": "official", "text": "x", "zone": "kuzey"}]', "zone"),  # bilinmeyen alan (rapor bölgeye bağlı değil)
        ('[{"time": "13:05", "source": "police", "text": "x"}]', "source"),
        ('[{"time": "1:05", "source": "official", "text": "x"}]', "time"),
        ('[{"time": "13:05", "source": "official", "text": ""}]', "text"),
        ('[{"time": "13:05", "source": "official", "text": " x"}]', "boşluk"),
        ('[{"time": "13:05", "source": "official"}]', "text"),
        (json.dumps([{"time": "13:05", "source": "official", "text": "x" * 2001}]), "text"),
        ('[{"time": "13:05", "source": "official", "text": "x"},]', "geçersiz JSON"),
    ],
)
def test_field_reports_strict_rejections(text, needle):
    with pytest.raises(DatasetError, match=needle):
        parse_reports(text, "2025-06-01")


def test_empty_report_list_is_valid():
    assert parse_reports("[]", "2025-06-01") == []


def test_zones_sample_parses_and_slugs():
    base, zones = parse_zones(ZONES_SAMPLE)
    assert base.name == "Merkez Us" and base.radius_m is None
    assert [z.zone_id for z in zones] == ["kuzey-yolu", "dogu-yolu"]
    assert 3100 < zones[0].distance_from_base_m < 3300 and zones[0].bearing_from_base_deg < 1  # ~3.2 km kuzey
    assert 85 < zones[1].bearing_from_base_deg < 95
    assert slugify("Güneydoğu Yolu İkinci") == "guneydogu-yolu-ikinci"


def test_field_reports_sample_parses_time_source_and_coords():
    reps = parse_reports(REPORTS_SAMPLE, "2025-06-01")
    assert [r.time for r in reps] == ["11:55", "13:05"]  # zamana göre sıralı
    assert reps[1].source == "official" and (reps[1].lat, reps[1].lon) == (39.9374, 32.8483)
    assert reps[1].text == "39.9374N 32.8483E civarinda 1 kamyon goruldu, yukleri tespit edilemedi."
    assert reps[0].lat is None  # genel rapor (konumsuz)
    assert extract_coords("39.9N, 32.8E civarinda") == (39.9, 32.8)
    assert extract_coords("konum yok") is None


@pytest.fixture
def client(tmp_path):
    (tmp_path / "zones.json").write_text(ZONES_SAMPLE)
    (tmp_path / "field_reports.json").write_text(REPORTS_SAMPLE)
    with TestClient(create_app(Settings(data_dir=str(tmp_path)))) as c:
        yield c


def test_base_zones_and_assign(client):
    b = client.get("/base").json()
    assert b["name"] == "Merkez Us" and b["radius_m"] == 1500 and b["source"] == "dataset"
    zs = client.get("/zones").json()
    assert zs["source"] == "dataset" and [z["zone_id"] for z in zs["zones"]] == ["kuzey-yolu", "dogu-yolu"]
    a = client.post("/zones/assign", json={"points": [{"lat": 39.99, "lon": 32.86}, {"lat": 39.92, "lon": 32.95}]}).json()["assignments"]
    assert [x["zone_id"] for x in a] == ["kuzey-yolu", "dogu-yolu"]
    assert client.get("/zones/kuzey-yolu").json()["center"]["lat"] == 39.950586
    assert client.get("/zones/yok").status_code == 404


def test_reports_respect_as_of_and_location(client):
    # olay 14:10: iki rapor da geçmişte; konumlu olan (39.9374N 32.8483E) Kuzey Yolu merkezine ~1.4 km
    r = client.get("/reports/kuzey-yolu?as_of=14:10").json()
    assert r["source"] == "dataset" and r["as_of"] == "2025-06-01T14:10:00Z"
    assert [i["time"] for i in r["items"]] == ["11:55", "13:05"]
    assert r["items"][1]["distance_m"] < 2500 and r["items"][1]["age_min"] == 65.0
    # 12:00: 13:05 raporu HENÜZ yazılmamış -> gelecek bilgisi sızmaz
    assert [i["time"] for i in client.get("/reports/kuzey-yolu?as_of=12:00").json()["items"]] == ["11:55"]
    # konumlu rapor doğu bölgesinden uzak; genel rapor (tatbikat) her yerde görünür
    east = client.get("/reports/dogu-yolu?as_of=14:10").json()["items"]
    assert [i["time"] for i in east] == ["11:55"]
    # olay konumuna göre süz
    near_event = client.get("/reports/dogu-yolu?as_of=14:10&lat=39.9374&lon=32.8483&radius_km=1").json()["items"]
    assert [i["time"] for i in near_event] == ["11:55", "13:05"]
    assert client.get("/reports/kuzey-yolu?as_of=zz").status_code == 422


def test_intel_placeholder_for_dataset_and_legacy_still_works(client):
    i = client.get("/intel/kuzey-yolu").json()
    assert i["items"] == [] and i["placeholder"] is True
    legacy = client.get("/intel/ZONE-ALPHA?as_of=14:10").json()
    assert legacy["items"] and all(x["confidence"] == "low" for x in legacy["items"])
    assert client.get("/reports/ZONE-ALPHA").json()["source"] == "legacy"


def test_bad_or_missing_files_stop_the_service_but_no_data_dir_is_legacy(tmp_path):
    from app.loaders.strict import DatasetError

    (tmp_path / "zones.json").write_text("bu json degil")
    (tmp_path / "field_reports.json").write_text("[]")
    with pytest.raises(DatasetError, match="zones.json: geçersiz JSON"):
        with TestClient(create_app(Settings(data_dir=str(tmp_path)))):
            pass
    with pytest.raises(DatasetError, match="DATA_DIR bulunamadı"):
        with TestClient(create_app(Settings(data_dir=str(tmp_path / "yok")))):
            pass
    (tmp_path / "field_reports.json").unlink()
    (tmp_path / "zones.json").write_text('{"base":{"name":"B","lat":1,"lon":1},"zones":[{"name":"A","center":[1,1.1]}]}')
    with pytest.raises(DatasetError, match="field_reports.json yok"):
        with TestClient(create_app(Settings(data_dir=str(tmp_path)))):
            pass
    with TestClient(create_app(Settings(data_dir=""))) as c:  # DATA_DIR yok: eski/demo akışı
        assert c.get("/zones").json()["source"] == "legacy"
        assert c.get("/base").json()["source"] == "legacy"


def test_reports_are_related_to_zones_by_coordinates_and_by_named_road(tmp_path):
    (tmp_path / "zones.json").write_text('{"base":{"name":"B","lat":39.92184,"lon":32.85306},"zones":['
                                         '{"name":"Kuzey Yolu","center":[39.950586,32.85306]},{"name":"Kuzeydogu Yolu","center":[39.9445,32.9]},'
                                         '{"name":"Dogu Yolu","center":[39.92184,32.890542]}]}')
    (tmp_path / "field_reports.json").write_text(json.dumps([
        {"time": "10:00", "source": "third_party", "text": "Kuzey yolunda yol calismasi var."},
        {"time": "10:05", "source": "third_party", "text": "Kuzeydogu yolundaki araclar cok hizli."},
        {"time": "10:10", "source": "third_party", "text": "Dogu yolu kapali."},
        {"time": "10:15", "source": "official", "text": "39.9450N 32.8530E civarinda 1 arac goruldu."},
        {"time": "10:20", "source": "official", "text": "Genel uyari: sis var."},
    ]))
    with TestClient(create_app(Settings(data_dir=str(tmp_path)))) as c:
        def times(zone):
            return [i["time"] for i in c.get(f"/reports/{zone}?as_of=11:00").json()["items"]]

        # 'dogu yolu' kalıbı 'kuzeydogu yolundaki' içinde EŞLEŞMEZ (kelime başı sınırı); ek almış 'yolunda/yolundaki' eşleşir
        assert times("kuzey-yolu") == ["10:00", "10:15", "10:20"]  # koordinat da en yakın bölge = Kuzey Yolu
        assert times("kuzeydogu-yolu") == ["10:05", "10:20"]
        assert times("dogu-yolu") == ["10:10", "10:20"]
