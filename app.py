import os
import base64
from datetime import datetime
from dateutil import tz

from flask import Flask, redirect, request, send_file, abort
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, current_user, login_user, logout_user

import dash
from dash import html, dcc, Input, Output, State, dash_table, no_update
import dash_bootstrap_components as dbc
from werkzeug.security import generate_password_hash, check_password_hash
from io import BytesIO

APP_TZ = tz.gettz("America/Toronto")

# -------------------------
# Config
# -------------------------
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DB_PATH = os.environ.get("DB_PATH", os.path.join("data", "app.db"))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if not os.path.isabs(DB_PATH):
    DB_PATH = os.path.join(BASE_DIR, DB_PATH)

UPLOAD_MAX_MB = int(os.environ.get("UPLOAD_MAX_MB", "10"))  # PDF/data upload size cap

ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "admin@conatoc.net")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "ChangeMeNow!")

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# -------------------------
# Flask + DB + Login
# -------------------------
server = Flask(__name__)
server.config["SECRET_KEY"] = SECRET_KEY
server.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
server.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# Safer defaults for SQLite when Dash/Flask uses threaded server in dev or gunicorn workers:
server.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"connect_args": {"check_same_thread": False}}
server.config["MAX_CONTENT_LENGTH"] = UPLOAD_MAX_MB * 1024 * 1024

db = SQLAlchemy(server)

login_manager = LoginManager()
login_manager.init_app(server)

def now_local():
    return datetime.now(tz=APP_TZ).replace(tzinfo=None)

# -------------------------
# Models
# -------------------------
class User(db.Model, UserMixin):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    name = db.Column(db.String(120), nullable=False)
    affiliation = db.Column(db.String(180), nullable=True)
    role = db.Column(db.String(32), nullable=False, default="patient")  # admin | researcher | doctor | patient
    password_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

class Paper(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(400), nullable=False)
    link = db.Column(db.String(800), nullable=True)
    tags = db.Column(db.String(300), nullable=True)
    summary = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    file_name = db.Column(db.String(255), nullable=True)
    file_bytes = db.Column(db.LargeBinary, nullable=True)

class Dataset(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(400), nullable=False)
    description = db.Column(db.Text, nullable=True)
    link = db.Column(db.String(800), nullable=True)
    tags = db.Column(db.String(300), nullable=True)
    visibility = db.Column(db.String(32), nullable=False, default="members")  # members | researchers
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    file_name = db.Column(db.String(255), nullable=True)
    file_bytes = db.Column(db.LargeBinary, nullable=True)

class NewsPost(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(240), nullable=False)
    body = db.Column(db.Text, nullable=False)
    link = db.Column(db.String(800), nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

class ChatMessage(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    channel = db.Column(db.String(32), nullable=False, default="general")  # general | research | patients
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=now_local)
    created_by = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

# -------------------------
# Bootstrap admin + DB
# -------------------------
with server.app_context():
    db.create_all()
    admin = User.query.filter_by(email=ADMIN_EMAIL).first()
    if not admin:
        admin = User(
            email=ADMIN_EMAIL,
            name="Marica Bakovic (Admin)",
            affiliation="University of Guelph",
            role="admin",
            password_hash=generate_password_hash(ADMIN_PASSWORD),
            is_active=True,
        )
        db.session.add(admin)
        db.session.commit()

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# -------------------------
# Download routes
# -------------------------
@server.route("/logout")
def route_logout():
    logout_user()
    return redirect("/login")

@server.route("/download/paper/<int:paper_id>")
def download_paper(paper_id: int):
    if not current_user.is_authenticated:
        return redirect("/login")
    paper = Paper.query.get(paper_id)
    if not paper or not paper.file_bytes:
        abort(404)
    bio = BytesIO(paper.file_bytes)
    bio.seek(0)
    fname = paper.file_name or f"paper_{paper_id}.pdf"
    return send_file(bio, as_attachment=True, download_name=fname, mimetype="application/pdf")

@server.route("/download/dataset/<int:dataset_id>")
def download_dataset(dataset_id: int):
    if not current_user.is_authenticated:
        return redirect("/login")
    ds = Dataset.query.get(dataset_id)
    if not ds or not ds.file_bytes:
        abort(404)
    if ds.visibility == "researchers" and current_user.role not in ("admin", "researcher"):
        abort(403)
    bio = BytesIO(ds.file_bytes)
    bio.seek(0)
    fname = ds.file_name or f"dataset_{dataset_id}.bin"
    return send_file(bio, as_attachment=True, download_name=fname, mimetype="application/octet-stream")

# -------------------------
# Dash app
# -------------------------
external_stylesheets = [
    dbc.themes.BOOTSTRAP,
]
app = dash.Dash(
    __name__,
    server=server,
    external_stylesheets=external_stylesheets,
    suppress_callback_exceptions=True,
    title="CONATOC Net",
)

def nav_link(label, href, icon=None):
    icon_span = html.Span(icon + " ", style={"opacity": 0.9}) if icon else None
    return dbc.NavLink([icon_span, label], href=href, active="exact", className="small")

def user_badge():
    if not current_user.is_authenticated:
        return dbc.Badge("Guest", color="secondary", className="ms-2")
    role = current_user.role.capitalize()
    return dbc.Badge(role, color=("warning" if role=="Admin" else "info"), className="ms-2")

def top_navbar():
    brand = dbc.NavbarBrand(
        [
            html.Span("CONATOC Net", style={"fontWeight": 800}),
            html.Span("  •  Community & Registry", className="small-muted ms-2"),
        ],
        href="/",
    )

    right = html.Div(
        [
            html.Div(
                [
                    html.Div(
                        current_user.name if current_user.is_authenticated else "Not signed in",
                        style={"fontWeight": 700},
                    ),
                    html.Div(current_user.email if current_user.is_authenticated else "", className="small-muted"),
                ],
                className="me-3",
                style={"textAlign": "right"},
            ),
            user_badge(),
            (
                dbc.Button("Logout", href="/logout", className="ms-3 btn-glow", size="sm", outline=True)
                if current_user.is_authenticated
                else dbc.Button("Login", href="/login", className="ms-3 btn-glow", size="sm")
            ),
        ],
        className="d-flex align-items-center",
    )

    items = [
        nav_link("Home", "/", "🏠"),
        nav_link("Papers", "/papers", "📚"),
        nav_link("Data Hub", "/data", "🧬"),
        nav_link("Researchers", "/researchers", "🧑‍🔬"),
        nav_link("Doctors", "/doctors", "🩺"),
        nav_link("Patients", "/patients", "🧑‍🤝‍🧑"),
        nav_link("Community", "/community", "💬"),
    ]
    if current_user.is_authenticated and current_user.role == "admin":
        items.append(nav_link("Admin", "/admin", "🛡️"))

    nav = dbc.Nav(items, pills=True, className="ms-3")

    return dbc.Navbar(
        dbc.Container(
            [
                brand,
                nav,
                dbc.NavbarToggler(id="navbar-toggler"),
                dbc.Collapse(right, id="navbar-collapse", navbar=True, is_open=True),
            ]
        ),
        color="light",
        dark=False,
        className="navbar-glass",
        sticky="top",
    )

def auth_guard(content):
    if current_user.is_authenticated:
        return content
    return dbc.Container(
        dbc.Row(
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.H3("Members-only area", className="mb-2"),
                            html.P("Please log in (or register) to access this section.", className="small-muted"),
                            dbc.Button("Go to Login", href="/login", className="btn-glow"),
                        ]
                    ),
                    className="glass",
                ),
                md=7,
                className="mx-auto mt-4",
            )
        ),
        fluid=True,
    )

