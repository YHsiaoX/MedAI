import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
import base64
from datetime import datetime
import fitz  # PyMuPDF
from qcloud_cos import CosConfig
from qcloud_cos import CosS3Client
import json

# ==========================================
# 0. 系统配置与 COS 云端初始化
# ==========================================
st.set_page_config(page_title="医智元 MedAI | 智库平台", layout="wide")

# 🎯 核心配置
DIFY_BASE_URL = "http://1.14.246.12/v1" 

@st.cache_resource
def get_cos_client():
    """使用缓存确保客户端只初始化一次"""
    try:
        cos_config = CosConfig(
            Region=st.secrets["COS_REGION"],
            SecretId=st.secrets["COS_SECRET_ID"],
            SecretKey=st.secrets["COS_SECRET_KEY"],
            Scheme='https'
        )
        return CosS3Client(cos_config)
    except Exception as e:
        st.error(f"❌ COS 客户端初始化失败: {e}")
        return None

cos_client = get_cos_client()

# --- 云端记忆工具函数 ---
def load_history_from_cos(user_id, subject):
    file_key = f"users/{user_id}/chat_history_{subject}.json"
    try:
        response = cos_client.get_object(Bucket=st.secrets["COS_BUCKET"], Key=file_key)
        content = response['Body'].get_raw_stream().read().decode('utf-8')
        return json.loads(content)
    except:
        return []

def save_history_to_cos(user_id, subject, history_list):
    file_key = f"users/{user_id}/chat_history_{subject}.json"
    try:
        cos_client.put_object(
            Bucket=st.secrets["COS_BUCKET"],
            Body=json.dumps(history_list, ensure_ascii=False).encode('utf-8'),
            Key=file_key
        )
    except Exception as e:
        st.warning(f"⚠️ 云端记忆同步失败: {e}")

# ==========================================
# 0.1 门禁系统 (Login)
# ==========================================
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "current_subject" not in st.session_state:
    st.session_state.current_subject = None

if not st.session_state.user_id:
    st.title("🔐 医智元 MedAI 专属智库")
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.info("💡 内部测试期间免密登录，请输入专属代号。")
        user_code = st.text_input("👤 专属代号:")
        if st.button("🚀 验证并进入系统", use_container_width=True):
            if user_code.strip():
                st.session_state.user_id = user_code.strip()
                st.rerun()
    st.stop()

# ==========================================
# 0.2 多学科初始化
# ==========================================
SUBJECT_CONFIG = {
    "外科学": st.secrets.get("DIFY_KEY_SURGERY", "YOUR_KEY"),
    "内科学": st.secrets.get("DIFY_KEY_INTERNAL", "YOUR_KEY"),
    "影像学": st.secrets.get("DIFY_KEY_IMAGING", "YOUR_KEY"),
    "流行病学": st.secrets.get("DIFY_KEY_EPI", "YOUR_KEY")
}
SUBJECTS = list(SUBJECT_CONFIG.keys())
BASE_UPLOAD_DIR = "uploaded_materials"

