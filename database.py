import sqlite3
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import json
from contextlib import contextmanager

class Database:
    def __init__(self, db_path="university_bot.db"):
        self.db_path = db_path
        self.init_tables()
    
    @contextmanager
    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    
    def init_tables(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Users table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    timezone TEXT DEFAULT 'UTC',
                    date_format TEXT DEFAULT 'DD.MM.YYYY',
                    time_format TEXT DEFAULT '24h',
                    language TEXT DEFAULT 'ru',
                    subscription_type TEXT DEFAULT 'free',
                    subscription_expires_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Subjects table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS subjects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    name TEXT,
                    emoji TEXT DEFAULT '📚',
                    color TEXT DEFAULT '#3498db',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    UNIQUE(user_id, name)
                )
            ''')
            
            # Tasks table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    subject_id INTEGER,
                    title TEXT,
                    task_type TEXT,
                    description TEXT,
                    due_date DATE,
                    due_time TIME,
                    remind_before TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    completed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(user_id),
                    FOREIGN KEY (subject_id) REFERENCES subjects(id)
                )
            ''')
            
            # Notifications sent log
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications_sent (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    remind_type TEXT,
                    sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id),
                    UNIQUE(task_id, remind_type)
                )
            ''')
            
            # Task audit log
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS task_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER,
                    user_id INTEGER,
                    action TEXT,
                    old_value TEXT,
                    new_value TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (task_id) REFERENCES tasks(id)
                )
            ''')
            
            # User settings
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    aggregation_enabled INTEGER DEFAULT 1,
                    aggregation_hour INTEGER DEFAULT 20,
                    quiet_hours_start INTEGER DEFAULT 23,
                    quiet_hours_end INTEGER DEFAULT 8,
                    max_notifications_per_day INTEGER DEFAULT 5,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            ''')
            
            conn.commit()
    
    # User methods
    def get_or_create_user(self, user_id: int, username: str = None, first_name: str = None, last_name: str = None) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
            user = cursor.fetchone()
            
            if not user:
                cursor.execute('''
                    INSERT INTO users (user_id, username, first_name, last_name)
                    VALUES (?, ?, ?, ?)
                ''', (user_id, username, first_name, last_name))
                # Also create default settings
                cursor.execute('''
                    INSERT INTO user_settings (user_id) VALUES (?)
                ''', (user_id,))
                conn.commit()
                return self.get_or_create_user(user_id, username, first_name, last_name)
            
            return dict(user)
    
    def get_user_settings(self, user_id: int) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_settings WHERE user_id = ?", (user_id,))
            settings = cursor.fetchone()
            if not settings:
                cursor.execute("INSERT INTO user_settings (user_id) VALUES (?)", (user_id,))
                conn.commit()
                return {"user_id": user_id, "aggregation_enabled": 1, "aggregation_hour": 20,
                        "quiet_hours_start": 23, "quiet_hours_end": 8, "max_notifications_per_day": 5}
            return dict(settings)
    
    def update_user_setting(self, user_id: int, setting: str, value):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"UPDATE user_settings SET {setting} = ? WHERE user_id = ?", (value, user_id))
            conn.commit()
    
    # Subject methods
    def add_subject(self, user_id: int, name: str, emoji: str = "📚") -> int:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO subjects (user_id, name, emoji)
                VALUES (?, ?, ?)
            ''', (user_id, name, emoji))
            conn.commit()
            return cursor.lastrowid
    
    def get_user_subjects(self, user_id: int) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT s.*, 
                    COUNT(CASE WHEN t.status = 'pending' AND t.due_date >= date('now') THEN 1 END) as active_count,
                    COUNT(CASE WHEN t.status = 'pending' AND t.due_date < date('now') THEN 1 END) as overdue_count
                FROM subjects s
                LEFT JOIN tasks t ON t.subject_id = s.id AND t.status != 'completed'
                WHERE s.user_id = ?
                GROUP BY s.id
                ORDER BY s.name
            ''', (user_id,))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_subject(self, subject_id: int) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subjects WHERE id = ?", (subject_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_subject(self, subject_id: int, **kwargs):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for key, value in kwargs.items():
                cursor.execute(f"UPDATE subjects SET {key} = ? WHERE id = ?", (value, subject_id))
            conn.commit()
    
    def delete_subject(self, subject_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM tasks WHERE subject_id = ?", (subject_id,))
            cursor.execute("DELETE FROM subjects WHERE id = ?", (subject_id,))
            conn.commit()
    
    # Task methods
    def add_task(self, user_id: int, subject_id: int, title: str, task_type: str,
                 due_date: str, due_time: str = None, remind_before: List = None) -> int:
        if remind_before is None:
            remind_before = []
        
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT INTO tasks (user_id, subject_id, title, task_type, due_date, due_time, remind_before)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (user_id, subject_id, title, task_type, due_date, due_time, json.dumps(remind_before)))
            task_id = cursor.lastrowid
            
            # Log audit
            cursor.execute('''
                INSERT INTO task_audit (task_id, user_id, action, new_value)
                VALUES (?, ?, ?, ?)
            ''', (task_id, user_id, "created", json.dumps({"title": title, "due_date": due_date})))
            conn.commit()
            return task_id
    
    def get_user_tasks(self, user_id: int, status: str = None, subject_id: int = None) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            query = '''
                SELECT t.*, s.name as subject_name, s.emoji as subject_emoji
                FROM tasks t
                LEFT JOIN subjects s ON t.subject_id = s.id
                WHERE t.user_id = ?
            '''
            params = [user_id]
            
            if status:
                query += " AND t.status = ?"
                params.append(status)
            
            if subject_id:
                query += " AND t.subject_id = ?"
                params.append(subject_id)
            
            query += " ORDER BY t.due_date ASC, t.due_time ASC"
            
            cursor.execute(query, params)
            return [dict(row) for row in cursor.fetchall()]
    
    def get_task(self, task_id: int) -> Optional[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.*, s.name as subject_name, s.emoji as subject_emoji
                FROM tasks t
                LEFT JOIN subjects s ON t.subject_id = s.id
                WHERE t.id = ?
            ''', (task_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
    
    def update_task(self, task_id: int, **kwargs):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            old_task = self.get_task(task_id)
            
            for key, value in kwargs.items():
                if key == "remind_before" and isinstance(value, list):
                    value = json.dumps(value)
                cursor.execute(f"UPDATE tasks SET {key} = ? WHERE id = ?", (value, task_id))
            
            # Log audit
            if old_task:
                changes = {k: v for k, v in kwargs.items() if str(old_task.get(k)) != str(v)}
                if changes:
                    cursor.execute('''
                        INSERT INTO task_audit (task_id, user_id, action, old_value, new_value)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (task_id, old_task['user_id'], "updated", 
                          json.dumps({k: old_task.get(k) for k in changes.keys()}),
                          json.dumps(changes)))
            conn.commit()
    
    def complete_task(self, task_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE tasks SET status = 'completed', completed_at = CURRENT_TIMESTAMP
                WHERE id = ?
            ''', (task_id,))
            conn.commit()
    
    def delete_task(self, task_id: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            # Get user_id for audit
            task = self.get_task(task_id)
            if task:
                cursor.execute('''
                    INSERT INTO task_audit (task_id, user_id, action)
                    VALUES (?, ?, ?)
                ''', (task_id, task['user_id'], "deleted"))
            cursor.execute("DELETE FROM notifications_sent WHERE task_id = ?", (task_id,))
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            conn.commit()
    
    def postpone_task(self, task_id: int, days: int):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT due_date FROM tasks WHERE id = ?
            ''', (task_id,))
            row = cursor.fetchone()
            if row:
                old_date = row['due_date']
                new_date_obj = datetime.strptime(old_date, "%Y-%m-%d") + timedelta(days=days)
                new_date = new_date_obj.strftime("%Y-%m-%d")
                cursor.execute("UPDATE tasks SET due_date = ? WHERE id = ?", (new_date, task_id))
                # Clear sent notifications for this task
                cursor.execute("DELETE FROM notifications_sent WHERE task_id = ?", (task_id,))
                conn.commit()
                return new_date
        return None
    
    def get_tasks_for_notification(self) -> List[Dict]:
        """Get tasks that need notifications"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            today = datetime.now().date()
            
            cursor.execute('''
                SELECT t.*, s.name as subject_name, s.emoji as subject_emoji,
                       u.timezone, u.aggregation_enabled
                FROM tasks t
                LEFT JOIN subjects s ON t.subject_id = s.id
                LEFT JOIN users u ON t.user_id = u.user_id
                LEFT JOIN user_settings us ON t.user_id = us.user_id
                WHERE t.status = 'pending'
            ''')
            
            tasks = []
            for row in cursor.fetchall():
                task = dict(row)
                due_date = datetime.strptime(task['due_date'], "%Y-%m-%d").date()
                remind_before = json.loads(task['remind_before'] or '[]')
                
                for reminder in remind_before:
                    remind_type = f"{reminder['type']}_{reminder.get('days_before', reminder.get('hours_before', 0))}"
                    
                    # Check if already sent
                    cursor.execute('''
                        SELECT 1 FROM notifications_sent 
                        WHERE task_id = ? AND remind_type = ?
                    ''', (task['id'], remind_type))
                    if cursor.fetchone():
                        continue
                    
                    # Calculate reminder datetime
                    if reminder['type'] == 'day':
                        remind_date = due_date - timedelta(days=reminder['days_before'])
                        if remind_date == today:
                            tasks.append((task, remind_type))
                    elif reminder['type'] == 'hour':
                        if task['due_time']:
                            due_datetime = datetime.combine(due_date, datetime.strptime(task['due_time'], "%H:%M").time())
                            remind_datetime = due_datetime - timedelta(hours=reminder['hours_before'])
                            if datetime.now() >= remind_datetime:
                                tasks.append((task, remind_type))
            
            return tasks
    
    def mark_notification_sent(self, task_id: int, remind_type: str):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO notifications_sent (task_id, remind_type)
                VALUES (?, ?)
            ''', (task_id, remind_type))
            conn.commit()
    
    def get_tasks_by_date(self, user_id: int, date: str) -> List[Dict]:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT t.*, s.name as subject_name, s.emoji as subject_emoji
                FROM tasks t
                LEFT JOIN subjects s ON t.subject_id = s.id
                WHERE t.user_id = ? AND t.due_date = ? AND t.status = 'pending'
                ORDER BY t.due_time ASC
            ''', (user_id, date))
            return [dict(row) for row in cursor.fetchall()]
    
    def get_statistics(self, user_id: int) -> Dict:
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # Overall stats
            cursor.execute('''
                SELECT 
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                    COUNT(CASE WHEN status = 'pending' AND due_date < date('now') THEN 1 END) as overdue,
                    COUNT(CASE WHEN status = 'pending' AND due_date >= date('now') THEN 1 END) as active
                FROM tasks
                WHERE user_id = ?
            ''', (user_id,))
            overall = cursor.fetchone()
            
            # Stats by subject
            cursor.execute('''
                SELECT s.name, s.emoji,
                    COUNT(CASE WHEN t.status = 'completed' THEN 1 END) as completed,
                    COUNT(*) as total
                FROM subjects s
                LEFT JOIN tasks t ON t.subject_id = s.id
                WHERE s.user_id = ?
                GROUP BY s.id
            ''', (user_id,))
            by_subject = [dict(row) for row in cursor.fetchall()]
            
            # Stats by task type
            cursor.execute('''
                SELECT task_type,
                    COUNT(CASE WHEN status = 'completed' THEN 1 END) as completed,
                    COUNT(*) as total
                FROM tasks
                WHERE user_id = ?
                GROUP BY task_type
            ''', (user_id,))
            by_type = [dict(row) for row in cursor.fetchall()]
            
            return {
                "overall": dict(overall),
                "by_subject": by_subject,
                "by_type": by_type
            }

# Create global database instance
db = Database()