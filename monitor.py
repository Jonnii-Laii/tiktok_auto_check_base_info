import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlencode

import requests
from apscheduler.schedulers.blocking import BlockingScheduler
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("tiktok-seller-monitor")


@dataclass
class Config:
    seller_base_url: str
    seller_overview_path: str
    locale: str
    language: str
    aid: str
    app_name: str
    country_code: str
    seller_id: str
    shop_id: str
    oec_seller_id: str
    timezone_name: str
    headless: bool
    storage_state_path: Path
    cron_expr: str
    timezone: str
    alert_webhook_url: str


def _must_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"Missing required env: {name}")
    return value


def load_config() -> Config:
    load_dotenv()
    return Config(
        seller_base_url=_must_env("SELLER_BASE_URL"),
        seller_overview_path=_must_env("SELLER_OVERVIEW_PATH"),
        locale=os.getenv("LOCALE", "en"),
        language=os.getenv("LANGUAGE", "en"),
        aid=_must_env("AID"),
        app_name=_must_env("APP_NAME"),
        country_code=_must_env("COUNTRY_CODE"),
        seller_id=_must_env("SELLER_ID"),
        shop_id=_must_env("SHOP_ID"),
        oec_seller_id=os.getenv("OEC_SELLER_ID", os.getenv("SELLER_ID", "")),
        timezone_name=os.getenv("TIMEZONE_NAME", "Asia/Jakarta"),
        headless=os.getenv("HEADLESS", "true").lower() == "true",
        storage_state_path=Path(os.getenv("STORAGE_STATE_PATH", "storage_state.json")),
        cron_expr=os.getenv("CRON_EXPR", "0 1 * * *"),
        timezone=os.getenv("TIMEZONE", "UTC"),
        alert_webhook_url=os.getenv("ALERT_WEBHOOK_URL", "").strip(),
    )


def _build_query(cfg: Config) -> Dict[str, str]:
    return {
        "locale": cfg.locale,
        "language": cfg.language,
        "oec_seller_id": cfg.oec_seller_id,
        "seller_id": cfg.seller_id,
        "aid": cfg.aid,
        "app_name": cfg.app_name,
        "shop_id": cfg.shop_id,
        "country_code": cfg.country_code,
        "timezone_name": cfg.timezone_name,
    }


def _extract_violation_score(payload: Dict[str, Any]) -> Optional[int]:
    candidates = [
        payload.get("data", {}).get("violation_score"),
        payload.get("violation_score"),
    ]
    for item in candidates:
        if isinstance(item, int):
            return item
        if isinstance(item, str) and item.isdigit():
            return int(item)
    return None


def bootstrap_login(cfg: Config) -> None:
    cfg.storage_state_path.parent.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.goto(cfg.seller_base_url, wait_until="domcontentloaded")
        print("\n请在打开的浏览器窗口中手动登录。完成后回到终端按 Enter 保存会话...\n")
        input()
        context.storage_state(path=str(cfg.storage_state_path))
        browser.close()
    logger.info("Saved session to %s", cfg.storage_state_path)


def fetch_violation_overview(cfg: Config) -> Dict[str, Any]:
    if not cfg.storage_state_path.exists():
        raise FileNotFoundError(
            f"storage state not found: {cfg.storage_state_path}. Run bootstrap-login first."
        )

    endpoint = f"{cfg.seller_base_url.rstrip('/')}{cfg.seller_overview_path}"
    query = _build_query(cfg)
    url = f"{endpoint}?{urlencode(query)}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.headless)
        context = browser.new_context(storage_state=str(cfg.storage_state_path))
        page = context.new_page()
        # Open base page first so cookies/session are active for same-site request.
        page.goto(cfg.seller_base_url, wait_until="domcontentloaded")

        result = page.evaluate(
            """async (targetUrl) => {
                const resp = await fetch(targetUrl, {
                    method: 'GET',
                    credentials: 'include',
                    headers: {
                        'accept': 'application/json, text/plain, */*'
                    }
                });
                const text = await resp.text();
                return {
                    ok: resp.ok,
                    status: resp.status,
                    text
                };
            }""",
            url,
        )
        browser.close()

    if not result.get("ok"):
        raise RuntimeError(f"HTTP {result.get('status')} from overview endpoint: {result.get('text')}")

    try:
        return json.loads(result["text"])
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Invalid JSON response: {result['text']}") from err


def send_alert(cfg: Config, message: str) -> None:
    if not cfg.alert_webhook_url:
        logger.warning("ALERT_WEBHOOK_URL not configured, skip alert: %s", message)
        return

    resp = requests.post(
        cfg.alert_webhook_url,
        json={"text": message, "msg_type": "text"},
        timeout=15,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"Alert webhook failed: {resp.status_code}, body={resp.text}")


def run_once(cfg: Config) -> None:
    payload = fetch_violation_overview(cfg)
    score = _extract_violation_score(payload)

    if score is None:
        logger.error("violation_score not found in payload: %s", payload)
        raise RuntimeError("violation_score missing")

    now = datetime.now(timezone.utc).isoformat()
    logger.info("[%s] violation_score=%s", now, score)

    if score != 0:
        message = (
            f"[TikTok Seller Alert] violation_score={score} (seller_id={cfg.seller_id}, shop_id={cfg.shop_id})"
        )
        logger.warning(message)
        send_alert(cfg, message)


def schedule_job(cfg: Config) -> None:
    scheduler = BlockingScheduler(timezone=cfg.timezone)
    minute, hour, day, month, day_of_week = cfg.cron_expr.split()
    scheduler.add_job(
        run_once,
        trigger="cron",
        args=[cfg],
        minute=minute,
        hour=hour,
        day=day,
        month=month,
        day_of_week=day_of_week,
    )
    logger.info("Scheduler started with CRON_EXPR='%s', TIMEZONE='%s'", cfg.cron_expr, cfg.timezone)
    scheduler.start()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="TikTok Seller violation_score monitor")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap-login", help="Open browser and save logged-in storage state")
    sub.add_parser("run-once", help="Fetch and check violation score once")
    sub.add_parser("schedule", help="Run APScheduler with CRON_EXPR")
    return parser


def main() -> int:
    try:
        cfg = load_config()
        parser = build_parser()
        args = parser.parse_args()

        if args.command == "bootstrap-login":
            bootstrap_login(cfg)
        elif args.command == "run-once":
            run_once(cfg)
        elif args.command == "schedule":
            schedule_job(cfg)
        else:
            parser.print_help()
            return 2

        return 0
    except Exception as exc:  # noqa: BLE001
        logger.exception("Fatal error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
