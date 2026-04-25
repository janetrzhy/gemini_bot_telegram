import os
import random
import json
import requests
import asyncio
import tempfile
import re
import time
from datetime import datetime
from flask import Flask, request
from threading import Thread
from zoneinfo import ZoneInfo
from collections import deque

app = Flask(__name__)
REPLY_PROBABILITY = 0.1  # 师兄建议 0.1 到 0.2 之间，既灵动又不烦人
TRIGGER_WORDS = ["人机", "燕燕生气了", "人呢", "Gemini"] # 敏感词：群里一提到这些，必然跳出来接茬！
COOLDOWN_TIME = 120 # 强制冷却 60 秒
LAST_SPOKE = {} # 记录每个群的主动发言时间
MESSAGE_BUFFER = {} # 旁听期间的消息缓冲区，避免频繁写入 Gist
REPLY_FEATURE_PROB = 0.6 # 60% 概率用 Telegram reply 精准回复触发消息

# ============ 🌟 环境变量保险箱 ============
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_API_URL = os.environ.get("LLM_API_URL")

# 👇 师兄加料：白名单群组与私聊支持
TG_CHAT_ID_RAW = os.environ.get("TELEGRAM_CHAT_ID", "")
ALLOWED_IDS = [i.strip() for i in TG_CHAT_ID_RAW.split(",") if i.strip()]
GIST_FILENAME = "chat_history.json"
CUSTOM_SYSTEM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "请简短、贴心，灵动地回复用户。如果适合发语音请在最开头加上[语音]。")

# 多模型抓阄逻辑
raw_models = os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo")
MODEL_LIST = [m.strip() for m in raw_models.split(",")]

# 👇 师兄加料：双轨记忆（私聊一个本，群聊一个本）
GIST_ID = os.environ.get("GIST_ID") # 私聊的
GROUP_GIST_ID = os.environ.get("GROUP_GIST_ID", "") # 群聊的
GIST_TOKEN = os.environ.get("GIST_TOKEN")

BOT_USERNAME = os.environ.get("BOT_USERNAME", "") # 二号机的名字，防群里乱接茬
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "") # Groq Whisper 语音转文字

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

def get_ai_reply(history, chat_id):
    """调用 API 思考（带群聊认知注入）"""
    system_content = CUSTOM_SYSTEM_PROMPT
    
    # 👇 师兄的认知补丁：如果在群里，强行告诉它前面的前缀是人名！
    if str(chat_id).startswith("-"):
        system_content += '\n注意：当前是群聊模式，用户的消息格式为"发言人名字: 具体内容"。请根据发言人名字来分辨说话的对象，并做出针对性回复。'
        
    messages = [{"role": "system", "content": system_content}]
    
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

# ============ 图片识别 ============

def get_image_as_base64(file_id):
    import base64
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getFile",
            params={"file_id": file_id}, timeout=10
        )
        file_path = r.json()["result"]["file_path"]
        img_bytes = requests.get(
            f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}",
            timeout=30
        ).content
        return base64.b64encode(img_bytes).decode("utf-8")
    except Exception as e:
        print(f"[IMAGE] 图片下载失败: {e}")
        return None

