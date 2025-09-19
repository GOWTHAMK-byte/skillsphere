from typing import Optional, List
import sqlalchemy as sa
import sqlalchemy.orm as so
from app import db, login
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# --- 1. DEFINE THE ASSOCIATION TABLE ---
# This table links profiles to skills without needing its own model class.
profile_skills = db.Table(
    'profile_skills',
    db.Model.metadata,
    sa.Column('profile_id', sa.ForeignKey('profile.id'), primary_key=True),
    sa.Column('skill_id', sa.ForeignKey('skill.id'), primary_key=True)
)

@login.user_loader
def load_user(id):
    return db.session.get(User, int(id))

class User(db.Model, UserMixin):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    username: so.Mapped[str] = so.mapped_column(sa.String(64), index=True,
                                                unique=True)
    email: so.Mapped[str] = so.mapped_column(sa.String(120), index=True,
                                             unique=True)
    password_hash: so.Mapped[Optional[str]] = so.mapped_column(sa.String(256))

    profile: so.Mapped['Profile'] = so.relationship(
        back_populates='user', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    def __repr__(self):
        return f'<User {self.username}>'

class Profile(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    name: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100))
    bio: so.Mapped[Optional[str]] = so.mapped_column(sa.String(500))
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)

    college: so.Mapped[Optional[str]] = so.mapped_column(sa.String(200))
    year: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    degree: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100))
    github_url: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255))
    linkedin_url: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255))
    resume_file: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255))
    location: so.Mapped[Optional[str]] = so.mapped_column(sa.String(150))
    avatar_filename: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255))

    user: so.Mapped['User'] = so.relationship(back_populates='profile')

    # --- 2. ADD THE RELATIONSHIP TO PROFILE ---
    # This will allow you to access user.profile.skills
    skills: so.Mapped[List['Skill']] = so.relationship(
        secondary=profile_skills, back_populates='profiles')

    def __repr__(self):
        return f'<Profile for {self.user.username}>'

# --- 3. CREATE THE NEW SKILL MODEL ---
class Skill(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    name: so.Mapped[str] = so.mapped_column(sa.String(50), unique=True, index=True)

    # This relationship connects Skill back to Profile
    profiles: so.Mapped[List['Profile']] = so.relationship(
        secondary=profile_skills, back_populates='skills')

    def __repr__(self):
        return f'<Skill {self.name}>'