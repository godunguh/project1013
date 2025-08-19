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

# --- 상수 및 기본 설정 ---
HEADERS = ["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password", "explanation", "question_image_id", "explanation_image_id", "question_type"]
DRIVE_FOLDER_NAME = "MyQuizApp Images"

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
                font-size: 2.2rem; /* 기본 글씨 크기 조정 */
            }
            /* 페이지 부제목 */
            h2 {
                border-bottom: 2px solid #0d6efd;
                padding-bottom: 0.5rem;
                color: #0d6efd;
            }
            
            /* 모바일 화면 대응 */
            @media (max-width: 768px) {
                h1 {
                    font-size: 1.8rem; /* 모바일에서 글씨 크기 더 줄이기 */
                }
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
    
    keys_to_init = ['creator', 'title', 'question', 'opt1', 'opt2', 'opt3', 'opt4', 'password', 'category', 'answer', 'explanation', 'question_type']
    for key in keys_to_init:
        if key not in st.session_state:
            st.session_state[key] = "" if key not in ['category', 'answer', 'question_type'] else None

# --- 구글 API 연결 함수 ---
@st.cache_resource
def get_google_creds():
    """다양한 환경에 맞춰 구글 인증 정보를 로드합니다."""
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    
    # 1. Render 또는 다른 환경 변수 기반 플랫폼
    if "GCP_CREDS_JSON" in os.environ:
        creds_json_str = os.environ["GCP_CREDS_JSON"]
        creds_json = json.loads(creds_json_str)
        return Credentials.from_service_account_info(creds_json, scopes=scopes)
    
    # 2. Streamlit Cloud Secrets
    if "gcp_service_account" in st.secrets:
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scopes)

    # 3. 로컬 credentials.json 파일
    script_dir = os.path.dirname(os.path.abspath(__file__))
    credentials_path = os.path.join(script_dir, "credentials.json")
    if os.path.exists(credentials_path):
        return Credentials.from_service_account_file(credentials_path, scopes=scopes)
    
    # 모든 방법 실패 시
    st.error("🚨 구글 API 연결 정보를 찾을 수 없습니다. 환경에 맞는 설정이 필요합니다.")
    st.stop()

@st.cache_resource
def get_gspread_client(_creds):
    return gspread.authorize(_creds)

@st.cache_resource
def get_drive_service(_creds):
    return build('drive', 'v3', credentials=_creds)

# --- 구글 드라이브 처리 함수 ---
@st.cache_resource
def get_or_create_drive_folder(_drive_service, folder_name):
    """지정된 이름의 폴더를 찾거나 생성하고 ID를 반환합니다."""
    try:
        q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        response = _drive_service.files().list(q=q, spaces='drive', fields='files(id, name)').execute()
        files = response.get('files', [])
        if files:
            return files[0].get('id')
        else:
            file_metadata = {'name': folder_name, 'mimeType': 'application/vnd.google-apps.folder'}
            folder = _drive_service.files().create(body=file_metadata, fields='id').execute()
            return folder.get('id')
    except HttpError as error:
        st.error(f"Google Drive 폴더 생성/조회 중 오류 발생: {error}")
        return None

def upload_image_to_drive(_drive_service, folder_id, image_file):
    """이미지를 Google Drive에 업로드하고 파일 ID를 반환합니다."""
    try:
        file_metadata = {'name': f"{uuid.uuid4().hex}.png", 'parents': [folder_id]}
        media = MediaIoBaseUpload(BytesIO(image_file.getvalue()), mimetype='image/png', resumable=True)
        file = _drive_service.files().create(body=file_metadata, media_body=media, fields='id, webContentLink').execute()
        
        # 파일 권한을 '누구나 볼 수 있게'로 설정
        permission = {'type': 'anyone', 'role': 'reader'}
        _drive_service.permissions().create(fileId=file.get('id'), body=permission).execute()
        
        return file.get('id')
    except HttpError as error:
        st.error(f"이미지 업로드 중 오류 발생: {error}")
        return None

def delete_file_from_drive(_drive_service, file_id):
    """Google Drive에서 파일을 삭제합니다."""
    if not file_id or not isinstance(file_id, str): return
    try:
        _drive_service.files().delete(fileId=file_id).execute()
    except HttpError as error:
        # 파일이 이미 삭제되었거나 찾을 수 없는 경우(404)는 무시
        if error.resp.status == 404:
            print(f"File with ID {file_id} not found. Already deleted.")
        else:
            st.warning(f"Drive 파일 삭제 중 오류 발생 (ID: {file_id}): {error}")


