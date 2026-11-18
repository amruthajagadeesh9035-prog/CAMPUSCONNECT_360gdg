
import os
import io
import sqlite3
import secrets
from functools import wraps

import qrcode

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    flash,
    session,
    send_file
)

from database import create_tables, get_db_connection

from werkzeug.security import (
    generate_password_hash,
    check_password_hash
)


app = Flask(__name__)

app.secret_key = os.environ.get(
    "SECRET_KEY",
    "local-development-only-change-this-key"
)

# Create database tables
create_tables()


# --------------------------------------------------
# CLOUD ADMIN INITIALIZATION
# --------------------------------------------------

def initialize_admin():

    admin_username = os.environ.get("ADMIN_USERNAME")
    admin_password = os.environ.get("ADMIN_PASSWORD")

    if not admin_username or not admin_password:
        print("Admin environment variables are not configured.")
        return

    if len(admin_password) < 8:
        print("Admin password must contain at least 8 characters.")
        return

    conn = get_db_connection()

    try:
        hashed_password = generate_password_hash(admin_password)

        existing_admin = conn.execute(
            "SELECT id FROM admins WHERE username = ?",
            (admin_username,)
        ).fetchone()

        if existing_admin is None:

            conn.execute(
                """INSERT INTO admins (username, password)
                   VALUES (?, ?)""",
                (admin_username, hashed_password)
            )

            print("Cloud admin account created successfully!")

        else:

            conn.execute(
                """UPDATE admins
                   SET password = ?
                   WHERE username = ?""",
                (hashed_password, admin_username)
            )

            print("Cloud admin password updated successfully!")

        conn.commit()

    finally:
        conn.close()


initialize_admin()


# --------------------------------------------------
# ADMIN ACCESS PROTECTION
# --------------------------------------------------

def admin_required(route_function):

    @wraps(route_function)
    def wrapper(*args, **kwargs):

        if "admin_id" not in session:
            flash("Please login as admin first.")
            return redirect(url_for("admin_login"))

        return route_function(*args, **kwargs)

    return wrapper


# --------------------------------------------------
# HOME PAGE
# --------------------------------------------------

@app.route("/")
def home():

    return render_template("index.html")


# --------------------------------------------------
# STUDENT REGISTRATION
# --------------------------------------------------

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        name = request.form["name"].strip()
        email = request.form["email"].strip()
        password = request.form["password"]

        if not name or not email:
            flash("Please enter your name and email.")
            return redirect(url_for("register"))

        if len(password) < 8:
            flash("Password must contain at least 8 characters.")
            return redirect(url_for("register"))

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()

        try:

            conn.execute(
                """INSERT INTO students
                   (name, email, password, registered_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
                (name, email, hashed_password)
            )

            conn.commit()

            flash("Registration successful! Please login.")

            return redirect(url_for("login"))

        except sqlite3.IntegrityError:

            flash("This email is already registered.")

        finally:
            conn.close()

    return render_template("register.html")


# --------------------------------------------------
# STUDENT LOGIN
# --------------------------------------------------

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        email = request.form["email"].strip()
        password = request.form["password"]

        conn = get_db_connection()

        student = conn.execute(
            "SELECT * FROM students WHERE email = ?",
            (email,)
        ).fetchone()

        conn.close()

        if student and check_password_hash(
            student["password"], password
        ):

            session.clear()

            session["student_id"] = student["id"]
            session["student_name"] = student["name"]

            flash("Student login successful!")

            return redirect(url_for("student_dashboard"))

        flash("Invalid email or password.")

    return render_template("login.html")


# --------------------------------------------------
# ADMIN ADD EVENT
# --------------------------------------------------

@app.route("/add-event", methods=["GET", "POST"])
@admin_required
def add_event():

    if request.method == "POST":

        title = request.form["title"]
        description = request.form["description"]
        event_date = request.form["event_date"]
        location = request.form["location"]

        # Generate a unique QR attendance token
        attendance_token = secrets.token_urlsafe(32)

        conn = get_db_connection()

        try:

            conn.execute(
                """INSERT INTO events
                   (title, description, event_date, location,
                    attendance_token)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    title,
                    description,
                    event_date,
                    location,
                    attendance_token
                )
            )

            conn.commit()

        finally:
            conn.close()

        flash("Event added successfully with QR code!")

        return redirect(url_for("events"))

    return render_template("add_event.html")


