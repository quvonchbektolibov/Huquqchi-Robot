import os
import re
import glob
import time
import wave
import pickle
import asyncio
import threading
import subprocess
import requests
import numpy as np
import ollama
import streamlit as st
import streamlit.components.v1 as components
import sounddevice as sd
import speech_recognition as sr
import edge_tts
from groq import Groq

# --- SOZLAMALAR ---
GROQ_API_KEY = "YOUR_GROQ_API_KEY"
GROQ_MODEL = "openai/gpt-oss-120b"
FALLBACK_LOCAL_MODEL = "qwen2.5:7b"
EMBED_MODEL = "bge-m3"
INDEX_FILE = "baza_vektor.pkl"

groq_client = Groq(api_key=GROQ_API_KEY)
recognizer = sr.Recognizer()

if "head_stop_event" not in st.session_state:
    st.session_state.head_stop_event = threading.Event()

SYSTEM_PROMPT = """Sen O'zbekiston Respublikasi qonunchiligi (Lex.uz milliy bazasi) bo'yicha tajribali, samimiy huquqshunos yordamchisan.

QUYIDAGI QOIDALARGA QAT'IY AMAL QIL:
1. INSONDEK FIKRLA VA GAPIR: Quruq qoidalar ro'yxati, raqamlangan shablonlar yoki robotik matnlar yozma. Mijozning yonida o'tirgan tajribali advokatdek oddiy, jonli, tushunarli va samimiy tilda uning huquqini tushuntirib ber.
2. FAQAT BOSHIDA SAVOL BER: Aniqlashtiruvchi savolni FAQAT suhbatning eng boshida (1-xabarda) va chindan ham muhim fakt yetishmasa ber. Mijoz javob bergach, boshqa savol bermasdan, tarixni eslab qolgan holda darhol aniq yechim ber.
3. 0% XATO (MUQADDAS QOIDA): Kodekslar nomi, qonunlar nomi va modda raqamlarini FAQAT senga berilgan Lex.uz matnidan aynan, xatosiz yoz. Hech qachon modda yoki qonun nomini o'zingdan to'qima. Inkor qo'shimchalarini ("kam bo'lmasligi kerak", "taqiqlanadi", "mumkin emas") aslo teskari ma'noga o'zgartirma.
4. HUQUQIY ASOSNI TABIIY QO'SH: Qonun va modda nomini gap orasida tabiiy keltirib o't (masalan: "Mehnat kodeksining 151-moddasiga ko'ra...").
5. QISQA VA RAVON: Ovozli o'qilishi uchun javobingni 3-5 ta tushunarli, jonli gapda jamla.
"""


# --- ESP32-S3 BILAN SIMSIZ ALOQA ---
def esp_get(path):
    ip = st.session_state.get("esp_ip", "").strip()
    if not ip or not st.session_state.get("esp_yoqilgan", False):
        return

    def _send():
        try:
            requests.get(f"http://{ip}{path}", timeout=0.6)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()


def esp_yubor(holat):
    arms_flag = "1" if st.session_state.get("esp_arms", False) else "0"
    esp_get(f"/state?s={holat}&arms={arms_flag}")


def boshni_qimirlatish_boshla(davomiylik):
    if not st.session_state.get("esp_head_move", True):
        return
    ip = st.session_state.get("esp_ip", "").strip()
    if not ip or not st.session_state.get("esp_yoqilgan", False):
        return

    stop_ev = st.session_state.head_stop_event
    stop_ev.clear()

    def _move_loop():
        tugash = time.time() + davomiylik
        burchaklar = [82, 98, 86, 94, 80, 100]
        i = 0
        while time.time() < tugash and not stop_ev.is_set():
            try:
                requests.get(f"http://{ip}/servo?id=neck&a={burchaklar[i % len(burchaklar)]}", timeout=0.4)
            except Exception:
                pass
            i += 1
            stop_ev.wait(0.75)
        try:
            requests.get(f"http://{ip}/servo?id=neck&a=90", timeout=0.4)
        except Exception:
            pass

    threading.Thread(target=_move_loop, daemon=True).start()


def ovozni_toxtatish():
    st.session_state.head_stop_event.set()
    subprocess.run(["killall", "afplay"], stderr=subprocess.DEVNULL)


def audio_davomiyligini_olish(fayl, matn_uzunligi):
    try:
        out = subprocess.check_output(["afinfo", fayl], text=True, stderr=subprocess.DEVNULL)
        m = re.search(r"estimated duration:\s*([\d.]+)", out)
        if m:
            return float(m.group(1))
    except Exception:
        pass
    return max(3.0, matn_uzunligi / 13.0)


