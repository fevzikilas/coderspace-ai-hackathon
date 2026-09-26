#!/usr/bin/env python3
"""Bozuk (katı loader'ın reddettiği) veri dosyalarını BİLİNÇLİ olarak onarır. Çalışma zamanı asla onarmaz.

  python scripts/repair_dataset.py <girdi_klasörü> <çıktı_klasörü>

Yalnızca .json dosyalarına dokunur; girdiyi DEĞİŞTİRMEZ, onarılmış kopyayı çıktı klasörüne yazar (diğer dosyalar aynen kopyalanır)
ve her onarımı listeler. Bilinen kusurlar: sondaki virgül · string içinde ham satır sonu · dizisiz nesne akışı ({..}{..} → [..]) ·
eksik dış {} (`"img_1": {...}` parçası). Onarım sonrası dosya `json.loads` ile doğrulanır; hâlâ geçersizse hata verir.
Onarılmış klasör ardından yine KATI loader'lardan geçer — onarım, doğrulamanın yerine geçmez.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path


def _scan(text: str):
    """(indeks, karakter, string_içinde_mi) üretir; kaçışlı tırnakları doğru işler."""
    in_str = esc = False
    for i, ch in enumerate(text):
        if in_str:
            yield i, ch, True
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
        else:
            if ch == '"':
                in_str = True
            yield i, ch, False


def _fix_raw_newlines(text: str) -> tuple[str, int]:
    """String içindeki ham satır sonlarını (ve ardındaki girinti boşluklarını) tek boşluğa çevirir."""
    out, n, skip_ws = [], 0, False
    for _, ch, ins in _scan(text):
        if ins and ch in "\r\n\t ":
            if ch in "\r\n":
                n += 1
                if not skip_ws:
                    out.append(" ")
                skip_ws = True
            elif not skip_ws:
                out.append(ch)
            continue
        skip_ws = False
        out.append(ch)
    return "".join(out), n


def _strip_trailing_commas(text: str) -> tuple[str, int]:
    """`,` + boşluk + `}`/`]` → virgül silinir; YALNIZCA string dışında (metin içindeki ", }" korunur)."""
    drop: set[int] = set()
    marks = [(i, ch) for i, ch, ins in _scan(text) if not ins and not ch.isspace()]
    for (i, ch), (_, nxt) in zip(marks, marks[1:]):
        if ch == "," and nxt in "}]":
            drop.add(i)
    return "".join(c for i, c in enumerate(text) if i not in drop), len(drop)


def _top_level_objects(text: str) -> int:
    depth = n = 0
    for _, ch, ins in _scan(text):
        if ins:
            continue
        if ch == "{":
            n += depth == 0
            depth += 1
        elif ch == "}":
            depth -= 1
    return n


def repair_json(text: str) -> tuple[str, list[str]]:
    fixes: list[str] = []
    text = text.lstrip("﻿")
    text, n = _fix_raw_newlines(text)
    if n:
        fixes.append(f"string içinde {n} ham satır sonu boşluğa çevrildi")
    text, n = _strip_trailing_commas(text)
    if n:
        fixes.append(f"{n} sondaki virgül silindi")
    stripped = text.strip()
    if stripped.startswith('"'):
        text = "{" + stripped + "}"
        fixes.append('dış {} eklendi ("anahtar": {...} parçası)')
    elif stripped.startswith("{") and _top_level_objects(stripped) > 1:
        text = "[" + re.sub(r"\}\s*,?\s*\{", "},{", stripped) + "]"
        fixes.append("dizisiz nesne akışı [ ... ] içine alındı")
    try:
        json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"onarım sonrası hâlâ geçersiz JSON (satır {exc.lineno}, sütun {exc.colno}): {exc.msg}") from exc
    return text, fixes


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    if not src.is_dir() or src.resolve() == dst.resolve():
        print("girdi klasörü yok ya da çıktı girdiyle aynı (yerinde onarım yapılmaz)")
        return 2
    dst.mkdir(parents=True, exist_ok=True)
    bad = 0
    for f in sorted(src.iterdir()):
        target = dst / f.name
        if f.is_dir():
            shutil.copytree(f, target, dirs_exist_ok=True)
        elif f.suffix == ".json":
            raw = f.read_text(encoding="utf-8")
            try:
                fixed, fixes = repair_json(raw)
            except ValueError as exc:
                print(f"✗ {f.name}: {exc}")
                bad += 1
                continue
            target.write_text(fixed if fixes else raw, encoding="utf-8")
            print(f"{'✓' if not fixes else '~'} {f.name}: " + ("zaten geçerli" if not fixes else "; ".join(fixes)))
        else:
            shutil.copy2(f, target)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
