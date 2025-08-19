import streamlit as st
import gspread
from google.oauth2.service_account import Credentials
import pandas as pd
import uuid
import os
import json
import streamlit.components.v1 as components
from io import BytesIO
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from googleapiclient.errors import HttpError
from streamlit_oauth import OAuth2Component
from datetime import datetime

# --- 상수 및 기본 설정 ---
# !!! 중요 !!!: 관리자 대시보드에 접근할 수 있는 Google 계정 이메일을 여기에 입력하세요. 
ADMIN_EMAIL = "jwj1013kor@gmail.com"

# Google Sheets 헤더 정의
PROBLEM_HEADERS = [
    "id", "title", "category", "question", "option1", "option2", "option3", "option4", 
    "answer", "creator_name", "creator_email", "explanation", "question_image_id", 
    "explanation_image_id", "question_type", "created_at"
]
SOLUTION_HEADERS = ["problem_id", "user_email", "user_name", "solved_at"]
DRIVE_FOLDER_NAME = "MyQuizApp Images"

# OAuth2 설정 (secrets.toml 파일 사용)
CLIENT_ID = st.secrets.get("oauth_credentials", {}).get("CLIENT_ID")
CLIENT_SECRET = st.secrets.get("oauth_credentials", {}).get("CLIENT_SECRET")
REDIRECT_URI = st.secrets.get("oauth_credentials", {}).get("REDIRECT_URI", "http://localhost:8501")
AUTHORIZE_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

# --- CSS 스타일 ---
def apply_custom_css():
    st.markdown(r"""
        <style>
            /* 메인 타이틀 */
            .st-emotion-cache-10trblm { background-color: #0d6efd; color: white; padding: 1rem;
                border-radius: 0.5rem; text-align: center;
            }
            h1 { color: white; font-size: 2.2rem; }
            h2 { border-bottom: 2px solid #0d6efd; padding-bottom: 0.5rem; color: #0d6efd; }
            @media (max-width: 768px) { h1 { font-size: 1.8rem; } }
        </style>
    """, unsafe_allow_html=True)

# --- 상태 관리 함수 ---
def initialize_app_state():
    """앱 세션 상태 초기화"""
    if 'page' not in st.session_state: st.session_state.page = "목록"
    if 'selected_problem_id' not in st.session_state: st.session_state.selected_problem_id = None
    if 'token' not in st.session_state: st.session_state.token = None
    if 'user_info' not in st.session_state: st.session_state.user_info = None

# --- 구글 API 연결 함수 ---
@st.cache_resource
def get_google_creds():
    """Google 서비스 계정 인증 정보를 로드합니다."""
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # credentials.json 파일 경로를 직접 지정하여 로드합니다.
    creds_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
    
    if os.path.exists(creds_path):
        return Credentials.from_service_account_file(creds_path, scopes=scopes)
    
    # 파일이 없을 경우 명확한 에러 메시지 출력
    st.error("🚨 `credentials.json` 파일을 찾을 수 없습니다. 프로젝트 폴더에 파일이 있는지 확인해주세요.")
    st.stop()

@st.cache_resource
def get_gspread_client(_creds): return gspread.authorize(_creds)

@st.cache_resource
def get_drive_service(_creds): return build('drive', 'v3', credentials=_creds)

# --- 구글 드라이브 처리 함수 ---
@st.cache_resource
def get_or_create_drive_folder(_drive_service, folder_name):
    try:
        q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = _drive_service.files().list(q=q, spaces='drive', fields='files(id, name)').execute()
        if files := response.get('files', []): return files[0].get('id')
        
        folder_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
        folder = _drive_service.files().create(body=folder_metadata, fields='id').execute()
        return folder.get('id')
    except HttpError as e:
        st.error(f"Google Drive 폴더 오류: {e}"); return None

def upload_image_to_drive(_drive_service, folder_id, image_file):
    try:
        file_metadata = {'name': f"{uuid.uuid4().hex}.png", 'parents': [folder_id]}
        media = MediaIoBaseUpload(BytesIO(image_file.getvalue()), mimetype='image/png', resumable=True)
        file = _drive_service.files().create(body=file_metadata, media_body=media, fields='id').execute()
        _drive_service.permissions().create(fileId=file.get('id'), body={'type': 'anyone', 'role': 'reader'}).execute()
        return file.get('id')
    except HttpError as e:
        st.error(f"이미지 업로드 오류: {e}"); return None