def role_guard(allowed_roles, content):
    if not current_user.is_authenticated:
        return auth_guard(content)
    if current_user.role in allowed_roles:
        return content
    return dbc.Container(
        dbc.Row(
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.H3("Restricted", className="mb-2"),
                            html.P("You don’t have permission to view this section.", className="small-muted"),
                        ]
                    ),
                    className="glass",
                ),
                md=7,
                className="mx-auto mt-4",
            )
        ),
        fluid=True,
    )

# -------------------------
# Pages
# -------------------------
def page_home():
    # Public view if not logged in (still shows hero + public info)
    with server.app_context():
        researchers_count = User.query.filter(User.role.in_(["researcher","admin"])).count()
        patients_count = User.query.filter_by(role="patient").count()
        papers_count = Paper.query.count()
        latest_papers = Paper.query.order_by(Paper.created_at.desc()).limit(6).all()
        latest_news = NewsPost.query.order_by(NewsPost.created_at.desc()).limit(4).all()

    hero = html.Div(
        className="hero fade-in",
        children=[
            html.Img(src=app.get_asset_url("images/conatoc_hero.jpg")),
            html.Div(
                className="hero-content",
                children=[
                    html.Div(
                        [
                            html.Span("Rare disease collaboration space", className="badge-pill me-2"),
                            html.Span("Registry • Research • Community", className="badge-pill"),
                        ]
                    ),
                    html.Div(
                        [
                            html.H1("CONATOC Net", className="h1title"),
                            html.P(
                                "A private, member-based hub for CONATOC: share papers, post news, coordinate projects, and maintain a secure registry with role-based access.",
                                className="subtitle",
                            ),
                            html.Div(
                                [
                                    dbc.Button("Explore Papers", href="/papers", className="btn-glow me-2"),
                                    dbc.Button("Join / Login", href="/login", className="btn-glow") if not current_user.is_authenticated else dbc.Button("Open Community", href="/community", className="btn-glow"),
                                ],
                                className="mt-2",
                            ),
                        ]
                    ),
                ],
            ),
        ],
    )

    kpis = dbc.Row(
        [
            dbc.Col(html.Div([html.H3(str(papers_count)), html.P("Shared papers")], className="kpi glass-soft"), md=4),
            dbc.Col(html.Div([html.H3(str(researchers_count)), html.P("Active researchers")], className="kpi glass-soft"), md=4),
            dbc.Col(html.Div([html.H3(str(patients_count)), html.P("Registered patients")], className="kpi glass-soft"), md=4),
        ],
        className="g-3 mt-3",
    )

    def paper_card(p):
        return dbc.Card(
            dbc.CardBody(
                [
                    html.Div(p.title, style={"fontWeight": 800}),
                    html.Div(p.tags or "—", className="small-muted mt-1"),
                    html.Div(
                        [
                            dbc.Button("Open link", href=p.link, target="_blank", size="sm", className="btn-glow me-2") if p.link else None,
                            dbc.Button("Download PDF", href=f"/download/paper/{p.id}", size="sm", className="btn-glow") if p.file_bytes else None,
                        ],
                        className="mt-2",
                    ),
                ]
            ),
            className="glass",
        )

    def news_card(n):
        return dbc.Card(
            dbc.CardBody(
                [
                    html.Div(n.title, style={"fontWeight": 800}),
                    html.Div(n.body[:180] + ("..." if len(n.body) > 180 else ""), className="small-muted mt-1"),
                    dbc.Button("Source link", href=n.link, target="_blank", size="sm", className="btn-glow mt-2") if n.link else None,
                ]
            ),
            className="glass",
        )

    latest = dbc.Row(
        [
            dbc.Col(
                [
                    html.H4("Latest shared papers", className="mt-4"),
                    html.Div(
                        dbc.Row([dbc.Col(paper_card(p), md=6) for p in latest_papers], className="g-3"),
                        className="fade-in",
                    ),
                ],
                md=7,
            ),
            dbc.Col(
                [
                    html.H4("Latest news posts", className="mt-4"),
                    html.Div(dbc.Row([dbc.Col(news_card(n), md=12) for n in latest_news], className="g-3"), className="fade-in"),
                    html.H4("Research group", className="mt-4"),
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.Img(src=app.get_asset_url("images/lab_group.jpg"), style={"width": "100%", "borderRadius": "16px", "border": "1px solid rgba(0,0,0,0.10)"}),
                                html.Div("Administrator: Marica Bakovic", className="small-muted mt-2"),
                            ]
                        ),
                        className="glass",
                    ),
                ],
                md=5,
            ),
        ],
        className="g-3",
    )

    return dbc.Container([hero, kpis, latest], fluid=True, className="pb-5")