# --------------------------------------------------
# VIEW CAMPUS EVENTS
# --------------------------------------------------

@app.route("/events")
def events():

    conn = get_db_connection()

    all_events = conn.execute(
        "SELECT * FROM events ORDER BY event_date"
    ).fetchall()

    conn.close()

    return render_template(
        "events.html",
        events=all_events
    )


# --------------------------------------------------
# ADMIN EVENT QR CODE PAGE
# --------------------------------------------------

@app.route("/admin-event-qr/<int:event_id>")
@admin_required
def admin_event_qr(event_id):

    conn = get_db_connection()

    event = conn.execute(
        "SELECT * FROM events WHERE id = ?",
        (event_id,)
    ).fetchone()

    conn.close()

    if event is None:

        flash("Event not found.")
        return redirect(url_for("events"))

    if not event["attendance_token"]:

        return "QR token is missing for this event.", 400

    # Generate the student check-in URL
    qr_url = url_for(
        "event_checkin",
        token=event["attendance_token"],
        _external=True
    )

    return render_template(
        "event_qr.html",
        event=event,
        qr_url=qr_url
    )


# --------------------------------------------------
# GENERATE QR CODE IMAGE
# --------------------------------------------------

@app.route("/event-qr-image/<int:event_id>")
@admin_required
def event_qr_image(event_id):

    conn = get_db_connection()

    event = conn.execute(
        "SELECT * FROM events WHERE id = ?",
        (event_id,)
    ).fetchone()

    conn.close()

    if event is None:
        return "Event not found.", 404

    if not event["attendance_token"]:
        return "QR token is missing.", 400

    qr_url = url_for(
        "event_checkin",
        token=event["attendance_token"],
        _external=True
    )

    # Generate QR image
    qr = qrcode.make(qr_url)

    image_buffer = io.BytesIO()

    qr.save(image_buffer, format="PNG")

    image_buffer.seek(0)

    return send_file(
        image_buffer,
        mimetype="image/png"
    )


# --------------------------------------------------
# STUDENT EVENT QR CHECK-IN
# --------------------------------------------------

@app.route("/event-checkin/<token>", methods=["GET", "POST"])
def event_checkin(token):

    if "student_id" not in session:

        flash("Please login before checking in.")

        return redirect(url_for("login"))

    student_id = session["student_id"]

    conn = get_db_connection()

    try:

        event = conn.execute(
            """SELECT * FROM events
               WHERE attendance_token = ?""",
            (token,)
        ).fetchone()

        if event is None:
            return "Invalid event QR code.", 404

        # Check student event registration
        registration = conn.execute(
            """SELECT id FROM event_registrations
               WHERE student_id = ?
               AND event_id = ?""",
            (student_id, event["id"])
        ).fetchone()

        if registration is None:
            return (
                "Please register for this event before checking in.",
                403
            )

        # Check existing attendance
        existing = conn.execute(
            """SELECT id FROM attendance
               WHERE student_id = ?
               AND event_id = ?""",
            (student_id, event["id"])
        ).fetchone()

        if request.method == "POST":

            if existing:

                flash("Attendance has already been recorded.")

            else:

                conn.execute(
                    """INSERT INTO attendance
                       (student_id, event_id, status)
                       VALUES (?, ?, 'Present')""",
                    (student_id, event["id"])
                )

                conn.commit()

                flash("Your event attendance has been recorded!")

            return redirect(url_for("achievements"))

        return render_template(
            "event_checkin.html",
            event=event,
            already_marked=existing is not None
        )

    finally:
        conn.close()


# --------------------------------------------------
# STUDENT LEADERBOARD AND ANALYTICS
# --------------------------------------------------

