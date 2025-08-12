import tkinter as tk
from tkinter import messagebox
import pandas as pd

class QuizApp:
    def __init__(self, root):
        self.root = root
        self.root.title("데스크톱 퀴즈 앱")
        self.root.geometry("600x400")

        try:
            self.quiz_data = pd.read_csv("quiz.csv").to_dict(orient="records")
        except FileNotFoundError:
            messagebox.showerror("오류", "'quiz.csv' 파일을 찾을 수 없습니다.")
            self.root.destroy()
            return
        
        self.current_question = 0
        self.score = 0
        self.user_answers = {}

        self.question_label = tk.Label(root, text="", wraplength=580, font=("Helvetica", 14))
        self.question_label.pack(pady=20)

        self.var = tk.StringVar()
        
        self.radio_buttons = []
        for i in range(4):
            rb = tk.Radiobutton(root, text="", variable=self.var, value="", font=("Helvetica", 12))
            self.radio_buttons.append(rb)
            rb.pack(anchor="w", padx=50)

        self.next_button = tk.Button(root, text="다음", command=self.next_question, font=("Helvetica", 12))
        self.next_button.pack(pady=20)

        self.display_question()

    def display_question(self):
        if self.current_question < len(self.quiz_data):
            q_data = self.quiz_data[self.current_question]
            self.question_label.config(text=f"문제 {self.current_question + 1}: {q_data['question']}")
            
            options = [q_data['option1'], q_data['option2'], q_data['option3'], q_data['option4']]
            self.var.set(None) # 선택 초기화

            for i, option in enumerate(options):
                self.radio_buttons[i].config(text=option, value=option)
        else:
            self.show_results()

    def next_question(self):
        selected_answer = self.var.get()
        if not selected_answer or selected_answer == 'None':
            messagebox.showwarning("알림", "답을 선택해주세요!")
            return

        self.user_answers[self.current_question] = selected_answer
        self.current_question += 1
        
        if self.current_question < len(self.quiz_data):
            self.display_question()
        else:
            self.show_results()

    def show_results(self):
        # 마지막 문제 답변 저장
        if self.current_question > 0:
             self.user_answers[self.current_question -1] = self.var.get()

        for i, q_data in enumerate(self.quiz_data):
            if self.user_answers.get(i) == q_data['answer']:
                self.score += 1
        
        messagebox.showinfo("결과", f"퀴즈가 종료되었습니다!\n\n총 점수: {len(self.quiz_data)}점 만점에 {self.score}점")
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    app = QuizApp(root)
    root.mainloop()
