"""field_reports.json okuyucu — KATI (SADECE parse + doğrulama).

Biçim: [{"time": "13:05", "source": "official" | "third_party", "text": "..."}, ...]  (yalnızca bu üç alan; boş liste geçerli).
Rapor bir bölgeye bağlı DEĞİLDİR; konum yalnızca metinde "39.9374N 32.8483E civarinda ..." biçiminde geçebilir — bulunursa
çıkarılır (yoksa rapor genel/konumsuzdur). Rapor metni GÜVENİLMEYEN VERİdir: burada yalnızca uzunluk/tip doğrulanır.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, StringConstraints, TypeAdapter, ValidationError

from ..timeutil import parse_ts
from .strict import DatasetError, StrictModel, parse_json_strict, validation_message

_COORD_RE = re.compile(r"(-?\d{1,2}\.\d+)\s*°?\s*N\b[\s,;]*(-?\d{1,3}\.\d+)\s*°?\s*E\b", re.IGNORECASE)
MAX_TEXT = 2000


@dataclass(frozen=True)
class FieldReport:
    ts: float
    time: str
    source: str  # official | third_party
    text: str
    lat: float | None
    lon: float | None


def _no_edge_space(v: str) -> str:
    if v != v.strip():
        raise ValueError("metnin başında/sonunda boşluk var")
    return v


class ReportModel(StrictModel):
    time: Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]  # "HH:MM"
    source: Literal["official", "third_party"]
    text: Annotated[str, Field(min_length=1, max_length=MAX_TEXT), AfterValidator(_no_edge_space)]


_FILE = TypeAdapter(Annotated[list[ReportModel], Field(max_length=100_000)])


def extract_coords(text: str) -> tuple[float, float] | None:
    m = _COORD_RE.search(text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def parse_reports(text: str, base_date: str, source: str = "field_reports.json") -> list[FieldReport]:
    data = parse_json_strict(text, source)
    try:
        items = _FILE.validate_python(data)
    except ValidationError as exc:
        raise DatasetError(validation_message(source, exc)) from exc
    out: list[FieldReport] = []
    for r in items:
        loc = extract_coords(r.text)
        out.append(FieldReport(parse_ts(r.time, base_date), r.time, r.source, r.text, loc[0] if loc else None, loc[1] if loc else None))
    out.sort(key=lambda x: x.ts)
    return out


def load_reports(path: Path, base_date: str) -> list[FieldReport]:
    return parse_reports(path.read_text(encoding="utf-8"), base_date, path.name)


__all__ = ["FieldReport", "parse_reports", "load_reports", "extract_coords"]
