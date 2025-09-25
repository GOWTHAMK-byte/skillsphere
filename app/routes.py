from flask import render_template, redirect, url_for, flash, request, jsonify
from app.forms import LoginForm, RegisterForm, EditProfileForm, CreatePostForm, VerifySkillForm
from flask_login import current_user, login_user, logout_user, login_required
from app.models import User, Profile, Skill, Post, Application, post_required_skills, ChatMessage, ChatReadStatus, \
    ProfileSkill
from app import app, db, socketio
import sqlalchemy as sa
from werkzeug.utils import secure_filename
import os
import secrets
import json
from flask_socketio import join_room, leave_room, emit
from datetime import datetime, timezone
from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderUnavailable
from geopy.distance import great_circle
# --- MODIFIED IMPORTS ---
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np
import fitz  # PyMuPDF for handling PDFs
from PIL import Image
import pytesseract  # For Local OCR
import re
from sqlalchemy import case

# --- NEW: Context Processor to make notifications available globally ---
@app.context_processor
def inject_notifications():
    if not current_user.is_authenticated:
        return dict(notifications_count=0)

    # Find all post IDs where the user is a member
    user_post_ids = db.session.scalars(
        sa.select(Post.id)
        .where(sa.or_(Post.creator_id == current_user.id, Post.teammates.any(id=current_user.id)))
    ).all()

    if not user_post_ids:
        return dict(notifications_count=0)

    # Get all of the user's read statuses in one query
    read_statuses = db.session.scalars(
        sa.select(ChatReadStatus)
        .where(ChatReadStatus.user_id == current_user.id, ChatReadStatus.post_id.in_(user_post_ids))
    ).all()

    # Create a map for quick lookup: post_id -> last_read_timestamp
    last_read_map = {rs.post_id: rs.last_read for rs in read_statuses}

    total_unread = 0

    # Loop through each chat room and count unread messages
    for post_id in user_post_ids:
        last_read_time = last_read_map.get(post_id)

        query = sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post_id)

        # If the user has read this chat before, only count messages after that time
        if last_read_time:
            query = query.where(ChatMessage.timestamp > last_read_time)

        unread_count = db.session.scalar(query)
        total_unread += unread_count or 0

    return dict(notifications_count=total_unread)


def analyze_certificate_locally(file_stream, filename, user_name, skill_name):
    """
    Analyzes a certificate image/pdf locally using Tesseract OCR.
    """
    try:
        text = ''
        if filename.lower().endswith('.pdf'):
            with fitz.open(stream=file_stream.read(), filetype="pdf") as doc:
                if not doc:
                    return {'success': False, 'reason': 'The provided PDF document could not be read.'}
                page = doc.load_page(0)
                pix = page.get_pixmap()
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                text = pytesseract.image_to_string(img)
        elif filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            img = Image.open(file_stream)
            text = pytesseract.image_to_string(img)
        else:
            return {'success': False, 'reason': 'Unsupported file type. Please upload a PDF, PNG, or JPG.'}

        if not text.strip():
            return {'success': False,
                    'reason': 'No text could be detected on the document. Please try a clearer image.'}

        full_text = text.lower()

        user_name_parts = user_name.lower().split()
        first_name_found = user_name_parts[0] in full_text if user_name_parts else False

        checks = {
            "User Name": first_name_found,
            "Skill Name": skill_name.lower() in full_text,
            "Certificate Keywords": any(
                keyword in full_text for keyword in ['certificate', 'certified', 'completion', 'course', 'award'])
        }

        if all(checks.values()):
            return {'success': True, 'reason': 'All checks passed.'}
        else:
            missing = [key for key, passed in checks.items() if not passed]
            reason = f"the following could not be found on the document: {', '.join(missing)}. Please upload a clearer or more relevant document."
            return {'success': False, 'reason': reason}

    except Exception as e:
        app.logger.error(f"Local OCR (Tesseract) error: {e}")
        return {'success': False, 'reason': 'a system error occurred during document analysis.'}