# --- 3D ROBOT BOSHI ---
def robot_3d_korsat(holat="idle", gapirish_soniyasi=0.0):
    holat_matnlari = {
        "idle": "🟢 Tayyor — savolingizni kutyapman",
        "listening": "🔴 Eshitmoqdaman... Gapiring!",
        "thinking": "🟣 Lex.uz qonunlarini tahlil qilyapman...",
        "speaking": "🔊 Javob bermoqdaman..."
    }
    status_text = holat_matnlari.get(holat, "")

    html_code = f"""
    <style>
      body {{
        margin: 0; background: transparent; display: flex; flex-direction: column;
        align-items: center; justify-content: center;
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; overflow: hidden;
      }}
      .scene {{ perspective: 900px; padding: 28px 0 10px 0; }}
      .robot-head {{
        position: relative; width: 274px; height: 215px;
        background: radial-gradient(circle at 30% 25%, #3a4659, #1b222c 65%, #0e131a 100%);
        border-radius: 72px 72px 62px 62px;
        box-shadow: inset 0 10px 20px rgba(255,255,255,0.22), inset 0 -15px 25px rgba(0,0,0,0.75),
                    0 22px 45px rgba(0, 0, 0, 0.7), 0 0 32px rgba(0, 212, 255, 0.22);
        display: flex; flex-direction: column; align-items: center; justify-content: center;
        animation: float3d 3.5s ease-in-out infinite; transform-style: preserve-3d;
        border: 2px solid rgba(255,255,255,0.14);
      }}
      .robot-head::before, .robot-head::after {{
        content: ""; position: absolute; width: 23px; height: 75px; top: 68px;
        background: linear-gradient(180deg, #4b586e, #1a2029); border-radius: 12px;
        box-shadow: inset 0 2px 6px rgba(255,255,255,0.3), 0 5px 12px rgba(0,0,0,0.6);
      }}
      .robot-head::before {{ left: -21px; }}
      .robot-head::after {{ right: -21px; }}
      .antenna {{
        position: absolute; top: -28px; width: 8px; height: 28px;
        background: linear-gradient(90deg, #5a6982, #232b38); border-radius: 4px;
      }}
      .antenna::after {{
        content: ""; position: absolute; top: -13px; left: -5px; width: 18px; height: 18px;
        border-radius: 50%; background: var(--glow-color, #00e5ff);
        box-shadow: 0 0 18px var(--glow-color, #00e5ff);
      }}
      .visor {{
        width: 224px; height: 154px;
        background: radial-gradient(circle at 50% 30%, #111927, #05080f 85%);
        border-radius: 46px;
        box-shadow: inset 0 8px 18px rgba(0,0,0,0.9), inset 0 -2px 8px rgba(255,255,255,0.08), 0 2px 8px rgba(255,255,255,0.08);
        display: flex; flex-direction: column; align-items: center; justify-content: space-between;
        padding: 26px 22px 14px 22px; box-sizing: border-box; position: relative; overflow: hidden;
      }}
      .visor::before {{
        content: ""; position: absolute; top: -38px; left: -26px; width: 180px; height: 78px;
        background: linear-gradient(135deg, rgba(255,255,255,0.14), transparent);
        transform: rotate(-15deg); border-radius: 50%; pointer-events: none;
      }}
      .eyes {{ display: flex; gap: 44px; align-items: center; justify-content: center; }}
      .eye {{
        width: 48px; height: 50px; border-radius: 50% 50% 45% 45%;
        background: radial-gradient(circle at 35% 35%, #ffffff 0%, var(--glow-color, #00e5ff) 45%, #0077ff 100%);
        box-shadow: 0 0 22px var(--glow-color, #00e5ff), inset 0 0 10px #ffffff;
        animation: blink 4.2s infinite; transition: all 0.25s ease;
      }}
      .mouth-svg {{ width: 96px; height: 44px; overflow: visible; filter: drop-shadow(0 0 8px var(--glow-color, #00e5ff)); }}
      #mouth-shape {{ transition: d 0.08s ease-out; }}
      .idle {{ --glow-color: #00e5ff; }}
      .listening {{ --glow-color: #ff3b5c; }}
      .thinking {{ --glow-color: #b46cff; }}
      .speaking {{ --glow-color: #00ffaa; }}
      .thinking .eye {{ height: 20px; border-radius: 12px; animation: scan 0.9s ease-in-out infinite alternate; }}
      .status-badge {{ margin-top: 14px; color: #cbd5e1; font-size: 14px; font-weight: 500; }}
      @keyframes float3d {{ 0%, 100% {{ transform: translateY(0px) rotateX(4deg) rotateY(-4deg); }} 50% {{ transform: translateY(-8px) rotateX(-3deg) rotateY(4deg); }} }}
      @keyframes blink {{ 0%, 92%, 100% {{ transform: scaleY(1); }} 96% {{ transform: scaleY(0.08); }} }}
      @keyframes scan {{ 0% {{ transform: translateX(-8px); }} 100% {{ transform: translateX(8px); }} }}
    </style>

    <div id="robot-wrapper" class="scene {holat}">
      <div class="robot-head">
        <div class="antenna"></div>
        <div class="visor">
          <div class="eyes"><div class="eye"></div><div class="eye"></div></div>
          <svg class="mouth-svg" viewBox="0 0 100 46">
            <path id="mouth-shape" d="M 24 22 Q 50 34 76 22 Q 50 30 24 22 Z"
                  fill="rgba(0, 229, 255, 0.25)" stroke="var(--glow-color, #00e5ff)" stroke-width="3.5" stroke-linejoin="round"/>
          </svg>
        </div>
      </div>
    </div>
    <div id="status-text" class="status-badge">{status_text}</div>

    <script>
      const holat = "{holat}";
      const durationSec = {gapirish_soniyasi:.2f};
      const mouthPath = document.getElementById("mouth-shape");
      const idleSmile = "M 24 22 Q 50 35 76 22 Q 50 31 24 22 Z";
      const listenMouth = "M 36 22 Q 50 12 64 22 Q 50 32 36 22 Z";
      const thinkMouth = "M 32 23 Q 50 23 68 23 Q 50 25 32 23 Z";
      const talkShapes = [
        "M 24 20 Q 50 8 76 20 Q 50 40 24 20 Z",
        "M 34 20 Q 50 8 66 20 Q 50 38 34 20 Z",
        "M 22 22 Q 50 14 78 22 Q 50 32 22 22 Z",
        "M 38 22 Q 50 14 62 22 Q 50 32 38 22 Z",
        "M 26 22 Q 50 21 74 22 Q 50 25 26 22 Z",
        "M 28 21 Q 50 11 72 21 Q 50 36 28 21 Z"
      ];
      if (holat === "listening") {{ mouthPath.setAttribute("d", listenMouth); }}
      else if (holat === "thinking") {{ mouthPath.setAttribute("d", thinkMouth); }}
      else if (holat === "speaking" && durationSec > 0) {{
        mouthPath.setAttribute("fill", "rgba(0, 255, 170, 0.35)");
        let lastIdx = -1;
        const talkInterval = setInterval(() => {{
          let idx;
          do {{ idx = Math.floor(Math.random() * talkShapes.length); }} while (idx === lastIdx);
          lastIdx = idx;
          mouthPath.setAttribute("d", talkShapes[idx]);
        }}, 115);
        setTimeout(() => {{
          clearInterval(talkInterval);
          mouthPath.setAttribute("d", idleSmile);
          mouthPath.setAttribute("fill", "rgba(0, 229, 255, 0.25)");
          const wrap = document.getElementById("robot-wrapper");
          const txt = document.getElementById("status-text");
          if (wrap) {{ wrap.classList.remove("speaking"); wrap.classList.add("idle"); }}
          if (txt) {{ txt.innerText = "🟢 Tayyor — keyingi savolingizni kutyapman"; }}
        }}, durationSec * 1000);
      }} else {{ mouthPath.setAttribute("d", idleSmile); }}
    </script>
    """
    components.html(html_code, height=315)


