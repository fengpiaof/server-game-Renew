#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器 自动续期脚本（增强点击版）
- 账号密码登录
- 服务器列表页自动点击【ゲーム管理】按钮（超级加强 selectors）
- 只在剩余时间 < 24 小时 时续期
- GitHub Actions 完全兼容
- 详细截图 + Telegram 通知 + Artifact 上传
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

    WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "60000"))
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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
        self.remaining_hours: Optional[int] = None
        self.error_message: Optional[str] = None

    async def shot(self, name: str):
        if self.page:
            try:
                await self.page.screenshot(path=f"{name}.png", full_page=True)
                logger.info(f"📸 已保存截图: {name}.png")
            except Exception as e:
                logger.warning(f"截图失败 {name}: {e}")

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

            # 必须在服务器列表页点击【ゲーム管理】
            if "xmgame/game/index" in self.page.url or await self.page.query_selector('text=サーバー一覧'):
                logger.info("已进入服务器列表页，准备点击【ゲーム管理】按钮")
                await self.shot("05_server_list_loaded")

                # 打印页面部分 HTML 调试
                try:
                    table_html = await self.page.inner_html("table", timeout=10000)
                    logger.info(f"表格 HTML 片段 (前500字): {table_html[:500]}")
                except:
                    logger.warning("无法获取表格 HTML")

                # 等待页面完全加载
                try:
                    await self.page.wait_for_selector("table", timeout=30000)
                    await self.page.wait_for_load_state("networkidle", timeout=40000)
                except:
                    logger.warning("表格等待超时，继续尝试点击")

                await asyncio.sleep(8)  # 更长保险延迟

                # 超级加强 selectors（优先表单 input/button）
                selectors = [
                    # 优先匹配表单提交（最可能）
                    "input[type='submit'][value='ゲーム管理']",
                    "input[value='ゲーム管理']",
                    "button[type='submit']:has-text('ゲーム管理')",
                    "button:has-text('ゲーム管理')",

                    # 表格内 input/button/a
                    "td >> input[value='ゲーム管理']",
                    "td >> button:has-text('ゲーム管理')",
                    "td:has-text('ゲーム管理') >> input",
                    "td:has-text('ゲーム管理') >> button",
                    "td:has-text('ゲーム管理') >> a",
                    "table input[value*='ゲーム管理']",
                    "table button:has-text('ゲーム管理')",

                    # 宽松匹配
                    "[role='button']:has-text('ゲーム管理')",
                    "text=ゲーム管理 >> clickable",
                    "a:has-text('ゲーム管理')",
                    "a:has-text('ゲーム管理') >> nth=0",

                    # XPath 终极
                    "//input[contains(@value, 'ゲーム管理')]",
                    "//button[contains(text(), 'ゲーム管理')]",
                    "//td[contains(., 'ゲーム管理')]//input",
                    "//td[contains(., 'ゲーム管理')]//button",
                    "//td[contains(., 'ゲーム管理')]//a",
                    "//a[contains(text(), 'ゲーム管理')]",
                ]

                clicked = False
                for i, sel in enumerate(selectors):
                    try:
                        logger.info(f"尝试点击 selector {i+1}/{len(selectors)}: {sel}")
                        locator = self.page.locator(sel).first
                        await locator.click(timeout=20000)

                        await asyncio.sleep(15)  # 更长等待跳转
                        await self.shot(f"06_clicked_selector_{i+1}")

                        # 判断是否成功进入面板
                        if "game-panel" in self.page.url or await self.page.query_selector('text=アップグレード・期限延長'):
                            logger.info(f"✅ 成功进入面板！使用 selector: {sel}")
                            clicked = True
                            break
                    except Exception as e:
                        logger.warning(f"selector {sel} 失败: {str(e)[:100]}")
                        continue

                if not clicked:
                    logger.error("❌ 所有点击方式均失败")
                    await self.shot("07_all_click_failed")
                    self.error_message = "无法点击【ゲーム管理】按钮，请检查页面结构是否变动"
                    await Notifier.notify("❌ 进入面板失败", "所有点击方式无效，请手动查看最新截图和日志中的表格 HTML")
                    return False

            # 最终确认进入面板
            if "game-panel" in self.page.url or await self.page.query_selector('text=アップグレード・期限延長'):
                logger.info("🎉 成功进入游戏服务器面板")
                await self.shot("08_panel_entered")
                return True
            else:
                logger.error("❌ 未检测到面板页面特征")
                await self.shot("09_still_not_panel")
                return False

        except Exception as e:
            logger.error(f"❌ 登录过程异常: {e}")
            await self.shot("error_login")
            self.error_message = str(e)
            return False

    async def get_remaining_time(self) -> bool:
        try:
            await asyncio.sleep(5)
            await self.shot("10_panel_loaded")

            selectors = [
                "*:has-text('残り')",
                "text=無料サーバー契約期限",
                "div:has-text('時間')",
                "text=/残り.*時間/",
            ]

            for sel in selectors:
                try:
                    text = await self.page.inner_text(sel, timeout=15000)
                    logger.info(f"找到文本: {text[:200]}")
                    match = re.search(r'残り\s*(\d+)\s*時間', text)
                    if match:
                        self.remaining_hours = int(match.group(1))
                        logger.info(f"📅 当前剩余时间: {self.remaining_hours} 小时")
                        return True
                except:
                    continue

            logger.warning("⚠️ 未找到剩余时间文本")
            await self.shot("11_no_remaining_text")
            return False

        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}")
            await self.shot("error_remaining_time")
            self.error_message = str(e)
            return False

    async def extend_contract(self) -> bool:
        try:
            logger.info("🔄 开始续期操作")
            await self.page.click("text=アップグレード・期限延長", timeout=20000)
            await asyncio.sleep(6)
            await self.shot("12_extend_clicked")

            if await self.page.query_selector("text=確認"):
                await self.page.click("text=確認")
                await asyncio.sleep(4)
                await self.shot("13_confirm_clicked")

            try:
                await self.page.wait_for_selector("text=延長しました", timeout=30000)
                logger.info("🎉 续期成功！看到“延長しました”提示")
                await self.shot("14_success")
                return True
            except PlaywrightTimeout:
                logger.info("ℹ️ 未看到成功提示，但很可能已续期")
                await self.shot("15_possible_success")
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
                await Notifier.notify("❌ 启动失败", self.error_message or "浏览器启动异常")
                return

            if not await self.login():
                await Notifier.notify("❌ 登录或进入面板失败", self.error_message or "")
                return

            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 检查剩余时间失败", "无法读取剩余时间")
                return

            if self.remaining_hours >= 24:
                logger.info(f"ℹ️ 剩余 {self.remaining_hours} 小时，无需续期")
                await Notifier.notify("ℹ️ 无需续期", f"当前剩余 {self.remaining_hours} 小时")
                return

            logger.info(f"⚠️ 剩余仅 {self.remaining_hours} 小时，开始续期")
            if await self.extend_contract():
                await Notifier.notify("✅ 续期成功", "已成功延长约 72 小时")
            else:
                await Notifier.notify("❌ 续期失败", self.error_message or "未知错误")

        finally:
            logger.info("🏁 脚本执行结束")
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
