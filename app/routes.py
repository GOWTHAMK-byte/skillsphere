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

# --- NEW IMPORTS FOR RECOMMENDATION ENGINE ---
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np


def get_recommended_posts(user, limit=6):
    """
    Generates highly personalized post recommendations for a user.

    This function integrates multiple advanced recommendation strategies:
    1.  **NLP Content Similarity:** Understands the *meaning* of post descriptions to find conceptually similar projects.
    2.  **Complementary Skill Matching:** Identifies posts where the user's skills would be a perfect "missing piece" for the team (e.g., frontend dev for a backend project).
    3.  **Weighted Scoring:** Balances multiple factors like direct skill matches, location, and user preferences.
    4.  **Cold Start Handling:** Provides a sensible default for brand new users.
    5.  **Diversity Control:** Ensures the user sees a variety of projects from different creators.
    """
    # --- 1. SETUP: SKILL CATEGORIES & WEIGHTS ---
    SKILL_CATEGORIES = {
        'Frontend': ['react', 'angular', 'vue', 'javascript', 'html', 'css', 'typescript', 'svelte'],
        'Backend': ['python', 'java', 'node.js', 'ruby', 'php', 'go', 'flask', 'django', 'express.js'],
        'Database': ['sql', 'mysql', 'postgresql', 'mongodb', 'redis'],
        'DevOps': ['docker', 'kubernetes', 'aws', 'gcp', 'azure', 'jenkins', 'ci/cd'],
        'Mobile': ['swift', 'kotlin', 'react native', 'flutter', 'ios', 'android'],
        'Data Science': ['machine learning', 'tensorflow', 'pytorch', 'pandas', 'numpy', 'scikit-learn'],
        'Design': ['figma', 'adobe xd', 'sketch', 'ui', 'ux', 'prototyping']
    }
    COMPLEMENTARY_PAIRS = {
        'Frontend': ['Backend', 'Design', 'Database'],
        'Backend': ['Frontend', 'DevOps', 'Data Science', 'Database'],
        'Design': ['Frontend', 'Mobile'],
        'Data Science': ['Backend', 'Database'],
        'Mobile': ['Backend', 'Design']
    }
    WEIGHTS = {
        'skill': 4.0,           # Score for direct skill matches
        'complementary': 8.0,   # High score for being a good team fit
        'rarity': 2.0,          # Bonus for having rare skills
        'event_type': 3.0,      # Score for matching preferred event types
        'recency': 1.5,         # Bonus for newer posts
        'location': 4.0,        # Score for local events
        'nlp': 10.0             # Highest weight for conceptual similarity
    }

    # --- 2. NLP ANALYSIS: UNDERSTAND USER'S "TASTE" ---
    all_posts_for_nlp = db.session.scalars(sa.select(Post)).all()
    user_applications_for_nlp = db.session.scalars(sa.select(Application).where(Application.applicant_id == user.id)).all()
    user_liked_texts = [app.post.description + ' ' + app.post.idea for app in user_applications_for_nlp if app.post and app.post.description and app.post.idea]
    if not user_liked_texts and user.profile.bio:
        user_liked_texts.append(user.profile.bio)

    post_texts = {post.id: (post.description or '') + ' ' + (post.idea or '') for post in all_posts_for_nlp}
    corpus = list(post_texts.values()) + user_liked_texts
    user_taste_vector, post_vectors = None, {}
    if len(corpus) > 1:
        try:
            vectorizer = TfidfVectorizer(stop_words='english', min_df=1)
            tfidf_matrix = vectorizer.fit_transform(corpus)
            post_vectors = {pid: tfidf_matrix[i] for i, pid in enumerate(post_texts.keys())}
            if user_liked_texts:
                user_taste_vector = tfidf_matrix[-len(user_liked_texts):].mean(axis=0)
                # Convert to dense ndarray for compatibility
                if hasattr(user_taste_vector, 'toarray'):
                    user_taste_vector = user_taste_vector.toarray()
                user_taste_vector = np.asarray(user_taste_vector)
        except ValueError: # Handles edge case of empty vocabulary
            pass

    # --- 3. USER PROFILING & COLD START ---
    user_skills = {skill.name.lower() for skill in user.profile.skills}
    is_new_user = not user_skills and not user_applications_for_nlp and not user.profile.bio
    if is_new_user: # If user is new, return the most recent posts as a fallback
        all_posts = db.session.scalars(
            sa.select(Post).where(Post.creator_id != user.id, Post.applications_closed == False)
            .order_by(Post.timestamp.desc())
        ).all()
        return all_posts[:5] if len(all_posts) >= 5 else all_posts

    user_gender = user.profile.gender
    user_location = (user.profile.location or '').strip().lower() if user.profile and user.profile.location else ''
    user_event_types = {app.post.event_type for app in user_applications_for_nlp if app.post and app.post.event_type}
    user_posts_created = db.session.scalars(sa.select(Post).where(Post.creator_id == user.id)).all()
    for post in user_posts_created:
        if post.event_type: user_event_types.add(post.event_type)

    # --- 4. SCORING LOOP: EVALUATE EACH POST ---
    applied_post_ids = {app.post_id for app in user_applications_for_nlp}
    teammate_post_ids = {post.id for post in user.teams}
    candidate_posts = db.session.scalars(
        sa.select(Post).where(Post.creator_id != user.id, Post.applications_closed == False)
    ).unique().all()

    scored_posts = []
    for post in candidate_posts:
        if post.id in applied_post_ids or post.id in teammate_post_ids: continue
        if post.gender_requirement and post.gender_requirement != 'Any' and post.gender_requirement != user_gender: continue

        required_skills = {skill.name.lower() for skill in post.required_skills}

        # Direct Skill Match Score
        matched_skills = user_skills & required_skills
        skill_score = len(matched_skills)

        # Rarity Bonus
        rarity_bonus = sum(1 for skill in matched_skills if db.session.scalar(sa.select(sa.func.count(post_required_skills.c.post_id)).join(Skill).where(Skill.name == skill)) < 5)

        # Complementary Skill "Team Fit" Score
        user_skill_cats = {cat for cat, skills in SKILL_CATEGORIES.items() if any(s in user_skills for s in skills)}
        post_skill_cats = {cat for cat, skills in SKILL_CATEGORIES.items() if any(s in required_skills for s in skills)}
        missing_cats = post_skill_cats - user_skill_cats
        complementary_score = 0
        for missing_cat in missing_cats:
            if any(user_cat in COMPLEMENTARY_PAIRS and missing_cat in COMPLEMENTARY_PAIRS[user_cat] for user_cat in user_skill_cats):
                complementary_score += 1

        # Other Preference Scores
        event_type_score = 1 if post.event_type in user_event_types else 0
        now_utc = datetime.now(timezone.utc)
        post_time = post.timestamp.replace(tzinfo=timezone.utc) if post.timestamp.tzinfo is None else post.timestamp
        recency_score = max(0, 1 - ((now_utc - post_time).days / 30))

        post_location = (post.event_venue or '').strip().lower() if post.event_venue else ''
        location_score = 0
        if user_location and post_location:
            if user_location in post_location or post_location in user_location: location_score = 1.0
            else:
                user_city, post_city = user_location.split(',')[0].strip(), post_location.split(',')[0].strip()
                if user_city and post_city and user_city == post_city: location_score = 0.7

        # NLP Content Similarity Score
        nlp_score = 0
        if user_taste_vector is not None and post.id in post_vectors:
            post_vector = post_vectors[post.id]
            # Convert both vectors to dense ndarrays for compatibility
            user_vec_dense = user_taste_vector
            if hasattr(user_vec_dense, 'toarray'):
                user_vec_dense = user_vec_dense.toarray()
            user_vec_dense = np.asarray(user_vec_dense)
            post_vec_dense = post_vector
            if hasattr(post_vec_dense, 'toarray'):
                post_vec_dense = post_vec_dense.toarray()
            post_vec_dense = np.asarray(post_vec_dense)
            if np.any(user_vec_dense) and np.any(post_vec_dense):
                nlp_score = cosine_similarity(user_vec_dense, post_vec_dense)[0][0]

        # Calculate Final Weighted Score
        total_score = (
            (skill_score * WEIGHTS['skill']) +
            (complementary_score * WEIGHTS['complementary']) +
            (rarity_bonus * WEIGHTS['rarity']) +
            (event_type_score * WEIGHTS['event_type']) +
            (recency_score * WEIGHTS['recency']) +
            (location_score * WEIGHTS['location']) +
            (nlp_score * WEIGHTS['nlp'])
        )
        scored_posts.append((total_score, post.timestamp, post))

    # --- 5. FINAL RANKING & DIVERSITY CONTROL ---
    scored_posts.sort(key=lambda x: x[0], reverse=True) # Sort primarily by score

    seen_creators, unique_posts = {}, []
    for _, _, post in scored_posts:
        creator_id = post.creator_id
        if seen_creators.get(creator_id, 0) >= 2: continue # Limit to max 2 posts from same creator
        unique_posts.append(post)
        seen_creators[creator_id] = seen_creators.get(creator_id, 0) + 1
        if len(unique_posts) >= limit: break

    # Ensure at least 5 posts if possible
    if len(unique_posts) < 5:
        # Add more posts (ignoring diversity) until 5 or all are included
        all_sorted_posts = [post for _, _, post in scored_posts if post not in unique_posts]
        for post in all_sorted_posts:
            unique_posts.append(post)
            if len(unique_posts) >= 5:
                break

    return unique_posts