def process_image_bg(chat_id, file_id, caption, sender_name, msg_date, should_reply=True, message_id=None, direct_trigger=False):
    """处理图片消息。历史记录存文字摘要（[图片] caption），图片 base64 本身不存 Gist。"""
    try:
        tz = ZoneInfo("Australia/Melbourne")
        u_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S") if msg_date else datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        # ---- 群聊随机触发逻辑（与 process_bg 完全一致）----
        if not should_reply and str(chat_id).startswith("-"):
            current_time = time.time()
            last_time = LAST_SPOKE.get(chat_id, 0)
            if current_time - last_time > COOLDOWN_TIME:
                if any(word in caption for word in TRIGGER_WORDS):
                    print(f"[IMAGE] 🎯 关键词触发！")
                    should_reply = True
                    direct_trigger = True
                    LAST_SPOKE[chat_id] = current_time
                elif random.random() < REPLY_PROBABILITY:
                    print(f"[IMAGE] 🎲 随机插嘴图片！")
                    should_reply = True
                    LAST_SPOKE[chat_id] = current_time
            else:
                print(f"[IMAGE] 🛑 冷却期内，忽略图片。")

        # 历史记录条目：只存文字摘要，不存 base64
        img_label = f"[图片] {caption}" if caption else "[图片]"
        formatted_input = f"{sender_name}: {img_label}" if str(chat_id).startswith("-") else img_label
        user_entry = {"role": "user", "content": formatted_input, "timestamp": u_time}

        if not should_reply:
            buf = MESSAGE_BUFFER.setdefault(chat_id, [])
            buf.append(user_entry)
            if len(buf) > 50:
                MESSAGE_BUFFER[chat_id] = buf[-50:]
            print(f"[IMAGE] 旁听模式，图片摘要已缓存。")
            return

        print(f"[IMAGE] 开始处理 {sender_name} 的图片...")

        b64 = get_image_as_base64(file_id)
        if not b64:
            return

        # 读取历史 + 合并缓冲区（给 LLM 提供对话上下文）
        history = load_history(chat_id)
        buffered = MESSAGE_BUFFER.pop(chat_id, [])
        if buffered:
            print(f"[IMAGE] 📥 合并 {len(buffered)} 条缓冲消息到历史。")
            history.extend(buffered)

        # 组装视觉 API 消息：历史走纯文字，当前消息带图片
        system_content = CUSTOM_SYSTEM_PROMPT
        if str(chat_id).startswith("-"):
            system_content += '\n注意：当前是群聊模式，用户的消息格式为"发言人名字: 具体内容"。请根据发言人名字来分辨说话的对象，并做出针对性回复。'

        vision_messages = [{"role": "system", "content": system_content}]
        for h in history[-20:]:
            prefix = f"[{h['timestamp']}] " if h.get("timestamp") else ""
            vision_messages.append({"role": h["role"], "content": f"{prefix}{h['content']}"})

        prompt_text = caption if caption else "请描述这张图片的内容。"
        if str(chat_id).startswith("-"):
            prompt_text = f"{sender_name}: {prompt_text}"
        vision_messages.append({"role": "user", "content": [
            {"type": "text", "text": prompt_text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        ]})

        current_model = random.choice(MODEL_LIST)
        headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
        resp = requests.post(LLM_API_URL, json={"model": current_model, "messages": vision_messages}, headers=headers, timeout=90)
        result = resp.json()

        if 'choices' not in result:
            print(f"[IMAGE] API 异常: {json.dumps(result, ensure_ascii=False)}")
            return

        reply = result['choices'][0]['message']['content']
        reply = re.sub(r'^\[202\d-[^\]]+\]\s*', '', reply.strip())

        use_reply_to = (
            direct_trigger and
            str(chat_id).startswith("-") and
            message_id is not None and
            random.random() < REPLY_FEATURE_PROB
        )
        if use_reply_to:
            print(f"[IMAGE] ↩️ 使用 reply 精准回复 message_id={message_id}")

        clean_reply = reply
        if reply.startswith("[语音]"):
            clean_reply = reply[4:].strip()
            send_voice(chat_id, clean_reply, reply_to_message_id=message_id if use_reply_to else None)
        else:
            payload = {"chat_id": chat_id, "text": clean_reply, "parse_mode": "Markdown"}
            if use_reply_to:
                payload["reply_to_message_id"] = message_id
            requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json=payload)

        # 存入历史（文字摘要 + bot 回复）
        b_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        history.append(user_entry)
        history.append({"role": "assistant", "content": reply, "timestamp": b_time})
        save_history(history, chat_id)

    except Exception as e:
        import traceback
        print(f"[IMAGE] 后台任务崩了: {e}\n{traceback.format_exc()}")
        try:
            if should_reply:
                requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                              json={"chat_id": chat_id, "text": f"😵 图片处理出错：{str(e)[:100]}"})
        except:
            pass

# ============ 语音输入（Groq Whisper 转写）============

