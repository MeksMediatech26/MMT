# ============================================
# IMPORTS - START
# ============================================

# Standard Library Imports
import os
import logging
import openai
import asyncio
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Optional, List

# Third-Party Imports
from dotenv import load_dotenv
import jwt
import motor.motor_asyncio
import cloudinary
import cloudinary.uploader
import httpx
from passlib.context import CryptContext
from bson import ObjectId
from redis import Redis

# FastAPI Imports
from fastapi import FastAPI, HTTPException, Depends, status, UploadFile, File, Form, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.exceptions import RequestValidationError
from fastapi import Form

# Starlette Imports
from starlette.exceptions import HTTPException as StarletteHTTPException

# Pydantic Imports
from pydantic import BaseModel, EmailStr, Field, field_validator





# ============================================
# IMPORTS - END
# ============================================

# Load environment variables
load_dotenv()

# ============================================
# LOGGING CONFIGURATION - START
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logger.info("🔧 Logging system initialized")

# ============================================
# LOGGING CONFIGURATION - END
# ============================================

# ============================================
# ENVIRONMENT CONFIGURATION - START
# ============================================

logger.info("⚙️ Loading environment configuration...")

# JWT Configuration
SECRET_KEY = os.getenv("SECRET_KEY", "your-secret-key-here")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
logger.info(f"✅ JWT Configuration loaded - Token expiry: {ACCESS_TOKEN_EXPIRE_MINUTES} minutes")

# MongoDB Configuration
MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017/enejistats")
client = None
database = None
try:
    client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    database = client.enejistats
    logger.info("✅ MongoDB client initialized successfully")
except Exception as e:
    logger.error(f"❌ MongoDB initialization failed: {e}")

# Cloudinary Configuration
cloudinary_url = os.getenv("CLOUDINARY_URL")
if cloudinary_url:
    try:
        cloudinary.config(
            secure=True,
            cloudinary_url=cloudinary_url
        )
        logger.info("✅ Cloudinary configured successfully")
    except Exception as e:
        logger.error(f"❌ Cloudinary configuration failed: {e}")
else:
    logger.warning("⚠️ CLOUDINARY_URL not set - Image uploads will fail")

# OpenAI Configuration
openai_api_key = os.getenv("OPENAI_API_KEY")
if openai_api_key:
    try:
        openai.api_key = openai_api_key
        logger.info("✅ OpenAI configured successfully")
    except Exception as e:
        logger.error(f"❌ OpenAI configuration failed: {e}")
else:
    logger.warning("⚠️ OPENAI_API_KEY not set - Content enhancement will fail")

# Redis Configuration
REDIS_URL = os.getenv("UPSTASH_REDIS_URL")
REDIS_TOKEN = os.getenv("UPSTASH_REDIS_TOKEN")

redis_client = None

# Log raw variable presence (safe — does not expose sensitive value)
logger.info(f"🔎 Redis URL present: {'YES' if REDIS_URL else 'NO'}")
logger.info(f"🔎 Redis Token present: {'YES' if REDIS_TOKEN else 'NO'}")

if REDIS_URL and REDIS_TOKEN:
    try:
        # Show partial masked token for debugging
        masked_token = REDIS_TOKEN[:6] + "..." if REDIS_TOKEN else "None"

        logger.info(f"🔧 Attempting Redis connection")
        logger.info(f"🔧 Using Redis URL: {REDIS_URL}")
        logger.info(f"🔧 Using Redis Token (masked): {masked_token}")

        redis_client = Redis.from_url(
            REDIS_URL,
            password=REDIS_TOKEN,
            decode_responses=True
        )

        # Test connection
        redis_client.ping()
        logger.info("✅ Redis client initialized and connected successfully")

    except Exception as e:
        # Detailed Redis error classifier
        error_message = str(e)

        if "WRONGPASS" in error_message or "authentication" in error_message.lower():
            logger.error("❌ Redis authentication failed – invalid UPSTASH_REDIS_TOKEN")
        elif "Name or service not known" in error_message or "nodename" in error_message:
            logger.error("❌ Redis host unreachable – DNS resolution failed (bad URL)")
        elif "Timeout" in error_message:
            logger.error("❌ Redis timeout – Upstash unreachable or network issue")
        elif "Connection refused" in error_message:
            logger.error("❌ Redis connection refused – wrong port or Upstash offline")
        else:
            logger.error(f"❌ Redis initialization failed: {error_message}")

        redis_client = None
else:
    logger.warning("⚠️ Redis credentials not configured - Caching disabled")

# Email Configuration
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_EMAIL = os.getenv("SMTP_EMAIL")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD")

if SMTP_EMAIL and SMTP_PASSWORD:
    logger.info(f"✅ Email configuration loaded - SMTP: {SMTP_HOST}:{SMTP_PORT}")
else:
    logger.warning("⚠️ Email credentials not configured - Email notifications disabled")

# Auto-Ping Configuration
PING_PLATFORMS = os.getenv("PING_PLATFORMS", "")
MONITORING_URLS = {}

if PING_PLATFORMS:
    for entry in PING_PLATFORMS.split(","):
        try:
            url_part, interval = entry.split(";", 1)
            interval = int(interval.strip())
            name, url = url_part.split("=", 1)
            MONITORING_URLS[name.strip()] = {
                "url": url.strip(),
                "interval": interval,
                "last_log": None,
                "last_ping": None
            }
            logger.info(f"✅ Auto-ping configured for {name.strip()} - Interval: {interval}s")
        except Exception as e:
            logger.error(f"❌ Invalid PING_PLATFORMS entry '{entry}': {e}")
else:
    logger.info("ℹ️ No auto-ping platforms configured")

logger.info("✅ Environment configuration completed")

# ============================================
# ENVIRONMENT CONFIGURATION - END
# ============================================
async def enhance_content(text: str, context_type: str) -> str:
    """
    Enhance text to be professional, confident, and reference Meks Media Tech (MMT)
    context_type: 'service', 'faq', 'about', 'team'
    """
    logger.info(f"🔧 Attempting to enhance {context_type} content: {text[:50]}...")

    if not text.strip():
        logger.warning("⚠️ Empty text - returning original")
        return text

    try:
        prompt = (
            f"Enhance the following {context_type} content professionally, confidently, "
            f"and reference the company 'Meks Media Tech (MMT)' naturally to improve credibility:\n\n"
            f"{text}\n\nReturn only the enhanced text without explanations."
        )

        client = openai.AsyncOpenAI()
        response = await client.chat.completions.create(
            model="gpt-3.5-turbo",  # <-- use gpt-3.5-turbo instead of gpt-4
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500
        )

        enhanced_text = response.choices[0].message.content.strip()
        logger.info(f"✅ Content enhanced successfully: {enhanced_text[:50]}...")
        return enhanced_text
    except Exception as e:
        logger.error(f"❌ OpenAI enhancement failed: {e}")
        return text
