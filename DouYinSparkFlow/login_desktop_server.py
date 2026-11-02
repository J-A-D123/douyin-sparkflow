import asyncio
import hmac
import os
import shutil
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Response
import uvicorn
from playwright.async_api import async_playwright

from core.login import collect_login_result
from utils.login_api_auth import ensure_login_api_token, read_login_api_token
from fastapi.responses import JSONResponse


REMOTE_LOGIN_URL = "https://creator.douyin.com/"
CHAT_LOGIN_URL = "https://www.douyin.com/chat"
LOGIN_ENTRY_URL = (
    CHAT_LOGIN_URL
    if os.getenv("LOGIN_DESKTOP_LOGIN_PAGE", "creator").strip().lower() == "chat"
    else REMOTE_LOGIN_URL
)
WWW_SELF_URL = "https://www.douyin.com/user/self"
DEFAULT_PROFILE_DIR = (
    Path(__file__).resolve().parents[1] / "state" / "login-profile"
    if os.name == "nt"
    else Path("/data/login-profile")
)
PROFILE_DIR = Path(os.getenv("LOGIN_PROFILE_DIR", str(DEFAULT_PROFILE_DIR))).expanduser()
LOGIN_DESKTOP_MODE = str(
    os.getenv("LOGIN_DESKTOP_MODE", "native" if os.name == "nt" else "novnc")
).strip().lower()
if LOGIN_DESKTOP_MODE not in {"native", "novnc"}:
    LOGIN_DESKTOP_MODE = "native" if os.name == "nt" else "novnc"
IDLE_TIMEOUT_SECONDS = max(300, int(os.getenv("LOGIN_DESKTOP_IDLE_TIMEOUT_SECONDS", "1800")))
STOP_AFTER_EXPORT_SECONDS = max(0, int(os.getenv("LOGIN_DESKTOP_STOP_AFTER_EXPORT_SECONDS", "60")))
STATUS_CACHE_SECONDS = max(1, int(os.getenv("LOGIN_DESKTOP_STATUS_CACHE_SECONDS", "15")))
LOGIN_NETWORK_MODE = str(os.getenv("LOGIN_DESKTOP_PROXY_MODE", "auto")).strip().lower()
if LOGIN_NETWORK_MODE not in {"auto", "direct", "proxy"}:
    LOGIN_NETWORK_MODE = "auto"
LOGIN_PROXY_SERVER = str(os.getenv("LOGIN_DESKTOP_PROXY", "http://proxy:7890")).strip()
LOGIN_PREFLIGHT_TIMEOUT_SECONDS = max(
    3, int(os.getenv("LOGIN_DESKTOP_PREFLIGHT_TIMEOUT_SECONDS", "15"))
)
LOGIN_NETWORK_CACHE_SECONDS = max(
    0, int(os.getenv("LOGIN_DESKTOP_NETWORK_CACHE_SECONDS", "30"))
)
GENERIC_WWW_NAMES = {
    "",
    "我的",
    "我",
    "抖音官网账号",
    "精选",
    "推荐",
    "搜索",
    "关注",
    "朋友",
    "直播",
    "放映厅",
    "短剧",
    "小游戏",
    "客户端",
    "通知",
    "私信",
    "投稿",
    "海量优质视频内容",
    "抖音精选电脑版",
}


class LoginNetworkError(RuntimeError):
    """Raised when the login browser cannot reach Douyin."""

    def __init__(self, message, *, checks=None):
        super().__init__(message)
        self.checks = checks or {}


def _safe_proxy_label(proxy_server):
    if not proxy_server:
        return ""
    try:
        parsed = urlsplit(proxy_server)
        host = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port else ""
        return f"{host}{port}" if host else "configured proxy"
    except ValueError:
        return "configured proxy"


def _probe_login_target(proxy_server=None, timeout_seconds=15):
    """Probe Douyin without inheriting the process proxy environment."""
    if proxy_server:
        handlers = [
            urllib.request.ProxyHandler(
                {"http": proxy_server, "https": proxy_server}
            )
        ]
    else:
        handlers = [urllib.request.ProxyHandler({})]
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        LOGIN_ENTRY_URL,
        headers={"User-Agent": "DouYinSparkFlow-login-preflight/1"},
    )
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            response.read(256)
            status = int(getattr(response, "status", 200))
        if status >= 400:
            raise RuntimeError(f"HTTP {status}")
        return {
            "ok": True,
            "status": status,
            "latency_ms": round((time.monotonic() - started) * 1000),
        }
    except Exception as exc:
        error = str(exc) or exc.__class__.__name__
        if proxy_server:
            error = error.replace(proxy_server, _safe_proxy_label(proxy_server))
        return {
            "ok": False,
            "error": error[:240],
            "latency_ms": round((time.monotonic() - started) * 1000),
        }


