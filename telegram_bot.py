import os
import requests
from flask import Flask, request

app = Flask(__name__)

# 🌟 从 Render 的环境变量保险箱里抽取核心能源砖
TG_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_API_URL = os.environ.get("LLM_API_URL")
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "gpt-3.5-turbo")
CUSTOM_SYSTEM_PROMPT = os.environ.get("CUSTOM_SYSTEM_PROMPT", "请简短、贴心，灵动地回复用户。")

def get_ai_reply(user_text):
    """瞬间唤醒赛博大脑，注入专属灵魂"""
    payload = {
        "model": LLM_MODEL_NAME,
        "messages": [
            {"role": "system", "content": CUSTOM_SYSTEM_PROMPT},
            {"role": "user", "content": user_text}
        ]
    }
    headers = {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"}
    try:
        resp = requests.post(LLM_API_URL, json=payload, headers=headers).json()
        return resp['choices'][0]['message']['content']
    except Exception as e:
        print(f"思考中断: {e}")
        return "云端神经元稍微卡了一下壳，等我重连一下~👀"

def send_message(chat_id, text):
    """顺着原路推回你的手机"""
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    requests.post(url, json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"})

@app.route('/webhook', methods=['POST'])
def webhook():
    """专门接待 Telegram 传令兵的客栈大门"""
    update = request.get_json()
    
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        user_text = update["message"]["text"]
        
        print(f"--> 收到实时传音: {user_text}")
        
        reply = get_ai_reply(user_text)
        send_message(chat_id, reply)
        
    return "OK", 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