# ============================================
# REDIS HELPER FUNCTIONS - START
# ============================================

async def get_cached_data(key: str):
    """Get cached data from Redis"""
    if redis_client is None:
        logger.debug(f"ℹ️ Cache unavailable for: {key}")
        return None
    try:
        data = redis_client.get(key)
        if data:
            logger.info(f"🎯 Cache hit for: {key}")
            return json.loads(data)
        logger.debug(f"❌ Cache miss for: {key}")
        return None
    except Exception as e:
        logger.error(f"❌ Redis get error for {key}: {e}", exc_info=True)
        return None

async def set_cached_data(key: str, data: any, expire_seconds: int = 300):
    """Set cached data in Redis with expiration"""
    if redis_client is None:
        logger.debug(f"ℹ️ Cache unavailable, skipping: {key}")
        return False
    try:
        redis_client.setex(key, expire_seconds, json.dumps(data))
        logger.info(f"💾 Cached data for: {key} (expires in {expire_seconds}s)")
        return True
    except Exception as e:
        logger.error(f"❌ Redis set error for {key}: {e}", exc_info=True)
        return False

async def invalidate_cache(pattern: str = "*"):
    """Invalidate cache by pattern"""
    if redis_client is None:
        logger.debug(f"ℹ️ Cache unavailable for invalidation: {pattern}")
        return False
    try:
        keys = redis_client.keys(pattern)
        if keys:
            redis_client.delete(*keys)
            logger.info(f"🗑️ Invalidated {len(keys)} cache keys matching: {pattern}")
        else:
            logger.debug(f"ℹ️ No cache keys found matching: {pattern}")
        return True
    except Exception as e:
        logger.error(f"❌ Redis invalidate error for {pattern}: {e}", exc_info=True)
        return False

logger.info("✅ Redis helper functions loaded")

# ============================================
# REDIS HELPER FUNCTIONS - END
# ============================================
def normalize_contact_fields(doc: dict):
    """
    Duplicate contact fields into multiple writing cases
    """
    field_map = {
        "id": ["ID", "Id"],
        "name": ["Name", "NAME", "fullname", "Fullname"],
        "contact": ["Contact", "CONTACT", "emailOrPhone", "EmailOrPhone"],
        "state": ["State", "STATE", "region"],
        "message": ["Message", "MESSAGE", "msg", "Msg"],
        "date": ["Date", "DATE", "createdAt", "CreatedAt"],
        "created_at": ["createdAt", "CreatedAt", "dateCreated", "DateCreated"]
    }

    for base_key, variants in field_map.items():
        if base_key in doc:
            for v in variants:
                doc.setdefault(v, doc.get(base_key))

    return doc


def normalize_gallery_item_fields(doc: dict):
    """
    Duplicate gallery item fields into multiple writing cases
    without removing or altering original fields
    """
    field_map = {
        "id": ["ID", "Id"],
        "title": ["Title", "TITLE", "name", "Name"],
        "description": ["Description", "DESC", "details", "Details"],
        "url": ["URL", "Url", "src", "SRC", "source"],
        "thumbnail": ["thumbnailUrl", "ThumbnailURL", "Thumbnail"],
        "type": ["Type", "mediaType", "MediaType"],
        "category": ["Category", "CATEGORY"],
        "created_at": ["createdAt", "CreatedAt", "dateCreated", "DateCreated"]
    }

    for base_key, variants in field_map.items():
        if base_key in doc:
            for v in variants:
                doc.setdefault(v, doc.get(base_key))

    return doc

def normalize_testimonial_fields(doc: dict):
    """
    Duplicate testimonial fields into multiple writing cases
    without removing or altering original fields
    """
    field_map = {
        "name": ["Name", "NAME", "fullName", "FullName"],
        "state": ["State", "STATE", "location", "Location"],
        "date": ["Date", "testimonialDate", "TestimonialDate"],
        "rating": ["Rating", "RATING", "stars", "Stars"],
        "text": ["Text", "TEXT", "message", "Message", "content", "Content"],
        "media_url": ["mediaUrl", "MediaURL", "MediaUrl"],
        "media_type": ["mediaType", "MediaType"],
        "approved": ["Approved", "isApproved", "IsApproved"],
        "created_at": ["createdAt", "CreatedAt", "dateCreated", "DateCreated"]
    }

    for base_key, variants in field_map.items():
        if base_key in doc:
            for v in variants:
                doc.setdefault(v, doc.get(base_key))

    return doc

def normalize_faq_fields(doc: dict):
    """
    Duplicate FAQ fields into multiple writing cases
    without removing or altering original fields
    """
    field_map = {
        "question": ["Question", "QUESTION", "title", "Title", "faq", "FAQ"],
        "answer": ["Answer", "ANSWER", "response", "Response", "content", "Content"],
        "created_at": ["createdAt", "CreatedAt", "date", "Date"],
        "updated_at": ["updatedAt", "UpdatedAt"]
    }

    for base_key, variants in field_map.items():
        if base_key in doc:
            for v in variants:
                doc.setdefault(v, doc.get(base_key))

    return doc



def normalize_fields(doc: dict):
    """
    Duplicate common fields into multiple writing cases
    without removing or altering original fields
    """
    field_map = {
        "photo": ["Photo", "PHOTO", "image", "Image", "img", "IMG", "avatar", "picture"],
        "video": ["Video", "VIDEO", "video_url", "videoUrl", "media"],
        "title": ["Title", "TITLE", "name", "Name"],
        "text": ["Text", "TEXT", "content", "Content", "message", "Message"],
        "state": ["State", "STATE", "location", "Location"],
        "date": ["Date", "DATE", "createdAt", "created_at"],
        "approved": ["Approved", "APPROVED"],
        "rating": ["Rating", "RATING", "stars", "Stars"]
    }

    for base_key, variants in field_map.items():
        if base_key in doc:
            for v in variants:
                doc.setdefault(v, doc.get(base_key))

    return doc


# ============================================
# EMAIL HELPER FUNCTION - START
# ============================================

