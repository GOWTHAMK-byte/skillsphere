from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import SubmitField, StringField, BooleanField, PasswordField, TextAreaField, SelectField, IntegerField, \
    DateField, URLField
from wtforms.validators import DataRequired, Email, EqualTo, ValidationError, Length, Optional, URL, NumberRange
from app.models import User
from app import db
import sqlalchemy as sa
from wtforms_sqlalchemy.fields import QuerySelectMultipleField
from app.models import Skill
from wtforms.widgets import ListWidget, CheckboxInput


# This is a custom field that will render a multi-select as a list of checkboxes
class MultiCheckboxField(QuerySelectMultipleField):
    widget = ListWidget(prefix_label=False)
    option_widget = CheckboxInput()


# This function provides the choices for the skills field
def get_skills():
    return db.session.scalars(sa.select(Skill).order_by(Skill.name)).all()


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
    skills = StringField('Skills')
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
    submit = SubmitField('Save Changes')
    gender = SelectField('Gender', choices=[('Male', 'Male'), ('Female', 'Female'), ('Other', 'Other'),
                                            ('Prefer not to say', 'Prefer not to say')], validators=[Optional()])


class CreatePostForm(FlaskForm):
    event_name = StringField('Event Name', validators=[DataRequired(), Length(max=140)])
    description = TextAreaField('Description', validators=[DataRequired()])
    event_poster = FileField('Event Poster (optional)',
                             validators=[FileAllowed(['jpg', 'png', 'jpeg'], 'Images only!')])
    event_type = SelectField('Type of Event', choices=[
        ('Hackathon', 'Hackathon'),
        ('Project', 'Project'),
        ('Competition', 'Competition'),
        ('Collaboration', 'Collaboration'),
        ('Other', 'Other')
    ], validators=[Optional()])
    idea = TextAreaField('Idea (optional)')
    team_size = IntegerField('Team Size', validators=[Optional(), NumberRange(min=1, max=10)])
    required_skills = StringField('Skills Required')
    event_datetime = DateField('Event Date', format='%Y-%m-%d', validators=[Optional()])
    event_venue = StringField('Event Venue (or "Online")', validators=[Optional()])
    # --- NEW FIELD ---
    location = StringField('Location (City/Region for Recommendations)', validators=[Optional(), Length(max=150)])
    submit = SubmitField('Create Post')
    male_slots = IntegerField('Number of Males', default=0)
    female_slots = IntegerField('Number of Females', default=0)


# --- MODIFIED: VerifySkillForm ---
class VerifySkillForm(FlaskForm):
    proof_type = SelectField('Proof Type', choices=[
        ('project', 'Project URL'),
        ('certificate', 'Certificate Upload'),
        ('event', 'Completed Event/Hackathon')
    ], validators=[DataRequired()])
    project_url = URLField('Project URL', validators=[Optional(), URL()])
    certificate_file = FileField('Certificate Upload (.pdf, .png, .jpg)',
                                 validators=[FileAllowed(['pdf', 'png', 'jpg', 'jpeg']), Optional()])

    # --- NEW: Added the missing event_id field ---
    event_id = SelectField('Select Completed Event', coerce=int, validators=[Optional()])

    submit = SubmitField('Submit Proof for Review')