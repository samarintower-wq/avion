import os
import re
import secrets
import hashlib
import hmac
import base64
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from urllib.parse import quote

import httpx
from fastapi import FastAPI, HTTPException, Depends, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, EmailStr, Field
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

APP_NAME = "SkyWorld Simulator"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_NAME = os.getenv("MONGODB_DB_NAME", "skyworld").strip()
SECRET_KEY = os.getenv("SECRET_KEY", "").strip()
CESIUM_ION_TOKEN = os.getenv("CESIUM_ION_TOKEN", "").strip()
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "").strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "").strip()
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "").strip()
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "").strip()
CF_IMAGE_MODEL = os.getenv("CLOUDFLARE_IMAGE_MODEL", "@cf/black-forest-labs/flux-1-schnell")
client = None
db = None

AIRCRAFT = [
    {"id":"airbus_a320","name":"Airbus A320","type":"Passenger","cruise_kmh":840,"range_km":6100,"description":"Короткие и среднемагистральные рейсы."},
    {"id":"boeing_737_800","name":"Boeing 737-800","type":"Passenger","cruise_kmh":842,"range_km":5435,"description":"Популярный узкофюзеляжный авиалайнер."},
    {"id":"airbus_a350_900","name":"Airbus A350-900","type":"Long-haul","cruise_kmh":903,"range_km":15000,"description":"Современный дальнемагистральный самолёт."},
    {"id":"boeing_787","name":"Boeing 787 Dreamliner","type":"Long-haul","cruise_kmh":903,"range_km":14140,"description":"Дальние рейсы с высокой топливной эффективностью."},
    {"id":"boeing_747_400","name":"Boeing 747-400","type":"Widebody","cruise_kmh":920,"range_km":13450,"description":"Классический четырёхдвигательный лайнер."},
    {"id":"airbus_a380","name":"Airbus A380","type":"Widebody","cruise_kmh":903,"range_km":15200,"description":"Сверхкрупный пассажирский самолёт."},
    {"id":"cessna_172","name":"Cessna 172","type":"General aviation","cruise_kmh":226,"range_km":1289,"description":"Лёгкий самолёт для спокойных полётов."},
    {"id":"pilatus_pc12","name":"Pilatus PC-12","type":"Turboprop","cruise_kmh":528,"range_km":3417,"description":"Универсальный одномоторный турбовинтовой самолёт."},
    {"id":"f16","name":"F-16 Fighting Falcon","type":"Fighter","cruise_kmh":1500,"range_km":4220,"description":"Высокоскоростной военный самолёт; игровой режим."},
    {"id":"boeing_c17","name":"Boeing C-17 Globemaster III","type":"Transport","cruise_kmh":830,"range_km":4445,"description":"Тяжёлый военно-транспортный самолёт."},
]

def utcnow():
    return datetime.now(timezone.utc)

def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 310_000)
    return f"{salt.hex()}${digest.hex()}"

def password_verify(password: str, stored: str) -> bool:
    try:
        salt_hex, expected = stored.split("$", 1)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 310_000).hex()
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False

def token_digest(token: str) -> str:
    key = SECRET_KEY.encode() if SECRET_KEY else b"dev-only-not-for-production"
    return hmac.new(key, token.encode(), hashlib.sha256).hexdigest()

def public_user(user: dict) -> dict:
    return {
        "nickname": user["nickname"],
        "email": user["email"],
        "selected_aircraft": user.get("selected_aircraft", "airbus_a320"),
        "earth_mode": user.get("earth_mode", "realistic"),
        "departure": user.get("departure"),
        "destination": user.get("destination"),
        "created_at": user.get("created_at").isoformat() if user.get("created_at") else None,
        "cesium_token_saved": bool(user.get("cesium_user_token")),
    }