def get_recommended_posts(user, limit=6):
    """
    Generates highly personalized post recommendations for a user, with proximity scoring.
    """
    # These dictionaries remain the same
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
        'skill': 4.0, 'complementary': 8.0, 'rarity': 2.0, 'event_type': 3.0,
        'recency': 1.5, 'location': 6.0, 'nlp': 10.0  # Increased location weight
    }

    # --- NLP Data Preparation (No Changes) ---
    all_posts_for_nlp = db.session.scalars(sa.select(Post)).all()
    user_applications_for_nlp = db.session.scalars(
        sa.select(Application).where(Application.applicant_id == user.id)).all()
    user_liked_texts = [(app.post.description or '') + ' ' + (app.post.idea or '') for app in user_applications_for_nlp
                        if app.post]
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
                if hasattr(user_taste_vector, 'toarray'): user_taste_vector = user_taste_vector.toarray()
                user_taste_vector = np.asarray(user_taste_vector)
        except ValueError:
            pass

    # --- User Profile Preparation ---
    user_skills = {assoc.skill.name.lower() for assoc in user.profile.skill_associations}
    is_new_user = not user_skills and not user_applications_for_nlp and not user.profile.bio
    if is_new_user:
        all_posts = db.session.scalars(
            sa.select(Post).where(Post.creator_id != user.id, Post.applications_closed == False).order_by(
                Post.timestamp.desc())).all()
        return all_posts[:5] if len(all_posts) >= 5 else all_posts

    user_gender = user.profile.gender
    user_event_types = {app.post.event_type for app in user_applications_for_nlp if app.post and app.post.event_type}
    user_posts_created = db.session.scalars(sa.select(Post).where(Post.creator_id == user.id)).all()
    for post in user_posts_created:
        if post.event_type: user_event_types.add(post.event_type)

    # NEW: Get user coordinates once before the loop
    user_coords = (user.profile.latitude,
                   user.profile.longitude) if user.profile and user.profile.latitude is not None and user.profile.longitude is not None else None

    # --- Scoring Logic ---
    applied_post_ids = {app.post_id for app in user_applications_for_nlp}
    teammate_post_ids = {post.id for post in user.teams}
    candidate_posts = db.session.scalars(
        sa.select(Post).where(Post.creator_id != user.id, Post.applications_closed == False)).unique().all()

    scored_posts = []
    for post in candidate_posts:
        if post.id in applied_post_ids or post.id in teammate_post_ids:
            continue
        if post.gender_requirement and post.gender_requirement != 'Any' and post.gender_requirement != user_gender:
            continue

        # --- Skill, Rarity, Complementary, Event, Recency, NLP scores (No Changes) ---
        required_skills = {skill.name.lower() for skill in post.required_skills}
        matched_skills = user_skills & required_skills
        skill_score = len(matched_skills)
        rarity_bonus = sum(1 for skill in matched_skills if db.session.scalar(
            sa.select(sa.func.count(post_required_skills.c.post_id)).join(Skill).where(Skill.name == skill)) < 5)
        user_skill_cats = {cat for cat, skills in SKILL_CATEGORIES.items() if any(s in user_skills for s in skills)}
        post_skill_cats = {cat for cat, skills in SKILL_CATEGORIES.items() if any(s in required_skills for s in skills)}
        missing_cats = post_skill_cats - user_skill_cats
        complementary_score = sum(1 for missing_cat in missing_cats if any(
            user_cat in COMPLEMENTARY_PAIRS and missing_cat in COMPLEMENTARY_PAIRS[user_cat] for user_cat in
            user_skill_cats))
        event_type_score = 1 if post.event_type in user_event_types else 0
        now_utc = datetime.now(timezone.utc)
        post_time = post.timestamp.replace(tzinfo=timezone.utc) if post.timestamp.tzinfo is None else post.timestamp
        recency_score = max(0, 1 - ((now_utc - post_time).days / 30))
        nlp_score = 0
        if user_taste_vector is not None and post.id in post_vectors:
            post_vector = post_vectors[post.id]
            if np.any(user_taste_vector) and np.any(post_vector.toarray()):
                nlp_score = cosine_similarity(user_taste_vector.reshape(1, -1), post_vector)[0][0]

        # --- REVISED: Location Scoring with Proximity ---
        location_score = 0
        post_coords = (
        post.latitude, post.longitude) if post.latitude is not None and post.longitude is not None else None

        if user_coords and post_coords:
            distance_km = great_circle(user_coords, post_coords).kilometers

            if distance_km <= 50:  # Very close / Same city
                location_score = 1.0
            elif distance_km <= 120:  # Neighboring city (e.g., Chennai to Vellore)
                location_score = 0.6
            elif distance_km <= 300:  # Reasonable travel distance
                location_score = 0.3

        # --- Final Score Calculation (No Changes) ---
        total_score = (
                (skill_score * WEIGHTS['skill']) +
                (complementary_score * WEIGHTS['complementary']) +
                (rarity_bonus * WEIGHTS['rarity']) +
                (event_type_score * WEIGHTS['event_type']) +
                (recency_score * WEIGHTS['recency']) +
                (location_score * WEIGHTS['location']) +
                (nlp_score * WEIGHTS['nlp'])
        )

        if total_score > 0:
            scored_posts.append((total_score, post.timestamp, post))

    # --- Final Filtering and Sorting (No Changes) ---
    scored_posts.sort(key=lambda x: x[0], reverse=True)
    seen_creators, unique_posts = {}, []
    for _, _, post in scored_posts:
        creator_id = post.creator_id
        if seen_creators.get(creator_id, 0) >= 2:
            continue
        unique_posts.append(post)
        seen_creators[creator_id] = seen_creators.get(creator_id, 0) + 1
        if len(unique_posts) >= limit:
            break
    if len(unique_posts) < limit and len(unique_posts) < len(scored_posts):
        remaining_posts = [post for _, _, post in scored_posts if post not in unique_posts]
        needed = limit - len(unique_posts)
        unique_posts.extend(remaining_posts[:needed])

    return unique_posts
