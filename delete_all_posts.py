from app import db
from app.models import Post, Application, ChatMessage, ChatReadStatus, Profile, ProfileSkill, User
from skillsphere import app
from sqlalchemy import text

with app.app_context():
    # Delete from dependent tables first
    print("Deleting Applications...")
    Application.query.delete()
    print("Deleting ChatMessages...")
    ChatMessage.query.delete()
    print("Deleting ChatReadStatus...")
    ChatReadStatus.query.delete()

    # Delete from association tables using raw SQL
    print("Deleting from post_required_skills...")
    db.session.execute(text('DELETE FROM post_required_skills'))
    print("Deleting from post_teammates...")
    db.session.execute(text('DELETE FROM post_teammates'))

    # Delete all posts
    print("Deleting Posts...")
    Post.query.delete()

    # Delete all profile skills
    print("Deleting ProfileSkill records...")
    ProfileSkill.query.delete()
    # Delete all profiles
    print("Deleting Profiles...")
    Profile.query.delete()
    # Delete all users
    print("Deleting Users...")
    User.query.delete()

    db.session.commit()
    print("All posts, users, and dependent records deleted.")
