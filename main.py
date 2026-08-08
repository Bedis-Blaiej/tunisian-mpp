"""
Tunisian Score Prediction App - FastAPI Backend
Starter template for 2-week MVP
"""

from fastapi import FastAPI, HTTPException, Depends, status
from fastapi import Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
from typing import List, Optional
import jwt
import os
from passlib.context import CryptContext
from dotenv import load_dotenv
import uuid

load_dotenv()

# ============ CONFIG ============
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://user:password@localhost/tunisian_mpp")
SECRET_KEY = os.getenv("SECRET_KEY", "your-super-secret-key-change-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24 * 30  # 30 days

engine = create_engine(DATABASE_URL, echo=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

app = FastAPI(title="Tunisian Score Prediction API")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://localhost:5173",
        "https://tunisian-mpp-frontend.vercel.app"
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
    password_hash = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class League(Base):
    __tablename__ = "leagues"
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100))
    invite_code = Column(String(8), unique=True, index=True)
    created_by = Column(String, ForeignKey("users.id"))
    season = Column(Integer, default=2025)
    created_at = Column(DateTime, default=datetime.utcnow)
    status = Column(String(20), default="active")  # active, completed


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
    odds_home = Column(Integer)  # Points for home win
    odds_draw = Column(Integer)  # Points for draw
    odds_away = Column(Integer)  # Points for away win


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


# Create tables
Base.metadata.create_all(bind=engine)


# ============ PYDANTIC SCHEMAS ============
class UserRegister(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    created_at: datetime


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


# ============ UTILITY FUNCTIONS ============
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
    to_encode = {"sub": user_id, "exp": expire}
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> str:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")



def get_current_user(authorization: str = Header(None), db: Session = Depends(get_db)) -> User:
    """Extract user from Authorization header"""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing token")
    
    # Token format: "Bearer <token>"
    if authorization.startswith("Bearer "):
        token = authorization[7:]
    else:
        token = authorization
    
    user_id = verify_token(token)
    user = db.query(User).filter(User.id == user_id).first()
    
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    return user


def generate_invite_code() -> str:
    """Generate 6-char invite code"""
    import random
    import string
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))


# ============ POINTS CALCULATION ============
def calculate_points(prediction, match, all_predictions_for_match):
    """
    Calculate points for one prediction
    """
    
    # Get actual result
    if match.home_goals is None or match.away_goals is None:
        return {'base_points': 0, 'exact_bonus': 0, 'total': 0, 'is_exact': False}
    
    actual_score = f"{match.home_goals}-{match.away_goals}"
    predicted_score = f"{prediction.predicted_home_goals}-{prediction.predicted_away_goals}"
    
    # Determine actual result
    if match.home_goals > match.away_goals:
        actual_result = '1'
    elif match.home_goals < match.away_goals:
        actual_result = '2'
    else:
        actual_result = 'X'
    
    # Step 1: Result points
    if prediction.predicted_result != actual_result:
        return {'base_points': 0, 'exact_bonus': 0, 'total': 0, 'is_exact': False}
    
    # Get base points based on result
    if actual_result == '1':
        base_points = match.odds_home
    elif actual_result == 'X':
        base_points = match.odds_draw
    else:
        base_points = match.odds_away
    
    # Step 2: Exact score bonus
    exact_bonus = 0
    is_exact = False
    
    if predicted_score == actual_score:
        is_exact = True
        # Count how many predicted this exact score
        exact_count = sum(
            1 for p in all_predictions_for_match 
            if f"{p.predicted_home_goals}-{p.predicted_away_goals}" == actual_score
        )
        
        # Rarity bonus: fewer predictions = higher bonus
        total_predictions = len(all_predictions_for_match)
        rarity_multiplier = max(1.0, 2.0 - (exact_count / max(1, total_predictions)))
        exact_bonus = int(base_points * rarity_multiplier * 0.5)  # 50% bonus max
    
    # Step 3: Apply X2 if used
    total = base_points + exact_bonus
    if prediction.x2_applied:
        total = total * 2
    
    return {
        'base_points': base_points,
        'exact_bonus': exact_bonus,
        'total': total,
        'is_exact': is_exact
    }


def recalculate_league_standings(league_id, db):
    """Recalculate all scores in a league"""
    members = db.query(LeagueMember).filter(LeagueMember.league_id == league_id).all()
    
    for member in members:
        predictions = db.query(Prediction).filter(
            Prediction.user_id == member.user_id,
            Prediction.league_id == league_id
        ).all()
        
        total_points = 0
        for pred in predictions:
            match = db.query(Match).filter(Match.id == pred.match_id).first()
            if match and match.status == "finished":
                all_preds = db.query(Prediction).filter(
                    Prediction.match_id == match.id,
                    Prediction.league_id == league_id
                ).all()
                
                points_data = calculate_points(pred, match, all_preds)
                pred.points_earned = points_data['total']
                pred.is_exact_match = points_data['is_exact']
                pred.rarity_bonus = points_data['exact_bonus']
                total_points += points_data['total']
        
        member.points = total_points
    
    db.commit()