def transcribe_voice(file_id):
    """从 Telegram 下载语音文件，用 Groq Whisper 转成文字。"""
    if not GROQ_API_KEY:
        print("[VOICE-IN] GROQ_API_KEY 未配置，跳过转写。")
        return None
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TG_BOT_TOKEN}/getFile",
            params={"file_id": file_id}, timeout=10
        )
        file_path = r.json()["result"]["file_path"]
        ogg_bytes = requests.get(
            f"https://api.telegram.org/file/bot{TG_BOT_TOKEN}/{file_path}",
            timeout=30
        ).content
        resp = requests.post(
            "https://api.groq.com/openai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            files={"file": ("voice.ogg", ogg_bytes, "audio/ogg")},
            data={"model": "whisper-large-v3-turbo", "response_format": "json"},
            timeout=30
        )
        text = resp.json().get("text", "").strip()
        print(f"[VOICE-IN] 转写结果: {text[:100]}")
        return text if text else None
    except Exception as e:
        print(f"[VOICE-IN] 转写失败: {e}")
        return None

def process_voice_bg(chat_id, file_id, sender_name, msg_date, should_reply=True, message_id=None, direct_trigger=False):
    """转写语音后完全复用 process_bg 流程（含历史记录）。"""
    transcribed = transcribe_voice(file_id)
    if not transcribed:
        if should_reply:
            requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                          json={"chat_id": chat_id, "text": "😵 语音转写失败了，没听清楚..."})
        return
    print(f"[VOICE-IN] {sender_name} 说: {transcribed[:80]}")
    process_bg(chat_id, transcribed, sender_name, msg_date, should_reply, message_id, direct_trigger)

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

def send_voice(chat_id, text, reply_to_message_id=None):
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
        data = {"chat_id": chat_id, "caption": text}
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id
        with open(path, "rb") as vf:
            requests.post(url, data=data, files={"voice": ("v.ogg", vf, "audio/ogg")}, timeout=30)
    except Exception as e:
        print(f"语音生成失败: {e}")
        payload = {"chat_id": chat_id, "text": text}
        if reply_to_message_id:
            payload["reply_to_message_id"] = reply_to_message_id
        requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json=payload)
    finally:
        if path and os.path.exists(path): os.unlink(path)