def page_login():
    # login + register
    return dbc.Container(
        dbc.Row(
            dbc.Col(
                dbc.Card(
                    dbc.CardBody(
                        [
                            html.H3("Sign in", className="mb-1"),
                            html.P("Use your email to access the members-only workspace.", className="small-muted"),
                            dbc.Alert(id="auth-alert", is_open=False),
                            dbc.Input(id="login-email", placeholder="Email", type="email", className="mb-2"),
                            dbc.Input(id="login-pass", placeholder="Password", type="password", className="mb-2"),
                            dbc.Button("Login", id="btn-login", className="btn-glow me-2"),
                            dbc.Button("Create account", id="btn-show-register", className="btn-glow", outline=True),
                            html.Hr(className="my-4"),
                            html.Div(
                                id="register-area",
                                children=[
                                    html.H4("Create an account", className="mb-2"),
                                    dbc.Input(id="reg-name", placeholder="Full name", className="mb-2"),
                                    dbc.Input(id="reg-affil", placeholder="Affiliation (optional)", className="mb-2"),
                                    dbc.Input(id="reg-email", placeholder="Email", type="email", className="mb-2"),
                                    dbc.Input(id="reg-pass", placeholder="Password", type="password", className="mb-2"),
                                    dbc.RadioItems(
                                        id="reg-role",
                                        options=[
                                            {"label": "Patient / caregiver", "value": "patient"},
                                            {"label": "Researcher", "value": "researcher"},
                                            {"label": "Doctor / clinician", "value": "doctor"},
                                        ],
                                        value="patient",
                                        inline=True,
                                        className="mb-2",
                                    ),
                                    dbc.Checkbox(id="reg-consent", label="I agree to community guidelines and understand privacy limits.", value=False),
                                    dbc.Button("Register", id="btn-register", className="btn-glow mt-2"),
                                    html.Div(id="auth-redirect"),
                                ],
                                style={"display": "none"},
                            ),
                        ]
                    ),
                    className="glass",
                ),
                md=6,
                className="mx-auto mt-4",
            )
        ),
        fluid=True,
    )

def page_researchers():
    if not current_user.is_authenticated:
        return auth_guard(None)

    with server.app_context():
        users = User.query.filter(User.role.in_(["researcher","admin"])).order_by(User.created_at.desc()).all()

    rows = []
    for u in users:
        rows.append({
            "Name": u.name,
            "Email": u.email,
            "Affiliation": u.affiliation or "",
            "Role": u.role,
            "Joined": u.created_at.strftime("%Y-%m-%d"),
        })

    return dbc.Container(
        [
            html.H2("Active Researchers", className="mt-4"),
            html.P("Verified researchers and collaborators working on CONATOC-related projects.", className="small-muted"),
            dbc.Input(id="researcher-search", placeholder="Search name / affiliation / email…", className="mb-3"),
            html.Div(
                dash_table.DataTable(
                    id="researchers-table",
                    data=rows,
                    columns=[{"name": k, "id": k} for k in ["Name","Affiliation","Email","Role","Joined"]],
                    page_size=12,
                    style_as_list_view=True,
                    style_header={"backgroundColor": "rgba(0,0,0,0.04)", "color": "rgba(0,0,0,0.85)", "border": "none"},
                    style_cell={"backgroundColor": "rgba(255,255,255,0.80)", "color": "rgba(0,0,0,0.80)", "border": "none", "padding": "10px", "whiteSpace": "normal", "height": "auto"},
                    style_table={"overflowX": "auto"},
                ),
                className="table-glass glass p-2",
            ),
        ],
        fluid=True,
        className="pb-5",
    )

def page_doctors():
    if not current_user.is_authenticated:
        return auth_guard(None)

    with server.app_context():
        users = User.query.filter_by(role="doctor").order_by(User.created_at.desc()).all()

    rows = []
    for u in users:
        rows.append({
            "Name": u.name,
            "Email": u.email,
            "Affiliation": u.affiliation or "",
            "Role": u.role,
            "Joined": u.created_at.strftime("%Y-%m-%d"),
        })

    return dbc.Container(
        [
            html.H2("Active Doctors / Clinicians", className="mt-4"),
            html.P("Clinicians and physicians connected to the CONATOC community.", className="small-muted"),
            dbc.Input(id="doctor-search", placeholder="Search name / affiliation / email…", className="mb-3"),
            html.Div(
                dash_table.DataTable(
                    id="doctors-table",
                    data=rows,
                    columns=[{"name": k, "id": k} for k in ["Name","Affiliation","Email","Role","Joined"]],
                    page_size=12,
                    style_as_list_view=True,
                    style_header={"backgroundColor": "rgba(0,0,0,0.04)", "color": "rgba(0,0,0,0.85)", "border": "none"},
                    style_cell={"backgroundColor": "rgba(255,255,255,0.55)", "color": "rgba(0,0,0,0.80)", "border": "none", "padding": "10px"},
                    style_table={"overflowX": "auto"},
                ),
                className="table-glass glass p-2",
            ),
        ],
        fluid=True,
        className="pb-5",
    )


def page_patients():
    # Patients page is restricted to researchers/admin for viewing list.
    # Patients can see a “profile / registry” message instead.
    if not current_user.is_authenticated:
        return auth_guard(None)

    if current_user.role == "patient":
        return dbc.Container(
            dbc.Row(
                dbc.Col(
                    dbc.Card(
                        dbc.CardBody(
                            [
                                html.H3("Patient Registry", className="mb-2"),
                                html.P("Thank you for being part of the community. Your account is active.", className="small-muted"),
                                html.P("For privacy, patient lists are not shown to patient accounts. If you want to update your information, contact the administrator.", className="small-muted"),
                            ]
                        ),
                        className="glass",
                    ),
                    md=8,
                    className="mx-auto mt-4",
                )
            ),
            fluid=True,
        )

    with server.app_context():
        patients = User.query.filter_by(role="patient").order_by(User.created_at.desc()).all()

    rows = [{"Name": p.name, "Email": p.email, "Affiliation": p.affiliation or "", "Joined": p.created_at.strftime("%Y-%m-%d")} for p in patients]

    return dbc.Container(
        [
            html.H2("Registered Patients", className="mt-4"),
            html.P("Visible to researchers/admin only. Avoid storing sensitive details without consent + governance.", className="small-muted"),
            dbc.Input(id="patient-search", placeholder="Search name / email…", className="mb-3"),
            html.Div(
                dash_table.DataTable(
                    id="patients-table",
                    data=rows,
                    columns=[{"name": k, "id": k} for k in ["Name","Email","Affiliation","Joined"]],
                    page_size=12,
                    style_as_list_view=True,
                    style_header={"backgroundColor": "rgba(0,0,0,0.04)", "color": "rgba(0,0,0,0.85)", "border": "none"},
                    style_cell={"backgroundColor": "rgba(255,255,255,0.80)", "color": "rgba(0,0,0,0.80)", "border": "none", "padding": "10px", "whiteSpace": "normal", "height": "auto"},
                    style_table={"overflowX": "auto"},
                ),
                className="table-glass glass p-2",
            ),
        ],
        fluid=True,
        className="pb-5",
    )

