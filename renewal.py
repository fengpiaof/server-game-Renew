#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
XServer GAMEs 無料ゲームサーバ 自動續期スクリプト（真正最終版）
- 成功進入管理頁面後因URL不變而誤判失敗的問題已修復
- 採用多策略、多方法（標準/強制/JS）在主頁面與所有Iframe中點擊
- 優化時間與續期成功的檢測邏輯
- GitHub Actions 完全相容，日志與通知功能完善
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
        raise ValueError("請設置 XSERVER_GAME_SERVER_ID 環境變數")

    GAME_PANEL_URL = f"https://cure.xserver.ne.jp/game-panel/{GAME_SERVER_ID}"


# ======================== 日誌 & 通知 ==========================
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
        if not all([Config.TELEGRAM_BOT_TOKEN, Config.TELEGRAM_CHAT_ID]):
            return

        try:
            import aiohttp
            url = f"https://api.telegram.org/bot{Config.TELEGRAM_BOT_TOKEN}/sendMessage"
            data = {"chat_id": Config.TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}

            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=data, timeout=10) as resp:
                    if resp.status != 200:
                        logger.error(f"Telegram 發送失敗: {resp.status} {await resp.text()}")
                    else:
                        logger.info("Telegram 發送成功")
        except Exception as e:
            logger.error(f"Telegram 發送異常: {e}")

    @staticmethod
    async def notify(title: str, content: str = ""):
        await Notifier.send_telegram(
            f"<b>{title}</b>\n{content}" if content else title
        )


