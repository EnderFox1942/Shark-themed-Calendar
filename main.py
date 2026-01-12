"""
Shark-Themed Web Calendar System with Authentication
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
from aiohttp_session import setup, get_session, new_session
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
    
    def __init__(self, supabase_url: str, supabase_key: str):
        # Supabase client with built-in connection pooling
        # The Supabase client automatically handles connection pooling
        # and keeps connections alive for reuse
        self.client: Client = create_client(supabase_url, supabase_key)
        self.events_table = "shark_events"
        self.users_table = "shark_users"
    
    async def initialize_tables(self):
        """Create tables if they don't exist"""
        try:
            # SQL to create tables
            create_tables_sql = """
            -- Create events table if not exists
            CREATE TABLE IF NOT EXISTS shark_events (
                id BIGSERIAL PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT,
                event_date DATE NOT NULL,
                event_time TIME,
                shark_species TEXT DEFAULT 'Great White',
                username TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );

            -- Create users table if not exists
            CREATE TABLE IF NOT EXISTS shark_users (
                username TEXT PRIMARY KEY,
                profile_picture TEXT,
                updated_at TIMESTAMP DEFAULT NOW()
            );

            -- Enable Row Level Security if not already enabled
            DO $ 
            BEGIN
                ALTER TABLE shark_events ENABLE ROW LEVEL SECURITY;
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $;

            DO $ 
            BEGIN
                ALTER TABLE shark_users ENABLE ROW LEVEL SECURITY;
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $;

            -- Create policies if they don't exist
            DO $ 
            BEGIN
                CREATE POLICY "Enable all operations for authenticated users" 
                ON shark_events FOR ALL 
                USING (true);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $;

            DO $ 
            BEGIN
                CREATE POLICY "Enable all operations for authenticated users" 
                ON shark_users FOR ALL 
                USING (true);
            EXCEPTION
                WHEN duplicate_object THEN NULL;
            END $;
            """
            
            # Execute SQL using Supabase RPC or raw SQL
            # Note: Supabase Python client doesn't directly support raw SQL execution
            # We'll try to query the tables to check if they exist
            try:
                # Test if tables exist by querying them
                self.client.table(self.events_table).select("id").limit(1).execute()
                self.client.table(self.users_table).select("username").limit(1).execute()
                print("✅ Database tables verified")
            except Exception as table_error:
                print("⚠️  Tables may not exist. Please create them manually in Supabase SQL Editor:")
                print("\n" + "="*70)
                print(create_tables_sql)
                print("="*70 + "\n")
                print("Or run this SQL in your Supabase SQL Editor dashboard.")
                # Don't raise error, let app continue
                
        except Exception as e:
            print(f"⚠️  Warning during table initialization: {e}")
            print("The app will continue, but you may need to create tables manually.")
    
    async def create_event(self, title: str, description: str, 
                          event_date: str, event_time: str,
                          shark_species: str, username: str) -> Dict:
        """Create a new calendar event"""
        logger.info(f"📝 Creating event: '{title}' for user '{username}'")
        logger.info(f"   Date: {event_date}, Time: {event_time}, Species: {shark_species}")
        
        data = {
            "title": title,
            "description": description,
            "event_date": event_date,
            "event_time": event_time,
            "shark_species": shark_species,
            "username": username,
            "created_at": datetime.now().isoformat()
        }
        
        try:
            result = self.client.table(self.events_table).insert(data).execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Event created successfully with ID: {result.data[0].get('id')}")
                return result.data[0]
            elif isinstance(result, dict):
                logger.info("✅ Event created successfully")
                return result
            logger.warning("⚠️  Event creation returned empty result")
            return {}
        except Exception as e:
            logger.error(f"❌ Error creating event: {e}")
            return {}
    
    async def get_events(self, username: str, start_date: Optional[str] = None, 
                        end_date: Optional[str] = None) -> List[Dict]:
        """Get events for a specific user"""
        logger.info(f"📅 Fetching events for user: {username}")
        if start_date or end_date:
            logger.info(f"   Date range: {start_date or 'any'} to {end_date or 'any'}")
        
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
            elif isinstance(result, list):
                logger.info(f"✅ Retrieved {len(result)} events")
                return result
            logger.info("ℹ️  No events found")
            return []
        except Exception as e:
            logger.error(f"❌ Error getting events: {e}")
            return []
    
    async def update_event(self, event_id: int, username: str, updates: Dict) -> Dict:
        """Update an existing event (only if it belongs to the user)"""
        logger.info(f"✏️  Updating event ID {event_id} for user '{username}'")
        logger.info(f"   Updates: {updates}")
        
        try:
            result = self.client.table(self.events_table)\
                .update(updates)\
                .eq("id", event_id)\
                .eq("username", username)\
                .execute()
            if hasattr(result, 'data') and result.data:
                logger.info(f"✅ Event {event_id} updated successfully")
                return result.data[0]
            elif isinstance(result, dict):
                logger.info(f"✅ Event {event_id} updated successfully")
                return result
            logger.warning(f"⚠️  Event {event_id} not found or update failed")
            return {}
        except Exception as e:
            logger.error(f"❌ Error updating event: {e}")
            return {}
    
    async def delete_event(self, event_id: int, username: str) -> bool:
        """Delete an event (only if it belongs to the user)"""
        logger.info(f"🗑️  Deleting event ID {event_id} for user '{username}'")
        
        try:
            result = self.client.table(self.events_table)\
                .delete()\
                .eq("id", event_id)\
                .eq("username", username)\
                .execute()
            if hasattr(result, 'data'):
                success = bool(result.data)
            else:
                success = bool(result)
            
            if success:
                logger.info(f"✅ Event {event_id} deleted successfully")
            else:
                logger.warning(f"⚠️  Event {event_id} not found or delete failed")
            return success
        except Exception as e:
            logger.error(f"❌ Error deleting event: {e}")
            return False
    
    async def save_profile_picture(self, username: str, picture_data: str) -> Dict:
        """Save or update user profile picture"""
        logger.info(f"🖼️  Saving profile picture for user '{username}'")
        logger.info(f"   Picture size: {len(picture_data)} bytes")
        
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
                logger.info(f"✅ Profile picture saved successfully for '{username}'")
                return result.data[0]
            elif isinstance(result, dict):
                logger.info(f"✅ Profile picture saved successfully for '{username}'")
                return result
            logger.warning("⚠️  Profile picture save returned empty result")
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
                    logger.info(f"✅ Profile picture found for '{username}'")
                else:
                    logger.info(f"ℹ️  No profile picture set for '{username}'")
                return picture
            elif isinstance(result, list) and len(result) > 0:
                picture = result[0].get("profile_picture")
                if picture:
                    logger.info(f"✅ Profile picture found for '{username}'")
                else:
                    logger.info(f"ℹ️  No profile picture set for '{username}'")
                return picture
            logger.info(f"ℹ️  No profile picture found for '{username}'")
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
            env_vars['SUPABASE_KEY']
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
        from aiohttp_session.cookie_storage import SimpleCookieStorage
        setup(self.app, SimpleCookieStorage())
        logger.info("✅ Session storage configured")
    
    def setup_routes(self):
        """Setup application routes"""
        routes = [
            ('GET', '/health', 'Health Check'),
            ('GET', '/favicon.ico', 'Favicon'),
            ('GET', '/login', 'Login Page'),
            ('POST', '/login', 'Login Submit'),
            ('GET', '/logout', 'Logout'),
            ('GET', '/', 'Main Calendar'),
            ('GET', '/api/events', 'Get Events API'),
            ('POST', '/api/events', 'Create Event API'),
            ('PUT', '/api/events/{id}', 'Update Event API'),
            ('DELETE', '/api/events/{id}', 'Delete Event API'),
            ('POST', '/api/profile-picture', 'Upload Profile Picture API'),
            ('GET', '/api/profile-picture', 'Get Profile Picture API'),
        ]
        
        for method, path, description in routes:
            logger.info(f"   {method:6} {path:30} → {description}")
        
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
        
        logger.info(f"✅ {len(routes)} routes configured")
    
    async def health_check(self, request: web.Request):
        """Public health check endpoint for uptime monitoring"""
        logger.info("🏥 Health check requested")
        response_data = {
            'status': 'ok',
            'service': 'Shark Calendar API',
            'timestamp': datetime.now().isoformat(),
            'uptime': 'online',
            'message': '🦈 Swimming smoothly!'
        }
        logger.info("✅ Health check: OK")
        return web.json_response(response_data)
    
    async def serve_favicon(self, request: web.Request):
        """Serve shark emoji as favicon"""
        logger.info("🦈 Favicon requested")
        # SVG shark favicon (inline, no external file needed)
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
        logger.info("📄 Login page requested")
        session = await get_session(request)
        if session.get('authenticated'):
            logger.info("   User already authenticated, redirecting to main page")
            raise web.HTTPFound('/')
        return {'error': request.query.get('error', '')}
    
    async def do_login(self, request: web.Request):
        """Handle login form submission"""
        logger.info("🔐 Login attempt...")
        data = await request.post()
        username = data.get('username', '')
        password = data.get('password', '')
        
        logger.info(f"   Username: {username}")
        
        if username == self.user.username and self.user.verify_password(password):
            session = await new_session(request)
            session['authenticated'] = True
            session['username'] = username
            
            logger.info(f"✅ Login successful for user '{username}'")
            
            # Load profile picture
            profile_pic = await self.db.get_profile_picture(username)
            if profile_pic:
                self.user.set_profile_picture(profile_pic)
            
            raise web.HTTPFound('/')
        else:
            logger.warning(f"❌ Login failed for username '{username}'")
            raise web.HTTPFound('/login?error=Invalid credentials')
    
    async def logout(self, request: web.Request):
        """Handle logout"""
        session = await get_session(request)
        username = session.get('username', 'Unknown')
        logger.info(f"🚪 User '{username}' logging out")
        session.clear()
        logger.info("✅ Session cleared, redirecting to login")
        raise web.HTTPFound('/login')
    
    @require_auth
    @aiohttp_jinja2.template('index.html')
    async def index(self, request: web.Request):
        """Render main calendar page"""
        session = await get_session(request)
        username = session['username']
        logger.info(f"📄 Main calendar page requested by '{username}'")
        
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
        
        logger.info(f"🔍 API: Get events requested by '{username}'")
        
        events = await self.db.get_events(username, start_date, end_date)
        return web.json_response(events)
    
    @require_auth
    async def create_event(self, request: web.Request):
        """API endpoint to create event"""
        session = await get_session(request)
        username = session['username']
        
        try:
            data = await request.json()
            logger.info(f"➕ API: Create event requested by '{username}'")
            
            event = await self.db.create_event(
                title=data['title'],
                description=data.get('description', ''),
                event_date=data['event_date'],
                event_time=data.get('event_time', '12:00'),
                shark_species=data.get('shark_species', 'Great White'),
                username=username
            )
            return web.json_response(event, status=201)
        except Exception as e:
            logger.error(f"❌ API: Error creating event: {e}")
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def update_event(self, request: web.Request):
        """API endpoint to update event"""
        session = await get_session(request)
        username = session['username']
        
        try:
            event_id = int(request.match_info['id'])
            data = await request.json()
            logger.info(f"✏️  API: Update event {event_id} requested by '{username}'")
            
            event = await self.db.update_event(event_id, username, data)
            return web.json_response(event)
        except Exception as e:
            logger.error(f"❌ API: Error updating event: {e}")
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def delete_event(self, request: web.Request):
        """API endpoint to delete event"""
        session = await get_session(request)
        username = session['username']
        
        try:
            event_id = int(request.match_info['id'])
            logger.info(f"🗑️  API: Delete event {event_id} requested by '{username}'")
            
            success = await self.db.delete_event(event_id, username)
            if success:
                return web.json_response({'success': True})
            return web.json_response({'error': 'Event not found'}, status=404)
        except Exception as e:
            logger.error(f"❌ API: Error deleting event: {e}")
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
                logger.warning(f"⚠️  API: No picture data provided by '{username}'")
                return web.json_response({'error': 'No picture data'}, status=400)
            
            logger.info(f"📸 API: Upload profile picture requested by '{username}'")
            await self.db.save_profile_picture(username, picture_data)
            return web.json_response({'success': True})
        except Exception as e:
            logger.error(f"❌ API: Error uploading profile picture: {e}")
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def get_profile_picture(self, request: web.Request):
        """API endpoint to get profile picture"""
        session = await get_session(request)
        username = session['username']
        
        try:
            logger.info(f"🖼️  API: Get profile picture requested by '{username}'")
            picture = await self.db.get_profile_picture(username)
            return web.json_response({'picture': picture})
        except Exception as e:
            logger.error(f"❌ API: Error getting profile picture: {e}")
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
        * { 
            margin: 0; 
            padding: 0; 
            box-sizing: border-box; 
        }
        
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
            overflow: hidden;
            position: relative;
        }
        
        /* Animated background water effect */
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
            top: 0;
            left: 0;
        }
        
        .bubble {
            position: absolute;
            bottom: -100px;
            width: 40px;
            height: 40px;
            background: radial-gradient(circle at 30% 30%, rgba(255,255,255,0.3), rgba(255,255,255,0.05));
            border-radius: 50%;
            opacity: 0.5;
            animation: rise 15s infinite ease-in;
        }
        
        .bubble:nth-child(1) { left: 10%; width: 25px; height: 25px; animation-delay: 0s; animation-duration: 12s; }
        .bubble:nth-child(2) { left: 25%; width: 35px; height: 35px; animation-delay: 2s; animation-duration: 14s; }
        .bubble:nth-child(3) { left: 50%; width: 20px; height: 20px; animation-delay: 4s; animation-duration: 10s; }
        .bubble:nth-child(4) { left: 75%; width: 30px; height: 30px; animation-delay: 1s; animation-duration: 13s; }
        .bubble:nth-child(5) { left: 85%; width: 45px; height: 45px; animation-delay: 3s; animation-duration: 16s; }
        .bubble:nth-child(6) { left: 40%; width: 28px; height: 28px; animation-delay: 5s; animation-duration: 11s; }
        
        @keyframes rise {
            0% {
                bottom: -100px;
                transform: translateX(0);
            }
            50% {
                transform: translateX(50px);
            }
            100% {
                bottom: 110%;
                transform: translateX(-50px);
            }
        }
        
        /* Swimming shark silhouette */
        .shark-silhouette {
            position: fixed;
            font-size: 120px;
            opacity: 0.1;
            animation: swim 25s infinite linear;
            z-index: 1;
            filter: blur(2px);
        }
        
        @keyframes swim {
            0% {
                left: -150px;
                top: 20%;
            }
            100% {
                left: 110%;
                top: 60%;
            }
        }
        
        /* Main container */
        .login-wrapper {
            position: relative;
            z-index: 10;
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
            box-shadow: 
                0 0 60px rgba(0, 217, 255, 0.2),
                inset 0 0 40px rgba(0, 102, 161, 0.1);
            position: relative;
        }
        
        /* Sonar ping effect */
        .login-container::before {
            content: '';
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            width: 100px;
            height: 100px;
            border: 2px solid var(--neon-cyan);
            border-radius: 50%;
            opacity: 0;
            animation: sonar 3s infinite;
        }
        
        @keyframes sonar {
            0% {
                width: 100px;
                height: 100px;
                opacity: 0.6;
            }
            100% {
                width: 500px;
                height: 500px;
                opacity: 0;
            }
        }
        
        .shark-logo {
            font-size: 100px;
            text-align: center;
            margin-bottom: 20px;
            animation: float 4s ease-in-out infinite;
            filter: drop-shadow(0 0 20px rgba(0, 217, 255, 0.5));
        }
        
        @keyframes float {
            0%, 100% { 
                transform: translateY(0px) rotate(-5deg); 
            }
            50% { 
                transform: translateY(-15px) rotate(5deg); 
            }
        }
        
        h1 {
            font-family: 'Bebas Neue', cursive;
            color: var(--neon-cyan);
            text-align: center;
            font-size: 3.5em;
            letter-spacing: 8px;
            margin-bottom: 10px;
            text-transform: uppercase;
            text-shadow: 
                0 0 10px rgba(0, 217, 255, 0.8),
                0 0 20px rgba(0, 217, 255, 0.5),
                0 0 30px rgba(0, 217, 255, 0.3);
        }
        
        .subtitle {
            color: var(--foam-white);
            text-align: center;
            margin-bottom: 40px;
            font-size: 0.9em;
            letter-spacing: 2px;
            text-transform: uppercase;
            opacity: 0.7;
        }
        
        .form-group {
            margin-bottom: 25px;
            position: relative;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: var(--foam-white);
            font-weight: 700;
            font-size: 11px;
            letter-spacing: 2px;
            text-transform: uppercase;
            opacity: 0.8;
        }
        
        .form-group input {
            width: 100%;
            padding: 15px;
            background: rgba(0, 61, 92, 0.4);
            border: 2px solid rgba(0, 217, 255, 0.2);
            border-radius: 4px;
            font-size: 16px;
            font-family: 'Space Mono', monospace;
            color: var(--foam-white);
            transition: all 0.3s;
        }
        
        .form-group input::placeholder {
            color: rgba(232, 244, 248, 0.3);
        }
        
        .form-group input:focus {
            outline: none;
            border-color: var(--neon-cyan);
            background: rgba(0, 61, 92, 0.6);
            box-shadow: 
                0 0 20px rgba(0, 217, 255, 0.3),
                inset 0 0 20px rgba(0, 217, 255, 0.1);
        }
        
        .btn-login {
            width: 100%;
            padding: 18px;
            background: linear-gradient(135deg, var(--ocean-light) 0%, var(--ocean-mid) 100%);
            color: var(--foam-white);
            border: 2px solid var(--neon-cyan);
            border-radius: 4px;
            font-size: 16px;
            font-weight: 700;
            font-family: 'Space Mono', monospace;
            cursor: pointer;
            transition: all 0.3s;
            margin-top: 10px;
            letter-spacing: 3px;
            text-transform: uppercase;
            position: relative;
            overflow: hidden;
        }
        
        .btn-login::before {
            content: '';
            position: absolute;
            top: 0;
            left: -100%;
            width: 100%;
            height: 100%;
            background: linear-gradient(90deg, transparent, rgba(0, 217, 255, 0.4), transparent);
            transition: left 0.5s;
        }
        
        .btn-login:hover::before {
            left: 100%;
        }
        
        .btn-login:hover {
            transform: translateY(-2px);
            box-shadow: 
                0 10px 30px rgba(0, 217, 255, 0.4),
                0 0 40px rgba(0, 217, 255, 0.3);
            border-color: var(--neon-cyan);
        }
        
        .btn-login:active {
            transform: translateY(0);
        }
        
        .error {
            background: rgba(255, 71, 87, 0.2);
            color: var(--danger-red);
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
            border: 2px solid var(--danger-red);
            display: none;
            font-size: 14px;
            text-align: center;
            animation: shake 0.5s;
        }
        
        .error.show {
            display: block;
        }
        
        @keyframes shake {
            0%, 100% { transform: translateX(0); }
            25% { transform: translateX(-10px); }
            75% { transform: translateX(10px); }
        }
        
        .depth-indicator {
            position: fixed;
            right: 30px;
            top: 50%;
            transform: translateY(-50%);
            color: rgba(232, 244, 248, 0.3);
            font-size: 12px;
            letter-spacing: 2px;
            writing-mode: vertical-rl;
            z-index: 5;
            text-transform: uppercase;
        }
        
        @media (max-width: 600px) {
            h1 {
                font-size: 2.5em;
                letter-spacing: 4px;
            }
            
            .login-container {
                padding: 40px 30px;
            }
            
            .depth-indicator {
                display: none;
            }
        }
    </style>
