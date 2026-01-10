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
        """嘗試在遊戲管理面板中執行續期操作（兩階段點擊 + 固定等待10秒）"""
        try:
            panel = self.page

            # 第一階段：點擊入口「アップグレード・期限延長」
            logger.info("🔄 第一階段：搜尋並點擊入口按鈕...")
            entry_loc = panel.locator(":text('アップグレード・期限延長')").first

            if not await entry_loc.is_visible(timeout=8000):
                raise Exception("找不到入口按鈕 'アップグレード・期限延長'")

            await entry_loc.scroll_into_view_if_needed()
            await entry_loc.wait_for(state="visible", timeout=15000)

            # 三段式點擊入口
            clicked_entry = False
            for method in ["normal click", "dispatch", "js force"]:
                try:
                    if method == "normal click":
                        await entry_loc.click(force=True, timeout=10000)
                    elif method == "dispatch":
                        await entry_loc.dispatch_event("click")
                    else:
                        await entry_loc.evaluate("el => el.click()")
                    clicked_entry = True
                    logger.info(f"第一階段入口點擊成功 ({method})")
                    break
                except Exception as e:
                    logger.warning(f"第一階段 {method} 失敗: {str(e)[:80]}...")

            if not clicked_entry:
                raise Exception("第一階段入口點擊失敗")

            # 固定等待10秒，讓頁面完全載入（暫時移除特征驗證）
            logger.info("第一階段完成，固定等待10秒讓續期頁面載入...")
            await asyncio.sleep(10)

            await self.shot("08_after_first_click")  # 拍攝進入頁面截圖，便於確認

            # 第二階段：直接搜尋並點擊最終綠色「期限延長する」按鈕
            logger.info("🔄 第二階段：搜尋並點擊最終『期限延長する』按鈕...")
            final_button = panel.locator(":text('期限延長する')").first

            # 如果嚴格匹配失敗，放寬 locator
            if not await final_button.is_visible(timeout=15000):
                logger.warning("嚴格 locator 未命中，嘗試放寬...")
                final_button = panel.locator(
                    "text=期限延長する, text=期限延長, button:text('期限延長'), "
                    "[class*='btn']:text('期限延長'), [class*='button']:text('期限延長')"
                ).first

            if not await final_button.is_visible(timeout=20000):
                # 診斷：列出頁面所有 "期限" 相關文字
                all_related = await panel.locator("text=期限, text=延長, text=する").all_inner_texts()
                logger.error(f"最終按鈕未找到！頁面相關文字: {all_related}")
                await self.shot("DEBUG_no_final_button")
                raise Exception("第二階段：找不到最終綠色按鈕 '期限延長する'")

            await final_button.scroll_into_view_if_needed()  # 確保滾到右下角
            await final_button.wait_for(state="visible", timeout=20000)

            # 三段式點擊最終按鈕
            clicked_final = False
            for method in ["normal click", "dispatch", "js force"]:
                try:
                    if method == "normal click":
                        await final_button.click(force=True, timeout=10000)
                    elif method == "dispatch":
                        await final_button.dispatch_event("click")
                    else:
                        await final_button.evaluate("el => el.click()")
                    clicked_final = True
                    logger.info(f"第二階段最終按鈕點擊成功 ({method})")
                    break
                except Exception as e:
                    logger.warning(f"第二階段 {method} 失敗: {str(e)[:80]}...")

            if not clicked_final:
                raise Exception("第二階段最終按鈕點擊失敗")

            await asyncio.sleep(4)

            # 處理可能的確認彈窗
            confirm = panel.locator(
                "button:text('確認'), button:text('はい'), :text('確認'), :text('OK')"
            ).first
            if await confirm.is_visible(timeout=10000):
                await confirm.click(force=True)
                logger.info("已點擊確認彈窗")

            # 等待最終成功提示
            await panel.locator(
                "text=延長しました, text=更新しました, text=完了, text=成功"
            ).wait_for(state="visible", timeout=60000)

            logger.info("🎉 續期全流程成功！")
            self.renewal_status = "Success"
            await self.shot("success_final")
            return True

        except Exception as e:
            self.error_message = f"續期失敗: {str(e)}"
            logger.error(self.error_message, exc_info=True)
            await self.shot("error_extend_final")
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
