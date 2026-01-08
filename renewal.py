import asyncio, re
from playwright.async_api import async_playwright

LOGIN_URL = "https://cure.xserver.ne.jp/game/"
USERNAME = "你的账号"
PASSWORD = "你的密码"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars"
        ])

        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            locale="ja-JP",
            timezone_id="Asia/Tokyo"
        )

        await context.add_init_script("""
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
""")

        page = await context.new_page()
        await page.goto(LOGIN_URL, wait_until="networkidle", timeout=120000)

        # 登录
        await page.wait_for_selector('input[name="memberid"]', timeout=60000)
        await page.fill('input[name="memberid"]', USERNAME)
        await page.fill('input[name="password"]', PASSWORD)
        await page.click('button[type="submit"]')

        await page.wait_for_load_state("networkidle")

        # 读取剩余时间
        text = await page.inner_text("body")
        m = re.search(r"残り\s*(\d+)\s*時間", text)

        if not m:
            print("❌ 没找到剩余时间")
            await browser.close()
            return

        hours = int(m.group(1))
        print("当前剩余小时：", hours)

        if hours < 24:
            print("⏳ 触发自动续期")
            await page.click("text=更新")
            await page.wait_for_load_state("networkidle")
            print("✅ 已执行续期")
        else:
            print("✔ 还很充足，不需要续期")

        await browser.close()

asyncio.run(main())
