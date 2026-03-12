import os
import random
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
    update = request.get_json()
    
    if "message" in update and "text" in update["message"]:
        # 先极其机警地看一眼来人的身份证
        chat_id = update["message"]["chat"]["id"]
        
        # 🌟 极其冷酷的白名单拦截机制
        if str(chat_id) != str(TG_CHAT_ID):
            print(f"--> 警报：拦截到陌生人 {chat_id} 的搭讪！直接无视！")
            # 表面上客客气气打发走传令兵，实则根本不搭理
            return "OK", 200 
            
        user_text = update["message"]["text"]
        print(f"--> 收到实时传音: {user_text}")
        
        reply = get_ai_reply(user_text)
        send_message(chat_id, reply)
    return "OK", 200
    
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