</head>
<body>
    <div class="ocean-bg"></div>
    
    <div class="bubbles">
        <div class="bubble"></div>
        <div class="bubble"></div>
        <div class="bubble"></div>
        <div class="bubble"></div>
        <div class="bubble"></div>
        <div class="bubble"></div>
    </div>
    
    <div class="shark-silhouette">🦈</div>
    
    <div class="depth-indicator">DEPTH: 200M - ACCESS PORTAL</div>
    
    <div class="login-wrapper">
        <div class="login-container">
            <div class="shark-logo">🦈</div>
            <h1>APEX</h1>
            <p class="subtitle">Calendar System</p>
            
            {% if error %}
            <div class="error show">⚠ {{ error }}</div>
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
                
                <button type="submit" class="btn-login">
                    ▶ Dive In
                </button>
            </form>
        </div>
    </div>
    
    <script>
        // Add particle effect on mouse move
        document.addEventListener('mousemove', (e) => {
            if (Math.random() > 0.95) {
                const particle = document.createElement('div');
                particle.style.position = 'fixed';
                particle.style.left = e.clientX + 'px';
                particle.style.top = e.clientY + 'px';
                particle.style.width = '3px';
                particle.style.height = '3px';
                particle.style.background = 'rgba(0, 217, 255, 0.6)';
                particle.style.borderRadius = '50%';
                particle.style.pointerEvents = 'none';
                particle.style.zIndex = '100';
                particle.style.boxShadow = '0 0 10px rgba(0, 217, 255, 0.8)';
                document.body.appendChild(particle);
                
                setTimeout(() => {
                    particle.style.transition = 'all 1s ease-out';
                    particle.style.transform = 'translateY(-50px)';
                    particle.style.opacity = '0';
                }, 10);
                
                setTimeout(() => {
                    document.body.removeChild(particle);
                }, 1000);
            }
        });
        
        // Typing sound effect simulation
        const inputs = document.querySelectorAll('input');
        inputs.forEach(input => {
            input.addEventListener('keydown', () => {
                input.style.boxShadow = '0 0 25px rgba(0, 217, 255, 0.4)';
                setTimeout(() => {
                    input.style.boxShadow = '0 0 20px rgba(0, 217, 255, 0.3)';
                }, 100);
            });
        });
    </script>
