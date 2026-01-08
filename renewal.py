import asyncio
import re
import os
from playwright.async_api import async_playwright

XS_EMAIL = os.getenv("XS_EMAIL")
XS_PASSWORD = os.getenv("XS_PASSWORD")

GAME_PANEL_URL = "https://secure.xserver.ne.jp/xapanel/login/xmgame"

def parse_game_time(text: str):
    h = re.search(r'(\d+)時間', text)
    m = re.search(r'(\d+)分', text)

    hours = int(h.group(1)) if h else 0
    minutes = int(m.group(1)) if m else 0
    return hours + minutes / 60


def need_renew(remain_hours: float):
    return remain_hours < 24


async def login(page):
    print("进入 Xserver 统一登录页")
    await page.goto("https://secure.xserver.ne.jp/login/", wait_until="domcontentloaded")

    # 邮箱
    await page.wait_for_selector('input[name="memberid"]')
    await page.fill('input[name="memberid"]', XS_EMAIL)

    # 密码
    await page.fill('input[name="password"]', XS_PASSWORD)

    # 登录
    await page.click('button[type="submit"]')
    await page.wait_for_load_state("networkidle")

    print("登录完成，进入面板")


async def renew_game(page):
    print("执行续期流程")

    await page.click("text=アップグレード・期限延長")
    await page.wait_for_load_state("networkidle")

    # 下一步 / 确认
    for btn in ["次へ", "申し込む", "更新", "確認"]:
        try:
            await page.click(f"text={btn}", timeout=3000)
            break
        except:
            pass

    await page.wait_for_timeout(4000)
    print("续期完成")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        await login(page)

        # 👇 新增这两行
        await page.goto("https://cure.xserver.ne.jp/game/")
        await page.wait_for_load_state("networkidle")

        time_text = await page.locator("text=/残り.*時間/").inner_text()
        remain_hours = parse_game_time(time_text)
        print("服务器剩余小时:", remain_hours)

        if need_renew(remain_hours):
            await renew_game(page)
        else:
            print("剩余时间充足，跳过")

        await browser.close()



if __name__ == "__main__":
    asyncio.run(main())
