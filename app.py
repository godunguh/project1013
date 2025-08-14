import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import uuid
import os
import codecs

# --- 상수 및 기본 설정 ---
# ADMIN_PASSWORD는 Streamlit Secrets 또는 로컬 환경 변수에서 가져옵니다.

# --- CSS 스타일 ---
def apply_custom_css():
    st.markdown(r"""
        <style>
            /* 메인 타이틀 */
            .st-emotion-cache-10trblm {
                background-color: #0d6efd;
                color: white;
                padding: 1rem;
                border-radius: 0.5rem;
                text-align: center;
            }
            h1 {
                color: white;
            }
            /* 페이지 부제목 */
            h2 {
                border-bottom: 2px solid #0d6efd;
                padding-bottom: 0.5rem;
                color: #0d6efd;
            }
        </style>
    """, unsafe_allow_html=True)

# --- 상태 관리 함수 ---
def initialize_app_state():
    """앱 세션 상태 초기화"""
    if 'page' not in st.session_state:
        st.session_state.page = "목록"
    if 'selected_problem_id' not in st.session_state:
        st.session_state.selected_problem_id = None
    if 'unlocked_problem_id' not in st.session_state:
        st.session_state.unlocked_problem_id = None
    
    keys_to_init = ['creator', 'title', 'question', 'opt1', 'opt2', 'opt3', 'opt4', 'password', 'category', 'answer']
    for key in keys_to_init:
        if key not in st.session_state:
            st.session_state[key] = "" if key not in ['category', 'answer'] else None

