from flask import Flask, render_template, request, redirect, url_for, session, flash
import json
import sqlite3
import random
from pathlib import Path
import logging
from datetime import datetime
import click  # For flask cli command
import os  # For environment variables (good practice)

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = Flask(__name__)
# IMPORTANT: Set a strong, random secret key for production.
# For development, this is okay. For production, use an environment variable.
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev_default_super_secret_key_change_me_!@#$')

# --- Configuration ---
DATABASE_NAME = "quiz_bot.db"
LECTURES_DIR = Path(__file__).resolve().parent
LECTURE_INFO = {
    "lec_1": {"title": "محاضرة ١: تحضير المحاليل من مواد صلبة", "file": "lecture_1.json"},
    "lec_2": {"title": "محاضرة ٢: تحضير المحاليل من سوائل", "file": "lecture_2.json"},
    "lec_3": {"title": "محاضرة ٣: معايرة HCl مع NaOH", "file": "lecture_3.json"},
    "lec_4": {"title": "محاضرة ٤: اختبار موليش", "file": "lecture_4.json"},
    "lec_5": {"title": "محاضرة ٥: اختبار سيلوانوف", "file": "lecture_5.json"},
    "lec_6": {"title": "محاضرة ٦: اختبار بايل", "file": "lecture_6.json"},
}


