import os
import random
import json
import requests
import asyncio
import tempfile
import re
from datetime import datetime
from flask import Flask, request
from threading import Thread
from zoneinfo import ZoneInfo
from collections import deque

app = Flask(__name__)

# ============ 🌟 环境变量保险箱 ============
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_API_URL = os.environ.get("LLM_API_URL")

# 👇 师兄加料：白名单群组与私聊支持
TG_CHAT_ID_RAW = os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED_IDS = [i.strip() for i in TG_CHAT_ID_RAW.split(",") if i.strip()]

CUSTOM_SYSTEM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "请简短、贴心，灵动地回复用户。如果适合发语音请在最开头加上[语音]。")

# 多模型抓阄逻辑
raw_models = os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo")
MODEL_LIST = [m.strip() for m in raw_models.split(",")]

# 👇 师兄加料：双轨记忆（私聊一个本，群聊一个本）
GIST_ID = os.environ.get("GIST_ID") # 私聊的
GROUP_GIST_ID = os.environ.get("GROUP_GIST_ID", "") # 群聊的
GIST_TOKEN = os.environ.get("GIST_TOKEN")

BOT_USERNAME = os.environ.get("BOT_USERNAME", "") # 二号机的名字，防群里乱接茬

# 防复读机神器
PROCESSED_UPDATES = deque(maxlen=100)

# 🗣️ 发声组件：MiniMax (中) + 专属 Edge API (英)
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
MINIMAX_VOICE_ZH = os.environ.get("MINIMAX_VOICE_ZH", "")
EDGE_TTS_URL = os.environ.get("EDGE_TTS_URL", "") 
EDGE_TTS_API_KEY = os.environ.get("EDGE_TTS_API_KEY", "") # 门卫钥匙
VOICE_NAME_EN = "en-US-AndrewMultilingualNeural" 

# ============ 核心逻辑函数 ============
def get_target_gist_id(chat_id):
    """极其强壮的 ID 提取器，管你填的是长 URL 还是纯 ID，统统揪出来"""
    raw_id = GROUP_GIST_ID if str(chat_id).startswith("-") and GROUP_GIST_ID else GIST_ID
    if not raw_id: return None
    # 自动斩断前面的网址，只留最后的 ID
    return raw_id.rstrip("/").split("/")[-1]

def load_history(chat_id):
    """读取云端记忆（带报错排雷）"""
    target_id = get_target_gist_id(chat_id)
    if not target_id or not GIST_TOKEN:
        return []
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    try:
        resp = requests.get(f"https://api.github.com/gists/{target_id}", headers=headers, timeout=10)
        if resp.status_code != 200:
            print(f"🚨 [读取警告] Gist拒绝访问 ({resp.status_code}): {resp.text[:150]}")
            return []
        
        # 安全提取，就算新建的 Gist 是个空壳或者名字不对，也不会崩溃
        files = resp.json().get('files', {})
        content = files.get(GIST_FILENAME, {}).get('content', '[]')
        
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"🚨 [读取崩溃]: {e}")
        return []

