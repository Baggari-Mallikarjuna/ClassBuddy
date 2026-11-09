from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import login_user, logout_user, login_required
from models.student import Student
from services.email_service import is_valid_email

auth_bp = Blueprint('auth', __name__)


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')
        confirm_password = request.form.get('confirm_password', '')

        if not name or not email or not password or not confirm_password:
            flash('All fields are required.', 'danger')
            return redirect(url_for('auth.register'))

        if len(name) > 100:
            flash('Name must be 100 characters or fewer.', 'danger')
            return redirect(url_for('auth.register'))

        if not is_valid_email(email):
            flash('Enter a valid email address.', 'danger')
            return redirect(url_for('auth.register'))

        if password != confirm_password:
            flash('Passwords do not match.', 'danger')
            return redirect(url_for('auth.register'))

        if len(password) < 8:
            flash('ClassBuddy passwords must be at least 8 characters.', 'danger')
            return redirect(url_for('auth.register'))

        if Student.find_by_email(email):
            flash('An account with this email already exists.', 'danger')
            return redirect(url_for('auth.register'))

        if Student.create(name, email, password) is None:
            flash('An account with this email already exists.', 'danger')
            return redirect(url_for('auth.register'))

        flash('Account created successfully. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('register.html')


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        password = request.form.get('password', '')

        student = Student.find_by_email(email)
        if student and student.verify_password(password):
            session.clear()
            login_user(student)
            return redirect(url_for('dashboard'))

        flash('Invalid email or password.', 'danger')
        return redirect(url_for('auth.login'))

    return render_template('login.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()
    flash('You have been logged out.', 'info')
    response = redirect(url_for('auth.login'))
    response.delete_cookie(
        current_app.config.get('REMEMBER_COOKIE_NAME', 'remember_token'),
        path=(
            current_app.config.get('REMEMBER_COOKIE_PATH')
            or current_app.config.get('SESSION_COOKIE_PATH')
            or '/'
        ),
        domain=current_app.config.get('REMEMBER_COOKIE_DOMAIN')
    )
    return response
