from flask import render_template, redirect, url_for, flash, request, jsonify
from app.forms import LoginForm, RegisterForm, EditProfileForm, CreatePostForm
from flask_login import current_user, login_user, logout_user, login_required
from app.models import User, Profile, Skill, Post, Application, post_required_skills, ChatMessage, ChatReadStatus
from app import app, db, socketio
import sqlalchemy as sa
from werkzeug.utils import secure_filename
import os
import secrets
import json
from flask_socketio import join_room, leave_room, emit
from datetime import datetime, timezone


def get_recommended_posts(user, limit=6):
    user_skills = {skill.name for skill in user.profile.skills}
    user_gender = user.profile.gender

    # Query all open posts not created by the user
    posts = db.session.scalars(
        sa.select(Post)
        .where(
            Post.creator_id != user.id,
            Post.applications_closed == False
        )
        .order_by(Post.timestamp.desc())
    ).unique().all()

    scored_posts = []
    for post in posts:
        # Gender requirement check
        if post.gender_requirement and post.gender_requirement != 'Any' and post.gender_requirement != user_gender:
            continue
        required_skills = {skill.name for skill in post.required_skills}
        if not required_skills:
            matched_count = 0  # No required skills, neutral
        else:
            matched_skills = user_skills & required_skills
            matched_count = len(matched_skills)
        # Only recommend if user has at least one required skill or there are no required skills
        if matched_count > 0 or not required_skills:
            scored_posts.append((matched_count, len(required_skills), post.timestamp, post))
    # Sort by matched_count (desc), then fewer required_skills (asc), then timestamp (desc)
    scored_posts.sort(key=lambda x: (-x[0], x[1], -x[2].timestamp() if hasattr(x[2], 'timestamp') else 0))
    # Deduplicate by post id
    seen = set()
    unique_posts = []
    for _, _, _, post in scored_posts:
        if post.id not in seen:
            unique_posts.append(post)
            seen.add(post.id)
        if len(unique_posts) >= limit:
            break
    return unique_posts


@app.route("/")
@app.route("/index")
def index():
    gender_filter = request.args.get('gender')
    app_status = {}
    posts = db.session.scalars(
        sa.select(Post).order_by(Post.timestamp.desc())
    ).unique().all()
    # Deduplicate posts by ID
    seen = set()
    unique_posts = []
    for p in posts:
        if p.id not in seen:
            unique_posts.append(p)
            seen.add(p.id)
    posts = unique_posts
    # --- Messages summary for nav/index ---
    messages_summary = []
    if current_user.is_authenticated:
        from app.models import ChatReadStatus, ChatMessage
        # All posts where user is teammate or creator
        chat_posts = db.session.scalars(
            sa.select(Post)
            .where(sa.or_(Post.creator_id == current_user.id, Post.teammates.any(id=current_user.id)))
        ).unique().all()
        for post in chat_posts:
            latest_msg = db.session.scalars(
                sa.select(ChatMessage).where(ChatMessage.post_id == post.id).order_by(ChatMessage.timestamp.desc())
            ).first()
            read_status = db.session.scalar(
                sa.select(ChatReadStatus).where(ChatReadStatus.user_id == current_user.id, ChatReadStatus.post_id == post.id)
            )
            last_read = read_status.last_read if read_status else None
            if last_read:
                unread_count = db.session.scalar(
                    sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id, ChatMessage.timestamp > last_read)
                )
            else:
                unread_count = db.session.scalar(
                    sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id)
                )
            messages_summary.append({
                'post': post,
                'latest_msg': latest_msg,
                'unread_count': unread_count or 0
            })
        # Sort by latest message time, descending
        messages_summary.sort(key=lambda c: c['latest_msg'].timestamp if c['latest_msg'] else datetime.min, reverse=True)
        messages_summary = messages_summary[:3]
        app_rows = db.session.scalars(sa.select(Application).where(Application.applicant_id == current_user.id)).all()
        for app in app_rows:
            app_status[app.post_id] = app.status
    return render_template("index.html", title="Home", posts=posts, gender_filter=gender_filter, app_status=app_status, recommended_mode=False, recommended_posts=[], messages_summary=messages_summary)