@st.cache_resource
def system_init():
    for sub in SUBJECTS:
        os.makedirs(os.path.join(BASE_UPLOAD_DIR, sub), exist_ok=True)
    conn = sqlite3.connect('medai_cloud.db', check_same_thread=False)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS student_logs 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, 
                  student_id TEXT, subject TEXT, query TEXT, has_image BOOLEAN)''')
    conn.commit()
    conn.close()
    return True

system_init()

# ==========================================
# 1. 核心工具函数 (PDF/DB/Upload)
# ==========================================
def find_page_by_text(file_path, search_text):
    try:
        doc = fitz.open(file_path)
        clean_search = search_text.replace(" ", "").replace("\n", "")[:20]
        for page_num in range(len(doc)):
            if clean_search in doc.load_page(page_num).get_text().replace(" ", "").replace("\n", ""):
                doc.close()
                return page_num + 1
        doc.close()
    except: pass
    return 1

def display_pdf_with_page(file_path, page_num=1):
    with open(file_path, "rb") as f:
        base64_pdf = base64.b64encode(f.read()).decode('utf-8')
    pdf_url = f"data:application/pdf;base64,{base64_pdf}#page={page_num}"
    st.markdown(f'<embed src="{pdf_url}" width="100%" height="800" type="application/pdf"></embed>', unsafe_allow_html=True)

def log_to_db(student_id, subject, query, has_image=False):
    conn = sqlite3.connect('medai_cloud.db')
    conn.cursor().execute("INSERT INTO student_logs (timestamp, student_id, subject, query, has_image) VALUES (?, ?, ?, ?, ?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), student_id, subject, query, has_image))
    conn.commit()
    conn.close()

def upload_image_to_dify(image_bytes, file_name, api_key):
    url = f"{DIFY_BASE_URL}/files/upload"
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        r = requests.post(url, headers=headers, files={"file": (file_name, image_bytes, "image/jpeg")}, data={"user": st.session_state.user_id}, timeout=30)
        return r.json().get("id")
    except: return None

# ==========================================
# 2. 侧边栏
# ==========================================
st.sidebar.title("🔐 MedAI 导航")
st.sidebar.success(f"当前在线: **{st.session_state.user_id}**")
if st.sidebar.button("🚪 退出登录"):
    st.session_state.user_id = None
    st.session_state.clear() # 彻底清理防止串号
    st.rerun()

st.sidebar.divider()
role = st.sidebar.radio("请选择身份：", ["🧑‍🎓 学生端", "👨‍🏫 教师端", "🏛️ 教研室端"])

# ==========================================
# 3. 🧑‍🎓 学生端
# ==========================================
if role == "🧑‍🎓 学生端":
    st.title("📚 医智元：期末复习导航")
    subject = st.selectbox("🎯 请选择当前复习科目", SUBJECTS)
    
    # --- 🧠 核心记忆逻辑：科目切换检测 ---
    chat_history_key = f"chat_history_{subject}"
    
    # 如果用户换了科目，或者这是第一次加载记忆
    if st.session_state.current_subject != subject:
        with st.spinner(f"⏳ 正在云端调取【{subject}】的复习档案..."):
            st.session_state[chat_history_key] = load_history_from_cos(st.session_state.user_id, subject)
            st.session_state.current_subject = subject
    
    CURRENT_API_KEY = SUBJECT_CONFIG[subject]
    if "show_pdf" not in st.session_state: st.session_state.show_pdf = False

    # 渲染历史
    for msg_idx, msg in enumerate(st.session_state.get(chat_history_key, [])):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("real_sources"):
                with st.expander("📂 查看引用依据"):
                    for i, s in enumerate(msg["real_sources"]):
                        d_name = s.get('document_name')
                        if st.button(f"📖 定位原文:《{d_name}》", key=f"hist_{subject}_{msg_idx}_{i}"):
                            st.session_state.current_page = find_page_by_text(os.path.join(BASE_UPLOAD_DIR, subject, d_name), s.get('content'))
                            st.session_state.current_pdf = os.path.join(BASE_UPLOAD_DIR, subject, d_name)
                            st.session_state.show_pdf = True
                            st.rerun()

    input_mode = st.radio("输入方式：", ["🚫 纯文字", "🖼️ 上传图片", "📸 拍照"], horizontal=True)
    uploaded_img = st.file_uploader("选图", type=["jpg","png"]) if input_mode=="🖼️ 上传图片" else None
    camera_img = st.camera_input("拍照") if input_mode=="📸 拍照" else None
    user_query = st.chat_input("询问教材内容...")

    final_img = camera_img if camera_img else uploaded_img
    
    if user_query or final_img:
        q_text = user_query if user_query else "解析图片..."
        st.session_state[chat_history_key].append({"role": "user", "content": q_text})
        
        with st.chat_message("user"):
            if final_img: st.image(final_img, width=300)
            st.markdown(q_text)

        with st.chat_message("assistant"):
            with st.spinner("检索中..."):
                f_id = upload_image_to_dify(final_img.getvalue(), "img.jpg", CURRENT_API_KEY) if final_img else None
                payload = {
                    "inputs": {}, "query": q_text, "response_mode": "blocking", 
                    "user": st.session_state.user_id,
                    "files": [{"type": "image", "transfer_method": "local_file", "upload_file_id": f_id}] if f_id else []
                }
                
                try:
                    r = requests.post(f"{DIFY_BASE_URL}/chat-messages", headers={"Authorization": f"Bearer {CURRENT_API_KEY}"}, json=payload, timeout=120)
                    if r.status_code == 200:
                        res = r.json()
                        ans = res.get("answer", "...")
                        srcs = res.get("metadata", {}).get("retriever_resources", [])
                        st.markdown(ans)
                        
                        # --- ☁️ 实时存云端 ---
                        st.session_state[chat_history_key].append({"role": "assistant", "content": ans, "real_sources": srcs})
                        save_history_to_cos(st.session_state.user_id, subject, st.session_state[chat_history_key])
                        
                        if srcs:
                            with st.expander("📂 教材溯源"):
                                for i, doc in enumerate(srcs):
                                    d_name = doc.get("document_name")
                                    if st.button(f"📖 定位：{d_name}", key=f"new_{subject}_{i}"):
                                        st.session_state.current_page = find_page_by_text(os.path.join(BASE_UPLOAD_DIR, subject, d_name), doc.get("content"))
                                        st.session_state.current_pdf = os.path.join(BASE_UPLOAD_DIR, subject, d_name)
                                        st.session_state.show_pdf = True
                                        st.rerun()
                        log_to_db(st.session_state.user_id, subject, q_text, bool(final_img))
                    else: st.error(f"Error: {r.status_code}")
                except Exception as e: st.error(f"链接失败: {e}")

    if st.session_state.show_pdf:
        st.divider()
        if st.button("✖️ 关闭阅读器"): st.session_state.show_pdf = False; st.rerun()
        display_pdf_with_page(st.session_state.current_pdf, st.session_state.get("current_page", 1))

# ==========================================
# 4. 教师/教研室端
# ==========================================
elif role == "👨‍🏫 教师端":
    st.title("📤 数字化教材入库")
    target_sub = st.selectbox("📂 目标学科", SUBJECTS)
    files = st.file_uploader("选择 PDF", type=["pdf"], accept_multiple_files=True)
    if st.button("⚡ 开始入库"):
        for f in files:
            with open(os.path.join(BASE_UPLOAD_DIR, target_sub, f.name), "wb") as save_f:
                save_f.write(f.getbuffer())
        st.success("✅ 教材已存入本地库")

elif role == "🏛️ 教研室端":
    st.title("📊 学习大数据监控")
    conn = sqlite3.connect('medai_cloud.db')
    st.dataframe(pd.read_sql_query("SELECT * FROM student_logs ORDER BY timestamp DESC", conn), use_container_width=True)
    conn.close()