class LoginDesktopManager:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._page_operation_lock = asyncio.Lock()
        self.playwright = None
        self.context = None
        self.page = None
        self._last_activity = time.monotonic()
        self._status_cache = None
        self._status_checked_at = 0.0
        self._idle_monitor_task = None
        self._scheduled_stop_task = None
        self._network_route = None
        self._network_checks = {}
        self._network_checked_at = 0.0

    def _network_payload(self):
        route = dict(self._network_route or {})
        return {
            "mode": LOGIN_NETWORK_MODE,
            "selected": route.get("mode", ""),
            "proxy": _safe_proxy_label(LOGIN_PROXY_SERVER) if route.get("mode") == "proxy" else "",
            "checked_at": route.get("checked_at", ""),
            "checks": dict(self._network_checks),
        }

    def _invalidate_network_route(self):
        self._network_route = None
        self._network_checked_at = 0.0

    async def _select_network_route(self, *, force=False):
        now = time.monotonic()
        if (
            not force
            and self._network_route
            and now - self._network_checked_at < LOGIN_NETWORK_CACHE_SECONDS
        ):
            return dict(self._network_route)

        checks = {}
        candidates = []
        if LOGIN_NETWORK_MODE in {"auto", "direct"}:
            candidates.append(("direct", None))
        if LOGIN_NETWORK_MODE in {"auto", "proxy"} and LOGIN_PROXY_SERVER:
            candidates.append(("proxy", LOGIN_PROXY_SERVER))

        for mode, proxy_server in candidates:
            result = await asyncio.to_thread(
                _probe_login_target,
                proxy_server,
                LOGIN_PREFLIGHT_TIMEOUT_SECONDS,
            )
            checks[mode] = result
            if result.get("ok"):
                route = {
                    "mode": mode,
                    "proxy": _safe_proxy_label(proxy_server),
                    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                }
                self._network_checks = checks
                self._network_route = route
                self._network_checked_at = now
                return dict(route)

        self._network_checks = checks
        self._network_route = None
        self._network_checked_at = now
        if LOGIN_NETWORK_MODE == "direct":
            message = "无法直连抖音创作者中心，请检查服务器网络出口"
        elif LOGIN_NETWORK_MODE == "proxy":
            message = f"代理 {_safe_proxy_label(LOGIN_PROXY_SERVER)} 无法访问抖音创作者中心"
        else:
            message = "直连和代理都无法访问抖音创作者中心"
        raise LoginNetworkError(message, checks=checks)

    async def network_preflight(self, *, force=True):
        try:
            route = await self._select_network_route(force=force)
            return {
                "ok": True,
                "route": route,
                "network": self._network_payload(),
            }
        except LoginNetworkError as exc:
            return {
                "ok": False,
                "error": str(exc),
                "network": self._network_payload(),
            }

    def mark_activity(self):
        self._last_activity = time.monotonic()
        self._status_cache = None
        self._status_checked_at = 0.0
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
        self._scheduled_stop_task = None

    async def start_idle_monitor(self):
        if self._idle_monitor_task and not self._idle_monitor_task.done():
            return
        self._idle_monitor_task = asyncio.create_task(self._idle_monitor())

    async def stop_idle_monitor(self):
        if self._idle_monitor_task and not self._idle_monitor_task.done():
            self._idle_monitor_task.cancel()
            await asyncio.gather(self._idle_monitor_task, return_exceptions=True)
        self._idle_monitor_task = None
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()
            await asyncio.gather(self._scheduled_stop_task, return_exceptions=True)
        self._scheduled_stop_task = None

    async def _idle_monitor(self):
        while True:
            await asyncio.sleep(60)
            if self.context and time.monotonic() - self._last_activity >= IDLE_TIMEOUT_SECONDS:
                await self.stop(clear_profile=False)

    def schedule_stop_after_export(self):
        if STOP_AFTER_EXPORT_SECONDS <= 0:
            return
        if self._scheduled_stop_task and not self._scheduled_stop_task.done():
            self._scheduled_stop_task.cancel()

        async def stop_later():
            await asyncio.sleep(STOP_AFTER_EXPORT_SECONDS)
            await self.stop(clear_profile=False)

        self._scheduled_stop_task = asyncio.create_task(stop_later())

    async def reduce_page_activity(self, page):
        try:
            await page.emulate_media(reduced_motion="reduce")
            await page.add_style_tag(
                content="""
                *, *::before, *::after {
                  animation-duration: 0s !important;
                  animation-iteration-count: 1 !important;
                  transition-duration: 0s !important;
                  scroll-behavior: auto !important;
                }
                video, canvas[class*="main-animation"] {
                  visibility: hidden !important;
                }
                """
            )
        except Exception:
            pass

    async def start(self):
        self.mark_activity()
        async with self._lock:
            if self.context and not self._context_is_closed():
                return
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            route = await self._select_network_route()
            self.playwright = await async_playwright().start()
            launch_args = [
                "--start-maximized",
                "--window-position=0,0",
                "--window-size=1600,1000",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-features=Translate,MediaRouter,OptimizationHints,AutofillServerCommunication",
            ]
            if os.name != "nt":
                launch_args.extend(
                    [
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                        "--disable-gpu",
                        "--disable-gpu-compositing",
                        "--disable-software-rasterizer",
                        "--disable-accelerated-2d-canvas",
                        "--disable-accelerated-video-decode",
                        "--renderer-process-limit=2",
                    ]
                )
            launch_options = {
                "headless": False,
                "viewport": {"width": 1600, "height": 1000},
                "args": launch_args,
            }
            if route["mode"] == "proxy":
                launch_options["proxy"] = {"server": LOGIN_PROXY_SERVER}
            else:
                launch_args.append("--no-proxy-server")
            self.context = await self.playwright.chromium.launch_persistent_context(
                str(PROFILE_DIR),
                **launch_options,
            )
            self.page = self.context.pages[0] if self.context.pages else await self.context.new_page()

    def _context_is_closed(self):
        return not self.context or getattr(self.context, "_impl_obj", None) is None

    async def _get_active_page(self):
        await self.ensure_running()
        try:
            if self.page and not self.page.is_closed():
                return self.page
        except Exception:
            pass
        try:
            for candidate in self.context.pages:
                if not candidate.is_closed():
                    self.page = candidate
                    return candidate
        except Exception:
            pass
        self.page = await self.context.new_page()
        return self.page

    async def stop(self, clear_profile=False):
        async with self._lock:
            if self.page:
                try:
                    await self.page.close()
                except Exception:
                    pass
                self.page = None
            if self.context:
                try:
                    await self.context.close()
                except Exception:
                    pass
                self.context = None
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None
            if clear_profile and PROFILE_DIR.exists():
                shutil.rmtree(PROFILE_DIR, ignore_errors=True)
            self._status_cache = None
            self._status_checked_at = 0.0

    async def reset(self):
        self.mark_activity()
        await self.stop(clear_profile=True)
        await self.start()

    async def ensure_running(self):
        if not self.context or self._context_is_closed():
            await self.start()

    async def focus_browser(self):
        self.mark_activity()
        page = await self._get_active_page()
        try:
            await page.bring_to_front()
        except Exception:
            pass
        return {
            "ok": True,
            "url": page.url,
            "mode": LOGIN_DESKTOP_MODE,
            "network": self._network_payload(),
        }

    async def status(self):
        now = time.monotonic()
        if self._status_cache is not None and now - self._status_checked_at < STATUS_CACHE_SECONDS:
            return dict(self._status_cache)

        logged_in = False
        username = ""
        unique_id = ""
        current_url = ""

        if not self.context or self._context_is_closed():
            payload = {
                "running": False,
                "logged_in": False,
                "username": "",
                "unique_id": "",
                "current_url": "",
                "profile_dir": str(PROFILE_DIR),
                "network": self._network_payload(),
            }
            self._status_cache = payload
            self._status_checked_at = now
            return dict(payload)

        page = None
        try:
            if self.page and not self.page.is_closed():
                page = self.page
            else:
                for candidate in self.context.pages:
                    if not candidate.is_closed():
                        self.page = candidate
                        page = candidate
                        break
        except Exception:
            self.page = None
            self.context = None
            payload = {
                "running": False,
                "logged_in": False,
                "username": "",
                "unique_id": "",
                "current_url": "",
                "profile_dir": str(PROFILE_DIR),
                "network": self._network_payload(),
            }
            self._status_cache = payload
            self._status_checked_at = now
            return dict(payload)

        if page:
            current_url = page.url
            if not self._page_operation_lock.locked():
                try:
                    result = await collect_login_result(page, self.context, timeout_ms=1000)
                    logged_in = True
                    username = result["username"]
                    unique_id = result["unique_id"]
                except Exception:
                    pass
                if not logged_in and await self._chat_session_ready(page):
                    logged_in = True
                    username = "抖音网页账号（保存时核验身份）"

        payload = {
            "running": True,
            "logged_in": logged_in,
            "username": username,
            "unique_id": unique_id,
            "current_url": current_url,
            "profile_dir": str(PROFILE_DIR),
            "network": self._network_payload(),
        }
        self._status_cache = payload
        self._status_checked_at = now
        return dict(payload)

    async def open_login(self):
        self.mark_activity()
        try:
            await asyncio.wait_for(self._page_operation_lock.acquire(), timeout=5)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("login page is busy; retry shortly") from exc
        try:
            page = await self._get_active_page()
            if page.url.startswith(LOGIN_ENTRY_URL):
                return {"ok": True, "url": page.url, "network": self._network_payload()}
            refresh_url = LOGIN_ENTRY_URL
            try:
                await page.goto(refresh_url, wait_until="commit", timeout=30000)
            except Exception:
                await self.stop(clear_profile=False)
                await self.start()
                page = await self._get_active_page()
                await page.goto(refresh_url, wait_until="commit", timeout=30000)
            return {"ok": True, "url": page.url, "network": self._network_payload()}
        finally:
            self._page_operation_lock.release()

    async def refresh_login_qr(self):
        self.mark_activity()
        try:
            await asyncio.wait_for(self._page_operation_lock.acquire(), timeout=5)
        except asyncio.TimeoutError as exc:
            raise RuntimeError("login page is busy; retry shortly") from exc
        try:
            return await self._refresh_login_qr_locked()
        finally:
            self._page_operation_lock.release()

    async def _refresh_login_qr_locked(self):
        refresh_url = LOGIN_ENTRY_URL
        try:
            page = await self._get_active_page()
            await page.goto(refresh_url, wait_until="commit", timeout=30000)
        except Exception:
            await self.reset()
            page = await self._get_active_page()
            await page.goto(refresh_url, wait_until="commit", timeout=30000)

        deadline = asyncio.get_running_loop().time() + 45
        logged_in = False
        qr_ready = False
        while asyncio.get_running_loop().time() < deadline:
            for selector in (
                'img[class*="qrcode"]',
                'img[src^="data:image/png;base64"]',
            ):
                candidates = page.locator(selector)
                for index in range(await candidates.count()):
                    qr = candidates.nth(index)
                    try:
                        if not await qr.is_visible():
                            continue
                        box = await qr.bounding_box()
                        if not box or box["width"] < 120 or box["height"] < 120:
                            continue
                        ratio = box["width"] / max(1, box["height"])
                        if 0.8 <= ratio <= 1.25:
                            qr_ready = True
                            break
                    except Exception:
                        pass
                if qr_ready:
                    break

            if "/creator-micro/" in page.url:
                logged_in = True
                break
            if await self._chat_session_ready(page):
                logged_in = True
                break
            try:
                await collect_login_result(page, self.context, timeout_ms=800)
                logged_in = True
                break
            except Exception:
                pass
            await asyncio.sleep(0.75)

        if not qr_ready and not logged_in:
            raise RuntimeError("Douyin login page did not expose a QR code or a logged-in session")

        await self.reduce_page_activity(page)
        if qr_ready:
            try:
                await page.wait_for_function(
                    "() => !/\u4e8c\u7ef4\u7801\u5931\u6548|\u4e8c\u7ef4\u7801\u8fc7\u671f/.test(document.body?.innerText || '')",
                    timeout=30000,
                )
            except Exception:
                pass
        return {"ok": True, "url": page.url, "logged_in": logged_in, "qr_ready": qr_ready}

    async def _chat_session_ready(self, page):
        parsed = urlsplit(page.url)
        if parsed.hostname != "www.douyin.com" or parsed.path.rstrip("/") != "/chat":
            return False
        try:
            cookies = await self.context.cookies(CHAT_LOGIN_URL)
            if not any(c.get("name") in {"sessionid", "sessionid_ss"} and c.get("value") for c in cookies):
                return False
            for text in ("扫码登录", "验证码登录", "Log in to Douyin"):
                login = page.get_by_text(text, exact=True)
                if await login.count() and await login.first.is_visible():
                    return False
            search = page.locator('input[placeholder="搜索"]')
            return bool(await search.count() and await search.first.is_visible())
        except Exception:
            return False

    async def export(self):
        self.mark_activity()
        async with self._page_operation_lock:
            page = await self._get_active_page()
            # A normal web login also signs into Creator Center through Douyin's
            # own session. Read the visible self card before binding any account.
            if urlsplit(page.url).hostname == "www.douyin.com":
                if not await self._chat_session_ready(page):
                    raise RuntimeError("请先在普通聊天页完成本人登录")
                await page.goto(REMOTE_LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
            result = await collect_login_result(page, self.context, timeout_ms=20000)
        self.schedule_stop_after_export()
        return result


manager = LoginDesktopManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_login_api_token()
    await manager.start_idle_monitor()
    try:
        yield
    finally:
        await manager.stop_idle_monitor()
        await manager.stop(clear_profile=False)


app = FastAPI(title="Douyin Login Desktop", lifespan=lifespan)
ordinary_run_lock = asyncio.Lock()


@app.middleware('http')
async def authenticate_service(request: Request, call_next):
    if request.url.path == '/health':
        return await call_next(request)
    try:
        expected = read_login_api_token()
    except (OSError, RuntimeError):
        return JSONResponse({'detail': 'Login service is not ready'}, status_code=503)
    supplied = request.headers.get('x-sparkflow-token', '')
    if not supplied or not hmac.compare_digest(supplied.encode('utf-8'), expected.encode('utf-8')):
        return JSONResponse({'detail': 'Unauthorized'}, status_code=401)
    if request.url.path.startswith('/debug/') and os.getenv('SPARKFLOW_ENABLE_LOGIN_DEBUG') != '1':
        return JSONResponse({'detail': 'Not found'}, status_code=404)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"ok": True}