@asynccontextmanager
async def lifespan(app: FastAPI):
    global client, db
    if DATABASE_URL:
        client = AsyncIOMotorClient(DATABASE_URL, serverSelectionTimeoutMS=15000, connectTimeoutMS=15000, appname="SkyWorld")
        try:
            await client.admin.command("ping")
            db = client[DB_NAME]
            await db.users.create_index("email", unique=True)
            await db.users.create_index("nickname", unique=True)
            await db.users.create_index("session_hash", sparse=True)
        except Exception:
            client.close()
            client = db = None
            raise
    yield
    if client:
        client.close()

app = FastAPI(title=APP_NAME, version="0.1.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")

class RegisterBody(BaseModel):
    nickname: str = Field(min_length=3, max_length=24, pattern=r"^[A-Za-z0-9_-]+$")
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class LoginBody(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

class PreferenceBody(BaseModel):
    selected_aircraft: str | None = None
    earth_mode: str | None = None
    departure: dict | None = None
    destination: dict | None = None

class TokenBody(BaseModel):
    token: str = Field(min_length=10, max_length=2000)

class CoordinatesBody(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)

class ImageBody(CoordinatesBody):
    place_name: str = Field(default="Unknown location", max_length=160)

async def current_user(request: Request):
    if db is None:
        raise HTTPException(503, "MongoDB is not configured. Set DATABASE_URL in Render.")
    token = request.cookies.get("skyworld_session")
    if not token:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        raise HTTPException(401, "Please sign in.")
    user = await db.users.find_one({"session_hash": token_digest(token)})
    if not user:
        raise HTTPException(401, "Session expired. Please sign in again.")
    return user

@app.get("/", response_class=HTMLResponse)
async def home():
    with open("index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())

@app.get("/health")
async def health():
    return {"status": "ok", "database_configured": bool(DATABASE_URL), "cesium_configured": bool(CESIUM_ION_TOKEN)}

@app.get("/api/config")
async def public_config(user=Depends(current_user)):
    # CesiumJS necessarily receives the viewer token in the browser. Accept only a
    # dedicated, URL-restricted public viewer token here, never a master/admin token.
    personal_token = (user.get("cesium_user_token") or "").strip()
    return {
        "cesium_token": personal_token or CESIUM_ION_TOKEN,
        "personal_cesium_token_saved": bool(personal_token),
        "openweather_configured": bool(OPENWEATHER_API_KEY),
    }

@app.get("/api/aircraft")
async def aircraft_catalog():
    return {"aircraft": AIRCRAFT}

@app.post("/api/register")
async def register(body: RegisterBody, response: Response):
    if db is None:
        raise HTTPException(503, "MongoDB is not configured. Add DATABASE_URL in Render.")
    nickname = body.nickname.strip()
    email = str(body.email).lower().strip()
    token = secrets.token_urlsafe(48)
    doc = {
        "nickname": nickname,
        "email": email,
        "password_hash": password_hash(body.password),
        "session_hash": token_digest(token),
        "selected_aircraft": "airbus_a320",
        "earth_mode": "realistic",
        "departure": None,
        "destination": None,
        "created_at": utcnow(),
    }
    try:
        await db.users.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "This email or nickname is already registered.")
    response.set_cookie("skyworld_session", token, httponly=True, secure=True, samesite="lax", max_age=60*60*24*30, path="/")
    return {"ok": True, "user": public_user(doc)}

@app.post("/api/login")
async def login(body: LoginBody, response: Response):
    if db is None:
        raise HTTPException(503, "MongoDB is not configured. Add DATABASE_URL in Render.")
    user = await db.users.find_one({"email": str(body.email).lower().strip()})
    if not user or not password_verify(body.password, user.get("password_hash", "")):
        raise HTTPException(401, "Incorrect email or password.")
    token = secrets.token_urlsafe(48)
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"session_hash": token_digest(token)}})
    response.set_cookie("skyworld_session", token, httponly=True, secure=True, samesite="lax", max_age=60*60*24*30, path="/")
    return {"ok": True, "user": public_user(user)}