async def send_email(to_email: str, subject: str, body: str, raise_on_error: bool = False):
    """Enhanced email sending function with proper error handling and logging"""    
    try:
        if not SMTP_EMAIL or not SMTP_PASSWORD:
            logger.warning(f"⚠️ SMTP credentials not configured - Email simulation mode")
            logger.info(f"📧 [SIMULATION] To: {to_email} | Subject: {subject}")
            logger.debug(f"📧 [SIMULATION] Body preview: {body[:100]}...")
            return False

        msg = MIMEMultipart()
        msg['From'] = SMTP_EMAIL
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        logger.info(f"📧 Attempting to send email to {to_email} - Subject: {subject}")
        
        server = smtplib.SMTP(SMTP_HOST, SMTP_PORT)
        server.starttls()
        server.login(SMTP_EMAIL, SMTP_PASSWORD)
        text = msg.as_string()
        server.sendmail(SMTP_EMAIL, to_email, text)
        server.quit()
        logger.info(f"✅ Email sent successfully to {to_email}")
        return True
        
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"❌ SMTP Authentication failed: {e}", exc_info=True)
        if raise_on_error:
            raise HTTPException(status_code=500, detail="Email authentication failed")
        return False
    except smtplib.SMTPException as e:
        logger.error(f"❌ SMTP error occurred while sending email to {to_email}: {e}", exc_info=True)
        if raise_on_error:
            raise HTTPException(status_code=500, detail=f"Email sending failed: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"❌ Unexpected error sending email to {to_email}: {e}", exc_info=True)
        if raise_on_error:
            raise HTTPException(status_code=500, detail=f"Email error: {str(e)}")
        return False

logger.info("✅ Email helper function loaded")

# ============================================
# EMAIL HELPER FUNCTION - END
# ============================================

# ============================================
# FASTAPI APP INITIALIZATION - START
# ============================================

logger.info("🚀 Initializing FastAPI application...")

app = FastAPI(
    title="Eneji Stats API",
    version="1.0.0",
    description="Comprehensive API for Eneji Stats platform"
)

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
logger.info("✅ CORS middleware configured")

# Security
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
logger.info("✅ Security context initialized")

# Templates Configuration
current_dir = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(os.path.dirname(current_dir), "templates")
logger.info(f"📁 Templates directory: {TEMPLATES_DIR}")
logger.info(f"📁 Templates directory exists: {os.path.exists(TEMPLATES_DIR)}")

# Jinja2 Templates
try:
    templates = Jinja2Templates(directory=TEMPLATES_DIR)
    logger.info("✅ Jinja2 templates initialized successfully")
except Exception as e:
    logger.error(f"❌ Templates initialization failed: {e}", exc_info=True)
    templates = None

# MongoDB Collections
services_collection = None
faqs_collection = None
testimonials_collection = None
gallery_photos_collection = None
gallery_videos_collection = None
contacts_collection = None
quotes_collection = None
about_collection = None
team_collection = None
users_collection = None

if database is not None:
    services_collection = database.services
    faqs_collection = database.faqs
    testimonials_collection = database.testimonials
    gallery_photos_collection = database.gallery_photos
    gallery_videos_collection = database.gallery_videos
    contacts_collection = database.contacts
    quotes_collection = database.quotes
    about_collection = database.about
    team_collection = database.team
    users_collection = database.users
    logger.info("✅ MongoDB collections initialized")
else:
    logger.error("❌ MongoDB collections unavailable - Database not connected")

# Static assets configuration
ASSETS_DIR = os.path.join(TEMPLATES_DIR, "assets")
if os.path.exists(ASSETS_DIR):
    app.mount("/assets", StaticFiles(directory=ASSETS_DIR), name="assets")
    logger.info(f"✅ Static assets mounted from: {ASSETS_DIR}")
else:
    logger.warning(f"⚠️ Assets directory not found: {ASSETS_DIR}")

logger.info("✅ FastAPI application initialized successfully")

# ============================================
# FASTAPI APP INITIALIZATION - END
# ============================================

# ============================================
# PYDANTIC MODELS - START
# ============================================

class ServiceModel(BaseModel):
    id: Optional[str] = None
    title: str
    description: str
    category: str
    icon: Optional[str] = None
    created_at: Optional[datetime] = None

class FAQModel(BaseModel):
    id: Optional[str] = None
    question: str
    answer: str
    created_at: Optional[datetime] = None

class AboutModel(BaseModel):
    mission: str
    history: str

class TeamMemberModel(BaseModel):
    id: Optional[str] = None
    name: str
    position: str
    desc: str
    photo: Optional[str] = None
    created_at: Optional[datetime] = None

class TestimonialModel(BaseModel):
    id: Optional[str] = None
    name: str
    state: str
    date: str
    rating: int = Field(ge=1, le=5)
    text: str
    media_url: Optional[str] = None
    media_type: Optional[str] = None
    photo_url: Optional[str] = None
    location: Optional[str] = None
    approved: bool = False
    created_at: Optional[datetime] = None
    
class GalleryItemModel(BaseModel):
    id: Optional[str] = None
    url: str
    title: str
    created_at: Optional[datetime] = None

class ContactModel(BaseModel):
    id: Optional[str] = None
    name: str
    contact: str  # email OR phone
    state: str
    message: str
    created_at: Optional[datetime] = None


class QuoteModel(BaseModel):
    id: Optional[str] = None
    fullname: str
    contact: str  # email OR phone
    service: str
    extras: List[str] = []
    state: str
    submitted: Optional[str] = None
    created_at: Optional[datetime] = None
  
class LoginModel(BaseModel):
    email: EmailStr
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"

# ============================================
# PYDANTIC MODELS - END
# ============================================

# ============================================
# AUTHENTICATION HELPERS - START
# ============================================

def create_access_token(data: dict):
    """Create JWT access token"""
    logger.info(f"🔐 Creating access token for: {data.get('sub')}")
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    logger.info(f"✅ Access token created successfully")
    return encoded_jwt

async def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token"""
    logger.info(f"🔍 Verifying token")
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            logger.error("❌ Token validation failed: no email in payload")
            raise HTTPException(status_code=401, detail="Invalid token")
        logger.info(f"✅ Token verified for: {email}")
        return email
    except jwt.ExpiredSignatureError:
        logger.error("❌ Token expired")
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError as e:
        logger.error(f"❌ JWT error: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")

# ============================================
# AUTHENTICATION HELPERS - END
# ============================================

# ============================================
# ERROR HANDLERS - START
# ============================================

@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Handle HTTP exceptions with error page"""
    logger.error(f"❌ HTTP {exc.status_code} error on {request.url.path}: {exc.detail}")
    try:
        if templates is not None:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "error_code": exc.status_code},
                status_code=exc.status_code
            )
    except Exception as e:
        logger.error(f"❌ Error rendering error page: {e}", exc_info=True)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """Handle validation errors with error page"""
    logger.error(f"❌ Validation error on {request.url.path}: {exc.errors()}")
    try:
        if templates is not None:
            return templates.TemplateResponse(
                "error.html",
                {"request": request, "error_code": 400},
                status_code=400
            )
    except Exception as e:
        logger.error(f"❌ Error rendering error page: {e}", exc_info=True)
    return JSONResponse(
        status_code=400,
        content={"error": "Validation error"}
    )