@app.post('/ordinary/run')
async def ordinary_run(request: Request):
    from core.ordinary_dispatch import select_saved_accounts
    from core.ordinary_chat import run_accounts
    from utils.config import get_config, get_userData
    from utils import process_lock
    if ordinary_run_lock.locked() or not process_lock.is_locked(Path('logs/task.run.lock')):
        raise HTTPException(status_code=409, detail='A task lease is required and the desktop must be idle')
    lines = Path('logs/task.run.lock').read_text().splitlines()
    leases = [line.split('=',1)[1] for line in lines if line.startswith('lease=')]
    supplied = request.headers.get('x-sparkflow-task-lease', '')
    if (len(leases) != 1 or 'purpose=manual_verification' in lines
            or not hmac.compare_digest(supplied.encode(), leases[0].encode())):
        raise HTTPException(status_code=409, detail='Invalid or non-send task lease')
    try:
        selected = select_saved_accounts(await request.json(), get_userData(force_reload=True))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail='Invalid saved account selection')
    config = get_config(force_reload=True)
    config['_visibleSender'] = True
    if ordinary_run_lock.locked():
        raise HTTPException(status_code=409, detail='Desktop is already in use')
    async with ordinary_run_lock:
        await run_accounts(config, selected)
    return {'ok': True, 'processedAccounts': len(selected)}


