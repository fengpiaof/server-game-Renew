#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 自动续期脚本（最终修正版）
- 修复了在 Iframe 内部获取剩余时间失败的问题。
- 所有面板操作（获取时间、续期）现在都会先正确定位到 Iframe 内部再执行。
- 整合了之前所有成功的登录、点击、验证策略。
- 这是最稳定、最健壮的版本。
"""

import asyncio
import re
import os
import logging
from typing import Optional
from playwright.async_api import async_playwright, FrameLocator, TimeoutError as PlaywrightTimeout

try:
    from playwright_stealth import stealth_async
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False

# ======================== 配置 ==========================
class Config:
    LOGIN_EMAIL = os.getenv("XSERVER_EMAIL")
    LOGIN_PASSWORD = os.getenv("XSERVER_PASSWORD")
    GAME_SERVER_ID = os.getenv("XSERVER_GAME_SERVER_ID")
    WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "60000"))
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
    if not GAME_SERVER_ID:
        raise ValueError("请设置 XSERVER_GAME_SERVER_ID 环境变量")

# ======================== 日志 & 通知 ==========================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('renewal.log', 'w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Notifier:
    @staticmethod
    async def send_telegram(message: str):
        if not all([Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID]): return
        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {"chat_id": Config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=10) as resp:
                    if resp.status != 200: logger.error(f"Telegram 发送失败: {resp.status} {await resp.text()}")
                    else: logger.info("Telegram 发送成功")
        except Exception as e:
            logger.error(f"Telegram 发送异常: {e}")

    @staticmethod
    async def notify(title: str, content: str = ""):
        await Notifier.send_telegram(f"<b>{title}</b>\n{content}" if content else title)

# ======================== 核心类 ==========================
class XServerGamesRenewal:
    def __init__(self):
        self.page = None
        self.browser = None
        self._pw = None
        self.panel_frame: Optional[FrameLocator] = None # 用于存储游戏面板的Iframe
        self.renewal_status = "Unknown"
        self.remaining_hours: Optional[int] = None
        self.error_message: Optional[str] = None

    async def shot(self, name: str):
        if self.page:
            try:
                await self.page.screenshot(path=f"{name}.png", full_page=True)
                logger.info(f"📸 已保存截图: {name}.png")
            except Exception as e:
                logger.warning(f"截图失败: {e}")

    async def setup_browser(self) -> bool:
        try:
            self._pw = await async_playwright().start()
            self.browser = await self._pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            context = await self.browser.new_context(locale="ja-JP", user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            self.page = await context.new_page()
            if STEALTH_AVAILABLE: await stealth_async(self.page)
            self.page.set_default_timeout(Config.WAIT_TIMEOUT)
            return True
        except Exception as e:
            self.error_message = f"浏览器启动失败: {e}"
            return False

    async def login(self) -> bool:
        try:
            await self.page.goto("https://secure.xserver.ne.jp/xapanel/login/xmgame/")
            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            
            async with self.page.expect_navigation(wait_until="domcontentloaded"):
                await self.page.click("input[type='submit'], button[type='submit']")
            
            if await self.page.is_visible('text=認証コード'):
                self.error_message = "需要邮箱验证码，请关闭“不審なログイン時の認証”"
                return False

            iframe_selector = "iframe[src*='game/index']"
            await self.page.wait_for_selector(iframe_selector, timeout=20000)
            self.panel_frame = self.page.frame_locator(iframe_selector)

            target_locator_str = f"tr:has-text('{Config.GAME_SERVER_ID}') >> a:has-text('ゲーム管理')"
            await self.panel_frame.locator(target_locator_str).dispatch_event('click')
            
            await self.panel_frame.locator("text=アップグレード・期限延長").wait_for(state="visible", timeout=30000)
            logger.info("🎉 登录并进入管理面板成功！")
            await self.shot("01_panel_success")
            return True
        except Exception as e:
            self.error_message = f"登录或进入面板流程失败: {e}"
            await self.shot("error_login_or_panel")
            return False

    async def get_remaining_time(self) -> bool:
        try:
            if not self.panel_frame:
                self.error_message = "逻辑错误：未找到有效的游戏面板 Iframe。"
                return False

            logger.info("正在管理面板 (Iframe) 内部获取剩余时间...")
            text_locator = self.panel_frame.locator("*:textmatches('残り\\s*\\d+\\s*時間')")
            text_content = await text_locator.first.text_content(timeout=15000)
            
            match = re.search(r'残り\s*(\d+)\s*時間', text_content)
            if match:
                self.remaining_hours = int(match.group(1))
                logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                await self.shot("02_get_time_success")
                return True
            
            self.error_message = "在管理面板内部未找到剩余时间文本。"
            return False
        except Exception as e:
            self.error_message = f"获取剩余时间失败: {e}"
            await self.shot("error_get_time")
            return False

    async def extend_contract(self) -> bool:
        try:
            if not self.panel_frame:
                self.error_message = "逻辑错误：未找到有效的游戏面板 Iframe。"
                return False

            logger.info("🔄 正在管理面板 (Iframe) 内部开始续期流程...")
            await self.panel_frame.locator("text=アップグレード・期限延長").click(timeout=15000)
            
            confirm_button = self.panel_frame.locator("button:has-text('確認'), input:has-text('確認')")
            if await confirm_button.count() > 0:
                await confirm_button.first.click()
            
            await self.panel_frame.locator("text=延長しました").wait_for(state="visible", timeout=30000)
            
            logger.info("🎉 续期成功！")
            await self.shot("03_extend_success")
            self.renewal_status = "Success"
            return True
        except Exception as e:
            self.error_message = f"续期操作失败: {e}"
            await self.shot("error_extend")
            return False

    async def run(self):
        try:
            logger.info("=" * 60 + "\n🚀 XServer GAMEs 自动续期开始\n" + "=" * 60)
            if not await self.setup_browser():
                await Notifier.notify("❌ 启动失败", self.error_message)
                return
            if not await self.login():
                await Notifier.notify("❌ 登录/进入面板失败", self.error_message)
                return
            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查时间失败", self.error_message)
                return
            if self.remaining_hours is not None and self.remaining_hours >= 24:
                self.renewal_status = "Not Needed"
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
            else:
                logger.info(f"⚠️ 剩余 {self.remaining_hours or '未知'} 小时，开始续期。")
                if await self.extend_contract():
                    await Notifier.notify("✅ 续期成功", "操作完成，服务器已续期。")
                else:
                    self.renewal_status = "Failed"
                    await Notifier.notify("❌ 续期失败", self.error_message)
        except Exception as e:
            self.renewal_status = "Critical Error"
            await Notifier.notify("💥 脚本严重错误", str(e))
            logger.error(f"CRITICAL: 脚本主流程发生严重错误: {e}", exc_info=True)
        finally:
            logger.info(f"🏁 脚本结束 - 最终状态: {self.renewal_status}")
            if self.browser: await self.browser.close()
            if self._pw: await self._pw.stop()

async def main():
    await XServerGamesRenewal().run()

if __name__ == "__main__":
    if not all([os.getenv("XSERVER_EMAIL"), os.getenv("XSERVER_PASSWORD"), os.getenv("XSERVER_GAME_SERVER_ID")]):
        print("错误：请确保 XSERVER_EMAIL, XSERVER_PASSWORD, 和 XSERVER_GAME_SERVER_ID 环境变量都已设置！")
    else:
        asyncio.run(main())