# ... (routes from landing to messages remain unchanged) ...
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
    posts = db.session.scalars(sa.select(Post).order_by(Post.timestamp.desc())).unique().all()
    seen = set()
    unique_posts = []
    for p in posts:
        if p.id not in seen:
            unique_posts.append(p)
            seen.add(p.id)
    posts = unique_posts
    messages_summary = []
    if current_user.is_authenticated:
        chat_posts = db.session.scalars(sa.select(Post).where(
            sa.or_(Post.creator_id == current_user.id, Post.teammates.any(id=current_user.id)))).unique().all()
        for post in chat_posts:
            latest_msg = db.session.scalars(sa.select(ChatMessage).where(ChatMessage.post_id == post.id).order_by(
                ChatMessage.timestamp.desc())).first()
            read_status = db.session.scalar(sa.select(ChatReadStatus).where(ChatReadStatus.user_id == current_user.id,
                                                                            ChatReadStatus.post_id == post.id))
            last_read = read_status.last_read if read_status else None
            if last_read:
                unread_count = db.session.scalar(
                    sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id,
                                                                   ChatMessage.timestamp > last_read))
            else:
                unread_count = db.session.scalar(
                    sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id))
            messages_summary.append({'post': post, 'latest_msg': latest_msg, 'unread_count': unread_count or 0})
        messages_summary.sort(key=lambda c: c['latest_msg'].timestamp if c['latest_msg'] else datetime.min,
                              reverse=True)
        messages_summary = messages_summary[:3]
        app_rows = db.session.scalars(sa.select(Application).where(Application.applicant_id == current_user.id)).all()
        for app in app_rows:
            app_status[app.post_id] = app.status
    return render_template("index.html", title="Home", posts=posts, gender_filter=gender_filter, app_status=app_status,
                           recommended_mode=False, recommended_posts=[], messages_summary=messages_summary)


@app.route("/recommend")
@login_required
def recommend():
    gender_filter = request.args.get('gender')
    app_status = {}
    recommended_posts = get_recommended_posts(current_user)
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
    return render_template("index.html", title="Recommended", posts=[], gender_filter=gender_filter,
                           app_status=app_status, recommended_mode=True, recommended_posts=recommended_posts)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    form = LoginForm()
    if form.validate_on_submit():
        user = db.session.scalar(sa.select(User).where(User.username == form.username.data))
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
    posts = db.session.scalars(
        sa.select(Post).where(Post.creator_id == user.id).order_by(Post.timestamp.desc()).options(
            sa.orm.joinedload(Post.teammates))).unique().all()
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


@app.route('/api/skills/search')
@login_required
def search_skills():
    query = request.args.get('q', '')
    if not query:
        return jsonify([])
    results = db.session.scalars(sa.select(Skill.name).where(Skill.name.ilike(f'{query}%')).limit(10)).all()
    return jsonify(results)


