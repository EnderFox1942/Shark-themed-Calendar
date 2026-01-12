"""
Shark-Themed Web Calendar System with Authentication
Requires: aiohttp, python-dotenv, supabase, aiohttp-session, cryptography
Install: pip install aiohttp python-dotenv supabase aiohttp-jinja2 jinja2 aiohttp-session cryptography
"""

import os
import json
import base64
import hashlib
from datetime import datetime, timedelta
from typing import Optional, Dict, List
from dotenv import load_dotenv
from aiohttp import web
import aiohttp_jinja2
import jinja2
from aiohttp_session import setup, get_session, new_session
from aiohttp_session.cookie_storage import EncryptedCookieStorage
from cryptography import fernet
from supabase import create_client, Client


def load_environment() -> Dict[str, str]:
    """Load environment variables from .env file"""
    load_dotenv()
    
    required_vars = ['SUPABASE_URL', 'SUPABASE_KEY', 'USER', 'PASS', 'SECRET_KEY']
    env_vars = {}
    
    for var in required_vars:
        value = os.getenv(var)
        if not value:
            raise ValueError(f"Missing required environment variable: {var}")
        env_vars[var] = value
    
    # Port configuration for Render (uses PORT env var) or default to 8080
    env_vars['APP_PORT'] = os.getenv('PORT', os.getenv('APP_PORT', '8080'))
    env_vars['APP_HOST'] = os.getenv('APP_HOST', '0.0.0.0')
    
    return env_vars


class User:
    """User class for authentication"""
    
    def __init__(self, username: str, password: str):
        self.username = username
        self.password_hash = self._hash_password(password)
        self.profile_picture = None
        self.display_name = username
    
    @staticmethod
    def _hash_password(password: str) -> str:
        """Hash password using SHA-256"""
        return hashlib.sha256(password.encode()).hexdigest()
    
    def verify_password(self, password: str) -> bool:
        """Verify password against stored hash"""
        return self._hash_password(password) == self.password_hash
    
    def set_profile_picture(self, picture_data: str):
        """Set profile picture (base64 encoded)"""
        self.profile_picture = picture_data