def save_history(history, chat_id):
    """刻录云端记忆（失败必报警）"""
    target_id = get_target_gist_id(chat_id)
    if not target_id or not GIST_TOKEN: return
    headers = {"Authorization": f"token {GIST_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {"files": {GIST_FILENAME: {"content": json.dumps(history[-30:], ensure_ascii=False)}}}
    try:
        resp = requests.patch(f"https://api.github.com/gists/{target_id}", json=payload, headers=headers, timeout=10)
        if resp.status_code != 200:
            # 👇 核心报警器：如果 GitHub 敢拒绝，立刻在日志里大声嚷嚷！
            print(f"🚨 [保存惨死] GitHub 报错 ({resp.status_code}): {resp.text[:200]}")
        else:
            print(f"[DEBUG] 💾 记忆完美写入 Gist (ID: {target_id[:6]}...)")
    except Exception as e:
        print(f"🚨 [写入崩溃]: {e}")

def get_ai_reply(history):
    """调用 API 思考"""
    # 这里不需要再手动把 user_text 塞进去了，因为 process_bg 已经提前塞好了
    messages = [{"role": "system", "content": CUSTOM_SYSTEM_PROMPT}]
    
    for h in history[-20:]:
        prefix = f"[{h['timestamp']}] " if h.get("timestamp") else ""
        messages.append({"role": h["role"], "content": f"{prefix}{h['content']}"})
    
    current_model = random.choice(MODEL_LIST)
    payload = {"model": current_model, "messages": messages}
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    
    try:
        resp = requests.post(LLM_API_URL, json=payload, headers=headers, timeout=40)
        result = resp.json()
        
        if 'choices' not in result:
            print(f"🚨 API 抽风警告！返回内容: {json.dumps(result, ensure_ascii=False)}")
            return f"思考中断，API 好像有意见：{str(result)[:100]}"
            
        return result['choices'][0]['message']['content']
    except Exception as e:
        print(f"思考过程崩了: {e}")
        return "神经元刚才短路了一下下... 👀"

# ============ 语音生成与分流 ============

def detect_language(text):
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    total_letters = sum(1 for c in text if c.isalpha())
    if total_letters > 0 and ascii_letters / total_letters > 0.6:
        return "EN"
    return "ZH"

def _gen_minimax(text, path):
    url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={MINIMAX_GROUP_ID}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"}
    body = {
        "model": "speech-01-hd", "text": text, "stream": False,
        "voice_setting": {"voice_id": MINIMAX_VOICE_ZH},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"}
    }
    r = requests.post(url, headers=headers, json=body, timeout=30).json()
    if r.get("base_resp", {}).get("status_code") != 0:
        raise Exception(f"MiniMax 报错: {r.get('base_resp', {}).get('status_msg')}")
    with open(path, "wb") as f: f.write(bytes.fromhex(r["data"]["audio"]))

def _gen_edge(text, path):
    if not EDGE_TTS_URL: raise ValueError("EDGE_TTS_URL未配置")
    url = f"{EDGE_TTS_URL.rstrip('/')}/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    if EDGE_TTS_API_KEY: headers["Authorization"] = f"Bearer {EDGE_TTS_API_KEY}"
    body = {"model": "tts-1", "input": text, "voice": VOICE_NAME_EN}
    r = requests.post(url, headers=headers, json=body, timeout=60)
    r.raise_for_status()
    with open(path, "wb") as f: f.write(r.content)

def send_voice(chat_id, text):
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
            
        lang = detect_language(text)
        if lang == "ZH" and MINIMAX_API_KEY:
            _gen_minimax(text, path)
        else:
            _gen_edge(text, path)

        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendVoice"
        with open(path, "rb") as vf:
            requests.post(url, data={"chat_id": chat_id, "caption": text}, 
                          files={"voice": ("v.ogg", vf, "audio/ogg")}, timeout=30)
    except Exception as e:
        print(f"语音生成失败: {e}")
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": chat_id, "text": text})
    finally:
        if path and os.path.exists(path): os.unlink(path)

# ============ 后台处理与 Webhook ============

def process_bg(chat_id, user_text, sender_name, msg_date, should_reply=True):
    try:
        tz = ZoneInfo("Australia/Melbourne")
        u_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S") if msg_date else datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        # 👇 群聊带名字，私聊纯文本
        formatted_input = f"{sender_name}: {user_text}" if str(chat_id).startswith("-") else user_text
        
        history = load_history(chat_id)
        
        # 第一步：不管说不说，先记在小本本上
        history.append({"role": "user", "content": formatted_input, "timestamp": u_time})
        
        # 第二步：如果只是“旁听”，存档后闪人
        if not should_reply:
            save_history(history, chat_id)
            print(f"[DEBUG] 🤫 二号机悄悄记下 {sender_name} 的发言。")
            return

        # 第三步：被叫到了，砸钱问 AI
        print(f"[DEBUG] 🗣️ 二号机被点名！思考中...")
        reply = get_ai_reply(history)
        
        if not reply: return
        
        # 暴力清洗大模型自己瞎加的时间戳
        reply = re.sub(r'^\[202\d-[^\]]+\]\s*', '', reply.strip())
        
        clean_reply = reply
        if reply.startswith("[语音]"):
            clean_reply = reply[4:].strip()
            send_voice(chat_id, clean_reply)
        else:
            requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", 
                          json={"chat_id": chat_id, "text": clean_reply, "parse_mode": "Markdown"})

        # 存入 Bot 自己的回复
        b_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        history.append({"role": "assistant", "content": reply, "timestamp": b_time})
        save_history(history, chat_id)
        
    except Exception as e:
        print(f"🚨 后台任务崩了: {e}")

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    if not data: return "OK", 200
    
    # 👇 师兄加料：连环催命符拦截器！
    update_id = data.get("update_id")
    if update_id:
        if update_id in PROCESSED_UPDATES:
            return "OK", 200
        PROCESSED_UPDATES.append(update_id)
        
    if "message" not in data or "text" not in data["message"]:
        return "OK", 200
        
    msg = data["message"]
    chat_id = str(msg.get("chat", {}).get("id", ""))
    
    if chat_id not in ALLOWED_IDS: 
        return "OK", 200 
        
    user_text = msg["text"]
    
    # 👇 师兄加料：群聊静音偷听逻辑
    should_reply = True
    if chat_id.startswith("-"):
        if BOT_USERNAME and f"@{BOT_USERNAME}" not in user_text:
            should_reply = False
        elif BOT_USERNAME:
            user_text = user_text.replace(f"@{BOT_USERNAME}", "").strip()
            
    if not user_text and not should_reply: return "OK", 200
            
    msg_date = msg.get("date")
    sender_name = msg.get("from", {}).get("first_name", "神秘人")
    
    Thread(target=process_bg, args=(chat_id, user_text, sender_name, msg_date, should_reply)).start()
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
