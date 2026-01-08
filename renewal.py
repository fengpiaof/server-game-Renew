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
    print("进入 Xserver 统一登录入口")
    await page.goto("https://secure.xserver.ne.jp/login/", wait_until="domcontentloaded")

    # 先等 Cloudflare Turnstile 自动放行（最关键）
    try:
        await page.wait_for_selector("iframe[src*='challenges.cloudflare.com']", timeout=15000)
        print("检测到 Cloudflare 验证，等待自动放行...")
        await page.wait_for_timeout(8000)
    except:
        pass

    # 等真正的登录框出现
    await page.wait_for_selector("input", timeout=30000)

    # 邮箱 / 会员ID（Xserver 这两个任一都会出现）
    for selector in [
        'input[name="memberid"]',
        'input[name="mail"]',
        'input[type="email"]'
    ]:
        try:
            if await page.locator(selector).count() > 0:
                await page.fill(selector, XS_EMAIL)
                break
        except:
            pass

    # 密码
    await page.wait_for_selector('input[type="password"]')
    await page.fill('input[type="password"]', XS_PASSWORD)

    # 登录
    for btn in ["ログイン", "Login", "submit"]:
        try:
            await page.click(f"text={btn}")
            break
        except:
            pass

    await page.wait_for_load_state("networkidle")
    print("登录成功")



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
