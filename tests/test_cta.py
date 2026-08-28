from pathlib import Path

from PIL import Image

from jaguartv_factory.core import connect_db
from jaguartv_factory.cta import (
    delete_cta_asset,
    import_cta_path,
    list_cta_assets,
    rename_active_cta_assets,
    select_random_cta,
)
from jaguartv_factory import cli


def make_config(tmp_path: Path) -> dict:
    return {
        "_root": str(tmp_path),
        "run": {"workspace": "workspace"},
        "storage": {"root": "workspace/server_media"},
    }


def test_cta_import_assigns_orientation_name_and_preserves_original_name(tmp_path: Path):
    config = make_config(tmp_path)
    source = tmp_path / "Minha CTA final.jpg"
    Image.new("RGB", (1600, 900), "green").save(source)

    item = import_cta_path(config, source, actor="test")

    assert item["name"] == "横版1.jpg"
    assert item["original_name"] == "Minha CTA final.jpg"
    assert item["media_type"] == "image"
    assert item["orientation"] == "landscape"
    assert item["duration_sec"] == 2.0
    assert item["preview_url"].startswith("/media/cta/")
    assert item["preview_url"].endswith("/%E6%A8%AA%E7%89%881.jpg") or item["preview_url"].endswith("/横版1.jpg")
    assert select_random_cta(config, "landscape")["id"] == item["id"]


def test_cta_import_numbers_images_and_videos_in_one_orientation_sequence(tmp_path: Path, monkeypatch):
    config = make_config(tmp_path)
    first = tmp_path / "first.jpg"
    second = tmp_path / "second.mp4"
    Image.new("RGB", (1600, 900), "green").save(first)
    second.write_bytes(b"video")
    monkeypatch.setattr(
        "jaguartv_factory.cta._probe",
        lambda path: (
            ("video", 1920, 1080, 4.0, "video/mp4")
            if path.suffix == ".mp4"
            else ("image", 1600, 900, 2.0, "image/jpeg")
        ),
    )

    first_item = import_cta_path(config, first)
    second_item = import_cta_path(config, second)

    assert first_item["name"] == "横版1.jpg"
    assert second_item["name"] == "横版2.mp4"
    assert Path(select_random_cta(config, "landscape")["file_path"]).name in {"横版1.jpg", "横版2.mp4"}


def test_legacy_cta_assets_are_renamed_without_losing_original_names(tmp_path: Path):
    config = make_config(tmp_path)
    cta_dir = tmp_path / "workspace" / "server_media" / "cta"
    cta_dir.mkdir(parents=True)
    landscape = cta_dir / "legacy-landscape.jpg"
    portrait = cta_dir / "legacy-portrait.jpg"
    Image.new("RGB", (1600, 900), "green").save(landscape)
    Image.new("RGB", (900, 1600), "blue").save(portrait)
    connection = connect_db(config)
    for identifier, path, orientation, created_at in (
        ("landscape-1", landscape, "landscape", "2026-08-01T00:00:00+00:00"),
        ("portrait-1", portrait, "portrait", "2026-08-02T00:00:00+00:00"),
    ):
        connection.execute(
            """
            INSERT INTO cta_assets(
              id,name,original_name,file_path,media_type,orientation,mime_type,width,height,
              duration_sec,size_bytes,sha256,status,created_by,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'ACTIVE',?,?,?)
            """,
            (
                identifier, path.name, path.name, str(path), "image", orientation, "image/jpeg",
                1600 if orientation == "landscape" else 900,
                900 if orientation == "landscape" else 1600,
                2.0, path.stat().st_size, identifier, "test", created_at, created_at,
            ),
        )
    connection.commit()

    renamed = rename_active_cta_assets(config, actor="test")

    assert [item["name"] for item in renamed] == ["横版1.jpg", "竖版1.jpg"]
    assert not landscape.exists()
    assert not portrait.exists()
    assert (cta_dir / "横版1.jpg").is_file()
    assert (cta_dir / "竖版1.jpg").is_file()
    rows = connection.execute(
        "SELECT name,original_name,file_path FROM cta_assets ORDER BY orientation"
    ).fetchall()
    assert [(row["name"], row["original_name"], Path(row["file_path"]).name) for row in rows] == [
        ("横版1.jpg", "legacy-landscape.jpg", "横版1.jpg"),
        ("竖版1.jpg", "legacy-portrait.jpg", "竖版1.jpg"),
    ]


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
