import csv
import requests
import json
from datetime import datetime

API_URL = "https://tunisian-mpp-production.up.railway.app"  # Your Railway URL
ADMIN_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIzNDlkOWE1Yy01NzhhLTQ5OGItYjk0Ny1lNTdjZWU5NjY1YmIiLCJleHAiOjE3ODg1Njg4OTd9.WK2pgQsNdXd_-76Zb14s5hrA2ERvAqimvDv4IN2F5VY"  # REPLACE WITH YOUR TOKEN!

print(f"API URL: {API_URL}")
print(f"Token: {ADMIN_TOKEN[:20]}..." if ADMIN_TOKEN != "your-jwt-token-here" else "⚠️ WARNING: No token set!")

if ADMIN_TOKEN == "your-jwt-token-here":
    print("\n❌ ERROR: You must replace 'your-jwt-token-here' with your actual JWT token!")
    print("Go to http://localhost:5173, login, open DevTools (F12), and copy the token from Local Storage.\n")
    exit()

# Calculate odds based on team strength (simple algorithm)
def calculate_odds(home_team, away_team):
    """Simple odds calculation based on team history"""
    
    # Strong teams (favorites)
    strong_teams = ["Espérance", "Club Africain", "Club Sfaxien", "Étoile du Sahel"]
    
    home_is_strong = home_team in strong_teams
    away_is_strong = away_team in strong_teams
    
    if home_is_strong and not away_is_strong:
        # Home favorite
        return {"home": 55, "draw": 80, "away": 130}
    elif away_is_strong and not home_is_strong:
        # Away favorite
        return {"home": 120, "draw": 80, "away": 60}
    elif home_is_strong and away_is_strong:
        # Both strong (evenly matched)
        return {"home": 70, "draw": 75, "away": 85}
    else:
        # Both weak (evenly matched)
        return {"home": 75, "draw": 70, "away": 80}

headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

print(f"\nLoading matches from CSV...\n")

# Read CSV
matches_loaded = 0
matches_failed = 0

try:
    with open('matches.csv', 'r', encoding='utf-8') as csvfile:
        reader = csv.DictReader(csvfile)
        
        for row in reader:
            try:
                # Parse data from CSV
                gameweek = int(row['gameweek'])
                date = row['date']  # YYYY-MM-DD
                time = row['time']  # HH:MM
                home_team = row['home_team'].strip()
                away_team = row['away_team'].strip()
                
                # Combine date + time into ISO format
                kickoff_time = f"{date}T{time}:00Z"
                
                # Calculate odds
                odds = calculate_odds(home_team, away_team)
                
                # Create match object
                match = {
                    "home_team": home_team,
                    "away_team": away_team,
                    "gameweek": gameweek,
                    "kickoff_time": kickoff_time,
                    "odds_home": odds["home"],
                    "odds_draw": odds["draw"],
                    "odds_away": odds["away"]
                }
                
                # Send to API
                response = requests.post(
                    f"{API_URL}/admin/matches",
                    json=match,
                    headers=headers
                )
                
                if response.status_code == 200:
                    print(f"✅ GW{gameweek}: {home_team:20} vs {away_team:20}")
                    matches_loaded += 1
                else:
                    print(f"❌ FAILED: {home_team} vs {away_team}")
                    print(f"   Status: {response.status_code}")
                    print(f"   Response: {response.text}\n")
                    matches_failed += 1
            
            except Exception as e:
                print(f"❌ ERROR processing row: {e}\n")
                matches_failed += 1

except FileNotFoundError:
    print("❌ ERROR: matches.csv not found in current directory!")
    print("Make sure matches.csv is in the same folder as this script.\n")
    exit()

print(f"\n{'='*60}")
print(f"RESULTS:")
print(f"✅ Loaded: {matches_loaded}")
print(f"❌ Failed: {matches_failed}")
print(f"Total: {matches_loaded + matches_failed}")
print(f"{'='*60}")

print(f"\nDone! Check http://localhost:5173 to see matches")