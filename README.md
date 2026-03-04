==================================================

XServer VPS 自动续期脚本（Playwright 自动化 + OCR 验证码识别）
🔥 高自动化 · 💻 浏览器模拟 · 🤖 自动续期 · 📸 全程截图 · 📢 Telegram 推送

==================================================

【📝 项目简介】

本项目用于自动续期 XServer 免费 VPS（無料VPS）。
脚本通过 Playwright 启动浏览器，模拟真实用户操作，实现自动续期流程，包括：

🔐 自动登录 XServer 面板

📅 自动 VPS 利用期限

🤖 自动判断是否已到可续期日（到期前 1 天即可续期）

🧠 自动绕过 Cloudflare Turnstile（尽力处理，无 FlareSolverr）

🔍 自动抓取图片验证码并 OCR 识别

🖱️ 自动提交续期

📄 自动生成 README 状态报告

📬 自动 Telegram 通知

📸 自动截图留存执行记录

此版本为「非 FlareSolverr 版本」，适用于没有重度 Cloudflare 挡板的 XServer 面板。

==================================================

【✨ 功能特点】

🎯 自动判断续期日期
示例：
到期日：2025-11-25
可续期开始日：2025-11-24
到达可续期日后自动执行续期，否则自动跳过。

🤖 自动完成 Turnstile 处理（尽力）
包括：

模拟真人鼠标移动

尝试点击验证框

注入脚本

多 Frame 扫描
适用于普通 Turnstile 页面（非强验证模式）。

🔍 图片验证码自动识别
通过 OCR API（可自定义 API URL）。

📬 全程 Telegram 推送
包含成功、失败、未到续期日等提醒。

📄 自动更新 README.md
显示最新续期状态：

✅ Success

ℹ️ Unexpired

❌ Failed

📸 自动截图
全流程保留关键截图：

登录

表单提交前后

验证码

错误页面等

==================================================

【⚙️ 环境变量说明】

以下环境变量可在 GitHub Actions / 本地系统配置：

XSERVER_EMAIL（必填）
XSERVER_PASSWORD（必填）
XSERVER_VPS_ID（必填）
TELEGRAM_BOT_TOKEN（可选）
TELEGRAM_CHAT_ID（可选）
PROXY_SERVER（可选）
CAPTCHA_API_URL（可选，自带默认值）

==================================================

【🧩 依赖环境】

Python 3.9+
Playwright
Chromium
aiohttp
OCR API

==================================================

【🚀 本地运行方式】

pip install playwright
playwright install chromium
python3 renewal.py

==================================================

【⏱️ GitHub Actions 自动续期示例】

name: XServer VPS 自动续期
schedule: 每 6 小时自动运行
workflow_dispatch: 支持手动运行

==================================================

【📌 提示】

此版本为「无 FlareSolverr」方案，适用于普通 Cloudflare 环境。
如果 XServer 开启更强 Cloudflare 验证，则需使用 FlareSolverr 或 Puppeteer + Stealth Headful 浏览器。

==================================================