</body>
</html>
        '''
    
    def get_index_template(self) -> str:
        """Return HTML template for main page"""
        return '''
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ title }}</title>
    <link rel="icon" href="/favicon.ico" type="image/x-icon">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            overflow: hidden;
        }
        
        .header {
            background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            color: white;
            padding: 30px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            position: relative;
            overflow: hidden;
        }
        
        .header::before {
            content: '🦈';
            position: absolute;
            font-size: 150px;
            opacity: 0.1;
            left: -30px;
            top: -40px;
            transform: rotate(-15deg);
        }
        
        .header-left h1 {
            font-size: 3em;
            margin-bottom: 10px;
            text-shadow: 2px 2px 4px rgba(0,0,0,0.3);
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
            background: rgba(255,255,255,0.1);
            padding: 10px 20px;
            border-radius: 50px;
            cursor: pointer;
            transition: all 0.3s;
        }
        
        .profile-section:hover {
            background: rgba(255,255,255,0.2);
        }
        
        .profile-picture {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            object-fit: cover;
            border: 3px solid white;
            background: white;
            cursor: pointer;
        }
        
        .profile-placeholder {
            width: 50px;
            height: 50px;
            border-radius: 50%;
            background: white;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 24px;
            border: 3px solid white;
        }
        
        .username {
            font-weight: 600;
            font-size: 1.1em;
        }
        
        .logout-btn {
            padding: 10px 20px;
            background: rgba(255,255,255,0.2);
            border: 2px solid white;
            color: white;
            border-radius: 8px;
            cursor: pointer;
            font-weight: 600;
            transition: all 0.3s;
        }
        
        .logout-btn:hover {
            background: white;
            color: #2a5298;
        }
        
        .controls {
            padding: 20px 30px;
            background: #f8f9fa;
            border-bottom: 2px solid #dee2e6;
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
        }
        
        .btn {
            padding: 12px 24px;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 16px;
            font-weight: 600;
            transition: all 0.3s;
            display: inline-flex;
            align-items: center;
            gap: 8px;
        }
        
        .btn-primary {
            background: #2a5298;
            color: white;
        }
        
        .btn-primary:hover {
            background: #1e3c72;
            transform: translateY(-2px);
            box-shadow: 0 5px 15px rgba(42, 82, 152, 0.4);
        }
        
        .calendar-grid {
            padding: 30px;
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
            gap: 20px;
        }
        
        .event-card {
            background: white;
            border: 2px solid #e0e0e0;
            border-radius: 12px;
            padding: 20px;
            transition: all 0.3s;
            cursor: pointer;
            position: relative;
            overflow: hidden;
        }
        
        .event-card::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            width: 5px;
            height: 100%;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        }
        
        .event-card:hover {
            transform: translateY(-5px);
            box-shadow: 0 10px 25px rgba(0,0,0,0.15);
            border-color: #667eea;
        }
        
        .event-card h3 {
            color: #2a5298;
            margin-bottom: 10px;
            font-size: 1.4em;
        }
        
        .event-card .shark-species {
            display: inline-block;
            padding: 5px 12px;
            background: #e3f2fd;
            color: #1976d2;
            border-radius: 20px;
            font-size: 0.85em;
            font-weight: 600;
            margin-bottom: 10px;
        }
        
        .event-card .date-time {
            color: #666;
            font-size: 0.95em;
            margin: 8px 0;
        }
        
        .event-card .description {
            color: #555;
            line-height: 1.6;
        }
        
        .modal {
            display: none;
            position: fixed;
            top: 0;
            left: 0;
            width: 100%;
            height: 100%;
            background: rgba(0,0,0,0.6);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        
        .modal.active {
            display: flex;
        }
        
        .modal-content {
            background: white;
            border-radius: 15px;
            padding: 30px;
            max-width: 500px;
            width: 90%;
            max-height: 80vh;
            overflow-y: auto;
        }
        
        .form-group {
            margin-bottom: 20px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 600;
        }
        
        .form-group input,
        .form-group textarea,
        .form-group select {
            width: 100%;
            padding: 12px;
            border: 2px solid #e0e0e0;
            border-radius: 8px;
            font-size: 16px;
            transition: border 0.3s;
        }
        
        .form-group input:focus,
        .form-group textarea:focus,
        .form-group select:focus {
            outline: none;
            border-color: #667eea;
        }
        
        .form-actions {
            display: flex;
            gap: 10px;
            justify-content: flex-end;
            margin-top: 20px;
        }
        
        .btn-secondary {
            background: #6c757d;
            color: white;
        }
        
        .btn-secondary:hover {
            background: #545b62;
        }
        
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #999;
        }
        
        .empty-state .shark-emoji {
            font-size: 80px;
            margin-bottom: 20px;
            opacity: 0.5;
        }
        
        .profile-upload {
            margin-top: 15px;
            text-align: center;
        }
        
        .profile-upload input[type="file"] {
            display: none;
        }
        
        .profile-upload label {
            display: inline-block;
            padding: 8px 16px;
            background: #667eea;
            color: white;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            transition: all 0.3s;
        }
        
        .profile-upload label:hover {
            background: #5568d3;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="header-left">
                <h1>🦈 Shark Calendar</h1>
                <p>Dive into your schedule!</p>
            </div>
            <div class="header-right">
                <div class="profile-section" onclick="openProfileModal()">
                    {% if profile_picture %}
                    <img src="{{ profile_picture }}" class="profile-picture" id="headerProfilePic">
                    {% else %}
                    <div class="profile-placeholder">👤</div>
                    {% endif %}
                    <span class="username">{{ username }}</span>
                </div>
                <button class="logout-btn" onclick="location.href='/logout'">🚪 Logout</button>
            </div>
        </div>
        
        <div class="controls">
            <button class="btn btn-primary" onclick="openCreateModal()">
                ➕ Add Event
            </button>
            <button class="btn btn-primary" onclick="loadEvents()">
                🔄 Refresh
            </button>
        </div>
        
        <div class="calendar-grid" id="eventsContainer">
            <div class="empty-state">
                <div class="shark-emoji">🦈</div>
                <h2>No events yet!</h2>
                <p>Click "Add Event" to create your first shark calendar entry</p>
            </div>
        </div>
    </div>
    
    <div class="modal" id="eventModal">
        <div class="modal-content">
            <h2 id="modalTitle">🦈 New Shark Event</h2>
            <form id="eventForm">
                <div class="form-group">
                    <label>Event Title *</label>
                    <input type="text" id="eventTitle" required>
                </div>
                
                <div class="form-group">
                    <label>Description</label>
                    <textarea id="eventDescription" rows="3"></textarea>
                </div>
                
                <div class="form-group">
                    <label>Date *</label>
                    <input type="date" id="eventDate" required>
                </div>
                
                <div class="form-group">
                    <label>Time</label>
                    <input type="time" id="eventTime" value="12:00">
                </div>
                
                <div class="form-group">
                    <label>Shark Species 🦈</label>
                    <select id="sharkSpecies">
                        <option>Great White</option>
                        <option>Hammerhead</option>
                        <option>Tiger Shark</option>
                        <option>Bull Shark</option>
                        <option>Whale Shark</option>
                        <option>Mako Shark</option>
                        <option>Reef Shark</option>
                    </select>
                </div>
                
                <div class="form-actions">
                    <button type="button" class="btn btn-secondary" onclick="closeModal()">
                        Cancel
                    </button>
                    <button type="submit" class="btn btn-primary">
                        💾 Save Event
                    </button>
                </div>
            </form>
        </div>
    </div>
    
    <div class="modal" id="profileModal">
        <div class="modal-content">
            <h2>👤 Profile Settings</h2>
            <div style="text-align: center; margin: 20px 0;">
                {% if profile_picture %}
                <img src="{{ profile_picture }}" id="profilePreview" style="width: 150px; height: 150px; border-radius: 50%; object-fit: cover; border: 4px solid #667eea;">
                {% else %}
                <div id="profilePreview" style="width: 150px; height: 150px; border-radius: 50%; background: #e0e0e0; display: inline-flex; align-items: center; justify-content: center; font-size: 60px; border: 4px solid #667eea;">
                    👤
                </div>
                {% endif %}
            </div>
            
            <div class="profile-upload">
                <input type="file" id="profilePictureInput" accept="image/*" onchange="handleProfilePicture(event)">
                <label for="profilePictureInput">📸 Upload Profile Picture</label>
            </div>
            
            <div class="form-actions" style="margin-top: 30px;">
                <button type="button" class="btn btn-secondary" onclick="closeProfileModal()">
                    Close
                </button>
            </div>
        </div>
    </div>
    
    <script>
        let currentEventId = null;
        
        async function loadEvents() {
            try {
                const response = await fetch('/api/events');
                const events = await response.json();
                
                const container = document.getElementById('eventsContainer');
                
                if (events.length === 0) {
                    container.innerHTML = `
                        <div class="empty-state">
                            <div class="shark-emoji">🦈</div>
                            <h2>No events yet!</h2>
                            <p>Click "Add Event" to create your first shark calendar entry</p>
                        </div>
                    `;
                    return;
                }
                
                container.innerHTML = events.map(event => `
                    <div class="event-card" onclick="viewEvent(${event.id})">
                        <h3>${event.title}</h3>
                        <span class="shark-species">🦈 ${event.shark_species}</span>
                        <div class="date-time">
                            📅 ${new Date(event.event_date).toLocaleDateString()}
                            🕐 ${event.event_time}
                        </div>
                        <div class="description">${event.description || 'No description'}</div>
                    </div>
                `).join('');
            } catch (error) {
                console.error('Error loading events:', error);
                alert('Failed to load events');
            }
        }
        
        function openCreateModal() {
            currentEventId = null;
            document.getElementById('modalTitle').textContent = '🦈 New Shark Event';
            document.getElementById('eventForm').reset();
            document.getElementById('eventModal').classList.add('active');
        }
        
        function closeModal() {
            document.getElementById('eventModal').classList.remove('active');
        }
        
        function openProfileModal() {
            document.getElementById('profileModal').classList.add('active');
        }
        
        function closeProfileModal() {
            document.getElementById('profileModal').classList.remove('active');
        }
        
        async function viewEvent(eventId) {
            alert(`View event ${eventId} - Full view coming soon!`);
        }
        
        async function handleProfilePicture(event) {
            const file = event.target.files[0];
            if (!file) return;
            
            // Check file size (max 2MB)
            if (file.size > 2 * 1024 * 1024) {
                alert('File too large! Please choose an image under 2MB.');
                return;
            }
            
            const reader = new FileReader();
            reader.onload = async function(e) {
                const base64 = e.target.result;
                
                // Update preview
                const preview = document.getElementById('profilePreview');
                if (preview.tagName === 'IMG') {
                    preview.src = base64;
                } else {
                    preview.outerHTML = `<img src="${base64}" id="profilePreview" style="width: 150px; height: 150px; border-radius: 50%; object-fit: cover; border: 4px solid #667eea;">`;
                }
                
                // Update header
                const headerPic = document.getElementById('headerProfilePic');
                if (headerPic) {
                    headerPic.src = base64;
                } else {
                    document.querySelector('.profile-placeholder').outerHTML = `<img src="${base64}" class="profile-picture" id="headerProfilePic">`;
                }
                
                // Upload to server
                try {
                    const response = await fetch('/api/profile-picture', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({ picture: base64 })
                    });
                    
                    if (response.ok) {
                        console.log('Profile picture uploaded successfully');
                    } else {
                        alert('Failed to upload profile picture');
                    }
                } catch (error) {
                    console.error('Error:', error);
                    alert('Failed to upload profile picture');
                }
            };
            reader.readAsDataURL(file);
        }
        
        document.getElementById('eventForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            
            const data = {
                title: document.getElementById('eventTitle').value,
                description: document.getElementById('eventDescription').value,
                event_date: document.getElementById('eventDate').value,
                event_time: document.getElementById('eventTime').value,
                shark_species: document.getElementById('sharkSpecies').value
            };
            
            try {
                const response = await fetch('/api/events', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(data)
                });
                
                if (response.ok) {
                    closeModal();
                    loadEvents();
                } else {
                    alert('Failed to create event');
                }
            } catch (error) {
                console.error('Error:', error);
                alert('Failed to create event');
            }
        });
        
        // Load events on page load
        loadEvents();
    </script>