@app.route("/leaderboard")
def leaderboard():

    conn = get_db_connection()

    try:

        students = conn.execute("""
            SELECT
                students.id AS student_id,
                students.name AS student_name,

                COUNT(DISTINCT event_registrations.event_id)
                    AS events_registered,

                COUNT(DISTINCT CASE
                    WHEN attendance.status = 'Present'
                    THEN attendance.event_id
                END) AS events_joined,

                (
                    COUNT(DISTINCT event_registrations.event_id) * 5
                    +
                    COUNT(DISTINCT CASE
                        WHEN attendance.status = 'Present'
                        THEN attendance.event_id
                    END) * 10
                ) AS points

            FROM students

            LEFT JOIN event_registrations
                ON students.id = event_registrations.student_id

            LEFT JOIN attendance
                ON students.id = attendance.student_id

            GROUP BY students.id, students.name

            ORDER BY points DESC,
                     events_joined DESC,
                     students.name ASC

        """).fetchall()

        total_students = conn.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]

        total_events = conn.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]

        total_registrations = conn.execute(
            "SELECT COUNT(*) FROM event_registrations"
        ).fetchone()[0]

        total_attendance = conn.execute("""
            SELECT COUNT(*)
            FROM attendance
            WHERE status = 'Present'
        """).fetchone()[0]

        total_points = sum(
            student["points"] for student in students
        )

        top_student = students[0] if students else None

        chart_labels = [
            student["student_name"] for student in students
        ]

        chart_points = [
            student["points"] for student in students
        ]

        monthly_data = conn.execute("""
            SELECT
                strftime('%Y-%m', registration_date) AS month,
                COUNT(*) AS total

            FROM event_registrations

            WHERE registration_date IS NOT NULL

            GROUP BY month

            ORDER BY month ASC
        """).fetchall()

        monthly_labels = [
            row["month"] for row in monthly_data
        ]

        monthly_counts = [
            row["total"] for row in monthly_data
        ]

        event_data = conn.execute("""
            SELECT
                events.title AS event_name,
                COUNT(event_registrations.id) AS total

            FROM events

            LEFT JOIN event_registrations
                ON events.id = event_registrations.event_id

            GROUP BY events.id, events.title

            ORDER BY total DESC
        """).fetchall()

        event_labels = [
            row["event_name"] for row in event_data
        ]

        event_counts = [
            row["total"] for row in event_data
        ]

        return render_template(
            "leaderboard.html",

            students=students,

            total_students=total_students,
            total_events=total_events,
            total_registrations=total_registrations,
            total_attendance=total_attendance,
            total_points=total_points,

            top_student=top_student,

            chart_labels=chart_labels,
            chart_points=chart_points,

            monthly_labels=monthly_labels,
            monthly_counts=monthly_counts,

            event_labels=event_labels,
            event_counts=event_counts
        )

    finally:
        conn.close()


# --------------------------------------------------
# STUDENT ATTENDANCE PAGE
# --------------------------------------------------

@app.route("/attendance")
def attendance():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    all_events = conn.execute(
        "SELECT * FROM events ORDER BY event_date"
    ).fetchall()

    conn.close()

    return render_template(
        "attendance.html",
        events=all_events
    )


# --------------------------------------------------
# DELETE EVENT
# --------------------------------------------------

@app.route("/delete-event/<int:event_id>", methods=["POST"])
@admin_required
def delete_event(event_id):

    conn = get_db_connection()

    try:

        conn.execute(
            "DELETE FROM attendance WHERE event_id = ?",
            (event_id,)
        )

        conn.execute(
            "DELETE FROM event_registrations WHERE event_id = ?",
            (event_id,)
        )

        conn.execute(
            "DELETE FROM events WHERE id = ?",
            (event_id,)
        )

        conn.commit()

    finally:
        conn.close()

    flash("Event deleted successfully!")

    return redirect(url_for("events"))


# --------------------------------------------------
# MANUAL MARK ATTENDANCE
# --------------------------------------------------

