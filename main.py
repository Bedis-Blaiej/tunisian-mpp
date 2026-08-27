"""
Tunisian Score Prediction App - FastAPI Backend
Cleaned & fixed version — now using Resend for transactional email
"""

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import sessionmaker, Session, declarative_base
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta
from typing import List, Optional
import jwt
import logging
import os
import uuid
import random
import string
import requests
from passlib.context import CryptContext
from dotenv import load_dotenv
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

load_dotenv()

# ============ CONFIG ============
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/tunisian_mpp")
SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 30  # 30 days
ADMIN_USERNAME = "admin"
ADMIN_EMAILS = {"bblaiej@gmail.com"}
TUNISIAN_LEAGUE_NAME = "Tunisian League"

# API-Football supplies the official final scores. The key must be stored as a
# Railway variable and is intentionally never returned by this API.
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY", "")
API_FOOTBALL_BASE_URL = "https://v3.football.api-sports.io"
API_FOOTBALL_TUNISIA_LEAGUE_ID = int(os.getenv("API_FOOTBALL_TUNISIA_LEAGUE_ID", "202"))
API_FOOTBALL_SEASON = int(os.getenv("API_FOOTBALL_SEASON", str(datetime.utcnow().year)))
RESULT_SYNC_INTERVAL_MINUTES = int(os.getenv("RESULT_SYNC_INTERVAL_MINUTES", "30"))
RESULT_SYNC_MINUTES_AFTER_KICKOFF = int(os.getenv("RESULT_SYNC_MINUTES_AFTER_KICKOFF", "105"))
RESULT_SYNC_LOOKBACK_HOURS = int(os.getenv("RESULT_SYNC_LOOKBACK_HOURS", "24"))

logger = logging.getLogger(__name__)

# Google Sign-In: create an OAuth client ID in Google Cloud Console (Web
# application) and put it here. See SETUP notes in the delivery message.
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")

# Email verification: sent via Resend transactional email service
# https://resend.com — sign up, verify your domain, generate an API key
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "")
VERIFICATION_CODE_TTL_MINUTES = 15

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Tunisian Score Prediction API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://tunisian-mpp-frontend.vercel.app",
        "https://tunisian-mpp.vercel.app",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============ DATABASE MODELS ============
class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    username = Column(String(50), unique=True, index=True)
    email = Column(String(255), unique=True, index=True)
    password_hash = Column(String(255), nullable=True)  # null for Google-only accounts
    password_reset_code = Column(String, nullable=True)
    password_reset_expires = Column(DateTime, nullable=True)
    auth_provider = Column(String(20), default="email")  # "email" or "google"
    is_verified = Column(Boolean, default=False)
    verification_code = Column(String(10), nullable=True)
    verification_expires = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


class ForgotPasswordRequest(BaseModel):
    email: str

class ForgotPasswordResponse(BaseModel):
    message: str
    email_sent: bool

class ResetPasswordRequest(BaseModel):
    email: str
    reset_code: str
    new_password: str

class ResetPasswordResponse(BaseModel):
    message: str
    success: bool


class League(Base):
    __tablename__ = "leagues"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100))
    invite_code = Column(String(8), unique=True, index=True)
    created_by = Column(String, ForeignKey("users.id"))
    season = Column(Integer, default=2025)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="active")


class LeagueMember(Base):
    __tablename__ = "league_members"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    league_id = Column(String, ForeignKey("leagues.id"), index=True)
    user_id = Column(String, ForeignKey("users.id"), index=True)
    points = Column(Integer, default=0)
    x2_used = Column(Boolean, default=False)
    joined_at = Column(DateTime, default=datetime.utcnow)


class Match(Base):
    __tablename__ = "matches"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    home_team = Column(String(50), index=True)
    away_team = Column(String(50), index=True)
    gameweek = Column(Integer, index=True)
    kickoff_time = Column(DateTime)
    status = Column(String(20), default="upcoming")  # upcoming, live, finished
    home_goals = Column(Integer, nullable=True)
    away_goals = Column(Integer, nullable=True)
    odds_home = Column(Integer)
    odds_draw = Column(Integer)
    odds_away = Column(Integer)


class Prediction(Base):
    __tablename__ = "predictions"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), index=True)
    league_id = Column(String, ForeignKey("leagues.id"), index=True)
    match_id = Column(String, ForeignKey("matches.id"), index=True)
    predicted_home_goals = Column(Integer)
    predicted_away_goals = Column(Integer)
    predicted_result = Column(String(1))  # '1', 'X', '2'
    points_earned = Column(Integer, default=0)
    is_exact_match = Column(Boolean, default=False)
    rarity_bonus = Column(Integer, default=0)
    x2_applied = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SendNotificationRequest(BaseModel):
    notification_type: str  # "gameweek_reminder" | "match_alert"
    gameweek: Optional[int] = None
    message: Optional[str] = None

class SendNotificationResponse(BaseModel):
    message: str
    emails_sent: int
    success: bool

Base.metadata.create_all(bind=engine)


# ============ SCHEMAS ============
class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class EmailVerify(BaseModel):
    email: EmailStr
    code: str


class ResendCode(BaseModel):
    email: EmailStr


class GoogleAuth(BaseModel):
    id_token: str


class RegisterResponse(BaseModel):
    message: str
    email: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    created_at: datetime
    is_admin: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


class LeagueCreate(BaseModel):
    name: str


class LeagueResponse(BaseModel):
    id: str
    name: str
    invite_code: str
    created_at: datetime


