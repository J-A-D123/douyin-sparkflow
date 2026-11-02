import asyncio
from urllib.parse import urlsplit

from rich.console import Console

from core.browser import get_browser
from core.chat_page import ensure_chat_access
from utils.config import normalize_unique_id, upsert_user_account


console = Console()

READY_SELECTOR = (
    'xpath=//*[contains(@id, "garfish_app_for_douyin_creator_pc_home")]'
    '/div/div[2]/div/div[2]/div[1]'
)
XPATHS = {
    "unique_id": (
        'xpath=//*[contains(@id, "garfish_app_for_douyin_creator_pc_home")]'
        '/div/div[2]/div/div[2]/div[1]/div[2]/div[1]/div[3]'
    ),
    "name": (
        'xpath=//*[contains(@id, "garfish_app_for_douyin_creator_pc_home")]'
        '/div/div[2]/div/div[2]/div[1]/div[2]/div[1]/div[1]/div[1]'
    ),
}


CREATOR_IDENTITY_SCRIPT = r"""() => {
    const visible = el => {
        const r = el.getBoundingClientRect();
        const s = getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== 'none' && s.visibility !== 'hidden';
    };
    const ids = [...document.querySelectorAll('[class*="unique_id-"], [class*="uniqueId"], [data-e2e="user-id"]')];
    const found = [];
    for (const el of ids) {
        if (!visible(el)) continue;
        const match = (el.innerText || '').trim().match(/^抖音号\s*[：:]\s*([0-9A-Za-z_.-]+)\s*$/);
        if (!match) continue;
        const header = el.parentElement;
        if (!header || !visible(header)) continue;
        const prefix = (header.innerText || '').split(/抖音号\s*[：:]/)[0].trim();
        const username = prefix.replace(/\s+/g, ' ');
        if (!username || username.length > 40 || /登录|首页|发布作品/.test(username)) continue;
        found.push({display_id: match[1], username});
    }
    const unique = [...new Map(found.map(i => [i.display_id + '\\n' + i.username, i])).values()];
    return unique.length === 1 ? unique[0] : null;
}"""


async def wait_for_logged_in_identity(page, timeout_ms=300000, *, fail_on_access_problem=False):
    """Read the labelled account card instead of obsolete child-index XPaths."""
    deadline = asyncio.get_running_loop().time() + max(0, timeout_ms) / 1000
    while True:
        url = urlsplit(page.url)
        if url.hostname != "creator.douyin.com" or not (
            url.path in {"", "/"} or url.path.rstrip("/") == "/creator-micro/home"
        ):
            raise RuntimeError("请先打开创作者中心首页核验当前登录账号")
        if fail_on_access_problem:
            await ensure_chat_access(page)
        identity = await page.evaluate(CREATOR_IDENTITY_SCRIPT)
        if identity:
            unique_id = normalize_unique_id(identity["display_id"])
            username = identity["username"].strip()
            if unique_id and username:
                return unique_id, username
        if asyncio.get_running_loop().time() >= deadline:
            raise RuntimeError("尚未读取到已登录账号的抖音号和昵称，请等待首页账号资料显示")
        await asyncio.sleep(0.25)


async def collect_login_result(page, context, timeout_ms=300000):
    unique_id, username = await wait_for_logged_in_identity(page, timeout_ms=timeout_ms)
    cookies = await context.cookies()
    return {
        "unique_id": unique_id,
        "username": username,
        "cookies": cookies,
    }


async def userLogin(targets=None):
    playwright, browser = await get_browser(GUI=True)
    try:
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://creator.douyin.com/")
        console.print("Please scan the QR code and finish logging into Douyin Creator Center.")

        login_result = await collect_login_result(page, context)
        console.print(f"Unique ID: {login_result['unique_id']}")
        console.print(f"Name: {login_result['username']}")
        console.print(f"Cookies: found {len(login_result['cookies'])} cookies")

        if targets is None:
            raw_targets = input(
                "Open Creator Center -> 互动管理 -> 私信管理 -> 朋友私信, then enter friend display names separated by spaces: "
            )
            targets = [target.strip() for target in raw_targets.split(" ") if target.strip()]

        account = upsert_user_account(
            login_result["unique_id"],
            login_result["username"],
            login_result["cookies"],
            targets,
        )
        console.print(f"[bold green]Login complete. Updated account {account['username']}.[/bold green]")
        return account
    finally:
        await playwright.stop()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(userLogin())