@app.route("/mark-attendance", methods=["POST"])
def mark_attendance():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    student_id = session["student_id"]
    event_id = request.form["event_id"]

    conn = get_db_connection()

    try:

        event = conn.execute(
            "SELECT id FROM events WHERE id = ?",
            (event_id,)
        ).fetchone()

        if event:

            existing = conn.execute(
                """SELECT id FROM attendance
                   WHERE student_id = ?
                   AND event_id = ?""",
                (student_id, event_id)
            ).fetchone()

            if existing:

                flash("You have already marked attendance for this event.")

            else:

                conn.execute(
                    """INSERT INTO attendance
                       (student_id, event_id, status)
                       VALUES (?, ?, 'Present')""",
                    (student_id, event_id)
                )

                conn.commit()

                flash("Attendance marked successfully!")

        else:

            flash("Invalid event.")

    finally:
        conn.close()

    return redirect(url_for("attendance"))


# --------------------------------------------------
# ADMIN ATTENDANCE REPORT
# --------------------------------------------------

@app.route("/admin-attendance")
@admin_required
def admin_attendance():

    conn = get_db_connection()

    records = conn.execute("""
        SELECT
            students.name,
            students.email,
            events.title,
            attendance.status

        FROM attendance

        JOIN students
            ON attendance.student_id = students.id

        JOIN events
            ON attendance.event_id = events.id

        ORDER BY events.event_date DESC

    """).fetchall()

    conn.close()

    return render_template(
        "admin_attendance.html",
        records=records
    )


# --------------------------------------------------
# STUDENT CERTIFICATE PAGE
# --------------------------------------------------

@app.route("/certificates")
def certificates():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    student_certificates = conn.execute("""
        SELECT
            students.name,
            events.title,
            events.event_date,
            'C360-' || attendance.id AS certificate_id

        FROM attendance

        JOIN students
            ON attendance.student_id = students.id

        JOIN events
            ON attendance.event_id = events.id

        WHERE attendance.student_id = ?
          AND attendance.status = 'Present'

        ORDER BY events.event_date DESC

    """, (session["student_id"],)).fetchall()

    conn.close()

    return render_template(
        "certificate.html",
        certificates=student_certificates
    )


# --------------------------------------------------
# ADMIN DASHBOARD
# --------------------------------------------------

@app.route("/admin-dashboard")
@admin_required
def admin_dashboard():

    conn = get_db_connection()

    try:

        total_students = conn.execute(
            "SELECT COUNT(*) FROM students"
        ).fetchone()[0]

        total_events = conn.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]

        total_attendance = conn.execute(
            "SELECT COUNT(*) FROM attendance"
        ).fetchone()[0]

        total_certificates = conn.execute(
            """SELECT COUNT(*) FROM attendance
               WHERE status = 'Present'"""
        ).fetchone()[0]

        recent_students = conn.execute("""
            SELECT id, name, email, registered_at
            FROM students
            ORDER BY id DESC
            LIMIT 5
        """).fetchall()

        upcoming_events = conn.execute("""
            SELECT * FROM events
            WHERE event_date >= date('now')
            ORDER BY event_date ASC
            LIMIT 5
        """).fetchall()

        registration_data = conn.execute("""
            SELECT
                strftime('%Y-%m', registered_at) AS month,
                COUNT(*) AS total

            FROM students

            WHERE registered_at IS NOT NULL

            GROUP BY month

            ORDER BY month ASC
        """).fetchall()

        registration_labels = [
            row["month"] for row in registration_data
        ]

        registration_counts = [
            row["total"] for row in registration_data
        ]

        attendance_data = conn.execute("""
            SELECT status, COUNT(*) AS total
            FROM attendance
            GROUP BY status
        """).fetchall()

        attendance_status_counts = {
            "Present": 0,
            "Absent": 0
        }

        for row in attendance_data:

            if row["status"] in attendance_status_counts:
                attendance_status_counts[row["status"]] = row["total"]

        attendance_labels = list(attendance_status_counts.keys())

        attendance_counts = list(attendance_status_counts.values())

        return render_template(
            "admin_dashboard.html",

            total_students=total_students,
            total_events=total_events,
            total_attendance=total_attendance,
            total_certificates=total_certificates,

            recent_students=recent_students,
            upcoming_events=upcoming_events,

            student_months=registration_labels,
            student_counts=registration_counts,

            present_count=attendance_status_counts["Present"],
            absent_count=attendance_status_counts["Absent"],

            registration_labels=registration_labels,
            registration_counts=registration_counts,

            attendance_labels=attendance_labels,
            attendance_counts=attendance_counts
        )

    finally:
        conn.close()