@app.post('/ordinary/prepare-check')
async def ordinary_prepare_check(request: Request):
    from core.ordinary_dispatch import select_saved_accounts
    from core.ordinary_chat import check_prepared
    from utils.config import get_config,get_userData
    from utils import process_lock
    lock=Path('logs/task.run.lock')
    if ordinary_run_lock.locked() or not process_lock.is_locked(lock):
        raise HTTPException(status_code=409,detail='An exclusive task lease is required')
    lines=lock.read_text().splitlines()
    leases=[line.split('=',1)[1] for line in lines if line.startswith('lease=')]
    supplied=request.headers.get('x-sparkflow-task-lease','')
    if len(leases)!=1 or 'purpose=manual_verification' in lines or not hmac.compare_digest(supplied.encode(),leases[0].encode()):
        raise HTTPException(status_code=409,detail='Invalid task lease')
    try:
        selected=select_saved_accounts(await request.json(),get_userData(force_reload=True))
        if len(selected)!=1 or len(selected[0]['targets'])!=1:raise ValueError('One target required')
    except (ValueError,TypeError):
        raise HTTPException(status_code=400,detail='Select one saved account and one configured target')
    config=get_config(force_reload=True);config['_visibleSender']=True
    if ordinary_run_lock.locked():raise HTTPException(status_code=409,detail='Desktop is already in use')
    async with ordinary_run_lock:
        try:return await check_prepared(selected[0],config)
        except RuntimeError as exc:
            # This endpoint never clicks send or changes dispatch/history state.
            raise HTTPException(status_code=409,detail=str(exc)) from exc