def page_papers():
    if not current_user.is_authenticated:
        return auth_guard(None)

    with server.app_context():
        papers = Paper.query.order_by(Paper.created_at.desc()).all()
        users = {u.id: u for u in User.query.all()}

    def serialize(p):
        u = users.get(p.uploaded_by)
        return {
            "ID": p.id,
            "Title": p.title,
            "Tags": p.tags or "",
            "Uploaded by": (u.name if u else "—"),
            "Date": p.created_at.strftime("%Y-%m-%d"),
            "Link": p.link or "",
            "PDF": ("Yes" if p.file_bytes else "No"),
        }

    rows = [serialize(p) for p in papers]

    upload = dbc.Card(
        dbc.CardBody(
            [
                html.H4("Share a paper", className="mb-2"),
                html.Div("Add a PubMed/DOI link, or upload a PDF (<= max upload size).", className="small-muted mb-2"),
                dbc.Input(id="paper-title", placeholder="Paper title*", className="mb-2"),
                dbc.Input(id="paper-link", placeholder="Link (PubMed / DOI / journal page)", className="mb-2"),
                dbc.Input(id="paper-tags", placeholder="Tags (comma-separated; e.g., SLC44A1, CTL1, lipidomics)", className="mb-2"),
                dbc.Textarea(id="paper-summary", placeholder="Short summary (optional)", className="mb-2", style={"minHeight": "90px"}),
                dcc.Upload(
                    id="paper-upload",
                    children=html.Div(["Drag & drop PDF, or ", html.A("browse")]),
                    multiple=False,
                    style={"border": "1px dashed rgba(0,0,0,0.22)", "borderRadius": "16px", "padding": "16px", "textAlign": "center", "background": "rgba(255,255,255,0.70)"},
                ),
                html.Div(id="paper-upload-meta", className="small-muted mt-1"),
                dbc.Button("Publish", id="btn-paper-publish", className="btn-glow mt-3"),
                dbc.Alert(id="paper-alert", is_open=False, className="mt-3"),
            ]
        ),
        className="glass",
    )

    table = html.Div(
        [
            dbc.Input(id="paper-search", placeholder="Search papers…", className="mb-2"),
            html.Div(
                dash_table.DataTable(
                    id="papers-table",
                    data=rows,
                    columns=[{"name": c, "id": c} for c in ["Title","Tags","Uploaded by","Date","Link","PDF","ID"]],
                    page_size=10,
                    sort_action="native",
                    filter_action="none",
                    row_selectable="single",
                    style_as_list_view=True,
                    style_header={"backgroundColor": "rgba(0,0,0,0.04)", "color": "rgba(0,0,0,0.85)", "border": "none"},
                    style_cell={"backgroundColor": "rgba(255,255,255,0.80)", "color": "rgba(0,0,0,0.80)", "border": "none", "padding": "10px", "whiteSpace": "normal", "height": "auto"},
                    style_table={"overflowX": "auto"},
                ),
                className="table-glass glass p-2",
            ),
            dbc.Alert("Tip: select a row to open/download.", color="info", className="mt-3", style={"background":"rgba(120,190,255,0.12)", "border":"1px solid rgba(0,0,0,0.10)"}),
            html.Div(id="paper-actions"),
        ]
    )

    return dbc.Container(
        [
            html.H2("Papers & Preprints", className="mt-4"),
            html.P("Members can upload papers and discuss them with the community.", className="small-muted"),
            dbc.Row([dbc.Col(upload, md=5), dbc.Col(table, md=7)], className="g-3"),
        ],
        fluid=True,
        className="pb-5",
    )

def page_data():
    if not current_user.is_authenticated:
        return auth_guard(None)

    with server.app_context():
        items = Dataset.query.order_by(Dataset.created_at.desc()).all()
        users = {u.id: u for u in User.query.all()}

    rows = []
    for ds in items:
        u = users.get(ds.uploaded_by)
        rows.append({
            "ID": ds.id,
            "Title": ds.title,
            "Tags": ds.tags or "",
            "Visibility": ds.visibility,
            "Uploaded by": (u.name if u else "—"),
            "Date": ds.created_at.strftime("%Y-%m-%d"),
            "Link": ds.link or "",
            "File": ("Yes" if ds.file_bytes else "No"),
        })

    upload = dbc.Card(
        dbc.CardBody(
            [
                html.H4("Upload disease-related data", className="mb-2"),
                html.Div("Upload small files or share links (datasets, protocols, slides, code, etc.).", className="small-muted mb-2"),
                dbc.Input(id="ds-title", placeholder="Title*", className="mb-2"),
                dbc.Textarea(id="ds-desc", placeholder="Description (optional)", className="mb-2", style={"minHeight": "90px"}),
                dbc.Input(id="ds-link", placeholder="Link (optional)", className="mb-2"),
                dbc.Input(id="ds-tags", placeholder="Tags (comma-separated)", className="mb-2"),
                dbc.RadioItems(
                    id="ds-vis",
                    options=[
                        {"label": "Visible to all members", "value": "members"},
                        {"label": "Researchers/admin only", "value": "researchers"},
                    ],
                    value="members",
                    inline=True,
                    className="mb-2",
                ),
                dcc.Upload(
                    id="ds-upload",
                    children=html.Div(["Drag & drop file, or ", html.A("browse")]),
                    multiple=False,
                    style={"border": "1px dashed rgba(0,0,0,0.22)", "borderRadius": "16px", "padding": "16px", "textAlign": "center", "background": "rgba(255,255,255,0.70)"},
                ),
                html.Div(id="ds-upload-meta", className="small-muted mt-1"),
                dbc.Button("Publish", id="btn-ds-publish", className="btn-glow mt-3"),
                dbc.Alert(id="ds-alert", is_open=False, className="mt-3"),
            ]
        ),
        className="glass",
    )

    table = html.Div(
        [
            dbc.Input(id="ds-search", placeholder="Search data…", className="mb-2"),
            html.Div(
                dash_table.DataTable(
                    id="ds-table",
                    data=rows,
                    columns=[{"name": c, "id": c} for c in ["Title","Tags","Visibility","Uploaded by","Date","Link","File","ID"]],
                    page_size=10,
                    sort_action="native",
                    row_selectable="single",
                    style_as_list_view=True,
                    style_header={"backgroundColor": "rgba(0,0,0,0.04)", "color": "rgba(0,0,0,0.85)", "border": "none"},
                    style_cell={"backgroundColor": "rgba(255,255,255,0.80)", "color": "rgba(0,0,0,0.80)", "border": "none", "padding": "10px", "whiteSpace": "normal", "height": "auto"},
                    style_table={"overflowX": "auto"},
                ),
                className="table-glass glass p-2",
            ),
            html.Div(id="ds-actions"),
        ]
    )

    return dbc.Container(
        [
            html.H2("Data Hub", className="mt-4"),
            html.P("Share datasets, protocols, slides, tools, and other disease-related resources.", className="small-muted"),
            dbc.Row([dbc.Col(upload, md=5), dbc.Col(table, md=7)], className="g-3"),
        ],
        fluid=True,
        className="pb-5",
    )

