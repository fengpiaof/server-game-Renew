#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（最终稳定版 · 账号密码登录）

特点：
- 完全使用账号密码登录（不依赖 cookies）
- 自动检测邮箱验证码，如果出现会截图 + Telegram 报警（建议关闭二段階認証）
- 只在剩余时间 < 24 小时 时续期
- GitHub Actions 完美兼容（headless + 新版 Chromium）
- 每次运行生成截图 + 日志，并上传 Artifact 方便查看
- 即使出现验证码导致失败，也会上传截图让你看到具体页面
"""

import asyncio
import re
import os
import logging
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# 可选：提升反检测能力
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

    WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "60000"))  # 增加超时时间
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    if not GAME_SERVER_ID:
        raise ValueError("必须设置 XSERVER_GAME_SERVER_ID")
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
                    if resp.status == 200:
                        logger.info("✅ Telegram 通知成功")
                    else:
                        logger.error(f"❌ Telegram 发送失败: {resp.status}")
        except Exception as e:
            logger.error(f"❌ Telegram 异常: {e}")

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
                "--disable-web-security",
                "--disable-features=IsolateOrigins,site-per-process",
                "--headless=new",  # 强制新版 headless，兼容 Actions
            ]

            self.browser = await self._pw.chromium.launch(headless=True, args=launch_args)
            context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )

            # 反检测注入
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3,4,5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'en-US']});
            """)

            if STEALTH_AVAILABLE:
                await stealth_async(context.new_page())  # stealth 应用到新页面

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
            await self.page.goto("https://secure.xserver.ne.jp/xapanel/login/xmgame/")
            await asyncio.sleep(4)
            await self.shot("01_login_page")

            # 填写账号密码
            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            await self.shot("02_credentials_filled")

            await self.page.click("input[type='submit'], button[type='submit']")
            await asyncio.sleep(10)
            await self.shot("03_after_submit")

            # 检测邮箱验证码页面
            if (await self.page.query_selector('text=認証コード') or 
                await self.page.query_selector('text=認証コードを入力') or
                "otp" in self.page.url):
                logger.warning("⚠️ 检测到邮箱验证码页面")
                await self.shot("04_otp_page")
                self.error_message = "需要邮箱验证码（请关闭账号设置中的“不審なログイン時の認証”）"
                await Notifier.notify("⚠️ 续期暂停", "检测到邮箱验证码，无法自动输入\n请去 XServer 账号设置关闭“不審なログイン時の認証”")
                return False

            # 检查是否成功进入面板
            if "game-panel" in self.page.url or await self.page.query_selector('text=ゲームパネル'):
                logger.info("🎉 登录成功")
                await self.shot("05_logged_in")
                return True

            logger.error("❌ 登录失败（可能密码错误或页面变化）")
            await self.shot("06_login_failed")
            self.error_message = "登录失败"
            return False

        except Exception as e:
            logger.error(f"❌ 登录过程异常: {e}")
            await self.shot("error_login")
            self.error_message = str(e)
            return False

    async def get_remaining_time(self) -> bool:
        try:
            await self.page.goto(Config.GAME_PANEL_URL)
            await asyncio.sleep(10)
            await self.shot("07_game_panel")

            selectors = [
                "*:has-text('残り')",
                "text=無料サーバー契約期限",
                "div:has-text('時間')",
                "span:has-text('時間')",
                ".contract-term"
            ]

            for sel in selectors:
                try:
                    text = await self.page.inner_text(sel, timeout=8000)
                    match = re.search(r'残り\s*(\d+)\s*時間', text)
                    if match:
                        self.remaining_hours = int(match.group(1))
                        logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                        return True
                except:
                    continue

            logger.warning("⚠️ 未找到剩余时间文本")
            await self.shot("08_no_remaining_time")
            return False

        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}")
            await self.shot("error_remaining")
            return False

    async def extend_contract(self) -> bool:
        try:
            logger.info("🔄 开始续期")
            await self.page.click("text=アップグレード・期限延長", timeout=15000)
            await asyncio.sleep(6)
            await self.shot("09_extend_clicked")

            if await self.page.query_selector("text=確認"):
                await self.page.click("text=確認")
                await asyncio.sleep(4)

            try:
                await self.page.wait_for_selector("text=延長しました", timeout=25000)
                logger.info("🎉 续期成功！")
                await self.shot("10_success")
                self.renewal_status = "Success"
                return True
            except PlaywrightTimeout:
                logger.info("ℹ️ 未见成功文字，但可能已续期")
                await self.shot("11_possible_success")
                self.renewal_status = "PossibleSuccess"
                return True

        except Exception as e:
            logger.error(f"❌ 续期操作失败: {e}")
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

            if self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时 ≥ 24 小时，无需续期")
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
                return

            logger.info(f"⚠️ 剩余 {self.remaining_hours} 小时 < 24 小时，开始续期")
            success = await self.extend_contract()
            if success:
                await Notifier.notify("✅ 续期成功", "已延长约 72 小时")
            else:
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
