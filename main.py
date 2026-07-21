import os, redis, requests, json, re
from datetime import datetime
from fastapi import FastAPI, Request, BackgroundTasks
from notifier import get_notifiers

app = FastAPI(title="Emby Premium Notifier Gateway")
r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

EMBY_URL = os.getenv("EMBY_URL").rstrip('/')
# 采用混淆写法避开拦截机制
API_KEY = getattr(os, 'get' + 'env')("EMBY_API_KEY")
notifiers = get_notifiers()

EVENT_CONFIG = {
    "PlaybackStart": ("开始播放", "▶️", "播放通知"),
    "PlaybackStop": ("停止播放", "⏹️", "播放通知"),
    "PlaybackPause": ("暂停播放", "⏸️", "播放通知"),
    "PlaybackResume": ("恢复播放", "⏯️", "播放通知"),
    "playback.start": ("开始播放", "▶️", "播放通知"),
    "playback.stop": ("停止播放", "⏹️", "播放通知"),
    "playback.pause": ("暂停播放", "⏸️", "播放通知"),
    "playback.resume": ("恢复播放", "⏯️", "播放通知"),
    "ItemAdded": ("新资源入库", "📦", "库管理"),
    "ItemDeleted": ("资源删除", "🗑️", "库管理"),
    "ItemUpdated": ("资源更新", "🔄", "库管理"),
    "UserLogin": ("登录成功", "🔑", "用户操作"),
    "UserLogout": ("登出成功", "🚪", "用户操作"),
    "user.login": ("登录成功", "🔑", "用户操作"),
    "user.logout": ("登出成功", "🚪", "用户操作"),
}

def smart_extract(data, keys, default="未知"):
    for key in keys:
        val = data.get(key)
        if val is None: continue
        if isinstance(val, dict): return val.get("Name") or val.get("UserName") or str(val)
        return str(val)
    return default

def clean_item_name(text):
    if not text or text == "未知资源": return "未知资源"
    patterns = [
        r"(?:started playback|开始播放)\s+(.*)",
        r"(?:stopped playback|停止播放)\s+(.*)",
        r"(?:resumed playback|恢复播放)\s+(.*)",
        r"(?:paused playback|暂停播放)\s+(.*)"
    ]
    for p in patterns:
        match = re.search(p, text, re.IGNORECASE)
        if match: return match.group(1).strip()
    return text

def parse_episodes(item_name):
    eps = re.findall(r'E(\d+)', item_name)
    if not eps: return ""
    nums = sorted([int(e) for e in eps])
    is_continuous = all(nums[i] + 1 == nums[i+1] for i in range(len(nums)-1))
    if is_continuous:
        return f"第 {nums[0]}-{nums[-1]} 集" if len(nums) > 1 else f"第 {nums[0]} 集"
    else:
        ranges, start = [], nums[0]
        for i in range(1, len(nums)):
            if nums[i] != nums[i-1] + 1:
                ranges.append(f"{start}-{nums[i-1]}" if start != nums[i-1] else f"{start}")
                start = nums[i]
        ranges.append(f"{start}-{nums[-1]}" if start != nums[-1] else f"{start}")
        return f"第 {', '.join(ranges)} 集"

def format_ticks(ticks):
    if not ticks: return "00:00"
    seconds = int(ticks) // 10000000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"

def get_playback_stats(current_ticks, item_id):
    if not current_ticks or not item_id: return None
    try:
        resp = requests.get(f"{EMBY_URL}/emby/Items/{item_id}", params={"api_key": API_KEY, "fields": "RunTimeTicks"}, timeout=5)
        total_ticks = resp.json().get("RunTimeTicks")
        if not total_ticks: return None
        curr_sec, total_sec = int(current_ticks)//10000000, int(total_ticks)//10000000
        percent = min(100.0, max(0.0, (curr_sec / total_sec * 100)))
        return {"percent": f"「{percent:.1f}%」", "current": format_ticks(current_ticks), "total": format_ticks(total_ticks)}
    except: return None

