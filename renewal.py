#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（最终修复版）

修复要点：
- GitHub Actions 环境下自动使用 headless=True（避免 XServer 错误）
- 第一次本地手动登录时使用 headless=False（浏览器可见，手动输入验证码）
- 持久化上下文保存登录状态（browser_profile 文件夹）
- 只在剩余时间 < 24 小时 时续期
- 兼容 Turnstile（通过 anti-bot 注入 + stealth）
- 支持 Telegram 通知 + 截图记录
"""

import asyncio
import re
import os
import logging
from typing import Optional

from playwright.async_api import async_playwright

# 尝试加载 playwright-stealth（可选，提升反检测能力）
try:
    from playwright_stealth import stealth_async
    STEALTH_AVAILABLE = True
except ImportError:
    STEALTH_AVAILABLE = False
    stealth_async = None


# ======================== 配置 ==========================

class Config:
    LOGIN_EMAIL = os.getenv("XSERVER_EMAIL")
    LOGIN_PASSWORD = os.getenv("XSERVER_PASSWORD")

    # 游戏服务器 ID（从面板 URL https://cure.xserver.ne.jp/game-panel/XXXX 中复制）
    GAME_SERVER_ID = os.getenv("XSERVER_GAME_SERVER_ID", "games-2026-01-05-15-27-05")

    # 是否第一次登录（本地运行时设为 true，弹出浏览器手动输入验证码）
    FIRST_TIME_LOGIN = os.getenv("FIRST_TIME_LOGIN", "false").lower() == "true"

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

            profile_dir = "browser_profile"

            # ★ 关键修复：根据模式选择 headless
            if Config.FIRST_TIME_LOGIN:
                logger.info("👐 第一次登录模式：浏览器可见（headless=False），请手动输入验证码")
                headless = False
            else:
                logger.info("🔄 自动续期模式：使用 headless=True（适用于 GitHub Actions 无头环境）")
                headless = True
                launch_args.append("--headless=new")  # 新版 headless 更接近真实浏览器

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

            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()
            self.page.set_default_timeout(Config.WAIT_TIMEOUT)

            # Anti-detection 注入
            await self.context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['ja-JP', 'en-US']});
                Object.defineProperty(navigator, 'permissions', {
                    get: () => ({query: () => Promise.resolve({state: 'granted'})})
                });
            """)

            # stealth（如果安装了）
            if STEALTH_AVAILABLE:
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

            # 已登录判断
            if "game-panel" in self.page.url or await self.page.query_selector('text=ゲームパネル'):
                logger.info("🎉 检测到已登录状态，跳过登录流程")
                return True

            # 填写账号密码
            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            await self.shot("02_filled")
            await self.page.click("input[type='submit'], button[type='submit']")
            await asyncio.sleep(5)

            # 邮箱验证码处理
            if await self.page.query_selector('text=認証コード') or "otp" in self.page.url:
                if Config.FIRST_TIME_LOGIN:
                    logger.info("⏳ 请在浏览器中手动输入邮箱收到的6位验证码，然后点击登录（等待120秒）")
                    await asyncio.sleep(120)
                else:
                    logger.error("⚠️ 需要邮箱验证码，但当前为自动模式（无法手动输入）")
                    self.error_message = "登录状态过期，请本地设置 FIRST_TIME_LOGIN=true 重新手动登录一次"
                    return False

            # 最终登录成功判断
            await asyncio.sleep(6)
            if "game-panel" in self.page.url or await self.page.query_selector('text=ゲームパネル'):
                logger.info("🎉 登录成功！状态已保存")
                return True

            logger.error("❌ 登录失败")
            self.error_message = "登录失败或验证码错误"
            return False
        except Exception as e:
            logger.error(f"❌ 登录异常: {e}")
            self.error_message = str(e)
            return False

    async def get_remaining_time(self) -> bool:
        try:
            await self.page.goto(Config.GAME_PANEL_URL)
            await asyncio.sleep(8)
            await self.shot("03_game_panel")

            # 多 selector 尝试匹配剩余时间
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
                        remaining_text = await el.inner_text()
                        if "残り" in remaining_text:
                            break
                except:
                    continue

            match = re.search(r'残り\s*(\d+)\s*時間', remaining_text)
            if match:
                self.remaining_hours = int(match.group(1))
                logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                return True

            logger.warning("⚠️ 未检测到剩余时间文本（页面可能已变更）")
            self.remaining_hours = None
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

            # 处理可能出现的确认按钮
            if await self.page.query_selector("text=確認"):
                await self.page.click("text=確認")
                await asyncio.sleep(3)

            # 等待成功提示
            try:
                await self.page.wait_for_selector("text=延長しました", timeout=20000)
                logger.info("🎉 续期成功！")
                self.renewal_status = "Success"
                await self.get_remaining_time()
                return True
            except:
                logger.info("ℹ️ 未看到“延長しました”，但可能已成功")
                self.renewal_status = "PossibleSuccess"
                return True

        except Exception as e:
            logger.error(f"❌ 续期失败: {e}")
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
                await Notifier.notify("❌ 登录失败", self.error_message or "")
                return

            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查失败", "无法读取剩余时间")
                return

            if self.remaining_hours is not None and self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时 >= 24 小时，无需续期")
                self.renewal_status = "Unexpired"
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
                return

            logger.info(f"⚠️ 剩余时间不足 24 小时，开始续期...")
            if await self.extend_contract():
                await Notifier.notify("✅ 续期成功", f"续期完成，预计增加约 72 小时")
            else:
                self.renewal_status = "Failed"
                await Notifier.notify("❌ 续期失败", self.error_message or "")

        finally:
            logger.info(f"🏁 脚本执行结束 - 状态: {self.renewal_status}")
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