@app.route("/recommend")
@login_required
def recommend():
    gender_filter = request.args.get('gender')
    app_status = {}
    recommended_posts = get_recommended_posts(current_user)
    # Deduplicate recommended_posts as well
    seen = set()
    unique_recommended = []
    for p in recommended_posts:
        if p.id not in seen:
            unique_recommended.append(p)
            seen.add(p.id)
    recommended_posts = unique_recommended
    app_rows = db.session.scalars(sa.select(Application).where(Application.applicant_id == current_user.id)).all()
    for app in app_rows:
        app_status[app.post_id] = app.status
    return render_template("index.html", title="Recommended", posts=[], gender_filter=gender_filter, app_status=app_status, recommended_mode=True, recommended_posts=recommended_posts)


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
    # Robust deduplication by post ID
    seen = set()
    unique_posts = []
    for p in posts:
        if p.id not in seen:
            unique_posts.append(p)
            seen.add(p.id)
    posts = unique_posts
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
    if form.is_submitted():
        print('Form data:', dict(request.form))
    if form.validate_on_submit():
        poster_filename = None
        # Handle the optional event poster upload
        if form.event_poster.data:
            random_hex = secrets.token_hex(8)
            _, f_ext = os.path.splitext(form.event_poster.data.filename)
            poster_filename = random_hex + f_ext
            poster_path = os.path.join(app.root_path, 'static/posters', poster_filename)
            form.event_poster.data.save(poster_path)

        # Debug: Print gender ratio values
        try:
            team_size = int(form.team_size.data or 0)
            male_slots = int(form.male_slots.data or 0)
            female_slots = int(form.female_slots.data or 0)
        except Exception as e:
            flash(f'Error reading gender ratio fields: {e}', 'danger')
            return render_template('create_post.html', title='Create a New Post', form=form)

        if team_size > 0 and (male_slots + female_slots != team_size):
            flash(f'The sum of male and female slots ({male_slots} + {female_slots}) must equal the team size ({team_size}).', 'danger')
            return render_template('create_post.html', title='Create a New Post', form=form)

        try:
            post = Post(
                event_name=form.event_name.data,
                description=form.description.data,
                idea=form.idea.data,
                team_size=team_size,
                team_requirement=form.team_requirement.data,
                event_poster_filename=poster_filename,
                event_type=form.event_type.data,
                event_datetime=form.event_datetime.data,
                event_venue=form.event_venue.data,
                creator=current_user,
                gender_requirement=form.gender_requirement.data,
                male_slots=male_slots,
                female_slots=female_slots
            )
        except Exception as e:
            flash(f'Error creating post object: {e}', 'danger')
            return render_template('create_post.html', title='Create a New Post', form=form)

        # Process the skills from the Tagify input
        try:
            skills_data = json.loads(form.required_skills.data)
        except (json.JSONDecodeError, TypeError):
            skills_data = []

        for skill_item in skills_data:
            skill_name = skill_item.get('value', '').strip()
            if skill_name:
                skill = db.session.scalar(sa.select(Skill).where(Skill.name == skill_name))
                if not skill:
                    skill = Skill(name=skill_name)
                    db.session.add(skill)
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
    return redirect(url_for('dashboard'))


@app.route('/search')
def search():
    q = request.args.get('q', '').strip()
    event_type = request.args.get('event_type', '').strip()
    team_size = request.args.get('team_size', '').strip()
    app_status = {}
    posts_query = sa.select(Post)
    filters = []
    if q:
        # Search event_name, description, and related skills
        skill_subq = sa.select(post_required_skills.c.post_id).join(Skill, Skill.id == post_required_skills.c.skill_id).where(Skill.name.ilike(f'%{q}%'))
        filters.append(
            sa.or_(Post.event_name.ilike(f'%{q}%'),
                   Post.description.ilike(f'%{q}%'),
                   Post.id.in_(skill_subq))
        )
    if event_type:
        filters.append(Post.event_type == event_type)
    if team_size:
        try:
            filters.append(Post.team_size == int(team_size))
        except ValueError:
            pass
    if filters:
        posts_query = posts_query.where(sa.and_(*filters))
    posts_query = posts_query.order_by(Post.timestamp.desc())
    posts = db.session.scalars(posts_query).unique().all()
    # Deduplicate posts by ID
    seen = set()
    unique_posts = []
    for p in posts:
        if p.id not in seen:
            unique_posts.append(p)
            seen.add(p.id)
    posts = unique_posts
    if current_user.is_authenticated:
        app_rows = db.session.scalars(sa.select(Application).where(Application.applicant_id == current_user.id)).all()
        for app in app_rows:
            app_status[app.post_id] = app.status
    return render_template("index.html", title="Search Results", search_mode=True, search_results=posts, posts=[], gender_filter=None, app_status=app_status, recommended_mode=False, recommended_posts=[])


