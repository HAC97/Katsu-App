from database import get_db, get_story
# pyrefly: ignore [missing-import]
from flask import Flask, render_template

app = Flask(__name__)
with app.app_context():
    story = get_story(1) # Assuming ID 1 or I can query it
    
    # Let's get the specific story
    conn = get_db()
    s = dict(conn.execute("SELECT * FROM stories WHERE title LIKE '%Luna says%'").fetchone())
    conn.close()
    
    print(render_template('story_detail.html', story=s))