@app.route('/edit_profile', methods=['GET', 'POST'])
@login_required
def edit_profile():
    form = EditProfileForm()
    if form.validate_on_submit():
        # --- Avatar handling ---
        if form.avatar.data:
            random_hex = secrets.token_hex(8)
            _, f_ext = os.path.splitext(form.avatar.data.filename)
            picture_fn = random_hex + f_ext
            picture_path = os.path.join(app.root_path, 'static/avatars', picture_fn)
            form.avatar.data.save(picture_path)
            current_user.profile.avatar_filename = picture_fn

        # --- Geocoding logic (this block is correct) ---
        # This check correctly prevents re-geocoding if the location hasn't changed.
        if current_user.profile.location != form.location.data:
            current_user.profile.location = form.location.data
            if form.location.data:
                try:
                    geolocator = Nominatim(user_agent="skillsphere_app")
                    location_data = geolocator.geocode(form.location.data)
                    if location_data:
                        current_user.profile.latitude = location_data.latitude
                        current_user.profile.longitude = location_data.longitude
                    else:
                        current_user.profile.latitude = None
                        current_user.profile.longitude = None
                except (GeocoderTimedOut, GeocoderUnavailable):
                    flash('Could not connect to the location service. Please try again later.', 'warning')
                    current_user.profile.latitude = None
                    current_user.profile.longitude = None
            else:
                current_user.profile.latitude = None
                current_user.profile.longitude = None

        # --- General profile data assignment ---
        current_user.profile.name = form.name.data
        current_user.profile.bio = form.bio.data
        current_user.profile.college = form.college.data
        current_user.profile.year = form.year.data
        current_user.profile.degree = form.degree.data
        current_user.profile.github_url = form.github_url.data
        current_user.profile.linkedin_url = form.linkedin_url.data
        # current_user.profile.location = form.location.data  <-- BUG: This redundant line has been removed.
        current_user.profile.gender = form.gender.data

        # --- Skills handling ---
        try:
            skills_data = json.loads(form.skills.data)
        except (json.JSONDecodeError, TypeError):
            skills_data = []
        current_skills = {assoc.skill.name for assoc in current_user.profile.skill_associations}
        form_skills = {item.get('value', '').strip() for item in skills_data if item.get('value', '').strip()}

        # Remove old skills
        for assoc in list(current_user.profile.skill_associations):
            if assoc.skill.name not in form_skills:
                db.session.delete(assoc)

        # Add new skills
        for skill_name in form_skills:
            if skill_name not in current_skills:
                skill = db.session.scalar(sa.select(Skill).where(Skill.name == skill_name))
                if not skill:
                    skill = Skill(name=skill_name)
                    db.session.add(skill)
                new_assoc = ProfileSkill(profile=current_user.profile, skill=skill)
                db.session.add(new_assoc)

        db.session.commit()
        flash('Your changes have been saved.')
        return redirect(url_for('user', username=current_user.username))

    # --- GET request logic (remains the same) ---
    elif request.method == 'GET':
        form.name.data = current_user.profile.name
        form.bio.data = current_user.profile.bio
        form.college.data = current_user.profile.college
        form.year.data = current_user.profile.year
        form.degree.data = current_user.profile.degree
        form.github_url.data = current_user.profile.github_url
        form.linkedin_url.data = current_user.profile.linkedin_url
        form.location.data = current_user.profile.location
        form.gender.data = current_user.profile.gender

    return render_template("edit_profile.html", title='Edit Profile', form=form)


@app.route('/create_post', methods=['GET', 'POST'])
@login_required
def create_post():
    form = CreatePostForm()
    if form.validate_on_submit():
        poster_filename = None
        if form.event_poster.data:
            random_hex = secrets.token_hex(8)
            _, f_ext = os.path.splitext(form.event_poster.data.filename)
            poster_filename = random_hex + f_ext
            poster_path = os.path.join(app.root_path, 'static/posters', poster_filename)
            form.event_poster.data.save(poster_path)

        try:
            team_size = int(form.team_size.data or 0)
            male_slots = int(form.male_slots.data or 0)
            female_slots = int(form.female_slots.data or 0)
        except (ValueError, TypeError):
            team_size, male_slots, female_slots = 0, 0, 0

        if team_size > 0 and (male_slots + female_slots != team_size):
            flash(
                f'The sum of male and female slots ({male_slots} + {female_slots}) must equal the team size ({team_size}).',
                'danger')
            return render_template('create_post.html', title='Create a New Post', form=form)

        # Create the Post object, now including the location from the form
        post = Post(event_name=form.event_name.data,
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
                    female_slots=female_slots,
                    location=form.location.data)  # Added location field

        # --- NEW: Geocode the post's location ---
        if post.location:
            try:
                geolocator = Nominatim(user_agent="skillsphere_app")
                location_data = geolocator.geocode(post.location)
                if location_data:
                    post.latitude = location_data.latitude
                    post.longitude = location_data.longitude
                else:
                    # If location is not found, nullify coordinates
                    post.latitude = None
                    post.longitude = None
            except (GeocoderTimedOut, GeocoderUnavailable):
                flash('Could not connect to the location service. Post created without location data.', 'warning')
                post.latitude = None
                post.longitude = None

        # Add required skills to the post
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
    existing = db.session.scalar(
        sa.select(Application).where(Application.post_id == post_id, Application.applicant_id == current_user.id))
    if existing:
        flash('You have already applied to this post.', 'warning')
        return redirect(url_for('index'))
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
    if request.method == 'POST':
        if 'close_applications' in request.form:
            post.applications_closed = True
            db.session.commit()
            flash('Applications have been closed.', 'success')
        elif 'open_applications' in request.form:
            post.applications_closed = False
            db.session.commit()
            flash('Applications have been reopened.', 'success')
        else:
            app_id = request.form.get('app_id')
            action = request.form.get('action')
            application = db.session.get(Application, int(app_id)) if app_id else None
            if application and application.post_id == post_id:
                if action == 'accept':
                    application.status = 'Accepted'
                    if application.applicant not in post.teammates:
                        post.teammates.append(application.applicant)
                    for required_skill in post.required_skills:
                        assoc = db.session.scalars(
                            sa.select(ProfileSkill).where(ProfileSkill.profile_id == application.applicant.profile.id,
                                                          ProfileSkill.skill_id == required_skill.id)).first()
                        if assoc:
                            assoc.level += 1
                    db.session.commit()
                    flash(f'Applicant has been accepted.', 'success')
                elif action == 'reject':
                    application.status = 'Rejected'
                    db.session.commit()
                    flash(f'Applicant has been rejected.', 'success')
        return redirect(url_for('manage_post', post_id=post_id))
    applications = db.session.scalars(sa.select(Application).where(Application.post_id == post_id)).all()
    recommended_users = recommend_users_for_post(post)
    return render_template('manage_post.html', post=post, applications=applications,
                           recommended_users=recommended_users)