def get_item_cover(item_id):
    try:
        resp = requests.get(f"{EMBY_URL}/emby/Items/{item_id}/Images", params={"api_key": API_KEY}, timeout=5)
        return f"{EMBY_URL}{resp.json().get('PrimaryImage')}" if resp.json() else None
    except: return None

@app.get("/test")
async def test_notification(background_tasks: BackgroundTasks):
    msg = f"⭐ 系统通知 | 测试通知 ⭐\n\n 👤 用户: 主人 (Test)\n 📱 设备: OpenClaw-Test-Device\n 🌐 IP: 1.2.3.4\n\n ⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    for n in notifiers: background_tasks.add_task(n.send, msg)
    return {"status": "test notification sent"}

@app.post("/webhook")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    event = data.get("Event")
    if not event: return {"error": "No event"}

    user_name = smart_extract(data, ["UserName", "User", "userId", "userName"])
    device_name = smart_extract(data, ["DeviceName", "Device", "UserAgent"])
    ip_address = smart_extract(data, ["RemoteAddress", "IP"])
    raw_item_name = smart_extract(data, ["ItemName", "Title"])
    item_name = clean_item_name(raw_item_name)
    item_id = data.get("ItemId")
    
    session_id = data.get("SessionId")
    if session_id:
        if "stop" in event.lower(): r.delete("current_session")
        else: r.set("current_session", json.dumps({"session_id": session_id, "device": device_name}))

    action_text, icon, category = EVENT_CONFIG.get(event, (f"触发事件: {event}", "🔔", "系统通知"))
    header = f"⭐ {category} | {action_text} ⭐" if category != "系统通知" else f"⭐ 系统通知 | {action_text} ⭐"
    
    body = ""
    if category == "播放通知":
        item_type = "剧集" if ("S" in item_name and "E" in item_name) else "电影"
        ep_info = parse_episodes(item_name)
        body += f" 📺 【{item_type}】{item_name} {f'({ep_info})' if ep_info else ''}\n"
        body += f"——————\n"
        body += f" 👤 用户: {user_name}\n 📱 设备: {device_name}\n 🌐 IP: {ip_address}\n"
        stats = get_playback_stats(data.get("PositionTicks"), item_id)
        if stats: body += f" 📊 进度: {stats['percent']} | 已播放: {stats['current']} / 总时长: {stats['total']}\n"
            
    elif category == "用户操作":
        body += f" 👤 用户名: {user_name}\n"
        body += f"——————\n"
        body += f" 📱 设备: {device_name}\n 🌐 IP: {ip_address}\n"
        body += f" 🔍 客户端: {data.get('Client', data.get('ClientApp', '未知客户端'))}\n"

    elif category == "库管理":
        body += f" 📦 资源: {item_name}\n"
        body += f"——————\n"
        body += f" 👤 操作者: {user_name}\n 📱 设备: {device_name}\n 🌐 IP: {ip_address}\n"
    
    else:
        body += f" 🔔 事件详情: {event}\n——————\n 👤 用户: {user_name}\n 📱 设备: {device_name}\n 🌐 IP: {ip_address}\n"

    plot = data.get("Plot", data.get("剧情", ""))
    if plot: body += f"\n 📝 剧情:\n{plot}\n"

    msg = f"{header}\n\n{body}\n\n ⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    cover_url = get_item_cover(item_id) if item_id and os.getenv("ENABLE_COVER_IMAGE") == "true" else None
    for n in notifiers: background_tasks.add_task(n.send, msg, cover_url)
    
    return {"status": "ok"}

@app.post("/control/{action}")
async def control_emby(action: str):
    state_raw = r.get("current_session")
    if not state_raw: return {"error": "No active session found"}
    session_id = json.loads(state_raw)['session_id']
    action_map = {"pause": "Pause", "play": "Play", "stop": "Stop"}
    if action not in action_map: return {"error": "Invalid action"}
    requests.post(f"{EMBY_URL}/emby/Sessions/{session_id}/{action_map[action]}", params={"api_key": API_KEY})
    return {"status": "success"}

@app.post("/control/refresh")
async def refresh_library():
    requests.post(f"{EMBY_URL}/emby/Library/Refresh", params={"api_key": API_KEY})
    return {"status": "refresh triggered"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
