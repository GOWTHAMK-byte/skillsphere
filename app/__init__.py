from flask import Flask
from config import Config
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_socketio import SocketIO
import sqlalchemy as sa

# --- ADD THESE TWO LINES AT THE TOP ---
from dotenv import load_dotenv
load_dotenv()
# ------------------------------------

app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)
migrate = Migrate(app, db)
login = LoginManager(app)
socketio = SocketIO(app, async_mode='threading')
# Redirect users to the login page if they try to access a protected page
login.login_view = 'login'


# --- CUSTOM COMMAND ---
@app.cli.command("seed-skills")
def seed_skills():
    """Adds predefined skills to the database."""
    # --- FIX: Import the model *inside* the function ---
    from app.models import Skill

    SKILLS = [
        'Python', 'JavaScript', 'Flask', 'SQLAlchemy', 'React', 'Node.js',
        'Tailwind CSS', 'HTML5', 'CSS3', 'PostgreSQL', 'Docker', 'Git',
        'Data Science', 'Machine Learning', 'UI/UX Design', 'Project Management'
    ]

    print("Seeding skills...")
    for skill_name in SKILLS:
        skill = db.session.scalar(
            sa.select(Skill).where(Skill.name == skill_name))

        if not skill:
            new_skill = Skill(name=skill_name)
            db.session.add(new_skill)
            print(f"Added skill: {skill_name}")
        else:
            print(f"Skill '{skill_name}' already exists.")

    db.session.commit()
    print("Skill seeding complete.")


# --- Import routes and models at the bottom to avoid circular imports ---
from app import routes, models
