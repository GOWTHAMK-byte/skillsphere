from app import app, db
from app.models import Post, Application
import sqlalchemy as sa

with app.app_context():
    # Delete all applications related to posts
    db.session.execute(sa.text('DELETE FROM application WHERE post_id IN (SELECT id FROM post)'))
    # Delete all teammates associations
    db.session.execute(sa.text('DELETE FROM post_teammates WHERE post_id IN (SELECT id FROM post)'))
    # Delete all required skills associations
    db.session.execute(sa.text('DELETE FROM post_required_skills WHERE post_id IN (SELECT id FROM post)'))
    # Delete all posts
    db.session.execute(sa.text('DELETE FROM post'))
    db.session.commit()
    print('All posts and dependent records have been deleted.')