class SharkCalendarDB:
    """Database handler for shark calendar using Supabase"""
    
    def __init__(self, supabase_url: str, supabase_key: str):
        # Supabase client with built-in connection pooling
        # The Supabase client automatically handles connection pooling
        # and keeps connections alive for reuse
        self.client: Client = create_client(
            supabase_url, 
            supabase_key,
            options={
                'schema': 'public',
                'auto_refresh_token': True,
                'persist_session': True
            }
        )
        self.events_table = "shark_events"
        self.users_table = "shark_users"
    
    async def create_event(self, title: str, description: str, 
                          event_date: str, event_time: str,
                          shark_species: str, username: str) -> Dict:
        """Create a new calendar event"""
        data = {
            "title": title,
            "description": description,
            "event_date": event_date,
            "event_time": event_time,
            "shark_species": shark_species,
            "username": username,
            "created_at": datetime.now().isoformat()
        }
        
        result = self.client.table(self.events_table).insert(data).execute()
        return result.data[0] if result.data else {}
    
    async def get_events(self, username: str, start_date: Optional[str] = None, 
                        end_date: Optional[str] = None) -> List[Dict]:
        """Get events for a specific user"""
        query = self.client.table(self.events_table).select("*").eq("username", username)
        
        if start_date:
            query = query.gte("event_date", start_date)
        if end_date:
            query = query.lte("event_date", end_date)
        
        result = query.order("event_date", desc=False).execute()
        return result.data if result.data else []
    
    async def update_event(self, event_id: int, username: str, updates: Dict) -> Dict:
        """Update an existing event (only if it belongs to the user)"""
        result = self.client.table(self.events_table)\
            .update(updates)\
            .eq("id", event_id)\
            .eq("username", username)\
            .execute()
        return result.data[0] if result.data else {}
    
    async def delete_event(self, event_id: int, username: str) -> bool:
        """Delete an event (only if it belongs to the user)"""
        result = self.client.table(self.events_table)\
            .delete()\
            .eq("id", event_id)\
            .eq("username", username)\
            .execute()
        return bool(result.data)
    
    async def save_profile_picture(self, username: str, picture_data: str) -> Dict:
        """Save or update user profile picture"""
        data = {
            "username": username,
            "profile_picture": picture_data,
            "updated_at": datetime.now().isoformat()
        }
        
        # Try to update first, if not exists, insert
        result = self.client.table(self.users_table)\
            .upsert(data, on_conflict="username")\
            .execute()
        return result.data[0] if result.data else {}
    
    async def get_profile_picture(self, username: str) -> Optional[str]:
        """Get user profile picture"""
        result = self.client.table(self.users_table)\
            .select("profile_picture")\
            .eq("username", username)\
            .execute()
        
        if result.data and len(result.data) > 0:
            return result.data[0].get("profile_picture")
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
        self.env_vars = env_vars
        self.user = User(env_vars['USER'], env_vars['PASS'])
        self.db = SharkCalendarDB(
            env_vars['SUPABASE_URL'],
            env_vars['SUPABASE_KEY']
        )
        self.app = web.Application()
        self.setup_session()
        self.setup_routes()
        self.setup_templates()
    
    def setup_session(self):
        """Setup encrypted session storage"""
        secret_key = base64.urlsafe_b64encode(
            self.env_vars['SECRET_KEY'].encode().ljust(32)[:32]
        )
        setup(self.app, EncryptedCookieStorage(secret_key))
    
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
    
    async def health_check(self, request: web.Request):
        """Public health check endpoint for uptime monitoring"""
        return web.json_response({
            'status': 'ok',
            'service': 'Shark Calendar API',
            'timestamp': datetime.now().isoformat(),
            'uptime': 'online',
            'message': '🦈 Swimming smoothly!'
        })
    
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
        profile_pic = await self.db.get_profile_picture(session['username'])
        
        return {
            'title': 'Shark Calendar 🦈',
            'username': session['username'],
            'profile_picture': profile_pic,
            'year': datetime.now().year,
            'month': datetime.now().strftime('%B')
        }
    
    @require_auth
    async def get_events(self, request: web.Request):
        """API endpoint to get events"""
        session = await get_session(request)
        start_date = request.query.get('start_date')
        end_date = request.query.get('end_date')
        
        events = await self.db.get_events(session['username'], start_date, end_date)
        return web.json_response(events)
    
    @require_auth
    async def create_event(self, request: web.Request):
        """API endpoint to create event"""
        try:
            session = await get_session(request)
            data = await request.json()
            event = await self.db.create_event(
                title=data['title'],
                description=data.get('description', ''),
                event_date=data['event_date'],
                event_time=data.get('event_time', '12:00'),
                shark_species=data.get('shark_species', 'Great White'),
                username=session['username']
            )
            return web.json_response(event, status=201)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def update_event(self, request: web.Request):
        """API endpoint to update event"""
        try:
            session = await get_session(request)
            event_id = int(request.match_info['id'])
            data = await request.json()
            event = await self.db.update_event(event_id, session['username'], data)
            return web.json_response(event)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def delete_event(self, request: web.Request):
        """API endpoint to delete event"""
        try:
            session = await get_session(request)
            event_id = int(request.match_info['id'])
            success = await self.db.delete_event(event_id, session['username'])
            if success:
                return web.json_response({'success': True})
            return web.json_response({'error': 'Event not found'}, status=404)
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def upload_profile_picture(self, request: web.Request):
        """API endpoint to upload profile picture"""
        try:
            session = await get_session(request)
            data = await request.json()
            picture_data = data.get('picture')
            
            if not picture_data:
                return web.json_response({'error': 'No picture data'}, status=400)
            
            await self.db.save_profile_picture(session['username'], picture_data)
            return web.json_response({'success': True})
        except Exception as e:
            return web.json_response({'error': str(e)}, status=400)
    
    @require_auth
    async def get_profile_picture(self, request: web.Request):
        """API endpoint to get profile picture"""
        try:
            session = await get_session(request)
            picture = await self.db.get_profile_picture(session['username'])
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
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        
        .login-container {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 50px;
            max-width: 450px;
            width: 100%;
            text-align: center;
        }
        
        .shark-logo {
            font-size: 80px;
            margin-bottom: 20px;
            animation: float 3s ease-in-out infinite;
        }
        
        @keyframes float {
            0%, 100% { transform: translateY(0px); }
            50% { transform: translateY(-10px); }
        }
        
        h1 {
            color: #2a5298;
            margin-bottom: 10px;
            font-size: 2.5em;
        }
        
        .subtitle {
            color: #666;
            margin-bottom: 40px;
            font-size: 1.1em;
        }
        
        .form-group {
            margin-bottom: 25px;
            text-align: left;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            color: #333;
            font-weight: 600;
            font-size: 14px;
        }
        
        .form-group input {
            width: 100%;
            padding: 15px;
            border: 2px solid #e0e0e0;
            border-radius: 10px;
            font-size: 16px;
            transition: all 0.3s;
        }
        
        .form-group input:focus {
            outline: none;
            border-color: #667eea;
            box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1);
        }
        
        .btn-login {
            width: 100%;
            padding: 15px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            color: white;
            border: none;
            border-radius: 10px;
            font-size: 18px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s;
            margin-top: 10px;
        }
        
        .btn-login:hover {
            transform: translateY(-2px);
            box-shadow: 0 10px 25px rgba(102, 126, 234, 0.4);
        }
        
        .btn-login:active {
            transform: translateY(0);
        }
        
        .error {
            background: #fee;
            color: #c33;
            padding: 15px;
            border-radius: 10px;
            margin-bottom: 20px;
            border: 2px solid #fcc;
            display: none;
        }
        
        .error.show {
            display: block;
        }
        
        .waves {
            position: fixed;
            bottom: 0;
            left: 0;
            width: 100%;
            height: 100px;
            pointer-events: none;
            opacity: 0.3;
        }
    </style>