@app.route('/invite/<int:post_id>/<int:user_id>', methods=['POST'])
@login_required
def invite_user(post_id, user_id):
    """Handles the logic for a post creator inviting a recommended user."""
    post = db.session.get(Post, post_id)
    recommended_user = db.session.get(User, user_id)

    # Authorization checks
    if not post or not recommended_user:
        flash('Post or user not found.', 'danger')
        return redirect(url_for('index'))
    if post.creator_id != current_user.id:
        flash('You are not authorized to invite users to this post.', 'danger')
        return redirect(url_for('manage_post', post_id=post_id))

    # Check if the user has already applied or been invited
    existing_application = db.session.scalar(
        sa.select(Application).where(
            Application.post_id == post_id,
            Application.applicant_id == user_id
        )
    )

    if existing_application:
        flash(f'{recommended_user.username} has already applied or been invited.', 'warning')
        return redirect(url_for('manage_post', post_id=post_id))

    # Create an 'Invited' application on behalf of the user
    invitation = Application(
        post_id=post.id,
        applicant_id=recommended_user.id,
        status='Invited'
    )
    db.session.add(invitation)
    db.session.commit()

    flash(f'{recommended_user.username} has been invited to join the team!', 'success')
    return redirect(url_for('manage_post', post_id=post_id))


@app.route('/handle_invitation/<int:application_id>/<action>', methods=['POST'])
@login_required
def handle_invitation(application_id, action):
    """Handles a user accepting or rejecting a post invitation."""
    application = db.session.get(Application, application_id)

    # Security check: Ensure the application exists and belongs to the current user
    if not application or application.applicant_id != current_user.id:
        flash('Invalid invitation.', 'danger')
        return redirect(url_for('dashboard'))

    # Ensure the action is only taken on an 'Invited' status
    if application.status != 'Invited':
        flash('This invitation has already been responded to.', 'warning')
        return redirect(url_for('dashboard'))

    post = application.post
    if action == 'accept':
        application.status = 'Accepted'
        if current_user not in post.teammates:
            post.teammates.append(current_user)
        flash(f'You have successfully joined the team for "{post.event_name}"!', 'success')

    elif action == 'reject':
        application.status = 'Rejected'
        flash(f'You have rejected the invitation to "{post.event_name}".', 'info')

    else:
        flash('Invalid action.', 'danger')

    db.session.commit()
    return redirect(url_for('dashboard'))