def page_community():
    if not current_user.is_authenticated:
        return auth_guard(None)

    channel_opts = [{"label":"General", "value":"general"}]
    if current_user.role in ("admin","researcher","doctor"):
        channel_opts.append({"label":"Research", "value":"research"})
    if current_user.role in ("admin","patient"):
        channel_opts.append({"label":"Patients", "value":"patients"})

    left = dbc.Card(
        dbc.CardBody(
            [
                html.H4("Community Chat", className="mb-2"),
                html.Div("Near real-time updates (polling). Keep patient-identifying details out of chat.", className="small-muted mb-2"),
                dbc.Select(id="chat-channel", options=channel_opts, value=channel_opts[0]["value"], className="mb-2"),
                html.Div(id="chat-feed", className="glass-soft p-2", style={"maxHeight":"420px", "overflowY":"auto"}),
                dcc.Interval(id="chat-refresh", interval=2500, n_intervals=0),
                dbc.Input(id="chat-text", placeholder="Write a message…", className="mt-2"),
                dbc.Button("Send", id="btn-chat-send", className="btn-glow mt-2"),
                dbc.Alert(id="chat-alert", is_open=False, className="mt-2"),
            ]
        ),
        className="glass",
    )

    right = dbc.Card(
        dbc.CardBody(
            [
                html.H4("Post news to the feed", className="mb-2"),
                dbc.Input(id="news-title", placeholder="Title*", className="mb-2"),
                dbc.Input(id="news-link", placeholder="Source link (optional)", className="mb-2"),
                dbc.Textarea(id="news-body", placeholder="What’s new? (2–6 sentences works best)", className="mb-2", style={"minHeight":"120px"}),
                dbc.Button("Publish", id="btn-news-publish", className="btn-glow"),
                dbc.Alert(id="news-alert", is_open=False, className="mt-3"),
                html.Hr(className="my-4"),
                html.H4("Latest feed", className="mb-2"),
                html.Div(id="news-feed"),
                dcc.Interval(id="news-refresh", interval=5000, n_intervals=0),
            ]
        ),
        className="glass",
    )

    return dbc.Container([html.H2("Community", className="mt-4"), dbc.Row([dbc.Col(left, md=6), dbc.Col(right, md=6)], className="g-3")], fluid=True, className="pb-5")

def page_admin():
    # Admin-only
    if not current_user.is_authenticated:
        return auth_guard(None)
    if current_user.role != "admin":
        return role_guard(["admin"], None)

    with server.app_context():
        users = User.query.order_by(User.created_at.desc()).all()

    rows = []
    for u in users:
        rows.append({
            "ID": u.id,
            "Name": u.name,
            "Email": u.email,
            "Role": u.role,
            "Affiliation": u.affiliation or "",
            "Active": "Yes" if u.is_active else "No",
            "Joined": u.created_at.strftime("%Y-%m-%d"),
        })

    return dbc.Container(
        [
            html.H2("Admin Console", className="mt-4"),
            html.P("Manage users and roles. (Initial admin is pre-seeded as Marica Bakovic.)", className="small-muted"),
            dbc.Alert("Select a user row, then set role / deactivate.", color="info", style={"background":"rgba(120,190,255,0.12)", "border":"1px solid rgba(0,0,0,0.10)"}),
            html.Div(
                dash_table.DataTable(
                    id="admin-users",
                    data=rows,
                    columns=[{"name": c, "id": c} for c in ["Name","Email","Role","Affiliation","Active","Joined","ID"]],
                    page_size=12,
                    row_selectable="single",
                    sort_action="native",
                    style_as_list_view=True,
                    style_header={"backgroundColor": "rgba(0,0,0,0.04)", "color": "rgba(0,0,0,0.85)", "border": "none"},
                    style_cell={"backgroundColor": "rgba(255,255,255,0.80)", "color": "rgba(0,0,0,0.80)", "border": "none", "padding": "10px"},
                    style_table={"overflowX": "auto"},
                ),
                className="table-glass glass p-2",
            ),
            dbc.Row(
                [
                    dbc.Col(dbc.Select(id="admin-role", options=[
                        {"label":"Admin", "value":"admin"},
                        {"label":"Researcher", "value":"researcher"},
                        {"label":"Doctor", "value":"doctor"},
                        {"label":"Patient", "value":"patient"},
                    ], value="researcher"), md=3),
                    dbc.Col(dbc.Button("Set role", id="btn-admin-setrole", className="btn-glow"), md="auto"),
                    dbc.Col(dbc.Button("Deactivate", id="btn-admin-deactivate", className="btn-glow", outline=True), md="auto"),
                ],
                className="g-2 mt-3 align-items-center",
            ),
            dbc.Alert(id="admin-alert", is_open=False, className="mt-3"),
        ],
        fluid=True,
        className="pb-5",
    )

# Router
app.layout = html.Div(
    [
        dcc.Location(id="url"),
        html.Div(id="nav"),
        html.Div(id="page"),
        dcc.Store(id="paper-upload-store"),
        dcc.Store(id="ds-upload-store"),
    ]
)

@app.callback(Output("nav", "children"), Input("url", "pathname"))
def render_nav(_):
    return top_navbar()

