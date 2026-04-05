import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
import base64
from datetime import datetime
import fitz  # PyMuPDF

# ==========================================
# 0. 系统配置与门禁系统
# ==========================================
st.set_page_config(page_title="医智元 MedAI | 智库平台", layout="wide")

# 🎯 核心修改：指向你现在能打开的私有 IP 地址
# 注意：如果 /v1 报错 404，请尝试将其改为 "http://1.14.246.12/api/v1"
DIFY_BASE_URL = "http://1.14.246.12/v1" 

if "user_id" not in st.session_state:
    st.session_state.user_id = None

if not st.session_state.user_id:
    st.title("🔐 医智元 MedAI 专属智库")
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.info("💡 内部测试期间免密登录，请输入教研室分配的专属代号。")
        user_code = st.text_input("👤 专属代号 (如: A, B, C...):")
        if st.button("🚀 验证并进入系统", use_container_width=True):
            if user_code.strip():
                st.session_state.user_id = user_code.strip()
                st.rerun()
            else:
                st.error("⚠️ 代号不能为空！")
    st.stop()

# ==========================================
# 0.1 初始化与多学科配置
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
    """提速魔法：文件夹和数据库初始化只跑一次"""
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

def get_subject_dir(subject_name):
    return os.path.join(BASE_UPLOAD_DIR, subject_name)

# ==========================================
# 1. 核心工具函数
# ==========================================
def find_page_by_text(file_path, search_text):
    try:
        doc = fitz.open(file_path)
        clean_search = search_text.replace(" ", "").replace("\n", "").replace("", "").replace("", "")
        fingerprint = clean_search[:20]
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            if fingerprint in page.get_text().replace(" ", "").replace("\n", ""):
                doc.close()
                return page_num + 1
        doc.close()
    except: pass
    return 1

def display_pdf_with_page(file_path, page_num=1):
    with open(file_path, "rb") as f:
        base64_pdf = base64.b64encode(f.read()).decode('utf-8')
    pdf_url = f"data:application/pdf;base64,{base64_pdf}#page={page_num}"
    pdf_display = f'<embed src="{pdf_url}" width="100%" height="800" type="application/pdf"></embed>'
    st.markdown(pdf_display, unsafe_allow_html=True)