@st.cache_resource
def bazani_tayyorlash(papka="baza"):
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "rb") as f:
            return pickle.load(f)
    moddalar = []
    for fayl in glob.glob(f"{papka}/*.txt"):
        qonun_nomi = os.path.basename(fayl).replace(".txt", "").replace("_", " ")
        with open(fayl, "r", encoding="utf-8", errors="ignore") as f:
            matn = f.read()
        for q in re.split(r'(?=\b\d+-modda\b)', matn):
            q = q.strip()
            if len(q) > 80:
                moddalar.append({"qonun": qonun_nomi, "matn": q[:1800]})
    vektorlar = [ollama.embeddings(model=EMBED_MODEL, prompt=i["matn"])["embedding"] for i in moddalar]
    data = {"moddalar": moddalar, "vektorlar": np.array(vektorlar, dtype=np.float32)}
    with open(INDEX_FILE, "wb") as f:
        pickle.dump(data, f)
    return data


def ovozli_savol_olish(mic_id, soniya=5, fayl="savol.wav"):
    ovozni_toxtatish()
    sd.stop()
    time.sleep(0.15)
    fs = int(sd.query_devices(mic_id)["default_samplerate"])
    try:
        audio = sd.rec(int(soniya * fs), samplerate=fs, channels=1, dtype="int16", device=mic_id)
        sd.wait()
    except sd.PortAudioError:
        sd.stop()
        time.sleep(0.2)
        default_in = sd.default.device[0]
        fs = int(sd.query_devices(default_in)["default_samplerate"])
        audio = sd.rec(int(soniya * fs), samplerate=fs, channels=1, dtype="int16", device=default_in)
        sd.wait()
    finally:
        sd.stop()
    with wave.open(fayl, "wb") as wf:
        wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(fs); wf.writeframes(audio.tobytes())
    with sr.AudioFile(fayl) as source:
        audio_data = recognizer.record(source)
    try:
        return recognizer.recognize_google(audio_data, language="uz-UZ")
    except Exception:
        return None