@app.route("/")
def landing():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    return render_template("landing.html", title="Welcome to SkillSphere")


@app.route("/index")
@login_required
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
                event_poster_filename=poster_filename,
                event_type=form.event_type.data,
                event_datetime=form.event_datetime.data,
                event_venue=form.event_venue.data,
                creator=current_user,
                gender_requirement=form.gender_requirement.data if hasattr(form, 'gender_requirement') else None,
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
    # Recommend users
    recommended_users = recommend_users_for_post(post)
    return render_template('manage_post.html', post=post, applications=applications, recommended_users=recommended_users)


def recommend_users_for_post(post, limit=5):
    from app.models import User, Application
    required_skills = {skill.name.lower() for skill in post.required_skills}
    teammate_ids = {user.id for user in post.teammates}
    applicant_ids = {app.applicant_id for app in post.applications}
    exclude_ids = teammate_ids | applicant_ids | {post.creator_id}
    candidates = db.session.scalars(sa.select(User).where(~User.id.in_(exclude_ids))).all()
    scored = []
    for user in candidates:
        if not user.profile:
            continue
        user_skills = {skill.name.lower() for skill in user.profile.skills}
        score = len(required_skills & user_skills)
        if score > 0:
            scored.append((score, user))
    scored.sort(reverse=True, key=lambda x: x[0])
    return [user for score, user in scored[:limit]]


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
    all_applications = db.session.scalars(
        sa.select(Application)
        .where(Application.applicant_id == current_user.id)
        .options(sa.orm.joinedload(Application.post).joinedload(Post.creator))
    ).all()

    # Filter out applications that are accepted and user is already a teammate
    applications = [
        app for app in all_applications
        if not (app.status == 'Accepted' and current_user in app.post.teammates)
    ]

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
            'timestamp': msg.timestamp.strftime('%b %d, %I:%M %p'),
            'sender_id': current_user.id
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