</body>
</html>
        '''
    
    def run(self):
        """Run the application"""
        logger.info("="*70)
        logger.info("🦈 SHARK CALENDAR SERVER STARTING")
        logger.info("="*70)
        logger.info(f"🌐 Host: {self.env_vars['APP_HOST']}")
        logger.info(f"🔌 Port: {self.env_vars['APP_PORT']}")
        logger.info(f"🏥 Health Check: http://{self.env_vars['APP_HOST']}:{self.env_vars['APP_PORT']}/health")
        logger.info(f"🔐 Login URL: http://{self.env_vars['APP_HOST']}:{self.env_vars['APP_PORT']}/login")
        logger.info(f"👤 Username: {self.env_vars['USER']}")
        logger.info("="*70)
        
        web.run_app(
            self.app,
            host=self.env_vars['APP_HOST'],
            port=int(self.env_vars['APP_PORT'])
        )


def main():
    """Main entry point"""
    logger.info("="*70)
    logger.info("🦈 SHARK CALENDAR - INITIALIZATION")
    logger.info("="*70)
    
    # Load environment variables using def function
    env_vars = load_environment()
    
    # Create application
    app = SharkCalendarApp(env_vars)
    
    # Initialize database tables
    logger.info("🔄 Initializing database...")
    import asyncio
    asyncio.run(app.db.initialize_tables())
    
    logger.info("✅ Initialization complete!")
    logger.info("")
    
    # Run application
    app.run()


if __name__ == '__main__':
    try:
        main()
    except ValueError as e:
        logger.error(f"❌ Configuration Error: {e}")
        logger.error("")
        logger.error("Please create a .env file with the following variables:")
        logger.error("SUPABASE_URL=your_supabase_url")
        logger.error("SUPABASE_KEY=your_supabase_key")
        logger.error("USER=your_username")
        logger.error("PASS=your_password")
        logger.error("SECRET_KEY=your_secret_key_for_sessions (use a proper Fernet key)")
        logger.error("APP_HOST=0.0.0.0")
        logger.error("APP_PORT=8080")
        logger.error("")
        logger.error("💡 Generate a Fernet key with: python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'")
        import sys
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("")
        logger.info("🛑 Shutdown requested by user")
        logger.info("👋 Shark Calendar stopped gracefully")
        import sys
        sys.exit(0)
    except Exception as e:
        logger.error(f"❌ Fatal Error: {e}")
        import traceback
        traceback.print_exc()
        import sys
        sys.exit(1)
else:
    # When imported as a module (e.g., by a WSGI server)
    logger.info("🦈 Shark Calendar imported as module")
    try:
        env_vars = load_environment()
        application = SharkCalendarApp(env_vars).app  # For WSGI servers
        logger.info("✅ WSGI application ready")
    except Exception as e:
        logger.error(f"❌ Failed to initialize WSGI application: {e}")
        raise