@app.post('/ordinary/contacts')
async def ordinary_contacts(request: Request):
    from core.ordinary_chat import fetch_conversation_names
    from utils.config import get_config, get_userData
    from utils import process_lock
    if ordinary_run_lock.locked() or process_lock.is_locked(Path('logs/task.run.lock')):
        raise HTTPException(status_code=409, detail='A task or owner verification is in progress')
    payload = await request.json()
    if not isinstance(payload, dict) or set(payload) != {'account_ref', 'unique_id'} or not all(isinstance(v, str) and v for v in payload.values()):
        raise HTTPException(status_code=400, detail='Invalid account lookup')
    selected = [u for u in get_userData(force_reload=True) if u.get('account_ref') == payload.get('account_ref') and u.get('unique_id') == payload.get('unique_id')]
    if len(selected) != 1:
        raise HTTPException(status_code=400, detail='Unknown saved account')
    config = get_config(force_reload=True)
    config['_visibleSender'] = True
    if ordinary_run_lock.locked():
        raise HTTPException(status_code=409, detail='Desktop is already in use')
    async with ordinary_run_lock:
        try:
            contacts = await fetch_conversation_names(selected[0], config)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {'contacts': contacts}


@app.get("/preflight")
async def preflight():
    result = await manager.network_preflight(force=True)
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result)
    return result


