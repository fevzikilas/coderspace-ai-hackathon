"""repair_dataset.py: bilinen kusurları onarır, geçerli JSON'a dokunmaz, onarılamazsa hata verir. (pytest scripts/test_repair_dataset.py)"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from repair_dataset import main, repair_json  # noqa: E402


def test_fixes_known_defects():
    fixed, fx = repair_json('{"zones": [\n {"name": "A", "center": [1, 2]},\n {"name": "B", "center": [3, 4]},\n ]}')
    assert [z["name"] for z in json.loads(fixed)["zones"]] == ["A", "B"] and any("virgül" in f for f in fx)
    fixed, fx = repair_json('{"time": "13:05", "text": "1 kamyon\n          goruldu"}\n \n{"time": "11:55", "text": "x"}')
    r = json.loads(fixed)
    assert [x["time"] for x in r] == ["13:05", "11:55"] and r[0]["text"] == "1 kamyon goruldu"
    fixed, fx = repair_json('"img_1": {"width_px": 10}')
    assert json.loads(fixed) == {"img_1": {"width_px": 10}} and any("dış {}" in f for f in fx)


def test_valid_json_is_untouched_and_strings_with_commas_survive():
    text = '{"t": "a, }", "u": [1, 2]}'
    assert repair_json(text) == (text, [])


def test_escaped_quotes_and_commas_inside_strings_are_preserved():
    text = '{"a": "he said \\"x, ]\\" ok,", "b": [1, 2,]}'
    fixed, fx = repair_json(text)
    assert json.loads(fixed) == {"a": 'he said "x, ]" ok,', "b": [1, 2]} and len(fx) == 1


def test_unrepairable_raises():
    with pytest.raises(ValueError, match="hâlâ geçersiz"):
        repair_json("bu json degil")


def test_cli_never_repairs_in_place(tmp_path):
    src, dst = tmp_path / "in", tmp_path / "out"
    src.mkdir()
    (src / "a.json").write_text('{"x": [1,],}')
    (src / "t.csv").write_text("a,b\n")
    assert main(["repair", str(src), str(src)]) == 2
    assert main(["repair", str(src), str(dst)]) == 0
    assert json.loads((dst / "a.json").read_text()) == {"x": [1]} and (src / "a.json").read_text() == '{"x": [1,],}'
    assert (dst / "t.csv").exists()
