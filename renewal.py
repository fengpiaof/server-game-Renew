#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（手机友好版 · cookies.txt 免登录）

特点：
- 支持上传 cookies.txt 到仓库，实现免登录（跳过账号密码 + 邮箱验证码）
- GitHub Actions 自动使用 headless=True（兼容无头环境）
- 只在剩余时间 < 24 小时 时续期
- 保留截图、Telegram 通知、上传 artifact
- 无需本地运行生成 browser_profile，完全手机操作可维护
"""

import asyncio
import re
import os
import logging
from typing import Optional

from playwright.async_api import async_playwright

# 可选：playwright-stealth 提升反检测（如果仓库没装会自动跳过）
try:
    from playwright_stealth import stealth_async
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    stealth_async = None


# ======================== 配置 ==========================

class Config:
    # 游戏服务器 ID（从 https://cure.xserver.ne.jp/game-panel/XXXX 复制）
    GAME_SERVER_ID = os.getenv("XSERVER_GAME_SERVER_ID", "games-2026-01-05-15-27-05")

    WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "30000"))

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    PROXY_SERVER = os.getenv("PROXY_SERVER")

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
                        logger.error(f"❌ Telegram 发送失败: {resp.status}")
        except Exception as e:
            logger.error(f"❌ Telegram 发送异常: {e}")

    @staticmethod
    async def notify(title: str, content: str = ""):
        msg = f"<b>{title}</b>\n{content}" if content else title
        await Notifier.send_telegram(msg)


# ======================== 核心类 ==========================

class XServerGamesRenewal:
    def __init__(self):
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
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
            ]

            if Config.PROXY_SERVER:
                launch_args.append(f"--proxy-server={Config.PROXY_SERVER}")
                logger.info(f"🌐 使用代理: {Config.PROXY_SERVER}")

            # GitHub Actions 为无头环境，强制使用 headless=True + 新版 headless
            launch_args.append("--headless=new")

            self.context = await self._pw.chromium.launch_persistent_context(
                user_data_dir="browser_profile_temp",  # 临时目录，实际不持久化
                headless=True,
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

            self.page = await self.context.new_page()
            self.page.set_default_timeout(Config.WAIT_TIMEOUT)

            # 反检测注入
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'en-US']});
            """)

            if STEALTH_AVAILABLE:
                await stealth_async(self.page)

            # 加载 cookies.txt（核心免登录功能）
            if os.path.exists("cookies.txt"):
                cookies = []
                try:
                    with open("cookies.txt", "r", encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line or line.startswith("#"):
                                continue
                            parts = line.split("\t")
                            if len(parts) >= 7:
                                cookies.append({
                                    "name": parts[5],
                                    "value": parts[6],
                                    "domain": parts[0],
                                    "path": parts[2],
                                    "expires": float(parts[4]) if parts[4] != "-1" else -1,
                                    "httpOnly": parts[1].lower() == "true",
                                    "secure": parts[3].lower() == "true",
                                })
                    if cookies:
                        await self.context.add_cookies(cookies)
                        logger.info(f"✅ 已成功加载 {len(cookies)} 条 cookies，尝试免登录")
                except Exception as e:
                    logger.warning(f"加载 cookies.txt 失败: {e}")
            else:
                logger.info("ℹ️ 未找到 cookies.txt，将尝试普通流程（可能需要手动登录）")

            logger.info("✅ 浏览器初始化成功")
            return True

        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {e}")
            self.error_message = str(e)
            return False

    async def login(self) -> bool:
        try:
            # 直接访问游戏面板（cookies 生效会直接进入）
            await self.page.goto(Config.GAME_PANEL_URL)
            await asyncio.sleep(8)
            await self.shot("01_panel_or_login")

            # 判断是否已进入面板
            if await self.page.query_selector('text=ゲームパネル') or "game-panel" in self.page.url:
                logger.info("🎉 Cookies 生效！成功免登录，直接进入游戏面板")
                return True
            else:
                logger.error("❌ Cookies 失效或未上传，请手动导出最新 cookies.txt 并上传到仓库")
                await self.shot("02_login_required")
                self.error_message = "需要登录（cookies 失效）"
                return False

        except Exception as e:
            logger.error(f"❌ 登录检查异常: {e}")
            self.error_message = str(e)
            return False

    async def get_remaining_time(self) -> bool:
        try:
            await self.page.goto(Config.GAME_PANEL_URL)
            await asyncio.sleep(8)
            await self.shot("03_game_panel")

            selectors = [
                "*:has-text('残り')",
                "text=無料サーバー契約期限",
                "div:has-text('時間')",
                ".free-term",
                "span:has-text('時間')"
            ]

            remaining_text = ""
            for sel in selectors:
                try:
                    el = await self.page.query_selector(sel)
                    if el:
                        text = await el.inner_text()
                        if "残り" in text:
                            remaining_text = text
                            break
                except:
                    continue

            match = re.search(r'残り\s*(\d+)\s*時間', remaining_text)
            if match:
                self.remaining_hours = int(match.group(1))
                logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                return True

            logger.warning("⚠️ 未检测到剩余时间（页面结构可能变化）")
            return False

        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}")
            return False

    async def extend_contract(self) -> bool:
        try:
            logger.info("🔄 开始续期操作")
            await self.page.click("text=アップグレード・期限延長", timeout=15000)
            await asyncio.sleep(5)
            await self.shot("04_extend_clicked")

            if await self.page.query_selector("text=確認"):
                await self.page.click("text=確認")
                await asyncio.sleep(3)

            try:
                await self.page.wait_for_selector("text=延長しました", timeout=20000)
                logger.info("🎉 续期成功！")
                self.renewal_status = "Success"
                await self.get_remaining_time()
                return True
            except:
                logger.info("ℹ️ 未看到成功提示，但可能已续期")
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
                await Notifier.notify("❌ 浏览器启动失败", self.error_message or "")
                return

            if not await self.login():
                await Notifier.notify("❌ 登录失败", self.error_message or "请检查 cookies.txt 是否最新")
                return

            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查失败", "无法读取剩余时间")
                return

            if self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时 ≥ 24 小时，无需续期")
                self.renewal_status = "Unexpired"
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
                return

            logger.info(f"⚠️ 剩余 {self.remaining_hours} 小时 < 24 小时，开始续期...")
            if await self.extend_contract():
                await Notifier.notify("✅ 续期成功", "已延长约 72 小时")
            else:
                await Notifier.notify("❌ 续期失败", self.error_message or "未知错误")

        finally:
            logger.info(f"🏁 脚本结束 - 状态: {self.renewal_status}")
            try:
                if self.context:
                    await self.context.close()
                if self._pw:
                    await self._pw.stop()
            except Exception as e:
                logger.warning(f"关闭浏览器出错: {e}")


async def main():
    runner = XServerGamesRenewal()
    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