async def _tts_saqlash(matn, ovoz, fayl):
    await edge_tts.Communicate(matn, ovoz).save(fayl)


def gapirtirish(matn, ovoz="uz-UZ-SardorNeural", fayl="javob.mp3"):
    toza_matn = re.sub(r"[*_#`>]", "", matn)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(_tts_saqlash(toza_matn, ovoz, fayl))
    loop.close()
    ovozni_toxtatish()
    davomiylik = audio_davomiyligini_olish(fayl, len(toza_matn))
    subprocess.Popen(["afplay", fayl])
    return fayl, davomiylik


def semantik_qidiruv(savol, data, tarix=None, top_k=3):
    qidiruv_matni = savol
    if tarix and len(savol.split()) < 6:
        oldingi = [m["content"] for m in tarix if m["role"] == "user"][-1:]
        if oldingi:
            qidiruv_matni = oldingi[0] + " " + savol
    savol_vec = np.array(ollama.embeddings(model=EMBED_MODEL, prompt=qidiruv_matni)["embedding"], dtype=np.float32)
    bazaviy_vec = data["vektorlar"]
    vec_ball = np.dot(bazaviy_vec, savol_vec) / (np.linalg.norm(bazaviy_vec, axis=1) * np.linalg.norm(savol_vec) + 1e-10)
    savol_sozlari = set(re.findall(r"[a-zA-Z0-9']+", qidiruv_matni.lower()))
    yakuniy_ballar = [
        float(vec_ball[i]) + min(sum(0.05 for s in savol_sozlari if len(s) >= 2 and s in item["matn"].lower()), 0.25)
        for i, item in enumerate(data["moddalar"])
    ]
    eng_yaxshi = np.argsort(yakuniy_ballar)[::-1][:top_k]
    return [(min(99.0, max(60.0, (yakuniy_ballar[idx] / 0.85) * 100)) / 100.0, data["moddalar"][idx]) for idx in eng_yaxshi]


def javob_berish(savol, topilganlar, tarix=None):
    kontekst = "\n\n---\n\n".join([f"[Lex.uz manbasi: {item['qonun']}]\n{item['matn']}" for _, item in topilganlar])
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if tarix:
        for m in tarix[-20:]:
            messages.append({"role": m["role"], "content": m["content"]})
    holat = (
        "ESLATMA: Bu 1-xabar. Qisqa tushuntirib, kerak bo'lsa 1-2 ta tabiiy savol ber."
        if not tarix else
        "ESLATMA: Bu suhbat davomi. Boshqa savol BERMA, tarixga qarab aniq yechim ber."
    )
    messages.append({"role": "user", "content": f"{holat}\n\nLEX.UZ:\n{kontekst}\n\nSAVOL: {savol}"})
    t0 = time.time()
    model_nomi = "Groq (120B)"
    try:
        javob = groq_client.chat.completions.create(model=GROQ_MODEL, messages=messages, temperature=0.15).choices[0].message.content.strip()
    except Exception:
        model_nomi = "Lokal Ollama (Oflayn)"
        javob = ollama.chat(model=FALLBACK_LOCAL_MODEL, messages=messages, options={"temperature": 0.1})["message"]["content"].strip()
    return javob, time.time() - t0, model_nomi