@app.callback(Output("page", "children"), Input("url", "pathname"))
def render_page(pathname):
    if pathname in (None, "/", ""):
        return page_home()
    if pathname == "/login":
        return page_login()
    if pathname == "/papers":
        return page_papers()
    if pathname == "/data":
        return page_data()
    if pathname == "/researchers":
        return page_researchers()
    if pathname == "/doctors":
        return page_doctors()
    if pathname == "/patients":
        return page_patients()
    if pathname == "/community":
        return page_community()
    if pathname == "/admin":
        return page_admin()
    return dbc.Container(dbc.Alert("Page not found", color="warning"), fluid=True)

# -------------------------
# Auth callbacks
# -------------------------
@app.callback(
    Output("register-area", "style"),
    Input("btn-show-register", "n_clicks"),
    prevent_initial_call=True
)
def show_register(_):
    return {"display": "block"}

@app.callback(
    Output("auth-alert", "children"),
    Output("auth-alert", "color"),
    Output("auth-alert", "is_open"),
    Output("auth-redirect", "children"),
    Input("btn-login", "n_clicks"),
    State("login-email", "value"),
    State("login-pass", "value"),
    prevent_initial_call=True
)
def do_login(_, email, password):
    if not email or not password:
        return "Please enter email and password.", "warning", True, no_update
    with server.app_context():
        u = User.query.filter_by(email=email.strip().lower()).first()
        if not u or not check_password_hash(u.password_hash, password):
            return "Invalid email or password.", "danger", True, no_update
        if not u.is_active:
            return "Account is deactivated. Contact the administrator.", "danger", True, no_update
        login_user(u)
    return "Logged in successfully.", "success", True, dcc.Location(href="/", id="redir-login")

@app.callback(
    Output("auth-alert", "children", allow_duplicate=True),
    Output("auth-alert", "color", allow_duplicate=True),
    Output("auth-alert", "is_open", allow_duplicate=True),
    Output("auth-redirect", "children", allow_duplicate=True),
    Input("btn-register", "n_clicks"),
    State("reg-name", "value"),
    State("reg-affil", "value"),
    State("reg-email", "value"),
    State("reg-pass", "value"),
    State("reg-role", "value"),
    State("reg-consent", "value"),
    prevent_initial_call=True
)
def do_register(_, name, affil, email, password, role, consent):
    if not consent:
        return "Please confirm the community/privacy checkbox.", "warning", True, no_update
    if not name or not email or not password:
        return "Name, email, and password are required.", "warning", True, no_update
    email = email.strip().lower()
    if "@" not in email:
        return "Please enter a valid email.", "warning", True, no_update
    if len(password) < 8:
        return "Use a stronger password (8+ characters).", "warning", True, no_update

    with server.app_context():
        if User.query.filter_by(email=email).first():
            return "This email is already registered. Try logging in.", "info", True, no_update
        u = User(
            email=email,
            name=name.strip(),
            affiliation=(affil.strip() if affil else None),
            role=(role if role in ("patient","researcher") else "patient"),
            password_hash=generate_password_hash(password),
            is_active=True,
        )
        db.session.add(u)
        db.session.commit()
        login_user(u)

    return "Account created. Welcome!", "success", True, dcc.Location(href="/", id="redir-reg")

# -------------------------
# Search filters
# -------------------------
@app.callback(Output("researchers-table", "data"), Input("researcher-search", "value"))
def filter_researchers(q):
    if not current_user.is_authenticated:
        return []
    q = (q or "").strip().lower()
    with server.app_context():
        users = User.query.filter(User.role.in_(["researcher","admin"])).order_by(User.created_at.desc()).all()
    rows = [{"Name": u.name, "Email": u.email, "Affiliation": u.affiliation or "", "Role": u.role, "Joined": u.created_at.strftime("%Y-%m-%d")} for u in users]
    if not q:
        return rows
    def hit(r):
        return any(q in (str(r[k]).lower()) for k in ["Name","Email","Affiliation","Role"])
    return [r for r in rows if hit(r)]


@app.callback(Output("doctors-table", "data"), Input("doctor-search", "value"))
def filter_doctors(q):
    if not current_user.is_authenticated:
        return []
    q = (q or "").strip().lower()
    with server.app_context():
        users = User.query.filter_by(role="doctor").order_by(User.created_at.desc()).all()
    rows = [{"Name": u.name, "Email": u.email, "Affiliation": u.affiliation or "", "Role": u.role, "Joined": u.created_at.strftime("%Y-%m-%d")} for u in users]
    if not q:
        return rows
    def hit(r):
        return any(q in str(r.get(k,"")).lower() for k in ["Name","Email","Affiliation","Role","Joined"])
    return [r for r in rows if hit(r)]

@app.callback(Output("patients-table", "data"), Input("patient-search", "value"))
def filter_patients(q):
    if not current_user.is_authenticated or current_user.role not in ("admin","researcher"):
        return []
    q = (q or "").strip().lower()
    with server.app_context():
        patients = User.query.filter_by(role="patient").order_by(User.created_at.desc()).all()
    rows = [{"Name": p.name, "Email": p.email, "Affiliation": p.affiliation or "", "Joined": p.created_at.strftime("%Y-%m-%d")} for p in patients]
    if not q:
        return rows
    return [r for r in rows if q in (r["Name"]+r["Email"]).lower()]

# -------------------------
# Upload helpers
# -------------------------
def parse_upload(contents):
    # returns (bytes, filename)
    if not contents:
        return None, None
    content_type, content_string = contents.split(",", 1)
    raw = base64.b64decode(content_string)
    return raw, None

@app.callback(
    Output("paper-upload-store", "data"),
    Output("paper-upload-meta", "children"),
    Input("paper-upload", "contents"),
    State("paper-upload", "filename"),
)
def cache_paper_upload(contents, filename):
    if not contents:
        return None, ""
    raw = base64.b64decode(contents.split(",", 1)[1])
    size_mb = len(raw) / (1024 * 1024)
    meta = f"Selected: {filename} • {size_mb:.2f} MB"
    return {"bytes_b64": base64.b64encode(raw).decode("utf-8"), "filename": filename}, meta

@app.callback(
    Output("ds-upload-store", "data"),
    Output("ds-upload-meta", "children"),
    Input("ds-upload", "contents"),
    State("ds-upload", "filename"),
)
def cache_ds_upload(contents, filename):
    if not contents:
        return None, ""
    raw = base64.b64decode(contents.split(",", 1)[1])
    size_mb = len(raw) / (1024 * 1024)
    meta = f"Selected: {filename} • {size_mb:.2f} MB"
    return {"bytes_b64": base64.b64encode(raw).decode("utf-8"), "filename": filename}, meta