@app.get("/status")
async def status():
    return await manager.status()


@app.post("/open-login")
async def open_login():
    try:
        return await manager.open_login()
    except LoginNetworkError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "LOGIN_NETWORK_UNAVAILABLE", "message": str(exc), "checks": exc.checks},
        ) from exc


@app.post("/reset")
async def reset():
    await manager.reset()
    return {"ok": True}


@app.post("/close")
async def close():
    await manager.stop(clear_profile=True)
    return {"ok": True}


@app.post("/focus")
async def focus():
    return await manager.focus_browser()


@app.post("/refresh-qr")
async def refresh_qr():
    try:
        return await manager.refresh_login_qr()
    except LoginNetworkError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "LOGIN_NETWORK_UNAVAILABLE", "message": str(exc), "checks": exc.checks},
        ) from exc


@app.post("/export")
async def export():
    try:
        result = await manager.export()
    except Exception as exc:
        # Do not manufacture an account ID from CSRF/session cookies when the
        # real self identity cannot be read.
        raise HTTPException(status_code=400, detail="登录身份尚未核验，请确认本人已登录，并等待创作者中心首页显示账号资料后重试保存") from exc
    return {"ok": True, "result": result}


@app.get("/qr")
async def login_qr():
    if manager._page_operation_lock.locked():
        raise HTTPException(status_code=503, detail="login page is busy; retry shortly")
    try:
        page = await manager._get_active_page()
    except LoginNetworkError as exc:
        raise HTTPException(
            status_code=502,
            detail={"code": "LOGIN_NETWORK_UNAVAILABLE", "message": str(exc), "checks": exc.checks},
        ) from exc
    expired = await page.locator('[class*="qrcode_expired"]').count()
    if expired and await page.locator('[class*="qrcode_expired"]').first.is_visible():
        raise HTTPException(status_code=409, detail="login QR code has expired")
    selectors = (
        'img[class*="qrcode"]',
        'img[src^="data:image/png;base64"]',
    )
    for selector in selectors:
        candidates = page.locator(selector)
        for index in range(await candidates.count()):
            candidate = candidates.nth(index)
            try:
                box = await candidate.bounding_box()
                if not box or box["width"] < 120 or box["height"] < 120:
                    continue
                ratio = box["width"] / max(1, box["height"])
                if not 0.8 <= ratio <= 1.25:
                    continue
                data = await candidate.screenshot(type="png")
                return Response(
                    content=data,
                    media_type="image/png",
                    headers={"Cache-Control": "no-store, max-age=0"},
                )
            except Exception:
                continue
    raise HTTPException(status_code=202, detail="login QR code is still starting", headers={"Retry-After": "2"})