# --- Database Functions ---
def get_db_connection():
    conn = sqlite3.connect(LECTURES_DIR / DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db_connection()
    schema_file_path = LECTURES_DIR / 'schema.sql'
    if schema_file_path.exists():
        with open(schema_file_path, mode='r') as f:
            conn.cursor().executescript(f.read())
        conn.commit()
        logger.info("Database initialized from schema.sql.")
    else:
        logger.error(
            "schema.sql not found! Database could not be initialized via init_db(). Please create it or run 'flask init-db'.")
    conn.close()


def save_score_db(username: str, lecture_id: str, lecture_title: str, score: int, total_questions: int):
    conn = get_db_connection()
    try:
        logger.info(
            f"Attempting to save score: User='{username}', LecID='{lecture_id}', Score={score}/{total_questions}")
        conn.execute("""
            INSERT INTO user_scores (username, lecture_id, lecture_title, score, total_questions)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (username, lecture_id, lecture_title, score, total_questions))
        conn.commit()
        logger.info(f"Score saved successfully for '{username}' on '{lecture_title}'.")
    except sqlite3.Error as e:
        logger.error(f"Database error saving score: {e}")
    finally:
        conn.close()


def get_top_scores_db(lecture_id: str, limit: int = 10):
    conn = get_db_connection()
    logger.info(f"Fetching top scores for lecture_id: {lecture_id}")
    scores = conn.execute("""
        SELECT username, score, total_questions 
        FROM user_scores
        WHERE lecture_id = ?
        ORDER BY score DESC, timestamp ASC 
        LIMIT ?
    """, (lecture_id, limit)).fetchall()
    conn.close()
    logger.info(f"Fetched {len(scores)} scores for lecture {lecture_id}.")
    return scores


def load_lecture_questions(lecture_id: str):
    logger.info(f"Attempting to load questions for lecture_id: {lecture_id}")
    if lecture_id not in LECTURE_INFO:
        logger.error(f"Unknown lecture_id: {lecture_id}")
        return None
    file_name = LECTURE_INFO[lecture_id]["file"]
    file_path = LECTURES_DIR / file_name
    if not file_path.exists():
        logger.error(f"JSON file does NOT exist: {file_path}")
        return None
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, dict) or "questions" not in data or not isinstance(data["questions"], list):
            logger.error(f"Malformed JSON: {file_path}")
            return None
        logger.info(f"Loaded {len(data['questions'])} questions for {lecture_id}.")
        return data["questions"]
    except Exception as e:
        logger.error(f"Error loading/parsing {file_path}: {e}")
        return None


@app.context_processor
def inject_current_year():
    return {'current_year': datetime.now(datetime.UTC).year if hasattr(datetime, 'UTC') else datetime.utcnow().year}


# --- Routes ---
@app.route('/')
def index():
    logger.info("Serving index page.")
    return render_template('index.html', lectures=LECTURE_INFO)


@app.route('/start_quiz/<lecture_id>')
def start_quiz(lecture_id):
    logger.info(f"Attempting to start quiz for lecture: {lecture_id}")
    if lecture_id not in LECTURE_INFO:
        flash("المحاضرة المختارة غير موجودة!", "danger")
        return redirect(url_for('index'))

    questions = load_lecture_questions(lecture_id)
    if not questions:
        flash(
            f"לא توجد أسئلة متاحة لهذه المحاضرة: {LECTURE_INFO[lecture_id]['title'] if lecture_id in LECTURE_INFO else lecture_id}",
            "warning")
        return redirect(url_for('index'))

    session.clear()
    session['lecture_id'] = lecture_id
    session['questions'] = questions
    session['current_q_idx'] = 0
    session['score'] = 0
    session['username'] = request.args.get('username', 'زائر').strip()
    if not session['username']:
        session['username'] = 'زائر'

    logger.info(f"Quiz started for {lecture_id} by {session['username']}. Questions: {len(questions)}")
    return redirect(url_for('show_question', lecture_id=lecture_id))


@app.route('/quiz/<lecture_id>/question')
def show_question(lecture_id):
    if 'lecture_id' not in session or session['lecture_id'] != lecture_id or 'questions' not in session:
        flash("خطأ في الاختبار. يرجى البدء من جديد.", "danger")
        return redirect(url_for('index'))

    q_idx = session.get('current_q_idx', 0)
    questions = session['questions']

    if not (0 <= q_idx < len(questions)):
        logger.info(f"Quiz for {lecture_id} ended or invalid q_idx {q_idx}. Redirecting to results.")
        return redirect(url_for('results', lecture_id=lecture_id))

    current_question_data = questions[q_idx]

    original_options = list(current_question_data.get('options', []))
    correct_option_text = None
    correct_option_index_key = current_question_data.get('correct_option_index', -1)

    if original_options and 0 <= correct_option_index_key < len(original_options):
        correct_option_text = original_options[correct_option_index_key]
    else:
        logger.error(f"Issue with options or correct_option_index for question: {current_question_data}")

    display_options = list(original_options)
    random.shuffle(display_options)

    session['current_display_options'] = display_options
    session['current_correct_answer_text'] = correct_option_text

    logger.info(f"Showing question {q_idx + 1} for {lecture_id} to {session.get('username', 'N/A')}")
    return render_template('quiz.html',
                           lecture_title=LECTURE_INFO[lecture_id]['title'],
                           question_text=current_question_data.get('question_text', "نص السؤال غير موجود"),
                           options=display_options,
                           q_idx=q_idx,
                           current_q_num=q_idx + 1,
                           total_questions=len(questions),
                           lecture_id=lecture_id)


@app.route('/submit_answer/<lecture_id>/<int:q_idx>', methods=['POST'])
def submit_answer(lecture_id, q_idx):
    logger.info(f"Submitting answer for {lecture_id}, q_idx {q_idx} by {session.get('username', 'N/A')}")
    if 'lecture_id' not in session or session['lecture_id'] != lecture_id or \
            q_idx != session.get('current_q_idx', -1):
        flash("خطأ في إرسال الإجابة. حاول مرة أخرى.", "danger")
        # Redirect back to the question they were on, not just /quiz/<lecture_id>/question
        return redirect(url_for('show_question', lecture_id=lecture_id))

    selected_option_text = request.form.get('option')
    if not selected_option_text:
        flash("يرجى اختيار إجابة.", "warning")
        return redirect(url_for('show_question', lecture_id=lecture_id))

    correct_answer_text = session.get('current_correct_answer_text')
    if correct_answer_text is None:
        flash("خطأ: الإجابة الصحيحة غير محددة لهذا السؤال.", "danger")
        logger.error(f"Correct answer text was None for q_idx {q_idx} of lecture {lecture_id}")
    elif selected_option_text == correct_answer_text:
        session['score'] = session.get('score', 0) + 1
        flash("إجابة صحيحة!", "success")
        logger.info("Answer was correct.")
    else:
        flash(f"إجابة خاطئة. الإجابة الصحيحة: {correct_answer_text}", "danger")
        logger.info(f"Answer incorrect. Correct: {correct_answer_text}, Selected: {selected_option_text}")

    session['current_q_idx'] = q_idx + 1

    if session['current_q_idx'] < len(session['questions']):
        return redirect(url_for('show_question', lecture_id=lecture_id))
    else:
        logger.info(f"Quiz finished for {lecture_id} by {session.get('username', 'N/A')}. Saving score.")
        if 'username' in session and 'score' in session and 'questions' in session:  # Ensure data is there
            save_score_db(username=session.get('username', 'زائر'),
                          lecture_id=lecture_id,
                          lecture_title=LECTURE_INFO[lecture_id]['title'],
                          score=session.get('score', 0),
                          total_questions=len(session['questions']))
        else:
            logger.error("Could not save score, missing data in session.")
        return redirect(url_for('results', lecture_id=lecture_id))


@app.route('/results/<lecture_id>')
def results(lecture_id):
    score = session.get('score')
    total_questions_list = session.get('questions', [])  # Get full list to count
    total_questions = len(total_questions_list)
    username = session.get('username', 'أنت')

    # If lecture_id in session matches, and score is not None (meaning quiz was completed)
    if session.get('lecture_id') != lecture_id or score is None:
        flash("لا توجد نتائج لعرضها أو انتهت الجلسة. يرجى بدء اختبار جديد.", "warning")
        return redirect(url_for('index'))

    lecture_title = LECTURE_INFO[lecture_id]['title']
    logger.info(f"Displaying results for {lecture_id} for {username}: {score}/{total_questions}")

    return render_template('results.html',
                           score=score,
                           total_questions=total_questions,
                           lecture_title=lecture_title,
                           lecture_id=lecture_id,
                           username=username)


@app.route('/leaderboard')
def leaderboard_lecture_select():
    logger.info("Serving leaderboard lecture selection page.")
    return render_template('leaderboard_select.html', lectures=LECTURE_INFO)


@app.route('/leaderboard/<lecture_id>')
def show_lecture_leaderboard(lecture_id):
    logger.info(f"Serving leaderboard for lecture: {lecture_id}")
    if lecture_id not in LECTURE_INFO:
        flash("المحاضرة المحددة للوحة الصدارة غير موجودة.", "danger")
        return redirect(url_for('leaderboard_lecture_select'))

    scores = get_top_scores_db(lecture_id=lecture_id, limit=10)
    return render_template('leaderboard.html',
                           scores=scores,
                           lecture_title=LECTURE_INFO[lecture_id]['title'],
                           lecture_id=lecture_id)


@app.cli.command('init-db')
def init_db_command():
    schema_file = LECTURES_DIR / 'schema.sql'
    if not schema_file.exists():
        schema_sql_content = """
        DROP TABLE IF EXISTS user_scores;
        CREATE TABLE user_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            lecture_id TEXT NOT NULL,
            lecture_title TEXT NOT NULL,
            score INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        ); """
        with open(schema_file, 'w') as f: f.write(schema_sql_content)
        logger.info(f"Created default schema.sql at {schema_file}")
    init_db()
    click.echo('Initialized the database.')


db_path = LECTURES_DIR / DATABASE_NAME
if not db_path.exists():
    logger.info(f"Database not found at {db_path}, initializing...")
    schema_file = LECTURES_DIR / 'schema.sql'
    if not schema_file.exists():
        schema_sql_content = """
        DROP TABLE IF EXISTS user_scores;
        CREATE TABLE user_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            lecture_id TEXT NOT NULL,
            lecture_title TEXT NOT NULL,
            score INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        ); """
        with open(schema_file, 'w') as f: f.write(schema_sql_content)
        logger.info(f"Created default schema.sql (startup check)")
    try:
        init_db()
    except Exception as e:
        logger.error(f"Failed to init DB on startup: {e}")

if __name__ == '__main__':
    from waitress import serve

    logger.info("Starting Waitress server on http://0.0.0.0:5000")
    # For development, app.run(debug=True) is fine. For shared access, waitress is better.
    # If you are just running locally for yourself, use:
    # app.run(debug=True, host='0.0.0.0', port=5000)
    # For sharing via ngrok or on a simple server:
    serve(app, host='0.0.0.0', port=5000, threads=4)