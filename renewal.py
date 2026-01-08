#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 免费游戏服务器自动延期脚本（基于 VPS 版改修）
- 针对 Minecraft 等免费服务器（cure.xserver.ne.jp 面板）
- 简单点击延期按钮，无验证码/Turnstile
- 当剩余时间 ≤ 24 小时时自动延期
"""

import asyncio
import re
import datetime
from datetime import timezone, timedelta
import os
import json
import logging
from typing import Optional, Dict

from playwright.async_api import async_playwright

# ======================== 配置 ==========================

class Config:
    LOGIN_EMAIL = os.getenv("XSERVER_EMAIL")
    LOGIN_PASSWORD = os.getenv("XSERVER_PASSWORD")
    
    # 可选：指定服务器名（面板显示的游戏サーバー名，如 "games-2026-01-05-15-27-05"）
    GAME_SERVER_NAME = os.getenv("GAME_SERVER_NAME", "")  # 留空则针对第一个/唯一服务器

    USE_HEADLESS = os.getenv("USE_HEADLESS", "false").lower() == "true"  # 推荐 False，便于调试
    WAIT_TIMEOUT = int(os.getenv("WAIT_TIMEOUT", "30000"))

    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

    PROXY_SERVER = os.getenv("PROXY_SERVER")

    # 游戏面板 URL
    LOGIN_URL = "https://cure.xserver.ne.jp/login/"
    DASHBOARD_URL = "https://cure.xserver.ne.jp/"  # 登录后跳转仪表盘


# ======================== 日志 & 通知 ==========================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler('renewal.log', encoding='utf-8'), logging.StreamHandler()]
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
                    logger.info("✅ Telegram 通知成功" if resp.status == 200 else f"❌ Telegram 失败: {resp.status}")
        except Exception as e:
            logger.error(f"❌ Telegram 发送失败: {e}")

    @staticmethod
    async def notify(subject: str, message: str):
        await Notifier.send_telegram(message)


# ======================== 核心类 ==========================

class XServerGamesRenewal:
    def __init__(self):
        self.browser = None
        self.context = None
        self.page = None
        self._pw = None

        self.renewal_status: str = "Unknown"
        self.old_remaining: Optional[str] = None  # 如 "79時間8分"
        self.new_remaining: Optional[str] = None
        self.error_message: Optional[str] = None

    async def shot(self, name: str):
        if self.page:
            try:
                await self.page.screenshot(path=f"{name}.png", full_page=True)
            except:
                pass

    async def setup_browser(self) -> bool:
        try:
            self._pw = await async_playwright().start()
            launch_args = ["--no-sandbox", "--disable-blink-features=AutomationControlled"]
            proxy = {"server": Config.PROXY_SERVER} if Config.PROXY_SERVER else None
            
            self.browser = await self._pw.chromium.launch(headless=Config.USE_HEADLESS, args=launch_args, proxy=proxy)
            self.context = await self.browser.new_context(viewport={"width": 1920, "height": 1080}, locale="ja-JP", timezone_id="Asia/Tokyo")
            self.page = await self.context.new_page()
            self.page.set_default_timeout(Config.WAIT_TIMEOUT)
            logger.info("✅ 浏览器初始化成功")
            return True
        except Exception as e:
            logger.error(f"❌ 浏览器初始化失败: {e}")
            self.error_message = str(e)
            return False

    async def login(self) -> bool:
        try:
            await self.page.goto(Config.LOGIN_URL)
            await asyncio.sleep(3)
            await self.shot("01_login_page")
            
            await self.page.fill("input[name='login_id']", Config.LOGIN_EMAIL)  # 实际 selector 以面板为准，可调整
            await self.page.fill("input[name='login_pass']", Config.LOGIN_PASSWORD)
            await self.page.click("button[type='submit']")  # 或 "input[type='submit']"
            
            await asyncio.sleep(5)
            await self.shot("02_after_login")
            
            if "cure.xserver.ne.jp" in self.page.url and "login" not in self.page.url:
                logger.info("🎉 登录成功")
                return True
            logger.error("❌ 登录失败")
            return False
        except Exception as e:
            logger.error(f"❌ 登录错误: {e}")
            return False

    async def get_remaining_time(self) -> bool:
        try:
            await self.page.goto(Config.DASHBOARD_URL)
            await asyncio.sleep(5)
            await self.shot("03_dashboard")
            
            # 提取剩余时间文本（可能多个服务器，取匹配的）
            remaining_text = await self.page.locator("text=残り").first.inner_text()
            # 或更精确：await self.page.locator("text=無料サーバー契約期限").inner_text()
            
            match = re.search(r"残り\s*(\d+)\s*時間\s*(\d*)\s*分*", remaining_text)
            if match:
                hours = int(match.group(1))
                mins = int(match.group(2) or 0)
                self.old_remaining = f"{hours}時間{mins}分"
                logger.info(f"📅 当前剩余时间: {self.old_remaining} ({hours * 60 + mins} 分钟)")
                return True
            logger.warning("⚠️ 未解析到剩余时间")
            return False
        except Exception as e:
            logger.error(f"❌ 获取剩余时间失败: {e}")
            return False

    async def extend_server(self) -> bool:
        try:
            # 点击“アップグレード・期限延長”按钮
            await self.page.click("text=アップグレード・期限延長", timeout=10000)
            await asyncio.sleep(3)
            await self.shot("04_extend_page")
            
            # 点击“期限を延長する”或“更新する”
            try:
                await self.page.click("text=期限を延長する")
            except:
                await self.page.click("text=契約の更新")
            await asyncio.sleep(5)
            await self.shot("05_after_extend")
            
            # 检查是否成功（页面有“成功”或剩余时间变化）
            content = await self.page.content()
            if "成功" in content or "延長" in content:
                logger.info("🎉 延期成功")
                self.renewal_status = "Success"
                await self.get_remaining_time()  # 更新新剩余时间
                self.new_remaining = self.old_remaining
                return True
            logger.warning("⚠️ 延期结果未知")
            return False
        except Exception as e:
            logger.error(f"❌ 延期操作失败: {e}")
            self.error_message = str(e)
            return False

    def generate_readme(self):
        ts = datetime.datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M:%S")
        out = "# XServer GAMEs 免费服务器自动延期状态\n\n"
        out += f"**运行时间**: `{ts} (UTC+8)`\n\n---\n\n"
        
        if self.renewal_status == "Success":
            out += "## ✅ 延期成功\n\n"
            out += f"- 🕛 **旧剩余**: `{self.old_remaining}`\n"
            out += f"- 🕡 **新剩余**: `{self.new_remaining}`\n"
        elif self.renewal_status == "Unexpired":
            out += "## ℹ️ 尚未到延期时间\n\n"
            out += f"- 🕛 **当前剩余**: `{self.old_remaining}`\n"
        else:
            out += "## ❌ 延期失败\n\n"
            out += f"- ⚠️ **错误**: {self.error_message or '未知'}\n"
        
        out += f"\n---\n\n*最后更新: {ts}*"
        
        with open("README.md", "w", encoding="utf-8") as f:
            f.write(out)
        logger.info("📄 README.md 已更新")

    async def run(self):
        try:
            logger.info("🚀 XServer GAMEs 自动延期开始")
            
            if not await self.setup_browser():
                return
            
            if not await self.login():
                self.renewal_status = "Failed"
                await Notifier.notify("❌ 延期失败", "登录失败")
                return
            
            if not await self.get_remaining_time():
                self.renewal_status = "Failed"
                return
            
            # 判断是否需要延期（剩余 ≤ 24 小时）
            match = re.search(r"(\d+)時間", self.old_remaining or "")
            if match:
                hours = int(match.group(1))
                if hours > 24:
                    logger.info(f"ℹ️ 剩余 {hours} 小时 > 24 小时，无需延期")
                    self.renewal_status = "Unexpired"
                    self.generate_readme()
                    await Notifier.notify("ℹ️ 尚未到延期时间", f"当前剩余: {self.old_remaining}")
                    return
            
            # 执行延期
            if await self.extend_server():
                await Notifier.notify("✅ 延期成功", f"新剩余时间: {self.new_remaining}")
            else:
                self.renewal_status = "Failed"
                await Notifier.notify("❌ 延期失败", self.error_message or "未知错误")
            
            self.generate_readme()
                
        finally:
            try:
                if self._pw:
                    await self._pw.stop()
            except:
                pass

async def main():
    runner = XServerGamesRenewal()
    await runner.run()

if __name__ == "__main__":
    asyncio.run(main())
