import requests
import json
from datetime import datetime, timedelta

API_URL = "http://localhost:8000"
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzZjNjMmM3Yy03MzUzLTRlMjYtOTY3Yy00ODEwYzRiY2M4NTMiLCJleHAiOjE3ODg1MTgxNjd9.UNsxHhwhzfZ-hvhlQYlxNtknUcsJC3S8yNy_ebaTonM"  # REPLACE WITH YOUR TOKEN!

print(f"API URL: {API_URL}")
print(f"Token: {ADMIN_TOKEN[:20]}..." if ADMIN_TOKEN != "your-jwt-token-here" else "⚠️ WARNING: No token set!")

if ADMIN_TOKEN == "your-jwt-token-here":
    print("\n❌ ERROR: You must replace 'your-jwt-token-here' with your actual JWT token!")
    print("Go to http://localhost:5173, login, open DevTools (F12), and copy the token from Local Storage.\n")
    exit()

matches = [
    {
        "home_team": "Espérance",
        "away_team": "Club Africain",
        "gameweek": 1,
        "kickoff_time": "2025-09-15T20:00:00Z",
        "odds_home": 65,
        "odds_draw": 72,
        "odds_away": 110
    },
    {
        "home_team": "Club Sfaxien",
        "away_team": "Stade Tunisien",
        "gameweek": 1,
        "kickoff_time": "2025-09-16T20:00:00Z",
        "odds_home": 85,
        "odds_draw": 65,
        "odds_away": 95
    },
    {
        "home_team": "Étoile du Sahel",
        "away_team": "Olympique Béja",
        "gameweek": 1,
        "kickoff_time": "2025-09-17T20:00:00Z",
        "odds_home": 75,
        "odds_draw": 70,
        "odds_away": 90
    },
]

headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

print(f"\nLoading {len(matches)} matches...\n")

for match in matches:
    try:
        response = requests.post(
            f"{API_URL}/admin/matches",
            json=match,
            headers=headers
        )
        
        if response.status_code == 200:
            print(f"✅ Created: {match['home_team']} vs {match['away_team']}")
        else:
            print(f"❌ FAILED: {match['home_team']} vs {match['away_team']}")
            print(f"   Status: {response.status_code}")
            print(f"   Response: {response.text}\n")
    
    except Exception as e:
        print(f"❌ ERROR: {match['home_team']} vs {match['away_team']}")
        print(f"   Error: {e}\n")

print("\nDone! Check http://localhost:8000/matches")