@app.post("/api/logout")
async def logout(response: Response, user=Depends(current_user)):
    await db.users.update_one({"_id": user["_id"]}, {"$unset": {"session_hash": ""}})
    response.delete_cookie("skyworld_session", path="/")
    return {"ok": True}

@app.get("/api/me")
async def me(user=Depends(current_user)):
    return {"user": public_user(user)}

@app.put("/api/preferences")
async def update_preferences(body: PreferenceBody, user=Depends(current_user)):
    update = {}
    if body.selected_aircraft is not None:
        if body.selected_aircraft not in {a["id"] for a in AIRCRAFT}:
            raise HTTPException(400, "Unknown aircraft.")
        update["selected_aircraft"] = body.selected_aircraft
    if body.earth_mode is not None:
        if body.earth_mode not in ("realistic", "weather"):
            raise HTTPException(400, "earth_mode must be realistic or weather.")
        update["earth_mode"] = body.earth_mode
    for field in ("departure", "destination"):
        value = getattr(body, field)
        if value is not None:
            try:
                lat, lon = float(value["latitude"]), float(value["longitude"])
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError()
                update[field] = {"latitude": lat, "longitude": lon, "label": str(value.get("label", ""))[:160]}
            except Exception:
                raise HTTPException(400, f"Invalid {field} coordinates.")
    if update:
        await db.users.update_one({"_id": user["_id"]}, {"$set": update})
    fresh = await db.users.find_one({"_id": user["_id"]})
    return {"ok": True, "user": public_user(fresh)}

@app.get("/api/geocode")
async def geocode(q: str, user=Depends(current_user)):
    q = q.strip()
    if not q or len(q) > 160:
        raise HTTPException(400, "Enter a place name or coordinates.")
    match = re.fullmatch(r"\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*", q)
    if match:
        lat, lon = float(match.group(1)), float(match.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"latitude": lat, "longitude": lon, "label": q}
        raise HTTPException(400, "Coordinates are out of range.")
    # Open-Meteo geocoding is used as a no-key starter geocoder.
    async with httpx.AsyncClient(timeout=12) as http:
        r = await http.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": q, "count": 1, "language": "en", "format": "json"})
        if r.status_code != 200:
            raise HTTPException(502, "Geocoding service is unavailable.")
        data = r.json()
    results = data.get("results") or []
    if not results:
        raise HTTPException(404, "Place not found.")
    item = results[0]
    label = ", ".join(str(v) for v in (item.get("name"), item.get("admin1"), item.get("country") ) if v)
    return {"latitude": item["latitude"], "longitude": item["longitude"], "label": label}

@app.get("/api/weather")
async def weather(latitude: float, longitude: float, user=Depends(current_user)):
    if not OPENWEATHER_API_KEY:
        raise HTTPException(503, "Set OPENWEATHER_API_KEY in Render.")
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get("https://api.openweathermap.org/data/2.5/weather", params={"lat": latitude, "lon": longitude, "appid": OPENWEATHER_API_KEY, "units": "metric"})
    if r.status_code != 200:
        raise HTTPException(r.status_code if r.status_code < 500 else 502, "OpenWeather request failed.")
    data = r.json()
    return {"name": data.get("name"), "temperature_c": data.get("main", {}).get("temp"), "feels_like_c": data.get("main", {}).get("feels_like"), "description": (data.get("weather") or [{}])[0].get("description"), "wind_mps": data.get("wind", {}).get("speed"), "humidity": data.get("main", {}).get("humidity")}

