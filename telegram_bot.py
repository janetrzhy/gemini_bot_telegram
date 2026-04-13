import os
import random
import json
import requests
import tempfile
import re
from datetime import datetime
from flask import Flask, request
from threading import Thread
from zoneinfo import ZoneInfo

app = Flask(__name__)

# 🌟 从 Render 的环境变量保险箱里抽取核心能源砖
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_API_URL = os.environ.get("LLM_API_URL")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CUSTOM_SYSTEM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "请简短、贴心，灵动地回复用户。如果适合发语音请在最开头加上[语音]。")

raw_models = os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo")
LLM_MODEL_NAME = random.choice([m.strip() for m in raw_models.split(",")])

# 🌟 记忆外挂的绝密钥匙
GIST_ID = os.environ.get("GIST_ID")
GIST_TOKEN = os.environ.get("GIST_TOKEN")
GIST_FILENAME = "chat_history.json"

# 🌟 发声器官：MiniMax + 你的专属 Edge API
MINIMAX_API_KEY = os.environ.get("MINIMAX_API_KEY", "")
MINIMAX_GROUP_ID = os.environ.get("MINIMAX_GROUP_ID", "")
MINIMAX_VOICE_ZH = os.environ.get("MINIMAX_VOICE_ZH", "")

VOICE_NAME_EN = os.environ.get("VOICE_NAME_EN", "en-US-AndrewMultilingualNeural")
EDGE_TTS_URL = os.environ.get("EDGE_TTS_URL", "") # 你的 https://github.com/janetrzhy/openai-edge-tts 地址

def load_history():
    if not GIST_ID or not GIST_TOKEN:
        print("未配置 Gist 钥匙，师兄暂时失忆。")
        return []
        
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    try:
        resp = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers, timeout=10)
        resp.raise_for_status()
        content = resp.json()['files'][GIST_FILENAME]['content']
        
        data = json.loads(content)
        if isinstance(data, dict):
            print("--> 警报：检测到记忆账本基因突变！已强行抹除错乱结构。")
            return [] 
        return data
    except Exception as e:
        print(f"--> 读取 Gist 记忆失败: {e}")
        return []

def save_history(history):
    if not GIST_ID or not GIST_TOKEN:
        return
        
    headers = {
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    payload = {
        "files": {
            GIST_FILENAME: {
                "content": json.dumps(history, ensure_ascii=False)
            }
        }
    }
    try:
        requests.patch(f"https://api.github.com/gists/{GIST_ID}", json=payload, headers=headers, timeout=10)
    except Exception as e:
        print(f"--> 写入 Gist 记忆失败: {e}")

def get_ai_reply(user_text, user_time):
    history = load_history()
    messages = [{"role": "system", "content": CUSTOM_SYSTEM_PROMPT}]
    
    # 巧妙伪装，把时间戳合并进文本里
    for h in history[-20:]:
        time_prefix = f"[{h['timestamp']}] " if h.get("timestamp") else ""
        messages.append({"role": h["role"], "content": f"{time_prefix}{h['content']}"})
        
    messages.append({"role": "user", "content": f"[{user_time}] {user_text}"})

    payload = {"model": LLM_MODEL_NAME, "messages": messages}
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    
    try:
        resp = requests.post(LLM_API_URL, json=payload, headers=headers, timeout=30).json()
        return resp['choices'][0]['message']['content']
    except Exception as e:
        print(f"思考中断: {e}")
        return "云端神经元稍微卡了一下壳，等我重连一下~👀"

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)

def detect_voice(text):
    ascii_letters = sum(1 for c in text if c.isascii() and c.isalpha())
    total_letters = sum(1 for c in text if c.isalpha())
    if total_letters > 0 and ascii_letters / total_letters > 0.6:
        return "edge_en"
    return "minimax_zh"

def _generate_minimax_audio(text, mp3_path):
    url = f"https://api.minimax.chat/v1/t2a_v2?GroupId={MINIMAX_GROUP_ID}"
    headers = {"Authorization": f"Bearer {MINIMAX_API_KEY}", "Content-Type": "application/json"}
    
    body = {
        "model": "speech-01-hd",  
        "text": text,
        "stream": False,
        "voice_setting": {"voice_id": MINIMAX_VOICE_ZH},
        "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3"}
    }
    resp = requests.post(url, headers=headers, json=body, timeout=30).json()
    if resp.get("base_resp", {}).get("status_code") != 0:
        raise Exception(f"MiniMax TTS 失败: {resp.get('base_resp', {}).get('status_msg')}")
    with open(mp3_path, "wb") as f:
        f.write(bytes.fromhex(resp["data"]["audio"]))

# 👇 师兄加料：纯 API 调用的 Edge TTS！
def _generate_edge_audio(text, mp3_path):
    if not EDGE_TTS_URL:
        raise ValueError("EDGE_TTS_URL 没配置，没法调你的专属语音服务！")
        
    url = f"{EDGE_TTS_URL.rstrip('/')}/v1/audio/speech"
    headers = {"Content-Type": "application/json"}
    body = {
        "model": "tts-1",
        "input": text,
        "voice": VOICE_NAME_EN
    }
    resp = requests.post(url, headers=headers, json=body, timeout=30)
    resp.raise_for_status()
    with open(mp3_path, "wb") as f:
        f.write(resp.content)

def send_telegram_voice(chat_id, text):
    voice_path = None
    try:
        # 我们只建一个临时文件，不再需要转码！
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            voice_path = f.name

        voice_type = detect_voice(text)
        
        # 中英文分流，全是高贵的 API 调用
        if voice_type == "minimax_zh" and MINIMAX_API_KEY and MINIMAX_GROUP_ID and MINIMAX_VOICE_ZH:
            _generate_minimax_audio(text, voice_path)
        else:
            _generate_edge_audio(text, voice_path)

        # 狸猫换太子：把拿到的 MP3 披上 ogg 的马甲直接发给 Telegram
        url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendVoice"
        with open(voice_path, "rb") as voice_file:
            requests.post(
                url,
                data={"chat_id": chat_id, "caption": text}, # 完美字幕
                files={"voice": ("voice.ogg", voice_file, "audio/ogg")},
                timeout=30
            )
    except Exception as e:
        print(f"[ERROR] 语音发送失败: {e}")
        send_message(chat_id, text)
    finally:
        if voice_path and os.path.exists(voice_path):
            try: os.unlink(voice_path)
            except Exception: pass

def process_message_background(chat_id, user_text, msg_date):
    tz = ZoneInfo("Australia/Melbourne")
    
    if msg_date:
        user_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S")
    else:
        user_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

    reply_text = get_ai_reply(user_text, user_time)
    
    clean_reply = reply_text
    if reply_text.startswith("[语音]"):
        clean_reply = reply_text[4:].strip()
        send_telegram_voice(chat_id, clean_reply)
    else:
        send_message(chat_id, clean_reply)

    bot_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
    
    latest_history = load_history()
    latest_history.append({"role": "user", "content": user_text, "timestamp": user_time})
    latest_history.append({"role": "assistant", "content": clean_reply, "timestamp": bot_time})
    
    if len(latest_history) > 20:
        latest_history = latest_history[-20:]
        
    save_history(latest_history)

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        
        if str(chat_id) != str(TG_CHAT_ID):
            return "OK", 200 
            
        user_text = update["message"]["text"]
        msg_date = update["message"].get("date") # 截获精准时间戳
        print(f"--> 收到传音: {user_text}")
        
        Thread(target=process_message_background, args=(chat_id, user_text, msg_date)).start()
        
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
