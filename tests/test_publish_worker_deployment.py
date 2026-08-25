from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_publish_worker_is_part_of_install_and_sync_contract() -> None:
    installer = ROOT / "scripts" / "install-publish-worker.sh"
    first_install = (ROOT / "scripts" / "server-install.sh").read_text(encoding="utf-8")
    sync = (ROOT / "scripts" / "server-sync.sh").read_text(encoding="utf-8")

    assert installer.is_file()
    script = installer.read_text(encoding="utf-8")
    assert "jaguartv-youtube-publish-worker" in script
    assert "publish-worker --sleep" in script
    assert 'systemctl enable "$SERVICE_NAME"' in script
    assert 'systemctl restart "$SERVICE_NAME"' in script
    assert "install-publish-worker.sh" in first_install
    assert "install-publish-worker.sh" in sync