def log_to_db(student_id, subject, query, has_image=False):
    conn = sqlite3.connect('medai_cloud.db')
    c = conn.cursor()
    c.execute("INSERT INTO student_logs (timestamp, student_id, subject, query, has_image) VALUES (?, ?, ?, ?, ?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), student_id, subject, query, has_image))
    conn.commit()
    conn.close()

def upload_image_to_dify(image_bytes, file_name, current_api_key):
    url = f"{DIFY_BASE_URL}/files/upload"
    headers = {"Authorization": f"Bearer {current_api_key}"}
    files = {"file": (file_name, image_bytes, "image/jpeg")}
    try:
        response = requests.post(url, headers=headers, files=files, data={"user": st.session_state.user_id}, timeout=30)
        return response.json().get("id")
    except Exception as e:
        st.error(f"📸 图片上传链路故障: {e}")
        return None

# ==========================================
# 2. 侧边栏
# ==========================================
st.sidebar.title("🔐 MedAI 导航")
st.sidebar.success(f"当前在线: **{st.session_state.user_id}**")
if st.sidebar.button("🚪 退出登录"):
    st.session_state.user_id = None
    st.rerun()

st.sidebar.divider()
role = st.sidebar.radio("请选择身份：", ["🧑‍🎓 学生端", "👨‍🏫 教师端", "🏛️ 教研室端"])

# ==========================================
# 3. 🧑‍🎓 学生端
# ==========================================
if role == "🧑‍🎓 学生端":
    st.title("📚 医智元：期末复习导航")
    subject = st.selectbox("🎯 请选择当前复习科目", SUBJECTS)
    CURRENT_API_KEY = SUBJECT_CONFIG[subject]
    
    chat_history_key = f"chat_history_{subject}"
    if chat_history_key not in st.session_state: st.session_state[chat_history_key] = []
    if "show_pdf" not in st.session_state: st.session_state.show_pdf = False

    # 历史记录渲染
    for msg_idx, msg in enumerate(st.session_state[chat_history_key]):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("real_sources"):
                with st.expander("📂 查看引用依据"):
                    for i, s in enumerate(msg["real_sources"]):
                        d_name = s.get('document_name')
                        if st.button(f"📖 定位原文:《{d_name}》", key=f"hist_{subject}_{msg_idx}_{i}"):
                            st.session_state.current_page = find_page_by_text(os.path.join(get_subject_dir(subject), d_name), s.get('content'))
                            st.session_state.current_pdf = os.path.join(get_subject_dir(subject), d_name)
                            st.session_state.show_pdf = True
                            st.rerun()

    input_mode = st.radio("输入方式：", ["🚫 纯文字", "🖼️ 上传图片", "📸 拍照"], horizontal=True)
    uploaded_img = st.file_uploader("选图", type=["jpg","png"]) if input_mode=="🖼️ 上传图片" else None
    camera_img = st.camera_input("拍照") if input_mode=="📸 拍照" else None
    user_query = st.chat_input("询问教材内容...")

    final_img = camera_img if camera_img else uploaded_img
    
    if user_query or final_img:
        query_text = user_query if user_query else "帮我分析这张医学图片内容。"
        st.session_state[chat_history_key].append({"role": "user", "content": query_text})
        
        with st.chat_message("user"):
            if final_img: st.image(final_img, width=300)
            st.markdown(query_text)

        with st.chat_message("assistant"):
            with st.spinner(f"正在检索【{subject}】私有智库..."):
                file_id = upload_image_to_dify(final_img.getvalue(), "img.jpg", CURRENT_API_KEY) if final_img else None
                
                headers = {"Authorization": f"Bearer {CURRENT_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "inputs": {}, "query": query_text, "response_mode": "blocking", 
                    "user": st.session_state.user_id,
                    "files": [{"type": "image", "transfer_method": "local_file", "upload_file_id": file_id}] if file_id else []
                }
                
                try:
                    r = requests.post(f"{DIFY_BASE_URL}/chat-messages", headers=headers, json=payload, timeout=120)
                    
                    if r.status_code != 200:
                        st.error(f"📡 接口响应失败 (状态码: {r.status_code})")
                        st.text(f"回传原始内容：{r.text[:300]}")
                    else:
                        res = r.json()
                        answer = res.get("answer", "未获得有效回答")
                        sources = res.get("metadata", {}).get("retriever_resources", [])
                        st.markdown(answer)
                        
                        if sources:
                            with st.expander("📂 教材深度溯源"):
                                for i, doc in enumerate(sources):
                                    d_name = doc.get("document_name")
                                    path = os.path.join(get_subject_dir(subject), d_name)
                                    if st.button(f"📖 定位原文：{d_name}", key=f"new_{subject}_{i}"):
                                        st.session_state.current_page = find_page_by_text(path, doc.get("content"))
                                        st.session_state.current_pdf = path
                                        st.session_state.show_pdf = True
                                        st.rerun()
                        
                        st.session_state[chat_history_key].append({"role": "assistant", "content": answer, "real_sources": sources})
                        log_to_db(st.session_state.user_id, subject, query_text, bool(final_img))
                except Exception as e:
                    st.error(f"❌ 链接中断：{e}")

    if st.session_state.show_pdf:
        st.divider()
        if st.button("✖️ 关闭阅读器"): st.session_state.show_pdf = False; st.rerun()
        display_pdf_with_page(st.session_state.current_pdf, st.session_state.get("current_page", 1))

# ==========================================
# 4. 教师/教研室端
# ==========================================
elif role == "👨‍🏫 教师端":
    st.title("📤 数字化教材入库")
    target_subject = st.selectbox("📂 目标学科", SUBJECTS)
    files = st.file_uploader("选择 PDF 教材", type=["pdf"], accept_multiple_files=True)
    if st.button("⚡ 开始入库"):
        if files:
            for f in files:
                with open(os.path.join(get_subject_dir(target_subject), f.name), "wb") as save_f:
                    save_f.write(f.getbuffer())
            st.success("✅ 教材已存入本地定位库，请确保 Dify 后端也已同步上传。")

elif role == "🏛️ 教研室端":
    st.title("📊 学习大数据监控")
    @st.cache_data(ttl=30)
    def load_logs():
        conn = sqlite3.connect('medai_cloud.db')
        df = pd.read_sql_query("SELECT * FROM student_logs ORDER BY timestamp DESC", conn)
        conn.close()
        return df
    df = load_logs()
    c1, c2 = st.columns(2)
    c1.metric("累计互动", len(df))
    c2.metric("活跃学科数", df['subject'].nunique() if not df.empty else 0)
    st.dataframe(df, use_container_width=True)