# ============ AUTH ENDPOINTS ============
@app.post("/auth/register", response_model=TokenResponse)
def register(user_data: UserRegister, db: Session = Depends(get_db)):
    """Register new user"""
    
    # Check if user exists
    if db.query(User).filter(User.email == user_data.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    
    # Create user
    new_user = User(
        id=str(uuid.uuid4()),
        username=user_data.username,
        email=user_data.email,
        password_hash=hash_password(user_data.password)
    )
    
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    
    # Return token
    token = create_access_token(new_user.id)
    
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(**{
            "id": new_user.id,
            "username": new_user.username,
            "email": new_user.email,
            "created_at": new_user.created_at
        })
    )


@app.post("/auth/login", response_model=TokenResponse)
def login(user_data: UserLogin, db: Session = Depends(get_db)):
    """Login user"""
    
    user = db.query(User).filter(User.email == user_data.email).first()
    
    if not user or not verify_password(user_data.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    token = create_access_token(user.id)
    
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        user=UserResponse(**{
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "created_at": user.created_at
        })
    )


# ============ LEAGUE ENDPOINTS ============
@app.post("/leagues", response_model=LeagueResponse)
def create_league(
    league_data: LeagueCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Create new league"""
    user = get_current_user(authorization, db)
    
    invite_code = generate_invite_code()
    
    new_league = League(
        id=str(uuid.uuid4()),
        name=league_data.name,
        invite_code=invite_code,
        created_by=user.id
    )
    
    db.add(new_league)
    db.commit()
    db.refresh(new_league)
    
    # Add creator as member
    member = LeagueMember(
        id=str(uuid.uuid4()),
        league_id=new_league.id,
        user_id=user.id
    )
    db.add(member)
    db.commit()
    
    return LeagueResponse(**{
        "id": new_league.id,
        "name": new_league.name,
        "invite_code": new_league.invite_code,
        "created_at": new_league.created_at
    })


@app.post("/leagues/{invite_code}/join", response_model=dict)
def join_league(
    invite_code: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Join league via invite code"""
    user = get_current_user(authorization, db)
    
    league = db.query(League).filter(League.invite_code == invite_code).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    
    # Check if already member
    if db.query(LeagueMember).filter(
        LeagueMember.league_id == league.id,
        LeagueMember.user_id == user.id
    ).first():
        raise HTTPException(status_code=400, detail="Already in league")
    
    # Add as member
    member = LeagueMember(
        id=str(uuid.uuid4()),
        league_id=league.id,
        user_id=user.id
    )
    
    db.add(member)
    db.commit()
    
    return {"message": f"Joined {league.name}", "league_id": league.id}

@app.delete("/leagues/{league_id}")
def delete_league(
    league_id: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Delete a league (creator or admin only)"""
    user = get_current_user(authorization, db)
    
    league = db.query(League).filter(League.id == league_id).first()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    
    # Check if user is creator OR admin
    is_creator = league.created_by == user.id
    is_admin = user.username == "admin"
    
    if not (is_creator or is_admin):
        raise HTTPException(status_code=403, detail="Only league creator or admin can delete")
    
    league_name = league.name
    
    # Delete in correct order to avoid foreign key violations
    # 1. Delete all predictions in this league
    predictions = db.query(Prediction).filter(Prediction.league_id == league_id).all()
    for pred in predictions:
        db.delete(pred)
    
    # 2. Delete all league members
    members = db.query(LeagueMember).filter(LeagueMember.league_id == league_id).all()
    for member in members:
        db.delete(member)
    
    # 3. Delete the league
    db.delete(league)
    
    db.commit()
    
    return {
        "message": f"League '{league_name}' deleted successfully",
        "league_id": league_id,
        "deleted_predictions": len(predictions),
        "deleted_members": len(members)
    }

@app.get("/admin/leagues")
def get_all_leagues(
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get all leagues (admin only)"""
    user = get_current_user(authorization, db)
    
    # Check if admin
    if user.username != "admin":
        raise HTTPException(status_code=403, detail="Admin only")
    
    leagues = db.query(League).all()
    
    result = []
    for league in leagues:
        creator = db.query(User).filter(User.id == league.created_by).first()
        member_count = db.query(LeagueMember).filter(LeagueMember.league_id == league.id).count()
        prediction_count = db.query(Prediction).filter(Prediction.league_id == league.id).count()
        
        result.append({
            "id": league.id,
            "name": league.name,
            "invite_code": league.invite_code,
            "creator": creator.username if creator else "Unknown",
            "created_at": league.created_at,
            "status": league.status,
            "members": member_count,
            "predictions": prediction_count
        })
    
    return result


@app.get("/leagues/{league_id}/standings")
def get_leaderboard(league_id: str, db: Session = Depends(get_db)) -> List[LeaderboardEntry]:
    """Get league leaderboard"""
    
    members = db.query(LeagueMember).filter(
        LeagueMember.league_id == league_id
    ).order_by(LeagueMember.points.desc()).all()
    
    result = []
    for rank, member in enumerate(members, 1):
        user = db.query(User).filter(User.id == member.user_id).first()
        result.append(LeaderboardEntry(
            user_id=member.user_id,
            username=user.username,
            points=member.points,
            rank=rank
        ))
    
    return result


'ADDED MANUALLY'
@app.get("/user/leagues")
def get_user_leagues(authorization: str = Header(None), db: Session = Depends(get_db)):
    """Get all leagues for current user"""
    user = get_current_user(authorization, db)
    
    league_members = db.query(LeagueMember).filter(
        LeagueMember.user_id == user.id
    ).all()
    
    result = []
    for member in league_members:
        league = db.query(League).filter(League.id == member.league_id).first()
        if league:
            result.append({
                "id": league.id,
                "name": league.name,
                "invite_code": league.invite_code,
                "created_at": league.created_at
            })
    
    return result

# ============ MATCH ENDPOINTS ============
@app.post("/admin/matches")
def create_match(
    match_data: MatchCreate,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Admin: Create new match"""
    user = get_current_user(authorization, db)
    
    new_match = Match(
        id=str(uuid.uuid4()),
        home_team=match_data.home_team,
        away_team=match_data.away_team,
        gameweek=match_data.gameweek,
        kickoff_time=match_data.kickoff_time,
        odds_home=match_data.odds_home,
        odds_draw=match_data.odds_draw,
        odds_away=match_data.odds_away
    )
    
    db.add(new_match)
    db.commit()
    db.refresh(new_match)
    
    return MatchResponse(**{
        "id": new_match.id,
        "home_team": new_match.home_team,
        "away_team": new_match.away_team,
        "gameweek": new_match.gameweek,
        "kickoff_time": new_match.kickoff_time,
        "status": new_match.status,
        "home_goals": new_match.home_goals,
        "away_goals": new_match.away_goals
    })


@app.get("/matches", response_model=List[MatchResponse])
def get_matches(gameweek: Optional[int] = None, db: Session = Depends(get_db)):
    """Get all matches, optionally filtered by gameweek"""
    
    query = db.query(Match)
    if gameweek:
        query = query.filter(Match.gameweek == gameweek)
    
    matches = query.order_by(Match.kickoff_time).all()
    
    return [
        MatchResponse(**{
            "id": m.id,
            "home_team": m.home_team,
            "away_team": m.away_team,
            "gameweek": m.gameweek,
            "kickoff_time": m.kickoff_time,
            "status": m.status,
            "home_goals": m.home_goals,
            "away_goals": m.away_goals
        })
        for m in matches
    ]


# ============ PREDICTION ENDPOINTS ============
@app.post("/predictions", response_model=PredictionResponse)
def submit_prediction(
    pred_data: PredictionCreate,
    league_id: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Submit or update prediction"""
    user = get_current_user(authorization, db)
    
    match = db.query(Match).filter(Match.id == pred_data.match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    
    # Check if match hasn't started (lockdown 15 min before)
    lockdown_time = match.kickoff_time - timedelta(minutes=15)
    if datetime.utcnow() >= lockdown_time:
        raise HTTPException(status_code=400, detail="Prediction locked - match starting soon")
    
    # Determine result
    if pred_data.predicted_home_goals > pred_data.predicted_away_goals:
        predicted_result = '1'
    elif pred_data.predicted_home_goals < pred_data.predicted_away_goals:
        predicted_result = '2'
    else:
        predicted_result = 'X'
    
    # Check if prediction exists
    existing = db.query(Prediction).filter(
        Prediction.user_id == user.id,
        Prediction.league_id == league_id,
        Prediction.match_id == pred_data.match_id
    ).first()
    
    if existing:
        # Update
        existing.predicted_home_goals = pred_data.predicted_home_goals
        existing.predicted_away_goals = pred_data.predicted_away_goals
        existing.predicted_result = predicted_result
        if pred_data.x2_apply:
            existing.x2_applied = True
        db.commit()
        db.refresh(existing)
        return PredictionResponse(**{
            "id": existing.id,
            "match_id": pred_data.match_id,
            "predicted_home_goals": existing.predicted_home_goals,
            "predicted_away_goals": existing.predicted_away_goals,
            "points_earned": existing.points_earned,
            "created_at": existing.created_at
        })
    else:
        # Create
        prediction = Prediction(
            id=str(uuid.uuid4()),
            user_id=user.id,
            league_id=league_id,
            match_id=pred_data.match_id,
            predicted_home_goals=pred_data.predicted_home_goals,
            predicted_away_goals=pred_data.predicted_away_goals,
            predicted_result=predicted_result,
            x2_applied=pred_data.x2_apply or False
        )
        db.add(prediction)
        db.commit()
        db.refresh(prediction)
        
        return PredictionResponse(**{
            "id": prediction.id,
            "match_id": pred_data.match_id,
            "predicted_home_goals": prediction.predicted_home_goals,
            "predicted_away_goals": prediction.predicted_away_goals,
            "points_earned": prediction.points_earned,
            "created_at": prediction.created_at
        })

@app.get("/user/predictions")
def get_user_predictions(
    league_id: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Get all predictions for current user in a league with detailed breakdown"""
    user = get_current_user(authorization, db)
    
    predictions = db.query(Prediction).filter(
        Prediction.user_id == user.id,
        Prediction.league_id == league_id
    ).all()
    
    result = []
    for pred in predictions:
        match = db.query(Match).filter(Match.id == pred.match_id).first()
        
        # Calculate breakdown
        base_points = 0
        if match.status == "finished":
            if pred.predicted_result == '1':
                base_points = match.odds_home if match.home_goals > match.away_goals else 0
            elif pred.predicted_result == 'X':
                base_points = match.odds_draw if match.home_goals == match.away_goals else 0
            else:
                base_points = match.odds_away if match.home_goals < match.away_goals else 0
        
        result.append({
            "id": pred.id,
            "match_id": pred.match_id,
            "home_team": match.home_team if match else "Unknown",
            "away_team": match.away_team if match else "Unknown",
            "predicted_home_goals": pred.predicted_home_goals,
            "predicted_away_goals": pred.predicted_away_goals,
            "actual_home_goals": match.home_goals if match.status == "finished" else None,
            "actual_away_goals": match.away_goals if match.status == "finished" else None,
            "match_status": match.status,
            "base_points": base_points,
            "exact_bonus": pred.rarity_bonus,
            "x2_multiplier": 2 if pred.x2_applied else 1,
            "points_earned": pred.points_earned,
            "is_exact_match": pred.is_exact_match,
            "x2_applied": pred.x2_applied,
            "created_at": pred.created_at
        })
    
    return result




@app.get("/predictions/x2-status/{gameweek}")
def check_x2_status(
    gameweek: int,
    league_id: str,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Check if user already used X2 in this gameweek"""
    user = get_current_user(authorization, db)
    
    x2_prediction = db.query(Prediction).join(Match).filter(
        Prediction.user_id == user.id,
        Prediction.league_id == league_id,
        Prediction.x2_applied == True,
        Match.gameweek == gameweek
    ).first()
    
    if x2_prediction:
        match = db.query(Match).filter(Match.id == x2_prediction.match_id).first()
        return {
            "x2_used": True,
            "used_for_match": f"{match.home_team} vs {match.away_team}",
            "match_id": match.id
        }
    else:
        return {
            "x2_used": False,
            "used_for_match": None,
            "match_id": None
        }
# ============ ADMIN ENDPOINTS ============
@app.put("/admin/matches/{match_id}/result")
def set_match_result(
    match_id: str,
    home_goals: int,
    away_goals: int,
    authorization: str = Header(None),
    db: Session = Depends(get_db)
):
    """Admin: Set match result and auto-calculate points"""
    user = get_current_user(authorization, db)
    
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    
    # Update match with actual result
    match.home_goals = home_goals
    match.away_goals = away_goals
    match.status = "finished"
    db.commit()
    
    # Get all predictions for this match
    all_predictions = db.query(Prediction).filter(
        Prediction.match_id == match_id
    ).all()
    
    # Calculate points for each prediction
    for pred in all_predictions:
        points_data = calculate_points(pred, match, all_predictions)
        pred.points_earned = points_data['total']
        pred.is_exact_match = points_data['is_exact']
        pred.rarity_bonus = points_data['exact_bonus']
    
    db.commit()
    
    # Update league standings for all affected leagues
    leagues_affected = set(p.league_id for p in all_predictions)
    for league_id in leagues_affected:
        recalculate_league_standings(league_id, db)
    
    return {
        "message": "Match result set and points calculated",
        "match_id": match_id,
        "score": f"{home_goals}-{away_goals}",
        "predictions_updated": len(all_predictions)
    }


# ============ HEALTH CHECK ============
@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