# --- 구글 시트 처리 함수 ---
@st.cache_resource
def get_sheet(_client, sheet_name="문제 목록"):
    """워크시트 객체를 가져오고 리소스로 캐시합니다."""
    try:
        spreadsheet = _client.open("MyQuizApp")
        worksheet = spreadsheet.worksheet(sheet_name)
        current_headers = worksheet.row_values(1)
        if not all(header in current_headers for header in HEADERS):
             worksheet.update('A1', [HEADERS])
        return worksheet
    except gspread.WorksheetNotFound:
        spreadsheet = _client.open("MyQuizApp")
        worksheet = spreadsheet.add_worksheet(title=sheet_name, rows="100", cols="20")
        worksheet.append_row(HEADERS)
        return worksheet

@st.cache_data(ttl=300)
def load_data(_worksheet):
    """Google Sheet에서 데이터를 로드하고 5분 동안 캐시합니다."""
    records = _worksheet.get_all_records()
    if not records:
        return pd.DataFrame(columns=HEADERS)
    
    df = pd.DataFrame(records)
    for col in HEADERS:
        if col not in df.columns:
            df[col] = ""
    return df

def save_problem(worksheet, data):
    worksheet.append_row([data.get(h, "") for h in HEADERS])
    load_data.clear()

def delete_problem(worksheet, drive_service, problem):
    delete_file_from_drive(drive_service, problem.get('question_image_id'))
    delete_file_from_drive(drive_service, problem.get('explanation_image_id'))
    cell = worksheet.find(problem['id'])
    if cell:
        worksheet.delete_rows(cell.row)
        load_data.clear()

def update_problem(worksheet, drive_service, old_problem, new_data):
    if new_data.get('question_image_id') != old_problem.get('question_image_id'):
        delete_file_from_drive(drive_service, old_problem.get('question_image_id'))
    if new_data.get('explanation_image_id') != old_problem.get('explanation_image_id'):
        delete_file_from_drive(drive_service, old_problem.get('explanation_image_id'))
    
    cell = worksheet.find(old_problem['id'])
    if cell:
        worksheet.update(f'A{cell.row}', [[new_data.get(h, "") for h in HEADERS]])
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
            if st.button(f"[{row['category']}] | {row['title']} - {row['creator']}", key=f"view_{row['id']}", use_container_width=True):
                st.session_state.selected_problem_id = row['id']
                st.session_state.page = "상세"
                st.rerun()