@app.middleware("http")
async def catch_exceptions_middleware(request: Request, call_next):
    """Catch all exceptions and serve error page"""
    try:
        response = await call_next(request)
        if response.status_code >= 400 and not request.url.path.startswith("/api"):
            try:
                if templates is not None:
                    return templates.TemplateResponse(
                        "error.html",
                        {"request": request, "error_code": response.status_code},
                        status_code=response.status_code
                    )
            except Exception:
                pass
        return response
    except Exception as e:
        logger.error(f"❌ Unhandled exception on {request.url.path}: {e}", exc_info=True)
        if not request.url.path.startswith("/api"):
            try:
                if templates is not None:
                    return templates.TemplateResponse(
                        "error.html",
                        {"request": request, "error_code": 500},
                        status_code=500
                    )
            except Exception:
                pass
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error"}
        )

logger.info("✅ Error handlers configured")

# ============================================
# ERROR HANDLERS - END
# ============================================

# ============================================
# AUTO-PING BACKGROUND TASK - START
# ============================================

@app.get("/ping")
async def health_check_ping(platform: str = "unknown"):
    """Public health check route for uptime monitors"""
    logger.info(f"🔔 External Health Check: platform={platform}")
    return {"status": "ok", "platform": platform, "timestamp": datetime.utcnow().isoformat()}

async def ping_platforms():
    """Background task to ping platforms"""
    logger.info("🔔 Auto-ping background task started")
    while True:
        try:
            current_time = datetime.utcnow()
            
            for name, config in MONITORING_URLS.items():
                last_ping = config.get("last_ping")
                interval = config["interval"]
                
                if last_ping is None or (current_time - last_ping).total_seconds() >= interval:
                    try:
                        async with httpx.AsyncClient() as client:
                            response = await client.get(config["url"], timeout=10.0)
                            
                            last_log = config.get("last_log")
                            if last_log is None or (current_time - last_log).total_seconds() >= 300:
                                logger.info(f"🔔 Pinged {name}: {response.status_code}")
                                config["last_log"] = current_time
                            
                            config["last_ping"] = current_time
                    except Exception as e:
                        logger.error(f"❌ Error pinging {name}: {e}", exc_info=True)
            
            await asyncio.sleep(60)
        except Exception as e:
            logger.error(f"❌ Error in ping loop: {e}", exc_info=True)
            await asyncio.sleep(60)

@app.on_event("startup")
async def startup_event():
    """Start background tasks on startup"""
    logger.info("🚀 Application startup event triggered")
    if MONITORING_URLS:
        logger.info(f"🔔 Starting auto-ping for {len(MONITORING_URLS)} platforms")
        asyncio.create_task(ping_platforms())
    else:
        logger.info("ℹ️ No monitoring URLs configured - Auto-ping disabled")
    logger.info("✅ Startup tasks completed successfully")

logger.info("✅ Auto-ping background task configured")

# ============================================
# AUTO-PING BACKGROUND TASK - END
# ============================================

# ============================================
# AUTHENTICATION ROUTES - START
# ============================================

@app.post("/api/auth/login", response_model=TokenResponse)
async def login(credentials: LoginModel):
    """Admin login endpoint"""
    logger.info(f"🔐 Login attempt for: {credentials.email}")
    
    # Check credentials (in production, check against database)
    admin_email = os.getenv("ADMIN_EMAIL", "admin@enejistats.com")
    admin_password = os.getenv("ADMIN_PASSWORD", "admin123")
    
    if credentials.email != admin_email or credentials.password != admin_password:
        logger.warning(f"❌ Failed login attempt for: {credentials.email}")
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    access_token = create_access_token(data={"sub": credentials.email})
    logger.info(f"✅ Login successful for: {credentials.email}")
    return {"access_token": access_token, "token_type": "bearer"}

@app.get("/api/auth/verify")
async def verify_auth(email: str = Depends(verify_token)):
    """Verify if token is valid"""
    logger.info(f"✅ Token verification successful for: {email}")
    return {"email": email, "valid": True}

# ============================================
# AUTHENTICATION ROUTES - END
# ============================================
# ============================================
# SERVICES ROUTES - START
# ============================================


@app.get("/api/admin/testimonials")
async def get_admin_testimonials():
    """Get all testimonials for admin (including unapproved)"""
    logger.info("📋 Fetching all testimonials for admin")
    try:
        testimonials = await database.testimonials.find().sort("created_at", -1).to_list(length=None)
        for testimonial in testimonials:
            testimonial["id"] = str(testimonial.pop("_id"))
            normalize_fields(testimonial)
        logger.info(f"✅ Retrieved {len(testimonials)} testimonials")
        return testimonials
    except Exception as e:
        logger.error(f"❌ Error fetching testimonials: {e}")
        return []


@app.get("/api/admin/contacts")
async def get_admin_contacts():
    """Get all contacts for admin"""
    logger.info("📋 Fetching all contacts")
    try:
        contacts = await database.contacts.find().sort("created_at", -1).to_list(length=None)
        for contact in contacts:
            contact["id"] = str(contact.pop("_id"))
            normalize_fields(contact)
        logger.info(f"✅ Retrieved {len(contacts)} contacts")
        return contacts
    except Exception as e:
        logger.error(f"❌ Error fetching contacts: {e}")
        return []


@app.get("/api/admin/quotes")
async def get_admin_quotes():
    """Get all quotes for admin"""
    logger.info("📋 Fetching all quotes")
    try:
        quotes = await database.quotes.find().sort("created_at", -1).to_list(length=None)
        for quote in quotes:
            quote["id"] = str(quote.pop("_id"))
            normalize_fields(quote)
        logger.info(f"✅ Retrieved {len(quotes)} quotes")
        return quotes
    except Exception as e:
        logger.error(f"❌ Error fetching quotes: {e}")
        return []


@app.get("/api/services")
async def get_services():
    """Get all services with Redis caching"""
    logger.info("📋 Fetching all services")

    cache_key = "services:all"
    cached_data = await get_cached_data(cache_key)
    if cached_data:
        return cached_data

    try:
        services = await database.services.find().to_list(length=None)
        for service in services:
            service["id"] = str(service.pop("_id"))
            normalize_fields(service)

        logger.info(f"✅ Retrieved {len(services)} services")

        await set_cached_data(cache_key, services)
        return services
    except Exception as e:
        logger.error(f"❌ Error fetching services: {e}")
        return []





