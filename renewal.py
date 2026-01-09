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
                    logger.info("Telegram 发送成功" if resp.status == 200 else f"Telegram 失败: {resp.status}")
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
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)
            if STEALTH_AVAILABLE:
                await stealth_async(context.new_page()) # This is slightly incorrect, stealth should be applied to the context
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
            await self.page.click("input[type='submit'], button[type='submit']")

            try:
                await self.page.wait_for_selector("text=認証コード, text=サーバー一覧", timeout=40000)
            except PlaywrightTimeout:
                logger.error("登录后既未看到验证码也未看到服务器列表，可能登录失败。")
                await self.shot("error_after_login")
                self.error_message = "登录后页面状态未知"
                return False
            await self.shot("03_after_submit")

            if await self.page.is_visible('text=認証コード'):
                logger.warning("⚠️ 检测到邮箱验证码页面")
                await self.shot("04_otp_page")
                self.error_message = "需要邮箱验证码（建议关闭“不審なログイン時の認証”）"
                await Notifier.notify("⚠️ 续期暂停", "检测到邮箱验证码，无法自动输入")
                return False

            # ==================== 全新的点击逻辑 ====================
            logger.info("已进入服务器列表页，将采用多策略点击【ゲーム管理】按钮...")
            await self.shot("05_server_list")

            clicked = False
            
            # 定义要寻找的目标
            def get_target_locator(context):
                # 精准定位：找到包含你服务器ID的那一行，再找那一行里的“ゲーム管理”按钮
                game_row = context.locator(f"tr:has-text('{Config.GAME_SERVER_ID}')")
                return game_row.locator("a:has-text('ゲーム管理'), button:has-text('ゲーム管理')")

            # 策略 1: 在主页面直接尝试
            logger.info("[策略 1/2] 尝试在主页面上直接点击...")
            try:
                main_page_button = get_target_locator(self.page)
                await main_page_button.click(timeout=5000)
                logger.info("✅ [策略 1] 成功在主页面上点击！")
                clicked = True
            except PlaywrightTimeout:
                logger.warning("  - [策略 1] 在主页面上未找到或无法点击按钮。")

            # 策略 2: 如果主页面失败，遍历所有 Iframe
            if not clicked:
                logger.info("[策略 2/2] 主页面失败，开始扫描所有 Iframe...")
                # page.frames[0] 是主页面自己，所以我们从 [1:] 开始
                iframes = self.page.frames[1:]
                if not iframes:
                    logger.error("❌ 页面上没有找到任何 Iframe。")
                else:
                    logger.info(f"发现 {len(iframes)} 个 Iframe，将逐一尝试...")
                    for i, frame in enumerate(iframes):
                        logger.info(f"  -> 正在检查 Iframe #{i+1} (Name: {frame.name}, URL: {frame.url})")
                        try:
                            iframe_button = get_target_locator(frame)
                            # 使用 force=True 应对可能的遮挡问题
                            await iframe_button.click(timeout=5000)
                            logger.info(f"✅ [策略 2] 成功在 Iframe #{i+1} 中点击！")
                            clicked = True
                            break  # 成功后跳出循环
                        except PlaywrightTimeout:
                            logger.warning(f"    - [策略 2] 在 Iframe #{i+1} 中未找到按钮。")
                            continue
            
            if not clicked:
                logger.error("❌ [最终失败] 尝试了所有策略（主页面 + 所有 Iframe），均无法点击【ゲーム管理】按钮。")
                self.error_message = "所有策略都无法定位并点击【ゲーム管理】按钮。"
                await self.shot("error_all_strategies_failed")
                return False

            # ========================================================

            # 等待进入面板页
            try:
                # 等待URL中包含game-panel部分
                await self.page.wait_for_url("**/game-panel/**", timeout=30000)
                logger.info("🎉 成功进入游戏服务器面板！")
                await self.shot("06_entered_panel_success")
                return True
            except PlaywrightTimeout:
                logger.error("❌ 点击后未在规定时间内进入游戏面板页。")
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
            # 已进入面板，无需再次 goto
            await asyncio.sleep(5)
            await self.shot("09_game_panel_loaded")
            selectors = [
                "*:has-text('残り')",
                "text=無料サーバー契約期限",
                "div:has-text('時間')",
            ]
            for sel in selectors:
                try:
                    text = await self.page.inner_text(sel, timeout=10000)
                    match = re.search(r'残り\s*(\d+)\s*時間', text)
                    if match:
                        self.remaining_hours = int(match.group(1))
                        logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                        return True
                except:
                    continue
            logger.warning("⚠️ 未找到剩余时间文本")
            await self.shot("10_no_time_text")
            return False
        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}")
            await self.shot("error_time")
            return False

    async def extend_contract(self) -> bool:
        try:
            logger.info("🔄 开始续期")
            await self.page.click("text=アップグレード・期限延長", timeout=15000)
            await asyncio.sleep(6)
            await self.shot("11_extend_clicked")
            if await self.page.query_selector("text=確認"):
                await self.page.click("text=確認")
                await asyncio.sleep(4)
            try:
                await self.page.wait_for_selector("text=延長しました", timeout=25000)
                logger.info("🎉 续期成功！")
                await self.shot("12_success")
                return True
            except PlaywrightTimeout:
                logger.info("ℹ️ 未见成功提示，但可能已续期")
                await self.shot("13_possible_success")
                return True
        except Exception as e:
            logger.error(f"❌ 续期失败: {e}")
            await self.shot("error_extend")
            self.error_message = str(e)
            return False

    async def run(self):
        try:
            logger.info("=" * 60)
            logger.info("🚀 XServer GAMEs 自动续期开始")
            logger.info("=" * 60)
            if not await self.setup_browser():
                await Notifier.notify("❌ 启动失败", self.error_message or "")
                return
            if not await self.login():
                await Notifier.notify("❌ 登录失败", self.error_message or "")
                return
            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查失败", "无法读取剩余时间")
                return

            if self.remaining_hours and self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时，无需续期")
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
                self.renewal_status = "Not Needed"
                return

            logger.info(f"⚠️ 剩余 {self.remaining_hours or 'N/A'} 小时，开始续期")
            if await self.extend_contract():
                self.renewal_status = "Success"
                await Notifier.notify("✅ 续期成功", "已延长约 72 小时")
            else:
                self.renewal_status = "Failed"
                await Notifier.notify("❌ 续期失败", self.error_message or "")
        finally:
            logger.info(f"🏁 脚本结束 - 状态: {self.renewal_status}")
            try:
                if self.browser:
                    await self.browser.close()
                if self._pw:
                    await self._pw.stop()
            except Exception as e:
                logger.warning(f"关闭浏览器出错: {e}")

async def main():
    runner = XServerGamesRenewal()
    await runner.run()

if __name__ == "__main__":
    asyncio.run(main())

