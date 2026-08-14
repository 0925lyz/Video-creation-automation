from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import load_config
from .sessions import PLATFORM_LOGIN_URLS, check_session, save_session, session_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m jaguartv_factory.session_login",
        description="Open a real browser for manual QR/login and persist platform session state.",
    )
    parser.add_argument("--config", default="config/pipeline.yaml")
    parser.add_argument("--platform", required=True, choices=sorted(PLATFORM_LOGIN_URLS))
    parser.add_argument("--account", required=True)
    parser.add_argument("--url", default="")
    parser.add_argument("--headless", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(Path(args.config))
    base = session_dir(config, args.platform, args.account)
    base.mkdir(parents=True, exist_ok=True)
    profile_dir = base / "browser_profile"
    state_path = base / "storage_state.json"
    login_url = args.url or PLATFORM_LOGIN_URLS[args.platform]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print(
            "Playwright is not installed. Run:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium",
            file=sys.stderr,
        )
        return 2

    print(f"Opening {login_url}")
    print("请在打开的浏览器里人工扫码/验证登录；登录完成后回到终端按 Enter 保存登录态。")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(
            str(profile_dir),
            headless=args.headless,
            viewport={"width": 1366, "height": 900},
            locale="zh-CN",
        )
        page = browser.pages[0] if browser.pages else browser.new_page()
        page.goto(login_url, wait_until="domcontentloaded", timeout=60_000)
        input("登录完成后按 Enter 保存 storage_state...")
        browser.storage_state(path=str(state_path))
        browser.close()

    manifest = save_session(config, {
        "platform": args.platform,
        "account": args.account,
        "login_url": login_url,
    })
    checked = check_session(config, args.platform, args.account)
    print(f"Saved: {manifest['storage_state_path']}")
    print(f"Cookie file: {manifest['cookie_file_path']}")
    print(f"Status: {checked['status']} · {checked.get('status_reason') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