@app.delete("/api/services/{service_id}")
async def delete_service(service_id: str):
    """Delete a service"""
    logger.info(f"🗑️ Deleting service: {service_id}")
    try:
        result = await database.services.delete_one({"_id": ObjectId(service_id)})
        if result.deleted_count:
            await invalidate_cache("services:*")
            return {"message": "Service deleted successfully"}

        raise HTTPException(status_code=404, detail="Service not found")
    except Exception as e:
        logger.error(f"❌ Error deleting service: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# SERVICES ROUTES - END
# ============================================
# ============================================
# FAQS ROUTES - START
# ============================================

@app.get("/api/faqs")
async def get_faqs():
    """Get all FAQs with Redis caching"""
    logger.info("📋 Fetching all FAQs")
    
    # Check cache first
    cache_key = "faqs:all"
    cached_data = await get_cached_data(cache_key)
    if cached_data:
        return cached_data
    
    try:
        faqs = await database.faqs.find().to_list(length=None)
        for faq in faqs:
            faq["id"] = str(faq.pop("_id"))
            normalize_faq_fields(faq)

        logger.info(f"✅ Retrieved {len(faqs)} FAQs")
        
        # Cache the results
        await set_cached_data(cache_key, faqs)
        
        return faqs
    except Exception as e:
        logger.error(f"❌ Error fetching FAQs: {e}")
        return []



@app.delete("/api/faqs/{faq_id}")
async def delete_faq(faq_id: str):
    """Delete a FAQ"""
    logger.info(f"🗑️ Deleting FAQ: {faq_id}")
    try:
        result = await database.faqs.delete_one({"_id": ObjectId(faq_id)})
        if result.deleted_count:
            logger.info(f"✅ FAQ deleted: {faq_id}")
            
            # Invalidate cache
            await invalidate_cache("faqs:*")
            
            return {"message": "FAQ deleted successfully"}
        raise HTTPException(status_code=404, detail="FAQ not found")
    except Exception as e:
        logger.error(f"❌ Error deleting FAQ: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# FAQS ROUTES - END
# ============================================
# ============================================
# TESTIMONIALS ROUTES - START
# ============================================

@app.get("/api/testimonials")
async def get_testimonials():
    """Get all testimonials with Redis caching"""
    logger.info("📋 Fetching all testimonials")
    
    # Check cache first
    cache_key = "testimonials:all"
    cached_data = await get_cached_data(cache_key)
    if cached_data:
        return cached_data
    
    try:
        testimonials = await database.testimonials.find().to_list(length=None)
        for testimonial in testimonials:
            testimonial["id"] = str(testimonial.pop("_id"))
            
        logger.info(f"✅ Retrieved {len(testimonials)} testimonials")
        
        # Cache the results
        await set_cached_data(cache_key, testimonials)
        
        return testimonials
    except Exception as e:
        logger.error(f"❌ Error fetching testimonials: {e}")
        return []


MAX_IMAGE_SIZE = 1 * 1024 * 1024      # 1MB
MAX_VIDEO_SIZE = 50 * 1024 * 1024     # 50MB


@app.post("/api/testimonials")
async def create_testimonial(
    name: str = Form(...),
    state: str = Form(...),
    date: str = Form(...),
    rating: int = Form(...),
    text: str = Form(...),
    file: UploadFile | None = File(None)
):
    logger.info(f"➕ Creating testimonial from: {name}")

    media_url = None
    media_type = None

    try:
        if file:
            contents = await file.read()
            size = len(contents)

            if file.content_type.startswith("image/") and size > MAX_IMAGE_SIZE:
                raise HTTPException(status_code=400, detail="Image must not exceed 1MB")

            if file.content_type.startswith("video/") and size > MAX_VIDEO_SIZE:
                raise HTTPException(status_code=400, detail="Video must not exceed 50MB")

            upload_result = cloudinary.uploader.upload(
                contents,
                resource_type="video" if file.content_type.startswith("video/") else "image"
            )

            media_url = upload_result.get("secure_url")
            media_type = "video" if file.content_type.startswith("video/") else "image"

        testimonial = {
            "name": name,
            "state": state,
            "date": date,
            "rating": rating,
            "text": text,
            "media_url": media_url,
            "media_type": media_type,
            "approved": False,
            "created_at": datetime.utcnow()
        }

        result = await database.testimonials.insert_one(testimonial)
        await invalidate_cache("testimonials:*")

        return {
            "id": str(result.inserted_id),
            "message": "Testimonial created successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error creating testimonial: {e}")
        raise HTTPException(status_code=500, detail="Failed to submit testimonial")


@app.patch("/api/testimonials/{testimonial_id}/approve")
async def approve_testimonial(testimonial_id: str):
    """Approve a testimonial"""
    logger.info(f"✅ Approving testimonial: {testimonial_id}")
    try:
        result = await database.testimonials.update_one(
            {"_id": ObjectId(testimonial_id)},
            {"$set": {"approved": True}}
        )
        if result.modified_count:
            logger.info(f"✅ Testimonial approved: {testimonial_id}")
            
            # Invalidate cache
            await invalidate_cache("testimonials:*")
            
            return {"message": "Testimonial approved successfully"}
        raise HTTPException(status_code=404, detail="Testimonial not found")
    except Exception as e:
        logger.error(f"❌ Error approving testimonial: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/testimonials/{testimonial_id}")
async def delete_testimonial(testimonial_id: str):
    """Delete a testimonial"""
    logger.info(f"🗑️ Deleting testimonial: {testimonial_id}")
    try:
        result = await database.testimonials.delete_one({"_id": ObjectId(testimonial_id)})
        if result.deleted_count:
            logger.info(f"✅ Testimonial deleted: {testimonial_id}")
            
            # Invalidate cache
            await invalidate_cache("testimonials:*")
            
            return {"message": "Testimonial deleted successfully"}
        raise HTTPException(status_code=404, detail="Testimonial not found")
    except Exception as e:
        logger.error(f"❌ Error deleting testimonial: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# TESTIMONIALS ROUTES - END
# ============================================
# ============================================
# GALLERY ROUTES - START
# ============================================

@app.get("/api/gallery")
async def get_gallery():
    """Get all gallery items with Redis caching"""
    logger.info("📋 Fetching gallery")
    
    # Check cache first
    cache_key = "gallery:all"
    cached_data = await get_cached_data(cache_key)
    if cached_data:
        return cached_data
    
    try:
        photos = await database.gallery_photos.find().to_list(length=None)
        videos = await database.gallery_videos.find().to_list(length=None)
        
        for photo in photos:
            photo["id"] = str(photo.pop("_id"))
            normalize_gallery_item_fields(photo)

        for video in videos:
            video["id"] = str(video.pop("_id"))
            normalize_gallery_item_fields(video)
        
        logger.info(f"✅ Retrieved {len(photos)} photos and {len(videos)} videos")
        
        result = {"photos": photos, "videos": videos}
        
        # Cache the results
        await set_cached_data(cache_key, result)
        
        return result
    except Exception as e:
        logger.error(f"❌ Error fetching gallery: {e}")
        return {"photos": [], "videos": []}


@app.get("/api/gallery/photos")
async def get_photos():
    """Get all photos"""
    logger.info("📋 Fetching gallery photos")
    try:
        photos = await database.gallery_photos.find().to_list(length=None)
        for photo in photos:
            photo["id"] = str(photo.pop("_id"))
            normalize_gallery_item_fields(photo)
        logger.info(f"✅ Retrieved {len(photos)} photos")
        return {"photos": photos}
    except Exception as e:
        logger.error(f"❌ Error fetching photos: {e}")
        return {"photos": []}


@app.get("/api/gallery/videos")
async def get_videos():
    """Get all videos"""
    logger.info("📋 Fetching gallery videos")
    try:
        videos = await database.gallery_videos.find().to_list(length=None)
        for video in videos:
            video["id"] = str(video.pop("_id"))
            normalize_gallery_item_fields(video)
        logger.info(f"✅ Retrieved {len(videos)} videos")
        return {"videos": videos}
    except Exception as e:
        logger.error(f"❌ Error fetching videos: {e}")
        return {"videos": []}


@app.post("/api/gallery/photos")
async def create_photo(item: GalleryItemModel):
    """Add a photo to gallery"""
    logger.info(f"➕ Adding photo: {item.title}")
    try:
        item_dict = item.model_dump(exclude={"id"})
        item_dict["created_at"] = datetime.utcnow()
        result = await database.gallery_photos.insert_one(item_dict)
        logger.info(f"✅ Photo added with ID: {result.inserted_id}")
        
        # Invalidate cache
        await invalidate_cache("gallery:*")
        
        return {"id": str(result.inserted_id), "message": "Photo added successfully"}
    except Exception as e:
        logger.error(f"❌ Error adding photo: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gallery/videos")
async def create_video(item: GalleryItemModel):
    """Add a video to gallery"""
    logger.info(f"➕ Adding video: {item.title}")
    try:
        item_dict = item.model_dump(exclude={"id"})
        item_dict["created_at"] = datetime.utcnow()
        result = await database.gallery_videos.insert_one(item_dict)
        logger.info(f"✅ Video added with ID: {result.inserted_id}")
        
        # Invalidate cache
        await invalidate_cache("gallery:*")
        
        return {"id": str(result.inserted_id), "message": "Video added successfully"}
    except Exception as e:
        logger.error(f"❌ Error adding video: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/gallery/photos/{item_id}")
async def update_photo(item_id: str, item: GalleryItemModel):
    """Update a photo"""
    logger.info(f"✏️ Updating photo: {item_id}")
    try:
        item_dict = item.model_dump(exclude={"id", "created_at"})
        result = await database.gallery_photos.update_one(
            {"_id": ObjectId(item_id)},
            {"$set": item_dict}
        )
        if result.modified_count:
            logger.info(f"✅ Photo updated: {item_id}")
            await invalidate_cache("gallery:*")
            return {"message": "Photo updated successfully"}
        raise HTTPException(status_code=404, detail="Photo not found")
    except Exception as e:
        logger.error(f"❌ Error updating photo: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/api/gallery/videos/{item_id}")
async def update_video(item_id: str, item: GalleryItemModel):
    """Update a video"""
    logger.info(f"✏️ Updating video: {item_id}")
    try:
        item_dict = item.model_dump(exclude={"id", "created_at"})
        result = await database.gallery_videos.update_one(
            {"_id": ObjectId(item_id)},
            {"$set": item_dict}
        )
        if result.modified_count:
            logger.info(f"✅ Video updated: {item_id}")
            await invalidate_cache("gallery:*")
            return {"message": "Video updated successfully"}
        raise HTTPException(status_code=404, detail="Video not found")
    except Exception as e:
        logger.error(f"❌ Error updating video: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/gallery/photos/{item_id}")
async def delete_photo(item_id: str):
    """Delete a photo"""
    logger.info(f"🗑️ Deleting photo: {item_id}")
    try:
        result = await database.gallery_photos.delete_one({"_id": ObjectId(item_id)})
        if result.deleted_count:
            logger.info(f"✅ Photo deleted: {item_id}")
            await invalidate_cache("gallery:*")
            return {"message": "Photo deleted successfully"}
        raise HTTPException(status_code=404, detail="Photo not found")
    except Exception as e:
        logger.error(f"❌ Error deleting photo: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/gallery/videos/{item_id}")
async def delete_video(item_id: str):
    """Delete a video"""
    logger.info(f"🗑️ Deleting video: {item_id}")
    try:
        result = await database.gallery_videos.delete_one({"_id": ObjectId(item_id)})
        if result.deleted_count:
            logger.info(f"✅ Video deleted: {item_id}")
            await invalidate_cache("gallery:*")
            return {"message": "Video deleted successfully"}
        raise HTTPException(status_code=404, detail="Video not found")
    except Exception as e:
        logger.error(f"❌ Error deleting video: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# GALLERY ROUTES - END
# ============================================
# ============================================
# CONTACT ROUTES - START
# ============================================



@app.get("/api/contacts")
async def get_contacts():
    """Get all contacts"""
    logger.info("📋 Fetching all contacts")
    try:
        contacts = await database.contacts.find().to_list(length=None)
        for contact in contacts:
            contact["id"] = str(contact.pop("_id"))
            normalize_contact_fields(contact)
        logger.info(f"✅ Retrieved {len(contacts)} contacts")
        return contacts
    except Exception as e:
        logger.error(f"❌ Error fetching contacts: {e}")
        return []

# Existing /apiworks/contact and /api/contact POST routes remain as-is
# with DB insertion preserved (no changes needed here)

@app.delete("/api/contacts/{contact_id}")
async def delete_contact(contact_id: str):
    """Delete a contact"""
    logger.info(f"🗑️ Deleting contact: {contact_id}")
    try:
        result = await database.contacts.delete_one({"_id": ObjectId(contact_id)})
        if result.deleted_count:
            logger.info(f"✅ Contact deleted: {contact_id}")
            return {"message": "Contact deleted successfully"}
        raise HTTPException(status_code=404, detail="Contact not found")
    except Exception as e:
        logger.error(f"❌ Error deleting contact: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# CONTACT ROUTES - END
# ============================================

# ============================================
# QUOTE ROUTES - START
# ============================================

def normalize_quote_fields(doc: dict):
    """Duplicate quote fields into multiple writing cases"""
    field_map = {
        "id": ["ID", "Id"],
        "fullname": ["Fullname", "fullName", "FULLNAME", "Name"],
        "contact": ["Contact", "CONTACT", "emailOrPhone", "EmailOrPhone"],
        "service": ["Service", "SERVICE"],
        "state": ["State", "STATE", "region"],
        "date": ["Date", "DATE", "createdAt", "CreatedAt"],
        "details": ["Details", "details", "DETAILS", "description", "Description"],
        "extras": ["Extras", "EXTRAS"],
        "submitted": ["Submitted", "submitted", "submittedAt", "SubmittedAt"],
        "created_at": ["createdAt", "CreatedAt", "dateCreated", "DateCreated"]
    }
    for base_key, variants in field_map.items():
        if base_key in doc:
            for v in variants:
                doc.setdefault(v, doc.get(base_key))
    return doc

@app.get("/api/quotes")
async def get_quotes():
    """Get all quotes"""
    logger.info("📋 Fetching all quotes")
    try:
        quotes = await database.quotes.find().to_list(length=None)
        for quote in quotes:
            quote["id"] = str(quote.pop("_id"))
            normalize_quote_fields(quote)
        logger.info(f"✅ Retrieved {len(quotes)} quotes")
        return quotes
    except Exception as e:
        logger.error(f"❌ Error fetching quotes: {e}")
        return []

# Existing /apiworks/quote and /api/quote POST routes remain intact

@app.delete("/api/quotes/{quote_id}")
async def delete_quote(quote_id: str):
    """Delete a quote"""
    logger.info(f"🗑️ Deleting quote: {quote_id}")
    try:
        result = await database.quotes.delete_one({"_id": ObjectId(quote_id)})
        if result.deleted_count:
            logger.info(f"✅ Quote deleted: {quote_id}")
            return {"message": "Quote deleted successfully"}
        raise HTTPException(status_code=404, detail="Quote not found")
    except Exception as e:
        logger.error(f"❌ Error deleting quote: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# QUOTE ROUTES - END
# ============================================

# ============================================
# ABOUT ROUTES - START
# ============================================

def normalize_team_member_fields(doc: dict):
    """Duplicate team member fields into multiple writing cases"""
    field_map = {
        "id": ["ID", "Id"],
        "name": ["Name", "NAME"],
        "role": ["Role", "ROLE", "position"],
        "bio": ["Bio", "BIO", "biography", "Biography"],
        "photo": ["Photo", "PHOTO", "photoUrl", "PhotoUrl"],
        "created_at": ["createdAt", "CreatedAt", "dateCreated", "DateCreated"]
    }
    for base_key, variants in field_map.items():
        if base_key in doc:
            for v in variants:
                doc.setdefault(v, doc.get(base_key))
    return doc

@app.get("/api/about")
async def get_about():
    """Get about information with Redis caching"""
    logger.info("📋 Fetching about information")
    
    cache_key = "about:info"
    cached_data = await get_cached_data(cache_key)
    if cached_data:
        return cached_data
    
    try:
        about = await database.about.find_one()
        team = await database.team.find().to_list(length=None)
        
        for member in team:
            member["id"] = str(member.pop("_id"))
            normalize_team_member_fields(member)
        
        if about:
            about.pop("_id", None)
            about["team"] = team
        else:
            about = {"mission": "", "history": "", "team": team}
        
        logger.info(f"✅ Retrieved about info with {len(team)} team members")
        
        await set_cached_data(cache_key, about)
        return about
    except Exception as e:
        logger.error(f"❌ Error fetching about: {e}")
        return {"mission": "", "history": "", "team": []}

# =========================
# Services Routes
# =========================
@app.post("/api/services")
async def create_service(service: ServiceModel):
    """Create a new service"""
    logger.info(f"➕ Creating service: {service.title}")
    try:
        service_dict = service.model_dump(exclude={"id"})
        # Enhance description
        service_dict["description"] = await enhance_content(service_dict.get("description", ""), "service")
        service_dict["created_at"] = datetime.utcnow()
        result = await database.services.insert_one(service_dict)
        await invalidate_cache("services:*")
        return {"id": str(result.inserted_id), "message": "Service created successfully"}
    except Exception as e:
        logger.error(f"❌ Error creating service: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/services/{service_id}")
async def update_service(service_id: str, service: ServiceModel):
    """Update a service"""
    logger.info(f"✏️ Updating service: {service_id}")
    try:
        service_dict = service.model_dump(exclude={"id", "created_at"})
        service_dict["description"] = await enhance_content(service_dict.get("description", ""), "service")
        result = await database.services.update_one(
            {"_id": ObjectId(service_id)},
            {"$set": service_dict}
        )
        if result.modified_count:
            await invalidate_cache("services:*")
            return {"message": "Service updated successfully"}
        raise HTTPException(status_code=404, detail="Service not found")
    except Exception as e:
        logger.error(f"❌ Error updating service: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
# =========================
# FAQs Routes
# =========================
@app.post("/api/faqs")
async def create_faq(faq: FAQModel):
    """Create a new FAQ"""
    logger.info(f"➕ Creating FAQ: {faq.question[:50]}...")
    try:
        faq_dict = faq.model_dump(exclude={"id"})
        faq_dict["question"] = await enhance_content(faq_dict.get("question", ""), "faq")
        faq_dict["answer"] = await enhance_content(faq_dict.get("answer", ""), "faq")
        faq_dict["created_at"] = datetime.utcnow()
        result = await database.faqs.insert_one(faq_dict)
        logger.info(f"✅ FAQ created with ID: {result.inserted_id}")
        await invalidate_cache("faqs:*")
        return {"id": str(result.inserted_id), "message": "FAQ created successfully"}
    except Exception as e:
        logger.error(f"❌ Error creating FAQ: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/faqs/{faq_id}")
async def update_faq(faq_id: str, faq: FAQModel):
    """Update a FAQ"""
    logger.info(f"✏️ Updating FAQ: {faq_id}")
    try:
        faq_dict = faq.model_dump(exclude={"id", "created_at"})
        faq_dict["question"] = await enhance_content(faq_dict.get("question", ""), "faq")
        faq_dict["answer"] = await enhance_content(faq_dict.get("answer", ""), "faq")
        result = await database.faqs.update_one(
            {"_id": ObjectId(faq_id)},
            {"$set": faq_dict}
        )
        if result.modified_count:
            logger.info(f"✅ FAQ updated: {faq_id}")
            await invalidate_cache("faqs:*")
            return {"message": "FAQ updated successfully"}
        raise HTTPException(status_code=404, detail="FAQ not found")
    except Exception as e:
        logger.error(f"❌ Error updating FAQ: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
# =========================
# About Route
# =========================
@app.put("/api/about")
async def update_about(about: AboutModel):
    """Update about information"""
    logger.info("✏️ Updating about information")
    try:
        about_dict = about.model_dump()
        # Enhance mission and history
        about_dict["mission"] = await enhance_content(about_dict.get("mission", ""), "about")
        about_dict["history"] = await enhance_content(about_dict.get("history", ""), "about")
        await invalidate_cache("about:*")
        result = await database.about.update_one(
            {},
            {"$set": about_dict},
            upsert=True
        )
        logger.info("✅ About information updated and enhanced")
        return {"message": "About information updated and enhanced successfully"}
    except Exception as e:
        logger.error(f"❌ Error updating about: {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
# =========================
# Team Member Routes
# =========================
@app.post("/api/team")
async def create_team_member(member: TeamMemberModel):
    """Add a team member"""
    logger.info(f"➕ Adding team member: {member.name}")
    try:
        member_dict = member.model_dump(exclude={"id"})
        # Enhance desc
        if "desc" in member_dict:
            member_dict["desc"] = await enhance_content(member_dict["desc"], "team")
        member_dict["created_at"] = datetime.utcnow()
        result = await database.team.insert_one(member_dict)
        logger.info(f"✅ Team member added with ID: {result.inserted_id}")
        return {"id": str(result.inserted_id), "message": "Team member added successfully"}
    except Exception as e:
        logger.error(f"❌ Error adding team member: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/team/{member_id}")
async def update_team_member(member_id: str, member: TeamMemberModel):
    """Update a team member"""
    logger.info(f"✏️ Updating team member: {member_id}")
    try:
        member_dict = member.model_dump(exclude={"id", "created_at"})
        if "desc" in member_dict:
            member_dict["desc"] = await enhance_content(member_dict["desc"], "team")
        result = await database.team.update_one(
            {"_id": ObjectId(member_id)},
            {"$set": member_dict}
        )
        if result.modified_count:
            logger.info(f"✅ Team member updated: {member_id}")
            return {"message": "Team member updated successfully"}
        raise HTTPException(status_code=404, detail="Team member not found")
    except Exception as e:
        logger.error(f"❌ Error updating team member: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================
# ABOUT ROUTES - END
# ============================================

# ============================================
# FILE UPLOAD ROUTE - START
# ============================================

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload a file to Cloudinary"""
    logger.info(f"📤 Uploading file: {file.filename}")
    try:
        if not cloudinary_url:
            raise HTTPException(status_code=500, detail="Cloudinary not configured")
        
        contents = await file.read()
        
        upload_result = cloudinary.uploader.upload(
            contents,
            folder="enejistats",
            resource_type="auto"
        )
        
        logger.info(f"✅ File uploaded successfully: {upload_result['secure_url']}")
        # duplicate keys for frontend casing support
        result = {
            "url": upload_result["secure_url"],
            "URL": upload_result["secure_url"],
            "public_id": upload_result["public_id"],
            "publicID": upload_result["public_id"]
        }
        return result
        
    except Exception as e:
        logger.error(f"❌ Error uploading file: {e}")
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)}")

# ============================================
# FILE UPLOAD ROUTE - END
# ============================================

# ============================================
# STATIC HTML PAGES ROUTES - START
# ============================================

@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve index.html (home page)"""
    logger.info("📄 GET / - Serving index.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "index.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ index.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ index.html not found at {file_path}", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving index.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/about", response_class=HTMLResponse)
async def serve_about():
    """Serve about.html page"""
    logger.info("📄 GET /about - Serving about.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "about.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ about.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ about.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving about.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/services", response_class=HTMLResponse)
async def serve_services():
    """Serve services.html page"""
    logger.info("📄 GET /services - Serving services.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "services.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ services.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ services.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving services.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/gallery", response_class=HTMLResponse)
async def serve_gallery():
    """Serve gallery.html page"""
    logger.info("📄 GET /gallery - Serving gallery.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "gallery.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ gallery.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ gallery.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving gallery.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/testimonials", response_class=HTMLResponse)
async def serve_testimonials():
    """Serve testimonials.html page"""
    logger.info("📄 GET /testimonials - Serving testimonials.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "testimonials.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ testimonials.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ testimonials.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving testimonials.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/submit-testimonials", response_class=HTMLResponse)
async def serve_submit_testimonials():
    """Serve submit-testimonials.html page"""
    logger.info("📄 GET /submit-testimonials - Serving submit-testimonials.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "submit-testimonials.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ submit-testimonials.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ submit-testimonials.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving submit-testimonials.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/contact", response_class=HTMLResponse)
async def serve_contact():
    """Serve contact.html page"""
    logger.info("📄 GET /contact - Serving contact.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "contact.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ contact.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ contact.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving contact.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/login", response_class=HTMLResponse)
async def serve_login():
    """Serve login.html page"""
    logger.info("📄 GET /login - Serving login.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "login.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ login.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ login.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving login.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve dashboard.html page"""
    logger.info("📄 GET /dashboard - Serving dashboard.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "dashboard.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info("✅ dashboard.html served successfully")
        return HTMLResponse(content=content)
    except FileNotFoundError:
        logger.error(f"❌ dashboard.html not found", exc_info=True)
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving dashboard.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/404", response_class=HTMLResponse)
async def serve_404():
    """Serve 404.html page"""
    logger.info("📄 Serving 404.html")
    try:
        file_path = os.path.join(TEMPLATES_DIR, "404.html")
        if not os.path.exists(file_path):
            logger.error(f"❌ File not found: {file_path}")
            raise HTTPException(status_code=404, detail="Page not found")
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return HTMLResponse(content=content, status_code=404)
    except FileNotFoundError:
        logger.error(f"❌ 404.html not found")
        raise HTTPException(status_code=404, detail="Page not found")
    except Exception as e:
        logger.error(f"❌ Error serving 404.html: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

logger.info("✅ Static HTML page routes configured")

# ============================================
# STATIC HTML PAGES ROUTES - END
# ============================================

# ============================================
# HEALTH CHECK - START
# ============================================

@app.get("/api/health")
async def api_health_check():
    """Health check endpoint"""
    logger.info("🏥 GET /api/health - Health check requested")
    response = {
        "status": "healthy",
        "service": "Eneji Stats API",
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat()
    }
    logger.info("✅ Health check completed successfully")
    return response

@app.get("/api/status")
async def detailed_health_check():
    """Detailed health check"""
    logger.info("🏥 GET /api/status - Detailed health check requested")
    
    db_status = "connected" if client is not None else "disconnected"
    email_status = "configured" if SMTP_EMAIL and SMTP_PASSWORD else "not configured"
    cloudinary_status = "configured" if cloudinary_url else "not configured"
    redis_status = "connected" if redis_client is not None else "disconnected"
    
    response = {
        "status": "healthy",
        "database": db_status,
        "email": email_status,
        "cloudinary": cloudinary_status,
        "redis": redis_status,
        "timestamp": datetime.utcnow().isoformat()
    }
    
    logger.info(f"✅ Status check: DB={db_status}, Email={email_status}, Cloudinary={cloudinary_status}, Redis={redis_status}")
    return response

logger.info("✅ Health check routes configured")

# ============================================
# HEALTH CHECK - END
# ============================================

if __name__ == "__main__":
    import uvicorn
    logger.info("🚀 Starting Eneji Stats API Server")
    uvicorn.run(app, host="0.0.0.0", port=5000, log_level="info")
