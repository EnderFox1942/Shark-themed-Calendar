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
        try:
            if isinstance(value, list):
                return json.dumps(value)
            if isinstance(value, str):
                try:
                    parsed = json.loads(value)
                    if isinstance(parsed, list):
                        return value
                except Exception:
                    parts = [p.strip() for p in value.split(',') if p.strip()]
                    return json.dumps(parts)
            return json.dumps([])
        except Exception:
            return json.dumps([])

    async def create_event(self, title: str, description: str,
                          event_date: str, event_time: Optional[str],
                          tags, platforms, username: str) -> Dict:
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
        logger.info(f"✏️  Updating event ID {event_id}")
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
    async def wrapper(self, request: web.Request):
        session = await get_session(request)
        if not session.get('authenticated'):
            raise web.HTTPFound('/login')
        return await handler(self, request)
    return wrapper


class SharkCalendarApp:
    def __init__(self, env_vars: Dict[str, str]):
        logger.info("🦈 Initializing Shark Calendar Application...")
        self.env_vars = env_vars
        self.user = User(env_vars['USER'], env_vars['PASS'])
        self.db = SharkCalendarDB(
            env_vars['SUPABASE_URL'],
            env_vars['SUPABASE_KEY'],
            env_vars.get('SUPABASE_POOLER_URL')
        )
        self.tags = [
            "Coding Project",
            "College Assignment",
            "Video",
            "Stream",
            "Personal",
            "Work",
            "University",
            "Gaming"
        ]
        self.platforms = [
            "GitHub",
            "Roblox",
            "Discord",
            "Teams",
            "YouTube",
            "Twitch",
            "UCAS",
            "Valorant",
            "League of Legends",
            "Overwatch",
            "Steam"
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
        logger.info("🔐 Configuring session storage...")
        setup(self.app, SimpleCookieStorage())
        logger.info("✅ Session storage configured")

    def setup_templates(self):
        aiohttp_jinja2.setup(
            self.app,
            loader=jinja2.DictLoader({
                'login.html': self.get_login_template(),
                'index.html': self.get_index_template(),
            })
        )

    def setup_routes(self):
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
        return web.json_response({
            'status': 'ok',
            'service': 'APEX Calendar API',
            'timestamp': datetime.now().isoformat(),
            'message': '🦈 Swimming smoothly!'
        })

    async def serve_favicon(self, request: web.Request):
        svg_favicon = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
            <text y="80" font-size="80">🦈</text>
        </svg>'''
        return web.Response(body=svg_favicon, content_type='image/svg+xml',
                            headers={'Cache-Control': 'public, max-age=604800'})

    @aiohttp_jinja2.template('login.html')
    async def login_page(self, request: web.Request):
        session = await get_session(request)
        if session.get('authenticated'):
            raise web.HTTPFound('/')
        return {'error': request.query.get('error', '')}

    async def do_login(self, request: web.Request):
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
        session = await get_session(request)
        session.clear()
        raise web.HTTPFound('/login')

    @require_auth
    @aiohttp_jinja2.template('index.html')
    async def index(self, request: web.Request):
        session = await get_session(request)
        username = session['username']
        profile_pic = await self.db.get_profile_picture(username)
        now = datetime.now()
        return {
            'title': 'APEX Calendar System',
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
        session = await get_session(request)
        username = session['username']
        start_date = request.query.get('start_date')
        end_date = request.query.get('end_date')
        events = await self.db.get_events(username, start_date, end_date)
        return web.json_response(events)

    @require_auth
    async def create_event(self, request: web.Request):
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
        session = await get_session(request)
        username = session['username']
        try:
            picture = await self.db.get_profile_picture(username)
            return web.json_response({'picture': picture})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)

    def get_login_template(self) -> str:
        return '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>🦈 APEX - Login</title>
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
        """Mobile-responsive calendar with platform logos, holographic dropdowns, event indicators, profile pic upload, and custom event details modal"""
        return r'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no" />
<title>{{ title }}</title>
<link rel="icon" href="/favicon.ico" type="image/x-icon">
<link href="https://fonts.googleapis.com/css2?family=Bebas+Neue&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
:root {
  --bg-dark: #001428; --bg-mid: #002b44; --bg-light: #00557a;
  --neon: #00e5ff; --text: #e8f4f8;
}
* { box-sizing: border-box; }
html,body { margin: 0; padding: 0; font-family:'Space Mono',monospace; color:var(--text); background: linear-gradient(180deg,var(--bg-dark),var(--bg-mid) 40%, var(--bg-light)); min-height:100vh; overflow-x:hidden; width:100%; }

/* Header */
.top-header { max-width:100%; margin:0 auto; padding:12px 16px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; }
.brand { display:flex; align-items:center; gap:11px; }
.brand .logo { font-size:40px; }
.brand .title h1 { font-family:'Bebas Neue',cursive; color:var(--neon); font-size:2em; margin:0; letter-spacing:6px; text-transform:uppercase; }
.brand .title .subtitle { font-size:11px; margin:5px 0 0 0; color:rgba(232,244,248,0.86); letter-spacing:2px; text-transform:uppercase; }
.user-actions { display:flex; gap:12px; align-items:center; }

/* Layout */
.wrapper { max-width:100%; margin:0 auto; padding:12px; display:flex; gap:12px; }
.sidebar { width:280px; flex-shrink:0; background:rgba(0,26,51,0.96); border-radius:14px; padding:16px; border:1px solid rgba(0,229,255,0.06); box-shadow: 0 8px 20px rgba(0,0,0,0.26); }
.main { flex:1; min-width:0; background:rgba(0,26,51,0.92); border-radius:14px; padding:14px; border:1px solid rgba(0,229,255,0.05); box-shadow: 0 6px 15px rgba(0,0,0,0.16); }

/* Mobile Responsive */
@media (max-width:900px){ 
  .wrapper { flex-direction:column; padding:8px; }
  .sidebar { width:100%; margin-bottom:12px; }
  .main { width:100%; }
  .brand .title h1 { font-size:1.5em; }
}

/* Desktop/Mobile View Toggle */
.desktop-only { display:block; }
.mobile-only { display:none; }

@media (max-width:900px){
  .desktop-only { display:none; }
  .mobile-only { display:block; }
}

/* Mobile List View */
.mobile-list-view {
  display:flex;
  flex-direction:column;
  gap:16px;
}

.mobile-date-group {
  background:rgba(0,26,51,0.6);
  border-radius:10px;
  padding:12px;
  border:1px solid rgba(0,229,255,0.08);
}

.mobile-date-header {
  font-weight:700;
  color:var(--neon);
  font-size:14px;
  margin-bottom:12px;
  padding-bottom:8px;
  border-bottom:1px solid rgba(0,229,255,0.2);
  display:flex;
  align-items:center;
  gap:8px;
}

.mobile-date-header.today {
  color:#00ff88;
  text-shadow: 0 0 10px rgba(0,255,136,0.5);
}

.mobile-event-list {
  display:flex;
  flex-direction:column;
  gap:8px;
}

.mobile-event-item {
  background: linear-gradient(135deg, rgba(0,229,255,0.12), rgba(0,100,140,0.1));
  border-radius:8px;
  padding:10px;
  border:1px solid rgba(0,229,255,0.15);
  cursor:pointer;
  transition:all 0.2s;
}

.mobile-event-item:hover {
  background: linear-gradient(135deg, rgba(0,229,255,0.22), rgba(0,100,140,0.18));
  border-color:rgba(0,229,255,0.4);
  transform:translateX(4px);
}

.mobile-event-title {
  font-weight:700;
  color:var(--text);
  font-size:14px;
  margin-bottom:6px;
  display:flex;
  align-items:center;
  gap:6px;
  flex-wrap:wrap;
}

.mobile-event-platforms {
  display:flex;
  gap:4px;
  margin-right:8px;
}

.mobile-event-description {
  color:rgba(232,244,248,0.7);
  font-size:12px;
  margin-top:4px;
  line-height:1.4;
}

.mobile-event-tags {
  display:flex;
  gap:6px;
  flex-wrap:wrap;
  margin-top:8px;
}

.mobile-tag-badge {
  background:rgba(0,229,255,0.15);
  border:1px solid rgba(0,229,255,0.3);
  padding:3px 8px;
  border-radius:4px;
  font-size:10px;
  color:var(--neon);
}

.mobile-no-events {
  text-align:center;
  padding:40px 20px;
  color:rgba(232,244,248,0.5);
  font-style:italic;
}

/* Mini Calendar in Sidebar */
.mini { background:rgba(10,20,41,0.97); border-radius:10px; padding:12px; border:1px solid rgba(0,229,255,0.08); }
.month-nav { display:flex; justify-content:space-between; align-items:center; margin-bottom:10px; gap:8px; }
.month-name { font-weight:700; color:var(--neon); font-size:16px; text-align:center; flex:1; white-space:nowrap; }
.weekdays { display:grid; grid-template-columns:repeat(7,1fr); gap:4px; margin-bottom:8px; }
.weekdays .day { text-align:center; font-weight:700; color:rgba(255,255,255,0.7); font-size:11px; }
#miniGrid { display:grid; grid-template-columns:repeat(7,1fr); gap:3px; }
.mini-day { 
  text-align:center; font-size:12px; padding:8px 4px; border-radius:50%; 
  position:relative; background:transparent; transition:all 0.2s; cursor:pointer;
  min-height:36px; display:flex; align-items:center; justify-content:center;
}
.mini-day:hover { background:rgba(0,229,255,0.1); }
.mini-day.today { 
  background: linear-gradient(135deg, rgba(0,229,255,0.25), rgba(0,229,255,0.15)); 
  border:2px solid var(--neon); font-weight:700;
  box-shadow: 0 0 20px rgba(0,229,255,0.5);
}
.mini-day.has-events { 
  border:2px solid rgba(0,229,255,0.6);
  box-shadow: 0 0 12px rgba(0,229,255,0.4);
}
.mini-day.today.has-events {
  border:3px solid var(--neon);
  box-shadow: 0 0 25px rgba(0,229,255,0.7);
}

/* Main Calendar View */
.calendar-view { margin-top:16px; }
.calendar-grid { 
  display: grid; 
  grid-template-columns: repeat(7, 1fr); 
  gap: 8px;
  width: 100%;
}
@media (max-width:600px){ 
  .calendar-grid { gap: 4px; }
}
.cal-header { 
  text-align:center; font-weight:700; color:var(--neon); 
  padding:8px; font-size:13px; background:rgba(0,229,255,0.05); 
  border-radius:6px; letter-spacing:1px;
}
.cal-day { 
  min-height:90px; 
  max-height:90px;
  background:rgba(0,26,51,0.6); border-radius:8px; 
  padding:8px 6px; position:relative; border:1px solid rgba(0,229,255,0.08);
  transition:all 0.2s; cursor:pointer;
  overflow-y:auto;
  overflow-x:hidden;
}
@media (max-width:600px){ 
  .cal-day { min-height:70px; max-height:70px; padding:6px 4px; }
}
.cal-day:hover { 
  border-color:rgba(0,229,255,0.3); 
  box-shadow: 0 4px 12px rgba(0,229,255,0.15);
}
.cal-day.today-highlight { 
  background: linear-gradient(135deg, rgba(0,229,255,0.18), rgba(0,100,140,0.12));
  border:2px solid var(--neon);
  box-shadow: 0 0 25px rgba(0,229,255,0.5);
}
.cal-day.has-events-day {
  border:2px solid rgba(0,229,255,0.45);
  box-shadow: 0 0 15px rgba(0,229,255,0.3);
}
.cal-day.today-highlight.has-events-day {
  border:3px solid var(--neon);
  box-shadow: 0 0 30px rgba(0,229,255,0.6);
}
.date-num { 
  font-weight:700; color:var(--neon); font-size:14px; 
  margin-bottom:6px;
}
@media (max-width:600px){ 
  .date-num { font-size:12px; }
}
.event-item { 
  background: linear-gradient(135deg, rgba(0,229,255,0.12), rgba(0,100,140,0.1)); 
  border-radius:6px; padding:4px 6px; font-size:11px; 
  color:var(--text); margin-bottom:4px; cursor:pointer; 
  border:1px solid rgba(0,229,255,0.15); 
  display:flex; align-items:center; gap:6px;
  transition:all 0.2s;
  overflow:visible;
  white-space:normal;
  word-wrap:break-word;
  line-height:1.3;
  min-height:22px;
}
.event-item:hover {
  background: linear-gradient(135deg, rgba(0,229,255,0.22), rgba(0,100,140,0.18));
  border-color:rgba(0,229,255,0.4);
  transform:translateY(-1px);
  z-index:10;
}
@media (max-width:600px){ 
  .event-item { font-size:10px; padding:3px 4px; min-height:20px; }
}
.platform-icon { width:16px; height:16px; flex-shrink:0; }
@media (max-width:600px){ 
  .platform-icon { width:14px; height:14px; }
}

/* Buttons */
.btn { 
  background:rgba(0,229,255,0.15); color:var(--neon); 
  border:1px solid var(--neon); padding:8px 14px; border-radius:6px; 
  cursor:pointer; font-family:'Space Mono',monospace; font-size:13px; 
  transition:all 0.2s; text-decoration:none; display:inline-block;
  white-space:nowrap;
}
.btn:hover { background:rgba(0,229,255,0.3); box-shadow: 0 0 15px rgba(0,229,255,0.4); }
.btn-small { padding:6px 10px; font-size:11px; }
.btn-danger {
  background:rgba(255,71,87,0.15);
  border-color:#ff4757;
  color:#ff4757;
}
.btn-danger:hover {
  background:rgba(255,71,87,0.3);
  box-shadow: 0 0 15px rgba(255,71,87,0.4);
}

/* Month Title */
.month-title { 
  font-family:'Bebas Neue',cursive; color:var(--neon); 
  font-size:2em; letter-spacing:4px; 
}
@media (max-width:600px){ 
  .month-title { font-size:1.5em; }
}

/* Modal */
.modal { 
  display:none; position:fixed; top:0; left:0; width:100%; height:100%; 
  background:rgba(0,0,0,0.85); z-index:1000; 
  justify-content:center; align-items:center; padding:20px;
  backdrop-filter:blur(10px);
}
.modal.active { display:flex; }
.modal-content { 
  background:rgba(0,26,51,0.98); border:2px solid rgba(0,229,255,0.3); 
  border-radius:12px; padding:24px; max-width:500px; width:100%; 
  box-shadow: 0 0 40px rgba(0,229,255,0.3);
  max-height:90vh; overflow-y:auto;
}
.modal-header { 
  font-family:'Bebas Neue',cursive; color:var(--neon); 
  font-size:1.8em; margin-bottom:20px; letter-spacing:3px;
}
.form-group { margin-bottom:16px; }
.form-label { 
  display:block; margin-bottom:6px; color:var(--text); 
  font-weight:700; font-size:11px; letter-spacing:1px; text-transform:uppercase;
}
.form-input, .form-textarea { 
  width:100%; padding:10px; background:rgba(0,61,92,0.4); 
  border:1px solid rgba(0,229,255,0.2); border-radius:6px; 
  font-family:'Space Mono',monospace; color:var(--text); 
  font-size:13px; transition:all 0.2s;
}
.form-input:focus, .form-textarea:focus { 
  outline:none; border-color:var(--neon); 
  box-shadow: 0 0 15px rgba(0,229,255,0.3);
}
.form-textarea { resize:vertical; min-height:80px; }

/* Custom Holographic Dropdown */
.custom-select { 
  position:relative; 
  background:rgba(0,61,92,0.4); 
  border:1px solid rgba(0,229,255,0.2); 
  border-radius:6px; cursor:pointer; 
  padding:10px; transition:all 0.2s;
}
.custom-select:hover { 
  border-color:rgba(0,229,255,0.4); 
  box-shadow: 0 0 10px rgba(0,229,255,0.2);
}
.custom-select.active { 
  border-color:var(--neon); 
  box-shadow: 0 0 20px rgba(0,229,255,0.4);
}
.select-selected { 
  display:flex; justify-content:space-between; align-items:center;
  color:var(--text); font-size:13px;
}
.select-selected:after { 
  content:'▼'; color:var(--neon); font-size:10px; 
  margin-left:10px; transition:transform 0.2s;
}
.custom-select.active .select-selected:after { transform:rotate(180deg); }
.select-items { 
  position:absolute; left:0; right:0; top:100%; 
  margin-top:4px; background:rgba(0,26,51,0.98); 
  border:1px solid rgba(0,229,255,0.3); border-radius:6px; 
  z-index:99; max-height:200px; overflow-y:auto;
  box-shadow: 0 8px 25px rgba(0,229,255,0.3);
  backdrop-filter:blur(10px);
  display:none;
}
.select-items.show { display:block; }
.select-item { 
  padding:10px; cursor:pointer; transition:all 0.2s;
  border-bottom:1px solid rgba(0,229,255,0.05);
  display:flex; align-items:center; gap:8px;
  font-size:13px;
}
.select-item:last-child { border-bottom:none; }
.select-item:hover { 
  background:rgba(0,229,255,0.15); 
  color:var(--neon);
}
.select-item.selected { 
  background:rgba(0,229,255,0.2); 
  color:var(--neon); font-weight:700;
}

/* Multi-select with checkboxes */
.multi-select-item { 
  display:flex; align-items:center; gap:10px; 
  padding:8px 10px; cursor:pointer;
  transition:all 0.2s;
}
.multi-select-item:hover { 
  background:rgba(0,229,255,0.1); 
}
.checkbox-custom { 
  width:18px; height:18px; border:2px solid var(--neon); 
  border-radius:4px; position:relative; flex-shrink:0;
  background:rgba(0,61,92,0.4);
  transition:all 0.2s;
}
.checkbox-custom.checked { 
  background:var(--neon); 
  box-shadow: 0 0 10px rgba(0,229,255,0.5);
}
.checkbox-custom.checked:after { 
  content:'✓'; position:absolute; top:50%; left:50%; 
  transform:translate(-50%,-50%); color:#001428; 
  font-weight:700; font-size:14px;
}

.modal-actions { 
  display:flex; gap:10px; margin-top:20px; justify-content:flex-end; flex-wrap:wrap;
}

/* Quick Actions */
.quick-actions { margin-top:16px; }
.quick-actions-title { 
  font-weight:700; margin-bottom:10px; font-size:13px; 
  letter-spacing:1px; text-transform:uppercase;
}

/* Event Details Modal */
.event-detail-row {
  margin-bottom:16px;
  padding-bottom:16px;
  border-bottom:1px solid rgba(0,229,255,0.1);
}
.event-detail-row:last-of-type {
  border-bottom:none;
}
.event-detail-label {
  color:var(--neon);
  font-size:11px;
  font-weight:700;
  letter-spacing:1px;
  text-transform:uppercase;
  margin-bottom:8px;
}
.event-detail-value {
  color:var(--text);
  font-size:14px;
  line-height:1.6;
}
.event-detail-tags, .event-detail-platforms {
  display:flex;
  gap:8px;
  flex-wrap:wrap;
  margin-top:8px;
}
.tag-badge, .platform-badge {
  background:rgba(0,229,255,0.15);
  border:1px solid rgba(0,229,255,0.3);
  padding:6px 12px;
  border-radius:6px;
  font-size:12px;
  display:flex;
  align-items:center;
  gap:6px;
}

/* Profile Picture Upload */
#cropperContainer {
  max-width:400px;
  margin:20px auto;
  position:relative;
}
#cropperCanvas {
  max-width:100%;
  border:2px solid rgba(0,229,255,0.3);
  border-radius:8px;
  cursor:crosshair;
}
.crop-preview {
  margin-top:20px;
  text-align:center;
}
.crop-preview canvas {
  border:2px solid var(--neon);
  border-radius:50%;
  box-shadow: 0 0 20px rgba(0,229,255,0.4);
}

/* Scrollbar */
::-webkit-scrollbar { width:8px; }
::-webkit-scrollbar-track { background:rgba(0,26,51,0.5); }
::-webkit-scrollbar-thumb { 
  background:rgba(0,229,255,0.3); border-radius:4px; 
}
::-webkit-scrollbar-thumb:hover { background:rgba(0,229,255,0.5); }
</style>
</head>
<body>
<div class="top-header">
  <div class="brand">
    <div class="logo">🦈</div>
    <div class="title">
      <h1>APEX</h1>
      <div class="subtitle">Calendar System</div>
    </div>
  </div>
  <div class="user-actions">
    <div style="display:flex;align-items:center;gap:12px;">
      <div id="profilePicContainer" style="width:40px;height:40px;border-radius:50%;overflow:hidden;border:2px solid var(--neon);background:rgba(0,61,92,0.4);cursor:pointer;" onclick="openProfilePicModal()">
        {% if profile_picture %}
        <img id="profilePicDisplay" src="{{ profile_picture }}" style="width:100%;height:100%;object-fit:cover;">
        {% else %}
        <div id="profilePicDisplay" style="width:100%;height:100%;display:flex;align-items:center;justify-content:center;font-size:20px;">👤</div>
        {% endif %}
      </div>
      <div style="font-weight:700;color:var(--neon)">{{ username }}</div>
    </div>
    <a href="/logout" class="btn btn-small">Logout</a>
  </div>
</div>

<div class="wrapper">
  <div class="sidebar">
    <div class="mini">
      <div class="month-nav">
        <button class="btn btn-small" onclick="prevMonth()">◀</button>
        <div class="month-name" id="sidebarMonthTitle">Loading...</div>
        <button class="btn btn-small" onclick="nextMonth()">▶</button>
      </div>
      <div class="weekdays">
        <div class="day">M</div><div class="day">T</div><div class="day">W</div>
        <div class="day">T</div><div class="day">F</div><div class="day">S</div><div class="day">S</div>
      </div>
      <div id="miniGrid"></div>
    </div>
    <div class="quick-actions">
      <div class="quick-actions-title">Quick Actions</div>
      <button class="btn" style="width:100%;" onclick="openEventModal()">+ New Event</button>
    </div>
  </div>
  
  <div class="main">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;flex-wrap:wrap;gap:12px;">
      <div class="month-title" id="mainMonthTitle">Loading...</div>
    </div>
    
    <!-- Desktop Calendar View -->
    <div class="calendar-view desktop-only">
      <div class="calendar-grid" id="calendarGrid" style="margin-top:8px;"></div>
    </div>
    
    <!-- Mobile List View -->
    <div class="mobile-list-view mobile-only" id="mobileListView">
      <!-- Events will be rendered here on mobile -->
    </div>
  </div>
</div>

<!-- Event Modal -->
<div class="modal" id="eventModal">
  <div class="modal-content">
    <div class="modal-header">Create Event</div>
    <form id="eventForm">
      <div class="form-group">
        <label class="form-label">Event Title</label>
        <input type="text" class="form-input" id="eventTitle" required placeholder="Enter event title">
      </div>
      <div class="form-group">
        <label class="form-label">Description</label>
        <textarea class="form-textarea" id="eventDescription" placeholder="Event details..."></textarea>
      </div>
      <div class="form-group">
        <label class="form-label">Date</label>
        <input type="date" class="form-input" id="eventDate" required>
      </div>
      <div class="form-group">
        <label class="form-label">Tags</label>
        <div class="custom-select" id="tagsSelect">
          <div class="select-selected" onclick="toggleDropdown('tagsSelect')">
            <span id="tagsDisplay">Select tags...</span>
          </div>
          <div class="select-items" id="tagsItems"></div>
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Platforms</label>
        <div class="custom-select" id="platformsSelect">
          <div class="select-selected" onclick="toggleDropdown('platformsSelect')">
            <span id="platformsDisplay">Select platforms...</span>
          </div>
          <div class="select-items" id="platformsItems"></div>
        </div>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn" onclick="closeEventModal()">Cancel</button>
        <button type="submit" class="btn">Create Event</button>
      </div>
    </form>
  </div>
</div>

<!-- Event Details Modal -->
<div class="modal" id="eventDetailsModal">
  <div class="modal-content">
    <div class="modal-header" id="detailsEventTitle">Event Details</div>
    
    <div class="form-group">
      <label class="form-label">Event Title</label>
      <input type="text" class="form-input" id="editEventTitle" placeholder="Event title">
    </div>
    
    <div class="form-group">
      <label class="form-label">Description</label>
      <textarea class="form-textarea" id="editEventDescription" placeholder="Event description..."></textarea>
    </div>
    
    <div class="form-group">
      <label class="form-label">Date</label>
      <input type="date" class="form-input" id="editEventDate">
    </div>
    
    <div class="form-group">
      <label class="form-label">Tags</label>
      <div class="custom-select" id="editTagsSelect">
        <div class="select-selected" onclick="toggleDropdown('editTagsSelect')">
          <span id="editTagsDisplay">Select tags...</span>
        </div>
        <div class="select-items" id="editTagsItems"></div>
      </div>
    </div>
    
    <div class="form-group">
      <label class="form-label">Platforms</label>
      <div class="custom-select" id="editPlatformsSelect">
        <div class="select-selected" onclick="toggleDropdown('editPlatformsSelect')">
          <span id="editPlatformsDisplay">Select platforms...</span>
        </div>
        <div class="select-items" id="editPlatformsItems"></div>
      </div>
    </div>
    
    <div class="modal-actions">
      <button type="button" class="btn" onclick="closeEventDetailsModal()">Cancel</button>
      <button type="button" class="btn btn-danger" onclick="deleteCurrentEvent()">Delete Event</button>
      <button type="button" class="btn" onclick="saveEventChanges()">Save Changes</button>
    </div>
  </div>
</div>

<!-- Profile Picture Upload Modal -->
<div class="modal" id="profilePicModal">
  <div class="modal-content">
    <div class="modal-header">Upload Profile Picture</div>
    <div class="form-group">
      <label class="form-label">Choose Image</label>
      <input type="file" class="form-input" id="profilePicInput" accept="image/*" onchange="loadImageForCrop(event)">
    </div>
    <div id="cropperContainer" style="display:none;">
      <canvas id="cropperCanvas"></canvas>
      <div class="crop-preview">
        <div class="event-detail-label">Preview</div>
        <canvas id="previewCanvas" width="150" height="150"></canvas>
      </div>
    </div>
    <div class="modal-actions">
      <button type="button" class="btn" onclick="closeProfilePicModal()">Cancel</button>
      <button type="button" class="btn" id="uploadPicBtn" onclick="uploadProfilePic()" style="display:none;">Upload</button>
    </div>
  </div>
</div>

<script>
// Platform logo URLs
const platformLogos = {
  'GitHub': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/github.svg',
  'Roblox': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/roblox.svg',
  'Discord': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/discord.svg',
  'Teams': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/microsoftteams.svg',
  'YouTube': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/youtube.svg',
  'Twitch': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/twitch.svg',
  'UCAS': 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"%3E%3Ctext y="18" font-size="18"%3E🎓%3C/text%3E%3C/svg%3E',
  'Valorant': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/riotgames.svg',
  'League of Legends': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/leagueoflegends.svg',
  'Overwatch': 'data:image/svg+xml,%3Csvg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"%3E%3Ctext y="18" font-size="18"%3E⚡%3C/text%3E%3C/svg%3E',
  'Steam': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/steam.svg'
};

const monthNames = ["January","February","March","April","May","June","July","August","September","October","November","December"];
let currentYear = {{ cur_year }};
let currentMonth = {{ cur_month }};
let eventsData = [];
const tags = {{ tags|tojson }};
const platforms = {{ platforms|tojson }};
let selectedTags = [];
let selectedPlatforms = [];
let selectedEditTags = [];
let selectedEditPlatforms = [];
let currentEventDetails = null;

// Image cropping variables
let cropImage = null;
let cropCanvas = null;
let cropCtx = null;
let cropStartX = 0;
let cropStartY = 0;
let cropEndX = 0;
let cropEndY = 0;
let isCropping = false;

// Mobile detection
const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 900;
console.log('Device detected:', isMobile ? 'Mobile' : 'Desktop');

// Notification System
async function requestNotificationPermission() {
  if (!('Notification' in window)) {
    console.log('This browser does not support notifications');
    return;
  }
  
  if (Notification.permission === 'granted') {
    console.log('Notification permission already granted');
    return;
  }
  
  if (Notification.permission !== 'denied') {
    const permission = await Notification.requestPermission();
    if (permission === 'granted') {
      console.log('Notification permission granted');
      new Notification('🦈 APEX Calendar', {
        body: 'Notifications enabled! You\'ll be reminded of upcoming events.',
        icon: '/favicon.ico',
        badge: '/favicon.ico'
      });
    }
  }
}

function scheduleNotifications() {
  if (!('Notification' in window) || Notification.permission !== 'granted') {
    return;
  }
  
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const tomorrow = new Date(today);
  tomorrow.setDate(tomorrow.getDate() + 1);
  
  eventsData.forEach(event => {
    const eventDate = new Date(event.event_date + 'T00:00:00');
    const eventDateOnly = new Date(eventDate.getFullYear(), eventDate.getMonth(), eventDate.getDate());
    
    // Check if event is today
    if (eventDateOnly.getTime() === today.getTime()) {
      const notifyTime = new Date(now);
      notifyTime.setHours(8, 0, 0, 0); // 8 AM notification
      
      if (now < notifyTime) {
        const timeUntil = notifyTime.getTime() - now.getTime();
        setTimeout(() => {
          showEventNotification(event, 'Today');
        }, timeUntil);
      }
    }
    
    // Check if event is tomorrow
    if (eventDateOnly.getTime() === tomorrow.getTime()) {
      const notifyTime = new Date(now);
      notifyTime.setHours(20, 0, 0, 0); // 8 PM notification for tomorrow
      
      if (now < notifyTime) {
        const timeUntil = notifyTime.getTime() - now.getTime();
        setTimeout(() => {
          showEventNotification(event, 'Tomorrow');
        }, timeUntil);
      }
    }
  });
}

function showEventNotification(event, timing) {
  if (Notification.permission !== 'granted') return;
  
  let body = `${timing}: ${event.title}`;
  if (event.description) {
    body += `\n${event.description}`;
  }
  
  const notification = new Notification(`🦈 ${timing}'s Event`, {
    body: body,
    icon: '/favicon.ico',
    badge: '/favicon.ico',
    tag: `event-${event.id}`,
    requireInteraction: false
  });
  
  notification.onclick = function() {
    window.focus();
    showEventDetails(event);
    notification.close();
  };
}

// Initialize
async function init() {
  console.log('=== INIT STARTED ===');
  console.log('Current month:', currentMonth, 'Current year:', currentYear);
  
  const calGrid = document.getElementById('calendarGrid');
  const miniGrid = document.getElementById('miniGrid');
  console.log('calendarGrid exists:', !!calGrid);
  console.log('miniGrid exists:', !!miniGrid);
  
  if (!calGrid || !miniGrid) {
    console.error('CRITICAL: Required DOM elements not found!');
    alert('Error: Calendar elements not found. Please refresh the page.');
    return;
  }
  
  initDropdowns();
  await loadEvents();
  await requestNotificationPermission();
  scheduleNotifications();
  console.log('=== INIT COMPLETED ===');
}

async function fetchEvents() {
  try {
    console.log('Fetching events from /api/events...');
    const r = await fetch('/api/events', {
      credentials: 'same-origin',
      headers: {
        'Accept': 'application/json'
      }
    });
    console.log('Response status:', r.status);
    
    if (r.status === 401 || r.status === 403) {
      console.error('Authentication error - redirecting to login');
      window.location.href = '/login';
      return [];
    }
    
    if (!r.ok) {
      console.error('Response not OK:', r.status, await r.text());
      return [];
    }
    
    const data = await r.json();
    console.log('Events loaded successfully:', data.length, 'events');
    console.log('Sample event:', data[0]);
    return Array.isArray(data) ? data : [];
  } catch (e) {
    console.error('FETCH ERROR:', e);
    console.error('Error stack:', e.stack);
    return [];
  }
}

async function loadEvents() {
  console.log('=== loadEvents called ===');
  try {
    eventsData = await fetchEvents();
    console.log('Events data stored:', eventsData.length, 'events');
    console.log('Calling renderCalendar...');
    renderCalendar();
    console.log('Calling renderMini...');
    renderMini();
    console.log('Calling renderMobileList...');
    renderMobileList();
    scheduleNotifications();
    console.log('=== loadEvents completed successfully ===');
  } catch (e) {
    console.error('ERROR in loadEvents:', e);
    console.error('Error stack:', e.stack);
    
    eventsData = [];
    renderCalendar();
    renderMini();
    renderMobileList();
  }
}

function renderCalendar() {
  console.log('=== renderCalendar START ===');
  console.log('Current month:', currentMonth, 'Year:', currentYear);
  console.log('Events to render:', eventsData.length);
  
  const mainTitle = document.getElementById('mainMonthTitle');
  const sideTitle = document.getElementById('sidebarMonthTitle');
  
  if (!mainTitle || !sideTitle) {
    console.error('CRITICAL: Title elements not found!');
    return;
  }
  
  mainTitle.innerText = monthNames[currentMonth-1] + ' ' + currentYear;
  sideTitle.innerText = monthNames[currentMonth-1];
  
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  console.log('Today:', todayStr);
  
  const first = new Date(currentYear, currentMonth-1, 1);
  const startOffset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(currentYear, currentMonth, 0).getDate();
  
  console.log('Days in month:', daysInMonth, 'Start offset:', startOffset);
  
  const grid = document.getElementById('calendarGrid');
  if (!grid) {
    console.error('CRITICAL: calendarGrid element not found!');
    alert('Error: Calendar grid not found. Please refresh the page.');
    return;
  }
  
  console.log('Clearing calendar grid...');
  grid.innerHTML = '';
  
  for (let i = 0; i < startOffset; i++) {
    const emptyCell = document.createElement('div');
    emptyCell.className = 'cal-day';
    emptyCell.style.opacity = '0.3';
    emptyCell.style.visibility = 'hidden';
    grid.appendChild(emptyCell);
  }
  
  console.log('Adding', daysInMonth, 'day cells...');
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${currentYear}-${String(currentMonth).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const dayEvents = eventsData.filter(e => e.event_date === dateStr);
    
    const dayCell = document.createElement('div');
    dayCell.className = 'cal-day';
    dayCell.dataset.date = dateStr;
    
    if (dateStr === todayStr) {
      dayCell.classList.add('today-highlight');
      console.log('Today cell:', d);
    }
    if (dayEvents.length > 0) {
      dayCell.classList.add('has-events-day');
      console.log('Day', d, 'has', dayEvents.length, 'events');
    }
    
    // Click to add event
    dayCell.onclick = function(e) {
      if (e.target === dayCell || e.target.classList.contains('date-num')) {
        openEventModalForDate(dateStr);
      }
    };
    
    const dateNum = document.createElement('div');
    dateNum.className = 'date-num';
    dateNum.innerText = d;
    dayCell.appendChild(dateNum);
    
    const maxEvents = isMobile ? 2 : 3;
    const displayEvents = dayEvents.slice(0, maxEvents);
    const remainingCount = dayEvents.length - maxEvents;
    
    displayEvents.forEach(ev => {
      const eventItem = document.createElement('div');
      eventItem.className = 'event-item';
      
      // Add all platform icons if available
      if (ev.platforms && ev.platforms.length > 0) {
        const iconsContainer = document.createElement('div');
        iconsContainer.style.display = 'flex';
        iconsContainer.style.gap = '4px';
        iconsContainer.style.alignItems = 'center';
        iconsContainer.style.flexShrink = '0';
        
        ev.platforms.forEach(platform => {
          if (platformLogos[platform]) {
            const icon = document.createElement('img');
            icon.src = platformLogos[platform];
            icon.className = 'platform-icon';
            icon.style.filter = 'invert(1) brightness(2)';
            iconsContainer.appendChild(icon);
          }
        });
        
        if (iconsContainer.children.length > 0) {
          eventItem.appendChild(iconsContainer);
        }
      }
      
      const title = document.createElement('span');
      title.innerText = ev.title;
      title.style.flex = '1';
      eventItem.appendChild(title);
      
      eventItem.onclick = (e) => {
        e.stopPropagation();
        showEventDetails(ev);
      };
      
      dayCell.appendChild(eventItem);
    });
    
    if (remainingCount > 0) {
      const moreItem = document.createElement('div');
      moreItem.className = 'event-item';
      moreItem.style.opacity = '0.7';
      moreItem.style.fontStyle = 'italic';
      moreItem.innerText = `+${remainingCount} more`;
      moreItem.onclick = () => {
        showAllEventsForDate(dateStr, dayEvents);
      };
      dayCell.appendChild(moreItem);
    }
    
    grid.appendChild(dayCell);
  }
  console.log('=== renderCalendar SUCCESS: Added', grid.children.length, 'total cells ===');
}

function renderMini() {
  console.log('renderMini called');
  const mini = document.getElementById('miniGrid');
  if (!mini) {
    console.error('miniGrid element not found!');
    return;
  }
  mini.innerHTML = '';
  
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  
  const first = new Date(currentYear, currentMonth-1, 1);
  const startOffset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(currentYear, currentMonth, 0).getDate();
  
  for (let i = 0; i < startOffset; i++) {
    const e = document.createElement('div');
    e.style.opacity = '0';
    e.style.minHeight = '36px';
    mini.appendChild(e);
  }
  
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${currentYear}-${String(currentMonth).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const hasEvents = eventsData.some(ev => ev.event_date === dateStr);
    
    const cell = document.createElement('div');
    cell.className = 'mini-day';
    if (dateStr === todayStr) cell.classList.add('today');
    if (hasEvents) cell.classList.add('has-events');
    cell.innerText = d;
    
    mini.appendChild(cell);
  }
  console.log('Mini calendar rendered successfully');
}

function renderMobileList() {
  console.log('renderMobileList called');
  
  if (!isMobile) {
    console.log('Not mobile, skipping mobile list render');
    return;
  }
  
  const container = document.getElementById('mobileListView');
  if (!container) {
    console.error('mobileListView element not found!');
    return;
  }
  
  container.innerHTML = '';
  
  // Filter events for current month
  const monthEvents = eventsData.filter(ev => {
    const evDate = new Date(ev.event_date + 'T00:00:00');
    return evDate.getMonth() + 1 === currentMonth && evDate.getFullYear() === currentYear;
  });
  
  if (monthEvents.length === 0) {
    const noEvents = document.createElement('div');
    noEvents.className = 'mobile-no-events';
    noEvents.innerText = '🦈 No events this month';
    container.appendChild(noEvents);
    return;
  }
  
  // Group events by date
  const eventsByDate = {};
  monthEvents.forEach(ev => {
    if (!eventsByDate[ev.event_date]) {
      eventsByDate[ev.event_date] = [];
    }
    eventsByDate[ev.event_date].push(ev);
  });
  
  // Sort dates
  const sortedDates = Object.keys(eventsByDate).sort();
  
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  
  // Render each date group
  sortedDates.forEach(dateStr => {
    const dateGroup = document.createElement('div');
    dateGroup.className = 'mobile-date-group';
    
    // Date header
    const dateHeader = document.createElement('div');
    dateHeader.className = 'mobile-date-header';
    if (dateStr === todayStr) {
      dateHeader.classList.add('today');
    }
    
    const date = new Date(dateStr + 'T00:00:00');
    const formatted = date.toLocaleDateString('en-US', { 
      weekday: 'short', 
      month: 'short', 
      day: 'numeric',
      year: 'numeric'
    });
    
    if (dateStr === todayStr) {
      dateHeader.innerHTML = `<span>📍</span><span>${formatted} (Today)</span>`;
    } else {
      dateHeader.innerText = formatted;
    }
    
    dateGroup.appendChild(dateHeader);
    
    // Event list for this date
    const eventList = document.createElement('div');
    eventList.className = 'mobile-event-list';
    
    eventsByDate[dateStr].forEach(ev => {
      const eventItem = document.createElement('div');
      eventItem.className = 'mobile-event-item';
      
      // Title row with platforms
      const titleRow = document.createElement('div');
      titleRow.className = 'mobile-event-title';
      
      // Platform icons
      if (ev.platforms && ev.platforms.length > 0) {
        const platformsContainer = document.createElement('div');
        platformsContainer.className = 'mobile-event-platforms';
        
        ev.platforms.forEach(platform => {
          if (platformLogos[platform]) {
            const icon = document.createElement('img');
            icon.src = platformLogos[platform];
            icon.style.width = '18px';
            icon.style.height = '18px';
            icon.style.filter = 'invert(1) brightness(2)';
            platformsContainer.appendChild(icon);
          }
        });
        
        titleRow.appendChild(platformsContainer);
      }
      
      // Title text
      const titleText = document.createElement('span');
      titleText.innerText = ev.title;
      titleText.style.flex = '1';
      titleRow.appendChild(titleText);
      
      eventItem.appendChild(titleRow);
      
      // Description
      if (ev.description) {
        const desc = document.createElement('div');
        desc.className = 'mobile-event-description';
        desc.innerText = ev.description;
        eventItem.appendChild(desc);
      }
      
      // Tags
      if (ev.tags && ev.tags.length > 0) {
        const tagsContainer = document.createElement('div');
        tagsContainer.className = 'mobile-event-tags';
        
        ev.tags.forEach(tag => {
          const tagBadge = document.createElement('div');
          tagBadge.className = 'mobile-tag-badge';
          tagBadge.innerText = tag;
          tagsContainer.appendChild(tagBadge);
        });
        
        eventItem.appendChild(tagsContainer);
      }
      
      // Click to show details
      eventItem.onclick = () => showEventDetails(ev);
      
      eventList.appendChild(eventItem);
    });
    
    dateGroup.appendChild(eventList);
    container.appendChild(dateGroup);
  });
  
  console.log('Mobile list rendered successfully');
}

function showEventDetails(ev) {
  currentEventDetails = ev;
  
  // Populate edit fields
  document.getElementById('detailsEventTitle').innerText = ev.title;
  document.getElementById('editEventTitle').value = ev.title;
  document.getElementById('editEventDescription').value = ev.description || '';
  document.getElementById('editEventDate').value = ev.event_date;
  
  // Set up edit tags
  selectedEditTags = ev.tags ? [...ev.tags] : [];
  updateDropdownDisplay('editTagsSelect', 'editTagsDisplay', selectedEditTags);
  updateCheckboxes('editTagsItems', selectedEditTags);
  
  // Set up edit platforms
  selectedEditPlatforms = ev.platforms ? [...ev.platforms] : [];
  updateDropdownDisplay('editPlatformsSelect', 'editPlatformsDisplay', selectedEditPlatforms);
  updateCheckboxes('editPlatformsItems', selectedEditPlatforms);
  
  document.getElementById('eventDetailsModal').classList.add('active');
}

function closeEventDetailsModal() {
  document.getElementById('eventDetailsModal').classList.remove('active');
  currentEventDetails = null;
}

async function saveEventChanges() {
  if (!currentEventDetails) return;
  
  const updatedData = {
    title: document.getElementById('editEventTitle').value,
    description: document.getElementById('editEventDescription').value,
    event_date: document.getElementById('editEventDate').value,
    tags: selectedEditTags,
    platforms: selectedEditPlatforms
  };
  
  try {
    const response = await fetch(`/api/events/${currentEventDetails.id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify(updatedData)
    });
    
    if (response.ok) {
      closeEventDetailsModal();
      await loadEvents();
    } else {
      alert('Error updating event');
    }
  } catch (e) {
    console.error('Error:', e);
    alert('Error updating event');
  }
}

async function deleteCurrentEvent() {
  if (!currentEventDetails) return;
  
  if (!confirm(`Are you sure you want to delete "${currentEventDetails.title}"?`)) {
    return;
  }
  
  try {
    const response = await fetch(`/api/events/${currentEventDetails.id}`, {
      method: 'DELETE',
      credentials: 'same-origin'
    });
    
    if (response.ok) {
      closeEventDetailsModal();
      await loadEvents();
    } else {
      alert('Error deleting event');
    }
  } catch (e) {
    console.error('Error:', e);
    alert('Error deleting event');
  }
}

function showAllEventsForDate(dateStr, events) {
  const formatted = new Date(dateStr + 'T00:00:00').toLocaleDateString('en-US', { 
    weekday: 'long', 
    year: 'numeric', 
    month: 'long', 
    day: 'numeric' 
  });
  
  let message = `All events on ${formatted}:\n\n`;
  events.forEach(ev => {
    message += `• ${ev.title}\n`;
    if (ev.description) {
      message += `  ${ev.description}\n`;
    }
    message += '\n';
  });
  
  alert(message);
}

function openEventModalForDate(dateStr) {
  document.getElementById('eventModal').classList.add('active');
  document.getElementById('eventDate').value = dateStr;
  selectedTags = [];
  selectedPlatforms = [];
  updateDropdownDisplay('tagsSelect', 'tagsDisplay', selectedTags);
  updateDropdownDisplay('platformsSelect', 'platformsDisplay', selectedPlatforms);
  updateCheckboxes('tagsItems', selectedTags);
  updateCheckboxes('platformsItems', selectedPlatforms);
}

function prevMonth() {
  currentMonth--;
  if (currentMonth < 1) {
    currentMonth = 12;
    currentYear--;
  }
  loadEvents();
}

function nextMonth() {
  currentMonth++;
  if (currentMonth > 12) {
    currentMonth = 1;
    currentYear++;
  }
  loadEvents();
}

function openEventModal() {
  document.getElementById('eventModal').classList.add('active');
  document.getElementById('eventDate').value = `${currentYear}-${String(currentMonth).padStart(2,'0')}-01`;
  selectedTags = [];
  selectedPlatforms = [];
  updateDropdownDisplay('tagsSelect', 'tagsDisplay', selectedTags);
  updateDropdownDisplay('platformsSelect', 'platformsDisplay', selectedPlatforms);
  updateCheckboxes('tagsItems', selectedTags);
  updateCheckboxes('platformsItems', selectedPlatforms);
}

function closeEventModal() {
  document.getElementById('eventModal').classList.remove('active');
  document.getElementById('eventForm').reset();
}

function initDropdowns() {
  // Tags dropdown for create modal
  const tagsItems = document.getElementById('tagsItems');
  tags.forEach(tag => {
    const item = document.createElement('div');
    item.className = 'multi-select-item';
    item.innerHTML = `
      <div class="checkbox-custom" data-value="${tag}"></div>
      <span>${tag}</span>
    `;
    item.onclick = () => toggleMultiSelect(tag, 'tags');
    tagsItems.appendChild(item);
  });
  
  // Platforms dropdown for create modal
  const platformsItems = document.getElementById('platformsItems');
  platforms.forEach(platform => {
    const item = document.createElement('div');
    item.className = 'multi-select-item';
    
    const checkbox = document.createElement('div');
    checkbox.className = 'checkbox-custom';
    checkbox.setAttribute('data-value', platform);
    
    const icon = document.createElement('img');
    icon.src = platformLogos[platform] || '';
    icon.style.width = '20px';
    icon.style.height = '20px';
    icon.style.filter = 'invert(1) brightness(2)';
    icon.onerror = function() { this.style.display = 'none'; };
    
    const label = document.createElement('span');
    label.innerText = platform;
    
    item.appendChild(checkbox);
    item.appendChild(icon);
    item.appendChild(label);
    item.onclick = () => toggleMultiSelect(platform, 'platforms');
    
    platformsItems.appendChild(item);
  });
  
  // Tags dropdown for edit modal
  const editTagsItems = document.getElementById('editTagsItems');
  tags.forEach(tag => {
    const item = document.createElement('div');
    item.className = 'multi-select-item';
    item.innerHTML = `
      <div class="checkbox-custom" data-value="${tag}"></div>
      <span>${tag}</span>
    `;
    item.onclick = () => toggleMultiSelect(tag, 'editTags');
    editTagsItems.appendChild(item);
  });
  
  // Platforms dropdown for edit modal
  const editPlatformsItems = document.getElementById('editPlatformsItems');
  platforms.forEach(platform => {
    const item = document.createElement('div');
    item.className = 'multi-select-item';
    
    const checkbox = document.createElement('div');
    checkbox.className = 'checkbox-custom';
    checkbox.setAttribute('data-value', platform);
    
    const icon = document.createElement('img');
    icon.src = platformLogos[platform] || '';
    icon.style.width = '20px';
    icon.style.height = '20px';
    icon.style.filter = 'invert(1) brightness(2)';
    icon.onerror = function() { this.style.display = 'none'; };
    
    const label = document.createElement('span');
    label.innerText = platform;
    
    item.appendChild(checkbox);
    item.appendChild(icon);
    item.appendChild(label);
    item.onclick = () => toggleMultiSelect(platform, 'editPlatforms');
    
    editPlatformsItems.appendChild(item);
  });
}

function toggleDropdown(selectId) {
  const select = document.getElementById(selectId);
  const items = select.querySelector('.select-items');
  
  document.querySelectorAll('.custom-select').forEach(s => {
    if (s.id !== selectId) {
      s.classList.remove('active');
      s.querySelector('.select-items').classList.remove('show');
    }
  });
  
  select.classList.toggle('active');
  items.classList.toggle('show');
}

function toggleMultiSelect(value, type) {
  let array, displayId, itemsId, selectId;
  
  if (type === 'tags') {
    array = selectedTags;
    displayId = 'tagsDisplay';
    itemsId = 'tagsItems';
    selectId = 'tagsSelect';
  } else if (type === 'platforms') {
    array = selectedPlatforms;
    displayId = 'platformsDisplay';
    itemsId = 'platformsItems';
    selectId = 'platformsSelect';
  } else if (type === 'editTags') {
    array = selectedEditTags;
    displayId = 'editTagsDisplay';
    itemsId = 'editTagsItems';
    selectId = 'editTagsSelect';
  } else if (type === 'editPlatforms') {
    array = selectedEditPlatforms;
    displayId = 'editPlatformsDisplay';
    itemsId = 'editPlatformsItems';
    selectId = 'editPlatformsSelect';
  }
  
  const idx = array.indexOf(value);
  
  if (idx > -1) {
    array.splice(idx, 1);
  } else {
    array.push(value);
  }
  
  updateDropdownDisplay(selectId, displayId, array);
  updateCheckboxes(itemsId, array);
}

function updateDropdownDisplay(selectId, displayId, array) {
  const display = document.getElementById(displayId);
  if (array.length === 0) {
    display.innerText = selectId.includes('tags') ? 'Select tags...' : 'Select platforms...';
  } else {
    display.innerText = array.join(', ');
  }
}

function updateCheckboxes(itemsId, array) {
  const items = document.getElementById(itemsId);
  items.querySelectorAll('.checkbox-custom').forEach(cb => {
    const val = cb.getAttribute('data-value');
    if (array.includes(val)) {
      cb.classList.add('checked');
    } else {
      cb.classList.remove('checked');
    }
  });
}

document.addEventListener('click', (e) => {
  if (!e.target.closest('.custom-select')) {
    document.querySelectorAll('.custom-select').forEach(s => {
      s.classList.remove('active');
      s.querySelector('.select-items').classList.remove('show');
    });
  }
});

document.getElementById('eventModal').addEventListener('click', (e) => {
  if (e.target.id === 'eventModal') {
    closeEventModal();
  }
});

document.getElementById('eventDetailsModal').addEventListener('click', (e) => {
  if (e.target.id === 'eventDetailsModal') {
    closeEventDetailsModal();
  }
});

document.getElementById('eventForm').addEventListener('submit', async (e) => {
  e.preventDefault();
  
  const eventData = {
    title: document.getElementById('eventTitle').value,
    description: document.getElementById('eventDescription').value,
    event_date: document.getElementById('eventDate').value,
    tags: selectedTags,
    platforms: selectedPlatforms
  };
  
  try {
    const response = await fetch('/api/events', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(eventData)
    });
    
    if (response.ok) {
      closeEventModal();
      await loadEvents();
    } else {
      alert('Error creating event');
    }
  } catch (e) {
    console.error('Error:', e);
    alert('Error creating event');
  }
});

// Profile Picture Functions
function openProfilePicModal() {
  document.getElementById('profilePicModal').classList.add('active');
}

function closeProfilePicModal() {
  document.getElementById('profilePicModal').classList.remove('active');
  document.getElementById('profilePicInput').value = '';
  document.getElementById('cropperContainer').style.display = 'none';
  document.getElementById('uploadPicBtn').style.display = 'none';
  cropImage = null;
}

function loadImageForCrop(event) {
  const file = event.target.files[0];
  if (!file) return;
  
  const reader = new FileReader();
  reader.onload = function(e) {
    const img = new Image();
    img.onload = function() {
      cropImage = img;
      initCropper();
      document.getElementById('cropperContainer').style.display = 'block';
      document.getElementById('uploadPicBtn').style.display = 'inline-block';
    };
    img.src = e.target.result;
  };
  reader.readAsDataURL(file);
}

function initCropper() {
  cropCanvas = document.getElementById('cropperCanvas');
  cropCtx = cropCanvas.getContext('2d');
  
  const maxWidth = 400;
  const scale = Math.min(1, maxWidth / cropImage.width);
  cropCanvas.width = cropImage.width * scale;
  cropCanvas.height = cropImage.height * scale;
  
  cropCtx.drawImage(cropImage, 0, 0, cropCanvas.width, cropCanvas.height);
  
  const size = Math.min(cropCanvas.width, cropCanvas.height);
  cropStartX = (cropCanvas.width - size) / 2;
  cropStartY = (cropCanvas.height - size) / 2;
  cropEndX = cropStartX + size;
  cropEndY = cropStartY + size;
  
  drawCropOverlay();
  updatePreview();
  
  cropCanvas.onmousedown = startCrop;
  cropCanvas.onmousemove = updateCrop;
  cropCanvas.onmouseup = endCrop;
  cropCanvas.ontouchstart = handleTouchStart;
  cropCanvas.ontouchmove = handleTouchMove;
  cropCanvas.ontouchend = endCrop;
}

function startCrop(e) {
  isCropping = true;
  const rect = cropCanvas.getBoundingClientRect();
  cropStartX = e.clientX - rect.left;
  cropStartY = e.clientY - rect.top;
}

function updateCrop(e) {
  if (!isCropping) return;
  const rect = cropCanvas.getBoundingClientRect();
  cropEndX = e.clientX - rect.left;
  cropEndY = e.clientY - rect.top;
  
  const size = Math.min(Math.abs(cropEndX - cropStartX), Math.abs(cropEndY - cropStartY));
  cropEndX = cropStartX + (cropEndX > cropStartX ? size : -size);
  cropEndY = cropStartY + (cropEndY > cropStartY ? size : -size);
  
  drawCropOverlay();
  updatePreview();
}

function endCrop() {
  isCropping = false;
}

function handleTouchStart(e) {
  e.preventDefault();
  const touch = e.touches[0];
  const rect = cropCanvas.getBoundingClientRect();
  cropStartX = touch.clientX - rect.left;
  cropStartY = touch.clientY - rect.top;
  isCropping = true;
}

function handleTouchMove(e) {
  if (!isCropping) return;
  e.preventDefault();
  const touch = e.touches[0];
  const rect = cropCanvas.getBoundingClientRect();
  cropEndX = touch.clientX - rect.left;
  cropEndY = touch.clientY - rect.top;
  
  const size = Math.min(Math.abs(cropEndX - cropStartX), Math.abs(cropEndY - cropStartY));
  cropEndX = cropStartX + (cropEndX > cropStartX ? size : -size);
  cropEndY = cropStartY + (cropEndY > cropStartY ? size : -size);
  
  drawCropOverlay();
  updatePreview();
}

function drawCropOverlay() {
  cropCtx.clearRect(0, 0, cropCanvas.width, cropCanvas.height);
  cropCtx.drawImage(cropImage, 0, 0, cropCanvas.width, cropCanvas.height);
  
  cropCtx.fillStyle = 'rgba(0, 0, 0, 0.5)';
  cropCtx.fillRect(0, 0, cropCanvas.width, cropCanvas.height);
  
  const x = Math.min(cropStartX, cropEndX);
  const y = Math.min(cropStartY, cropEndY);
  const w = Math.abs(cropEndX - cropStartX);
  const h = Math.abs(cropEndY - cropStartY);
  
  cropCtx.clearRect(x, y, w, h);
  cropCtx.drawImage(cropImage, 0, 0, cropCanvas.width, cropCanvas.height);
  cropCtx.strokeStyle = '#00e5ff';
  cropCtx.lineWidth = 2;
  cropCtx.strokeRect(x, y, w, h);
}

function updatePreview() {
  const previewCanvas = document.getElementById('previewCanvas');
  const previewCtx = previewCanvas.getContext('2d');
  
  const x = Math.min(cropStartX, cropEndX);
  const y = Math.min(cropStartY, cropEndY);
  const w = Math.abs(cropEndX - cropStartX);
  const h = Math.abs(cropEndY - cropStartY);
  
  const scale = cropCanvas.width / cropImage.width;
  const srcX = x / scale;
  const srcY = y / scale;
  const srcW = w / scale;
  const srcH = h / scale;
  
  previewCtx.clearRect(0, 0, 150, 150);
  previewCtx.drawImage(cropImage, srcX, srcY, srcW, srcH, 0, 0, 150, 150);
}

async function uploadProfilePic() {
  const previewCanvas = document.getElementById('previewCanvas');
  const dataURL = previewCanvas.toDataURL('image/jpeg', 0.9);
  
  try {
    const response = await fetch('/api/profile-picture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ picture: dataURL })
    });
    
    if (response.ok) {
      const profilePicDisplay = document.getElementById('profilePicDisplay');
      if (profilePicDisplay.tagName === 'IMG') {
        profilePicDisplay.src = dataURL;
      } else {
        const img = document.createElement('img');
        img.src = dataURL;
        img.style.width = '100%';
        img.style.height = '100%';
        img.style.objectFit = 'cover';
        document.getElementById('profilePicContainer').innerHTML = '';
        document.getElementById('profilePicContainer').appendChild(img);
      }
      
      closeProfilePicModal();
    } else {
      alert('Error uploading profile picture');
    }
  } catch (e) {
    console.error('Error:', e);
    alert('Error uploading profile picture');
  }
}

document.getElementById('profilePicModal').addEventListener('click', (e) => {
  if (e.target.id === 'profilePicModal') {
    closeProfilePicModal();
  }
});

// Initialize on load
console.log('=== SCRIPT LOADING ===');
console.log('Document ready state:', document.readyState);

window.addEventListener('load', function() {
  console.log('=== WINDOW LOAD EVENT FIRED ===');
  console.log('Starting initialization in 100ms...');
  
  setTimeout(function() {
    console.log('Calling init()...');
    init().catch(err => {
      console.error('INIT FAILED:', err);
      console.error('Stack:', err.stack);
      alert('Error loading calendar. Check console for details.');
    });
  }, 100);
});

if (document.readyState === 'complete') {
  console.log('Document already complete, initializing now...');
  setTimeout(function() {
    init().catch(err => {
      console.error('INIT FAILED:', err);
      alert('Error loading calendar. Check console for details.');
    });
  }, 100);
}

console.log('=== SCRIPT LOADED ===');
</script>
</body>
</html>'''



# Main entry
async def init_app():
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