def delete_file_from_drive(_drive_service, file_id):
    if not file_id or not isinstance(file_id, str): return
    try:
        _drive_service.files().delete(fileId=file_id).execute()
    except HttpError as e:
        if e.resp.status != 404: st.warning(f"Drive 파일 삭제 오류 (ID: {file_id}): {e}")

# --- 구글 시트 처리 함수 ---
def get_sheet(_client, sheet_name, headers):
    try:
        spreadsheet = _client.open("MyQuizApp")
        worksheet = spreadsheet.worksheet(sheet_name)
    except gspread.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols=len(headers))
        worksheet.append_row(headers)
    else:
        # 헤더가 비어있거나 다를 경우 업데이트
        current_headers = worksheet.row_values(1)
        if not current_headers or not all(h in current_headers for h in headers):
            worksheet.update('A1', [headers])
    return worksheet

@st.cache_data(ttl=300)
def load_data(_worksheet, headers):
    records = _worksheet.get_all_records()
    if not records: return pd.DataFrame(columns=headers)
    
    df = pd.DataFrame(records)
    for col in headers:
        if col not in df.columns: df[col] = ""
    return df

def save_data(worksheet, data, headers):
    worksheet.append_row([data.get(h, "") for h in headers])
    # 데이터 로드 캐시 클리어
    st.cache_data.clear()

def delete_problem(problem_sheet, drive_service, problem):
    delete_file_from_drive(drive_service, problem.get('question_image_id'))
    delete_file_from_drive(drive_service, problem.get('explanation_image_id'))
    if cell := problem_sheet.find(problem['id']):
        problem_sheet.delete_rows(cell.row)
        st.cache_data.clear()

# --- UI 렌더링 함수 ---
def render_sidebar(user_info):
    with st.sidebar:
        # user_info가 없거나 비어있는 경우를 대비한 방어 코드
        if not user_info or not isinstance(user_info, dict):
            st.warning("로그인 정보가 없습니다.")
            return

        st.header(f"👋 {user_info.get('name', '사용자')}님")
        st.write(f"_{user_info.get('email', '')}_")
        st.divider()
        
        # 인자로 받은 user_info를 일관되게 사용
        if user_info.get('email') == ADMIN_EMAIL:
            if st.button("📊 관리자 대시보드", use_container_width=True):
                st.session_state.page = "대시보드"; st.rerun()
        
        if st.button("📝 문제 목록", use_container_width=True):
            st.session_state.page = "목록"; st.rerun()
        
        if st.button("✍️ 새로운 문제 만들기", use_container_width=True):
            st.session_state.page = "만들기"; st.rerun()

def render_problem_list(problem_df):
    st.header("🔎 전체 문제 목록")
    search_query = st.text_input("🔎 문제 검색", placeholder="제목 또는 내용으로 검색하세요.")
    categories = ["전체"] + sorted(problem_df["category"].unique().tolist())
    selected_category = st.selectbox("📚 분류별로 보기:", categories)

    df = problem_df
    if search_query: df = df[df['title'].str.contains(search_query, na=False) | df['question'].str.contains(search_query, na=False)]
    if selected_category != "전체": df = df[df["category"] == selected_category]

    st.divider()
    if df.empty: st.info("표시할 문제가 없습니다.")
    else:
        for _, row in df.iterrows():
            if st.button(f"[{row['category']}] | {row['title']} - {row['creator_name']}", key=f"view_{row['id']}", use_container_width=True):
                st.session_state.selected_problem_id = row['id']
                st.session_state.page = "상세"; st.rerun()

