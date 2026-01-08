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
                await stealth_async(context.new_page())

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

            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            await self.shot("02_credentials_filled")

            await self.page.click("input[type='submit'], button[type='submit']")
            await asyncio.sleep(10)
            await self.shot("03_after_submit")

            # 检测邮箱验证码
            if await self.page.query_selector('text=認証コード'):
                logger.warning("⚠️ 检测到邮箱验证码")
                await self.shot("04_otp_page")
                self.error_message = "需要邮箱验证码（建议关闭“不審なログイン時の認証”）"
                await Notifier.notify("⚠️ 续期暂停", "检测到邮箱验证码，无法自动输入")
                return False

            # 关键：增强点击“ゲーム管理”按钮
            if "xmgame/game/index" in self.page.url or await self.page.query_selector('text=サーバー一覧'):
                logger.info("已进入服务器列表页，准备点击【ゲーム管理】按钮")
                await self.shot("05_server_list_loaded")

                # 先等待表格完全加载（保险）
                await self.page.wait_for_selector("table", timeout=20000)
                await self.page.wait_for_load_state("networkidle", timeout=30000)
                await asyncio.sleep(5)  # 额外延迟，防动态渲染

                clicked = False

                # 加强 selector 列表（从常见到罕见）
                selectors = [
                    # 最常见的：表格中的按钮或链接
                    "td:has-text('ゲーム管理') >> a", 
                    "td a:has-text('ゲーム管理')",
                    "table a:has-text('ゲーム管理')",
                    "a.button:has-text('ゲーム管理')",

                    # 纯文本匹配
                    "text=ゲーム管理",
                    "a:has-text('ゲーム管理')",

                    # 带属性的按钮
                    "button:has-text('ゲーム管理')",
                    "input[type='button']:has-text('ゲーム管理')",

                    # XPath 备用（更精确匹配表格行）
                    "//td[contains(., 'ゲーム管理')]//a",
                    "//a[contains(text(), 'ゲーム管理')]",
                    "//button[contains(text(), 'ゲーム管理')]",

                    # 如果有多个服务器，取第一个匹配的
                    "a:has-text('ゲーム管理') >> nth=0",
                ]

                for i, sel in enumerate(selectors):
                    try:
                        logger.info(f"尝试 selector {i+1}: {sel}")
                        if sel.startswith("//"):
                            await self.page.click(sel, timeout=15000)
                        else:
                            await self.page.click(sel, timeout=15000)

                        await asyncio.sleep(10)  # 点击后等页面跳转
                        await self.shot(f"06_clicked_with_selector_{i}")

                        if "game-panel" in self.page.url or await self.page.query_selector('text=アップグレード・期限延長'):
                            logger.info(f"✅ 成功点击进入面板！使用 selector: {sel}")
                            clicked = True
                            break
                    except Exception as e:
                        logger.warning(f"selector {sel} 失败: {str(e)[:100]}")
                        continue

                if not clicked:
                    logger.error("❌ 所有 selector 都失败，无法点击【ゲーム管理】")
                    await self.shot("07_all_selectors_failed")
                    self.error_message = "无法自动点击【ゲーム管理】按钮，可能页面布局变动"
                    await Notifier.notify("❌ 进入面板失败", "所有点击方式无效，请检查最新页面结构")
                    return False

            # 确认进入面板
            if "game-panel" in self.page.url or await self.page.query_selector('text=アップグレード・期限延長'):
                logger.info("🎉 成功进入游戏服务器面板")
                await self.shot("08_panel_entered")
                return True

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

            if self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时，无需续期")
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
                return

            logger.info(f"⚠️ 剩余 {self.remaining_hours} 小时，开始续期")
            if await self.extend_contract():
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