class MatchCreate(BaseModel):
    home_team: str
    away_team: str
    gameweek: int
    kickoff_time: datetime
    odds_home: int
    odds_draw: int
    odds_away: int


class MatchResponse(BaseModel):
    id: str
    home_team: str
    away_team: str
    gameweek: int
    kickoff_time: datetime
    status: str
    home_goals: Optional[int]
    away_goals: Optional[int]
    odds_home: int
    odds_draw: int
    odds_away: int


class PredictionCreate(BaseModel):
    match_id: str
    predicted_home_goals: int
    predicted_away_goals: int
    x2_apply: Optional[bool] = False


class PredictionResponse(BaseModel):
    id: str
    match_id: str
    predicted_home_goals: int
    predicted_away_goals: int
    points_earned: int
    created_at: datetime


class LeaderboardEntry(BaseModel):
    user_id: str
    username: str
    points: int
    rank: int


# ============ UTILITIES ============
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def create_access_token(user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def verify_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> User:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization[7:] if authorization.startswith("Bearer ") else authorization
    user_id = verify_token(token)
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def is_admin_user(user: User) -> bool:
    return user.username == ADMIN_USERNAME or (user.email or "").lower() in ADMIN_EMAILS


def require_admin(user: User):
    if not is_admin_user(user):
        raise HTTPException(status_code=403, detail="Admin access required")


def user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id, username=user.username, email=user.email,
        created_at=user.created_at, is_admin=is_admin_user(user),
    )


def generate_invite_code() -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


def generate_verification_code() -> str:
    return ''.join(random.choices(string.digits, k=6))


