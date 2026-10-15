import mock

from legacyapp.config import load


def test_load(tmp_path):
    p = tmp_path / "c.yml"
    p.write_text("a: 1")
    assert load(str(p)) == {"a": 1}
    assert mock
