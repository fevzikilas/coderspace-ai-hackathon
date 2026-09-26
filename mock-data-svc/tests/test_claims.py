"""Rapor metninden iddia çıkarımı: gerçek veri şablonları."""
import json
from pathlib import Path

import pytest

from app.claims import parse_claim

REAL = Path(__file__).resolve().parents[2] / "data" / "REAL" / "field_reports.json"


def c(text):
    return parse_claim(text)


def test_stationary_truck_with_duration():
    x = c("39.9N 32.8E civarinda bir kamyon uzun suredir hareketsiz duruyor.")
    assert (x["kind"], x["types"], x["count"], x["motion"], x["min_duration_min"]) == ("vehicle", ["truck"], 1, "stationary", 30)
    assert c("39.9N 32.8E konumundaki otomobil bir saatten uzun suredir yerinden ayrilmadi.")["min_duration_min"] == 60


def test_counts_and_hearsay():
    x = c("39.9N 32.8E cevresinde 3 kamyon bulundugu yonunde ihbar alindi.")
    assert x["count"] == 3 and x["types"] == ["truck"] and x["hearsay"] and x["count_relation"] == "exact"
    assert c("39.9N 32.8E yakininda 7 kamyonun durdugu bildirildi.")["count"] == 7
    conv = c("39.9N 32.8E civarinda 3 araclik bir kamyon konvoyu ilerliyor.")
    assert conv["count"] == 3 and conv["convoy"] and conv["motion"] == "moving"


def test_identity_claims_are_flagged_not_trusted():
    x = c("39.9N 32.8E konumundan usse dogru ilerleyen otomobil planli ikmal aracidir, kimlik teyidi yapilmistir.")
    assert x["identity"] == "friendly" and x["motion"] == "toward_base" and x["types"] == ["car"]
    assert c("39.9N 32.8E civarindaki mavi arac dost devriye unsurudur, kimlik teyidi yapilmistir.")["color"] == "mavi"


def test_zone_level_and_context_claims():
    assert c("Kuzey Yolu bolgesinde agir arac hareketi yok, yalnizca binek araclar goruluyor.")["kind"] == "zone_no_heavy"
    assert c("Kuzeybati Yolu bolgesinde trafik akisi normal seyrediyor.")["kind"] == "zone_activity"
    assert c("Sabah devriyesi Dogu Yolu bolgesinde olagandisi bir durum bildirmedi.")["kind"] == "zone_activity"
    for text, why in [("Hava acik, gorus mesafesi iyi.", "hava"), ("Dun gece Dogu Yolu cevresinde arac hareketliligi oldugu yonunde dogrulanmamis bir ihbar var.", "geçmiş"),
                      ("Lojistik konvoyu yakit ikmali icin planlanan saatte yola cikacak.", "plan"), ("Dogu Yolu bolgesindeki devriyeyle telsiz baglantisi 40 dakikadir kurulamiyor.", "haberleşme"),
                      ("Dogu Yolu cevresinden gelen bir ihbar incelendi, dogrulanamadi.", "doğrulanamadığını")]:
        x = c(text)
        assert x["kind"] == "context" and why in x["context_reason"], text


def test_density_baseline_is_not_a_reported_count():
    x = c("39.9N 32.8E cevresinde trafik olagandan yogun; bu bolgede genellikle 4 arac civari gorulur.")
    assert x["kind"] == "vehicle" and x["baseline"] == 4 and x["count"] is None and x["count_relation"] == "more_than_usual"


def test_heavy_vehicle_phrases():
    assert c("Bir kaynak, 39.9N 32.8E konumunda agir bir aracin beklemede oldugunu iletti.")["types"] == ["truck", "bus"]
    assert c("39.9N 32.8E civarinda 1 agir arac (kamyon/otobus) gozlendi.")["types"] == ["truck", "bus"]


@pytest.mark.skipif(not REAL.exists(), reason="gerçek veri yok")
def test_all_real_reports_are_classified_consistently():
    rep = json.loads(REAL.read_text(encoding="utf-8"))
    kinds = {}
    for r in rep:
        k = c(r["text"])["kind"]
        kinds[k] = kinds.get(k, 0) + 1
        # konumlu (koordinatlı) her rapor bir araç iddiasıdır; koordinatsızlar asla 'vehicle' değildir
        has_coord = any(ch.isdigit() for ch in r["text"]) and "N " in r["text"] and "E" in r["text"]
        assert (k == "vehicle") == has_coord, r["text"]
    assert kinds == {"vehicle": 72, "zone_activity": 16, "context": 43, "zone_no_heavy": 6}
