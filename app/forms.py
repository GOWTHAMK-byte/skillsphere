from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import SubmitField, StringField, BooleanField, PasswordField, TextAreaField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError, Length, Optional, URL
from app.models import User
from app import db
import sqlalchemy as sa


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    password = PasswordField("Password", validators=[DataRequired()])
    remember_me = BooleanField("Remember Me?")
    submit = SubmitField("Login")

class RegisterForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired()])
    email = StringField("Email", validators=[DataRequired(), Email()])
    password = PasswordField("Password", validators=[DataRequired()])
    password1 = PasswordField("Check Password", validators=[DataRequired(), EqualTo("password")])
    submit = SubmitField("Register")

    def validate_username(self, username):
        user = db.session.scalar(sa.select(User).where(
            User.username == username.data))
        if user is not None:
            raise ValidationError('Please use a different username.')

    def validate_email(self, email):
        user = db.session.scalar(sa.select(User).where(
            User.email == email.data))
        if user is not None:
            raise ValidationError('Please use a different email address.')

class EditProfileForm(FlaskForm):
    avatar = FileField('Update Profile Picture (jpg, png)',
                       validators=[FileAllowed(['jpg', 'png', 'jpeg'], 'Images only!')])
    name = StringField('Full Name', validators=[Length(min=0, max=100)])
    bio = TextAreaField('About Me', validators=[Length(min=0, max=500)])
    college = StringField('College/University', validators=[Length(min=0, max=200)])
    year = StringField('Year of Study', validators=[Length(min=0, max=50)])
    degree = StringField('Degree', validators=[Length(min=0, max=100)])
    github_url = StringField('GitHub URL', validators=[Optional(), URL(), Length(min=0, max=255)])
    linkedin_url = StringField('LinkedIn URL', validators=[Optional(), URL(), Length(min=0, max=255)])
    location = StringField('Location', validators=[Length(min=0, max=150)])
    resume = FileField('Update Resume (PDF only)', validators=[FileAllowed(['pdf'], 'Only PDF files are allowed!')])
    submit = SubmitField('Save Changes')