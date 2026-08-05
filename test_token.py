import requests

API_URL = "http://localhost:8000"
ADMIN_TOKEN = "your-jwt-token-here"  # REPLACE WITH YOUR TOKEN!

print(f"Token: {ADMIN_TOKEN}")

if ADMIN_TOKEN == "your-jwt-token-here":
    print("❌ ERROR: Token still says 'your-jwt-token-here'!")
    print("You must copy the REAL token from your browser.\n")
    exit()

headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

print(f"Testing token...\n")
print(f"Headers being sent: {headers}\n")

response = requests.get(f"{API_URL}/auth/me", headers=headers)

print(f"Response Status: {response.status_code}")
print(f"Response: {response.json()}")

if response.status_code == 200:
    print("\n✅ Token is VALID!")
else:
    print("\n❌ Token is INVALID!")