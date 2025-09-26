from typing import Optional, List
import sqlalchemy as sa
import sqlalchemy.orm as so
from app import db, login
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timezone
from flask import url_for

post_required_skills = db.Table(
    'post_required_skills',
    db.Model.metadata,
    sa.Column('post_id', sa.ForeignKey('post.id'), primary_key=True),
    sa.Column('skill_id', sa.ForeignKey('skill.id'), primary_key=True)
)
post_teammates = db.Table(
    'post_teammates',
    db.Model.metadata,
    sa.Column('post_id', sa.ForeignKey('post.id'), primary_key=True),
    sa.Column('user_id', sa.ForeignKey('user.id'), primary_key=True)
)


@login.user_loader
def load_user(id):
    return db.session.get(User, int(id))


class ProfileSkill(db.Model):
    __tablename__ = 'profile_skill'
    profile_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('profile.id'), primary_key=True)
    skill_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('skill.id'), primary_key=True)
    level: so.Mapped[int] = so.mapped_column(sa.Integer, default=1)
    is_verified: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=False)
    proof_link: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255))
    profile: so.Mapped['Profile'] = so.relationship(back_populates='skill_associations')
    skill: so.Mapped['Skill'] = so.relationship(back_populates='profile_associations')


class User(db.Model, UserMixin):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    username: so.Mapped[str] = so.mapped_column(sa.String(64), index=True, unique=True)
    email: so.Mapped[str] = so.mapped_column(sa.String(120), index=True, unique=True)
    password_hash: so.Mapped[Optional[str]] = so.mapped_column(sa.String(256))
    profile: so.Mapped['Profile'] = so.relationship(back_populates='user', cascade='all, delete-orphan')
    posts: so.Mapped[List['Post']] = so.relationship(back_populates='creator', cascade='all, delete-orphan')
    applications: so.Mapped[List['Application']] = so.relationship(back_populates='applicant', cascade='all, delete-orphan')
    teams: so.Mapped[List['Post']] = so.relationship(secondary=post_teammates, back_populates='teammates')

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
    avatar_filename: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255))
    gender: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20))
    user: so.Mapped['User'] = so.relationship(back_populates='profile')
    location: so.Mapped[Optional[str]] = so.mapped_column(sa.String(150))
    latitude: so.Mapped[Optional[float]] = so.mapped_column(sa.Float)
    longitude: so.Mapped[Optional[float]] = so.mapped_column(sa.Float)
    skill_associations: so.Mapped[List['ProfileSkill']] = so.relationship(
        back_populates='profile', cascade="all, delete-orphan")

    @property
    def avatar_url(self):
        if self.avatar_filename:
            return url_for('static', filename=f'avatars/{self.avatar_filename}')
        else:
            return f"https://api.dicebear.com/8.x/initials/svg?seed={self.user.username}"

    @property
    def skills(self) -> List['Skill']:
        return [assoc.skill for assoc in self.skill_associations]

    def __repr__(self):
        return f'<Profile for {self.user.username}>'


class Skill(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    name: so.Mapped[str] = so.mapped_column(sa.String(50), unique=True, index=True)
    profile_associations: so.Mapped[List['ProfileSkill']] = so.relationship(
        back_populates='skill', cascade="all, delete-orphan")
    required_by_posts: so.Mapped[List['Post']] = so.relationship(
        secondary=post_required_skills, back_populates='required_skills')

    def __repr__(self):
        return f'<Skill {self.name}>'


class Post(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    event_name: so.Mapped[str] = so.mapped_column(sa.String(140))
    description: so.Mapped[str] = so.mapped_column(sa.Text)
    idea: so.Mapped[Optional[str]] = so.mapped_column(sa.Text)
    team_size: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer)
    team_requirement: so.Mapped[Optional[str]] = so.mapped_column(sa.String(100))
    event_poster_filename: so.Mapped[Optional[str]] = so.mapped_column(sa.String(255))
    event_type: so.Mapped[Optional[str]] = so.mapped_column(sa.String(50))
    event_datetime: so.Mapped[Optional[datetime]] = so.mapped_column(sa.DateTime)
    timestamp: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))
    creator_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)
    location: so.Mapped[Optional[str]] = so.mapped_column(sa.String(150))
    latitude: so.Mapped[Optional[float]] = so.mapped_column(sa.Float)
    longitude: so.Mapped[Optional[float]] = so.mapped_column(sa.Float)
    creator: so.Mapped['User'] = so.relationship(back_populates='posts')
    required_skills: so.Mapped[List['Skill']] = so.relationship(
        secondary=post_required_skills, back_populates='required_by_posts')
    teammates: so.Mapped[List['User']] = so.relationship(
        secondary=post_teammates, back_populates='teams')
    applications: so.Mapped[List['Application']] = so.relationship(
        back_populates='post', cascade='all, delete-orphan')
    gender_requirement: so.Mapped[Optional[str]] = so.mapped_column(sa.String(20))
    applications_closed: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=False)
    male_slots: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer, default=0)
    female_slots: so.Mapped[Optional[int]] = so.mapped_column(sa.Integer, default=0)

    def __repr__(self):
        return f'<Post {self.event_name}>'


class Application(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    post_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('post.id'), index=True)
    applicant_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)
    status: so.Mapped[str] = so.mapped_column(sa.String(20), default='Pending')
    timestamp: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))
    notified: so.Mapped[bool] = so.mapped_column(sa.Boolean, default=False)
    post: so.Mapped['Post'] = so.relationship(back_populates='applications')
    applicant: so.Mapped['User'] = so.relationship(back_populates='applications')

    def __repr__(self):
        return f'<Application by {self.applicant.username} for {self.post.event_name}>'


class ChatMessage(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    post_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('post.id'), index=True)
    sender_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)
    content: so.Mapped[str] = so.mapped_column(sa.Text)
    timestamp: so.Mapped[datetime] = so.mapped_column(index=True, default=lambda: datetime.now(timezone.utc))
    post: so.Mapped['Post'] = so.relationship()
    sender: so.Mapped['User'] = so.relationship()


class ChatReadStatus(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    user_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('user.id'), index=True)
    post_id: so.Mapped[int] = so.mapped_column(sa.ForeignKey('post.id'), index=True)
    last_read: so.Mapped[datetime] = so.mapped_column(default=lambda: datetime.now(timezone.utc))
    user: so.Mapped['User'] = so.relationship()
    post: so.Mapped['Post'] = so.relationship()


class HackathonPost(db.Model):
    id: so.Mapped[int] = so.mapped_column(primary_key=True)
    title: so.Mapped[str] = so.mapped_column(sa.String(150))
    description: so.Mapped[Optional[str]] = so.mapped_column(sa.Text)
    date_posted: so.Mapped[datetime] = so.mapped_column(
        index=True, default=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f'<HackathonPost {self.title}>'