@app.get("/debug/screenshot")
async def debug_screenshot():
    page = await manager._get_active_page()
    data = await page.screenshot(full_page=False, type="png")
    return Response(content=data, media_type="image/png")


@app.get("/debug/snapshot")
async def debug_snapshot():
    page = await manager._get_active_page()
    items = await page.evaluate(
        r"""() => {
            const visible = (el) => {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
            };
            const textOf = (el) => String(el.innerText || el.textContent || el.getAttribute('aria-label') || el.title || '').replace(/\s+/g, ' ').trim();
            const nodes = [...document.querySelectorAll('button, a, [role="button"], [aria-label], input, textarea, [contenteditable="true"], [class*="message"], [class*="chat"], [class*="im"]')];
            return nodes.filter(visible).slice(0, 300).map((el, i) => {
                const r = el.getBoundingClientRect();
                return {
                    i,
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    aria: el.getAttribute('aria-label') || '',
                    title: el.title || '',
                    text: textOf(el).slice(0, 120),
                    cls: String(el.className || '').slice(0, 120),
                    contenteditable: el.getAttribute('contenteditable') || '',
                    rect: {x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height)}
                };
            });
        }"""
    )
    return {"url": page.url, "title": await page.title(), "items": items}


@app.post("/debug/action")
async def debug_action(request: Request):
    page = await manager._get_active_page()
    payload = await request.json()
    action = payload.get("action")
    if action == "click_text":
        text = str(payload.get("text") or "")
        exact = bool(payload.get("exact", False))
        await page.get_by_text(text, exact=exact).first.click(timeout=int(payload.get("timeout", 5000)))
    elif action == "click_at":
        await page.mouse.click(float(payload["x"]), float(payload["y"]))
    elif action == "wheel":
        await page.mouse.wheel(float(payload.get("dx", 0)), float(payload.get("dy", 0)))
    elif action == "type":
        await page.keyboard.type(str(payload.get("text") or ""), delay=int(payload.get("delay", 20)))
    elif action == "press":
        await page.keyboard.press(str(payload.get("key") or "Enter"))
    elif action == "goto":
        await page.goto(str(payload.get("url") or REMOTE_LOGIN_URL), wait_until="commit", timeout=15000)
    elif action == "eval":
        result = await page.evaluate(str(payload.get("script") or "undefined"))
        return {"ok": True, "result": result, "url": page.url}
    elif action == "list_frames":
        frames = []
        for fr in page.frames:
            frames.append({"url": fr.url, "name": fr.name})
        return {"ok": True, "frames": frames, "url": page.url}
    elif action == "eval_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        script = str(payload.get("script") or "undefined")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": f"no frame matching '{frame_url_match}' found", "url": page.url}
        result = await target_frame.evaluate(script)
        return {"ok": True, "result": result, "url": page.url, "frame_url": target_frame.url}
    elif action == "click_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        selector = str(payload.get("selector") or "")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": f"no frame matching '{frame_url_match}' found"}
        await target_frame.click(selector, timeout=int(payload.get("timeout", 5000)))
        return {"ok": True, "url": page.url, "frame_url": target_frame.url}
    elif action == "type_in_frame":
        frame_url_match = str(payload.get("frame_url") or "")
        selector = str(payload.get("selector") or "")
        text = str(payload.get("text") or "")
        target_frame = None
        for fr in page.frames:
            if frame_url_match and frame_url_match in fr.url:
                target_frame = fr
                break
        if not target_frame:
            return {"ok": False, "error": "no frame found"}
        await target_frame.fill(selector, text, timeout=int(payload.get("timeout", 5000)))
        return {"ok": True, "url": page.url}
    else:
        raise HTTPException(status_code=400, detail=f"unknown action {action!r}")
    return {"ok": True, "url": page.url}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("LOGIN_DESKTOP_API_PORT", "18090")), reload=False)