def send_verification_email(to_email: str, code: str):
    """Sends a 6-digit verification code via Resend transactional email.
    Requires RESEND_API_KEY and RESEND_FROM_EMAIL to be set in environment.
    Fails loudly rather than silently pretending to succeed."""
    if not RESEND_API_KEY or not RESEND_FROM_EMAIL:
        raise HTTPException(
            status_code=500,
            detail="Email sending isn't configured yet (missing RESEND_API_KEY / RESEND_FROM_EMAIL)."
        )

    html_content = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
        <h1 style="color: #333; margin-bottom: 20px;">Pronos Tunisie</h1>
        <p style="color: #666; font-size: 16px; margin-bottom: 20px;">
            Ton code de vérification Pronos Tunisie est :
        </p>
        <div style="background-color: #f0f0f0; padding: 20px; text-align: center; border-radius: 8px; margin-bottom: 20px;">
            <p style="font-size: 32px; font-weight: bold; color: #333; letter-spacing: 4px; margin: 0;">
                {code}
            </p>
        </div>
        <p style="color: #999; font-size: 14px; margin-bottom: 20px;">
            Ce code expire dans {VERIFICATION_CODE_TTL_MINUTES} minutes.
        </p>
        <p style="color: #999; font-size: 14px;">
            Si tu n'es pas à l'origine de cette demande, ignore cet email.
        </p>
    </div>
    """

    payload = {
        "from": RESEND_FROM_EMAIL,
        "to": to_email,
        "subject": f"{code} — Ton code de vérification Pronos Tunisie",
        "html": html_content,
    }

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers=headers,
            timeout=10
        )
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_detail = e.response.json().get('message', str(e))
            except:
                error_detail = e.response.text or str(e)
        raise HTTPException(status_code=500, detail=f"Couldn't send verification email: {error_detail}")


# ============ POINTS ENGINE ============
def calculate_points(prediction: Prediction, match: Match, all_predictions_for_match: List[Prediction]) -> dict:
    if match.home_goals is None or match.away_goals is None:
        return {'base_points': 0, 'exact_bonus': 0, 'total': 0, 'is_exact': False}

    actual_score = f"{match.home_goals}-{match.away_goals}"
    predicted_score = f"{prediction.predicted_home_goals}-{prediction.predicted_away_goals}"

    if match.home_goals > match.away_goals:
        actual_result = '1'
    elif match.home_goals < match.away_goals:
        actual_result = '2'
    else:
        actual_result = 'X'

    if prediction.predicted_result != actual_result:
        return {'base_points': 0, 'exact_bonus': 0, 'total': 0, 'is_exact': False}

    if actual_result == '1':
        base_points = match.odds_home
    elif actual_result == 'X':
        base_points = match.odds_draw
    else:
        base_points = match.odds_away

    exact_bonus = 0
    is_exact = False

    if predicted_score == actual_score:
        is_exact = True
        exact_count = sum(
            1 for p in all_predictions_for_match
            if f"{p.predicted_home_goals}-{p.predicted_away_goals}" == actual_score
        )
        total_predictions = len(all_predictions_for_match)
        rarity_multiplier = max(1.0, 2.0 - (exact_count / max(1, total_predictions)))
        exact_bonus = int(base_points * rarity_multiplier * 0.5)

    total = base_points + exact_bonus
    if prediction.x2_applied:
        total *= 2

    return {'base_points': base_points, 'exact_bonus': exact_bonus, 'total': total, 'is_exact': is_exact}


def get_user_total_points(user_id: str, db: Session) -> int:
    """A user's score is global: the sum of every finished prediction they've
    ever made, regardless of which league it was submitted through. Every
    league a user belongs to shows this same number — leagues are just
    different groups of members viewing the same underlying score, like
    Fantasy Premier League mini-leagues."""
    total = 0
    predictions = db.query(Prediction).filter(Prediction.user_id == user_id).all()
    for pred in predictions:
        match = db.query(Match).filter(Match.id == pred.match_id).first()
        if match and match.status == "finished":
            total += pred.points_earned
    return total


def recalc_all_members_points(db: Session):
    """Refresh every league_member row's cached points from each user's
    account-wide total."""
    members = db.query(LeagueMember).all()
    for member in members:
        member.points = get_user_total_points(member.user_id, db)
    db.commit()


def apply_match_result(match: Match, home_goals: int, away_goals: int, db: Session) -> int:
    """Save a final score and recalculate every affected prediction."""
    match.home_goals = home_goals
    match.away_goals = away_goals
    match.status = "finished"

    all_predictions = db.query(Prediction).filter(Prediction.match_id == match.id).all()
    for prediction in all_predictions:
        points_data = calculate_points(prediction, match, all_predictions)
        prediction.points_earned = points_data['total']
        prediction.is_exact_match = points_data['is_exact']
        prediction.rarity_bonus = points_data['exact_bonus']

    db.commit()
    recalc_all_members_points(db)
    return len(all_predictions)


def normalise_team_name(name: str) -> str:
    """Normalise provider and local team names for reliable fixture matching."""
    import unicodedata

    normalised = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii").lower()
    for token in (" de ", " du ", "union sportive ", "us ", "es ", "cs ", "as ", "ca "):
        normalised = normalised.replace(token, "")
    normalised = "".join(character for character in normalised if character.isalnum())
    # Keep local display names compatible with their provider spelling.
    return {"hamamsousse": "hammamsousse"}.get(normalised, normalised)


def find_provider_fixture(match: Match, fixtures: list) -> Optional[dict]:
    home_name = normalise_team_name(match.home_team)
    away_name = normalise_team_name(match.away_team)

    def names_match(first: str, second: str) -> bool:
        return first == second or (min(len(first), len(second)) >= 5 and (first.startswith(second) or second.startswith(first)))

    for fixture in fixtures:
        teams = fixture.get("teams", {})
        if (
            names_match(normalise_team_name(teams.get("home", {}).get("name", "")), home_name)
            and names_match(normalise_team_name(teams.get("away", {}).get("name", "")), away_name)
        ):
            return fixture
    return None


def sync_finished_match_results() -> dict:
    """Import only final API-Football results for recently completed matches."""
    if not API_FOOTBALL_KEY:
        logger.warning("Final-score sync skipped: API_FOOTBALL_KEY is not configured")
        return {"updated": 0, "checked_dates": 0, "reason": "API_FOOTBALL_KEY is not configured"}

    now = datetime.utcnow()
    earliest_kickoff = now - timedelta(hours=RESULT_SYNC_LOOKBACK_HOURS)
    latest_kickoff = now - timedelta(minutes=RESULT_SYNC_MINUTES_AFTER_KICKOFF)
    db = SessionLocal()
    updated = 0
    checked_dates = 0

    try:
        candidates = db.query(Match).filter(
            Match.status != "finished",
            Match.kickoff_time >= earliest_kickoff,
            Match.kickoff_time <= latest_kickoff,
        ).all()
        candidates_by_date = {}
        for match in candidates:
            candidates_by_date.setdefault(match.kickoff_time.date(), []).append(match)

        headers = {"x-apisports-key": API_FOOTBALL_KEY}
        final_statuses = {"FT", "AET", "PEN"}
        for match_date, matches in candidates_by_date.items():
            response = requests.get(
                f"{API_FOOTBALL_BASE_URL}/fixtures",
                headers=headers,
                params={
                    "league": API_FOOTBALL_TUNISIA_LEAGUE_ID,
                    "season": API_FOOTBALL_SEASON,
                    "date": match_date.isoformat(),
                },
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                logger.warning("API-Football returned errors for %s: %s", match_date, payload["errors"])
                continue

            checked_dates += 1
            fixtures = payload.get("response", [])
            for match in matches:
                fixture = find_provider_fixture(match, fixtures)
                if not fixture:
                    logger.info("No API-Football fixture found for %s vs %s", match.home_team, match.away_team)
                    continue

                if fixture.get("fixture", {}).get("status", {}).get("short") not in final_statuses:
                    continue

                goals = fixture.get("goals", {})
                home_goals, away_goals = goals.get("home"), goals.get("away")
                if not isinstance(home_goals, int) or not isinstance(away_goals, int):
                    logger.warning("Final fixture has no valid score for %s vs %s", match.home_team, match.away_team)
                    continue

                apply_match_result(match, home_goals, away_goals, db)
                updated += 1

        return {"updated": updated, "checked_dates": checked_dates, "eligible_matches": len(candidates)}
    except requests.RequestException as error:
        logger.exception("API-Football final-score sync failed: %s", error)
        return {"updated": updated, "checked_dates": checked_dates, "error": "Could not reach API-Football"}
    finally:
        db.close()


def get_or_create_tunisian_league(db: Session) -> League:
    """The Tunisian League is a read-only, single-entry system league that
    all players belong to by default. It holds their account-wide predictions."""
    league = db.query(League).filter(League.name == TUNISIAN_LEAGUE_NAME).first()
    if not league:
        league = League(
            id=str(uuid.uuid4()),
            name=TUNISIAN_LEAGUE_NAME,
            invite_code="SYSTEM",
            created_by="system",
            status="system",
        )
        db.add(league)
        db.commit()
        db.refresh(league)
    return league


def ensure_user_in_tunisian_league(user: User, db: Session):
    """Ensures the user is a member of the Tunisian League."""
    league = get_or_create_tunisian_league(db)
    membership = db.query(LeagueMember).filter(
        LeagueMember.league_id == league.id,
        LeagueMember.user_id == user.id
    ).first()
    if not membership:
        membership = LeagueMember(
            id=str(uuid.uuid4()),
            league_id=league.id,
            user_id=user.id,
        )
        db.add(membership)
        db.commit()


# ============ AUTH ============
@app.post("/auth/register", response_model=RegisterResponse)
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """Creates the account but does NOT log the user in yet — a 6-digit
    code is emailed to them, and /auth/verify-email exchanges a valid code
    for the actual access token. This is what enforces "real emails only":
    an account is useless until its owner proves they can receive mail
    sent to it."""
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    if db.query(User).filter(User.username == user_data.username).first():
        raise HTTPException(status_code=400, detail="Username already taken")

    code = generate_verification_code()
    new_user = User(
        id=str(uuid.uuid4()),
        username=user_data.username,
        email=user_data.email,
        password_hash=hash_password(user_data.password),
        auth_provider="email",
        is_verified=False,
        verification_code=code,
        verification_expires=datetime.utcnow() + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES),
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)

    send_verification_email(new_user.email, code)

    return RegisterResponse(message="Verification code sent", email=new_user.email)


@app.post("/auth/verify-email", response_model=TokenResponse)
def verify_email(payload: EmailVerify, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.is_verified:
        raise HTTPException(status_code=400, detail="This email is already verified — try logging in")
    if not user.verification_code or user.verification_code != payload.code:
        raise HTTPException(status_code=400, detail="Incorrect code")
    if not user.verification_expires or datetime.utcnow() > user.verification_expires:
        raise HTTPException(status_code=400, detail="This code has expired — request a new one")

    user.is_verified = True
    user.verification_code = None
    user.verification_expires = None
    db.commit()

    ensure_user_in_tunisian_league(user, db)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, token_type="bearer", user=user_to_response(user))


@app.post("/auth/resend-code")
def resend_code(payload: ResendCode, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="Account not found")
    if user.is_verified:
        raise HTTPException(status_code=400, detail="This email is already verified — try logging in")

    code = generate_verification_code()
    user.verification_code = code
    user.verification_expires = datetime.utcnow() + timedelta(minutes=VERIFICATION_CODE_TTL_MINUTES)
    db.commit()

    send_verification_email(user.email, code)
    return {"message": "Verification code resent"}


@app.post("/auth/login", response_model=TokenResponse)
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == user_data.email).first()
    if not user or not user.password_hash or not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_verified:
        raise HTTPException(status_code=403, detail="Please verify your email before logging in")

    # Lazily backfills accounts created before the Tunisian League existed.
    ensure_user_in_tunisian_league(user, db)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, token_type="bearer", user=user_to_response(user))


@app.post("/auth/google", response_model=TokenResponse)
def google_auth(payload: GoogleAuth, db: Session = Depends(get_db)):
    """Sign in / register with a Google account. Google has already
    verified the email address, so these accounts are marked verified
    immediately and need no code."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=500, detail="Google Sign-In isn't configured yet (missing GOOGLE_CLIENT_ID).")

    try:
        info = google_id_token.verify_oauth2_token(
            payload.id_token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Google token")

    email = info.get("email")
    if not email or not info.get("email_verified", False):
        raise HTTPException(status_code=401, detail="Google account email isn't verified")

    user = db.query(User).filter(User.email == email).first()
    if not user:
        base_username = email.split("@")[0][:40]
        username = base_username
        suffix = 0
        while db.query(User).filter(User.username == username).first():
            suffix += 1
            username = f"{base_username}{suffix}"

        user = User(
            id=str(uuid.uuid4()),
            username=username,
            email=email,
            password_hash=None,
            auth_provider="google",
            is_verified=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    elif not user.is_verified:
        # An existing email-registered (but unverified) account signing in
        # with the same Google address — Google's confirmation is enough.
        user.is_verified = True
        db.commit()

    ensure_user_in_tunisian_league(user, db)

    token = create_access_token(user.id)
    return TokenResponse(access_token=token, token_type="bearer", user=user_to_response(user))


@app.get("/auth/me", response_model=UserResponse)
def get_me(authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)
    return user_to_response(user)


@app.post("/auth/forgot-password", response_model=ForgotPasswordResponse)
def forgot_password(request: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Request password reset code via email."""
    email = request.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        # Don't reveal if email exists (security)
        return ForgotPasswordResponse(message="If an account exists with this email, a reset code has been sent.", email_sent=True)
    
    # Don't allow reset for Google-only accounts
    if user.auth_provider == "google" or not user.password_hash:
        return ForgotPasswordResponse(message="This account uses Google Sign-In. Please use Google to reset your password.", email_sent=False)
    
    # Generate reset code
    reset_code = ''.join(random.choices(string.digits, k=6))
    user.password_reset_code = reset_code
    user.password_reset_expires = datetime.utcnow() + timedelta(minutes=15)
    db.commit()
    
    # Send email
    try:
        send_email(
            email=user.email,
            subject="Réinitialise ton mot de passe - Pronos Tunisie",
            html=f"""
            <h2>Réinitialise ton mot de passe</h2>
            <p>Ton code de réinitialisation:</p>
            <h1 style="letter-spacing: 5px; font-family: monospace;">{reset_code}</h1>
            <p>Ce code expire dans 15 minutes.</p>
            <p>Si tu n'as pas demandé une réinitialisation, ignore cet email.</p>
            """
        )
        print(f"[INFO] Password reset code sent to {user.email}")
        return ForgotPasswordResponse(message="Reset code sent to your email", email_sent=True)
    except Exception as e:
        print(f"[ERROR] Failed to send password reset email: {e}")
        return ForgotPasswordResponse(message="Failed to send reset code. Please try again.", email_sent=False)


@app.post("/auth/reset-password", response_model=ResetPasswordResponse)
def reset_password(request: ResetPasswordRequest, db: Session = Depends(get_db)):
    """Reset password using reset code."""
    email = request.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Verify code exists and not expired
    if not user.password_reset_code or not user.password_reset_expires:
        raise HTTPException(status_code=400, detail="No password reset requested")
    
    if datetime.utcnow() > user.password_reset_expires:
        raise HTTPException(status_code=400, detail="Reset code has expired")
    
    if user.password_reset_code != request.reset_code.strip():
        raise HTTPException(status_code=403, detail="Invalid reset code")
    
    # Validate new password
    if len(request.new_password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    
    # Update password and clear reset code
    user.password_hash = hash_password(request.new_password)
    user.password_reset_code = None
    user.password_reset_expires = None
    db.commit()
    
    print(f"[INFO] Password reset successful for {user.email}")
    
    return ResetPasswordResponse(message="Password reset successfully", success=True)


def generate_gameweek_reminder_email(username: str, gameweek: int) -> str:
    """Generate a catchy gameweek reminder email."""
    return f"""
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; }}
        .container {{ max-width: 600px; margin: 0 auto; padding: 20px; }}
        .header {{ background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%); color: #ffd700; padding: 30px 20px; border-radius: 12px; text-align: center; margin-bottom: 30px; }}
        .header h1 {{ margin: 0; font-size: 28px; font-weight: 800; }}
        .header p {{ margin: 8px 0 0 0; font-size: 14px; color: #ddd; }}
        .content {{ background: #f5f5f5; padding: 30px 20px; border-radius: 12px; margin-bottom: 20px; }}
        .content h2 {{ color: #1a1a2e; margin-top: 0; font-size: 20px; }}
        .highlight {{ background: linear-gradient(135deg, rgba(255, 215, 0, 0.1), rgba(255, 215, 0, 0.05)); padding: 20px; border-left: 4px solid #ffd700; border-radius: 6px; margin: 20px 0; }}
        .highlight strong {{ color: #ffd700; }}
        .cta {{ display: inline-block; background: #ffd700; color: #1a1a2e; padding: 14px 32px; text-decoration: none; border-radius: 8px; font-weight: 800; margin: 20px 0; }}
        .footer {{ text-align: center; color: #999; font-size: 12px; margin-top: 30px; }}
        .stats {{ display: flex; justify-content: space-around; text-align: center; margin: 20px 0; }}
        .stat-box {{ flex: 1; }}
        .stat-number {{ font-size: 24px; font-weight: 800; color: #ffd700; }}
        .stat-label {{ font-size: 12px; color: #666; margin-top: 5px; text-transform: uppercase; }}
    </style>
    
    <div class="container">
        <div class="header">
            <h1>⚽ PRONOS TUNISIE</h1>
            <p>Journée {gameweek} — Les matchs t'attendent!</p>
        </div>
        
        <div class="content">
            <h2>Salut {username}! 👋</h2>
            
            <p>La Journée <strong>{gameweek}</strong> de la Ligue 1 démarre <strong>DEMAIN</strong> et c'est le moment de faire tes pronostics!</p>
            
            <div class="highlight">
                <strong>⏱️ C'est quoi le plan?</strong><br>
                Tu as jusqu'à 15 minutes avant chaque match pour deviner les scores. Plus tu devines juste, plus tu gagnes de points! 🎯
            </div>
            
            <h3>🎁 Comment ça marche?</h3>
            <ul>
                <li><strong>Score correct</strong> → Tu gagnes des points selon les cotes du match</li>
                <li><strong>Score exact</strong> → Bonus surprise de rareté! 🌟</li>
                <li><strong>Joker ×2</strong> → Une fois par journée pour doubler tes points sur UN match</li>
            </ul>
            
            <div class="stats">
                <div class="stat-box">
                    <div class="stat-number">∞</div>
                    <div class="stat-label">Enjeu</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number">100%</div>
                    <div class="stat-label">Gratuit</div>
                </div>
                <div class="stat-box">
                    <div class="stat-number">30s</div>
                    <div class="stat-label">À toi</div>
                </div>
            </div>
            
            <p style="text-align: center;">
                <a href="https://pronos-tunisie.vercel.app" class="cta">ALLER FAIRE MES PRONOS →</a>
            </p>
            
            <div class="highlight" style="border-left-color: #42c98a; background: rgba(66, 201, 138, 0.05);">
                <strong style="color: #42c98a;">💪 Tip Pro:</strong> Les premiers à pronostiquer voient souvent les cotes avant tout le monde. Sois rapide! ⚡
            </div>
        </div>
        
        <div class="footer">
            <p>Tu reçois cet email parce que tu as un compte Pronos Tunisie. C'est le seul reminder qu'on va t'envoyer.</p>
            <p style="color: #ccc; margin-top: 10px;">© 2026 Pronos Tunisie — Le jeu de prédictions 100% tunisien</p>
        </div>
    </div>
    """

# ============ LEAGUES ============
@app.post("/leagues", response_model=LeagueResponse)
def create_league(league_data: LeagueCreate, authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)

    invite_code = generate_invite_code()
    while db.query(League).filter(League.invite_code == invite_code).first():
        invite_code = generate_invite_code()

    new_league = League(
        id=str(uuid.uuid4()),
        name=league_data.name,
        invite_code=invite_code,
        created_by=user.id,
    )
    db.add(new_league)
    db.commit()
    db.refresh(new_league)

    member = LeagueMember(
        id=str(uuid.uuid4()),
        league_id=new_league.id,
        user_id=user.id,
    )
    db.add(member)
    db.commit()

    return LeagueResponse(
        id=new_league.id,
        name=new_league.name,
        invite_code=new_league.invite_code,
        created_at=new_league.created_at,
    )


@app.get("/leagues/{league_id}", response_model=LeagueResponse)
def get_league(league_id: str, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    return LeagueResponse(
        id=league.id,
        name=league.name,
        invite_code=league.invite_code,
        created_at=league.created_at,
    )


@app.get("/leagues/invite/{invite_code}", response_model=LeagueResponse)
def get_league_by_invite(invite_code: str, db: Session = Depends(get_db)):
    league = db.query(League).filter(League.invite_code == invite_code).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    return LeagueResponse(
        id=league.id,
        name=league.name,
        invite_code=league.invite_code,
        created_at=league.created_at,
    )


@app.post("/leagues/{league_id}/join")
def join_league(league_id: str, authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    existing = db.query(LeagueMember).filter(
        LeagueMember.league_id == league_id,
        LeagueMember.user_id == user.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already a member of this league")

    member = LeagueMember(
        id=str(uuid.uuid4()),
        league_id=league_id,
        user_id=user.id,
    )
    db.add(member)
    db.commit()

    return {"message": f"Joined {league.name}"}


@app.post("/leagues/invite/{invite_code}/join")
def join_league_by_invite(invite_code: str, authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)
    league = db.query(League).filter(League.invite_code == invite_code).first()
    if not league:
        raise HTTPException(status_code=404, detail="Invite code not found")

    existing = db.query(LeagueMember).filter(
        LeagueMember.league_id == league.id,
        LeagueMember.user_id == user.id
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="Already a member of this league")

    member = LeagueMember(
        id=str(uuid.uuid4()),
        league_id=league.id,
        user_id=user.id,
    )
    db.add(member)
    db.commit()

    return {"message": f"Joined {league.name}", "league_id": league.id}


@app.get("/user/leagues")
def get_user_leagues(authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)
    memberships = db.query(LeagueMember).filter(LeagueMember.user_id == user.id).all()
    result = []
    for m in memberships:
        league = db.query(League).filter(League.id == m.league_id).first()
        if league:
            result.append({
                "id": league.id,
                "name": league.name,
                "invite_code": league.invite_code,
                "created_at": league.created_at,
                "points": m.points,
            })
    return result


@app.get("/leagues/{league_id}/leaderboard")
def get_leaderboard(league_id: str, db: Session = Depends(get_db)):
    members = db.query(LeagueMember).filter(LeagueMember.league_id == league_id).order_by(
        LeagueMember.points.desc()
    ).all()

    result = []
    for rank, m in enumerate(members, start=1):
        user = db.query(User).filter(User.id == m.user_id).first()
        if user:
            result.append(LeaderboardEntry(
                user_id=user.id,
                username=user.username,
                points=m.points,
                rank=rank,
            ))
    return result


@app.get("/leagues/{league_id}/standings")
def get_standings(league_id: str, db: Session = Depends(get_db)):
    """Backward-compatible alias for the league leaderboard."""
    return get_leaderboard(league_id, db)


@app.delete("/leagues/{league_id}")
def delete_league(league_id: str, authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    if league.created_by != user.id:
        raise HTTPException(status_code=403, detail="Only the league creator can delete it")

    db.query(LeagueMember).filter(LeagueMember.league_id == league_id).delete()
    db.delete(league)
    db.commit()

    return {"message": "League deleted"}


# ============ MATCHES ============
@app.post("/matches", response_model=MatchResponse)
def create_match(match_data: MatchCreate, authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)
    require_admin(user)

    new_match = Match(
        id=str(uuid.uuid4()),
        home_team=match_data.home_team,
        away_team=match_data.away_team,
        gameweek=match_data.gameweek,
        kickoff_time=match_data.kickoff_time,
        odds_home=match_data.odds_home,
        odds_draw=match_data.odds_draw,
        odds_away=match_data.odds_away,
    )
    db.add(new_match)
    db.commit()
    db.refresh(new_match)

    return MatchResponse(
        id=new_match.id,
        home_team=new_match.home_team,
        away_team=new_match.away_team,
        gameweek=new_match.gameweek,
        kickoff_time=new_match.kickoff_time,
        status=new_match.status,
        home_goals=new_match.home_goals,
        away_goals=new_match.away_goals,
        odds_home=new_match.odds_home,
        odds_draw=new_match.odds_draw,
        odds_away=new_match.odds_away,
    )


@app.get("/matches")
def get_matches(gameweek: Optional[int] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    query = db.query(Match)
    if gameweek is not None:
        query = query.filter(Match.gameweek == gameweek)
    if status:
        query = query.filter(Match.status == status)
    matches = query.all()

    return [
        MatchResponse(
            id=m.id,
            home_team=m.home_team,
            away_team=m.away_team,
            gameweek=m.gameweek,
            kickoff_time=m.kickoff_time,
            status=m.status,
            home_goals=m.home_goals,
            away_goals=m.away_goals,
            odds_home=m.odds_home,
            odds_draw=m.odds_draw,
            odds_away=m.odds_away,
        )
        for m in matches
    ]


@app.get("/matches/{match_id}", response_model=MatchResponse)
def get_match(match_id: str, db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    return MatchResponse(
        id=match.id,
        home_team=match.home_team,
        away_team=match.away_team,
        gameweek=match.gameweek,
        kickoff_time=match.kickoff_time,
        status=match.status,
        home_goals=match.home_goals,
        away_goals=match.away_goals,
        odds_home=match.odds_home,
        odds_draw=match.odds_draw,
        odds_away=match.odds_away,
    )


# ============ PREDICTIONS ============
@app.post("/predictions", response_model=PredictionResponse)
def submit_prediction(
    pred_data: PredictionCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Predictions are account-wide, not tied to a specific league: a user
    fills in one score per match, and that same prediction/score is what
    every league they belong to shows on its leaderboard. Internally it's
    still stored against the Tunisian League row so the existing schema
    (which requires a league_id) is satisfied."""
    user = get_current_user(authorization, db)

    match = db.query(Match).filter(Match.id == pred_data.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    lockdown_time = match.kickoff_time - timedelta(minutes=15)
    if datetime.utcnow() >= lockdown_time:
        raise HTTPException(status_code=400, detail="Predictions are locked - match starting soon")

    tunisian_league = get_or_create_tunisian_league(db)
    ensure_user_in_tunisian_league(user, db)

    # X2 per-gameweek validation — global across the account (one ×2 per
    # gameweek total, since there's only ever one prediction per match now).
    if pred_data.x2_apply:
        x2_in_gameweek = db.query(Prediction).join(Match, Prediction.match_id == Match.id).filter(
            Prediction.user_id == user.id,
            Prediction.x2_applied == True,  # noqa: E712
            Match.gameweek == match.gameweek,
            Prediction.match_id != pred_data.match_id,
        ).first()
        if x2_in_gameweek:
            other_match = db.query(Match).filter(Match.id == x2_in_gameweek.match_id).first()
            raise HTTPException(
                status_code=400,
                detail=f"X2 already used for {other_match.home_team} vs {other_match.away_team} in Gameweek {match.gameweek}"
            )

    if pred_data.predicted_home_goals > pred_data.predicted_away_goals:
        predicted_result = '1'
    elif pred_data.predicted_home_goals < pred_data.predicted_away_goals:
        predicted_result = '2'
    else:
        predicted_result = 'X'

    existing = db.query(Prediction).filter(
        Prediction.user_id == user.id,
        Prediction.match_id == pred_data.match_id
    ).first()

    if existing:
        existing.predicted_home_goals = pred_data.predicted_home_goals
        existing.predicted_away_goals = pred_data.predicted_away_goals
        existing.predicted_result = predicted_result
        existing.x2_applied = bool(pred_data.x2_apply)
        db.commit()
        db.refresh(existing)
        target = existing
    else:
        target = Prediction(
            id=str(uuid.uuid4()),
            user_id=user.id,
            league_id=tunisian_league.id,
            match_id=pred_data.match_id,
            predicted_home_goals=pred_data.predicted_home_goals,
            predicted_away_goals=pred_data.predicted_away_goals,
            predicted_result=predicted_result,
            x2_applied=bool(pred_data.x2_apply),
        )
        db.add(target)
        db.commit()
        db.refresh(target)

    return PredictionResponse(
        id=target.id, match_id=pred_data.match_id,
        predicted_home_goals=target.predicted_home_goals, predicted_away_goals=target.predicted_away_goals,
        points_earned=target.points_earned, created_at=target.created_at,
    )


@app.get("/user/predictions")
def get_user_predictions(
    league_id: Optional[str] = None,  # kept for backward compatibility, no longer used to filter
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    """All of the current user's predictions (account-wide), with full points breakdown."""
    user = get_current_user(authorization, db)

    predictions = db.query(Prediction).filter(Prediction.user_id == user.id).all()

    result = []
    for pred in predictions:
        match = db.query(Match).filter(Match.id == pred.match_id).first()
        if not match:
            continue

        base_points = 0
        if match.status == "finished":
            if pred.predicted_result == '1':
                base_points = match.odds_home if match.home_goals > match.away_goals else 0
            elif pred.predicted_result == 'X':
                base_points = match.odds_draw if match.home_goals == match.away_goals else 0
            else:
                base_points = match.odds_away if match.home_goals < match.away_goals else 0

        # Potential points if match hasn't finished yet (what they'd earn if the result is correct)
        potential_base = 0
        if match.status != "finished":
            if pred.predicted_result == '1':
                potential_base = match.odds_home
            elif pred.predicted_result == 'X':
                potential_base = match.odds_draw
            else:
                potential_base = match.odds_away

        result.append({
            "id": pred.id,
            "match_id": pred.match_id,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "gameweek": match.gameweek,
            "kickoff_time": match.kickoff_time,
            "predicted_home_goals": pred.predicted_home_goals,
            "predicted_away_goals": pred.predicted_away_goals,
            "actual_home_goals": match.home_goals if match.status == "finished" else None,
            "actual_away_goals": match.away_goals if match.status == "finished" else None,
            "match_status": match.status,
            "base_points": base_points,
            "potential_base_points": potential_base,
            "exact_bonus": pred.rarity_bonus,
            "x2_multiplier": 2 if pred.x2_applied else 1,
            "points_earned": pred.points_earned,
            "is_exact_match": pred.is_exact_match,
            "x2_applied": pred.x2_applied,
            "created_at": pred.created_at,
        })
    return result

# Add this endpoint to main.py (after the get_user_predictions endpoint, around line 1142)

@app.get("/users/{user_id}/predictions/finished")
def get_user_finished_predictions(user_id: str, db: Session = Depends(get_db)):
    """Get another user's predictions for finished matches only."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    predictions = db.query(Prediction).filter(Prediction.user_id == user_id).all()

    result = []
    for pred in predictions:
        match = db.query(Match).filter(Match.id == pred.match_id).first()
        if not match or match.status != "finished":
            continue

        base_points = 0
        if pred.predicted_result == '1':
            base_points = match.odds_home if match.home_goals > match.away_goals else 0
        elif pred.predicted_result == 'X':
            base_points = match.odds_draw if match.home_goals == match.away_goals else 0
        else:
            base_points = match.odds_away if match.home_goals < match.away_goals else 0

        result.append({
            "id": pred.id,
            "match_id": pred.match_id,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "gameweek": match.gameweek,
            "predicted_home_goals": pred.predicted_home_goals,
            "predicted_away_goals": pred.predicted_away_goals,
            "actual_home_goals": match.home_goals,
            "actual_away_goals": match.away_goals,
            "points_earned": pred.points_earned,
            "is_exact_match": pred.is_exact_match,
        })
    
    return sorted(result, key=lambda x: x['gameweek'])

@app.get("/predictions/x2-status/{gameweek}")
def check_x2_status(
    gameweek: int,
    league_id: Optional[str] = None,  # kept for backward compatibility, no longer used to filter
    authorization: str = Header(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(authorization, db)

    x2_prediction = db.query(Prediction).join(Match, Prediction.match_id == Match.id).filter(
        Prediction.user_id == user.id,
        Prediction.x2_applied == True,  # noqa: E712
        Match.gameweek == gameweek,
    ).first()

    if x2_prediction:
        match = db.query(Match).filter(Match.id == x2_prediction.match_id).first()
        return {"x2_used": True, "used_for_match": f"{match.home_team} vs {match.away_team}", "match_id": match.id}
    return {"x2_used": False, "used_for_match": None, "match_id": None}


# ============ ADMIN: RESULTS ============
@app.put("/admin/matches/{match_id}/result")
def set_match_result(
    match_id: str,
    home_goals: int,
    away_goals: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    user = get_current_user(authorization, db)
    require_admin(user)

    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    predictions_updated = apply_match_result(match, home_goals, away_goals, db)

    return {
        "message": "Match result set and points calculated",
        "match_id": match_id,
        "score": f"{home_goals}-{away_goals}",
        "predictions_updated": predictions_updated,
    }


@app.post("/admin/results/sync")
def sync_results_now(authorization: str = Header(None), db: Session = Depends(get_db)):
    """Allow an administrator to run the final-score import immediately."""
    user = get_current_user(authorization, db)
    require_admin(user)
    return sync_finished_match_results()


@app.put("/admin/matches/{match_id}/reset")
def reset_match_result(match_id: str, authorization: str = Header(None), db: Session = Depends(get_db)):
    user = get_current_user(authorization, db)
    require_admin(user)

    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")

    match_name = f"{match.home_team} vs {match.away_team}"
    match.home_goals = None
    match.away_goals = None
    match.status = "upcoming"

    predictions = db.query(Prediction).filter(Prediction.match_id == match_id).all()
    for pred in predictions:
        pred.points_earned = 0
        pred.is_exact_match = False
        pred.rarity_bonus = 0
    db.commit()

    recalc_all_members_points(db)

    return {
        "message": f"Match result reset: {match_name}",
        "match_id": match_id,
        "predictions_reset": len(predictions),
    }


@app.post("/admin/send-notification", response_model=SendNotificationResponse)
def send_notification(
    request: SendNotificationRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Admin endpoint to send notifications to all users."""
    
    # Verify admin using EMAIL instead of username
    ADMIN_EMAILS = {"bblaiej@gmail.com"}  # Add your admin emails here
    
    if current_user.email not in ADMIN_EMAILS:
        raise HTTPException(status_code=403, detail="Admin access required")
    
    # Rest of the function stays the same...
    # Get all verified users
    users = db.query(User).filter(User.is_verified == True).all()
    
    if not users:
        return SendNotificationResponse(message="No users to notify", emails_sent=0, success=False)
    
    emails_sent = 0
    
    if request.notification_type == "gameweek_reminder":
        gameweek = request.gameweek or 1
        
        for user in users:
            try:
                send_email(
                    email=user.email,
                    subject=f"⏰ Pronos Tunisie — Journée {gameweek} démarre demain!",
                    html=generate_gameweek_reminder_email(user.username, gameweek)
                )
                emails_sent += 1
                print(f"[INFO] Gameweek reminder sent to {user.email}")
            except Exception as e:
                print(f"[ERROR] Failed to send email to {user.email}: {e}")
        
        return SendNotificationResponse(
            message=f"Gameweek {gameweek} reminder sent to {emails_sent} users",
            emails_sent=emails_sent,
            success=emails_sent > 0
        )
    
    return SendNotificationResponse(message="Unknown notification type", emails_sent=0, success=False)


# ============ HEALTH ============
@app.get("/health")
def health():
    return {"status": "ok", "final_score_sync_configured": bool(API_FOOTBALL_KEY)}


score_sync_scheduler = BackgroundScheduler(timezone="UTC")


@app.on_event("startup")
def start_score_sync_scheduler():
    if API_FOOTBALL_KEY and not score_sync_scheduler.running:
        score_sync_scheduler.add_job(
            sync_finished_match_results,
            "interval",
            minutes=RESULT_SYNC_INTERVAL_MINUTES,
            id="api-football-final-score-sync",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
        score_sync_scheduler.start()
        logger.info("Final-score sync scheduled every %s minutes", RESULT_SYNC_INTERVAL_MINUTES)


@app.on_event("shutdown")
def stop_score_sync_scheduler():
    if score_sync_scheduler.running:
        score_sync_scheduler.shutdown(wait=False)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
