import streamlit as st
import sqlite3
import pandas as pd
import requests
import os
import base64
from datetime import datetime
import fitz  # PyMuPDF

# ==========================================
# 0. 系统配置与门禁系统 (Login)
# ==========================================
st.set_page_config(page_title="医智元 MedAI | 智库平台", layout="wide")

# ⚠️ 全局拦截：极简登录界面
if "user_id" not in st.session_state:
    st.session_state.user_id = None

if not st.session_state.user_id:
    # 这里是未登录时看到的页面
    st.title("🔐 医智元 MedAI 专属智库")
    st.markdown("---")
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.info("💡 内部测试期间免密登录，请输入教研室分配的专属代号。")
        user_code = st.text_input("👤 专属代号 (如: A, B, C...):")
        if st.button("🚀 验证并进入系统", use_container_width=True):
            if user_code.strip():
                st.session_state.user_id = user_code.strip()
                st.rerun()  # 刷新页面，进入主系统
            else:
                st.error("⚠️ 代号不能为空！")
    st.stop() # 关键：拦截下面的所有代码执行

# ==========================================
# 0.1 学科、API 与目录路由初始化 (核心修改区)
# ==========================================
# 🎯 更新：配置多学科及其对应的专属 Dify API Key
# 建议在 .streamlit/secrets.toml 中配置这些 key
SUBJECT_CONFIG = {
    "外科学": st.secrets.get("DIFY_KEY_SURGERY", "YOUR_SURGERY_API_KEY"),
    "内科学": st.secrets.get("DIFY_KEY_INTERNAL", "YOUR_INTERNAL_API_KEY"),
    "影像学": st.secrets.get("DIFY_KEY_IMAGING", "YOUR_IMAGING_API_KEY"),
    "流行病学": st.secrets.get("DIFY_KEY_EPI", "YOUR_EPI_API_KEY")
}

SUBJECTS = list(SUBJECT_CONFIG.keys())
BASE_UPLOAD_DIR = "uploaded_materials"

# 启动时自动为每个学科建立专属的“单间”
for sub in SUBJECTS:
    os.makedirs(os.path.join(BASE_UPLOAD_DIR, sub), exist_ok=True)

# 动态获取学科专属路径的函数
def get_subject_dir(subject_name):
    return os.path.join(BASE_UPLOAD_DIR, subject_name)

# ==========================================
# 1. 核心工具函数与云端初始化
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
    except Exception as e:
        print(f"定位异常: {e}")
    return 1

def display_pdf_with_page(file_path, page_num=1):
    with open(file_path, "rb") as f:
        base64_pdf = base64.b64encode(f.read()).decode('utf-8')
    pdf_url = f"data:application/pdf;base64,{base64_pdf}#page={page_num}"
    pdf_display = f'<embed src="{pdf_url}" width="100%" height="800" type="application/pdf"></embed>'
    st.caption(f"🚀 云端引擎已定位至第 {page_num} 页")
    st.markdown(pdf_display, unsafe_allow_html=True)

