import os
import random
import json
import requests
from flask import Flask, request

app = Flask(__name__)

# 🌟 从 Render 的环境变量保险箱里抽取核心能源砖
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_API_URL = os.environ.get("LLM_API_URL")
TG_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
CUSTOM_SYSTEM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "请简短、贴心，灵动地回复用户。")
# 先把那串带逗号的长文本整个摸出来
raw_models = os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo")
# 让代码把它们切成独立的碎片，并在这堆大脑里随机抓阄抽选一个！
LLM_MODEL_NAME = random.choice([m.strip() for m in raw_models.split(",")])

# 🌟 记忆外挂的绝密钥匙
GIST_ID = os.environ.get("GIST_ID")
GIST_TOKEN = os.environ.get("GIST_TOKEN")
GIST_FILENAME = "chat_history.json"

def load_history():
    """跨越维度，去 GitHub 保险箱里翻看记忆"""
    if not GIST_ID or not GIST_TOKEN:
        print("未配置 Gist 钥匙，暂时失忆。")
        return []
        
    headers = {"Authorization": f"token {GIST_TOKEN}"}
    try:
        resp = requests.get(f"https://api.github.com/gists/{GIST_ID}", headers=headers)
        resp.raise_for_status()
        content = resp.json()['files'][GIST_FILENAME]['content']
        return json.loads(content)
    except Exception as e:
        print(f"--> 读取 Gist 记忆失败: {e}")
        return []

def save_history(history):
    """把刚刚的话，极其强硬地刻进微软的硬盘里"""
    if not GIST_ID or not GIST_TOKEN:
        return
        
    headers = {
        "Authorization": f"token {GIST_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    # 挥动赛博画笔，把账本打包成 JSON 字符串
    payload = {
        "files": {
            GIST_FILENAME: {
                "content": json.dumps(history, ensure_ascii=False)
            }
        }
    }
    try:
        requests.patch(f"https://api.github.com/gists/{GIST_ID}", json=payload, headers=headers)
        print("--> 记忆已成功锚定在云端 Gist！")
    except Exception as e:
        print(f"--> 写入 Gist 记忆失败: {e}")

def get_ai_reply(user_text):
    """带着完整的记忆聊天"""
    history = load_history()
    history.append({"role": "user", "content": user_text})
    
    # 极其冷酷的滑动窗口：永远只保留最热乎的 20 句话
    if len(history) > 20:
        history = history[-20:]
        
    messages = [{"role": "system", "content": CUSTOM_SYSTEM_PROMPT}] + history

    payload = {"model": LLM_MODEL_NAME, "messages": messages}
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    
    try:
        resp = requests.post(LLM_API_URL, json=payload, headers=headers).json()
        reply_text = resp['choices'][0]['message']['content']
        
        history.append({"role": "assistant", "content": reply_text})
        save_history(history)
        
        return reply_text
    except Exception as e:
        print(f"思考中断: {e}")
        return "云端神经元稍微卡了一下壳，等我重连一下~👀"

def send_message(chat_id, text):
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

@app.route('/webhook', methods=['POST'])
def webhook():
    update = request.get_json()
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        
        # 霸道的护短锁
        if str(chat_id) != str(TG_CHAT_ID):
            return "OK", 200 
            
        user_text = update["message"]["text"]
        print(f"--> 收到传音: {user_text}")
        reply = get_ai_reply(user_text)
        send_message(chat_id, reply)
        
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