# --------------------------------------------------
# ADMIN STUDENT MANAGEMENT
# --------------------------------------------------

@app.route("/admin-students")
@admin_required
def admin_students():

    conn = get_db_connection()

    students = conn.execute(
        """SELECT id, name, email
           FROM students
           ORDER BY id DESC"""
    ).fetchall()

    conn.close()

    return render_template(
        "admin_students.html",
        students=students
    )


# --------------------------------------------------
# ADMIN LOGIN
# --------------------------------------------------

@app.route("/admin-login", methods=["GET", "POST"])
def admin_login():

    if request.method == "POST":

        username = request.form["username"].strip()
        password = request.form["password"]

        conn = get_db_connection()

        admin = conn.execute(
            "SELECT * FROM admins WHERE username = ?",
            (username,)
        ).fetchone()

        conn.close()

        if admin and check_password_hash(
            admin["password"], password
        ):

            session.clear()

            session["admin_id"] = admin["id"]
            session["admin_username"] = admin["username"]

            flash("Admin login successful!")

            return redirect(url_for("admin_dashboard"))

        flash("Invalid admin username or password.")

    return render_template("admin_login.html")


# --------------------------------------------------
# ADMIN LOGOUT
# --------------------------------------------------

@app.route("/admin-logout")
def admin_logout():

    session.clear()

    flash("Admin logged out successfully.")

    return redirect(url_for("admin_login"))


# --------------------------------------------------
# STUDENT LOGOUT
# --------------------------------------------------

@app.route("/logout")
def logout():

    session.clear()

    flash("You have logged out successfully.")

    return redirect(url_for("home"))


# --------------------------------------------------
# STUDENT PROFILE
# --------------------------------------------------

@app.route("/student-profile")
def student_profile():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    student = conn.execute(
        """SELECT id, name, email
           FROM students
           WHERE id = ?""",
        (session["student_id"],)
    ).fetchone()

    attendance_records = conn.execute(
        """SELECT
               events.title,
               events.event_date,
               events.location,
               attendance.status

           FROM attendance

           JOIN events
               ON attendance.event_id = events.id

           WHERE attendance.student_id = ?

           ORDER BY events.event_date DESC""",
        (session["student_id"],)
    ).fetchall()

    conn.close()

    return render_template(
        "student_profile.html",
        student=student,
        attendance_records=attendance_records
    )


# --------------------------------------------------
# STUDENT DASHBOARD
# --------------------------------------------------

@app.route("/student-dashboard")
def student_dashboard():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    student = conn.execute(
        """SELECT id, name, email
           FROM students
           WHERE id = ?""",
        (session["student_id"],)
    ).fetchone()

    conn.close()

    if student is None:

        session.clear()

        flash("Student account not found. Please login again.")

        return redirect(url_for("login"))

    return render_template(
        "student_dashboard.html",
        student=student
    )


# --------------------------------------------------
# EVENT REGISTRATION PAGE
# --------------------------------------------------

@app.route("/event-registration")
def event_registration():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    events = conn.execute(
        "SELECT * FROM events ORDER BY event_date ASC"
    ).fetchall()

    registrations = conn.execute(
        """SELECT event_id FROM event_registrations
           WHERE student_id = ?""",
        (session["student_id"],)
    ).fetchall()

    registered_event_ids = {
        registration["event_id"]
        for registration in registrations
    }

    conn.close()

    return render_template(
        "event_registration.html",
        events=events,
        registered_event_ids=registered_event_ids
    )


# --------------------------------------------------
# REGISTER FOR AN EVENT
# --------------------------------------------------