# 本地 SQLite 数据库保持统一，通过 subject 字段区分，方便教研室端统一看板
def init_db():
    conn = sqlite3.connect('medai_cloud.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS student_logs 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME, 
                  student_id TEXT, subject TEXT, query TEXT, has_image BOOLEAN)''')
    conn.commit()
    conn.close()

def log_to_db(student_id, subject, query, has_image=False):
    conn = sqlite3.connect('medai_cloud.db')
    c = conn.cursor()
    c.execute("INSERT INTO student_logs (timestamp, student_id, subject, query, has_image) VALUES (?, ?, ?, ?, ?)",
              (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), student_id, subject, query, has_image))
    conn.commit()
    conn.close()

init_db()

# 🎯 更新：视觉大模型上传函数，现在需要动态接收对应的 API Key
def upload_image_to_dify(image_bytes, file_name, current_api_key):
    """先把图片传给对应学科的 Dify 数据库获取专属 ID"""
    url = "https://api.dify.ai/v1/files/upload"
    headers = {"Authorization": f"Bearer {current_api_key}"}
    files = {"file": (file_name, image_bytes, "image/jpeg")}
    data = {"user": st.session_state.user_id}
    try:
        response = requests.post(url, headers=headers, files=files, data=data, timeout=30)
        return response.json().get("id")
    except Exception as e:
        st.error(f"图片上传失败: {e}")
        return None

# ==========================================
# 2. 角色网关 (此时已登录)
# ==========================================
st.sidebar.title("🔐 MedAI 导航")
st.sidebar.success(f"当前在线: **{st.session_state.user_id}**")
if st.sidebar.button("🚪 退出登录"):
    st.session_state.user_id = None
    st.rerun()

st.sidebar.divider()
role = st.sidebar.radio("请选择身份：", ["🧑‍🎓 学生端", "👨‍🏫 教师端", "🏛️ 教研室端"])

# ==========================================
# 3. 🧑‍🎓 学生端：支持多模态（学科+拍照+提问）
# ==========================================
if role == "🧑‍🎓 学生端":
    st.title("📚 医智元：期末复习导航")
    
    # 🎯 动态调用全局学科列表
    subject = st.selectbox("🎯 请选择当前复习科目", SUBJECTS)
    
    # 获取当前选中学科对应的 API KEY
    CURRENT_API_KEY = SUBJECT_CONFIG[subject]
    if CURRENT_API_KEY.startswith("YOUR_"):
        st.warning(f"⚠️ 当前科目【{subject}】尚未配置真实的 API Key，请在代码或 Secrets 中配置。")
    
    # 🎯 更新：状态初始化 (将聊天记录按学科隔离，防止串台)
    chat_history_key = f"chat_history_{subject}"
    if chat_history_key not in st.session_state: 
        st.session_state[chat_history_key] = []
    if "show_pdf" not in st.session_state: st.session_state.show_pdf = False
    if "current_page" not in st.session_state: st.session_state.current_page = 1

    # 渲染历史记录 (读取当前学科的记忆)
    for msg_idx, msg in enumerate(st.session_state[chat_history_key]):
        with st.chat_message(msg["role"]):
            if msg.get("image_msg"):
                st.info("📸 您发送了一张图片")
            st.markdown(msg["content"])
            if msg.get("real_sources"):
                with st.expander("📂 查看引用依据"):
                    for i, s in enumerate(msg["real_sources"]):
                        doc_name = s.get('document_name')
                        st.info(f"🎯 证据来自《{doc_name}》")
                        local_path = os.path.join(get_subject_dir(subject), doc_name)
                        if os.path.exists(local_path) and st.button(f"📖 定位原文", key=f"hist_{subject}_{msg_idx}_{i}"):
                            st.session_state.current_page = find_page_by_text(local_path, s.get('content'))
                            st.session_state.current_pdf = local_path
                            st.session_state.show_pdf = True
                            st.rerun()

    st.markdown("---")
    input_mode = st.radio(
        "选择图片输入方式（可选）：", 
        ["🚫 纯文字提问", "🖼️ 上传本地图片", "📸 开启相机拍照"], 
        horizontal=True
    )

    uploaded_img = None
    camera_img = None

    if input_mode == "🖼️ 上传本地图片":
        uploaded_img = st.file_uploader("请选择设备中的图片", type=["jpg", "jpeg", "png"])
    elif input_mode == "📸 开启相机拍照":
        st.info("💡 浏览器可能会请求摄像头权限，请点击「允许」。")
        camera_img = st.camera_input("对准题目拍照")

    user_query = st.chat_input("询问教材内容或解析上方图片...")
    final_img = camera_img if camera_img else uploaded_img
    
    if user_query or final_img:
        query_text = user_query if user_query else "请根据你们的教材，帮我解答图片中的医学问题。"
        
        # 保存到当前学科的记忆中
        st.session_state[chat_history_key].append({"role": "user", "content": query_text, "image_msg": True if final_img else False})
        with st.chat_message("user"):
            if final_img: st.image(final_img, width=300)
            st.markdown(query_text)

        with st.chat_message("assistant"):
            with st.spinner(f"AI 正在检索【{subject}】教材大脑..."):
                dify_files = []
                if final_img:
                    # 🎯 传入当前学科专属的 API KEY
                    file_id = upload_image_to_dify(final_img.getvalue(), final_img.name if uploaded_img else "camera.jpg", CURRENT_API_KEY)
                    if file_id:
                        dify_files.append({"type": "image", "transfer_method": "local_file", "upload_file_id": file_id})

                # 🎯 使用当前学科专属的 API KEY 发起对话
                headers = {"Authorization": f"Bearer {CURRENT_API_KEY}", "Content-Type": "application/json"}
                payload = {
                    "inputs": {}, 
                    "query": query_text, # 不再需要硬拼接前缀，因为本身就是独立的数据库了
                    "response_mode": "blocking", 
                    "user": st.session_state.user_id,
                    "files": dify_files
                }
                
                try:
                    r = requests.post("https://api.dify.ai/v1/chat-messages", headers=headers, json=payload, timeout=80)
                    res = r.json()
                    answer = res.get("answer", "...")
                    sources = res.get("metadata", {}).get("retriever_resources", [])
                    st.markdown(answer)
                    
                    if sources:
                        with st.expander("📂 教材深度溯源", expanded=True):
                            for i, doc in enumerate(sources):
                                d_name = doc.get("document_name")
                                st.success(f"🎯 《{d_name}》")
                                path = os.path.join(get_subject_dir(subject), d_name)
                                if os.path.exists(path) and st.button(f"📖 定位原文：{d_name}", key=f"new_{subject}_{i}_{datetime.now().timestamp()}"):
                                    st.session_state.current_page = find_page_by_text(path, doc.get("content"))
                                    st.session_state.current_pdf = path
                                    st.session_state.show_pdf = True
                                    st.rerun()
                    
                    st.session_state[chat_history_key].append({
                        "role": "assistant", "content": answer, 
                        "real_sources": sources
                    })
                    log_to_db(st.session_state.user_id, subject, query_text, True if final_img else False)
                    
                except Exception as e:
                    st.error(f"大脑思考超时，请重试: {e}")

    # 全局阅读器
    if st.session_state.show_pdf and st.session_state.get("current_pdf"):
        st.divider()
        if st.button("✖️ 关闭阅读"):
            st.session_state.show_pdf = False
            st.rerun()
        display_pdf_with_page(st.session_state.current_pdf, st.session_state.current_page)

# ==========================================
# 4. 教研室端 / 教师端 (保持不变)
# ==========================================
elif role == "👨‍🏫 教师端":
    st.title("📤 教研室资产管理中心")
    st.markdown("在这里管理您的云端教材库。您上传的文件将直接用于学生端的**【原文精准定位】**。")
    
    target_subject = st.selectbox("📂 选择目标入库学科", SUBJECTS)
    st.subheader(f"当前管理: 【{target_subject}】书库")
    
    current_subject_dir = get_subject_dir(target_subject)
    existing_files = os.listdir(current_subject_dir)
    pdf_files = [f for f in existing_files if f.endswith('.pdf')]
    
    if pdf_files:
        file_data = []
        for f in pdf_files:
            file_path = os.path.join(current_subject_dir, f)
            size_mb = os.path.getsize(file_path) / (1024 * 1024)
            file_data.append({
                "📄 教材名称": f, 
                "💾 大小 (MB)": f"{size_mb:.2f}", 
                "🟢 状态": "已就绪"
            })
        st.dataframe(pd.DataFrame(file_data), use_container_width=True, hide_index=True)
    else:
        st.info(f"📭 当前【{target_subject}】书库为空。")
        
    st.markdown("---")
    st.subheader("🚀 上传新教材 / 课件")
    st.info("💡 提示：为保证 AI 能够检索到，请确保同名 PDF 也已同步至 Dify 后台对应的知识库。")
    files = st.file_uploader("将 PDF 拖拽至此 (支持批量)", type=["pdf"], accept_multiple_files=True)
    
    if st.button("⚡ 确认入库并激活定位", type="primary"):
        if files:
            with st.spinner("正在将教材写入云端存储..."):
                for f in files:
                    save_path = os.path.join(current_subject_dir, f.name)
                    with open(save_path, "wb") as save_f:
                        save_f.write(f.getbuffer())
            st.success(f"✅ 教材已成功存入【{target_subject}】云端书库！")
            st.rerun() 
        else:
            st.warning("⚠️ 别急，您还没选择任何文件呢！")

elif role == "🏛️ 教研室端":
    st.title("📊 监控大屏")
    try:
        conn = sqlite3.connect('medai_cloud.db')
        df = pd.read_sql_query("SELECT timestamp, student_id, subject, query, has_image FROM student_logs ORDER BY timestamp DESC", conn)
        conn.close()
        if not df.empty:
            c1, c2, c3 = st.columns(3)
            c1.metric("累计调用", len(df))
            c2.metric("覆盖学科", df['subject'].nunique())
            c3.metric("拍照提问次数", df['has_image'].sum())
            st.dataframe(df, use_container_width=True)
        else:
            st.info("暂无数据")
    except:
        pass