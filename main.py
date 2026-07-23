import os, redis, requests, json, re, logging
from datetime import datetime
from fastapi import FastAPI, Request, BackgroundTasks, Query
from notifier import get_notifiers

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("emby-notifier")

app = FastAPI(title="Emby Premium Notifier Gateway")
r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"), port=6379, decode_responses=True)

EMBY_URL = (os.getenv("EMBY_URL") or "http://localhost:8096").rstrip('/')
API_KEY = os.getenv("EMBY_API_KEY")
notifiers = get_notifiers()

# Redis 键名
SESSIONS_KEY = "emby:sessions"
LAST_ACTIVE_USER = "emby:last_user"

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
    if seconds >= 3600:
        return f"{seconds // 3600:02d}:{(seconds % 3600) // 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60:02d}:{seconds % 60:02d}"

def get_item_details(item_id):
    if not item_id: return None
    try:
        resp = requests.get(f"{EMBY_URL}/emby/Items/{item_id}", params={"api_key": API_KEY, "fields": "RunTimeTicks,CommunityRating,ProductionYear,Genres"}, timeout=5)
        data = resp.json()
        return {
            "total_ticks": data.get("RunTimeTicks"),
            "rating": data.get("CommunityRating"),
            "year": data.get("ProductionYear"),
            "genres": ",".join(data.get("Genres", [])),
            "name": data.get("Name")
        }
    except Exception as e:
        logger.error(f"Failed to get item details for {item_id}: {e}")
        return None

def get_playback_stats(current_ticks, details):
    if not current_ticks or not details or not details["total_ticks"]: return None
    try:
        curr_sec, total_sec = int(current_ticks)//10000000, int(details["total_ticks"])//10000000
        percent = min(100.0, max(0.0, (curr_sec / total_sec * 100)))
        return {"percent": f"{percent:.1f}%", "current": format_ticks(current_ticks), "total": format_ticks(details["total_ticks"])}
    except: return None

def get_item_cover(item_id):
    try:
        resp = requests.get(f"{EMBY_URL}/emby/Items/{item_id}/Images", params={"api_key": API_KEY}, timeout=5)
        return f"{EMBY_URL}{resp.json().get('PrimaryImage')}" if resp.json() else None
    except: return None

@app.get("/test")
async def test_notification(background_tasks: BackgroundTasks):
    msg = f"⭐ <b>系统通知 | 测试通知</b> ⭐\n\n👤 用户: <code>主人 (Test)</code>\n📱 设备: <code>OpenClaw-Test-Device</code>\n🌐 IP: <code>1.2.3.4</code>\n\n⏰ 时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    for n in notifiers: background_tasks.add_task(n.send, msg)
    return {"status": "test notification sent"}

