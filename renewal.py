import re
import time
import os
from playwright.sync_api import sync_playwright, TimeoutError

XSERVER_ID = os.getenv("XSERVER_ID")
XSERVER_PASSWORD = os.getenv("XSERVER_PASSWORD")

GAME_URL = "secure.xserver.ne.jp/xapanel/login/xmgame"

RENEW_THRESHOLD_HOURS = 24


def log(msg):
    print(f"[XSERVER-GAME] {msg}", flush=True)


def parse_remaining_hours(text: str) -> int:
    """
    从 '残り 79時間8分' 中解析小时
    """
    m = re.search(r"残り\s*(\d+)時間", text)
    if not m:
        return 999
    return int(m.group(1))


def login(page):
    log("访问 Xserver Game 面板")
    page.goto(GAME_URL, timeout=60000)

    # 已登录直接返回
    if "ゲームサーバー" in page.content():
        log("已登录")
        return

    log("开始登录")
    page.wait_for_selector('input[name="account"]', timeout=60000)

    page.fill('input[name="account"]', XSERVER_ID)
    page.fill('input[name="password"]', XSERVER_PASSWORD)

    page.click('button[type="submit"]')

    # 等待登录完成
    page.wait_for_load_state("networkidle", timeout=60000)
    time.sleep(3)


def goto_game_panel(page):
    log("进入游戏服务器管理页")
    page.goto(GAME_URL, timeout=60000)
    page.wait_for_selector("text=Minecraft", timeout=60000)


def get_remaining_hours(page) -> int:
    log("读取剩余时间")

    locator = page.locator("text=残り").first
    locator.wait_for(timeout=30000)

    text = locator.text_content()
    log(f"期限文本: {text}")

    hours = parse_remaining_hours(text)
    log(f"剩余小时: {hours}")
    return hours


def renew_game_server(page):
    log("尝试延期")

    # 点击「アップグレード・期限延長」
    page.click("text=アップグレード・期限延長")
    time.sleep(2)

    # 有的页面会有确认
    try:
        page.click("text=確認", timeout=5000)
    except TimeoutError:
        pass

    time.sleep(3)
    log("延期操作完成（请人工确认是否成功）")


def main():
    if not XSERVER_ID or not XSERVER_PASSWORD:
        raise RuntimeError("请设置环境变量 XSERVER_ID / XSERVER_PASSWORD")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            args=["--disable-blink-features=AutomationControlled"],
        )

        context = browser.new_context(
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
        )

        page = context.new_page()

        login(page)
        goto_game_panel(page)

        hours = get_remaining_hours(page)

        if hours <= RENEW_THRESHOLD_HOURS:
            renew_game_server(page)
        else:
            log("未到可延期时间，退出")

        time.sleep(5)
        browser.close()


if __name__ == "__main__":
    main()
