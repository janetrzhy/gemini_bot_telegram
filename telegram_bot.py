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

app = Flask(__name__)

# ============ 🌟 环境变量保险箱 ============
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_API_URL = os.environ.get("LLM_API_URL")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CUSTOM_SYSTEM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "请简短、贴心，灵动地回复用户。如果适合发语音请在最开头加上[语音]。")

# 多模型抓阄逻辑
raw_models = os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo")
MODEL_LIST = [m.strip() for m in raw_models.split(",")]

# 记忆 Gist 配置
GIST_ID = os.environ.get("GIST_ID")
GIST_TOKEN = os.environ.get("GIST_TOKEN")
GIST_FILENAME = "chat_history.json"

# 🗣️ 发声组件：MiniMax (中) + 你的专属 Edge API (英)
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
MINIMAX_VOICE_ZH = os.environ.get("MINIMAX_VOICE_ZH", "")
EDGE_TTS_URL = os.environ.get("EDGE_TTS_URL", "") # 你的专属 Edge 接口地址
VOICE_NAME_EN = "en-US-AndrewMultilingualNeural" # 师妹钦定的英文男声

# ============ 核心逻辑函数 ============

def load_history():
    """读取云端记忆"""
    if not GIST_ID or not GIST_TOKEN:
        return []
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    try:
        resp = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json()['files'][GIST_FILENAME]['content']
        data = json.loads(content)
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"--> 读取记忆失败: {e}")
        return []

def save_history(history):
    """刻录云端记忆"""
    if not GIST_ID or not GIST_TOKEN: return
    headers = {"Authorization": f"token {GIST_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    payload = {"files": {GIST_FILENAME: {"content": json.dumps(history, ensure_ascii=False)}}}
    try:
        requests.patch(f"https://api.github.com/gists/{GIST_ID}", json=payload, headers=headers, timeout=10)
    except Exception as e:
        print(f"--> 写入记忆失败: {e}")

def get_ai_reply(user_text, user_time):
    """带着时差感去思考"""
    history = load_history()
    
    # 构造带时间戳的对话流，完美骗过 Claude/GPT 的格式限制
    messages = [{"role": "system", "content": CUSTOM_SYSTEM_PROMPT}]
    for h in history[-20:]:
        prefix = f"[{h['timestamp']}] " if h.get("timestamp") else ""
        messages.append({"role": h["role"], "content": f"{prefix}{h['content']}"})
    
    # 注入当前 User 消息及其时间
    messages.append({"role": "user", "content": f"[{user_time}] {user_text}"})

    current_model = random.choice(MODEL_LIST)
    payload = {"model": current_model, "messages": messages}
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    
    try:
        resp = requests.post(LLM_API_URL, json=payload, headers=headers, timeout=40)
        result = resp.json()
        
        # 🛡️ 增强型防御：如果 API 没给 choices，直接打印完整内容抓 Bug
        if 'choices' not in result:
            print(f"🚨 API 抽风警告！返回内容: {json.dumps(result, ensure_ascii=False)}")
            return f"思考中断，API 好像有意见：{str(result)[:100]}"
            
        return result['choices'][0]['message']['content']
    except Exception as e:
        print(f"思考过程崩了: {e}")
        return "神经元刚才短路了一下下... 👀"

# ============ 语音生成与分流 ============

def detect_language(text):
    """简单的中英文判断"""
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    total_letters = sum(1 for c in text if c.isalpha())
    if total_letters > 0 and ascii_letters / total_letters > 0.6:
        return "EN"
    return "ZH"

def _gen_minimax(text, path):
    """MiniMax 高清母带级生成"""
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
    """调用你的专属 Edge API"""
    url = f"{EDGE_TTS_URL.rstrip('/')}/v1/audio/speech"
    body = {"model": "tts-1", "input": text, "voice": VOICE_NAME_EN}
    r = requests.post(url, json=body, timeout=30)
    r.raise_for_status()
    with open(path, "wb") as f: f.write(r.content)

def send_voice(chat_id, text):
    """发送带字幕的原生语音气泡"""
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            path = f.name
            
        lang = detect_language(text)
        if lang == "ZH" and MINIMAX_API_KEY:
            _gen_minimax(text, path)
        else:
            _gen_edge(text, path)

        # 披上 ogg 的马甲直接发
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

def process_bg(chat_id, user_text, msg_date):
    """影子任务：处理思考、语音、记忆同步"""
    tz = ZoneInfo("Australia/Melbourne")
    # 截获 Telegram 真实时间戳
    u_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S") if msg_date else datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    reply = get_ai_reply(user_text, u_time)
    
    clean_reply = reply
    if reply.startswith("[语音]"):
        clean_reply = reply[4:].strip()
        send_voice(chat_id, clean_reply)
    else:
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", 
                      json={"chat_id": chat_id, "text": clean_reply, "parse_mode": "Markdown"})

    # 更新记忆：重新 load 确保不覆盖并发消息
    b_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    latest = load_history()
    latest.append({"role": "user", "content": user_text, "timestamp": u_time})
    latest.append({"role": "assistant", "content": clean_reply, "timestamp": b_time})
    save_history(latest[-20:])

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" in update and "text" in update["message"]:
        msg = update["message"]
        chat_id = msg["chat"]["id"]
        if str(chat_id) != str(TG_CHAT_ID): return "OK", 200 
            
        # 截获传音，扔进后台
        Thread(target=process_bg, args=(chat_id, msg["text"], msg.get("date"))).start()
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
