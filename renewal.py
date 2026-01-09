#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（真正最终版）
- 修复了成功进入管理页面后，因URL不变而误判失败的问题。
- 采用多策略、多方法（标准/强制/JS）在主页面和所有Iframe中点击。
- 优化了时间和续期成功的检测逻辑。
- GitHub Actions 完全兼容，日志和通知功能完善。
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
            logger.info("✅ 浏览器启动成功")
            return True
        except Exception as e:
            self.error_message = f"浏览器启动失败: {e}"
            logger.error(self.error_message, exc_info=True)
            return False

    async def login(self) -> bool:
        try:
            await self.page.goto("https://secure.xserver.ne.jp/xapanel/login/xmgame/")
            await self.page.wait_for_selector("input[name='memberid'], input[name='email']", timeout=30000)
            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            await self.shot("01_credentials_filled")

            async with self.page.expect_navigation(wait_until="domcontentloaded", timeout=40000):
                await self.page.click("input[type='submit'], button[type='submit']")
            await self.shot("02_after_login")

            if await self.page.is_visible('text=認証コード'):
                self.error_message = "需要邮箱验证码，请关闭“不審なログイン時の認証”"
                await self.shot("03_otp_page")
                return False

            logger.info("登录成功，开始执行终极点击策略...")
            clicked = False
            target_locator_str = f"tr:has-text('{Config.GAME_SERVER_ID}') >> a:has-text('ゲーム管理')"

            async def robust_click(locator):
                nonlocal clicked
                try:
                    await locator.wait_for(state='visible', timeout=7000)
                    await locator.dispatch_event('click')
                    clicked = True
                except Exception: pass

            logger.info("[阶段 1/2] 正在主页面上尝试...")
            main_page_button = self.page.locator(target_locator_str)
            if await main_page_button.count() > 0: await robust_click(main_page_button.first)

            if not clicked:
                logger.info("[阶段 2/2] 主页面失败，正在扫描所有 Iframe...")
                for i, frame in enumerate(self.page.frames[1:], 1):
                    logger.info(f"--- 检查 Iframe #{i} ---")
                    iframe_button = frame.locator(target_locator_str)
                    if await iframe_button.count() > 0:
                        await robust_click(iframe_button.first)
                        if clicked: break

            if not clicked:
                self.error_message = f"终极策略失败：无法点击ID为'{Config.GAME_SERVER_ID}'的管理按钮。"
                await self.shot("04_click_failure")
                return False
            
            # ========== 关键修改：验证方式变更 ==========
            logger.info("✅ 点击操作已执行！现在验证是否成功进入管理页面...")
            try:
                # 不再等待URL，而是等待新页面上的标志性元素出现
                landmark_element = self.page.locator("text=アップグレード・期限延長")
                await landmark_element.wait_for(state="visible", timeout=30000)
                logger.info("🎉 验证成功！已在页面上找到'アップグレード・期限延長'，确认进入管理面板！")
                await self.shot("05_panel_success")
                return True
            except PlaywrightTimeout:
                self.error_message = "点击后，未在管理页面上找到标志性元素，判定进入失败。"
                logger.error(self.error_message)
                await self.shot("06_panel_validation_failed")
                return False

        except Exception as e:
            self.error_message = f"登录或点击流程发生未知错误: {e}"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_login_critical")
            return False

    async def get_remaining_time(self) -> bool:
        try:
            # 确保我们有Iframe的上下文，这是之前版本成功的基础
            if not hasattr(self, 'panel_frame') or not self.panel_frame:
                # 如果因为某些原因 panel_frame 没有被设置，尝试重新定位
                logger.warning("panel_frame 未设置，尝试重新定位Iframe...")
                iframe_selector = "iframe[src*='game/index']"
                await self.page.wait_for_selector(iframe_selector, timeout=15000)
                self.panel_frame = self.page.frame_locator(iframe_selector)

            logger.info("正在管理面板 (Iframe) 内部采用基于截图的“决定性框定”策略获取时间...")
            await self.shot("03_before_get_time")

            # 1. 决定性框定：找到那个同时包含“契约期限”标题和“续期”按钮的“盒子”
            # 这是从您的截图中得到的最可靠的定位器
            server_info_box = self.panel_frame.locator(
                "div.section:has(div.title:has-text('無料サーバー契約期限')):has(button:has-text('アップグレード・期限延長'))"
            ).first
            
            await server_info_box.wait_for(state="visible", timeout=15000)
            logger.info("✅ 成功框定服务器信息区域。")

            # 2. 提取该区域的所有文字
            full_text = await server_info_box.inner_text()
            logger.debug(f"提取到的区域文本: \n---\n{full_text}\n---")

            # 3. 在文字中搜索时间模式
            match = re.search(r'残り\s*(\d+)\s*時間', full_text, re.MULTILINE)
            if match:
                self.remaining_hours = int(match.group(1))
                logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                return True
            
            self.error_message = "在服务器信息区域内，无法从文本中匹配到 '残り X 時間' 模式。"
            logger.error(self.error_message)
            return False
        except Exception as e:
            self.error_message = f"获取剩余时间失败: {e}"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_get_time")
            return False

    async def extend_contract(self) -> bool:
        try:
            # 再次确保我们有Iframe的上下文
            if not hasattr(self, 'panel_frame') or not self.panel_frame:
                 self.error_message = "逻辑错误：执行续期时未找到有效的游戏面板 Iframe。"
                 logger.error(self.error_message)
                 return False

            logger.info("🔄 正在管理面板 (Iframe) 内部开始续期流程...")
            
            # 在Iframe内部点击续期按钮
            extend_button = self.panel_frame.locator("button:has-text('アップグレード・期限延長')")
            await extend_button.click(timeout=15000)
            
            # (可选) 加入一个短暂的延迟，等待对话框弹出
            await asyncio.sleep(3) 
            
            # 处理可能出现的确认对话框，同样在Iframe的上下文中
            # 注意：这里的定位器可能需要根据实际情况微调，比如它是否在一个modal里
            confirm_button = self.panel_frame.locator("div.modal-content button:has-text('確認'), div.modal-content input:has-text('確認')").first
            if await confirm_button.is_visible(timeout=5000):
                logger.info("发现确认对话框，正在点击确认...")
                await confirm_button.click()
            
            # 等待成功消息
            await self.panel_frame.locator("text=延長しました").wait_for(state="visible", timeout=30000)
            
            logger.info("🎉 续期成功！")
            self.renewal_status = "Success"
            await self.shot("04_extend_success")
            return True
        except Exception as e:
            self.error_message = f"续期操作失败: {e}"
            self.renewal_status = "Failed"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_extend")
            return False

    async def run(self):
        try:
            logger.info("=" * 60 + "\n🚀 XServer GAMEs 自动续期开始\n" + "=" * 60)
            if not await self.setup_browser():
                await Notifier.notify("❌ 启动失败", self.error_message)
                return
            if not await self.login():
                await Notifier.notify("❌ 登录/点击失败", self.error_message)
                return
            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查失败", self.error_message)
                return
            if self.remaining_hours is not None and self.remaining_hours >= 24:
                self.renewal_status = "Not Needed"
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
            else:
                logger.info(f"⚠️ 剩余 {self.remaining_hours or '未知'} 小时，开始续期。")
                if await self.extend_contract():
                    await Notifier.notify("✅ 续期成功", "操作完成，服务器已续期。")
                else:
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