# -------------------------
# Publish paper + refresh table + row actions
# -------------------------
@app.callback(
    Output("paper-alert", "children"),
    Output("paper-alert", "color"),
    Output("paper-alert", "is_open"),
    Output("papers-table", "data"),
    Input("btn-paper-publish", "n_clicks"),
    State("paper-title", "value"),
    State("paper-link", "value"),
    State("paper-tags", "value"),
    State("paper-summary", "value"),
    State("paper-upload-store", "data"),
    prevent_initial_call=True
)
def publish_paper(_, title, link, tags, summary, upload_store):
    if not current_user.is_authenticated:
        return "Please log in.", "warning", True, no_update
    if not title:
        return "Title is required.", "warning", True, no_update

    file_name, file_bytes = None, None
    if upload_store and upload_store.get("bytes_b64"):
        file_bytes = base64.b64decode(upload_store["bytes_b64"])
        file_name = upload_store.get("filename")

    with server.app_context():
        p = Paper(
            title=title.strip(),
            link=(link.strip() if link else None),
            tags=(tags.strip() if tags else None),
            summary=(summary.strip() if summary else None),
            uploaded_by=current_user.id,
            file_name=file_name,
            file_bytes=file_bytes,
        )
        db.session.add(p)
        db.session.commit()

        papers = Paper.query.order_by(Paper.created_at.desc()).all()
        users = {u.id: u for u in User.query.all()}
        rows = []
        for pp in papers:
            u = users.get(pp.uploaded_by)
            rows.append({
                "ID": pp.id,
                "Title": pp.title,
                "Tags": pp.tags or "",
                "Uploaded by": (u.name if u else "—"),
                "Date": pp.created_at.strftime("%Y-%m-%d"),
                "Link": pp.link or "",
                "PDF": ("Yes" if pp.file_bytes else "No"),
            })

    return "Published!", "success", True, rows

@app.callback(
    Output("papers-table", "data", allow_duplicate=True),
    Input("paper-search", "value"),
    State("papers-table", "data"),
    prevent_initial_call=True
)
def filter_papers(q, data):
    q = (q or "").strip().lower()
    if not q:
        return data
    def hit(r):
        return any(q in str(r.get(k,"")).lower() for k in ["Title","Tags","Uploaded by","Link"])
    return [r for r in (data or []) if hit(r)]

@app.callback(
    Output("paper-actions", "children"),
    Input("papers-table", "selected_rows"),
    State("papers-table", "data"),
)
def paper_actions(sel, data):
    if not sel or not data:
        return ""
    row = data[sel[0]]
    pid = row.get("ID")
    buttons = []
    if row.get("Link"):
        buttons.append(dbc.Button("Open link", href=row["Link"], target="_blank", className="btn-glow me-2"))
    if row.get("PDF") == "Yes":
        buttons.append(dbc.Button("Download PDF", href=f"/download/paper/{pid}", className="btn-glow"))
    return html.Div(buttons, className="mt-2")

# -------------------------
# Publish dataset + table actions + visibility control
# -------------------------
@app.callback(
    Output("ds-alert", "children"),
    Output("ds-alert", "color"),
    Output("ds-alert", "is_open"),
    Output("ds-table", "data"),
    Input("btn-ds-publish", "n_clicks"),
    State("ds-title", "value"),
    State("ds-desc", "value"),
    State("ds-link", "value"),
    State("ds-tags", "value"),
    State("ds-vis", "value"),
    State("ds-upload-store", "data"),
    prevent_initial_call=True
)
def publish_dataset(_, title, desc, link, tags, vis, upload_store):
    if not current_user.is_authenticated:
        return "Please log in.", "warning", True, no_update
    if not title:
        return "Title is required.", "warning", True, no_update
    vis = vis if vis in ("members","researchers") else "members"
    if vis == "researchers" and current_user.role not in ("admin","researcher"):
        return "Only researchers/admin can post researcher-only items.", "danger", True, no_update

    file_name, file_bytes = None, None
    if upload_store and upload_store.get("bytes_b64"):
        file_bytes = base64.b64decode(upload_store["bytes_b64"])
        file_name = upload_store.get("filename")

    with server.app_context():
        ds = Dataset(
            title=title.strip(),
            description=(desc.strip() if desc else None),
            link=(link.strip() if link else None),
            tags=(tags.strip() if tags else None),
            visibility=vis,
            uploaded_by=current_user.id,
            file_name=file_name,
            file_bytes=file_bytes,
        )
        db.session.add(ds)
        db.session.commit()

        items = Dataset.query.order_by(Dataset.created_at.desc()).all()
        users = {u.id: u for u in User.query.all()}
        rows = []
        for it in items:
            u = users.get(it.uploaded_by)
            rows.append({
                "ID": it.id,
                "Title": it.title,
                "Tags": it.tags or "",
                "Visibility": it.visibility,
                "Uploaded by": (u.name if u else "—"),
                "Date": it.created_at.strftime("%Y-%m-%d"),
                "Link": it.link or "",
                "File": ("Yes" if it.file_bytes else "No"),
            })

    return "Published!", "success", True, rows

@app.callback(
    Output("ds-table", "data", allow_duplicate=True),
    Input("ds-search", "value"),
    State("ds-table", "data"),
    prevent_initial_call=True
)
def filter_ds(q, data):
    q = (q or "").strip().lower()
    if not q:
        return data
    def hit(r):
        return any(q in str(r.get(k,"")).lower() for k in ["Title","Tags","Uploaded by","Link","Visibility"])
    return [r for r in (data or []) if hit(r)]

@app.callback(
    Output("ds-actions", "children"),
    Input("ds-table", "selected_rows"),
    State("ds-table", "data"),
)
def ds_actions(sel, data):
    if not sel or not data:
        return ""
    row = data[sel[0]]
    did = row.get("ID")
    btns = []
    if row.get("Link"):
        btns.append(dbc.Button("Open link", href=row["Link"], target="_blank", className="btn-glow me-2"))
    if row.get("File") == "Yes":
        btns.append(dbc.Button("Download file", href=f"/download/dataset/{did}", className="btn-glow"))
    return html.Div(btns, className="mt-2")

