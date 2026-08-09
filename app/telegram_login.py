import glob
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from config import SESSION_PATH
from redis_store import get, set_value, delete, push_event, r
from telegram_session_lock import SESSION_LOCK


def _listener_running() -> bool:
    try:
        from bot_runner import manager
        return bool(manager.is_running())
    except Exception:
        return False


def _api() -> tuple[int, str]:
    api_id = int(get("tg_api_id", "0") or 0)
    api_hash = get("tg_api_hash", "") or ""
    if not api_id or not api_hash:
        raise ValueError("请先保存 API ID 和 API HASH")
    return api_id, api_hash


def save_api(api_id: str, api_hash: str, phone: str) -> None:
    api_id = str(api_id or "").strip()
    api_hash = str(api_hash or "").strip()
    phone = str(phone or "").strip()
    if not api_id.isdigit():
        raise ValueError("API ID 必须是数字")
    if not api_hash:
        raise ValueError("API HASH 不能为空")
    if not phone.startswith("+"):
        raise ValueError("手机号必须带国家区号，例如 +86xxxx")
    set_value("tg_api_id", api_id)
    set_value("tg_api_hash", api_hash)
    set_value("tg_phone", phone)
    push_event("info", "Telegram API 配置已保存")


async def send_code() -> str:
    if _listener_running():
        raise ValueError("请先停止监听后再登录或重新登录")
    api_id, api_hash = _api()
    phone = get("tg_phone", "") or ""
    if not phone:
        raise ValueError("请先保存手机号")
    with SESSION_LOCK:
        client = TelegramClient(SESSION_PATH, api_id, api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
            set_value("tg_phone_code_hash", sent.phone_code_hash)
            push_event("info", f"验证码已发送到 {phone}")
            return "验证码已发送，请在 Telegram 里查看"
        finally:
            await client.disconnect()


async def sign_in(code: str, password: str = "") -> dict:
    if _listener_running():
        raise ValueError("请先停止监听后再登录或重新登录")
    api_id, api_hash = _api()
    phone = get("tg_phone", "") or ""
    phone_code_hash = get("tg_phone_code_hash", "") or ""
    code = str(code or "").strip().replace(" ", "")
    password = str(password or "")
    if not phone or not phone_code_hash:
        raise ValueError("请先发送验证码")
    if not code:
        raise ValueError("验证码不能为空")

    with SESSION_LOCK:
        client = TelegramClient(SESSION_PATH, api_id, api_hash)
        await client.connect()
        try:
            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
            except SessionPasswordNeededError:
                if not password:
                    raise ValueError("需要二步验证密码，请填写后再提交")
                await client.sign_in(password=password)
            me = await client.get_me()
            info = {
                "id": me.id,
                "username": me.username or "",
                "first_name": me.first_name or "",
                "phone": phone,
            }
            set_value("tg_logged_in", "1")
            set_value("tg_current_user", f"@{me.username}" if me.username else (me.first_name or str(me.id)))
            set_value("tg_current_id", str(me.id))
            delete("tg_phone_code_hash")
            push_event("success", f"Telegram 登录成功：{info['username'] or info['first_name'] or info['id']}")
            return info
        finally:
            await client.disconnect()


async def status() -> dict:
    if _listener_running():
        if (get("tg_logged_in", "0") or "0") == "1":
            return {
                "logged_in": True,
                "user": get("tg_current_user", "已登录") or "已登录",
                "id": get("tg_current_id", ""),
            }
        return {"logged_in": False, "user": "未登录"}
    api_id_raw = get("tg_api_id", "0") or "0"
    api_hash = get("tg_api_hash", "") or ""
    if not api_id_raw.isdigit() or not int(api_id_raw) or not api_hash:
        return {"logged_in": False, "user": "未配置 API"}
    with SESSION_LOCK:
        client = TelegramClient(SESSION_PATH, int(api_id_raw), api_hash)
        await client.connect()
        try:
            ok = await client.is_user_authorized()
            if not ok:
                set_value("tg_logged_in", "0")
                return {"logged_in": False, "user": "未登录"}
            me = await client.get_me()
            user = f"@{me.username}" if me.username else (me.first_name or str(me.id))
            set_value("tg_logged_in", "1")
            set_value("tg_current_user", user)
            set_value("tg_current_id", str(me.id))
            return {"logged_in": True, "user": user, "id": me.id}
        finally:
            await client.disconnect()


async def logout() -> str:
    api_id_raw = get("tg_api_id", "0") or "0"
    api_hash = get("tg_api_hash", "") or ""
    if api_id_raw.isdigit() and int(api_id_raw) and api_hash:
        with SESSION_LOCK:
            client = TelegramClient(SESSION_PATH, int(api_id_raw), api_hash)
            await client.connect()
            try:
                if await client.is_user_authorized():
                    await client.log_out()
            finally:
                await client.disconnect()
    for path in glob.glob(SESSION_PATH + "*"):
        try:
            os.remove(path)
        except Exception:
            pass
    delete("tg_logged_in", "tg_current_user", "tg_current_id", "tg_phone_code_hash")
    push_event("warning", "已退出当前 Telegram 账号")
    return "已退出当前 Telegram 账号"