@app.route('/leaderboard')
@login_required
def leaderboard():
    """Displays a leaderboard of users ranked by skill categories."""
    SKILL_CATEGORIES = {
        'Frontend': ['react', 'angular', 'vue', 'javascript', 'html', 'css', 'typescript', 'svelte'],
        'Backend': ['python', 'java', 'node.js', 'ruby', 'php', 'go', 'flask', 'django', 'express.js'],
        'Database': ['sql', 'mysql', 'postgresql', 'mongodb', 'redis'],
        'DevOps': ['docker', 'kubernetes', 'aws', 'gcp', 'azure', 'jenkins', 'ci/cd'],
        'Mobile': ['swift', 'kotlin', 'react native', 'flutter', 'ios', 'android'],
        'Data Science': ['machine learning', 'tensorflow', 'pytorch', 'pandas', 'numpy', 'scikit-learn'],
        'Design': ['figma', 'adobe xd', 'sketch', 'ui', 'ux', 'prototyping']
    }

    active_category = request.args.get('category', 'Frontend')
    category_skills = SKILL_CATEGORIES.get(active_category, [])

    ranked_users = []
    if category_skills:
        score_formula = sa.func.sum(
            ProfileSkill.level + case((ProfileSkill.is_verified, 5), else_=0)
        ).label('total_score')

        subquery = sa.select(
            Profile.user_id,
            score_formula
        ).join(Profile.skill_associations).join(ProfileSkill.skill).where(
            # --- THIS IS THE FIX ---
            # We now convert the skill name to lowercase before checking it.
            sa.func.lower(Skill.name).in_(category_skills)
        ).group_by(Profile.user_id).subquery()

        ranked_users_query = sa.select(User, subquery.c.total_score) \
            .join(subquery, User.id == subquery.c.user_id) \
            .order_by(sa.desc(subquery.c.total_score)) \
            .limit(50)

        ranked_users = db.session.execute(ranked_users_query).all()

    return render_template('leaderboard.html',
                           title='Leaderboard',
                           ranked_users=ranked_users,
                           categories=SKILL_CATEGORIES.keys(),
                           active_category=active_category)

def recommend_users_for_post(post, limit=5):
    """Recommends users for a post, now with location scoring."""
    required_skills = {skill.name.lower() for skill in post.required_skills}
    teammate_ids = {user.id for user in post.teammates}
    applicant_ids = {app.applicant_id for app in post.applications}
    exclude_ids = teammate_ids | applicant_ids | {post.creator_id}

    # Prepare post's location for comparison
    post_location = (post.location or '').strip().lower()
    post_coords = (post.latitude, post.longitude) if post.latitude and post.longitude else None

    candidates = db.session.scalars(sa.select(User).where(User.id.notin_(exclude_ids))).all()
    scored_users = []

    for user in candidates:
        if not user.profile:
            continue

        # --- Skill Scoring (Existing Logic) ---
        user_skill_data = {assoc.skill.name.lower(): assoc for assoc in user.profile.skill_associations}
        skill_score = 0
        for req_skill_name in required_skills:
            if req_skill_name in user_skill_data:
                assoc = user_skill_data[req_skill_name]
                skill_score += 2  # Base score for having the skill
                if assoc.is_verified:
                    skill_score += 5  # Bonus for verified skill
                skill_score += assoc.level  # Bonus for skill level

        # --- NEW: Location Scoring ---
        location_score = 0
        user_location = (user.profile.location or '').strip().lower()
        user_coords = (
        user.profile.latitude, user.profile.longitude) if user.profile.latitude and user.profile.longitude else None

        if post_coords and user_coords:
            distance_km = great_circle(post_coords, user_coords).kilometers

            if distance_km <= 50:  # Within the same city or very close
                location_score = 10
            elif distance_km <= 100:  # Neighboring city (like Chennai to Kanchipuram)
                location_score = 5

        # --- Total Score Calculation ---
        total_score = skill_score + location_score

        if total_score > 0:
            scored_users.append((total_score, user))

    # Sort candidates by their total score in descending order
    scored_users.sort(reverse=True, key=lambda x: x[0])

    # Return the top N recommended users
    return [user for score, user in scored_users[:limit]]

@socketio.on('drawing', namespace='/chat')
@login_required
def handle_drawing(data):
    """
    Handles whiteboard drawing data from a client and broadcasts it to the room.
    """
    post_id = data.get('post_id')
    if not post_id:
        return

    # Broadcast the drawing data to all clients in the room except the sender
    emit('drawing', data, to=str(post_id), skip_sid=request.sid)


@socketio.on('clear_canvas', namespace='/chat')
@login_required
def handle_clear_canvas(data):
    """
    Handles a request to clear the whiteboard and broadcasts it to the room.
    """
    post_id = data.get('post_id')
    if not post_id:
        return

    # Broadcast the clear event to all clients in the room except the sender
    emit('clear_canvas', to=str(post_id), skip_sid=request.sid)

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
        skill_subq = sa.select(post_required_skills.c.post_id).join(Skill,
                                                                    Skill.id == post_required_skills.c.skill_id).where(
            Skill.name.ilike(f'%{q}%'))
        filters.append(
            sa.or_(Post.event_name.ilike(f'%{q}%'), Post.description.ilike(f'%{q}%'), Post.id.in_(skill_subq)))
    if event_type: filters.append(Post.event_type == event_type)
    if team_size:
        try:
            filters.append(Post.team_size == int(team_size))
        except ValueError:
            pass
    if filters: posts_query = posts_query.where(sa.and_(*filters))
    posts_query = posts_query.order_by(Post.timestamp.desc())
    posts = db.session.scalars(posts_query).unique().all()
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
    return render_template("index.html", title="Search Results", search_mode=True, search_results=posts, posts=[],
                           gender_filter=None, app_status=app_status, recommended_mode=False, recommended_posts=[])