# --- STREAMLIT INTERFEYSI ---
st.set_page_config(page_title="LexRobot 3D AI", page_icon="🤖", layout="centered")

with st.sidebar:
    st.header("🤖 LexRobot Pulti")
    st.toggle("Robotni ulash (Wi-Fi)", value=True, key="esp_yoqilgan")
    st.text_input("ESP32 IP manzili:", value="172.20.10.5", key="esp_ip")

    c_t1, c_t2 = st.columns(2)
    with c_t1:
        st.toggle("🗣️ Bosh qimirlasin", value=True, key="esp_head_move")
    with c_t2:
        st.toggle("🦾 Qo'l qimirlasin", value=False, key="esp_arms")

    tab_motor, tab_servo, tab_rgb = st.tabs(["🛞 Motor", "🦾 Servo", "🌈 RGB"])

    with tab_motor:
        m_speed = st.slider("Tezlik (PWM):", 80, 255, 140, key="m_speed")
        m_step = st.slider("Qadam vaqti (ms):", 100, 1500, 350, step=50, key="m_step")

        _, m_up, _ = st.columns(3)
        with m_up:
            if st.button("⬆️", use_container_width=True, key="m_fwd"):
                esp_get(f"/speed?v={m_speed}")
                esp_get(f"/motor?dir=fwd&ms={m_step}")

        m_left, m_stop, m_right = st.columns(3)
        with m_left:
            if st.button("⬅️", use_container_width=True, key="m_l"):
                esp_get(f"/speed?v={m_speed}")
                esp_get(f"/motor?dir=left&ms={m_step}")
        with m_stop:
            if st.button("⏹", type="primary", use_container_width=True, key="m_s"):
                esp_get("/motor?dir=stop")
        with m_right:
            if st.button("➡️", use_container_width=True, key="m_r"):
                esp_get(f"/speed?v={m_speed}")
                esp_get(f"/motor?dir=right&ms={m_step}")

        _, m_down, _ = st.columns(3)
        with m_down:
            if st.button("⬇️", use_container_width=True, key="m_b"):
                esp_get(f"/speed?v={m_speed}")
                esp_get(f"/motor?dir=back&ms={m_step}")

    with tab_servo:
        neck_a = st.slider("🗣️ Bo'yin (30°–150°):", 30, 150, 90, key="s_neck")
        if st.button("Bo'yinni burish", use_container_width=True):
            esp_get(f"/servo?id=neck&a={neck_a}")

        left_a = st.slider("🦾 Chap qo'l:", 0, 180, 90, key="s_left")
        if st.button("Chap qo'lni o'rnatish", use_container_width=True):
            esp_get(f"/servo?id=left&a={left_a}")

        right_a = st.slider("🦾 O'ng qo'l:", 0, 180, 90, key="s_right")
        if st.button("O'ng qo'lni o'rnatish", use_container_width=True):
            esp_get(f"/servo?id=right&a={right_a}")

        st.divider()
        st.caption("Tezkor funksiyalar:")
        sf1, sf2 = st.columns(2)
        with sf1:
            if st.button("👋 Salom", use_container_width=True):
                esp_get("/action?cmd=wave")
        with sf2:
            if st.button("🙌 Qo'l yuqori", use_container_width=True):
                esp_get("/action?cmd=up")
        sf3, sf4 = st.columns(2)
        with sf3:
            if st.button("🧘 Tinch", use_container_width=True):
                esp_get("/action?cmd=rest")
                esp_get("/servo?id=neck&a=90")
        with sf4:
            if st.button("👀 Atrofga qara", use_container_width=True):
                boshni_qimirlatish_boshla(3.0)

    with tab_rgb:
        hex_color = st.color_picker("Ko'z rangi (RGB / HEX):", "#00e5ff")
        r_val = int(hex_color[1:3], 16)
        g_val = int(hex_color[3:5], 16)
        b_val = int(hex_color[5:7], 16)
        st.caption(f"R: {r_val} | G: {g_val} | B: {b_val}")

        if st.button("🎨 Shu rangni yoqish", type="primary", use_container_width=True):
            esp_get(f"/rgb?r={r_val}&g={g_val}&b={b_val}")

        bright = st.slider("💡 Yorug'lik:", 10, 200, 70, key="rgb_br")
        if st.button("Yorug'likni qo'llash", use_container_width=True):
            esp_get(f"/eye?b={bright}")

        st.divider()
        st.caption("Animatsiyalar:")
        e1, e2 = st.columns(2)
        with e1:
            if st.button("🔵 Ko'k", use_container_width=True): esp_get("/eye?m=1")
            if st.button("🟣 O'ylash", use_container_width=True): esp_get("/eye?m=3")
            if st.button("🌈 Kamalak", use_container_width=True): esp_get("/eye?m=5")
        with e2:
            if st.button("🔴 Qizil", use_container_width=True): esp_get("/eye?m=2")
            if st.button("🟢 Yashil", use_container_width=True): esp_get("/eye?m=4")
            if st.button("⚫ O'chirish", use_container_width=True): esp_get("/eye?m=0")