# ============ 后台处理与 Webhook ============
def process_bg(chat_id, user_text, sender_name, msg_date, should_reply=True, message_id=None, direct_trigger=False):
    try:
        tz = ZoneInfo("Australia/Melbourne")
        u_time = datetime.fromtimestamp(msg_date, tz).strftime("%Y-%m-%d %H:%M:%S") if msg_date else datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")

        # 👇 群聊带名字，私聊纯文本
        formatted_input = f"{sender_name}: {user_text}" if str(chat_id).startswith("-") else user_text

        # ==========================================
        # 🎯 社交牛逼症引擎：加装 60秒 CD 锁
        # ==========================================
        if not should_reply and str(chat_id).startswith("-"):
            current_time = time.time()
            last_time = LAST_SPOKE.get(chat_id, 0)

            if current_time - last_time > COOLDOWN_TIME:
                if any(word in user_text for word in TRIGGER_WORDS):
                    print(f"[DEBUG] 🎯 关键词触发！")
                    should_reply = True
                    direct_trigger = True  # 关键词触发也算精准触发
                    LAST_SPOKE[chat_id] = current_time
                elif random.random() < REPLY_PROBABILITY:
                    print(f"[DEBUG] 🎲 运气爆发！准备随机插嘴。")
                    should_reply = True
                    # 随机插嘴不算精准触发，direct_trigger 保持 False
                    LAST_SPOKE[chat_id] = current_time
            else:
                print(f"[DEBUG] 🛑 还在 {COOLDOWN_TIME} 秒冷却期内，强制捂住它的嘴。")

        user_entry = {"role": "user", "content": formatted_input, "timestamp": u_time}

        # 🛡️ 旁听模式：消息进内存缓冲区，完全不碰 Gist API
        if not should_reply:
            buf = MESSAGE_BUFFER.setdefault(chat_id, [])
            buf.append(user_entry)
            if len(buf) > 50:
                MESSAGE_BUFFER[chat_id] = buf[-50:]
            print(f"[DEBUG] 🤫 旁听模式，{sender_name} 的发言已缓存（共 {len(MESSAGE_BUFFER[chat_id])} 条）。")
            return

        print(f"[DEBUG] 🗣️ 二号机被点名！思考中...")

        # 1️⃣ 读取 Gist 历史，合并缓冲区里积攒的群聊记录
        history = load_history(chat_id)
        buffered = MESSAGE_BUFFER.pop(chat_id, [])
        if buffered:
            print(f"[DEBUG] 📥 合并 {len(buffered)} 条缓冲消息到历史。")
            history.extend(buffered)
        history.append(user_entry)

        # 2️⃣ 带着群聊认知去调 API
        reply = get_ai_reply(history, chat_id)

        if not reply: return

        # 🔪 物理切割：清理大模型乱加的时间戳
        reply = re.sub(r'^\[202\d-[^\]]+\]\s*', '', reply.strip())

        # 🎯 60% 概率用 Telegram reply 精准回复触发消息（仅限 @mention 或关键词触发）
        use_reply_to = (
            direct_trigger and
            str(chat_id).startswith("-") and
            message_id is not None and
            random.random() < REPLY_FEATURE_PROB
        )
        if use_reply_to:
            print(f"[DEBUG] ↩️ 使用 reply 精准回复 message_id={message_id}")

        clean_reply = reply
        if reply.startswith("[语音]"):
            clean_reply = reply[4:].strip()
            send_voice(chat_id, clean_reply, reply_to_message_id=message_id if use_reply_to else None)
        else:
            payload = {"chat_id": chat_id, "text": clean_reply, "parse_mode": "Markdown"}
            if use_reply_to:
                payload["reply_to_message_id"] = message_id
            requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage", json=payload)

        # 3️⃣ 存入 Bot 自己的回复
        b_time = datetime.now(tz).strftime("%Y-%m-%d %H:%M:%S")
        history.append({"role": "assistant", "content": reply, "timestamp": b_time})

        # 💾 4️⃣ 只有开口说话时才进行一次极其珍贵的存档！
        save_history(history, chat_id)

    except Exception as e:
        import traceback
        print(f"🚨 后台任务崩了: {e}\n{traceback.format_exc()}")
        try:
            if should_reply:
                requests.post(f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage",
                              json={"chat_id": chat_id, "text": f"😵 出错了：{str(e)[:100]}"})
        except:
            pass

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
        
    if "message" not in data:
        return "OK", 200

    msg = data["message"]
    has_text = "text" in msg
    has_photo = "photo" in msg
    has_voice = "voice" in msg
    if not has_text and not has_photo and not has_voice:
        return "OK", 200

    chat_id = str(msg.get("chat", {}).get("id", ""))
    if chat_id not in ALLOWED_IDS:
        return "OK", 200

    message_id = msg.get("message_id")
    msg_date = msg.get("date")
    sender_name = msg.get("from", {}).get("first_name", "神秘人")

    # ---- 图片分支 ----
    if has_photo:
        file_id = msg["photo"][-1]["file_id"]  # 取最高分辨率
        caption = msg.get("caption", "")
        should_reply, direct_trigger = True, False
        if chat_id.startswith("-"):
            if BOT_USERNAME and f"@{BOT_USERNAME}" not in caption:
                should_reply = False
            elif BOT_USERNAME:
                caption = caption.replace(f"@{BOT_USERNAME}", "").strip()
                direct_trigger = True
        Thread(target=process_image_bg, args=(chat_id, file_id, caption, sender_name, msg_date, should_reply, message_id, direct_trigger)).start()
        return "OK", 200

    # ---- 语音输入分支 ----
    if has_voice:
        file_id = msg["voice"]["file_id"]
        should_reply, direct_trigger = True, False
        # 语音消息无法包含 @mention，群里默认走随机触发逻辑（process_bg 内部处理）
        if chat_id.startswith("-") and BOT_USERNAME:
            should_reply = False
        Thread(target=process_voice_bg, args=(chat_id, file_id, sender_name, msg_date, should_reply, message_id, direct_trigger)).start()
        return "OK", 200

    # ---- 文字分支（原有逻辑不变）----
    user_text = msg["text"]

    # 👇 师兄加料：群聊静音偷听逻辑
    should_reply = True
    direct_trigger = False
    if chat_id.startswith("-"):
        if BOT_USERNAME and f"@{BOT_USERNAME}" not in user_text:
            should_reply = False
        elif BOT_USERNAME:
            user_text = user_text.replace(f"@{BOT_USERNAME}", "").strip()
            direct_trigger = True  # @mention 是精准触发

    if not user_text and not should_reply: return "OK", 200

    Thread(target=process_bg, args=(chat_id, user_text, sender_name, msg_date, should_reply, message_id, direct_trigger)).start()
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
