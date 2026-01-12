"""
Shark-Themed Web Calendar System with Authentication and PFP Cropping
Requires: aiohttp, supabase, aiohttp-session, cryptography
Install: pip install aiohttp supabase aiohttp-jinja2 jinja2 aiohttp-session cryptography
"""

import os
import json
import base64
import hashlib
import logging
from datetime import datetime, timedelta
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
    
    # Try to load .env file if it exists (optional, for local development)
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
    
    # Optional: Use pooler URL if provided, otherwise use regular URL
    pooler_url = os.environ.get('SUPABASE_POOLER_URL')
    if pooler_url:
        env_vars['SUPABASE_POOLER_URL'] = pooler_url
        logger.info(f"✅ SUPABASE_POOLER_URL configured (using connection pooler)")
    else:
        logger.info("ℹ️  SUPABASE_POOLER_URL not set (using regular URL)")
    
    # Port configuration for Render (uses PORT env var) or default to 8080
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
        """Hash password using SHA-256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def verify_password(self, password: str) -> bool:
        """Verify password against stored hash"""
        is_valid = self._hash_password(password) == self.password_hash
        logger.info(f"🔐 Password verification for {self.username}: {'✅ Success' if is_valid else '❌ Failed'}")
        return is_valid
    
    def set_profile_picture(self, picture_data: str):
        """Set profile picture (base64 encoded)"""
        logger.info(f"🖼️  Setting profile picture for {self.username}")
        self.profile_picture = picture_data


class SharkCalendarDB:
    """Database handler for shark calendar using Supabase"""
    
    def __init__(self, supabase_url: str, supabase_key: str, pooler_url: Optional[str] = None):
        # Use pooler URL if provided, otherwise use regular URL
        connection_url = pooler_url if pooler_url else supabase_url
        
        logger.info(f"🔗 Connecting to Supabase...")
        if pooler_url:
            logger.info(f"   Using connection pooler for better performance")
        else:
            logger.info(f"   Using standard REST API connection")
        
        # Supabase client with built-in connection pooling
        # The Supabase client automatically handles connection pooling
        # and keeps connections alive for reuse
        self.client: Client = create_client(connection_url, supabase_key)
        self.events_table = "shark_events"
        self.users_table = "shark_users"
        
        logger.info(f"✅ Database client initialized")
    
    async def initialize_tables(self):
        """Create tables if they don't exist using Supabase REST API"""
        try:
            # Check if tables exist by attempting to query them
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
                          event_date: str, event_time: str,
                          shark_species: str, username: str) -> Dict:
        """Create a new calendar event"""
        logger.info(f"📝 Creating event: '{title}' for user '{username}'")
        
        data = {
            "title": title,
            "description": description,
            "event_date": event_date,
            "event_time": event_time,
            "shark_species": shark_species,
            "username": username,
            "created_at": datetime.now().isoformat()
        }
        
        function openProfileModal() {
            document.getElementById('profileModal').classList.add('active');
        }
        
        function closeProfileModal() {
            document.getElementById('profileModal').classList.remove('active');
            
            if (cropper) {
                cropper.destroy();
                cropper = null;
            }
            
            document.getElementById('profileFileInput').value = '';
            document.getElementById('cropContainer').style.display = 'none';
            document.getElementById('saveCropBtn').style.display = 'none';
        }
        
        // Event Form
        document.getElementById('eventForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const data = {
                title: document.getElementById('eventTitle').value,
                description: document.getElementById('eventDescription').value,
                event_date: document.getElementById('eventDate').value,
                event_time: document.getElementById('eventTime').value,
                tags: JSON.stringify(currentTags)
            };
            
            try {
                const response = await fetch('/api/events', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                
                if (response.ok) {
                    closeEventModal();
                    await loadEvents();
                    document.getElementById('eventForm').reset();
                    currentTags = [];
                } else {
                    alert('Failed to create event');
                }
            } catch (error) {
                alert('Failed to create event');
            }
        });
        
        // Initialize
        loadEvents();
        setupTagInput();
    </script>
        
        try:
            result = self.client.table(self.events_table).insert(data).execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Event created successfully")
                return result.data[0]
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
                return result.data
            return []
        except Exception as e:
            logger.error(f"❌ Error getting events: {e}")
            return []
    
    async def update_event(self, event_id: int, username: str, updates: Dict) -> Dict:
        """Update an existing event"""
        logger.info(f"✏️  Updating event ID {event_id}")
        
        try:
            result = self.client.table(self.events_table)\
                .update(updates)\
                .eq("id", event_id)\
                .eq("username", username)\
                .execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Event updated")
                return result.data[0]
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
            env_vars.get('SUPABASE_POOLER_URL')  # Optional pooler URL
        )
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
            
            # Load profile picture
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
            'month': datetime.now().strftime('%B')
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
        """API endpoint to create event"""
        session = await get_session(request)
        username = session['username']
        
        try:
            data = await request.json()
            event = await self.db.create_event(
                title=data['title'],
                description=data.get('description', ''),
                event_date=data['event_date'],
                event_time=data.get('event_time', '12:00'),
                tags=data.get('tags', '[]'),
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
        return '''
<!DOCTYPE html>
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
        
        .shark-logo {
            font-size: 100px;
            text-align: center;
            margin-bottom: 20px;
        }
        
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
        
        .form-group {
            margin-bottom: 25px;
        }
        
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
</html>
        '''
    
    def get_index_template(self) -> str:
        """Return HTML template for main page with cropping feature"""
        return '''
<!DOCTYPE html>
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
            --shark-grey: #808b96;
            --foam-white: #e8f4f8;
            --danger-red: #ff4757;
            --neon-cyan: #00d9ff;
        }
        
        body {
            font-family: 'Space Mono', monospace;
            background: var(--ocean-deep);
            min-height: 100vh;
            overflow-x: hidden;
            position: relative;
        }
        
        /* Animated background */
        .ocean-bg {
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: linear-gradient(180deg, 
                var(--ocean-deep) 0%, 
                var(--ocean-mid) 50%, 
                var(--ocean-light) 100%);
            z-index: 0;
        }
        
        .bubbles {
            position: fixed;
            width: 100%;
            height: 100%;
            z-index: 1;
            overflow: hidden;
            pointer-events: none;
        }
        
        .bubble {
            position: absolute;
            bottom: -100px;
            background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.3), rgba(255,255,255,0.05));
            border-radius: 50%;
            opacity: 0.4;
            animation: rise 15s infinite ease-in;
        }
        
        .bubble:nth-child(1) { left: 10%; width: 25px; height: 25px; animation-delay: 0s; animation-duration: 12s; }
        .bubble:nth-child(2) { left: 25%; width: 35px; height: 35px; animation-delay: 2s; animation-duration: 14s; }
        .bubble:nth-child(3) { left: 50%; width: 20px; height: 20px; animation-delay: 4s; animation-duration: 10s; }
        .bubble:nth-child(4) { left: 75%; width: 30px; height: 30px; animation-delay: 1s; animation-duration: 13s; }
        .bubble:nth-child(5) { left: 85%; width: 45px; height: 45px; animation-delay: 3s; animation-duration: 16s; }
        .bubble:nth-child(6) { left: 40%; width: 28px; height: 28px; animation-delay: 5s; animation-duration: 11s; }
        
        @keyframes rise {
            0% { bottom: -100px; transform: translateX(0); }
            50% { transform: translateX(50px); }
            100% { bottom: 110%; transform: translateX(-50px); }
        }
        
        .shark-silhouette {
            position: fixed;
            font-size: 100px;
            opacity: 0.08;
            animation: swim 30s infinite linear;
            z-index: 1;
            filter: blur(2px);
            pointer-events: none;
        }
        
        @keyframes swim {
            0% { left: -150px; top: 20%; }
            100% { left: 110%; top: 60%; }
        }
        
        .container {
            max-width: 1400px;
            margin: 0 auto;
            padding: 20px;
            position: relative;
            z-index: 10;
        }
        
        .header {
            background: rgba(0, 26, 51, 0.85);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 12px;
            color: var(--foam-white);
            padding: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 20px;
            box-shadow: 0 0 40px rgba(0, 217, 255, 0.2);
            position: relative;
            overflow: hidden;
        }
        
        .header::before {
            content: '🦈';
            position: absolute;
            font-size: 150px;
            opacity: 0.05;
            left: -20px;
            top: -40px;
            transform: rotate(-15deg);
        }
        
        .header h1 {
            font-family: 'Bebas Neue', cursive;
            font-size: 3em;
            letter-spacing: 6px;
            text-transform: uppercase;
            text-shadow: 0 0 20px rgba(0, 217, 255, 0.5);
            color: var(--neon-cyan);
        }
        
        .header-right {
            display: flex;
            align-items: center;
            gap: 20px;
            z-index: 1;
        }
        
        .profile-section {
            display: flex;
            align-items: center;
            gap: 15px;
            background: rgba(0, 102, 161, 0.3);
            padding: 10px 20px;
            border-radius: 50px;
            border: 2px solid rgba(0, 217, 255, 0.3);
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .profile-section:hover {
            background: rgba(0, 102, 161, 0.5);
            border-color: var(--neon-cyan);
            box-shadow: 0 0 20px rgba(0, 217, 255, 0.3);
        }
        
        .profile-picture {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid var(--neon-cyan);
            background: var(--foam-white);
            box-shadow: 0 0 15px rgba(0, 217, 255, 0.4);
        }
        
        .profile-placeholder {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            background: var(--foam-white);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            border: 3px solid var(--neon-cyan);
            box-shadow: 0 0 15px rgba(0, 217, 255, 0.4);
        }
        
        .username {
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            font-size: 0.9em;
        }
        
        .logout-btn {
            padding: 12px 24px;
            background: rgba(0, 61, 92, 0.4);
            border: 2px solid var(--neon-cyan);
            color: var(--foam-white);
            border-radius: 8px;
            cursor: pointer;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            font-size: 0.85em;
            transition: all 0.3s;
            font-family: 'Space Mono', monospace;
        }
        
        .logout-btn:hover {
            background: var(--neon-cyan);
            color: var(--ocean-deep);
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0, 217, 255, 0.4);
        }
        
        .controls {
            background: rgba(0, 26, 51, 0.85);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 12px;
            padding: 20px 30px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
            box-shadow: 0 0 40px rgba(0, 217, 255, 0.2);
        }
        
        .controls-left {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }
        
        .btn {
            padding: 14px 28px;
            border: 2px solid var(--neon-cyan);
            border-radius: 8px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 700;
            letter-spacing: 2px;
            text-transform: uppercase;
            transition: all 0.3s;
            font-family: 'Space Mono', monospace;
            position: relative;
            overflow: hidden;
        }
        
        .btn::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(0, 217, 255, 0.3), transparent);
            transition: left 0.5s;
        }
        
        .btn:hover::before {
            left: 100%;
        }
        
        .btn-primary {
            background: linear-gradient(135deg, var(--ocean-light) 0%, var(--ocean-mid) 100%);
            color: var(--foam-white);
        }
        
        .btn-primary:hover {
            transform: translateY(-2px);
            box-shadow: 0 5px 20px rgba(0, 217, 255, 0.4);
        }
        
        .calendar-view {
            background: rgba(0, 26, 51, 0.85);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 12px;
            padding: 25px;
            margin-bottom: 20px;
            box-shadow: 0 0 40px rgba(0, 217, 255, 0.2);
        }
        
        .calendar-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 25px;
        }
        
        .calendar-header h2 {
            font-family: 'Bebas Neue', cursive;
            color: var(--neon-cyan);
            font-size: 2em;
            letter-spacing: 3px;
        }
        
        .calendar-nav {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        
        .nav-btn {
            padding: 10px 16px;
            background: rgba(0, 102, 161, 0.3);
            border: 2px solid rgba(0, 217, 255, 0.3);
            color: var(--foam-white);
            border-radius: 6px;
            cursor: pointer;
            font-weight: 700;
            transition: all 0.3s;
            font-family: 'Space Mono', monospace;
        }
        
        .nav-btn:hover {
            background: rgba(0, 102, 161, 0.5);
            border-color: var(--neon-cyan);
            box-shadow: 0 0 15px rgba(0, 217, 255, 0.3);
        }
        
        .calendar-grid-month {
            display: grid;
            grid-template-columns: repeat(7, 1fr);
            gap: 8px;
        }
        
        .calendar-day-header {
            text-align: center;
            padding: 12px;
            font-weight: 700;
            color: var(--neon-cyan);
            font-size: 0.85em;
            letter-spacing: 1px;
            text-transform: uppercase;
        }
        
        .calendar-day {
            min-height: 120px;
            background: rgba(0, 61, 92, 0.3);
            border: 2px solid rgba(0, 217, 255, 0.2);
            border-radius: 8px;
            padding: 8px;
            cursor: pointer;
            transition: all 0.3s;
            position: relative;
        }
        
        .calendar-day:hover {
            background: rgba(0, 61, 92, 0.5);
            border-color: var(--neon-cyan);
            box-shadow: 0 0 15px rgba(0, 217, 255, 0.2);
        }
        
        .calendar-day.other-month {
            opacity: 0.3;
        }
        
        .calendar-day.today {
            border-color: var(--neon-cyan);
            box-shadow: 0 0 20px rgba(0, 217, 255, 0.4);
        }
        
        .day-number {
            font-weight: 700;
            color: var(--foam-white);
            margin-bottom: 6px;
            font-size: 0.9em;
        }
        
        .calendar-day.today .day-number {
            color: var(--neon-cyan);
            font-size: 1.1em;
        }
        
        .day-events {
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        
        .day-event {
            background: rgba(0, 217, 255, 0.2);
            border-left: 3px solid var(--neon-cyan);
            padding: 4px 6px;
            border-radius: 3px;
            font-size: 0.75em;
            color: var(--foam-white);
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .day-event:hover {
            background: rgba(0, 217, 255, 0.3);
            transform: translateX(2px);
        }
        
        .event-tag {
            display: inline-block;
            padding: 2px 6px;
            background: rgba(0, 217, 255, 0.3);
            border-radius: 3px;
            font-size: 0.7em;
            margin-left: 4px;
        }
        
        .view-toggle {
            display: flex;
            gap: 10px;
        }
        
        .view-toggle button {
            padding: 8px 16px;
            background: rgba(0, 61, 92, 0.4);
            border: 2px solid rgba(0, 217, 255, 0.2);
            color: var(--foam-white);
            border-radius: 6px;
            cursor: pointer;
            font-weight: 700;
            font-size: 0.85em;
            transition: all 0.3s;
            font-family: 'Space Mono', monospace;
        }
        
        .view-toggle button.active {
            background: rgba(0, 102, 161, 0.5);
            border-color: var(--neon-cyan);
            color: var(--neon-cyan);
        }
        
        .view-toggle button:hover {
            border-color: var(--neon-cyan);
        }
        
        .tags-input-container {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            padding: 8px;
            background: rgba(0, 61, 92, 0.4);
            border: 2px solid rgba(0, 217, 255, 0.2);
            border-radius: 8px;
            min-height: 45px;
            transition: all 0.3s;
        }
        
        .tags-input-container:focus-within {
            border-color: var(--neon-cyan);
            box-shadow: 0 0 20px rgba(0, 217, 255, 0.3);
        }
        
        .tag-item {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 6px 12px;
            background: rgba(0, 217, 255, 0.2);
            border: 1px solid var(--neon-cyan);
            color: var(--foam-white);
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
        }
        
        .tag-remove {
            cursor: pointer;
            color: var(--danger-red);
            font-weight: 700;
            transition: all 0.2s;
        }
        
        .tag-remove:hover {
            transform: scale(1.2);
        }
        
        .tag-input {
            border: none;
            background: transparent;
            color: var(--foam-white);
            outline: none;
            flex: 1;
            min-width: 120px;
            font-family: 'Space Mono', monospace;
            padding: 6px;
        }
        
        .tag-input::placeholder {
            color: rgba(232, 244, 248, 0.3);
        }
        
        .tag-suggestions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-top: 8px;
        }
        
        .tag-suggestion {
            padding: 6px 12px;
            background: rgba(0, 61, 92, 0.3);
            border: 1px solid rgba(0, 217, 255, 0.2);
            color: var(--foam-white);
            border-radius: 20px;
            font-size: 0.8em;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .tag-suggestion:hover {
            background: rgba(0, 217, 255, 0.2);
            border-color: var(--neon-cyan);
        }
        
        .controls {
            background: rgba(0, 26, 51, 0.85);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 12px;
            padding: 20px 30px;
            margin-bottom: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 15px;
            box-shadow: 0 0 40px rgba(0, 217, 255, 0.2);
        }
        
        .controls-left {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }
        
        .list-view {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 20px;
            padding-bottom: 40px;
        }
            background: rgba(0, 26, 51, 0.85);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 12px;
            padding: 25px;
            transition: all 0.3s;
            cursor: pointer;
            position: relative;
            overflow: hidden;
            box-shadow: 0 0 30px rgba(0, 217, 255, 0.15);
        }
        
        .event-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 5px;
            height: 100%;
            background: linear-gradient(180deg, var(--neon-cyan) 0%, var(--ocean-light) 100%);
        }
        
        .event-card:hover {
            transform: translateY(-5px);
            border-color: var(--neon-cyan);
            box-shadow: 0 10px 40px rgba(0, 217, 255, 0.3);
        }
        
        .event-card h3 {
            color: var(--neon-cyan);
            margin-bottom: 12px;
            font-size: 1.5em;
            font-family: 'Bebas Neue', cursive;
            letter-spacing: 2px;
        }
        
        .event-card .shark-badge {
            display: inline-block;
            padding: 6px 14px;
            background: rgba(0, 217, 255, 0.2);
            color: var(--neon-cyan);
            border: 1px solid var(--neon-cyan);
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: 700;
            margin-bottom: 12px;
            letter-spacing: 1px;
        }
        
        .event-card .date-time {
            color: var(--foam-white);
            font-size: 0.9em;
            margin: 10px 0;
            opacity: 0.8;
        }
        
        .event-card .description {
            color: var(--foam-white);
            line-height: 1.6;
            opacity: 0.7;
        }
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0, 26, 51, 0.95);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        
        .modal.active {
            display: flex;
        }
        
        .modal-content {
            background: rgba(0, 26, 51, 0.95);
            backdrop-filter: blur(20px);
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 12px;
            padding: 35px;
            max-width: 550px;
            width: 90%;
            max-height: 90vh;
            overflow-y: auto;
            box-shadow: 0 0 60px rgba(0, 217, 255, 0.3);
        }
        
        .modal-content h2 {
            font-family: 'Bebas Neue', cursive;
            color: var(--neon-cyan);
            font-size: 2.2em;
            letter-spacing: 3px;
            margin-bottom: 25px;
            text-shadow: 0 0 15px rgba(0, 217, 255, 0.5);
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: var(--foam-white);
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 2px;
            text-transform: uppercase;
        }
        
        .form-group input,
        .form-group textarea,
        .form-group select {
            width: 100%;
            padding: 12px;
            background: rgba(0, 61, 92, 0.4);
            border: 2px solid rgba(0, 217, 255, 0.2);
            border-radius: 8px;
            font-size: 15px;
            color: var(--foam-white);
            font-family: 'Space Mono', monospace;
            transition: all 0.3s;
        }
        
        .form-group input::placeholder,
        .form-group textarea::placeholder {
            color: rgba(232, 244, 248, 0.3);
        }
        
        .form-group input:focus,
        .form-group textarea:focus,
        .form-group select:focus {
            outline: none;
            border-color: var(--neon-cyan);
            box-shadow: 0 0 20px rgba(0, 217, 255, 0.3);
        }
        
        .crop-container {
            max-width: 100%;
            max-height: 400px;
            margin: 20px 0;
            border: 2px solid rgba(0, 217, 255, 0.3);
            border-radius: 8px;
            overflow: hidden;
        }
        
        .crop-container img {
            max-width: 100%;
        }
        
        .profile-preview {
            text-align: center;
            margin: 20px 0;
        }
        
        .profile-preview img {
            width: 150px;
            height: 150px;
            border-radius: 50%;
            object-fit: cover;
            border: 4px solid var(--neon-cyan);
            box-shadow: 0 0 30px rgba(0, 217, 255, 0.4);
        }
        
        .btn-secondary {
            background: rgba(128, 139, 150, 0.3);
            color: var(--foam-white);
            border: 2px solid var(--shark-grey);
        }
        
        .btn-secondary:hover {
            background: var(--shark-grey);
            transform: translateY(-2px);
        }
        
        .form-actions {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
            margin-top: 25px;
        }
        
        .empty-state {
            text-align: center;
            padding: 80px 20px;
            color: var(--foam-white);
            grid-column: 1 / -1;
        }
        
        .empty-state .shark-emoji {
            font-size: 120px;
            margin-bottom: 20px;
            opacity: 0.3;
            animation: float 4s ease-in-out infinite;
        }
        
        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-20px); }
        }
        
        .empty-state h2 {
            font-family: 'Bebas Neue', cursive;
            font-size: 2.5em;
            color: var(--neon-cyan);
            letter-spacing: 4px;
            margin-bottom: 15px;
        }
        
        .empty-state p {
            opacity: 0.7;
            font-size: 1.1em;
        }
        
        /* Custom scrollbar */
        ::-webkit-scrollbar {
            width: 10px;
        }
        
        ::-webkit-scrollbar-track {
            background: var(--ocean-deep);
        }
        
        ::-webkit-scrollbar-thumb {
            background: var(--ocean-light);
            border-radius: 5px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: var(--neon-cyan);
        }
        
        @media (max-width: 768px) {
            .header {
                flex-direction: column;
                gap: 20px;
                text-align: center;
            }
            
            .header h1 {
                font-size: 2em;
            }
            
            .list-view {
                grid-template-columns: 1fr;
            }
        }
    </style>