def render_problem_detail(problem, sheets, drive_service, user_info):
    problem_id = problem['id']
    problem_type = '주관식' if problem.get('question_type') == '주관식' else '객관식'

    st.header(f"{problem['title']}")
    st.caption(f"출제자: {problem['creator_name']} | 분류: {problem['category']} | 유형: {problem_type}")
    st.markdown(f"**문제 내용:**\n\n{problem['question']}")
    if problem.get('question_image_id'):
        st.image(f"https://drive.google.com/uc?id={problem['question_image_id']}")

    user_answer = st.radio("정답:", [problem.get(f"option{i}") for i in range(1, 5) if problem.get(f"option{i}")], index=None) if problem_type == '객관식' else st.text_input("정답:")

    if st.button("정답 확인"):
        is_correct = str(user_answer).strip() == str(problem["answer"]).strip()
        if is_correct:
            st.success("정답입니다! 👍")
            st.session_state[f"show_explanation_{problem_id}"] = True
            # 풀이 기록 저장
            solution_data = {
                "problem_id": problem_id, "user_email": user_info['email'],
                "user_name": user_info['name'], "solved_at": datetime.now().isoformat()
            }
            save_data(sheets['solutions'], solution_data, SOLUTION_HEADERS)
        else:
            st.error("틀렸습니다. 다시 시도해보세요. 👎")
            st.session_state[f"show_explanation_{problem_id}"] = False
    
    if st.session_state.get(f"show_explanation_{problem_id}") and problem.get('explanation'):
        st.info(f"**해설:**\n\n{problem['explanation']}")
        if problem.get('explanation_image_id'):
            st.image(f"https://drive.google.com/uc?id={problem['explanation_image_id']}")

    # 문제 관리 (수정/삭제)는 출제자 본인 또는 관리자만 가능
    if user_info['email'] == problem.get('creator_email') or user_info['email'] == ADMIN_EMAIL:
        st.divider()
        st.subheader("🔒 문제 관리")
        if st.button("🗑️ 문제 삭제하기", type="secondary"):
            delete_problem(sheets['problems'], drive_service, problem)
            st.success("문제가 삭제되었습니다."); st.session_state.page = "목록"; st.rerun()

def render_creation_form(worksheet, drive_service, user_info):
    st.header("✍️ 새로운 문제 만들기")
    with st.form("creation_form"):
        title = st.text_input("📝 문제 제목")
        category = st.selectbox("📚 분류", ["수학2", "확률과 통계", "독서", "영어", "물리학1", "화학1", "생명과학1", "지구과학1", "사회문화", "윤리와사상", "기타"], index=None)
        question_type = st.radio("📋 문제 유형", ('객관식', '주관식'))
        question = st.text_area("❓ 문제 내용")
        question_image = st.file_uploader("🖼️ 문제 이미지 추가", type=['png', 'jpg', 'jpeg'])
        explanation = st.text_area("📝 문제 풀이/해설")
        explanation_image = st.file_uploader("🖼️ 해설 이미지 추가", type=['png', 'jpg', 'jpeg'])

        if question_type == '객관식':
            options = [st.text_input(f"선택지 {i+1}") for i in range(4)]
            answer = st.selectbox("✅ 정답 선택", [opt for opt in options if opt], index=None)
        else:
            options = ["", "", "", ""]
            answer = st.text_input("✅ 정답 입력")
        
        submitted = st.form_submit_button("문제 제출하기", type="primary")

    if submitted:
        is_valid = all([title, category, question, answer]) and (all(options) if question_type == '객관식' else True)
        if not is_valid:
            st.warning("이미지를 제외한 모든 필수 필드를 채워주세요!")
        else:
            with st.spinner('처리 중...'):
                folder_id = get_or_create_drive_folder(drive_service, DRIVE_FOLDER_NAME)
                if not folder_id: st.error("Drive 폴더 오류"); return

                q_img_id = upload_image_to_drive(drive_service, folder_id, question_image) if question_image else ""
                e_img_id = upload_image_to_drive(drive_service, folder_id, explanation_image) if explanation_image else ""

                new_problem = {
                    "id": str(uuid.uuid4()), "title": title, "category": category, "question": question,
                    "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                    "answer": answer, "creator_name": user_info['name'], "creator_email": user_info['email'],
                    "explanation": explanation, "question_type": question_type,
                    "question_image_id": q_img_id, "explanation_image_id": e_img_id,
                    "created_at": datetime.now().isoformat()
                }
                save_data(worksheet, new_problem, PROBLEM_HEADERS)
                st.success("🎉 문제가 성공적으로 만들어졌습니다!"); st.session_state.page = "목록"; st.rerun()