@app.route('/dashboard')
@login_required
def dashboard():
    # Teams: all posts where user is creator or teammate
    teams = db.session.scalars(
        sa.select(Post)
        .where(sa.or_(Post.creator_id == current_user.id, Post.teammates.any(id=current_user.id)))
        .order_by(Post.timestamp.desc())
        .options(sa.orm.joinedload(Post.teammates), sa.orm.joinedload(Post.creator))
    ).unique().all()

    # Applications submitted by the user (with related posts and creators)
    applications = db.session.scalars(
        sa.select(Application)
        .where(Application.applicant_id == current_user.id)
        .options(sa.orm.joinedload(Application.post).joinedload(Post.creator))
    ).all()

    return render_template(
        'dashboard.html',
        teams=teams,
        applications=applications
    )


@app.route('/chat/<int:post_id>', methods=['GET', 'POST'])
@login_required
def chat(post_id):
    post = db.session.get(Post, post_id)
    if not post or (current_user not in post.teammates and current_user != post.creator):
        flash('You are not authorized to view this chat.', 'danger')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        content = request.form.get('content', '').strip()
        if content:
            msg = ChatMessage(post_id=post_id, sender_id=current_user.id, content=content)
            db.session.add(msg)
            db.session.commit()
            return redirect(url_for('chat', post_id=post_id))

    messages = db.session.scalars(
        sa.select(ChatMessage).where(ChatMessage.post_id == post_id).order_by(ChatMessage.timestamp)
    ).all()
    # Mark all as read for this user/post
    read_status = db.session.scalar(
        sa.select(ChatReadStatus).where(ChatReadStatus.user_id == current_user.id, ChatReadStatus.post_id == post_id)
    )
    now = datetime.now(timezone.utc)
    if read_status:
        read_status.last_read = now
    else:
        read_status = ChatReadStatus(user_id=current_user.id, post_id=post_id, last_read=now)
        db.session.add(read_status)
    db.session.commit()
    return render_template('chat.html', post=post, messages=messages)


@socketio.on('join', namespace='/chat')
@login_required
def handle_join(data):
    post_id = data.get('post_id')
    post = db.session.get(Post, post_id)
    if not post or (current_user not in post.teammates and current_user != post.creator):
        emit('error', {'message': 'Unauthorized'}, room=request.sid)
        return
    join_room(str(post_id))
    emit('status', {'message': f'{current_user.username} joined the chat.'}, room=str(post_id))


@socketio.on('send_message', namespace='/chat')
@login_required
def handle_send_message(data):
    post_id = data.get('post_id')
    content = data.get('content', '').strip()
    post = db.session.get(Post, post_id)
    if not post or (current_user not in post.teammates and current_user != post.creator):
        emit('error', {'message': 'Unauthorized'}, room=request.sid)
        return
    if content:
        msg = ChatMessage(post_id=post_id, sender_id=current_user.id, content=content)
        db.session.add(msg)
        db.session.commit()
        emit('receive_message', {
            'username': current_user.username,
            'content': content,
            'timestamp': msg.timestamp.strftime('%b %d, %I:%M %p')
        }, room=str(post_id))


@app.route('/messages')
@login_required
def messages():
    # All posts where user is teammate or creator
    posts = db.session.scalars(
        sa.select(Post)
        .where(sa.or_(Post.creator_id == current_user.id, Post.teammates.any(id=current_user.id)))
        .order_by(Post.timestamp.desc())
    ).unique().all()
    chat_data = []
    rooms = []
    unread_counts = {}
    for post in posts:
        rooms.append(post.id)
        # Get latest message
        latest_msg = db.session.scalars(
            sa.select(ChatMessage).where(ChatMessage.post_id == post.id).order_by(ChatMessage.timestamp.desc())
        ).first()
        # Get last read time
        read_status = db.session.scalar(
            sa.select(ChatReadStatus).where(ChatReadStatus.user_id == current_user.id, ChatReadStatus.post_id == post.id)
        )
        last_read = read_status.last_read if read_status else None
        # Count unread messages
        if last_read:
            unread_count = db.session.scalar(
                sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id, ChatMessage.timestamp > last_read)
            )
        else:
            unread_count = db.session.scalar(
                sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id)
            )
        unread_counts[post.id] = unread_count or 0
        chat_data.append({
            'post': post,
            'latest_msg': latest_msg,
            'unread_count': unread_count or 0
        })
    if request.args.get('json') == '1':
        return jsonify({'rooms': rooms, 'unread_counts': unread_counts})
    return render_template('messages.html', chat_data=chat_data)