# --- 구글 시트 및 데이터 처리 함수 ---
@st.cache_resource
def connect_to_sheet():
    """Streamlit Cloud 또는 로컬 환경에 따라 구글 시트에 연결합니다."""
    try:
        # Streamlit Cloud에 배포된 경우, st.secrets에서 직접 자격 증명 생성
        creds_json = {
            "type": st.secrets["gcp_service_account"]["type"],
            "project_id": st.secrets["gcp_service_account"]["project_id"],
            "private_key_id": st.secrets["gcp_service_account"]["private_key_id"],
            "private_key": codecs.decode(st.secrets["gcp_service_account"]["private_key"], "unicode_escape"),
            "client_email": st.secrets["gcp_service_account"]["client_email"],
            "client_id": st.secrets["gcp_service_account"]["client_id"],
            "auth_uri": st.secrets["gcp_service_account"]["auth_uri"],
            "token_uri": st.secrets["gcp_service_account"]["token_uri"],
            "auth_provider_x509_cert_url": st.secrets["gcp_service_account"]["auth_provider_x509_cert_url"],
            "client_x509_cert_url": st.secrets["gcp_service_account"]["client_x509_cert_url"],
            "universe_domain": "googleapis.com"
        }
        creds = Credentials.from_service_account_info(creds_json, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
        return gspread.authorize(creds)
    except (AttributeError, KeyError):
        # 로컬 환경에서 실행되는 경우, credentials.json 파일 사용
        script_dir = os.path.dirname(os.path.abspath(__file__))
        credentials_path = os.path.join(script_dir, "credentials.json")
        if os.path.exists(credentials_path):
            creds = Credentials.from_service_account_file(credentials_path, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
            return gspread.authorize(creds)
        else:
            st.error("🚨 구글 시트 연결 정보를 찾을 수 없습니다. 로컬에서는 credentials.json 파일이, Cloud에서는 Secrets 설정이 필요합니다."); st.stop()


@st.cache_resource
def get_sheet(_client, sheet_name="문제 목록"):
    """워크시트 객체를 가져오고 리소스로 캐시합니다."""
    try:
        spreadsheet = _client.open("MyQuizApp")
        worksheet = spreadsheet.worksheet(sheet_name)
        # 헤더가 존재하는지 확인하고, 없으면 추가합니다. 이 작업은 캐시 덕분에 한 번만 실행됩니다.
        headers = worksheet.row_values(1)
        if not headers or "title" not in headers:
             worksheet.insert_cols([["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password"]], col=1)
        return worksheet
    except gspread.WorksheetNotFound:
        spreadsheet = _client.open("MyQuizApp")
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        headers = ["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password"]
        worksheet.append_row(headers)
        return worksheet

@st.cache_data(ttl=600)
def load_data(_worksheet):
    """Google Sheet에서 데이터를 로드하고 10분 동안 캐시합니다."""
    records = _worksheet.get_all_records()
    headers = ["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password"]
    if not records:
        return pd.DataFrame(columns=headers)
    
    df = pd.DataFrame(records)
    # 모든 헤더가 있는지 확인
    for col in headers:
        if col not in df.columns:
            df[col] = ""
    return df

def save_problem(worksheet, data):
    """새로운 문제를 시트에 저장하고 캐시를 지웁니다."""
    headers = ["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password"]
    worksheet.append_row([data.get(h, "") for h in headers])
    load_data.clear()

def delete_problem(worksheet, problem_id):
    """시트에서 문제를 삭제하고 캐시를 지웁니다."""
    cell = worksheet.find(problem_id)
    if cell:
        worksheet.delete_rows(cell.row)
        load_data.clear()

def update_problem(worksheet, problem_id, data):
    """시트에서 문제를 업데이트하고 캐시를 지웁니다."""
    cell = worksheet.find(problem_id)
    if cell:
        headers = ["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password"]
        worksheet.update(f'A{cell.row}', [[data.get(h, "") for h in headers]])
        load_data.clear()

# --- UI 렌더링 함수 ---
def render_problem_list(problem_df):
    st.header("🔎 전체 문제 목록")
    if st.button("✍️ 새로운 문제 만들기"):
        st.session_state.page = "만들기"; st.rerun()

    search_query = st.text_input("🔎 문제 검색", placeholder="제목 또는 내용으로 검색하세요.")
    categories = ["전체"] + sorted(problem_df["category"].unique().tolist())
    selected_category = st.selectbox("📚 분류별로 보기:", categories)

    df = problem_df
    if search_query: df = df[df['title'].str.contains(search_query, na=False) | df['question'].str.contains(search_query, na=False)]
    if selected_category != "전체": df = df[df["category"] == selected_category]

    st.divider()
    if df.empty: st.info("표시할 문제가 없습니다.")
    else:
        for index, row in df.iterrows():
            if st.button(f"[{row['category']}] | {row['title']} = {row['creator']}", key=f"view_{row['id']}", use_container_width=True):
                st.session_state.selected_problem_id = row['id']
                st.session_state.page = "상세"
                st.rerun()

def render_problem_detail(problem, worksheet):
    if st.button("⬅️ 목록으로 돌아가기"):
        st.session_state.page = "목록"; st.rerun()

    st.header(f"{problem['title']}")
    st.caption(f"출제자: {problem['creator']} | 분류: {problem['category']}")
    st.markdown(f"**문제 내용:**\n\n{problem['question']}")

    options = [problem.get(f"option{i}") for i in range(1, 5) if problem.get(f"option{i}")]
    if options:
        user_answer = st.radio("정답을 선택하세요:", options, index=None)
        if st.button("정답 확인"):
            if user_answer == problem["answer"]:
                st.success("정답입니다! 👍")
            else:
                st.error("틀렸습니다. 다시 시도해보세요. 👎")

    st.divider()
    st.subheader("🔒 문제 관리")
    
    if st.session_state.get('unlocked_problem_id') == problem['id']:
        with st.expander("✏️ 문제 수정하기", expanded=True):
            edited_title = st.text_input("제목 수정", value=problem['title'])
            edited_question = st.text_area("내용 수정", value=problem['question'])
            edited_options = [st.text_input(f"선택지 {i+1} 수정", value=problem.get(f'option{i+1}', '')) for i in range(4)]
            valid_edited_options = [opt for opt in edited_options if opt]
            current_answer_index = valid_edited_options.index(problem['answer']) if problem['answer'] in valid_edited_options else 0
            edited_answer = st.selectbox("정답 수정", valid_edited_options, index=current_answer_index)

            if st.button("변경사항 저장"):
                updated_data = problem.copy()
                updated_data.update({"title": edited_title, "question": edited_question, "answer": edited_answer,
                                     "option1": edited_options[0], "option2": edited_options[1], 
                                     "option3": edited_options[2], "option4": edited_options[3]})
                update_problem(worksheet, problem['id'], updated_data)
                st.success("문제가 업데이트되었습니다."); st.rerun()
        
        if st.button("🗑️ 문제 삭제하기", type="primary"):
            delete_problem(worksheet, problem['id'])
            st.success("문제가 삭제되었습니다."); st.session_state.page = "목록"; st.rerun()
    else:
        password_input = st.text_input("문제 관리를 위해 비밀번호를 입력하세요.", type="password")
        if st.button("인증하기"):
            # Secrets에서 관리자 비밀번호 가져오기. 없으면 None.
            ADMIN_PASSWORD = st.secrets.get("general", {}).get("admin_password")
            if password_input == str(problem.get('password', '')) or (ADMIN_PASSWORD and password_input == ADMIN_PASSWORD):
                st.session_state.unlocked_problem_id = problem['id']; st.success("인증되었습니다."); st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")

def render_creation_form(worksheet):
    st.header("✍️ 새로운 문제 만들기")
    if st.button("⬅️ 목록으로 돌아가기"):
        st.session_state.page = "목록"; st.rerun()

    title = st.text_input("📝 문제 제목", key="title")
    creator = st.text_input("👤 출제자 이름", key="creator")
    category = st.selectbox("📚 분류", ["수학2", "확률과 통계", "독서", "영어"], index=None, key="category")
    password = st.text_input("🔒 비밀번호 설정", type="password", key="password")
    question = st.text_area("❓ 문제 내용", key="question")
    options = [st.text_input(f"선택지 {i+1}", key=f"opt{i+1}") for i in range(4)]
    answer = st.selectbox("✅ 정답 선택", [opt for opt in options if opt], index=None, key="answer")

    if st.button("문제 제출하기"):
        if not all([title, creator, category, password, question, answer]) or not all(options):
            st.warning("모든 필드를 채워주세요!")
        else:
            new_problem = {"id": str(uuid.uuid4()), "title": title, "category": category, "question": question,
                           "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                           "answer": answer, "creator": creator, "password": password}
            save_problem(worksheet, new_problem)
            st.success("🎉 문제가 성공적으로 만들어졌습니다!"); st.session_state.page = "목록"; st.rerun()

# --- 메인 앱 로직 ---
st.set_page_config(page_title="모두의 문제 게시판", layout="wide")
apply_custom_css()
st.title("📝 모두의 문제 게시판")

initialize_app_state()
client = connect_to_sheet()
worksheet = get_sheet(client)
problem_df = load_data(worksheet)

if st.session_state.page == "목록":
    render_problem_list(problem_df)
elif st.session_state.page == "상세":
    problem = problem_df[problem_df['id'] == st.session_state.selected_problem_id].iloc[0].to_dict()
    render_problem_detail(problem, worksheet)
elif st.session_state.page == "만들기":
    render_creation_form(worksheet)