# -------------------------
# Community: news feed + chat
# -------------------------
def render_news_cards(posts):
    cards = []
    for n in posts:
        cards.append(
            dbc.Card(
                dbc.CardBody(
                    [
                        html.Div(n.title, style={"fontWeight": 800}),
                        html.Div(n.body, className="small-muted mt-1"),
                        dbc.Button("Source", href=n.link, target="_blank", size="sm", className="btn-glow mt-2") if n.link else None,
                        html.Div(n.created_at.strftime("%Y-%m-%d %H:%M"), className="small-muted mt-2"),
                    ]
                ),
                className="glass mb-2",
            )
        )
    return cards or [html.Div("No posts yet. Be the first to share an update!", className="small-muted")]

@app.callback(
    Output("news-feed", "children"),
    Input("news-refresh", "n_intervals"),
)
def refresh_news(_):
    if not current_user.is_authenticated:
        return ""
    with server.app_context():
        posts = NewsPost.query.order_by(NewsPost.created_at.desc()).limit(10).all()
    return render_news_cards(posts)

@app.callback(
    Output("news-alert", "children"),
    Output("news-alert", "color"),
    Output("news-alert", "is_open"),
    Input("btn-news-publish", "n_clicks"),
    State("news-title", "value"),
    State("news-link", "value"),
    State("news-body", "value"),
    prevent_initial_call=True
)
def publish_news(_, title, link, body):
    if not current_user.is_authenticated:
        return "Please log in.", "warning", True
    if not title or not body:
        return "Title and message are required.", "warning", True
    with server.app_context():
        post = NewsPost(
            title=title.strip(),
            body=body.strip(),
            link=(link.strip() if link else None),
            created_by=current_user.id
        )
        db.session.add(post)
        db.session.commit()
    return "Posted to feed.", "success", True

def can_access_channel(ch):
    if ch == "general":
        return True
    if ch == "research":
        return current_user.role in ("admin","researcher","doctor")
    if ch == "patients":
        return current_user.role in ("admin","patient")
    return False

@app.callback(
    Output("chat-feed", "children"),
    Output("chat-alert", "children"),
    Output("chat-alert", "color"),
    Output("chat-alert", "is_open"),
    Input("chat-refresh", "n_intervals"),
    Input("chat-channel", "value"),
)
def refresh_chat(_, channel):
    if not current_user.is_authenticated:
        return "", "", "warning", False
    if not can_access_channel(channel):
        return html.Div("You can’t access this channel.", className="small-muted"), "Restricted channel.", "danger", True
    with server.app_context():
        msgs = ChatMessage.query.filter_by(channel=channel).order_by(ChatMessage.created_at.desc()).limit(60).all()
        users = {u.id: u for u in User.query.all()}
    msgs = list(reversed(msgs))
    items = []
    for m in msgs:
        u = users.get(m.created_by)
        items.append(
            html.Div(
                [
                    html.Span(u.name if u else "—", style={"fontWeight": 800}),
                    html.Span("  •  ", className="small-muted"),
                    html.Span(m.created_at.strftime("%H:%M"), className="small-muted"),
                    html.Div(m.message, style={"whiteSpace":"pre-wrap"}),
                    html.Hr(style={"opacity":0.15}),
                ]
            )
        )
    return items or html.Div("No messages yet.", className="small-muted"), "", "success", False

@app.callback(
    Output("chat-text", "value"),
    Output("chat-alert", "children", allow_duplicate=True),
    Output("chat-alert", "color", allow_duplicate=True),
    Output("chat-alert", "is_open", allow_duplicate=True),
    Input("btn-chat-send", "n_clicks"),
    State("chat-channel", "value"),
    State("chat-text", "value"),
    prevent_initial_call=True
)
def send_chat(_, channel, text):
    if not current_user.is_authenticated:
        return no_update, "Please log in.", "warning", True
    if not can_access_channel(channel):
        return no_update, "Restricted channel.", "danger", True
    if not text or not text.strip():
        return "", "Type a message first.", "warning", True
    with server.app_context():
        msg = ChatMessage(channel=channel, message=text.strip(), created_by=current_user.id)
        db.session.add(msg)
        db.session.commit()
    return "", "Sent.", "success", True

# -------------------------
# Admin actions
# -------------------------
@app.callback(
    Output("admin-alert", "children"),
    Output("admin-alert", "color"),
    Output("admin-alert", "is_open"),
    Output("admin-users", "data"),
    Input("btn-admin-setrole", "n_clicks"),
    Input("btn-admin-deactivate", "n_clicks"),
    State("admin-users", "selected_rows"),
    State("admin-users", "data"),
    State("admin-role", "value"),
    prevent_initial_call=True
)
def admin_actions(setrole_clicks, deact_clicks, selected, data, new_role):
    if not current_user.is_authenticated or current_user.role != "admin":
        return "Not authorized.", "danger", True, no_update
    if not selected:
        return "Select a user first.", "warning", True, no_update

    row = data[selected[0]]
    uid = int(row["ID"])

    trig = dash.callback_context.triggered[0]["prop_id"].split(".")[0]
    with server.app_context():
        u = User.query.get(uid)
        if not u:
            return "User not found.", "danger", True, no_update

        if trig == "btn-admin-setrole":
            if new_role not in ("admin","researcher","patient"):
                return "Invalid role.", "warning", True, no_update
            u.role = new_role
            db.session.commit()
            msg = f"Updated role for {u.email} → {new_role}."
            color = "success"
        else:
            # deactivate (do not deactivate yourself)
            if u.id == current_user.id:
                return "You can’t deactivate your own account.", "warning", True, no_update
            u.is_active = False
            db.session.commit()
            msg = f"Deactivated {u.email}."
            color = "info"

        users = User.query.order_by(User.created_at.desc()).all()
        rows = []
        for uu in users:
            rows.append({
                "ID": uu.id,
                "Name": uu.name,
                "Email": uu.email,
                "Role": uu.role,
                "Affiliation": uu.affiliation or "",
                "Active": "Yes" if uu.is_active else "No",
                "Joined": uu.created_at.strftime("%Y-%m-%d"),
            })
    return msg, color, True, rows

if __name__ == "__main__":
    # dev server (Dash v3 uses app.run; older versions use run_server)
    host = "0.0.0.0"
    port = int(os.environ.get("PORT", "8050"))
    debug = True
    run_fn = getattr(app, "run", None)
    if callable(run_fn):
        run_fn(host=host, port=port, debug=debug)
    else:
        # Fallback for older Dash
        app.run_server(host=host, port=port, debug=debug)