def render_problem_detail(problem, worksheet, drive_service):
    problem_id = problem['id']
    problem_type = '주관식' if problem.get('question_type') == '주관식' else '객관식' # 값이 없거나 '주관식'이 아니면 '객관식'으로 처리

    if f"show_explanation_{problem_id}" not in st.session_state:
        st.session_state[f"show_explanation_{problem_id}"] = False

    if st.button("⬅️ 목록으로 돌아가기"):
        st.session_state.page = "목록"; st.rerun()

    st.header(f"{problem['title']}")
    st.caption(f"출제자: {problem['creator']} | 분류: {problem['category']} | 유형: {problem_type}")
    st.markdown(f"**문제 내용:**\n\n{problem['question']}")
    if problem.get('question_image_id'):
        st.image(f"https://drive.google.com/uc?id={problem['question_image_id']}")

    user_answer = None
    if problem_type == '객관식':
        options = [problem.get(f"option{i}") for i in range(1, 5) if problem.get(f"option{i}")]
        if options:
            user_answer = st.radio("정답을 선택하세요:", options, index=None)
    else: # 주관식
        user_answer = st.text_input("정답을 입력하세요:")

    if st.button("정답 확인"):
        # 주관식 답은 양쪽 공백 제거 후 비교
        correct_answer = str(problem["answer"]).strip()
        user_input = str(user_answer).strip()

        if user_input == correct_answer:
            st.success("정답입니다! 👍")
            st.session_state[f"show_explanation_{problem_id}"] = True
        else:
            st.error("틀렸습니다. 다시 시도해보세요. 👎")
            st.session_state[f"show_explanation_{problem_id}"] = False
    
    if st.session_state[f"show_explanation_{problem_id}"] and problem.get('explanation'):
        st.info(f"**해설:**\n\n{problem['explanation']}")
        if problem.get('explanation_image_id'):
            st.image(f"https://drive.google.com/uc?id={problem['explanation_image_id']}")

    st.divider()
    st.subheader("🔒 문제 관리")
    
    if st.session_state.get('unlocked_problem_id') == problem_id:
        with st.expander("✏️ 문제 수정하기", expanded=True):
            updated_data = problem.copy()
            updated_data['title'] = st.text_input("제목 수정", value=problem['title'])
            updated_data['question'] = st.text_area("내용 수정", value=problem['question'])
            
            if problem.get('question_image_id'):
                st.write("현재 문제 이미지:")
                st.image(f"https://drive.google.com/uc?id={problem['question_image_id']}")
            if st.checkbox("문제 이미지 변경/삭제", key="q_img_del"):
                new_question_image = st.file_uploader("새 문제 이미지 업로드 (기존 이미지 대체)", type=['png', 'jpg', 'jpeg'])
                if new_question_image:
                    folder_id = get_or_create_drive_folder(drive_service, DRIVE_FOLDER_NAME)
                    updated_data['question_image_id'] = upload_image_to_drive(drive_service, folder_id, new_question_image)
                else:
                    updated_data['question_image_id'] = ""

            updated_data['explanation'] = st.text_area("해설 수정", value=str(problem.get('explanation', '')))
            
            if problem.get('explanation_image_id'):
                st.write("현재 해설 이미지:")
                st.image(f"https://drive.google.com/uc?id={problem['explanation_image_id']}")
            if st.checkbox("해설 이미지 변경/삭제", key="e_img_del"):
                new_explanation_image = st.file_uploader("새 해설 이미지 업로드 (기존 이미지 대체)", type=['png', 'jpg', 'jpeg'])
                if new_explanation_image:
                    folder_id = get_or_create_drive_folder(drive_service, DRIVE_FOLDER_NAME)
                    updated_data['explanation_image_id'] = upload_image_to_drive(drive_service, folder_id, new_explanation_image)
                else:
                    updated_data['explanation_image_id'] = ""

            if problem_type == '객관식':
                edited_options = [st.text_input(f"선택지 {i+1} 수정", value=problem.get(f'option{i+1}', '')) for i in range(4)]
                valid_edited_options = [opt for opt in edited_options if opt]
                current_answer_index = valid_edited_options.index(problem['answer']) if problem['answer'] in valid_edited_options else 0
                updated_data['answer'] = st.selectbox("정답 수정", valid_edited_options, index=current_answer_index)
                updated_data.update({f"option{i+1}": opt for i, opt in enumerate(edited_options)})
            else: # 주관식
                updated_data['answer'] = st.text_input("정답 수정", value=problem['answer'])


            if st.button("변경사항 저장", type="primary"):
                update_problem(worksheet, drive_service, problem, updated_data)
                st.success("문제가 업데이트되었습니다."); st.rerun()
        
        st.divider()

        if f'confirm_delete_{problem_id}' not in st.session_state:
            st.session_state[f'confirm_delete_{problem_id}'] = False

        if st.session_state[f'confirm_delete_{problem_id}']:
            st.error("정말로 이 문제를 삭제하시겠습니까? 이 작업은 되돌릴 수 없습니다.")
            col1, col2, _ = st.columns([1.5, 1, 2])
            with col1:
                if st.button("✅ 예, 삭제합니다"):
                    delete_problem(worksheet, drive_service, problem)
                    st.session_state[f'confirm_delete_{problem_id}'] = False
                    st.success("문제가 삭제되었습니다.")
                    st.session_state.page = "목록"; st.rerun()
            with col2:
                if st.button("❌ 아니요, 취소합니다"):
                    st.session_state[f'confirm_delete_{problem_id}'] = False; st.rerun()
        else:
            if st.button("🗑️ 문제 삭제하기"):
                st.session_state[f'confirm_delete_{problem_id}'] = True; st.rerun()
    else:
        password_input = st.text_input("문제 관리를 위해 비밀번호를 입력하세요.", type="password")
        if st.button("인증하기"):
            ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD") or st.secrets.get("general", {}).get("admin_password")
            if password_input == str(problem.get('password', '')) or (ADMIN_PASSWORD and password_input == ADMIN_PASSWORD):
                st.session_state.unlocked_problem_id = problem_id; st.success("인증되었습니다."); st.rerun()
            else:
                st.error("비밀번호가 틀렸습니다.")

