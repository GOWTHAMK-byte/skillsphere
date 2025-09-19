from flask import render_template, redirect, url_for, flash, request, jsonify
from app.forms import LoginForm, RegisterForm, EditProfileForm, CreatePostForm
from flask_login import current_user, login_user, logout_user, login_required
from app.models import User, Profile, Skill, Post, Application
from app import app, db
import sqlalchemy as sa
from werkzeug.utils import secure_filename
import os
import secrets
import json


@app.route("/")
@app.route("/index")
def index():
    gender_filter = request.args.get('gender')
    stmt = sa.select(Post).options(sa.orm.joinedload(Post.teammates)).order_by(Post.timestamp.desc())
    if gender_filter and gender_filter != 'Any':
        stmt = stmt.where(Post.gender_requirement == gender_filter)
    posts = db.session.scalars(stmt).unique().all()
    # Get application status for current user for each post
    app_status = {}
    if current_user.is_authenticated:
        app_rows = db.session.scalars(sa.select(Application).where(Application.applicant_id == current_user.id)).all()
        for app in app_rows:
            app_status[app.post_id] = app.status
    return render_template("index.html", title="Home", posts=posts, gender_filter=gender_filter, app_status=app_status)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(
            sa.select(User).where(User.username == form.username.data))
        if user is None or not user.check_password(form.password.data):
            flash('Invalid username or password')
            return redirect(url_for('login'))
        login_user(user, remember=form.remember_me.data)
        return redirect(url_for('index'))
    return render_template('login.html', title='Sign In', form=form)


@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = RegisterForm()
    if form.validate_on_submit():
        user = User(username=form.username.data, email=form.email.data)
        user.set_password(form.password.data)
        db.session.add(user)
        # Create an associated Profile object
        profile = Profile(user=user)
        db.session.add(profile)
        db.session.commit()
        flash('Congratulations, you are now a registered user!')
        return redirect(url_for('login'))
    return render_template('register.html', title='Register', form=form)


@app.route('/user/<username>')
@login_required
def user(username):
    user = db.first_or_404(sa.select(User).where(User.username == username))
    posts = db.session.scalars(sa.select(Post).where(Post.creator_id == user.id).order_by(Post.timestamp.desc()).options(sa.orm.joinedload(Post.teammates))).unique().all()
    return render_template('user.html', user=user, posts=posts)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


# --- NEW API ENDPOINT FOR SKILL AUTOCOMPLETE ---
@app.route('/api/skills/search')
@login_required
def search_skills():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])

    results = db.session.scalars(
        sa.select(Skill.name).where(Skill.name.ilike(f'{query}%')).limit(10)
    ).all()

    return jsonify(results)


# --- UPDATED ROUTE FOR EDITING PROFILES ---
@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm()
    if form.validate_on_submit():
        # Handle avatar file upload
        if form.avatar.data:
            random_hex = secrets.token_hex(8)
            _, f_ext = os.path.splitext(form.avatar.data.filename)
            picture_fn = random_hex + f_ext
            picture_path = os.path.join(app.root_path, 'static/avatars', picture_fn)
            form.avatar.data.save(picture_path)
            current_user.profile.avatar_filename = picture_fn

        # Update standard profile fields
        current_user.profile.name = form.name.data
        current_user.profile.bio = form.bio.data
        current_user.profile.college = form.college.data
        current_user.profile.year = form.year.data
        current_user.profile.degree = form.degree.data
        current_user.profile.github_url = form.github_url.data
        current_user.profile.linkedin_url = form.linkedin_url.data
        current_user.profile.location = form.location.data
        current_user.profile.gender = form.gender.data

        # Logic to process skills from Tagify
        current_user.profile.skills.clear()
        try:
            skills_data = json.loads(form.skills.data)
        except (json.JSONDecodeError, TypeError):
            skills_data = []

        for skill_item in skills_data:
            skill_name = skill_item.get('value', '').strip()
            if skill_name:
                skill = db.session.scalar(sa.select(Skill).where(Skill.name == skill_name))
                if not skill:
                    skill = Skill(name=skill_name)
                    db.session.add(skill)
                current_user.profile.skills.append(skill)

        db.session.commit()
        flash('Your changes have been saved.')
        return redirect(url_for('user', username=current_user.username))

    elif request.method == 'GET':
        # Pre-populate form with existing data
        form.name.data = current_user.profile.name
        form.bio.data = current_user.profile.bio
        form.college.data = current_user.profile.college
        form.year.data = current_user.profile.year
        form.degree.data = current_user.profile.degree
        form.github_url.data = current_user.profile.github_url
        form.linkedin_url.data = current_user.profile.linkedin_url
        form.location.data = current_user.profile.location
        form.gender.data = current_user.profile.gender
        # Skills are pre-populated in the template's input value attribute

    return render_template("edit_profile.html", title='Edit Profile', form=form)