def render_dashboard(problem_df, solution_df):
    st.header("📊 관리자 대시보드")
    st.write("사용자 활동 및 문제 통계를 확인합니다.")

    if problem_df.empty and solution_df.empty:
        st.info("아직 데이터가 없습니다.")
        return

    tab1, tab2, tab3 = st.tabs(["사용자별 통계", "문제별 통계", "전체 데이터"])

    with tab1:
        st.subheader("👤 사용자별 문제 생성 수")
        if not problem_df.empty:
            creation_stats = problem_df['creator_name'].value_counts().reset_index()
            creation_stats.columns = ['출제자', '생성한 문제 수']
            st.dataframe(creation_stats, use_container_width=True)
            st.bar_chart(creation_stats.set_index('출제자'))
        else:
            st.write("생성된 문제가 없습니다.")

        st.subheader("✅ 사용자별 문제 풀이 수")
        if not solution_df.empty:
            solution_stats = solution_df['user_name'].value_counts().reset_index()
            solution_stats.columns = ['사용자', '해결한 문제 수']
            st.dataframe(solution_stats, use_container_width=True)
            st.bar_chart(solution_stats.set_index('사용자'))
        else:
            st.write("풀이 기록이 없습니다.")

    with tab2:
        st.subheader("📈 가장 많이 푼 문제 Top 5")
        if not solution_df.empty and not problem_df.empty:
            solved_counts = solution_df['problem_id'].value_counts().reset_index()
            solved_counts.columns = ['id', 'solved_count']
            
            # 문제 제목 정보 병합
            problem_titles = problem_df[['id', 'title']]
            merged_stats = pd.merge(solved_counts, problem_titles, on='id', how='left')
            merged_stats = merged_stats[['title', 'solved_count']].head(5)
            merged_stats.columns = ['문제 제목', '풀이 횟수']

            st.dataframe(merged_stats, use_container_width=True)
            st.bar_chart(merged_stats.set_index('문제 제목'))
        else:
            st.write("풀이 기록이 없습니다.")

    with tab3:
        st.subheader("📚 전체 문제 데이터")
        st.dataframe(problem_df)
        st.subheader("📝 전체 풀이 기록")
        st.dataframe(solution_df)

# --- 메인 앱 로직 ---
def main():
    st.set_page_config(page_title="2학년 문제 공유 게시판", layout="wide")
    apply_custom_css()
    st.title("📝 2학년 문제 공유 게시판")

    initialize_app_state()

    if not all([CLIENT_ID, CLIENT_SECRET]):
        st.error("OAuth2.0 클라이언트 ID와 시크릿이 secrets.toml 파일에 설정되지 않았습니다.")
        st.stop()

    oauth2 = OAuth2Component(CLIENT_ID, CLIENT_SECRET, AUTHORIZE_ENDPOINT, TOKEN_ENDPOINT, TOKEN_ENDPOINT, REVOKE_ENDPOINT)

    if 'token' not in st.session_state or st.session_state.token is None:
        result = oauth2.authorize_button(
            name="구글 계정으로 로그인",
            icon="https://www.google.com/favicon.ico",
            redirect_uri=REDIRECT_URI,
            scope="openid email profile",
            key="google_login",
            use_container_width=True,
        )
        if result and "token" in result:
            st.session_state.token = result.get("token")
            # token 객체 자체를 user_info로 사용하여 안정성 확보
            st.session_state.user_info = result.get("token")
            st.rerun()
    else:
        # --- 로그인 후 앱 로직 ---
        user_info = st.session_state.get("user_info")

        if not user_info:
            st.error("사용자 정보를 가져오는 데 실패했습니다. 다시 로그인해주세요.")
            if st.button("로그인 페이지로 돌아가기"):
                st.session_state.token = None
                st.session_state.user_info = None
                st.rerun()
            st.stop()

        render_sidebar(user_info)
        
        if st.sidebar.button("로그아웃", use_container_width=True, type="secondary"):
            st.session_state.token = None
            st.session_state.user_info = None
            st.rerun()

        creds = get_google_creds()
        gspread_client = get_gspread_client(creds)
        drive_service = get_drive_service(creds)
        
        sheets = {
            'problems': get_sheet(gspread_client, "문제 목록", PROBLEM_HEADERS),
            'solutions': get_sheet(gspread_client, "풀이 기록", SOLUTION_HEADERS)
        }
        
        problem_df = load_data(sheets['problems'], PROBLEM_HEADERS)
        solution_df = load_data(sheets['solutions'], SOLUTION_HEADERS)

        if st.session_state.page == "목록":
            render_problem_list(problem_df)
        elif st.session_state.page == "상세":
            problem_df_filtered = problem_df[problem_df['id'] == st.session_state.selected_problem_id]
            if not problem_df_filtered.empty:
                problem = problem_df_filtered.iloc[0].to_dict()
                render_problem_detail(problem, sheets, drive_service, user_info)
            else:
                st.error("문제를 찾을 수 없습니다."); st.session_state.page = "목록"; st.rerun()
        elif st.session_state.page == "만들기":
            render_creation_form(sheets['problems'], drive_service, user_info)
        elif st.session_state.page == "대시보드" and user_info.get('email') == ADMIN_EMAIL:
            render_dashboard(problem_df, solution_df)
        else:
            # 기본 페이지로 이동
            st.session_state.page = "목록"; st.rerun()

if __name__ == "__main__":
    main()