# ======================== 核心類 ==========================
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
                logger.info(f"📸 已保存截圖: {name}.png")
            except Exception as e:
                logger.warning(f"截圖失敗: {e}")

    async def setup_browser(self) -> bool:
        try:
            self._pw = await async_playwright().start()
            self.browser = await self._pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            context = await self.browser.new_context(
                locale="ja-JP",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                )
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            self.page = await context.new_page()

            if STEALTH_AVAILABLE:
                await stealth_async(self.page)

            self.page.set_default_timeout(Config.WAIT_TIMEOUT)
            logger.info("✅ 瀏覽器啟動成功")
            return True

        except Exception as e:
            self.error_message = f"瀏覽器啟動失敗: {e}"
            logger.error(self.error_message, exc_info=True)
            return False

    # ── 登錄 & 進入管理面板 ──────────────────────────────────────────────
    async def login(self) -> bool:
        try:
            await self.page.goto("https://secure.xserver.ne.jp/xapanel/login/xmgame/")
            await self.page.wait_for_selector(
                "input[name='memberid'], input[name='email']", timeout=30000
            )

            await self.page.fill("input[name='memberid'], input[name='email']", Config.LOGIN_EMAIL)
            await self.page.fill("input[name='user_password'], input[name='password']", Config.LOGIN_PASSWORD)
            await self.shot("01_credentials_filled")

            async with self.page.expect_navigation(wait_until="domcontentloaded", timeout=40000):
                await self.page.click("input[type='submit'], button[type='submit']")

            await self.shot("02_after_login")

            if await self.page.is_visible('text=認証コード'):
                self.error_message = "需要郵箱驗證碼，請關閉「不審なログイン時の認証」"
                await self.shot("03_otp_page")
                return False

            logger.info("登錄成功，開始執行終極點擊策略...")
            clicked = False
            target_locator_str = f"tr:has-text('{Config.GAME_SERVER_ID}') >> a:has-text('ゲーム管理')"

            async def robust_click(locator):
                nonlocal clicked
                try:
                    await locator.wait_for(state='visible', timeout=7000)
                    await locator.dispatch_event('click')
                    clicked = True
                except Exception:
                    pass

            logger.info("[階段 1/2] 正在主頁面上嘗試...")
            main_page_button = self.page.locator(target_locator_str)
            if await main_page_button.count() > 0:
                await robust_click(main_page_button.first)

            if not clicked:
                logger.info("[階段 2/2] 主頁面失敗，正在掃描所有 Iframe...")
                for i, frame in enumerate(self.page.frames[1:], 1):
                    logger.info(f"--- 檢查 Iframe #{i} ---")
                    iframe_button = frame.locator(target_locator_str)
                    if await iframe_button.count() > 0:
                        await robust_click(iframe_button.first)
                        if clicked:
                            break

            if not clicked:
                self.error_message = f"終極策略失敗：無法點擊ID為'{Config.GAME_SERVER_ID}'的管理按鈕。"
                await self.shot("04_click_failure")
                return False

            # 關鍵修改：改用元素存在來驗證是否進入管理頁面（而非URL）
            logger.info("✅ 點擊操作已執行！現在驗證是否成功進入管理頁面...")
            try:
                await self.page.locator("text=アップグレード・期限延長").wait_for(
                    state="visible", timeout=30000
                )
                logger.info("🎉 驗證成功！已在頁面上找到'アップグレード・期限延長'，確認進入管理面板！")
                await self.shot("05_panel_success")
                return True
            except PlaywrightTimeout:
                self.error_message = "點擊後，未在管理頁面上找到標誌性元素，判定進入失敗。"
                logger.error(self.error_message)
                await self.shot("06_panel_validation_failed")
                return False

        except Exception as e:
            self.error_message = f"登錄或點擊流程發生未知錯誤: {e}"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_login_critical")
            return False

    # ── 獲取剩餘時間 ─────────────────────────────────────────────────────
    async def get_remaining_time(self) -> bool:
        try:
            logger.info("正在管理面板內部採用最終的「簡單包含」策略獲取時間...")
            await asyncio.sleep(3)  # 簡單的人為延遲
            await self.shot("07_before_get_time")

            time_section_locator = self.page.locator("*:has-text('残り'):has-text('時間')").first
            await time_section_locator.wait_for(state="visible", timeout=15000)
            logger.info("✅ 成功定位到包含剩餘時間的區域。")

            full_text = await time_section_locator.inner_text()
            logger.debug(f"提取到的區域文本:\n---\n{full_text}\n---")

            match = re.search(r'残り\s*(\d+)\s*時間', full_text, re.MULTILINE)
            if match:
                self.remaining_hours = int(match.group(1))
                logger.info(f"📅 當前剩餘時間: {self.remaining_hours} 小時")
                return True

            self.error_message = "在定位到的區域內，無法從文本中匹配到 '残り X 時間' 模式。"
            logger.error(self.error_message)
            return False

        except Exception as e:
            self.error_message = f"獲取剩餘時間失敗: {e}"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_get_time")
            return False

    # ── 執行續期 ─────────────────────────────────────────────────────────
    async def extend_contract(self) -> bool:
        try:
            # 注意：目前程式中 self.panel_frame 從未被賦值，建議在此處修正
            # 目前暫時使用 self.page 代替（根據實際頁面結構再調整）
            panel = self.page  # ← 臨時替代，應根據實際情況改為正確的 frame

            logger.info("🔄 正在管理面板內部採用「終極點擊策略」開始續期...")
            await asyncio.sleep(1.5)  # 簡單替代 human_like_delay

            extend_button = panel.locator("button:has-text('アップグレード・期限延長')")
            clicked = False

            # 策略1：dispatch_event
            try:
                logger.info("  - [策略1/2] 嘗試 dispatch_event('click')...")
                await extend_button.wait_for(state="visible", timeout=10000)
                await extend_button.dispatch_event('click')
                clicked = True
                logger.info("  - ✅ dispatch_event('click') 成功。")
            except Exception as e:
                logger.warning(f"  - [策略1/2] dispatch_event('click') 失敗: {e}")

                # 策略2：JavaScript 強制點擊
                try:
                    logger.info("  - [策略2/2] 嘗試最終的 JavaScript 點擊...")
                    await extend_button.evaluate("el => el.click()")
                    clicked = True
                    logger.info("  - ✅ JavaScript 點擊成功。")
                except Exception as js_e:
                    logger.error(f"  - [策略2/2] 所有點擊策略均失敗: {js_e}")
                    raise

            if not clicked:
                raise Exception("所有點擊策略均未能成功點擊續期按鈕")

            await asyncio.sleep(2.5)  # 等待可能的彈窗

            # 處理確認對話框
            confirm_button = panel.locator(
                "div.modal-content button:has-text('確認'), "
                "div.modal-content input:has-text('確認')"
            ).first

            if await confirm_button.is_visible(timeout=5000):
                logger.info("發現確認對話框，正在點擊確認...")
                await confirm_button.click()

            # 等待成功提示
            await panel.locator("text=延長しました").wait_for(state="visible", timeout=30000)

            logger.info("🎉 續期成功！")
            self.renewal_status = "Success"
            await self.shot("04_extend_success")
            return True

        except Exception as e:
            self.error_message = f"續期操作失敗: {e}"
            self.renewal_status = "Failed"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_extend")
            return False

    # ── 主流程 ───────────────────────────────────────────────────────────
    async def run(self):
        try:
            logger.info("=" * 60)
            logger.info("🚀 XServer GAMEs 自動續期開始")
            logger.info("=" * 60)

            if not await self.setup_browser():
                await Notifier.notify("❌ 啟動失敗", self.error_message)
                return

            if not await self.login():
                await Notifier.notify("❌ 登錄/點擊失敗", self.error_message)
                return

            if not await self.get_remaining_time():
                await Notifier.notify("⚠️ 檢查失敗", self.error_message)
                return

            if self.remaining_hours is not None and self.remaining_hours >= 24:
                self.renewal_status = "Not Needed"
                await Notifier.notify("ℹ️ 無需續期", f"當前剩餘 {self.remaining_hours} 小時")
            else:
                logger.info(f"⚠️ 剩餘 {self.remaining_hours or '未知'} 小時，開始續期。")
                if await self.extend_contract():
                    await Notifier.notify("✅ 續期成功", "操作完成，伺服器已續期。")
                else:
                    await Notifier.notify("❌ 續期失敗", self.error_message)

        except Exception as e:
            self.renewal_status = "Critical Error"
            await Notifier.notify("💥 腳本嚴重錯誤", str(e))
            logger.error(f"CRITICAL: 腳本主流程發生嚴重錯誤: {e}", exc_info=True)

        finally:
            logger.info(f"🏁 腳本結束 - 最終狀態: {self.renewal_status}")
            if self.browser:
                await self.browser.close()
            if self._pw:
                await self._pw.stop()


async def main():
    await XServerGamesRenewal().run()


if __name__ == "__main__":
    required_vars = ["XSERVER_EMAIL", "XSERVER_PASSWORD", "XSERVER_GAME_SERVER_ID"]
    if not all(os.getenv(var) for var in required_vars):
        print("錯誤：請確保以下環境變數都已設置！")
        print("   XSERVER_EMAIL")
        print("   XSERVER_PASSWORD")
        print("   XSERVER_GAME_SERVER_ID")
    else:
        asyncio.run(main())