@app.post("/webhook")
async def emby_webhook(request: Request, background_tasks: BackgroundTasks):
    data = await request.json()
    logger.info(f"Received Webhook Payload: {json.dumps(data, ensure_ascii=False)}")
    event = data.get("Event")
    if not event: return {"error": "No event"}

    user_name = smart_extract(data, ["UserName", "User", "userId", "userName"])
    device_name = smart_extract(data, ["DeviceName", "Device", "Client"])
    ip_address = smart_extract(data, ["RemoteEndPoint", "RemoteAddress", "IP"])
    raw_item_name = smart_extract(data, ["Name", "ItemName", "Title"])
    item_name = clean_item_name(raw_item_name)
    item_id = deep_search(data, "Id") or data.get("ItemId")
    
    session_id = data.get("SessionId")
    if session_id:
        if "stop" in event.lower() or "logout" in event.lower(): 
            r.hdel(SESSIONS_KEY, user_name)
        else: 
            r.hset(SESSIONS_KEY, user_name, json.dumps({"session_id": session_id, "device": device_name, "item_id": item_id}))
            r.set(LAST_ACTIVE_USER, user_name)

    action_text, icon, category = EVENT_CONFIG.get(event, (f"触发事件: {event}", "🔔", "系统通知"))
    header = f"⭐ <b>{category} | {action_text}</b> ⭐" if category != "系统通知" else f"⭐ <b>系统通知 | {action_text}</b> ⭐"
    
    body = ""
    if category == "播放通知":
        details = get_item_details(item_id)
        item_type = "剧集" if ("S" in item_name and "E" in item_name) else "电影"
        ep_info = parse_episodes(item_name)
        
        res_info = f"📺 <b>【{item_type}】{item_name}</b>"
        if details:
            year = f"({details['year']})" if details['year'] else ""
            rating = f" ⭐ {details['rating']}" if details['rating'] else ""
            res_info += f" {year}{rating}"
        if ep_info: res_info += f" <code>{ep_info}</code>"
        
        body += f"{res_info}\n"
        body += f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        body += f"👤 用户: <code>{user_name}</code>\n📱 设备: <code>{device_name}</code>\n🌐 IP: <code>{ip_address}</code>\n"
        stats = get_playback_stats(deep_search(data, "PositionTicks"), details)
        if stats: body += f"📊 进度: <code>{stats['percent']}</code> | {stats['current']} / {stats['total']}\n"
            
    elif category == "用户操作":
        body += f"👤 用户名: <code>{user_name}</code>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        body += f"📱 设备: <code>{device_name}</code>\n🌐 IP: <code>{ip_address}</code>\n"
        body += f"🔍 客户端: <code>{data.get('Client', data.get('ClientApp', '未知客户端'))}</code>\n"

    elif category == "库管理":
        body += f"📦 资源: <code>{item_name}</code>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        body += f"👤 操作者: <code>{user_name}</code>\n📱 设备: <code>{device_name}</code>\n🌐 IP: <code>{ip_address}</code>\n"
    
    else:
        body += f"🔔 事件详情: <code>{event}</code>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        body += f"👤 用户: <code>{user_name}</code>\n📱 设备: <code>{device_name}</code>\n🌐 IP: <code>{ip_address}</code>\n"

    plot = data.get("Plot", data.get("剧情", ""))
    if plot: body += f"\n📝 <b>剧情:</b>\n<i>{plot}</i>\n"

    msg = f"{header}\n\n{body}\n\n⏰ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
    cover_url = get_item_cover(item_id) if item_id and os.getenv("ENABLE_COVER_IMAGE") == "true" else None
    for n in notifiers: background_tasks.add_task(n.send, msg, cover_url)
    
    return {"status": "ok"}

@app.post("/control/{action}")
async def control_emby(action: str, user: str = Query(None), background_tasks: BackgroundTasks = None):
    # 确定目标用户
    target_user = user or r.get(LAST_ACTIVE_USER)
    if not target_user: return {"error": "No active user found"}
    
    state_raw = r.hget(SESSIONS_KEY, target_user)
    if not state_raw: return {"error": f"No active session found for user: {target_user}"}
    
    session_data = json.loads(state_raw)
    session_id = session_data['session_id']
    item_id = session_data.get('item_id')
    
    action_map = {"pause": "Pause", "play": "Play", "stop": "Stop"}
    if action not in action_map: return {"error": "Invalid action"}
    
    try:
        requests.post(f"{EMBY_URL}/emby/Sessions/{session_id}/{action_map[action]}", params={"api_key": API_KEY}, timeout=5)
        
        # 执行成功的闭环通知
        action_cn = {"pause": "暂停播放", "play": "恢复播放", "stop": "停止播放"}[action]
        details = get_item_details(item_id)
        item_name = details['name'] if details else "未知资源"
        
        feedback_msg = f"🛠️ <b>远程控制 | {action_cn}</b>\n\n👤 用户: <code>{target_user}</code>\n📺 资源: <code>{item_name}</code>\n\n⏰ 时间: <code>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</code>"
        for n in notifiers: background_tasks.add_task(n.send, feedback_msg)
        
        return {"status": "success", "user": target_user}
    except Exception as e:
        logger.error(f"Control error: {e}")
        return {"error": str(e)}

@app.post("/control/refresh")
async def refresh_library():
    requests.post(f"{EMBY_URL}/emby/Library/Refresh", params={"api_key": API_KEY}, timeout=5)
    return {"status": "refresh triggered"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
