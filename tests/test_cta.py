from pathlib import Path

from PIL import Image

from jaguartv_factory.core import connect_db
from jaguartv_factory.cta import delete_cta_asset, import_cta_path, list_cta_assets, select_random_cta
from jaguartv_factory import cli


def make_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
    }


def test_cta_import_preserves_name_and_classifies_orientation(tmp_path: Path):
    config = make_config(tmp_path)
    source = tmp_path / "Minha CTA final.jpg"
    Image.new("RGB", (1600, 900), "green").save(source)

    item = import_cta_path(config, source, actor="test")

    assert item["name"] == "Minha_CTA_final.jpg"
    assert item["original_name"] == "Minha CTA final.jpg"
    assert item["media_type"] == "image"
    assert item["orientation"] == "landscape"
    assert item["duration_sec"] == 2.0
    assert item["preview_url"].startswith("/media/cta/")
    assert select_random_cta(config, "landscape")["id"] == item["id"]


def test_cta_inventory_is_separate_from_candidates_and_delete_is_recoverable(tmp_path: Path):
    config = make_config(tmp_path)
    source = tmp_path / "cta.png"
    Image.new("RGB", (720, 1280), "blue").save(source)
    item = import_cta_path(config, source)

    connection = connect_db(config)
    assert connection.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 0
    assert len(list_cta_assets(config)) == 1

    result = delete_cta_asset(config, item["id"], actor="test")

    assert result["status"] == "DELETED"
    assert list_cta_assets(config) == []
    row = connect_db(config).execute("SELECT status,file_path FROM cta_assets WHERE id=?", (item["id"],)).fetchone()
    assert row["status"] == "DELETED"
    assert Path(row["file_path"]).is_file()


def test_cta_cli_directory_ignores_macos_sidecars_and_unsupported_files(tmp_path: Path, capsys):
    config_path = tmp_path / "config" / "pipeline.yaml"
    config_path.parent.mkdir()
    config_path.write_text("run:\n  workspace: workspace\nstorage:\n  root: workspace/server_media\n", encoding="utf-8")
    source_dir = tmp_path / "cta-import"
    source_dir.mkdir()
    Image.new("RGB", (720, 1280), "red").save(source_dir / "real.jpg")
    (source_dir / "._broken.mp4").write_bytes(b"macos-sidecar")
    (source_dir / "notes.txt").write_text("ignore", encoding="utf-8")

    assert cli.main(["--config", str(config_path), "cta-import", str(source_dir)]) == 0

    assert len(list_cta_assets({"_root": str(tmp_path), "run": {"workspace": "workspace"}, "storage": {"root": "workspace/server_media"}})) == 1
    assert '"original_name": "real.jpg"' in capsys.readouterr().out
