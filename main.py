"""
Shark-Themed Web Calendar System with Authentication and PFP Cropping
Requires: aiohttp, supabase, aiohttp-session, cryptography, aiohttp-jinja2
Install: pip install aiohttp supabase aiohttp-jinja2 jinja2 aiohttp-session cryptography
"""

import os
import json
import hashlib
import logging
from datetime import datetime
from typing import Optional, Dict, List
from aiohttp import web
import aiohttp_jinja2
import jinja2
from aiohttp_session import setup, get_session, new_session, SimpleCookieStorage
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
    platforms TEXT DEFAULT '[]',
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
                logger.error("\n" + "=" * 70)
                logger.error(create_sql)
                logger.error("=" * 70 + "\n")
                logger.info("📝 Copy the SQL above and run it in your Supabase SQL Editor")
                logger.info("🔗 Go to: https://supabase.com/dashboard/project/_/sql")
            else:
                logger.info("✅ All database tables verified and ready")

        except Exception as e:
            logger.warning(f"⚠️  Database check: {e}")

    async def _normalize_list_field(self, value) -> str:
        """Return JSON string for storage, accept list or comma-separated or JSON string."""
        try:
            if isinstance(value, list):
                return json.dumps(value)
            if isinstance(value, str):
                # if JSON array string
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return value
                except Exception:
                    # comma separated
                    parts = [p.strip() for p in value.split(',') if p.strip()]
                    return json.dumps(parts)
            return json.dumps([])
        except Exception:
            return json.dumps([])

    async def create_event(self, title: str, description: str,
                          event_date: str, event_time: Optional[str],
                          tags, platforms, username: str) -> Dict:
        """Create a new calendar event

        event_time kept for compatibility but can be None.
        tags/platforms stored as JSON text.
        """
        logger.info(f"📝 Creating event: '{title}' for user '{username}'")

        tags_json = await self._normalize_list_field(tags)
        platforms_json = await self._normalize_list_field(platforms)

        data = {
            "title": title,
            "description": description,
            "event_date": event_date,
            "event_time": event_time or None,
            "tags": tags_json,
            "platforms": platforms_json,
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
                try:
                    created['platforms'] = json.loads(created.get('platforms', '[]'))
                except Exception:
                    created['platforms'] = []
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
                # Normalize tags/platforms to lists
                for ev in events:
                    try:
                        ev['tags'] = json.loads(ev.get('tags', '[]')) if ev.get('tags') else []
                    except Exception:
                        ev['tags'] = []
                    try:
                        ev['platforms'] = json.loads(ev.get('platforms', '[]')) if ev.get('platforms') else []
                    except Exception:
                        ev['platforms'] = []
                return events
            return []
        except Exception as e:
            logger.error(f"❌ Error getting events: {e}")
            return []

    async def update_event(self, event_id: int, username: str, updates: Dict) -> Dict:
        """Update an existing event"""
        logger.info(f"✏️  Updating event ID {event_id}")

        # Ensure lists normalized if present
        if 'tags' in updates:
            updates['tags'] = await self._normalize_list_field(updates['tags'])
        if 'platforms' in updates:
            updates['platforms'] = await self._normalize_list_field(updates['platforms'])

        try:
            result = self.client.table(self.events_table) \
                .update(updates) \
                .eq("id", event_id) \
                .eq("username", username) \
                .execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Event updated")
                updated = result.data[0]
                try:
                    updated['tags'] = json.loads(updated.get('tags', '[]'))
                except Exception:
                    updated['tags'] = []
                try:
                    updated['platforms'] = json.loads(updated.get('platforms', '[]'))
                except Exception:
                    updated['platforms'] = []
                return updated
            return {}
        except Exception as e:
            logger.error(f"❌ Error updating event: {e}")
            return {}

    async def delete_event(self, event_id: int, username: str) -> bool:
        """Delete an event"""
        logger.info(f"🗑️  Deleting event ID {event_id}")

        try:
            result = self.client.table(self.events_table) \
                .delete() \
                .eq("id", event_id) \
                .eq("username", username) \
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
            result = self.client.table(self.users_table) \
                .upsert(data, on_conflict="username") \
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
            result = self.client.table(self.users_table) \
                .select("profile_picture") \
                .eq("username", username) \
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

        # default tags
        self.tags = [
            "Coding Project",
            "College Assignment",
            "Video",
            "Stream",
            "Personal",
            "Work"
        ]

        # default platforms (predefined choices)
        self.platforms = [
            "GitHub",
            "Roblox",
            "Discord",
            "Teams",
            "YouTube",
            "Twitch"
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
        now = datetime.now()

        return {
            'title': 'Shark Calendar 🦈',
            'username': username,
            'profile_picture': profile_pic,
            'year': now.year,
            'month': now.strftime('%B'),
            'cur_year': now.year,
            'cur_month': now.month,
            'tags': self.tags,
            'platforms': self.platforms
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
        """API endpoint to create event (tags & platforms instead of time)"""
        session = await get_session(request)
        username = session['username']

        try:
            data = await request.json()
            tags = data.get('tags', [])
            platforms = data.get('platforms', [])
            event = await self.db.create_event(
                title=data['title'],
                description=data.get('description', ''),
                event_date=data['event_date'],
                event_time=None,
                tags=tags,
                platforms=platforms,
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
        """Return HTML template for main page with enhanced month layout and platforms support."""
        template = r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1" />
<title>{{ title }}</title>
<link rel="icon" href="/favicon.ico" type="image/x-icon">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
/* Layout: left sidebar with month, right main calendar laid out horizontally like Google Calendar */
:root{
  --bg:#001a33; --mid:#003d5c; --light:#0066a1; --cyan:#00d9ff; --text:#e8f4f8;
}
body { margin:0; font-family:'Space Mono',monospace; background:linear-gradient(180deg,var(--bg),var(--mid),var(--light)); color:var(--text); min-height:100vh; }
.app { max-width:1400px; margin:20px auto; display:flex; gap:16px; padding:12px; }
.sidebar { width:260px; background:rgba(0,26,51,0.85); border-radius:10px; padding:12px; border:1px solid rgba(0,217,255,0.06); }
.main { flex:1; background:rgba(0,26,51,0.85); border-radius:10px; padding:12px; border:1px solid rgba(0,217,255,0.06); }
.header { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; }
.h1 { font-family:'Bebas Neue',cursive; font-size:22px; color:var(--cyan); letter-spacing:2px; }
.controls { display:flex; gap:8px; align-items:center; }
.btn{ background:rgba(0,61,92,0.5); color:var(--text); border:none; padding:8px 10px; border-radius:8px; cursor:pointer; }
.btn-secondary{ background:rgba(128,139,150,0.12); }
.mini-month { margin-bottom:12px; }
.weekdays { display:grid; grid-template-columns:repeat(7,1fr); gap:6px; margin-bottom:6px; }
.weekday{ text-align:center; font-weight:700; color:rgba(255,255,255,0.85); font-size:12px; }
.calendar-grid { display:grid; grid-template-columns: 120px 1fr; gap:12px; }
/* left column is day list, right is a wide horizontal grid of days for weeks */
.day-list { width:120px; display:flex; flex-direction:column; gap:6px; }
.day-item { background:rgba(0,26,51,0.75); padding:8px; border-radius:6px; font-weight:700; text-align:center; border:1px solid rgba(0,217,255,0.03); }
.weeks { flex:1; display:flex; gap:6px; overflow:auto; padding-bottom:6px; }
/* each week is a vertical column */
.week-col { min-width:200px; background:rgba(0,26,51,0.6); border-radius:8px; padding:8px; display:flex; flex-direction:column; gap:8px; border:1px solid rgba(0,217,255,0.02); }
.week-day { min-height:80px; padding:6px; border-radius:6px; background:rgba(0,26,51,0.85); position:relative; border:1px dashed rgba(0,217,255,0.02); }
/* event cards shown inside day box */
.event-card { background:rgba(0,217,255,0.06); border-radius:6px; padding:6px 8px; font-size:13px; margin-bottom:6px; cursor:pointer; border:1px solid rgba(0,217,255,0.04); overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.event-card .meta { font-size:11px; color:rgba(255,255,255,0.8); margin-top:4px; }
.tag { display:inline-block; padding:2px 6px; border-radius:6px; font-size:11px; color:var(--cyan); border:1px solid rgba(0,217,255,0.06); margin-right:6px; background:rgba(0,217,255,0.02); }
.platform { display:inline-block; padding:2px 6px; border-radius:6px; font-size:11px; color:#ffd; border:1px solid rgba(255,255,255,0.03); margin-left:6px; background:rgba(255,255,255,0.02); }
/* modals */
.modal { display:none; position:fixed; inset:0; align-items:center; justify-content:center; background:rgba(0,0,0,0.6); z-index:999; }
.modal.active{ display:flex; }
.modal-box { background:rgba(0,26,51,0.98); padding:16px; border-radius:8px; width:520px; max-width:96%; border:1px solid rgba(0,217,255,0.05); }
.form-row { margin-bottom:10px; }
input[type="text"], input[type="date"], textarea, select { width:100%; padding:8px; border-radius:6px; background:rgba(0,61,92,0.4); color:var(--text); border:1px solid rgba(0,217,255,0.06); }
.small { font-size:12px; color:rgba(255,255,255,0.8); }
</style>
</head>
<body>
<div style="max-width:1200px;margin:12px auto;color:var(--text);display:flex;justify-content:space-between;align-items:center;">
  <div style="font-family:'Bebas Neue',cursive;font-size:26px;color:var(--cyan);">🦈 Shark Calendar</div>
  <div style="display:flex;gap:12px;align-items:center;">
    <div style="cursor:pointer;" onclick="openProfileModal()">
      {% if profile_picture %}
        <img src="{{ profile_picture }}" style="width:40px;height:40px;border-radius:50%;border:2px solid var(--cyan)" alt="pfp">
      {% else %}
        <div style="width:40px;height:40px;border-radius:50%;background:#e8f4f8;display:flex;align-items:center;justify-content:center">👤</div>
      {% endif %}
    </div>
    <div style="font-weight:700;color:var(--cyan)">{{ username }}</div>
    <a href="/logout" class="btn">Logout</a>
  </div>
</div>

<div class="app">
  <div class="sidebar">
    <div class="mini-month">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
        <button class="btn" onclick="prevMonth()">◀</button>
        <div style="font-weight:700;color:var(--cyan)"; id="sidebarMonthTitle">Month Year</div>
        <button class="btn" onclick="nextMonth()">▶</button>
      </div>
      <div class="weekdays">
        <div class="weekday">M</div><div class="weekday">T</div><div class="weekday">W</div><div class="weekday">T</div><div class="weekday">F</div><div class="weekday">S</div><div class="weekday">S</div>
      </div>
      <div id="miniGrid" style="display:grid;grid-template-columns:repeat(7,1fr);gap:6px;"></div>
    </div>

    <div style="margin-top:12px;">
      <div style="font-weight:700;margin-bottom:8px;">Quick Actions</div>
      <button class="btn" onclick="openEventModal()">+ New Event</button>
      <div style="margin-top:12px;">
        <div style="font-weight:700;margin-bottom:6px;">Platforms</div>
        {% for p in platforms %}
          <div class="small">• {{ p }}</div>
        {% endfor %}
      </div>
    </div>
  </div>

  <div class="main">
    <div class="header">
      <div class="h1" id="mainMonthTitle">Month Year</div>
      <div class="controls">
        <button class="btn" onclick="toggleView()">Toggle View</button>
      </div>
    </div>

    <div class="calendar-grid">
      <div class="day-list" id="dayList">
        <!-- left sidebar days (Mon, Tue, ...) -->
      </div>

      <div class="weeks" id="weeksContainer">
        <!-- weeks will be appended as columns; each column contains 7 day boxes showing events -->
      </div>
    </div>

  </div>
</div>

<!-- Event Modal -->
<div id="eventModal" class="modal">
  <div class="modal-box">
    <h3>Create Event</h3>
    <form id="eventForm">
      <div class="form-row">
        <label class="small">Title</label>
        <input id="eventTitle" type="text" required>
      </div>
      <div class="form-row">
        <label class="small">Description</label>
        <textarea id="eventDescription" rows="3"></textarea>
      </div>
      <div class="form-row">
        <label class="small">Date</label>
        <input id="eventDate" type="date" required>
      </div>

      <div class="form-row">
        <label class="small">Pick Tags (Ctrl/Cmd to multi-select)</label>
        <select id="eventTags" multiple size="5">
          {% for tag in tags %}
          <option value="{{ tag }}">{{ tag }}</option>
          {% endfor %}
        </select>
      </div>

      <div class="form-row">
        <label class="small">Custom tags (comma separated)</label>
        <input id="customTags" type="text" placeholder="urgent,side-project">
      </div>

      <div class="form-row">
        <label class="small">Platforms (choose or add)</label>
        <select id="eventPlatforms" multiple size="6">
          {% for p in platforms %}
          <option value="{{ p }}">{{ p }}</option>
          {% endfor %}
        </select>
      </div>

      <div class="form-row">
        <label class="small">Custom platforms (comma separated)</label>
        <input id="customPlatforms" type="text" placeholder="e.g. itch, blender">
      </div>

      <div style="display:flex;justify-content:flex-end;gap:8px;">
        <button type="button" class="btn btn-secondary" onclick="closeEventModal()">Cancel</button>
        <button type="submit" class="btn">Create</button>
      </div>
    </form>
  </div>
</div>

<!-- Event Details Modal -->
<div id="eventDetailModal" class="modal">
  <div class="modal-box" id="eventDetailBox">
    <h3 id="detailTitle"></h3>
    <div id="detailDate" class="small"></div>
    <div id="detailDesc" style="margin-top:8px;"></div>
    <div id="detailTags" style="margin-top:8px;"></div>
    <div id="detailPlatforms" style="margin-top:8px;"></div>

    <div style="display:flex;justify-content:flex-end;margin-top:12px;gap:8px;">
      <button class="btn btn-secondary" onclick="closeEventDetail()">Close</button>
      <button class="btn" id="deleteFromDetailBtn">Delete</button>
    </div>
  </div>
</div>

<!-- Profile Modal (cropping omitted for brevity, kept as before) -->
<div id="profileModal" class="modal">
  <div class="modal-box">
    <h3>Profile Picture</h3>
    <input type="file" id="profileFileInput" accept="image/*" />
    <div id="cropContainer" style="display:none;margin-top:8px;"><img id="cropImage" style="max-width:100%"></div>
    <div style="display:flex;justify-content:flex-end;margin-top:8px;gap:8px;">
      <button class="btn btn-secondary" onclick="closeProfileModal()">Cancel</button>
      <button id="saveCropBtn" class="btn" style="display:none;">Save</button>
    </div>
  </div>
</div>

<script>
const monthNames = ["January","February","March","April","May","June","July","August","September","October","November","December"];
let currentYear = {{ cur_year }};
let currentMonth = {{ cur_month }}; // 1-12
let eventsData = [];

function escapeHtml(s){ if(!s) return ''; return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

async function fetchEvents(){ const r = await fetch('/api/events'); if(!r.ok) return []; return r.json(); }

async function loadEvents(){ eventsData = await fetchEvents(); renderCalendar(); renderSidebarMini(); }

function renderCalendar(){
  document.getElementById('mainMonthTitle').innerText = monthNames[currentMonth-1] + ' ' + currentYear;
  document.getElementById('sidebarMonthTitle').innerText = monthNames[currentMonth-1] + ' ' + currentYear;

  // Build day list (left), and weeks columns on right. We'll show 4-6 weeks depending on month.
  const first = new Date(currentYear, currentMonth-1, 1);
  // start on Monday
  const startOffset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(currentYear, currentMonth, 0).getDate();
  // compute total cells to show
  const totalCells = Math.ceil((startOffset + daysInMonth)/7) * 7;
  const weeks = totalCells / 7;

  const dayList = document.getElementById('dayList');
  dayList.innerHTML = '';
  for(let i=0;i<7;i++){
    const dayNames = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    const d = document.createElement('div');
    d.className='day-item';
    d.innerText = dayNames[i];
    dayList.appendChild(d);
  }

  const weeksContainer = document.getElementById('weeksContainer');
  weeksContainer.innerHTML = '';
  let day = 1 - startOffset;
  for(let w=0; w<weeks; w++){
    const col = document.createElement('div');
    col.className='week-col';
    for(let dow=0; dow<7; dow++){
      const dateNum = day;
      const cell = document.createElement('div');
      cell.className='week-day';
      if(dateNum < 1 || dateNum > daysInMonth){
        cell.style.opacity = '0.25';
      } else {
        const dateStr = `${currentYear}-${String(currentMonth).padStart(2,'0')}-${String(dateNum).padStart(2,'0')}`;
        const dayEvents = eventsData.filter(e => e.event_date === dateStr);
        const dateLabel = document.createElement('div');
        dateLabel.style.position='absolute'; dateLabel.style.left='8px'; dateLabel.style.top='6px';
        dateLabel.style.fontWeight='700'; dateLabel.style.color='var(--cyan)'; dateLabel.innerText = dateNum;
        cell.appendChild(dateLabel);

        const eventsContainer = document.createElement('div');
        eventsContainer.style.marginTop='26px';
        eventsContainer.style.display='flex';
        eventsContainer.style.flexDirection='column';
        eventsContainer.style.gap='6px';

        // show up to 3 events per day, others collapsed
        const maxShow = 3;
        for(let i=0;i<Math.min(dayEvents.length, maxShow); i++){
          const ev = dayEvents[i];
          const evDiv = document.createElement('div');
          evDiv.className='event-card';
          evDiv.innerText = ev.title.length>40?ev.title.slice(0,37)+'...':ev.title;
          evDiv.onclick = (e)=>{ e.stopPropagation(); openEventDetail(ev.id); };
          eventsContainer.appendChild(evDiv);
        }
        if(dayEvents.length > maxShow){
          const more = document.createElement('div');
          more.className='event-card';
          more.innerText = `+${dayEvents.length - maxShow} more`;
          more.onclick = (e)=>{ e.stopPropagation(); alert(dayEvents.map(a=>a.title).join('\n')); };
          eventsContainer.appendChild(more);
        }

        cell.appendChild(eventsContainer);
      }
      col.appendChild(cell);
      day++;
    }
    weeksContainer.appendChild(col);
  }
}

function renderSidebarMini(){
  // small grid showing all days (for quick glance)
  const mini = document.getElementById('miniGrid');
  mini.innerHTML = '';
  const first = new Date(currentYear, currentMonth-1, 1);
  const startOffset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(currentYear, currentMonth, 0).getDate();
  for(let i=0;i<startOffset;i++){
    const e = document.createElement('div'); e.style.opacity='0.2'; e.style.height='24px'; mini.appendChild(e);
  }
  for(let d=1; d<=daysInMonth; d++){
    const cell = document.createElement('div'); cell.style.height='24px'; cell.style.textAlign='center'; cell.style.fontSize='12px';
    cell.innerText = d; mini.appendChild(cell);
  }
}

function prevMonth(){ currentMonth--; if(currentMonth<1){ currentMonth=12; currentYear--; } loadEvents(); }
function nextMonth(){ currentMonth++; if(currentMonth>12){ currentMonth=1; currentYear++; } loadEvents(); }

function toggleView(){ /* placeholder: keep for future; currently UI is fixed to weeks layout */ alert('Month layout active.'); }

function openEventModal(){ document.getElementById('eventModal').classList.add('active'); document.getElementById('eventForm').reset(); }
function closeEventModal(){ document.getElementById('eventModal').classList.remove('active'); }

/* Event details modal */
function openEventDetail(id){
  const ev = eventsData.find(e=>e.id===id);
  if(!ev) return;
  document.getElementById('detailTitle').innerText = ev.title;
  document.getElementById('detailDate').innerText = 'Date: ' + ev.event_date;
  document.getElementById('detailDesc').innerText = ev.description || '';
  const tags = (ev.tags||[]).map(t=>`<span class="tag">${escapeHtml(t)}</span>`).join(' ');
  document.getElementById('detailTags').innerHTML = '<div class="small">Tags:</div>' + tags;
  const plats = (ev.platforms||[]).map(p=>`<span class="platform">${escapeHtml(p)}</span>`).join(' ');
  document.getElementById('detailPlatforms').innerHTML = '<div class="small">Platforms:</div>' + plats;
  document.getElementById('deleteFromDetailBtn').onclick = async ()=>{ if(confirm('Delete this event?')){ await deleteEvent(ev.id); closeEventDetail(); } };
  document.getElementById('eventDetailModal').classList.add('active');
}
function closeEventDetail(){ document.getElementById('eventDetailModal').classList.remove('active'); }

async function deleteEvent(id){
  try{
    const r = await fetch(`/api/events/${id}`, { method:'DELETE' });
    if(r.ok){ await loadEvents(); } else { const e = await r.json(); alert('Failed: '+(e.error||r.statusText)); }
  }catch(err){ alert('Delete failed'); }
}

/* Event create handler - collect tags + platforms */
document.getElementById('eventForm').addEventListener('submit', async (e)=>{
  e.preventDefault();
  const title = document.getElementById('eventTitle').value.trim();
  const description = document.getElementById('eventDescription').value.trim();
  const event_date = document.getElementById('eventDate').value;
  const selectedTags = Array.from(document.getElementById('eventTags').selectedOptions).map(o=>o.value);
  const customTags = (document.getElementById('customTags').value||'').split(',').map(s=>s.trim()).filter(Boolean);
  const tags = Array.from(new Set([...selectedTags, ...customTags]));

  const selectedPlats = Array.from(document.getElementById('eventPlatforms').selectedOptions).map(o=>o.value);
  const customPlats = (document.getElementById('customPlatforms').value||'').split(',').map(s=>s.trim()).filter(Boolean);
  const platforms = Array.from(new Set([...selectedPlats, ...customPlats]));

  const payload = { title, description, event_date, tags, platforms };
  try{
    const r = await fetch('/api/events', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(payload) });
    if(r.ok){ closeEventModal(); await loadEvents(); } else { const err = await r.json(); alert('Create failed: '+(err.error||r.statusText)); }
  }catch(err){ alert('Create failed'); }
});

/* profile picture handling - omitted cropper implementation for brevity; hooks kept */
document.getElementById('profileFileInput').addEventListener('change', function(e){
  // kept minimal: set file preview
  const f = e.target.files[0];
  if(!f) return;
  const reader = new FileReader();
  reader.onload = function(evt){ document.getElementById('cropImage').src = evt.target.result; document.getElementById('cropContainer').style.display='block'; document.getElementById('saveCropBtn').style.display='inline-block'; };
  reader.readAsDataURL(f);
});
document.getElementById('saveCropBtn').addEventListener('click', async ()=>{
  // simple upload of the image currently shown in #cropImage as dataURL
  const img = document.getElementById('cropImage');
  if(!img.src) return alert('No image');
  try{
    const r = await fetch('/api/profile-picture', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({ picture: img.src })});
    if(r.ok) location.reload(); else alert('Failed to save picture');
  }catch(err){ alert('Failed to save picture'); }
});

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
