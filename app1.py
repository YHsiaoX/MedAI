import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
import base64
from datetime import datetime
import fitz  # 云端会自动安装 PyMuPDF

# ==========================================
# 0. 系统配置
# ==========================================
st.set_page_config(page_title="医智元 MedAI | 云端版", layout="wide")

# 云端临时目录
UPLOAD_DIR = "uploaded_materials"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# --- 工具函数：页码探测引擎 ---
def find_page_by_text(file_path, search_text):
    try:
        doc = fitz.open(file_path)
        clean_search = search_text.replace(" ", "").replace("\n", "").replace("", "").replace("", "")
        fingerprint = clean_search[:20]
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            page_text = page.get_text().replace(" ", "").replace("\n", "")
            if fingerprint in page_text:
                doc.close()
                return page_num + 1
        doc.close()
    except Exception as e:
        st.error(f"定位引擎报错: {e}")
    return 1

# --- 工具函数：精准跳转阅读器 ---
def display_pdf_with_page(file_path, page_num=1):
    with open(file_path, "rb") as f:
        base64_pdf = base64.b64encode(f.read()).decode('utf-8')
    pdf_url = f"data:application/pdf;base64,{base64_pdf}#page={page_num}"
    pdf_display = f'<embed src="{pdf_url}" width="100%" height="800" type="application/pdf"></embed>'
    st.caption(f"🚀 云端引擎已定位至第 {page_num} 页")
    st.markdown(pdf_display, unsafe_allow_html=True)

# 数据库初始化（注：云端重启后数据会重置，后续需接入云数据库）
def init_db():
    conn = sqlite3.connect('medai_cloud.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS student_logs 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, 
                  student_id TEXT, subject TEXT, query TEXT, is_resolved BOOLEAN)''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 1. 角色网关
# ==========================================
st.sidebar.title("🔐 MedAI 云端入口")
role = st.sidebar.radio("请选择身份：", ["🧑‍🎓 学生端", "👨‍🏫 教师端", "🏛️ 教研室端"])

# ⚠️ 关键：从 Streamlit Secrets 读取 API Key
try:
    API_KEY = st.secrets["DIFY_API_KEY"]
except:
    st.error("❌ 未在云端 Secrets 中配置 DIFY_API_KEY")
    st.stop()

# ==========================================
# 2. 学生端逻辑
# ==========================================
if role == "🧑‍🎓 学生端":
    st.title("📚 医智元：期末复习导航")
    
    # Session 状态初始化
    if "chat_history" not in st.session_state: st.session_state.chat_history = []
    if "show_pdf" not in st.session_state: st.session_state.show_pdf = False
    if "current_page" not in st.session_state: st.session_state.current_page = 1

    # 渲染对话
    for msg_idx, msg in enumerate(st.session_state.chat_history):
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
            if msg.get("has_source") and "real_sources" in msg:
                with st.expander("📂 查看引用依据"):
                    for i, s in enumerate(msg["real_sources"]):
                        doc_name = s.get('document_name')
                        st.info(f"🎯 证据来自《{doc_name}》")
                        local_path = os.path.join(UPLOAD_DIR, doc_name)
                        if os.path.exists(local_path):
                            if st.button(f"📖 定位原文", key=f"hist_{msg_idx}_{i}"):
                                st.session_state.current_page = find_page_by_text(local_path, s.get('content'))
                                st.session_state.current_pdf = local_path
                                st.session_state.show_pdf = True
                                st.rerun()

    user_query = st.chat_input("询问教材内容...")
    if user_query:
        st.session_state.chat_history.append({"role": "user", "content": user_query})
        with st.chat_message("user"): st.markdown(user_query)

        with st.chat_message("assistant"):
            with st.spinner("AI 正在思考..."):
                headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
                payload = {"inputs": {}, "query": user_query, "response_mode": "blocking", "user": "cloud_user"}
                try:
                    r = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=payload, timeout=60)
                    res = r.json()
                    answer = res.get("answer", "...")
                    sources = res.get("metadata", {}).get("retriever_resources", [])
                    st.markdown(answer)
                    
                    if sources:
                        with st.expander("📂 来源依据", expanded=True):
                            for i, doc in enumerate(sources):
                                d_name = doc.get("document_name")
                                st.success(f"🎯 《{d_name}》")
                                path = os.path.join(UPLOAD_DIR, d_name)
                                if os.path.exists(path) and st.button(f"📖 定位原文：{d_name}", key=f"new_{i}"):
                                    st.session_state.current_page = find_page_by_text(path, doc.get("content"))
                                    st.session_state.current_pdf = path
                                    st.session_state.show_pdf = True
                                    st.rerun()
                    
                    st.session_state.chat_history.append({
                        "role": "assistant", "content": answer, 
                        "has_source": True if sources else False,
                        "real_sources": sources
                    })
                except Exception as e:
                    st.error(f"连接超时，请重试: {e}")

    # 全局阅读器
    if st.session_state.show_pdf and st.session_state.get("current_pdf"):
        st.divider()
        if st.button("✖️ 关闭阅读"):
            st.session_state.show_pdf = False
            st.rerun()
        display_pdf_with_page(st.session_state.current_pdf, st.session_state.current_page)

# ==========================================
# 3. 教师端
# ==========================================
elif role == "👨‍🏫 教师端":
    st.title("📤 资产入库")
    files = st.file_uploader("上传 PDF", type=["pdf"], accept_multiple_files=True)
    if st.button("🚀 确认上传"):
        for f in files:
            with open(os.path.join(UPLOAD_DIR, f.name), "wb") as save_f:
                save_f.write(f.getbuffer())
        st.success("已存入云端临时目录")