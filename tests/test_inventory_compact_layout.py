from pathlib import Path


def test_inventory_content_column_and_output_menu_are_compact() -> None:
    styles = Path("src/jaguartv_factory/web/styles.css").read_text(encoding="utf-8")

    assert ".inventory-table table { width: max-content; min-width: 0; }" in styles
    assert (
        ".inventory-table th:nth-child(2), .inventory-table td:nth-child(2) "
        "{ width: 390px; min-width: 390px; max-width: 390px;"
    ) in styles
    assert ".content-cell > div:last-child { width: 305px; min-width: 0; }" in styles
    assert ".output-menu { position: relative; display: inline-block; }" in styles
    assert "width: max-content; min-width: min(300px, calc(100vw - 32px));" in styles
    assert "position: absolute; top: 100%; right: 0; z-index: 20;" in styles
    assert ".output-menu-row { min-height: 27px; padding: 2px 0;" in styles
    assert ".output-menu-row .table-action { min-height: 23px;" in styles
