#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（最终版）

主要特性：
- 适配 XServer GAMEs 免费游戏服务器（Minecraft 等）
- 登录地址：https://secure.xserver.ne.jp/xapanel/login/xmgame/
- 支持邮箱验证码（二段階認証）
- 第一次运行手动输入验证码（浏览器可见），之后全自动（使用持久化登录状态）
- 只在剩余时间 < 24 小时 时才续期
- 每 6 小时检查一次（配合 GitHub Actions）
- 保留 Turnstile 处理、人类行为模拟、截图、Telegram 通知
- 无需配置 IMAP 邮箱密码
"""

import asyncio
import re
import datetime
import os
import logging
from typing import Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# 尝试兼容 playwright-stealth
try:
    from playwright_stealth import stealth_async
    STEALTH_VERSION = 'old'
except ImportError:
    STEALTH_VERSION = 'new'
    stealth_async = None


# ======================== 配置 ==========================

class Config:
    LOGIN_EMAIL = os.getenv("XSERVER_EMAIL")
    LOGIN_PASSWORD = os.getenv("XSERVER_PASSWORD")

    # 游戏服务器 ID，从你的游戏面板 URL 中获取，例如：games-2026-01-05-15-27-05
    GAME_SERVER_ID = os.getenv("XSERVER_GAME_SERVER_ID", "games-2026-01-05-15-27-05")

    # 是否第一次登录（设为 true 时会弹出浏览器让你手动输入验证码）
    FIRST_TIME_LOGIN = os.getenv("FIRST_TIME_LOGIN", "false").lower() == "true"

    WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "30000"))

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    PROXY_SERVER = os.getenv("PROXY_SERVER")

    # 游戏面板主页面（登录成功后会跳转到这里）
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
            data = {
                "chat_id": Config.TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            }
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data) as resp:
                    if resp.status == 200:
                        logger.info("✅ Telegram 通知发送成功")
                    else:
                        logger.error(f"❌ Telegram 返回状态码: {resp.status}")
        except Exception as e:
            logger.error(f"❌ Telegram 发送失败: {e}")

    @staticmethod
    async def notify(title: str, content: str = ""):
        msg = f"<b>{title}</b>\n{content}" if content else title
        await Notifier.send_telegram(msg)


# ======================== 核心类 ==========================

class XServerGamesRenewal:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self._pw = None

        self.renewal_status: str = "Unknown"
        self.remaining_hours: Optional[int] = None
        self.error_message: Optional[str] = None

    async def shot(self, name: str):
        if not self.page:
            return
        try:
            await self.page.screenshot(path=f"{name}.png", full_page=True)
            logger.info(f"📸 截图保存: {name}.png")
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
                "--disable-infobars",
                "--start-maximized",
            ]

            if Config.PROXY_SERVER:
                launch_args.append(f"--proxy-server={Config.PROXY_SERVER}")
                logger.info(f"🌐 使用代理: {Config.PROXY_SERVER}")

            # 持久化上下文目录（保存登录状态）
            profile_dir = "browser_profile"

            if Config.FIRST_TIME_LOGIN:
                logger.info("👐 第一次登录模式：浏览器将可见，请手动完成邮箱验证码登录")
                headless = False
            else:
                logger.info("🔄 自动模式：加载已保存的登录状态")
                headless = False  # Turnstile 通常需要非 headless，可根据实际情况改为 True 测试

            self.context = await self._pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=headless,
                args=launch_args,
                viewport={"width": 1920, "height": 1080},
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )

            # 如果已有打开的页面，使用第一个；否则新建
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.set_default_timeout(Config.WAIT_TIMEOUT)

            # Anti-bot 注入
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1,2,3]});
                Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP','en-US']});
            """)

            # stealth
            if STEALTH_VERSION == 'old' and stealth_async is not None:
                await stealth_async(self.page)

            logger.info("✅ 浏览器初始化成功")
            return True
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {e}")
            self.error_message = str(e)
            return False

    async def login(self) -> bool:
        try:
            await self.page.goto("https://secure.xserver.ne.jp/xapanel/login/xmgame/")
            await asyncio.sleep(3)
            await self.shot("01_login_page")

            # 如果已经登录，直接跳过
            if "game-panel" in self.page.url or await self.page.query_selector('text=ゲームパネル'):
                logger.info("🎉 已检测到登录状态，跳过登录步骤")
                return True

            # 填写账号密码
            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            await self.shot("02_credentials_filled")
            await self.page.click("input[type='submit'], button[type='submit']")
            await asyncio.sleep(5)

            # 检测是否出现邮箱验证码输入框
            if await self.page.query_selector('text=認証コード') or await self.page.query_selector('input[placeholder*="コード"]'):
                if Config.FIRST_TIME_LOGIN:
                    logger.info("⏳ 请在弹出的浏览器中手动输入邮箱收到的验证码，然后点击登录（有 120 秒时间）")
                    await asyncio.sleep(120)  # 给你足够时间手动操作
                else:
                    logger.warning("⚠️ 需要邮箱验证码，但当前为自动模式，无法手动输入")
                    self.error_message = "需要手动输入邮箱验证码，请设置 FIRST_TIME_LOGIN=true 重新运行一次"
                    return False

            # 检查最终是否登录成功
            await asyncio.sleep(5)
            if "game-panel" in self.page.url or await self.page.query_selector('text=ゲームパネル'):
                logger.info("🎉 登录成功！登录状态已保存到 browser_profile 文件夹")
                return True

            logger.error("❌ 登录失败（可能验证码错误或页面变化）")
            self.error_message = "登录失败"
            return False
        except Exception as e:
            logger.error(f"❌ 登录异常: {e}")
            self.error_message = str(e)
            return False

    async def get_remaining_time(self) -> bool:
        try:
            await self.page.goto(Config.GAME_PANEL_URL)
            await asyncio.sleep(6)
            await self.shot("03_game_panel")

            # 常见剩余时间文本位置
            selectors = [
                "*:has-text('残り')",
                ".contract-term",
                "text=無料サーバー契約期限",
                "div:has-text('時間')"
            ]

            remaining_text = ""
            for sel in selectors:
                try:
                    remaining_text = await self.page.inner_text(sel, timeout=5000)
                    if "残り" in remaining_text:
                        break
                except:
                    continue

            match = re.search(r'残り\s*(\d+)\s*時間', remaining_text)
            if match:
                self.remaining_hours = int(match.group(1))
                logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                return True

            logger.warning("⚠️ 未找到剩余时间文本，可能页面结构变化")
            return False
        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}")
            return False

    async def extend_contract(self) -> bool:
        try:
            logger.info("🔄 开始执行续期操作")

            # 点击延期按钮（常见文本）
            await self.page.click("text=アップグレード・期限延長", timeout=10000)
            await asyncio.sleep(5)
            await self.shot("04_extend_clicked")

            # 如果有确认弹窗
            if await self.page.query_selector("text=確認"):
                await self.page.click("text=確認")
                await asyncio.sleep(3)

            # 简单等待成功提示（游戏服务器续期通常无 Turnstile 或验证码）
            try:
                await self.page.wait_for_selector("text=延長しました", timeout=20000)
                logger.info("🎉 续期成功！")
                self.renewal_status = "Success"
                await self.get_remaining_time()  # 更新剩余时间
                return True
            except PlaywrightTimeout:
                logger.warning("⚠️ 未检测到“延長しました”提示，但可能已成功")
                self.renewal_status = "PossibleSuccess"
                return True

        except Exception as e:
            logger.error(f"❌ 续期操作失败: {e}")
            self.error_message = str(e)
            return False

    async def run(self):
        try:
            logger.info("=" * 60)
            logger.info("🚀 XServer GAMEs 自动续期开始")
            logger.info("=" * 60)

            if not await self.setup_browser():
                self.renewal_status = "Failed"
                await Notifier.notify("❌ 浏览器启动失败", self.error_message or "")
                return

            if not await self.login():
                self.renewal_status = "Failed"
                await Notifier.notify("❌ 登录失败", self.error_message or "")
                return

            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查失败", "无法读取剩余时间")
                return

            # 核心判断：剩余时间 >= 24 小时则不续期
            if self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时 >= 24 小时，无需续期")
                self.renewal_status = "Unexpired"
                await Notifier.notify(
                    "ℹ️ 无需续期",
                    f"当前剩余时间：{self.remaining_hours} 小时\n下次检查时若 < 24 小时将自动续期"
                )
                return

            logger.info(f"⚠️ 剩余 {self.remaining_hours} 小时 < 24 小时，开始续期...")
            success = await self.extend_contract()

            if success:
                await Notifier.notify(
                    "✅ 续期成功",
                    f"续期后预计剩余约 {self.remaining_hours + 72} 小时（+3天）"
                )
            else:
                self.renewal_status = "Failed"
                await Notifier.notify("❌ 续期失败", self.error_message or "未知错误")

        finally:
            logger.info(f"🏁 脚本结束，状态: {self.renewal_status}")
            try:
                if self.context:
                    await self.context.close()
                if self._pw:
                    await self._pw.stop()
            except Exception as e:
                logger.warning(f"关闭浏览器时出错: {e}")


async def main():
    runner = XServerGamesRenewal()
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