@app.route('/create_post', methods=['GET', 'POST'])
@login_required
def create_post():
    form = CreatePostForm()
    if form.validate_on_submit():
        poster_filename = None
        # Handle the optional event poster upload
        if form.event_poster.data:
            random_hex = secrets.token_hex(8)
            _, f_ext = os.path.splitext(form.event_poster.data.filename)
            poster_filename = random_hex + f_ext
            poster_path = os.path.join(app.root_path, 'static/posters', poster_filename)
            form.event_poster.data.save(poster_path)

        # Create a new Post object and populate it with form data
        post = Post(
            event_name=form.event_name.data,
            description=form.description.data,
            idea=form.idea.data,
            team_size=form.team_size.data,
            team_requirement=form.team_requirement.data,
            event_poster_filename=poster_filename,
            event_type=form.event_type.data,
            event_datetime=form.event_datetime.data,
            event_venue=form.event_venue.data,
            creator=current_user,
            gender_requirement=form.gender_requirement.data
        )

        # Process the skills from the Tagify input
        try:
            skills_data = json.loads(form.required_skills.data)
        except (json.JSONDecodeError, TypeError):
            skills_data = []

        for skill_item in skills_data:
            skill_name = skill_item.get('value', '').strip()
            if skill_name:
                # Check if skill already exists in the database
                skill = db.session.scalar(sa.select(Skill).where(Skill.name == skill_name))
                # If not, create a new one
                if not skill:
                    skill = Skill(name=skill_name)
                    db.session.add(skill)
                # Add the skill to the post's required_skills list
                post.required_skills.append(skill)

        db.session.add(post)
        db.session.commit()
        flash('Your post has been created successfully!', 'success')
        return redirect(url_for('user', username=current_user.username))

    return render_template('create_post.html', title='Create a New Post', form=form)


@app.route('/apply/<int:post_id>', methods=['POST'])
@login_required
def apply_to_post(post_id):
    post = db.session.get(Post, post_id)
    if not post:
        flash('Post not found.', 'danger')
        return redirect(url_for('index'))
    if post.applications_closed:
        flash('Applications for this post are closed.', 'warning')
        return redirect(url_for('index'))
    # Prevent duplicate applications
    existing = db.session.scalar(sa.select(Application).where(Application.post_id == post_id, Application.applicant_id == current_user.id))
    if existing:
        flash('You have already applied to this post.', 'warning')
        return redirect(url_for('index'))
    # Optionally check gender requirement
    if post.gender_requirement and post.gender_requirement != 'Any':
        if current_user.profile.gender != post.gender_requirement:
            flash('You do not meet the gender requirement for this post.', 'danger')
            return redirect(url_for('index'))
    application = Application(post_id=post_id, applicant_id=current_user.id)
    db.session.add(application)
    db.session.commit()
    flash('Application submitted!', 'success')
    return redirect(url_for('index'))


@app.route('/manage_post/<int:post_id>', methods=['GET', 'POST'])
@login_required
def manage_post(post_id):
    post = db.session.get(Post, post_id)
    if not post or post.creator_id != current_user.id:
        flash('You are not authorized to manage this post.', 'danger')
        return redirect(url_for('index'))
    # Handle accept/reject/close/reopen actions
    if request.method == 'POST':
        if 'close_applications' in request.form:
            post.applications_closed = True
            db.session.commit()
            flash('Applications have been closed.', 'success')
            return redirect(url_for('manage_post', post_id=post_id))
        elif 'open_applications' in request.form:
            post.applications_closed = False
            db.session.commit()
            flash('Applications have been reopened.', 'success')
            return redirect(url_for('manage_post', post_id=post_id))
        app_id = request.form.get('app_id')
        action = request.form.get('action')
        application = db.session.get(Application, int(app_id))
        if application and application.post_id == post_id:
            if action == 'accept':
                application.status = 'Accepted'
                db.session.commit()
                # Add to teammates if not already (after commit to ensure session is up to date)
                if application.applicant not in post.teammates:
                    post.teammates.append(application.applicant)
                    db.session.commit()
            elif action == 'reject':
                application.status = 'Rejected'
                db.session.commit()
            flash(f'Applicant has been {action}ed.', 'success')
        return redirect(url_for('manage_post', post_id=post_id))
    # List all applications
    applications = db.session.scalars(sa.select(Application).where(Application.post_id == post_id)).all()
    return render_template('manage_post.html', post=post, applications=applications)


@app.route('/teams')
@login_required
def teams():
    # All posts where current user is a teammate
    posts = db.session.scalars(sa.select(Post).options(sa.orm.joinedload(Post.teammates)).join(Post.teammates).where(User.id == current_user.id)).unique().all()
    return render_template('teams.html', posts=posts)