@app.route('/api/recommend/apply/<int:post_id>', methods=['POST'])
@login_required
def recommend_apply(post_id):
    post = db.session.get(Post, post_id)
    if not post:
        return jsonify({'success': False, 'error': 'Post not found'}), 404
    # Check if already applied
    existing = db.session.scalar(sa.select(Application).where(Application.post_id == post_id, Application.applicant_id == current_user.id))
    if not existing:
        application = Application(post_id=post_id, applicant_id=current_user.id)
        db.session.add(application)
        db.session.commit()
    return jsonify({'success': True})

@app.route('/api/recommend/ignore/<int:post_id>', methods=['POST'])
@login_required
def recommend_ignore(post_id):
    # For now, just acknowledge. Optionally, store rejection in DB for future improvements.
    return jsonify({'success': True})


@app.route('/leave_team/<int:post_id>', methods=['POST'])
@login_required
def leave_team(post_id):
    post = db.session.get(Post, post_id)
    if not post:
        flash('Team not found.', 'danger')
        return redirect(url_for('dashboard'))
    if post.creator_id == current_user.id:
        flash('Creators cannot leave their own team.', 'warning')
        return redirect(url_for('dashboard'))
    if current_user not in post.teammates:
        flash('You are not a member of this team.', 'danger')
        return redirect(url_for('dashboard'))
    post.teammates.remove(current_user)
    # Remove the application if it exists (so user can re-apply)
    application = db.session.scalar(sa.select(Application).where(Application.post_id == post_id, Application.applicant_id == current_user.id))
    if application:
        db.session.delete(application)
    db.session.commit()
    flash('You have left the team.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/delete_post/<int:post_id>', methods=['POST'])
@login_required
def delete_post(post_id):
    post = db.session.get(Post, post_id)
    if not post or post.creator_id != current_user.id:
        flash('You are not authorized to delete this post.', 'danger')
        return redirect(url_for('index'))
    # Remove all applications for this post
    applications = db.session.scalars(sa.select(Application).where(Application.post_id == post_id)).all()
    for app in applications:
        db.session.delete(app)
    # Remove all teammates associations
    post.teammates.clear()
    db.session.delete(post)
    db.session.commit()
    flash('Post deleted successfully.', 'success')
    return redirect(url_for('user', username=current_user.username))