@app.get("/api/weather-tiles/{layer}/{z}/{x}/{y}.png")
async def weather_tile(layer: str, z: int, x: int, y: int, user=Depends(current_user)):
    if not OPENWEATHER_API_KEY:
        raise HTTPException(503, "Set OPENWEATHER_API_KEY in Render.")
    allowed = {"temp_new", "precipitation_new", "clouds_new", "wind_new"}
    if layer not in allowed:
        raise HTTPException(400, "Unsupported weather layer.")
    url = f"https://tile.openweathermap.org/map/{layer}/{z}/{x}/{y}.png"
    async with httpx.AsyncClient(timeout=15) as http:
        r = await http.get(url, params={"appid": OPENWEATHER_API_KEY})
    if r.status_code != 200:
        raise HTTPException(502, "Weather tile unavailable. Check your OpenWeather plan and key.")
    from fastapi.responses import Response as FastResponse
    return FastResponse(content=r.content, media_type="image/png", headers={"Cache-Control": "public, max-age=300"})

@app.post("/api/describe")
async def describe_location(body: CoordinatesBody, user=Depends(current_user)):
    if not GROQ_API_KEY:
        raise HTTPException(503, "Set GROQ_API_KEY in Render.")
    system_prompt = (
        "You are a careful geography guide in a flight simulator. Describe the likely region using the coordinates. "
        "Do not pretend to know live facts or exact landmarks without evidence. State uncertainty where appropriate. "
        "Answer in Russian, in 2-4 concise paragraphs."
    )
    payload = {"model": os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"), "messages": [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": f"Describe the area near latitude {body.latitude:.5f}, longitude {body.longitude:.5f}. Give geographic context and what a pilot might observe."}
    ], "temperature": 0.5, "max_tokens": 450}
    async with httpx.AsyncClient(timeout=35) as http:
        r = await http.post("https://api.groq.com/openai/v1/chat/completions", headers={"Authorization": f"Bearer {GROQ_API_KEY}"}, json=payload)
    if r.status_code != 200:
        raise HTTPException(502, "Groq request failed. Check key, model and account limits.")
    data = r.json()
    return {"description": data["choices"][0]["message"]["content"]}

@app.post("/api/generate-image")
async def generate_image(body: ImageBody, user=Depends(current_user)):
    if not CLOUDFLARE_ACCOUNT_ID or not CLOUDFLARE_API_TOKEN:
        raise HTTPException(503, "Set CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN in Render.")
    prompt = (
        f"Photorealistic cinematic aerial landscape near {body.place_name}, "
        f"geographic coordinates {body.latitude:.4f}, {body.longitude:.4f}. "
        "Detailed natural terrain, realistic atmosphere, high quality travel photography. "
        "Illustrative reconstruction, not a claim to be a real satellite photograph."
    )[:2000]
    url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{CF_IMAGE_MODEL}"
    async with httpx.AsyncClient(timeout=90) as http:
        r = await http.post(url, headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"}, json={"prompt": prompt})
    if r.status_code != 200:
        raise HTTPException(502, "Cloudflare image generation failed. Check model access and limits.")
    data = r.json()
    result = data.get("result") or {}
    image_b64 = result.get("image")
    if not image_b64:
        raise HTTPException(502, "Cloudflare did not return an image.")
    return {"image_data_url": "data:image/jpeg;base64," + image_b64, "prompt": prompt}

@app.put("/api/cesium-token")
async def save_cesium_token(body: TokenBody, user=Depends(current_user)):
    # This endpoint is for a player's own URL-restricted public viewer token only.
    # Do NOT accept an administrative/master token with tokens:write permissions.
    await db.users.update_one({"_id": user["_id"]}, {"$set": {"cesium_user_token": body.token.strip()}})
    return {"ok": True, "message": "Токен сохранён. Перезагрузи глобус, чтобы применить его."}

@app.delete("/api/cesium-token")
async def delete_cesium_token(user=Depends(current_user)):
    await db.users.update_one({"_id": user["_id"]}, {"$unset": {"cesium_user_token": ""}})
    return {"ok": True}

@app.exception_handler(Exception)
async def unhandled_exception(request: Request, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "Internal server error. Check Render logs."})
