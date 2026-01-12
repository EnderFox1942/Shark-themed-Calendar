"""
Shark-Themed Web Calendar System with Authentication and PFP Cropping
Requires: aiohttp, supabase, aiohttp-session, cryptography, aiohttp-jinja2
Install: pip install aiohttp supabase aiohttp-jinja2 jinja2 aiohttp-session cryptography
"""

import os
import json
import base64
import hashlib
import logging
from datetime import datetime, timedelta, date
from typing import Optional, Dict, List
from aiohttp import web
import aiohttp_jinja2
import jinja2
from aiohttp_session import setup, get_session, new_session, SimpleCookieStorage
from cryptography import fernet
from supabase import create_client, Client

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('SharkCalendar')


def load_env_file(filepath='.env'):
    """Load environment variables from a .env file"""
    if not os.path.exists(filepath):
        logger.info(f"⚠️  Warning: {filepath} not found")
        return
    
    logger.info(f"📄 Loading environment from {filepath}")
    with open(filepath, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#'):
                if '=' in line:
                    key, value = line.split('=', 1)
                    value = value.strip().strip('"').strip("'")
                    os.environ[key.strip()] = value


def load_environment() -> Dict[str, str]:
    """Load environment variables from system environment or .env file"""
    logger.info("🔧 Loading environment variables...")
    
    load_env_file('.env')
    
    required_vars = ['SUPABASE_URL', 'SUPABASE_KEY', 'USER', 'PASS']
    env_vars = {}
    
    for var in required_vars:
        value = os.environ.get(var)
        if not value:
            logger.error(f"❌ Missing required environment variable: {var}")
            raise ValueError(f"Missing required environment variable: {var}")
        env_vars[var] = value
        if var in ['PASS', 'SECRET_KEY', 'SUPABASE_KEY']:
            logger.info(f"✅ {var} loaded (hidden for security)")
        else:
            logger.info(f"✅ {var} = {value}")
    
    pooler_url = os.environ.get('SUPABASE_POOLER_URL')
    if pooler_url:
        env_vars['SUPABASE_POOLER_URL'] = pooler_url
        logger.info(f"✅ SUPABASE_POOLER_URL configured (using connection pooler)")
    else:
        logger.info("ℹ️  SUPABASE_POOLER_URL not set (using regular URL)")
    
    env_vars['APP_PORT'] = os.environ.get('PORT', os.environ.get('APP_PORT', '8080'))
    env_vars['APP_HOST'] = os.environ.get('APP_HOST', '0.0.0.0')
    
    logger.info(f"✅ APP_HOST = {env_vars['APP_HOST']}")
    logger.info(f"✅ APP_PORT = {env_vars['APP_PORT']}")
    logger.info("✅ All environment variables loaded successfully")
    
    return env_vars


class User:
    """User class for authentication"""
    
    def __init__(self, username: str, password: str):
        logger.info(f"👤 Initializing user: {username}")
        self.username = username
        self.password_hash = self._hash_password(password)
        self.profile_picture = None
        self.display_name = username
        logger.info(f"✅ User {username} initialized successfully")
    
    @staticmethod
    def _hash_password(password: str) -> str:
        return hashlib.sha256(password.encode()).hexdigest()
    
    def verify_password(self, password: str) -> bool:
        is_valid = self._hash_password(password) == self.password_hash
        logger.info(f"🔐 Password verification for {self.username}: {'✅ Success' if is_valid else '❌ Failed'}")
        return is_valid
    
    def set_profile_picture(self, picture_data: str):
        logger.info(f"🖼️  Setting profile picture for {self.username}")
        self.profile_picture = picture_data


class SharkCalendarDB:
    """Database handler for shark calendar using Supabase"""
    
    def __init__(self, supabase_url: str, supabase_key: str, pooler_url: Optional[str] = None):
        connection_url = pooler_url if pooler_url else supabase_url
        
        logger.info(f"🔗 Connecting to Supabase...")
        if pooler_url:
            logger.info(f"   Using connection pooler for better performance")
        else:
            logger.info(f"   Using standard REST API connection")
        
        self.client: Client = create_client(connection_url, supabase_key)
        self.events_table = "shark_events"
        self.users_table = "shark_users"
        
        logger.info(f"✅ Database client initialized")
    
    async def initialize_tables(self):
        """Create tables if they don't exist using Supabase REST API"""
        try:
            tables_exist = True
            try:
                self.client.table(self.events_table).select("id").limit(1).execute()
                logger.info(f"✅ Table '{self.events_table}' exists")
            except Exception:
                tables_exist = False
                logger.warning(f"⚠️  Table '{self.events_table}' does not exist")
            
            try:
                self.client.table(self.users_table).select("username").limit(1).execute()
                logger.info(f"✅ Table '{self.users_table}' exists")
            except Exception:
                tables_exist = False
                logger.warning(f"⚠️  Table '{self.users_table}' does not exist")
            
            if not tables_exist:
                create_sql = """
-- Create events table
CREATE TABLE IF NOT EXISTS shark_events (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    event_date DATE NOT NULL,
    event_time TIME,
    tags TEXT DEFAULT '[]',
    username TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

-- Create users table
CREATE TABLE IF NOT EXISTS shark_users (
    username TEXT PRIMARY KEY,
    profile_picture TEXT,
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Enable Row Level Security
ALTER TABLE shark_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE shark_users ENABLE ROW LEVEL SECURITY;

-- Drop existing policies if they exist
DROP POLICY IF EXISTS "Enable all operations for authenticated users" ON shark_events;
DROP POLICY IF EXISTS "Enable all operations for authenticated users" ON shark_users;

-- Create policies
CREATE POLICY "Enable all operations for authenticated users" 
ON shark_events FOR ALL 
USING (true);

CREATE POLICY "Enable all operations for authenticated users" 
ON shark_users FOR ALL 
USING (true);
                """
                logger.error("❌ Tables do not exist. Please create them in Supabase SQL Editor:")
                logger.error("\n" + "="*70)
                logger.error(create_sql)
                logger.error("="*70 + "\n")
                logger.info("📝 Copy the SQL above and run it in your Supabase SQL Editor")
                logger.info("🔗 Go to: https://supabase.com/dashboard/project/_/sql")
            else:
                logger.info("✅ All database tables verified and ready")
                
        except Exception as e:
            logger.warning(f"⚠️  Database check: {e}")
    
    async def create_event(self, title: str, description: str, 
                          event_date: str, event_time: Optional[str],
                          tags, username: str) -> Dict:
        """Create a new calendar event
        
        Note: event_time is kept in the DB for compatibility but can be None/empty.
        tags may be a list or comma-separated string; we store it as JSON text.
        """
        logger.info(f"📝 Creating event: '{title}' for user '{username}'")
        
        # Normalize tags to JSON string
        try:
            if isinstance(tags, list):
                tags_json = json.dumps(tags)
            elif isinstance(tags, str):
                # If it's already JSON, keep; if comma-separated, convert
                try:
                    json.loads(tags)
                    tags_json = tags
                except Exception:
                    tags_list = [t.strip() for t in tags.split(',') if t.strip()]
                    tags_json = json.dumps(tags_list)
            else:
                tags_json = json.dumps([])
        except Exception:
            tags_json = json.dumps([])
        
        data = {
            "title": title,
            "description": description,
            "event_date": event_date,
            "event_time": event_time or None,
            "tags": tags_json,
            "username": username,
            "created_at": datetime.now().isoformat()
        }
        
        try:
            result = self.client.table(self.events_table).insert(data).execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Event created successfully")
                created = result.data[0]
                try:
                    created['tags'] = json.loads(created.get('tags', '[]'))
                except Exception:
                    created['tags'] = []
                return created
            return {}
        except Exception as e:
            logger.error(f"❌ Error creating event: {e}")
            return {}
    
    async def get_events(self, username: str, start_date: Optional[str] = None, 
                        end_date: Optional[str] = None) -> List[Dict]:
        """Get events for a specific user"""
        logger.info(f"📅 Fetching events for user: {username}")
        
        try:
            query = self.client.table(self.events_table).select("*").eq("username", username)
            
            if start_date:
                query = query.gte("event_date", start_date)
            if end_date:
                query = query.lte("event_date", end_date)
            
            result = query.order("event_date", desc=False).execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Retrieved {len(result.data)} events")
                events = result.data
                # Normalize tags field to lists
                for ev in events:
                    try:
                        ev['tags'] = json.loads(ev.get('tags', '[]')) if ev.get('tags') else []
                    except Exception:
                        ev['tags'] = []
                return events
            return []
        except Exception as e:
            logger.error(f"❌ Error getting events: {e}")
            return []
    
    async def update_event(self, event_id: int, username: str, updates: Dict) -> Dict:
        """Update an existing event"""
        logger.info(f"✏️  Updating event ID {event_id}")
        
        # Ensure tags normalized if present
        if 'tags' in updates:
            try:
                if isinstance(updates['tags'], list):
                    updates['tags'] = json.dumps(updates['tags'])
                elif isinstance(updates['tags'], str):
                    try:
                        json.loads(updates['tags'])
                    except Exception:
                        updates['tags'] = json.dumps([t.strip() for t in updates['tags'].split(',') if t.strip()])
            except Exception:
                updates['tags'] = json.dumps([])
        
        try:
            result = self.client.table(self.events_table)\
                .update(updates)\
                .eq("id", event_id)\
                .eq("username", username)\
                .execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Event updated")
                updated = result.data[0]
                try:
                    updated['tags'] = json.loads(updated.get('tags', '[]'))
                except Exception:
                    updated['tags'] = []
                return updated
            return {}
        except Exception as e:
            logger.error(f"❌ Error updating event: {e}")
            return {}
    
    async def delete_event(self, event_id: int, username: str) -> bool:
        """Delete an event"""
        logger.info(f"🗑️  Deleting event ID {event_id}")
        
        try:
            result = self.client.table(self.events_table)\
                .delete()\
                .eq("id", event_id)\
                .eq("username", username)\
                .execute()
            success = bool(result.data if hasattr(result, 'data') else result)
            if success:
                logger.info(f"✅ Event deleted")
            return success
        except Exception as e:
            logger.error(f"❌ Error deleting event: {e}")
            return False
    
    async def save_profile_picture(self, username: str, picture_data: str) -> Dict:
        """Save or update user profile picture"""
        logger.info(f"🖼️  Saving profile picture for user '{username}'")
        
        data = {
            "username": username,
            "profile_picture": picture_data,
            "updated_at": datetime.now().isoformat()
        }
        
        try:
            result = self.client.table(self.users_table)\
                .upsert(data, on_conflict="username")\
                .execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Profile picture saved")
                return result.data[0]
            return {}
        except Exception as e:
            logger.error(f"❌ Error saving profile picture: {e}")
            return {}
    
    async def get_profile_picture(self, username: str) -> Optional[str]:
        """Get user profile picture"""
        logger.info(f"🖼️  Fetching profile picture for user '{username}'")
        
        try:
            result = self.client.table(self.users_table)\
                .select("profile_picture")\
                .eq("username", username)\
                .execute()
            
            if hasattr(result, 'data') and result.data and len(result.data) > 0:
                picture = result.data[0].get("profile_picture")
                if picture:
                    logger.info(f"✅ Profile picture found")
                return picture
            return None
        except Exception as e:
            logger.error(f"❌ Error getting profile picture: {e}")
            return None


def require_auth(handler):
    """Decorator to require authentication for routes"""
    async def wrapper(self, request: web.Request):
        session = await get_session(request)
        if not session.get('authenticated'):
            raise web.HTTPFound('/login')
        return await handler(self, request)
    return wrapper


class SharkCalendarApp:
    """Main application class for shark calendar"""
    
    def __init__(self, env_vars: Dict[str, str]):
        logger.info("🦈 Initializing Shark Calendar Application...")
        self.env_vars = env_vars
        self.user = User(env_vars['USER'], env_vars['PASS'])
        self.db = SharkCalendarDB(
            env_vars['SUPABASE_URL'],
            env_vars['SUPABASE_KEY'],
            env_vars.get('SUPABASE_POOLER_URL')
        )
        # default tags (as requested) — user can pick from these and also add custom tags per-event
        self.tags = [
            "Coding Project",
            "College Assignment",
            "Video",
            "Stream",
            "Personal",
            "Work"
        ]
        self.app = web.Application()
        logger.info("🔧 Setting up session management...")
        self.setup_session()
        logger.info("🛣️  Setting up routes...")
        self.setup_routes()
        logger.info("📄 Setting up templates...")
        self.setup_templates()
        logger.info("✅ Shark Calendar Application initialized successfully")
    
    def setup_session(self):
        """Setup simple session storage"""
        logger.info("🔐 Configuring session storage...")
        setup(self.app, SimpleCookieStorage())
        logger.info("✅ Session storage configured")
    
    def setup_templates(self):
        """Setup Jinja2 templates"""
        aiohttp_jinja2.setup(
            self.app,
            loader=jinja2.DictLoader({
                'login.html': self.get_login_template(),
                'index.html': self.get_index_template(),
            })
        )
    
    def setup_routes(self):
        """Setup application routes"""
        self.app.router.add_get('/health', self.health_check)
        self.app.router.add_get('/favicon.ico', self.serve_favicon)
        self.app.router.add_get('/login', self.login_page)
        self.app.router.add_post('/login', self.do_login)
        self.app.router.add_get('/logout', self.logout)
        self.app.router.add_get('/', self.index)
        self.app.router.add_get('/api/events', self.get_events)
        self.app.router.add_post('/api/events', self.create_event)
        self.app.router.add_put('/api/events/{id}', self.update_event)
        self.app.router.add_delete('/api/events/{id}', self.delete_event)
        self.app.router.add_post('/api/profile-picture', self.upload_profile_picture)
        self.app.router.add_get('/api/profile-picture', self.get_profile_picture)
        logger.info("✅ Routes configured")
    
    async def health_check(self, request: web.Request):
        """Public health check endpoint"""
        return web.json_response({
            'status': 'ok',
            'service': 'Shark Calendar API',
            'timestamp': datetime.now().isoformat(),
            'message': '🦈 Swimming smoothly!'
        })
    
    async def serve_favicon(self, request: web.Request):
        """Serve shark emoji as favicon"""
        svg_favicon = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
            <text y="80" font-size="80">🦈</text>
        </svg>'''
        return web.Response(
            body=svg_favicon,
            content_type='image/svg+xml',
            headers={'Cache-Control': 'public, max-age=604800'}
        )
    
    @aiohttp_jinja2.template('login.html')
    async def login_page(self, request: web.Request):
        """Render login page"""
        session = await get_session(request)
        if session.get('authenticated'):
            raise web.HTTPFound('/')
        return {'error': request.query.get('error', '')}
    
    async def do_login(self, request: web.Request):
        """Handle login form submission"""
        data = await request.post()
        username = data.get('username', '')
        password = data.get('password', '')
        
        if username == self.user.username and self.user.verify_password(password):
            session = await new_session(request)
            session['authenticated'] = True
            session['username'] = username
            
            profile_pic = await self.db.get_profile_picture(username)
            if profile_pic:
                self.user.set_profile_picture(profile_pic)
            
            raise web.HTTPFound('/')
        else:
            raise web.HTTPFound('/login?error=Invalid credentials')
    
    async def logout(self, request: web.Request):
        """Handle logout"""
        session = await get_session(request)
        session.clear()
        raise web.HTTPFound('/login')
    
    @require_auth
    @aiohttp_jinja2.template('index.html')
    async def index(self, request: web.Request):
        """Render main calendar page"""
        session = await get_session(request)
        username = session['username']
        profile_pic = await self.db.get_profile_picture(username)
        
        return {
            'title': 'Shark Calendar 🦈',
            'username': username,
            'profile_picture': profile_pic,
            'year': datetime.now().year,
            'month': datetime.now().strftime('%B'),
            'tags': self.tags
        }
    
    @require_auth
    async def get_events(self, request: web.Request):
        """API endpoint to get events"""
        session = await get_session(request)
        username = session['username']
        start_date = request.query.get('start_date')
        end_date = request.query.get('end_date')
        
        events = await self.db.get_events(username, start_date, end_date)
        return web.json_response(events)
    
    @require_auth
    async def create_event(self, request: web.Request):
        """API endpoint to create event (tags instead of time)"""
        session = await get_session(request)
        username = session['username']
        
        try:
            data = await request.json()
            tags = data.get('tags', [])
            # allow tags string as comma-separated too
            event = await self.db.create_event(
                title=data['title'],
                description=data.get('description', ''),
                event_date=data['event_date'],
                event_time=None,  # time replaced by tags per your request
                tags=tags,
                username=username
            )
            return web.json_response(event, status=201)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def update_event(self, request: web.Request):
        """API endpoint to update event"""
        session = await get_session(request)
        username = session['username']
        
        try:
            event_id = int(request.match_info['id'])
            data = await request.json()
            event = await self.db.update_event(event_id, username, data)
            return web.json_response(event)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def delete_event(self, request: web.Request):
        """API endpoint to delete event"""
        session = await get_session(request)
        username = session['username']
        
        try:
            event_id = int(request.match_info['id'])
            success = await self.db.delete_event(event_id, username)
            if success:
                return web.json_response({'success': True})
            return web.json_response({'error': 'Event not found'}, status=404)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def upload_profile_picture(self, request: web.Request):
        """API endpoint to upload profile picture"""
        session = await get_session(request)
        username = session['username']
        
        try:
            data = await request.json()
            picture_data = data.get('picture')
            
            if not picture_data:
                return web.json_response({'error': 'No picture data'}, status=400)
            
            await self.db.save_profile_picture(username, picture_data)
            return web.json_response({'success': True})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def get_profile_picture(self, request: web.Request):
        """API endpoint to get profile picture"""
        session = await get_session(request)
        username = session['username']
        
        try:
            picture = await self.db.get_profile_picture(username)
            return web.json_response({'picture': picture})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    def get_login_template(self) -> str:
        """Return HTML template for login page"""
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🦈 Shark Calendar - Login</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Space Mono', monospace;
            background: linear-gradient(180deg, #001a33 0%, #003d5c 50%, #0066a1 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-container {
            background: rgba(0, 26, 51, 0.85);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 8px;
            padding: 50px 40px;
            max-width: 450px;
            width: 100%;
            box-shadow: 0 0 60px rgba(0, 217, 255, 0.2);
        }
        .shark-logo { font-size: 100px; text-align: center; margin-bottom: 20px; }
        h1 {
            font-family: 'Bebas Neue', cursive;
            color: #00d9ff;
            text-align: center;
            font-size: 3.5em;
            letter-spacing: 8px;
            margin-bottom: 10px;
            text-transform: uppercase;
            text-shadow: 0 0 20px rgba(0, 217, 255, 0.5);
        }
        .subtitle {
            color: #e8f4f8;
            text-align: center;
            margin-bottom: 40px;
            font-size: 0.9em;
            letter-spacing: 2px;
            text-transform: uppercase;
            opacity: 0.7;
        }
        .form-group { margin-bottom: 25px; }
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: #e8f4f8;
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 2px;
            text-transform: uppercase;
        }
        .form-group input {
            width: 100%;
            padding: 15px;
            background: rgba(0, 61, 92, 0.4);
            border: 2px solid rgba(0, 217, 255, 0.2);
            border-radius: 4px;
            font-size: 16px;
            font-family: 'Space Mono', monospace;
            color: #e8f4f8;
            transition: all 0.3s;
        }
        .form-group input:focus {
            outline: none;
            border-color: #00d9ff;
            box-shadow: 0 0 20px rgba(0, 217, 255, 0.3);
        }
        .btn-login {
            width: 100%;
            padding: 18px;
            background: linear-gradient(135deg, #0066a1 0%, #003d5c 100%);
            color: #e8f4f8;
            border: 2px solid #00d9ff;
            border-radius: 4px;
            font-size: 16px;
            font-weight: 700;
            font-family: 'Space Mono', monospace;
            cursor: pointer;
            transition: all 0.3s;
            letter-spacing: 3px;
            text-transform: uppercase;
        }
        .btn-login:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 30px rgba(0, 217, 255, 0.4);
        }
        .error {
            background: rgba(255, 71, 87, 0.2);
            color: #ff4757;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
            border: 2px solid #ff4757;
            text-align: center;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="shark-logo">🦈</div>
        <h1>APEX</h1>
        <p class="subtitle">Calendar System</p>
        {% if error %}
        <div class="error">⚠ {{ error }}</div>
        {% endif %}
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">Operator ID</label>
                <input type="text" id="username" name="username" placeholder="Enter credentials" required autofocus>
            </div>
            <div class="form-group">
                <label for="password">Access Code</label>
                <input type="password" id="password" name="password" placeholder="••••••••" required>
            </div>
            <button type="submit" class="btn-login">▶ Dive In</button>
        </form>
    </div>
</body>
</html>'''
    
    def get_index_template(self) -> str:
        """Return HTML template for main page with cropping feature, tags, month view and delete support.
        Time field has been replaced with tags and a custom-tag input.
        """
        template = r'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.6.1/cropper.min.css">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root {
            --ocean-deep: #001a33;
            --ocean-mid: #003d5c;
            --ocean-light: #0066a1;
            --neon-cyan: #00d9ff;
            --foam-white: #e8f4f8;
        }
        body {
            font-family: 'Space Mono', monospace;
            background: linear-gradient(180deg, var(--ocean-deep) 0%, var(--ocean-mid) 50%, var(--ocean-light) 100%);
            min-height: 100vh;
            color: var(--foam-white);
        }
        .container { max-width: 1100px; margin: 0 auto; padding: 20px; }
        .header {
            background: rgba(0, 26, 51, 0.85);
            border-radius: 12px;
            padding: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { color: var(--neon-cyan); font-family: 'Bebas Neue', cursive; }
        .controls { margin: 18px 0; display:flex; gap:12px; align-items:center; }
        .btn { padding: 10px 16px; border-radius:8px; background: rgba(0,61,92,0.5); border:1px solid rgba(0,217,255,0.2); cursor:pointer; color:var(--foam-white); }
        .btn-secondary { background: rgba(128,139,150,0.12); }
        .list-view { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
        .event-card { background: rgba(0,26,51,0.85); padding:12px; border-radius:10px; position:relative; }
        .small-action { position:absolute; right:12px; top:12px; display:flex; gap:8px; }
        .small-btn { background: rgba(128,139,150,0.12); border-radius:6px; padding:6px 8px; cursor:pointer; color:var(--foam-white); border:1px solid rgba(128,139,150,0.15); }
        .tag-badge { display:inline-block; background:rgba(0,217,255,0.08); color:var(--neon-cyan); padding:4px 8px; border-radius:12px; margin-right:6px; font-size:12px; border:1px solid rgba(0,217,255,0.06); }
        .modal { display:none; position:fixed; inset:0; align-items:center; justify-content:center; background: rgba(0,26,51,0.9); z-index:1000; }
        .modal.active { display:flex; }
        .modal-content { background: rgba(0,26,51,0.95); padding:22px; border-radius:10px; width:480px; max-width:95%; }
        .form-group { margin-bottom:14px; }
        .form-group label { display:block; margin-bottom:6px; font-size:12px; letter-spacing:1px; }
        .form-group input, .form-group textarea, .form-group select { width:100%; padding:10px; border-radius:8px; background: rgba(0,61,92,0.4); border:1px solid rgba(0,217,255,0.12); color:var(--foam-white); }
        .month-grid { display:grid; grid-template-columns: repeat(7,1fr); gap:8px; }
        .month-cell { background: rgba(0,26,51,0.85); padding:8px; border-radius:8px; min-height:84px; }
        .date-num { color:var(--neon-cyan); font-weight:700; margin-bottom:6px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🦈 Shark Calendar</h1>
            <div>
                <div style="display:flex; align-items:center; gap:12px;">
                    <div style="display:inline-block; cursor:pointer;" onclick="openProfileModal()">
                        {% if profile_picture %}
                            <img src="{{ profile_picture }}" style="width:44px; height:44px; border-radius:50%; border:2px solid var(--neon-cyan);" alt="Profile">
                        {% else %}
                            <div style="width:44px; height:44px; border-radius:50%; background:#e8f4f8; display:flex; align-items:center; justify-content:center;">👤</div>
                        {% endif %}
                    </div>
                    <div style="display:inline-block; color:var(--neon-cyan); font-weight:700;">{{ username }}</div>
                    <a href="/logout" class="btn" style="margin-left:12px;">Logout</a>
                </div>
            </div>
        </div>

        <div class="controls">
            <button class="btn" onclick="openEventModal()">+ New Event</button>
            <button id="toggleViewBtn" class="btn btn-secondary" onclick="toggleView()">Month View</button>
        </div>

        <div class="calendar-view">
            <div id="monthView" style="display:none;">
                <div id="monthGrid" class="month-grid"></div>
            </div>

            <div id="listView" class="list-view"></div>
        </div>
    </div>

    <!-- Event Modal -->
    <div id="eventModal" class="modal">
        <div class="modal-content">
            <h3>🦈 Create Event</h3>
            <form id="eventForm">
                <div class="form-group">
                    <label for="eventTitle">Title</label>
                    <input id="eventTitle" type="text" required>
                </div>

                <div class="form-group">
                    <label for="eventDescription">Description</label>
                    <textarea id="eventDescription" rows="3"></textarea>
                </div>

                <div class="form-group">
                    <label for="eventDate">Date</label>
                    <input id="eventDate" type="date" required>
                </div>

                <!-- Time has been removed and replaced with tags -->
                <div class="form-group">
                    <label for="eventTags">Pick Tags (hold Ctrl/Cmd to select multiple)</label>
                    <select id="eventTags" multiple size="5">
                        {% for tag in tags %}
                        <option value="{{ tag }}">{{ tag }}</option>
                        {% endfor %}
                    </select>
                </div>

                <div class="form-group">
                    <label for="customTags">Custom tags (comma separated)</label>
                    <input id="customTags" type="text" placeholder="e.g. urgent, side-project">
                </div>

                <div style="display:flex; gap:10px; justify-content:flex-end;">
                    <button type="button" class="btn btn-secondary" onclick="closeEventModal()">Cancel</button>
                    <button type="submit" class="btn">Create</button>
                </div>
            </form>
        </div>
    </div>

    <!-- Profile Modal (cropping) -->
    <div id="profileModal" class="modal">
        <div class="modal-content">
            <h3>🦈 Profile Picture</h3>
            <input type="file" id="profileFileInput" accept="image/*" style="margin-bottom:10px;">
            <div id="cropContainer" style="display:none; max-height:360px; overflow:auto; margin-bottom:10px;">
                <img id="cropImage" style="max-width:100%;">
            </div>
            <div style="display:flex; gap:8px; justify-content:flex-end;">
                <button type="button" class="btn btn-secondary" onclick="closeProfileModal()">Cancel</button>
                <button type="button" id="saveCropBtn" class="btn" style="display:none;">Save</button>
            </div>
        </div>
    </div>

    <script src="https://cdnjs.cloudflare.com/ajax/libs/cropperjs/1.6.1/cropper.min.js"></script>
    <script>
        let cropper = null;
        let eventsData = [];
        let isMonthView = false;

        async function fetchEvents() {
            const resp = await fetch('/api/events');
            if (!resp.ok) return [];
            return resp.json();
        }

        function escapeHtml(text) {
            if (!text) return '';
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        async function loadEvents() {
            try {
                eventsData = await fetchEvents();
                renderCurrentView();
            } catch (error) {
                console.error('Error loading events:', error);
            }
        }

        function renderCurrentView() {
            if (isMonthView) {
                document.getElementById('listView').style.display = 'none';
                document.getElementById('monthView').style.display = 'block';
                renderMonthView(eventsData);
                document.getElementById('toggleViewBtn').innerText = 'List View';
            } else {
                document.getElementById('monthView').style.display = 'none';
                document.getElementById('listView').style.display = 'grid';
                renderListView(eventsData);
                document.getElementById('toggleViewBtn').innerText = 'Month View';
            }
        }

        function toggleView() {
            isMonthView = !isMonthView;
            renderCurrentView();
        }

        function renderListView(events) {
            const container = document.getElementById('listView');
            if (!events || events.length === 0) {
                container.innerHTML = `<div style="padding:40px; text-align:center;"><div style="font-size:96px; opacity:0.25;">🦈</div><h3>No events yet</h3><p>Create your first event</p></div>`;
                return;
            }
            container.innerHTML = events.map(event => {
                const tagsHtml = (event.tags || []).map(t => `<span class="tag-badge">${escapeHtml(t)}</span>`).join('');
                return `
                    <div class="event-card" data-id="${event.id}">
                        <div class="small-action">
                            <button class="small-btn" onclick="eventStop(event); editEvent(${event.id})">Edit</button>
                            <button class="small-btn" onclick="eventStop(event); deleteEvent(${event.id})">Delete</button>
                        </div>
                        <div style="font-weight:700; margin-bottom:6px;">${escapeHtml(event.title)}</div>
                        <div style="font-size:13px; opacity:0.9; margin-bottom:8px;">📅 ${escapeHtml(event.event_date)}</div>
                        <div style="margin-bottom:8px;">${escapeHtml(event.description || '')}</div>
                        <div>${tagsHtml}</div>
                    </div>
                `;
            }).join('');
        }

        function renderMonthView(events) {
            const now = new Date();
            const year = now.getFullYear();
            const month = now.getMonth();
            const firstDay = new Date(year, month, 1);
            const startDay = firstDay.getDay();
            const daysInMonth = new Date(year, month + 1, 0).getDate();

            const grid = document.getElementById('monthGrid');
            grid.innerHTML = '';
            // pad
            for (let i = 0; i < startDay; i++) grid.innerHTML += `<div class="month-cell" style="opacity:0.4;"></div>`;
            for (let d = 1; d <= daysInMonth; d++) {
                const dateStr = `${year}-${String(month+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
                const dayEvents = (events || []).filter(ev => ev.event_date === dateStr);
                const evHtml = dayEvents.map(ev => `<div class="tag-badge" style="display:block;margin-bottom:6px;cursor:pointer;" onclick="eventStop(event); viewEvent(${ev.id})">${escapeHtml(ev.title)}</div>`).join('');
                grid.innerHTML += `<div class="month-cell"><div class="date-num">${d}</div>${evHtml}</div>`;
            }
        }

        function eventStop(e) {
            e.stopPropagation();
            e.preventDefault();
        }

        async function deleteEvent(id) {
            if (!confirm('Delete this event?')) return;
            try {
                const resp = await fetch(`/api/events/${id}`, { method: 'DELETE' });
                if (resp.ok) {
                    await loadEvents();
                } else {
                    const err = await resp.json();
                    alert('Failed to delete event: ' + (err.error || resp.statusText));
                }
            } catch (err) {
                alert('Failed to delete event');
            }
        }

        function openEventModal() {
            document.getElementById('eventModal').classList.add('active');
            // reset fields
            document.getElementById('eventForm').reset();
        }

        function closeEventModal() {
            document.getElementById('eventModal').classList.remove('active');
            document.getElementById('eventForm').reset();
        }

        function editEvent(id) {
            alert('Edit not implemented yet. I can add in-place editing if you want.');
        }

        function viewEvent(id) {
            // Could open a details modal — placeholder
            const ev = eventsData.find(e => e.id === id);
            if (ev) alert(`${ev.title}\n\n${ev.description || ''}\n\nTags: ${(ev.tags || []).join(', ')}`);
        }

        // Profile cropping handlers
        document.getElementById('profileFileInput').addEventListener('change', function(e) {
            const file = e.target.files[0];
            if (!file) return;
            const reader = new FileReader();
            reader.onload = function(evt) {
                const img = document.getElementById('cropImage');
                img.src = evt.target.result;
                document.getElementById('cropContainer').style.display = 'block';
                document.getElementById('saveCropBtn').style.display = 'inline-block';
                if (cropper) cropper.destroy();
                cropper = new Cropper(img, { aspectRatio: 1, viewMode: 2, autoCropArea: 1 });
            };
            reader.readAsDataURL(file);
        });

        document.getElementById('saveCropBtn').addEventListener('click', async function() {
            if (!cropper) return;
            const canvas = cropper.getCroppedCanvas({ width: 200, height: 200 });
            const dataUrl = canvas.toDataURL('image/jpeg', 0.85);
            try {
                const resp = await fetch('/api/profile-picture', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ picture: dataUrl })
                });
                if (resp.ok) location.reload();
                else alert('Failed to save picture');
            } catch (e) { alert('Failed to save picture'); }
        });

        document.querySelector('#profileModal .btn.btn-secondary')?.addEventListener?.('click', () => {
            if (cropper) { cropper.destroy(); cropper = null; }
            document.getElementById('cropContainer').style.display = 'none';
            document.getElementById('saveCropBtn').style.display = 'none';
            document.getElementById('profileFileInput').value = '';
        });

        document.getElementById('eventForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const title = document.getElementById('eventTitle').value.trim();
            const description = document.getElementById('eventDescription').value.trim();
            const event_date = document.getElementById('eventDate').value;
            // collect selected default tags
            const select = document.getElementById('eventTags');
            const selected = Array.from(select.selectedOptions).map(o => o.value);
            // parse custom tags input (comma separated)
            const customRaw = document.getElementById('customTags').value || '';
            const custom = customRaw.split(',').map(s => s.trim()).filter(Boolean);
            const tags = [...new Set([...(selected || []), ...(custom || [])])]; // dedupe

            const payload = { title, description, event_date, tags };

            try {
                const resp = await fetch('/api/events', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                if (resp.ok) {
                    closeEventModal();
                    await loadEvents();
                } else {
                    const err = await resp.json();
                    alert('Failed to create event: ' + (err.error || resp.statusText));
                }
            } catch (err) {
                alert('Failed to create event');
            }
        });

        // initial load
        loadEvents();
    </script>
</body>
</html>'''
        return template


# Main entry point
async def init_app():
    """Initialize and return the app"""
    logger.info("=" * 80)
    logger.info("🦈 SHARK CALENDAR SYSTEM - STARTING UP")
    logger.info("=" * 80)
    
    try:
        env_vars = load_environment()
        shark_app = SharkCalendarApp(env_vars)
        await shark_app.db.initialize_tables()
        
        logger.info("=" * 80)
        logger.info("✅ SHARK CALENDAR SYSTEM - READY")
        logger.info("=" * 80)
        
        return shark_app.app
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize application: {e}")
        raise


if __name__ == '__main__':
    import sys
    
    try:
        env_vars = load_environment()
        shark_app = SharkCalendarApp(env_vars)
        
        async def startup(app):
            await shark_app.db.initialize_tables()
        
        shark_app.app.on_startup.append(startup)
        
        host = env_vars['APP_HOST']
        port = int(env_vars['APP_PORT'])
        
        logger.info("=" * 80)
        logger.info(f"🌊 Starting server on {host}:{port}")
        logger.info("=" * 80)
        
        web.run_app(shark_app.app, host=host, port=port)
        
    except KeyboardInterrupt:
        logger.info("\n🛑 Shutting down gracefully...")
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}")
        sys.exit(1)