robot_placeholder = st.empty()
with robot_placeholder:
    robot_3d_korsat("idle")

mic_options = {f"[{i}] {d['name']}": i for i, d in enumerate(sd.query_devices()) if d["max_input_channels"] > 0}

col1, col2, col3 = st.columns([2, 1, 1])
with col1:
    tanlangan_mic = st.selectbox("Mikrofon:", list(mic_options.keys()))
with col2:
    tanlangan_ovoz = st.selectbox("Ovoz:", ["uz-UZ-SardorNeural", "uz-UZ-MadinaNeural"],
                                  format_func=lambda x: "👨 Sardor" if "Sardor" in x else "👩 Madina")
with col3:
    yozish_vaqti = st.number_input("Soniya:", min_value=3, max_value=10, value=6)

c_btn1, c_btn2, c_btn3 = st.columns([2.5, 1.2, 1.2])
with c_btn1:
    ovoz_tugma = st.button("🎤 BOSIB GAPIRING", type="primary", use_container_width=True)
with c_btn2:
    stop_tugma = st.button("⏹️ Ovozni to'xtatish", use_container_width=True)
with c_btn3:
    yangi_tugma = st.button("🔄 Yangi suhbat", use_container_width=True)

if stop_tugma:
    ovozni_toxtatish()
    esp_yubor("idle")
    with robot_placeholder:
        robot_3d_korsat("idle")

if yangi_tugma:
    ovozni_toxtatish()
    esp_yubor("idle")
    st.session_state.messages = []
    st.rerun()

st.divider()

data = bazani_tayyorlash("baza")
if "messages" not in st.session_state:
    st.session_state.messages = []

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if "manbalar" in msg:
            with st.expander("📜 Asoslangan qonun moddalari (Lex.uz)"):
                for ball, item in msg["manbalar"]:
                    st.markdown(f"**{item['qonun']}** *(moslik: {ball*100:.1f}%)*")
                    st.info(item["matn"])

savol = st.chat_input("Yoki savolingizni yozing...")

if ovoz_tugma:
    esp_yubor("listen")
    with robot_placeholder:
        robot_3d_korsat("listening")
    matn = ovozli_savol_olish(mic_options[tanlangan_mic], soniya=int(yozish_vaqti))
    if matn:
        savol = matn
    else:
        esp_yubor("idle")
        with robot_placeholder:
            robot_3d_korsat("idle")
        st.warning("❌ Ovoz tushunilmadi, qaytadan urinib ko'ring.")

if savol:
    oldingi_tarix = st.session_state.messages.copy()
    st.session_state.messages.append({"role": "user", "content": savol})
    with st.chat_message("user"):
        st.markdown(savol)

    esp_yubor("think")
    with robot_placeholder:
        robot_3d_korsat("thinking")

    with st.chat_message("assistant"):
        topilganlar = semantik_qidiruv(savol, data, tarix=oldingi_tarix, top_k=3)
        javob, vaqt, model_nomi = javob_berish(savol, topilganlar, tarix=oldingi_tarix)

        audio_fayl, davomiylik = gapirtirish(javob, ovoz=tanlangan_ovoz)
        esp_yubor("speak")
        boshni_qimirlatish_boshla(davomiylik)
        threading.Timer(davomiylik, lambda: esp_yubor("idle")).start()

        with robot_placeholder:
            robot_3d_korsat("speaking", gapirish_soniyasi=davomiylik)

        st.markdown(javob)
        st.caption(f"⏱ {vaqt:.2f} soniya | 🧠 {model_nomi}")

        with st.expander("📜 Asoslangan qonun moddalari (Lex.uz)"):
            for ball, item in topilganlar:
                st.markdown(f"**{item['qonun']}** *(moslik: {ball*100:.1f}%)*")
                st.info(item["matn"])

    st.session_state.messages.append({
        "role": "assistant",
        "content": javob,
        "manbalar": topilganlar
    })