@app.route("/register-event/<int:event_id>", methods=["POST"])
def register_event(event_id):

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    student_id = session["student_id"]

    conn = get_db_connection()

    try:

        event = conn.execute(
            "SELECT id, title FROM events WHERE id = ?",
            (event_id,)
        ).fetchone()

        if event is None:

            flash("Event not found.")

        else:

            existing = conn.execute(
                """SELECT id FROM event_registrations
                   WHERE student_id = ?
                   AND event_id = ?""",
                (student_id, event_id)
            ).fetchone()

            if existing:

                flash("You are already registered for this event.")

            else:

                conn.execute(
                    """INSERT INTO event_registrations
                       (student_id, event_id)
                       VALUES (?, ?)""",
                    (student_id, event_id)
                )

                conn.execute(
                    """INSERT INTO notifications
                       (student_id, title, message)
                       VALUES (?, ?, ?)""",
                    (
                        student_id,
                        "Event Registration Successful!",
                        "You are successfully registered for "
                        + event["title"]
                    )
                )

                conn.commit()

                flash("Event registration successful!")

    finally:
        conn.close()

    return redirect(url_for("event_registration"))


# --------------------------------------------------
# VIEW ANNOUNCEMENTS
# --------------------------------------------------

@app.route("/announcements")
def announcements():

    if "student_id" not in session and "admin_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    all_announcements = conn.execute(
        """SELECT * FROM announcements
           ORDER BY id DESC"""
    ).fetchall()

    conn.close()

    return render_template(
        "announcements.html",
        announcements=all_announcements
    )


# --------------------------------------------------
# ADD ANNOUNCEMENT
# --------------------------------------------------

@app.route("/add-announcement", methods=["POST"])
@admin_required
def add_announcement():

    title = request.form["title"]
    message = request.form["message"]

    conn = get_db_connection()

    conn.execute(
        """INSERT INTO announcements (title, message)
           VALUES (?, ?)""",
        (title, message)
    )

    conn.commit()
    conn.close()

    flash("Announcement published successfully!")

    return redirect(url_for("announcements"))


# --------------------------------------------------
# STUDENT FEEDBACK PAGE
# --------------------------------------------------

@app.route("/feedback")
def feedback():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    events = conn.execute(
        """SELECT id, title FROM events
           ORDER BY event_date ASC"""
    ).fetchall()

    conn.close()

    return render_template(
        "feedback.html",
        events=events
    )


# --------------------------------------------------
# SUBMIT STUDENT FEEDBACK
# --------------------------------------------------

@app.route("/submit-feedback", methods=["POST"])
def submit_feedback():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    event_id = request.form.get("event_id")

    rating = request.form.get("rating")

    feedback_text = request.form.get(
        "feedback_text", ""
    ).strip()

    if not feedback_text or not rating:

        flash("Please complete all required fields.")

        return redirect(url_for("feedback"))

    try:

        rating = int(rating)

    except ValueError:

        flash("Invalid rating.")

        return redirect(url_for("feedback"))

    if rating not in range(1, 6):

        flash("Rating must be between 1 and 5.")

        return redirect(url_for("feedback"))

    conn = get_db_connection()

    try:

        if event_id:

            event = conn.execute(
                "SELECT id FROM events WHERE id = ?",
                (event_id,)
            ).fetchone()

            if event is None:

                flash("Invalid event selected.")

                return redirect(url_for("feedback"))

        conn.execute(
            """INSERT INTO feedback
               (student_id, event_id, feedback_text, rating)
               VALUES (?, ?, ?, ?)""",
            (
                session["student_id"],
                int(event_id) if event_id else None,
                feedback_text,
                rating
            )
        )

        conn.commit()

    finally:
        conn.close()

    flash("Feedback submitted successfully!")

    return redirect(url_for("feedback"))


# --------------------------------------------------
# ADMIN FEEDBACK MANAGEMENT
# --------------------------------------------------

@app.route("/admin-feedback")
@admin_required
def admin_feedback():

    conn = get_db_connection()

    feedback_list = conn.execute("""
        SELECT
            feedback.id,
            feedback.feedback_text,
            feedback.rating,
            feedback.created_at,
            students.name AS student_name,
            students.email AS email,
            events.title AS event_title

        FROM feedback

        JOIN students
            ON feedback.student_id = students.id

        LEFT JOIN events
            ON feedback.event_id = events.id

        ORDER BY feedback.id DESC

    """).fetchall()

    conn.close()

    return render_template(
        "admin_feedback.html",
        feedback_list=feedback_list
    )