@app.route('/dashboard')
@login_required
def dashboard():
    teams = db.session.scalars(sa.select(Post).where(
        sa.or_(Post.creator_id == current_user.id, Post.teammates.any(id=current_user.id))).order_by(
        Post.timestamp.desc()).options(sa.orm.joinedload(Post.teammates),
                                       sa.orm.joinedload(Post.creator))).unique().all()
    all_applications = db.session.scalars(
        sa.select(Application).where(Application.applicant_id == current_user.id).options(
            sa.orm.joinedload(Application.post).joinedload(Post.creator))).all()
    applications = [app for app in all_applications if
                    not (app.status == 'Accepted' and current_user in app.post.teammates)]
    return render_template('dashboard.html', teams=teams, applications=applications)


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
        sa.select(ChatMessage).where(ChatMessage.post_id == post_id).order_by(ChatMessage.timestamp)).all()
    read_status = db.session.scalar(
        sa.select(ChatReadStatus).where(ChatReadStatus.user_id == current_user.id, ChatReadStatus.post_id == post_id))
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

    # Authorization check
    if not post or (current_user not in post.teammates and current_user != post.creator):
        emit('error', {'message': 'Unauthorized'}, room=request.sid)
        return

    if content:
        msg = ChatMessage(post_id=post_id, sender_id=current_user.id, content=content)
        db.session.add(msg)
        db.session.commit()

        # MODIFIED: Added 'avatar_url' to the payload
        emit('receive_message',
             {'username': current_user.username,
              'content': content,
              'timestamp': msg.timestamp.strftime('%b %d, %I:%M %p'),
              'sender_id': current_user.id,
              'avatar_url': current_user.profile.avatar_url},
             to=str(post_id),
             skip_sid=request.sid)


@app.route('/messages')
@login_required
def messages():
    posts = db.session.scalars(sa.select(Post).where(
        sa.or_(Post.creator_id == current_user.id, Post.teammates.any(id=current_user.id))).order_by(
        Post.timestamp.desc())).unique().all()
    chat_data = []
    rooms = []
    unread_counts = {}
    for post in posts:
        rooms.append(post.id)
        latest_msg = db.session.scalars(
            sa.select(ChatMessage).where(ChatMessage.post_id == post.id).order_by(ChatMessage.timestamp.desc())).first()
        read_status = db.session.scalar(sa.select(ChatReadStatus).where(ChatReadStatus.user_id == current_user.id,
                                                                        ChatReadStatus.post_id == post.id))
        last_read = read_status.last_read if read_status else None
        if last_read:
            unread_count = db.session.scalar(
                sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id,
                                                               ChatMessage.timestamp > last_read))
        else:
            unread_count = db.session.scalar(
                sa.select(sa.func.count(ChatMessage.id)).where(ChatMessage.post_id == post.id))
        unread_counts[post.id] = unread_count or 0
        chat_data.append({'post': post, 'latest_msg': latest_msg, 'unread_count': unread_count or 0})
    if request.args.get('json') == '1':
        return jsonify({'rooms': rooms, 'unread_counts': unread_counts})
    return render_template('messages.html', chat_data=chat_data)


