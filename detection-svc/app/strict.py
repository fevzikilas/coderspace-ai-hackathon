"""Katı veri okuma yardımcıları: bozuk veri SESSİZCE düzeltilmez/atlanmaz, `DatasetError` ile reddedilir.

(Eskiden burada 'toleranslı' JSON onarımı vardı; kaldırıldı. Bozuk bir dosyayı bilinçli olarak onarmak için
 `scripts/repair_dataset.py` kullanılır — çalışma zamanı asla onarmaz.)
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

_MAX_SHOWN = 8


class DatasetError(ValueError):
    """Veri dosyası biçim/doğrulama hatası (mesaj dosya + alan yolunu içerir)."""


class StrictModel(BaseModel):
    """Bilinmeyen alan yasak, tür dönüşümü yok (ör. "960" -> int reddedilir)."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise DatasetError(f"yinelenen anahtar: {k!r}")
        out[k] = v
    return out


def _reject_constant(name: str) -> Any:
    raise DatasetError(f"geçersiz sayı sabiti: {name}")


def parse_json_strict(text: str, source: str) -> Any:
    """RFC 8259 JSON: sondaki virgül/eksik parantez/yinelenen anahtar/NaN/Infinity hata verir."""
    try:
        return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{source}: geçersiz JSON (satır {exc.lineno}, sütun {exc.colno}): {exc.msg}") from exc
    except DatasetError as exc:
        raise DatasetError(f"{source}: {exc}") from exc


def validation_message(source: str, exc: ValidationError) -> str:
    errs = exc.errors()
    lines = []
    for e in errs[:_MAX_SHOWN]:
        loc = ".".join(str(x) for x in e["loc"]) or "(kök)"
        lines.append(f"  - {loc}: {e['msg']}")
    if len(errs) > _MAX_SHOWN:
        lines.append(f"  … ve {len(errs) - _MAX_SHOWN} hata daha")
    return f"{source}: {len(errs)} doğrulama hatası\n" + "\n".join(lines)
