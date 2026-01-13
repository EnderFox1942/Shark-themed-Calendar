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
            "Work"
        ]
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
        """Mobile-responsive calendar with platform logos, holographic dropdowns, and event indicators"""
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
  min-height:90px; background:rgba(0,26,51,0.6); border-radius:8px; 
  padding:8px 6px; position:relative; border:1px solid rgba(0,229,255,0.08);
  transition:all 0.2s;
}
@media (max-width:600px){ 
  .cal-day { min-height:70px; padding:6px 4px; }
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
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.event-item:hover {
  background: linear-gradient(135deg, rgba(0,229,255,0.22), rgba(0,100,140,0.18));
  border-color:rgba(0,229,255,0.4);
  transform:translateY(-1px);
}
@media (max-width:600px){ 
  .event-item { font-size:10px; padding:3px 4px; }
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
    <div style="font-weight:700;color:var(--neon)">{{ username }}</div>
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
    <div class="calendar-view">
      <div class="calendar-grid">
        <div class="cal-header">MON</div>
        <div class="cal-header">TUE</div>
        <div class="cal-header">WED</div>
        <div class="cal-header">THU</div>
        <div class="cal-header">FRI</div>
        <div class="cal-header">SAT</div>
        <div class="cal-header">SUN</div>
      </div>
      <div class="calendar-grid" id="calendarGrid" style="margin-top:8px;"></div>
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

<script>
// Platform logo URLs
const platformLogos = {
  'GitHub': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/github.svg',
  'Roblox': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/roblox.svg',
  'Discord': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/discord.svg',
  'Teams': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/microsoftteams.svg',
  'YouTube': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/youtube.svg',
  'Twitch': 'https://cdn.jsdelivr.net/npm/simple-icons@v9/icons/twitch.svg'
};

const monthNames = ["January","February","March","April","May","June","July","August","September","October","November","December"];
let currentYear = {{ cur_year }};
let currentMonth = {{ cur_month }};
let eventsData = [];
const tags = {{ tags|tojson }};
const platforms = {{ platforms|tojson }};
let selectedTags = [];
let selectedPlatforms = [];

// Mobile detection
const isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent) || window.innerWidth < 900;
console.log('Device detected:', isMobile ? 'Mobile' : 'Desktop');

// Initialize
async function init() {
  await loadEvents();
  initDropdowns();
}

async function fetchEvents() {
  try {
    const r = await fetch('/api/events');
    if (!r.ok) return [];
    return await r.json();
  } catch (e) {
    console.error('Error fetching events:', e);
    return [];
  }
}

async function loadEvents() {
  eventsData = await fetchEvents();
  renderCalendar();
  renderMini();
}

function renderCalendar() {
  document.getElementById('mainMonthTitle').innerText = monthNames[currentMonth-1] + ' ' + currentYear;
  document.getElementById('sidebarMonthTitle').innerText = monthNames[currentMonth-1];
  
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  
  const first = new Date(currentYear, currentMonth-1, 1);
  const startOffset = (first.getDay() + 6) % 7; // Monday = 0
  const daysInMonth = new Date(currentYear, currentMonth, 0).getDate();
  
  const grid = document.getElementById('calendarGrid');
  grid.innerHTML = '';
  
  // Add empty cells for offset
  for (let i = 0; i < startOffset; i++) {
    const emptyCell = document.createElement('div');
    emptyCell.className = 'cal-day';
    emptyCell.style.opacity = '0.3';
    emptyCell.style.visibility = 'hidden';
    grid.appendChild(emptyCell);
  }
  
  // Add days
  for (let d = 1; d <= daysInMonth; d++) {
    const dateStr = `${currentYear}-${String(currentMonth).padStart(2,'0')}-${String(d).padStart(2,'0')}`;
    const dayEvents = eventsData.filter(e => e.event_date === dateStr);
    
    const dayCell = document.createElement('div');
    dayCell.className = 'cal-day';
    
    if (dateStr === todayStr) {
      dayCell.classList.add('today-highlight');
    }
    if (dayEvents.length > 0) {
      dayCell.classList.add('has-events-day');
    }
    
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
      
      // Add platform icon if available
      if (ev.platforms && ev.platforms.length > 0 && platformLogos[ev.platforms[0]]) {
        const icon = document.createElement('img');
        icon.src = platformLogos[ev.platforms[0]];
        icon.className = 'platform-icon';
        icon.style.filter = 'invert(1) brightness(2)';
        eventItem.appendChild(icon);
      }
      
      const title = document.createElement('span');
      const maxLen = isMobile ? 12 : 20;
      title.innerText = ev.title.length > maxLen ? ev.title.slice(0,maxLen-1)+'…' : ev.title;
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
        alert('All events on ' + dateStr + ':

' + dayEvents.map(e => '• ' + e.title).join('
'));
      };
      dayCell.appendChild(moreItem);
    }
    
    grid.appendChild(dayCell);
  }
}

function renderMini() {
  const mini = document.getElementById('miniGrid');
  mini.innerHTML = '';
  
  const today = new Date();
  const todayStr = `${today.getFullYear()}-${String(today.getMonth()+1).padStart(2,'0')}-${String(today.getDate()).padStart(2,'0')}`;
  
  const first = new Date(currentYear, currentMonth-1, 1);
  const startOffset = (first.getDay() + 6) % 7;
  const daysInMonth = new Date(currentYear, currentMonth, 0).getDate();
  
  // Empty cells
  for (let i = 0; i < startOffset; i++) {
    const e = document.createElement('div');
    e.style.opacity = '0';
    e.style.minHeight = '36px';
    mini.appendChild(e);
  }
  
  // Days
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
}

function showEventDetails(ev) {
  let details = `${ev.title}

${ev.description || 'No description'}`;
  if (ev.tags && ev.tags.length > 0) {
    details += `

Tags: ${ev.tags.join(', ')}`;
  }
  if (ev.platforms && ev.platforms.length > 0) {
    details += `

Platforms: ${ev.platforms.join(', ')}`;
  }
  alert(details);
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
  // Tags dropdown
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
  
  // Platforms dropdown
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
}

function toggleDropdown(selectId) {
  const select = document.getElementById(selectId);
  const items = select.querySelector('.select-items');
  
  // Close other dropdowns
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
  const array = type === 'tags' ? selectedTags : selectedPlatforms;
  const idx = array.indexOf(value);
  
  if (idx > -1) {
    array.splice(idx, 1);
  } else {
    array.push(value);
  }
  
  const displayId = type === 'tags' ? 'tagsDisplay' : 'platformsDisplay';
  const itemsId = type === 'tags' ? 'tagsItems' : 'platformsItems';
  const selectId = type === 'tags' ? 'tagsSelect' : 'platformsSelect';
  
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

// Close dropdowns when clicking outside
document.addEventListener('click', (e) => {
  if (!e.target.closest('.custom-select')) {
    document.querySelectorAll('.custom-select').forEach(s => {
      s.classList.remove('active');
      s.querySelector('.select-items').classList.remove('show');
    });
  }
});

// Close modal when clicking outside
document.getElementById('eventModal').addEventListener('click', (e) => {
  if (e.target.id === 'eventModal') {
    closeEventModal();
  }
});

// Form submission
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

// Initialize on load
init();
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
