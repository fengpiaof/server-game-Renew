async def extend_contract(self) -> bool:
    try:
        panel = self.page  # 目前確定使用 page 作為操作上下文

        logger.info("🔄 開始終極搜尋『アップグレード・期限延長』元素...")

        # 擴大搜尋範圍的多組 selector（優先級由高到低）
        possible_selectors = [
            # 精準文字匹配（允許前後空格/換行）
            ":text('アップグレード・期限延長')",
            ":text('アップグレード ・ 期限延長')",
            ":text('期限延長')",  # 很多情況只顯示後半段

            # 常見的按鈕/連結樣式
            "button:has-text('アップグレード'), button:has-text('期限延長')",
            "a:has-text('アップグレード・期限延長')",
            "a:has-text('期限延長')",
            "[class*='btn']:has-text('アップグレード'), [class*='button']:has-text('アップグレード')",
            "[role='button']:has-text('期限延長')",

            # 最寬鬆兜底選擇器
            "[class*='upgrade'], [class*='extend'], [class*='renew']:has-text('期限延長')",
        ]

        extend_loc = None
        found_selector = None

        # 逐一嘗試每個 selector
        for sel in possible_selectors:
            loc = panel.locator(sel).first
            try:
                if await loc.is_visible(timeout=5000):
                    extend_loc = loc
                    found_selector = sel
                    logger.info(f"★ 命中 selector: {sel}")
                    break
            except Exception:
                continue

        # 如果全部沒找到，輸出診斷資訊
        if not extend_loc:
            all_matching = await panel.locator(":text('期限延長')").all_inner_texts()
            logger.error(f"找不到任何元素！但頁面有這些含『期限延長』的文字: {all_matching}")
            await self.shot("DEBUG_no_button_found")
            raise Exception("無法定位到任何『アップグレード・期限延長』相關元素")

        # 找到元素後的處理流程
        logger.info(f"元素已找到，使用 selector: {found_selector}")
        await extend_loc.scroll_into_view_if_needed()
        await extend_loc.wait_for(state="visible", timeout=15000)
        await extend_loc.wait_for(state="enabled", timeout=10000)

        # 三段式點擊嘗試（由弱到強）
        clicked = False
        for attempt, method in enumerate(["normal click", "dispatch", "js force"], 1):
            try:
                if attempt == 1:
                    await extend_loc.click(timeout=10000, force=True)
                elif attempt == 2:
                    await extend_loc.dispatch_event("click")
                else:
                    await extend_loc.evaluate(
                        "el => { el.click(); el.dispatchEvent(new MouseEvent('click', {bubbles: true})); }"
                    )
                clicked = True
                logger.info(f"點擊成功！使用 {method}")
                break
            except Exception as e:
                logger.warning(f"嘗試 {attempt}/3 ({method}) 失敗: {str(e)[:100]}...")

        if not clicked:
            raise Exception("三種點擊方式全部失敗")

        await asyncio.sleep(3)  # 等待可能的彈窗出現

        # 處理確認彈窗
        confirm_loc = panel.locator(
            "div.modal-content button:has-text('確認'), "
            "div.modal-content :text('確認')"
        ).first

        if await confirm_loc.is_visible(timeout=8000):
            logger.info("發現確認彈窗 → 點擊確認")
            await confirm_loc.click(force=True)

        # 等待續期成功的標誌文字（放寬條件）
        await panel.locator(
            "text=延長しました, text=更新しました"
        ).wait_for(state="visible", timeout=40000)

        logger.info("🎉 續期成功！")
        self.renewal_status = "Success"
        await self.shot("04_extend_success")
        return True

    except Exception as e:
        self.error_message = f"續期失敗: {str(e)}"
        self.renewal_status = "Failed"
        logger.error(self.error_message, exc_info=True)
        await self.shot("error_extend_final")
        return False
