import gspread
from google.oauth2.service_account import Credentials
import os

try:
    # --- 구글 시트 연결 ---
    script_dir = os.path.dirname(os.path.abspath(__file__))
    credentials_path = os.path.join(script_dir, "credentials.json")
    
    print("구글 시트에 연결 중...")
    creds = Credentials.from_service_account_file(credentials_path, scopes=["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    
    print("'MyQuizApp' 스프레드시트를 여는 중...")
    spreadsheet = client.open("MyQuizApp")
    
    print("'문제 목록' 워크시트를 여는 중...")
    worksheet = spreadsheet.worksheet("문제 목록")

    # --- 데이터 삭제 및 헤더 재생성 ---
    print("워크시트의 모든 데이터를 삭제하는 중...")
    worksheet.clear()
    
    headers = ["id", "title", "category", "question", "option1", "option2", "option3", "option4", "answer", "creator", "password"]
    print("새로운 헤더를 작성하는 중...")
    worksheet.append_row(headers)
    
    print("\n✅ 모든 기존 문제가 성공적으로 삭제되었습니다.")

except FileNotFoundError:
    print(f"🚨 오류: 'credentials.json' 파일을 찾을 수 없습니다. '{script_dir}' 위치에 파일이 있는지 확인해주세요.")
except Exception as e:
    print(f"🚨 예상치 못한 오류가 발생했습니다: {e}")