</head>
<body>
    <div class="login-container">
        <div class="shark-logo">🦈</div>
        <h1>Shark Calendar</h1>
        <p class="subtitle">Dive into your schedule</p>
        
        {% if error %}
        <div class="error show">❌ {{ error }}</div>
        {% endif %}
        
        <form method="POST" action="/login">
            <div class="form-group">
                <label for="username">Username</label>
                <input type="text" id="username" name="username" required autofocus>
            </div>
            
            <div class="form-group">
                <label for="password">Password</label>
                <input type="password" id="password" name="password" required>
            </div>
            
            <button type="submit" class="btn-login">
                🔐 Login to Calendar
            </button>
        </form>
    </div>
    
    <svg class="waves" xmlns="http://www.w3.org/2000/svg" viewBox="0 24 150 28" preserveAspectRatio="none">
        <defs>
            <path id="wave" d="M-160 44c30 0 58-18 88-18s 58 18 88 18 58-18 88-18 58 18 88 18 v44h-352z" />
        </defs>
        <g>
            <use href="#wave" x="50" y="0" fill="rgba(255,255,255,0.3)" />
            <use href="#wave" x="50" y="3" fill="rgba(255,255,255,0.5)" />
            <use href="#wave" x="50" y="6" fill="rgba(255,255,255,0.7)" />
        </g>
    </svg>
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
        web.run_app(
            self.app,
            host=self.env_vars['APP_HOST'],
            port=int(self.env_vars['APP_PORT'])
        )


def main():
    """Main entry point"""
    try:
        # Load environment variables using def function
        env_vars = load_environment()
        
        # Create and run application
        app = SharkCalendarApp(env_vars)
        print(f"🦈 Shark Calendar starting on {env_vars['APP_HOST']}:{env_vars['APP_PORT']}")
        print(f"👤 Login with username: {env_vars['USER']}")
        app.run()
        
    except ValueError as e:
        print(f"❌ Configuration Error: {e}")
        print("\nPlease create a .env file with the following variables:")
        print("SUPABASE_URL=your_supabase_url")
        print("SUPABASE_KEY=your_supabase_key")
        print("USER=your_username")
        print("PASS=your_password")
        print("SECRET_KEY=your_secret_key_for_sessions")
        print("APP_HOST=0.0.0.0")
        print("APP_PORT=8080")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == '__main__':
    main()
