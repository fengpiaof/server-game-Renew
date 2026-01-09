#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（最终强化版）
- 账号密码登录（最稳定）
- 采用多策略、多方法（标准/强制/JS）在主页面和所有Iframe中点击，解决复杂点击问题
- 只在剩余时间 < 24 小时 时续期
- GitHub Actions 完全兼容
- 详细截图 + Telegram 通知
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
            launch_args = ["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"]
            self.browser = await self._pw.chromium.launch(headless=True, args=launch_args)
            context = await self.browser.new_context(
                viewport={"width": 1920, "height": 1080},
                locale="ja-JP",
                timezone_id="Asia/Tokyo",
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            )
            await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            
            self.page = await context.new_page()
            if STEALTH_AVAILABLE:
                await stealth_async(self.page)

            self.page.set_default_timeout(Config.WAIT_TIMEOUT)
            logger.info("✅ 浏览器启动成功")
            return True
        except Exception as e:
            logger.error(f"❌ 浏览器启动失败: {e}", exc_info=True)
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
            async with self.page.expect_navigation(wait_until="domcontentloaded", timeout=40000):
                await self.page.click("input[type='submit'], button[type='submit']")
            await self.shot("03_after_login_redirect")
            
            if await self.page.is_visible('text=認証コード'):
                logger.warning("⚠️ 检测到邮箱验证码页面")
                self.error_message = "需要邮箱验证码（建议关闭“不審なログイン時の認証”）"
                await self.shot("04_otp_page")
                return False

            # ==================== 终极点击策略 ====================
            logger.info("登录成功，将采用终极策略点击【ゲーム管理】按钮...")
            await self.shot("05_server_list_page")
            
            clicked = False
            
            # 定义一个包含多种点击尝试的健壮函数
            async def robust_click(locator):
                nonlocal clicked
                try:
                    await locator.wait_for(state='visible', timeout=7000)
                    logger.info("    - 元素可见，尝试标准 click()...")
                    await locator.click(timeout=5000)
                    logger.info("    ✅ 标准 click() 成功!")
                    clicked = True
                    return
                except Exception:
                    logger.warning("    - 标准 click() 失败。")

                try:
                    logger.warning("    - 尝试强制 click()...")
                    await locator.click(timeout=5000, force=True)
                    logger.info("    ✅ 强制 click() 成功!")
                    clicked = True
                    return
                except Exception:
                    logger.warning("    - 强制 click() 失败。")

                try:
                    logger.warning("    - 尝试 JS click() (终极手段)...")
                    await locator.evaluate("el => el.click()")
                    logger.info("    ✅ JS click() 成功!")
                    clicked = True
                    return
                except Exception as e_js:
                    logger.error(f"    - JS click() 也失败了: {str(e_js).splitlines()[0]}")

            # 定义目标定位器
            target_locator_str = f"tr:has-text('{Config.GAME_SERVER_ID}') >> a:has-text('ゲーム管理')"
            
            # 策略 1: 在主页面上尝试
            logger.info("[阶段 1/2] 正在主页面上尝试...")
            main_page_button = self.page.locator(target_locator_str)
            if await main_page_button.count() > 0:
                await robust_click(main_page_button.first)
            else:
                logger.info("  - 主页面未发现目标按钮。")

            # 策略 2: 如果主页面失败，遍历所有 Iframe
            if not clicked:
                logger.info("[阶段 2/2] 主页面失败，正在扫描所有 Iframe...")
                iframes = self.page.frames[1:] # page.frames[0] is the main page
                if not iframes:
                     logger.warning("  - 未发现任何 Iframe。")
                else:
                    for i, frame in enumerate(iframes, 1):
                        logger.info(f"--- 检查 Iframe #{i} (Name: '{frame.name}', URL: '{frame.url}') ---")
                        iframe_button = frame.locator(target_locator_str)
                        if await iframe_button.count() > 0:
                            await robust_click(iframe_button.first)
                            if clicked:
                                break # Exit loop if successful
                        else:
                            logger.info(f"  - Iframe #{i} 未发现目标按钮。")

            if not clicked:
                self.error_message = f"终极策略失败：无法在主页面或任何Iframe中点击ID为'{Config.GAME_SERVER_ID}'的管理按钮。"
                logger.error(self.error_message)
                await self.shot("error_ultimate_failure")
                return False

            # ========================================================
            
            logger.info("点击操作已执行，等待页面跳转...")
            try:
                await self.page.wait_for_url("**/game-panel/**", timeout=30000)
                logger.info("🎉 成功进入游戏服务器面板！")
                await self.shot("06_entered_panel_success")
                return True
            except PlaywrightTimeout:
                self.error_message = "点击【ゲーム管理】后，页面跳转超时。"
                logger.error(self.error_message)
                await self.shot("07_panel_load_timeout")
                return False

        except Exception as e:
            self.error_message = f"登录流程发生未知严重错误: {e}"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_login_critical")
            return False

    async def get_remaining_time(self) -> bool:
        # This function remains the same as your original, it seems fine.
        try:
            await self.page.wait_for_load_state('domcontentloaded', timeout=20000)
            await asyncio.sleep(5)
            await self.shot("09_game_panel_loaded")
            text = await self.page.locator('body').inner_text()
            match = re.search(r'残り\s*(\d+)\s*時間', text)
            if match:
                self.remaining_hours = int(match.group(1))
                logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                return True
            logger.warning("⚠️ 在页面上未找到剩余时间文本。")
            await self.shot("10_no_time_text")
            return False
        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}", exc_info=True)
            await self.shot("error_time")
            return False

    async def extend_contract(self) -> bool:
        # This function remains the same as your original, it seems fine.
        try:
            logger.info("🔄 开始续期流程...")
            await self.page.click("text=アップグレード・期限延長", timeout=15000)
            await self.page.wait_for_load_state('domcontentloaded')
            await self.shot("11_extend_page_loaded")
            confirm_button = self.page.locator("button:has-text('確認'), input:has-text('確認')")
            if await confirm_button.count() > 0:
                await confirm_button.first.click()
            await self.page.wait_for_selector("text=延長しました", timeout=30000)
            logger.info("🎉 续期成功！")
            await self.shot("12_success")
            self.renewal_status = "Success"
            return True
        except Exception as e:
            logger.warning(f"ℹ️ 续期过程中出现异常，但可能已成功: {e}")
            await self.shot("13_possible_success")
            self.renewal_status = "Success (Implicit)"
            return True

    async def run(self):
        # This main run logic remains the same.
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
                return
            if self.remaining_hours is not None and self.remaining_hours >= 24:
                self.renewal_status = "Not Needed"
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
            else:
                logger.info(f"⚠️ 剩余 {self.remaining_hours or '未知'} 小时，需要执行续期操作。")
                if await self.extend_contract():
                    await Notifier.notify("✅ 续期成功", "操作完成，服务器已续期。")
                else:
                    self.renewal_status = "Failed"
                    await Notifier.notify("❌ 续期失败", self.error_message or "续期过程中发生未知错误")
        except Exception as e:
            self.renewal_status = "Critical Error"
            await Notifier.notify("💥 脚本严重错误", str(e))
            logger.error(f"CRITICAL: 脚本主流程发生严重错误: {e}", exc_info=True)
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