@app.route('/verify_skill/<int:skill_id>', methods=['GET', 'POST'])
@login_required
def verify_skill(skill_id):
    skill = db.session.get(Skill, skill_id)
    if not skill:
        flash('Skill not found.', 'danger')
        return redirect(url_for('user', username=current_user.username))
    assoc = db.session.scalars(sa.select(ProfileSkill).where(ProfileSkill.profile_id == current_user.profile.id,
                                                             ProfileSkill.skill_id == skill_id)).first()
    if not assoc:
        flash("You must add the skill to your profile first.", "warning")
        return redirect(url_for('edit_profile'))
    form = VerifySkillForm()
    completed_events = db.session.scalars(sa.select(Post).where(Post.teammates.any(id=current_user.id))).all()
    form.event_id.choices = [(p.id, p.event_name) for p in completed_events]
    form.event_id.choices.insert(0, (0, 'Select a completed event...'))

    if form.validate_on_submit():
        proof_type = form.proof_type.data
        if proof_type == 'certificate' and form.certificate_file.data:
            certificate_file = form.certificate_file.data
            user_name_on_cert = current_user.profile.name or current_user.username

            analysis_result = analyze_certificate_locally(certificate_file, certificate_file.filename,
                                                          user_name_on_cert, skill.name)

            if analysis_result['success']:
                if not assoc.is_verified:
                    assoc.level += 1
                assoc.is_verified = True
                random_hex = secrets.token_hex(8)
                _, f_ext = os.path.splitext(certificate_file.filename)
                cert_fn = random_hex + f_ext
                cert_path = os.path.join(app.root_path, 'static/certificates', cert_fn)
                os.makedirs(os.path.dirname(cert_path), exist_ok=True)

                certificate_file.seek(0)
                certificate_file.save(cert_path)

                assoc.proof_link = cert_fn
                assoc.proof_type = 'certificate'
                db.session.commit()
                flash(f'Verification successful! Your skill "{skill.name}" is now verified.', 'success')
                return redirect(url_for('user', username=current_user.username))
            else:
                # --- MODIFICATION: Rerender template with rejection message instead of flashing ---
                rejection_reason = analysis_result['reason']
                return render_template('verify_skill.html',
                                       title=f'Verify Skill: {skill.name}',
                                       form=form,
                                       skill=skill,
                                       rejection_reason=rejection_reason)

        # This part handles project and event verification
        if not assoc.is_verified:
            assoc.level += 1
        assoc.is_verified = True
        assoc.proof_type = proof_type
        if proof_type == 'project' and form.project_url.data:
            assoc.proof_link = form.project_url.data
        elif proof_type == 'event' and form.event_id.data > 0:
            event_post = db.session.get(Post, form.event_id.data)
            if event_post:
                assoc.proof_link = url_for('manage_post', post_id=event_post.id, _external=True)

        db.session.commit()
        flash(f'Verification submitted for {skill.name}!', 'info')
        return redirect(url_for('user', username=current_user.username))

    return render_template('verify_skill.html', title=f'Verify Skill: {skill.name}', form=form, skill=skill)


@app.route('/api/detect-skills', methods=['POST'])
@login_required
def detect_skills_from_file():
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file part'}), 400
    file = request.files.get('file')
    if not file or file.filename == '':
        return jsonify({'success': False, 'error': 'No selected file'}), 400
    text = ''
    try:
        if file.filename.lower().endswith('.pdf'):
            with fitz.open(stream=file.read(), filetype="pdf") as doc:
                for page in doc: text += page.get_text()
        elif file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            image = Image.open(file.stream)
            text = pytesseract.image_to_string(image)
        else:
            return jsonify({'success': False, 'error': 'Unsupported file type'}), 400
        if not text.strip():
            return jsonify({'success': False, 'error': 'Could not extract any text.'}), 400
        all_skills_from_db = db.session.scalars(sa.select(Skill.name)).all()
        known_skill_set = {skill.lower() for skill in all_skills_from_db}
        found_skills = set()
        processed_text = ' ' + text.lower().replace('\n', ' ') + ' '
        for skill in known_skill_set:
            if re.search(r'\b' + re.escape(skill) + r'\b', processed_text):
                original_casing_skill = next((s for s in all_skills_from_db if s.lower() == skill), skill)
                found_skills.add(original_casing_skill)
        return jsonify({'success': True, 'skills': sorted(list(found_skills))})
    except Exception as e:
        app.logger.error(f"Error in skill detection: {e}")
        return jsonify({'success': False, 'error': 'An error occurred during file processing.'}), 500


@app.route('/api/recommend/apply/<int:post_id>', methods=['POST'])
@login_required
def recommend_apply(post_id):
    post = db.session.get(Post, post_id)
    if not post:
        return jsonify({'success': False, 'error': 'Post not found'}), 404
    existing = db.session.scalar(
        sa.select(Application).where(Application.post_id == post_id, Application.applicant_id == current_user.id))
    if not existing:
        application = Application(post_id=post_id, applicant_id=current_user.id)
        db.session.add(application)
        db.session.commit()
    return jsonify({'success': True})


@app.route('/api/recommend/ignore/<int:post_id>', methods=['POST'])
@login_required
def recommend_ignore(post_id):
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
    application = db.session.scalar(
        sa.select(Application).where(Application.post_id == post_id, Application.applicant_id == current_user.id))
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
    applications = db.session.scalars(sa.select(Application).where(Application.post_id == post_id)).all()
    for app in applications:
        db.session.delete(app)
    post.teammates.clear()
    db.session.delete(post)
    db.session.commit()
    flash('Post deleted successfully.', 'success')
    return redirect(url_for('user', username=current_user.username))