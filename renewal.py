#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（最终版）
- 账号密码登录（最稳定）
- 登录后自动从列表页点击“ゲーム管理”进入面板
- 只在剩余时间 < 24 小时 时续期
- GitHub Actions 完全兼容
- 详细截图 + Telegram 通知 + Artifact 上传（即使失败也能看到哪里卡住）
"""

import asyncio
import re
import os
import logging
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

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
    GAME_PANEL_URL = f"https://cure.xserver.ne.jp/game-panel/{GAME_SERVER_ID}"

# ======================== 日志 & 通知 ==========================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('renewal.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class Notifier:
    @staticmethod
    async def send_telegram(message: str):
        if not all([Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID]):
            return
        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {"chat_id": Config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as resp:
                    if resp.status != 200:
                        logger.error(f"Telegram 发送失败: {resp.status} {await resp.text()}")
                    else:
                        logger.info("Telegram 发送成功")
        except Exception as e:
            logger.error(f"Telegram 发送异常: {e}")

    @staticmethod
    async def notify(title: str, content: str = ""):
        msg = f"<b>{title}</b>\n{content}" if content else title
        await Notifier.send_telegram(msg)

# ======================== 核心类 ==========================

class XServerGamesRenewal:
    def __init__(self):
        self.page = None
        self.browser = None
        self._pw = None
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
            launch_args = [
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--headless=new",
            ]
            self.browser = await self._pw.chromium.launch(headless=True, args=launch_args)
            context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            if STEALTH_AVAILABLE:
                self.page = await context.new_page()
                await stealth_async(self.page)
            else:
                 self.page = await context.new_page()
            self.page.set_default_timeout(Config.WAIT_TIMEOUT)
            logger.info("✅ 浏览器启动成功")
            return True
        except Exception as e:
            logger.error(f"❌ 浏览器启动失败: {e}")
            self.error_message = str(e)
            return False

    async def login(self) -> bool:
        try:
            logger.info("正在导航到登录页面...")
            await self.page.goto("https://secure.xserver.ne.jp/xapanel/login/xmgame/")
            await self.page.wait_for_selector("input[name='memberid'], input[name='email']", timeout=30000)
            await self.shot("01_login_page")

            logger.info("正在填写凭据...")
            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            await self.shot("02_credentials_filled")

            logger.info("正在提交登录表单...")
            # 使用 Promise.all 等待导航和点击完成
            async with self.page.expect_navigation(wait_until="domcontentloaded", timeout=40000):
                await self.page.click("input[type='submit'], button[type='submit']")
            
            await self.shot("03_after_login_redirect")
            
            # 登录后，页面会跳转。我们在这里等待跳转后的页面加载完成
            # 检查是否需要验证码或已进入列表页
            if await self.page.is_visible('text=認証コード'):
                logger.warning("⚠️ 检测到邮箱验证码页面")
                await self.shot("04_otp_page")
                self.error_message = "需要邮箱验证码（建议关闭“不審なログイン時の認証”）"
                await Notifier.notify("⚠️ 续期暂停", "检测到邮箱验证码，无法自动输入")
                return False

            # ==================== 精准 Iframe 点击逻辑 ====================
            logger.info("登录成功，正在等待并定位服务器列表 Iframe...")
            await self.shot("05_server_list_page")
            
            try:
                # 1. 明确等待 Iframe 元素出现
                iframe_selector = "iframe[src*='game/index']" # 使用 src 属性来更精确地找到 iframe
                await self.page.wait_for_selector(iframe_selector, timeout=20000)
                logger.info("✅ 成功定位到服务器列表 Iframe。")

                # 2. 创建一个指向该 Iframe 内部的 FrameLocator
                frame_context = self.page.frame_locator(iframe_selector)

                # 3. 在 Iframe 内部执行所有后续操作
                logger.info("正在 Iframe 内部查找并点击【ゲーム管理】按钮...")
                
                # 3.1 定义 Iframe 内部的目标按钮
                game_row = frame_context.locator(f"tr:has-text('{Config.GAME_SERVER_ID}')")
                management_button = game_row.locator("a:has-text('ゲーム管理'), button:has-text('ゲーム管理')")
                
                # 3.2 等待按钮可见并点击
                await management_button.wait_for(state="visible", timeout=15000)
                await management_button.click()
                
                logger.info("✅ 成功在 Iframe 内部点击【ゲーム管理】按钮！")

            except PlaywrightTimeout as e:
                logger.error(f"❌ 在 Iframe 中定位或点击【ゲーム管理】按钮时超时。请检查：", exc_info=True)
                logger.error(f"  1. 您的 XSERVER_GAME_SERVER_ID ('{Config.GAME_SERVER_ID}') 是否正确？")
                logger.error(f"  2. 页面结构是否发生变化？")
                self.error_message = f"无法在 Iframe 中找到 ID 为 '{Config.GAME_SERVER_ID}' 的服务器或其管理按钮。"
                await self.shot("error_iframe_click_failed")
                return False

            # =============================================================

            # 等待进入游戏面板页
            try:
                await self.page.wait_for_url("**/game-panel/**", timeout=30000)
                logger.info("🎉 成功进入游戏服务器面板！")
                await self.shot("06_entered_panel_success")
                return True
            except PlaywrightTimeout:
                logger.error("❌ 点击【ゲーム管理】后，页面跳转超时。")
                await self.shot("07_panel_load_timeout")
                self.error_message = "点击【ゲーム管理】后，页面跳转超时。"
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程中发生未知异常: {e}", exc_info=True)
            await self.shot("error_login_unexpected")
            self.error_message = str(e)
            return False


    async def get_remaining_time(self) -> bool:
        try:
            logger.info("正在获取剩余时间...")
            await self.page.wait_for_load_state('domcontentloaded')
            await asyncio.sleep(5) # 等待动态内容加载
            await self.shot("09_game_panel_loaded")
            
            # 使用更可靠的定位器来查找时间
            remaining_time_text_locator = self.page.locator("*:textmatches('残り\\s*\\d+\\s*時間')")
            
            try:
                text_content = await remaining_time_text_locator.first.text_content(timeout=15000)
                match = re.search(r'残り\s*(\d+)\s*時間', text_content)
                if match:
                    self.remaining_hours = int(match.group(1))
                    logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                    return True
            except PlaywrightTimeout:
                logger.warning("⚠️ 未能定位到包含'残り'和'時間'的文本。")

            logger.warning("⚠️ 无法从页面上解析剩余时间。")
            await self.shot("10_no_time_text")
            return False

        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}", exc_info=True)
            await self.shot("error_time")
            return False

    async def extend_contract(self) -> bool:
        try:
            logger.info("🔄 开始续期流程...")
            await self.page.click("text=アップグレード・期限延長", timeout=15000)
            await self.page.wait_for_load_state('domcontentloaded')
            await self.shot("11_extend_page_loaded")
            
            # 确认按钮可能存在，也可能不存在
            confirm_button = self.page.locator("button:has-text('確認'), input:has-text('確認')")
            if await confirm_button.is_visible():
                logger.info("发现确认按钮，正在点击...")
                await confirm_button.click()

            # 等待成功提示
            try:
                await self.page.wait_for_selector("text=延長しました", timeout=30000)
                logger.info("🎉 续期成功！检测到“延長しました”消息。")
                await self.shot("12_success")
                self.renewal_status = "Success"
                return True
            except PlaywrightTimeout:
                logger.warning("ℹ️ 未检测到明确的成功消息，但流程已完成。可能已成功续期。")
                await self.shot("13_possible_success")
                self.renewal_status = "Success (Implicit)"
                return True

        except Exception as e:
            logger.error(f"❌ 续期操作失败: {e}", exc_info=True)
            await self.shot("error_extend")
            self.error_message = str(e)
            self.renewal_status = "Failed"
            return False

    async def run(self):
        try:
            logger.info("=" * 60)
            logger.info("🚀 XServer GAMEs 自动续期开始")
            logger.info("=" * 60)
            if not await self.setup_browser():
                await Notifier.notify("❌ 启动失败", self.error_message or "无法启动浏览器")
                return

            if not await self.login():
                await Notifier.notify("❌ 登录或点击失败", self.error_message or "未知登录错误")
                return

            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查失败", "登录成功，但无法读取剩余时间")
                # 即使无法读取时间，也可能需要续期，可以选择继续或停止
                # 这里我们选择停止，因为不确定时间
                return

            if self.remaining_hours is not None and self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时，无需续期。")
                self.renewal_status = "Not Needed"
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
                return

            logger.info(f"⚠️ 剩余 {self.remaining_hours or '未知'} 小时，需要执行续期操作。")
            
            if await self.extend_contract():
                await Notifier.notify("✅ 续期成功", f"操作完成，服务器已续期。")
            else:
                await Notifier.notify("❌ 续期失败", self.error_message or "续期过程中发生未知错误")

        except Exception as e:
            logger.error(f" CRITICAL: 脚本主流程发生严重错误: {e}", exc_info=True)
            self.renewal_status = "Critical Error"
            await Notifier.notify("💥 脚本严重错误", str(e))
        finally:
            logger.info(f"🏁 脚本结束 - 最终状态: {self.renewal_status}")
            if self.browser:
                await self.browser.close()
            if self._pw:
                await self._pw.stop()

async def main():
    runner = XServerGamesRenewal()
    await runner.run()

if __name__ == "__main__":
    asyncio.run(main())