def render_creation_form(worksheet, drive_service):
    st.header("✍️ 새로운 문제 만들기")
    if st.button("⬅️ 목록으로 돌아가기"):
        st.session_state.page = "목록"; st.rerun()

    title = st.text_input("📝 문제 제목", key="title")
    creator = st.text_input("👤 출제자 이름", key="creator")
    category = st.selectbox("📚 분류", ["수학2", "확률과 통계", "독서", "영어", "물리학1", "화학1", "생명과학1", "지구과학1", "사회문화", "윤리와사상", "기타"], index=None, key="category")
    password = st.text_input("🔒 비밀번호 설정", type="password", key="password")
    question_type = st.radio("📋 문제 유형", ('객관식', '주관식'), key="question_type")
    
    question = st.text_area("❓ 문제 내용", key="question")
    question_image = st.file_uploader("🖼️ 문제 이미지 추가", type=['png', 'jpg', 'jpeg'])
    
    explanation = st.text_area("📝 문제 풀이/해설", key="explanation")
    explanation_image = st.file_uploader("🖼️ 해설 이미지 추가", type=['png', 'jpg', 'jpeg'])

    answer = None
    options = ["", "", "", ""]
    if question_type == '객관식':
        options = [st.text_input(f"선택지 {i+1}", key=f"opt{i+1}") for i in range(4)]
        answer = st.selectbox("✅ 정답 선택", [opt for opt in options if opt], index=None, key="answer")
    else: # 주관식
        answer = st.text_input("✅ 정답 입력", key="answer")


    if st.button("문제 제출하기"):
        is_valid = False
        if question_type == '객관식':
            if all([title, creator, category, password, question, answer]) and all(options):
                is_valid = True
        else: # 주관식
            if all([title, creator, category, password, question, answer]):
                is_valid = True

        if not is_valid:
            st.warning("이미지를 제외한 모든 필수 필드를 채워주세요!")
        else:
            with st.spinner('이미지를 업로드하고 문제를 저장하는 중...'):
                folder_id = get_or_create_drive_folder(drive_service, DRIVE_FOLDER_NAME)
                if not folder_id:
                    st.error("Google Drive 폴더를 찾거나 생성할 수 없어 문제를 저장할 수 없습니다.")
                    return

                question_image_id = ""
                explanation_image_id = ""
                upload_failed = False

                if question_image:
                    question_image_id = upload_image_to_drive(drive_service, folder_id, question_image)
                    if not question_image_id:
                        st.error("문제 이미지 업로드에 실패했습니다. 다시 시도해주세요.")
                        upload_failed = True

                if explanation_image and not upload_failed:
                    explanation_image_id = upload_image_to_drive(drive_service, folder_id, explanation_image)
                    if not explanation_image_id:
                        st.error("해설 이미지 업로드에 실패했습니다. 다시 시도해주세요.")
                        upload_failed = True
                
                if not upload_failed:
                    new_problem = {
                        "id": str(uuid.uuid4()), "title": title, "category": category, "question": question,
                        "option1": options[0], "option2": options[1], "option3": options[2], "option4": options[3],
                        "answer": answer, "creator": creator, "password": password, "explanation": explanation,
                        "question_type": question_type,
                        "question_image_id": question_image_id,
                        "explanation_image_id": explanation_image_id
                    }
                    save_problem(worksheet, new_problem)
                    st.success("🎉 문제가 성공적으로 만들어졌습니다!"); st.session_state.page = "목록"; st.rerun()

# --- 메인 앱 로직 ---
def main():
    st.set_page_config(page_title="2학년 문제 공유 게시판", layout="wide")
    apply_custom_css()
    st.title("📝 2학년 문제 공유 게시판")

    initialize_app_state()
    
    creds = get_google_creds()
    gspread_client = get_gspread_client(creds)
    drive_service = get_drive_service(creds)
    
    worksheet = get_sheet(gspread_client)
    problem_df = load_data(worksheet)

    if st.session_state.page == "목록":
        render_problem_list(problem_df)
    elif st.session_state.page == "상세":
        problem_df_filtered = problem_df[problem_df['id'] == st.session_state.selected_problem_id]
        if not problem_df_filtered.empty:
            problem = problem_df_filtered.iloc[0].to_dict()
            render_problem_detail(problem, worksheet, drive_service)
        else:
            st.error("선택된 문제를 찾을 수 없습니다. 목록으로 돌아갑니다.")
            st.session_state.page = "목록"; st.rerun()
    elif st.session_state.page == "만들기":
        render_creation_form(worksheet, drive_service)

if __name__ == "__main__":
    main()

# --- Streamlit UI 요소 숨기기 ---
hide_streamlit_elems = """
<script>
    const hideElements = () => {
        const selectors = ['div[data-testid="stToolbar"]', 'div[data-testid="stDecoration"]', '#MainMenu', 'header', 'footer', 'a[href*="streamlit.io"]'];
        const doc = window.parent.document;
        selectors.forEach(selector => {
            const elements = doc.querySelectorAll(selector);
            elements.forEach(el => {
                let targetElement = (el.tagName === 'A') ? el.closest('div') : el;
                if (targetElement && targetElement.style.display !== 'none') {
                    targetElement.style.display = 'none';
                }
            });
        });
    };
    const intervalId = setInterval(hideElements, 100);
</script>
"""
components.html(hide_streamlit_elems, height=0)