# --------------------------------------------------
# ADMIN NOTIFICATIONS PAGE
# --------------------------------------------------

@app.route("/admin-notifications")
@admin_required
def admin_notifications():

    return render_template("admin_notifications.html")


# --------------------------------------------------
# SEND NOTIFICATION TO ALL STUDENTS
# --------------------------------------------------

@app.route("/send-notification", methods=["POST"])
@admin_required
def send_notification():

    title = request.form.get("title", "").strip()

    message = request.form.get("message", "").strip()

    if not title or not message:

        flash("Please enter both title and message.")

        return redirect(url_for("admin_notifications"))

    conn = get_db_connection()

    try:

        students = conn.execute(
            "SELECT id FROM students"
        ).fetchall()

        for student in students:

            conn.execute(
                """INSERT INTO notifications
                   (student_id, title, message)
                   VALUES (?, ?, ?)""",
                (student["id"], title, message)
            )

        conn.commit()

    finally:
        conn.close()

    flash("Notification sent to all students!")

    return redirect(url_for("admin_notifications"))


# --------------------------------------------------
# STUDENT NOTIFICATIONS PAGE
# --------------------------------------------------

@app.route("/notifications")
def notifications():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    all_notifications = conn.execute(
        """SELECT * FROM notifications
           WHERE student_id = ?
           ORDER BY id DESC""",
        (session["student_id"],)
    ).fetchall()

    conn.close()

    return render_template(
        "notifications.html",
        notifications=all_notifications
    )


# --------------------------------------------------
# MY EVENT REGISTRATIONS
# --------------------------------------------------

@app.route("/my-registrations")
def my_registrations():

    if "student_id" not in session:

        flash("Please login first.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    registrations = conn.execute("""
        SELECT events.*

        FROM event_registrations

        JOIN events
            ON event_registrations.event_id = events.id

        WHERE event_registrations.student_id = ?

        ORDER BY events.event_date ASC

    """, (session["student_id"],)).fetchall()

    conn.close()

    return render_template(
        "my_registrations.html",
        registrations=registrations
    )


# --------------------------------------------------
# ADMIN EVENT REGISTRATIONS
# --------------------------------------------------

@app.route("/admin-event-registrations")
@admin_required
def admin_event_registrations():

    conn = get_db_connection()

    registrations = conn.execute("""
        SELECT
            students.name,
            students.email,
            events.title,
            events.event_date,
            event_registrations.registration_date

        FROM event_registrations

        JOIN students
            ON event_registrations.student_id = students.id

        JOIN events
            ON event_registrations.event_id = events.id

        ORDER BY event_registrations.id DESC

    """).fetchall()

    conn.close()

    return render_template(
        "admin_event_registrations.html",
        registrations=registrations
    )


# --------------------------------------------------
# STUDENT ACHIEVEMENTS AND BADGES
# --------------------------------------------------

@app.route("/achievements")
def achievements():

    if "student_id" not in session:

        flash("Please login to view your achievements.")

        return redirect(url_for("login"))

    conn = get_db_connection()

    attended_count = conn.execute("""
        SELECT COUNT(DISTINCT event_id)

        FROM attendance

        WHERE student_id = ?
        AND status = 'Present'

    """, (session["student_id"],)).fetchone()[0]

    conn.close()

    badges = [
        {"name": "First Step", "required": 1},
        {"name": "Active Participant", "required": 3},
        {"name": "Event Champion", "required": 5},
        {"name": "Campus Star", "required": 10}
    ]

    for badge in badges:
        badge["unlocked"] = attended_count >= badge["required"]

    next_badge = next(
        (badge for badge in badges if not badge["unlocked"]),
        None
    )

    return render_template(
        "achievements.html",
        badges=badges,
        attended_count=attended_count,
        next_badge=next_badge
    )


# --------------------------------------------------
# APPLICATION START
# --------------------------------------------------

if __name__ == "__main__":

    app.run(debug=False)
