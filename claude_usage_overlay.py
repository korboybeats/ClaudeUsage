import tkinter as tk
from tkinter import ttk, messagebox
import json
import os
import requests
from datetime import datetime
from pathlib import Path
import threading
import time
import sys

class ClaudeUsageBar:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Claude Usage")
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)
        
        # Paths
        self.app_data_dir = Path(os.getenv('APPDATA')) / 'ClaudeUsageBar'
        self.app_data_dir.mkdir(exist_ok=True)
        self.config_file = self.app_data_dir / 'config.json'
        
        # Load config
        self.config = self.load_config()
        
        # State
        self.dragging = False
        self.drag_x = 0
        self.drag_y = 0
        self.usage_data = None
        self.polling_active = True
        self.driver = None
        self.login_in_progress = False
        self.settings_window = None
        self.clickthrough_enabled = False
        
        # Setup UI
        self.setup_ui()
        self.position_window()
        
        # Check if we have auth token
        if not self.config.get('session_key'):
            self.root.after(500, self.show_login_dialog)
        else:
            self.start_polling()
        
    def load_config(self):
        default = {
            'position': {'x': 20, 'y': 80},
            'opacity': 0.9,
            'session_key': None,
            'poll_interval': 60
        }
        
        if self.config_file.exists():
            try:
                with open(self.config_file, 'r') as f:
                    loaded = json.load(f)
                    return {**default, **loaded}
            except:
                pass
        
        return default
    
    def save_config(self):
        with open(self.config_file, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def show_login_dialog(self):
        """Show login dialog"""
        self.login_dialog = tk.Toplevel(self.root)
        self.login_dialog.title("Login Required")
        self.login_dialog.geometry("420x200")
        self.login_dialog.configure(bg='#1a1a1a')
        self.login_dialog.attributes('-topmost', True)
        self.login_dialog.protocol("WM_DELETE_WINDOW", self.on_login_dialog_close)
        
        # Center
        self.login_dialog.update_idletasks()
        x = (self.login_dialog.winfo_screenwidth() // 2) - 210
        y = (self.login_dialog.winfo_screenheight() // 2) - 100
        self.login_dialog.geometry(f'+{x}+{y}')
        
        tk.Label(
            self.login_dialog,
            text="🔐 Sign in to Claude",
            font=('Segoe UI', 16, 'bold'),
            fg='#CC785C',
            bg='#1a1a1a'
        ).pack(pady=(25, 10))
        
        self.status_label = tk.Label(
            self.login_dialog,
            text="A browser window will open for login",
            font=('Segoe UI', 9),
            fg='#999999',
            bg='#1a1a1a'
        )
        self.status_label.pack(pady=10)
        
        def start_login():
            if self.login_in_progress:
                return
                
            self.login_button.config(state='disabled', text="Opening browser...")
            self.status_label.config(text="Launching browser...", fg='#ffaa44')
            self.login_dialog.update()
            
            # Launch browser in background thread
            self.login_in_progress = True
            threading.Thread(
                target=self.automated_browser_login,
                daemon=True
            ).start()
        
        self.login_button = tk.Button(
            self.login_dialog,
            text="Sign In",
            command=start_login,
            bg='#CC785C',
            fg='#ffffff',
            font=('Segoe UI', 11, 'bold'),
            relief='flat',
            cursor='hand2',
            padx=50,
            pady=12
        )
        self.login_button.pack(pady=15)
        
        # Cancel button
        cancel_btn = tk.Button(
            self.login_dialog,
            text="Cancel",
            command=self.on_login_dialog_close,
            bg='#3a3a3a',
            fg='#cccccc',
            font=('Segoe UI', 9),
            relief='flat',
            cursor='hand2',
            padx=30,
            pady=6
        )
        cancel_btn.pack()
    
    def on_login_dialog_close(self):
        """Handle login dialog close"""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
            self.driver = None
        
        self.login_in_progress = False
        
        if hasattr(self, 'login_dialog'):
            try:
                self.login_dialog.destroy()
            except:
                pass
        
        # If no session key, quit the app
        if not self.config.get('session_key'):
            self.root.quit()
    
    def automated_browser_login(self):
        """Open browser with undetected-chromedriver to bypass Cloudflare"""
        try:
            # Import undetected_chromedriver
            try:
                import undetected_chromedriver as uc
            except ImportError:
                self.root.after(0, lambda: [
                    self.status_label.config(
                        text="Installing undetected-chromedriver...",
                        fg='#ffaa44'
                    )
                ])
                # Try to install it
                import subprocess
                subprocess.check_call([sys.executable, "-m", "pip", "install", "undetected-chromedriver"])
                import undetected_chromedriver as uc
            
            self.root.after(0, lambda: self.status_label.config(
                text="Starting browser (bypassing Cloudflare)...",
                fg='#ffaa44'
            ))
            
            # Create undetected Chrome driver
            options = uc.ChromeOptions()
            options.add_argument('--start-maximized')
            
            try:
                self.driver = uc.Chrome(options=options, use_subprocess=True)
            except Exception as e:
                self.root.after(0, lambda: [
                    self.status_label.config(
                        text=f"Browser error: {str(e)[:40]}",
                        fg='#ff4444'
                    ),
                    self.login_button.config(state='normal', text="Sign In")
                ])
                self.login_in_progress = False
                return
            
            # Navigate to Claude
            self.root.after(0, lambda: self.status_label.config(
                text="Please log in to claude.ai in the browser...",
                fg='#ffaa44'
            ))
            
            self.driver.get('https://claude.ai')
            
            # Give it a moment to load
            time.sleep(3)
            
            # Wait for user to log in
            session_key = None
            all_cookies = None
            max_wait = 300  # 5 minutes
            elapsed = 0
            
            while elapsed < max_wait and not session_key and self.login_in_progress:
                try:
                    # Check cookies
                    cookies = self.driver.get_cookies()
                    
                    for cookie in cookies:
                        if cookie['name'] == 'sessionKey':
                            session_key = cookie['value']
                            all_cookies = cookies  # Save ALL cookies
                            break
                    
                    if session_key:
                        break
                    
                    # Check if browser was closed by user
                    try:
                        url = self.driver.current_url
                    except:
                        break
                    
                    time.sleep(2)
                    elapsed += 2
                    
                except:
                    break
            
            # Close browser
            if self.driver:
                try:
                    self.driver.quit()
                except:
                    pass
                finally:
                    self.driver = None
            
            if session_key:
                # Success! Save session key AND all cookies
                self.config['session_key'] = session_key
                
                # Save all cookies as a cookie string
                if all_cookies:
                    cookie_string = '; '.join([f"{c['name']}={c['value']}" for c in all_cookies])
                    self.config['cookie_string'] = cookie_string
                
                self.save_config()
                
                self.root.after(0, lambda: [
                    self.status_label.config(text="✓ Login successful!", fg='#44ff44'),
                ])
                
                # Close dialog and start polling
                time.sleep(1)
                self.root.after(0, lambda: [
                    self.login_dialog.destroy() if hasattr(self, 'login_dialog') else None,
                    self.start_polling()
                ])
            else:
                # Timeout or closed
                self.root.after(0, lambda: [
                    self.status_label.config(text="Login cancelled or timeout. Try again.", fg='#ff4444'),
                    self.login_button.config(state='normal', text="Sign In")
                ])
            
            self.login_in_progress = False
        
        except Exception as e:
            if self.driver:
                try:
                    self.driver.quit()
                except:
                    pass
                self.driver = None
            
            self.root.after(0, lambda: [
                self.status_label.config(text=f"Error: {str(e)[:40]}", fg='#ff4444'),
                self.login_button.config(state='normal', text="Sign In")
            ])
            self.login_in_progress = False
    
    def fetch_usage_data(self):
        """Fetch usage data from Claude API using requests with cloudflare bypass"""
        if not self.config.get('session_key'):
            return None
            
        try:
            # Use cloudscraper to bypass Cloudflare
            try:
                import cloudscraper
            except ImportError:
                import subprocess
                import sys
                subprocess.check_call([sys.executable, "-m", "pip", "install", "cloudscraper"])
                import cloudscraper
            
            # Create a scraper that bypasses Cloudflare
            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'mobile': False
                }
            )
            
            # Use full cookie string if available
            cookie_string = self.config.get('cookie_string', f'sessionKey={self.config["session_key"]}')
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json',
                'Accept-Language': 'en-US,en;q=0.9',
                'Referer': 'https://claude.ai/chats',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                'sec-ch-ua-mobile': '?0',
                'sec-ch-ua-platform': '"Windows"',
            }
            
            # Set cookies
            for cookie_pair in cookie_string.split('; '):
                if '=' in cookie_pair:
                    name, value = cookie_pair.split('=', 1)
                    scraper.cookies.set(name, value, domain='claude.ai')
            
            # Get organizations
            response = scraper.get(
                'https://claude.ai/api/organizations',
                headers=headers,
                timeout=15
            )
            
            if response.status_code == 200:
                orgs = response.json()
                
                if orgs and len(orgs) > 0:
                    org_id = orgs[0].get('uuid')
                    
                    # Get usage
                    usage_response = scraper.get(
                        f'https://claude.ai/api/organizations/{org_id}/usage',
                        headers=headers,
                        timeout=15
                    )
                    
                    if usage_response.status_code == 200:
                        usage_data = usage_response.json()
                        return usage_data
            
            elif response.status_code == 401:
                self.root.after(0, self.handle_auth_error)
                return None
            
            return None
                
        except Exception as e:
            return None
    
    def handle_auth_error(self):
        """Handle authentication errors"""
        if messagebox.askyesno("Session Expired", 
                               "Your session has expired. Would you like to log in again?"):
            self.config['session_key'] = None
            self.save_config()
            self.show_login_dialog()
    
    def polling_loop(self):
        """Background thread for polling API"""
        while self.polling_active:
            data = self.fetch_usage_data()
            if data:
                self.usage_data = data
                self.root.after(0, self.update_progress)
            
            time.sleep(self.config['poll_interval'])
    
    def start_polling(self):
        """Start background polling thread"""
        self.polling_active = True
        poll_thread = threading.Thread(target=self.polling_loop, daemon=True)
        poll_thread.start()
        
        # Initial fetch
        def initial_fetch():
            time.sleep(0.5)
            data = self.fetch_usage_data()
            if data:
                self.usage_data = data
                self.root.after(0, self.update_progress)
        
        threading.Thread(target=initial_fetch, daemon=True).start()
    
    def format_time_remaining(self, time_left_seconds):
        """Format time remaining in a clear, readable way"""
        if time_left_seconds <= 0:
            return "Resetting soon..."
        
        hours = int(time_left_seconds // 3600)
        minutes = int((time_left_seconds % 3600) // 60)
        seconds = int(time_left_seconds % 60)
        
        # Format based on duration
        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {seconds}s"
        else:
            return f"{seconds}s"
    
    def setup_ui(self):
        self.main_frame = tk.Frame(
            self.root,
            bg='#1a1a1a',
            relief='flat',
            bd=0
        )
        self.main_frame.pack(fill='both', expand=True, padx=1, pady=1)
        
        self.root.configure(bg='#1a1a1a')
        
        # Header
        self.header = tk.Frame(self.main_frame, bg='#2a2a2a', height=28)
        self.header.pack(fill='x', padx=6, pady=(6, 0))
        self.header.pack_propagate(False)
        
        # Clickthrough toggle button (always interactive)
        self.clickthrough_btn = tk.Label(
            self.header,
            text="👆",
            font=('Segoe UI', 10),
            fg='#888888',
            bg='#2a2a2a',
            cursor='hand2',
            padx=4
        )
        self.clickthrough_btn.pack(side='left', padx=(4, 0))
        self.clickthrough_btn.bind('<Button-1>', self.toggle_clickthrough)
        self.clickthrough_btn.bind('<Enter>', self.on_clickthrough_hover)
        self.clickthrough_btn.bind('<Leave>', self.on_clickthrough_leave)
        
        # Tooltip for clickthrough
        self.clickthrough_tooltip = None
        
        self.title_label = tk.Label(
            self.header,
            text="Claude Usage",
            font=('Segoe UI', 9, 'bold'),
            fg='#CC785C',
            bg='#2a2a2a',
            cursor='hand2'
        )
        self.title_label.pack(side='left', padx=(4, 4), pady=4)
        
        # Dragging
        for widget in [self.header, self.title_label]:
            widget.bind('<Button-1>', self.start_drag)
            widget.bind('<B1-Motion>', self.on_drag)
            widget.bind('<ButtonRelease-1>', self.stop_drag)
        
        # Buttons
        self.btn_frame = tk.Frame(self.header, bg='#2a2a2a')
        self.btn_frame.pack(side='right')
        
        # Refresh
        self.refresh_btn = tk.Label(
            self.btn_frame,
            text="\u21BB", 
            font=('Segoe UI Symbol', 11, 'bold'),
            fg='#888888',
            bg='#2a2a2a',
            cursor='hand2',
            padx=4
        )
        self.refresh_btn.pack(side='left', padx=2)
        self.refresh_btn.bind('<Button-1>', self.manual_refresh)
        self.refresh_btn.bind('<Enter>', lambda e: self.on_icon_hover(self.refresh_btn, '#CC785C'))
        self.refresh_btn.bind('<Leave>', lambda e: self.on_icon_leave(self.refresh_btn, '#888888'))
        
        # Settings
        self.settings_btn = tk.Label(
            self.btn_frame,
            text="⚙",
            font=('Segoe UI', 10),
            fg='#888888',
            bg='#2a2a2a',
            cursor='hand2',
            padx=4
        )
        self.settings_btn.pack(side='left', padx=2)
        self.settings_btn.bind('<Button-1>', self.show_settings)
        self.settings_btn.bind('<Enter>', lambda e: self.on_icon_hover(self.settings_btn, '#ffffff'))
        self.settings_btn.bind('<Leave>', lambda e: self.on_icon_leave(self.settings_btn, '#888888'))
        
        # Close
        self.close_btn = tk.Label(
            self.btn_frame,
            text="×",
            font=('Segoe UI', 13, 'bold'),
            fg='#888888',
            bg='#2a2a2a',
            cursor='hand2',
            padx=4
        )
        self.close_btn.pack(side='left', padx=2)
        self.close_btn.bind('<Button-1>', self.on_close)
        self.close_btn.bind('<Enter>', lambda e: self.on_icon_hover(self.close_btn, '#ff4444'))
        self.close_btn.bind('<Leave>', lambda e: self.on_icon_leave(self.close_btn, '#888888'))
        
        # Content
        self.content_frame = tk.Frame(self.main_frame, bg='#1a1a1a')
        self.content_frame.pack(fill='x', padx=8, pady=8)
        
        # 5-Hour Usage section
        tk.Label(
            self.content_frame,
            text="5-Hour Limit",
            font=('Segoe UI', 8, 'bold'),
            fg='#888888',
            bg='#1a1a1a',
            anchor='w'
        ).pack(fill='x', pady=(0, 2))
        
        self.five_hour_usage_label = tk.Label(
            self.content_frame,
            text="Loading...",
            font=('Segoe UI', 9),
            fg='#cccccc',
            bg='#1a1a1a',
            anchor='w'
        )
        self.five_hour_usage_label.pack(fill='x', pady=(0, 2))
        
        # 5-Hour Progress bar
        five_hour_progress_bg = tk.Frame(self.content_frame, bg='#2a2a2a', height=12)
        five_hour_progress_bg.pack(fill='x', pady=(0, 2))
        five_hour_progress_bg.pack_propagate(False)
        
        self.five_hour_progress_fill = tk.Frame(five_hour_progress_bg, bg='#CC785C', height=12)
        self.five_hour_progress_fill.place(x=0, y=0, relheight=1, width=0)
        
        self.five_hour_reset_label = tk.Label(
            self.content_frame,
            text="Resets in: --",
            font=('Segoe UI', 7),
            fg='#666666',
            bg='#1a1a1a',
            anchor='w'
        )
        self.five_hour_reset_label.pack(fill='x', pady=(0, 10))
        
        # Separator
        self.separator = tk.Frame(self.content_frame, bg='#333333', height=1)
        self.separator.pack(fill='x', pady=(0, 8))
        
        # Weekly Usage section
        tk.Label(
            self.content_frame,
            text="Weekly Limit",
            font=('Segoe UI', 8, 'bold'),
            fg='#888888',
            bg='#1a1a1a',
            anchor='w'
        ).pack(fill='x', pady=(0, 2))
        
        self.weekly_usage_label = tk.Label(
            self.content_frame,
            text="Loading...",
            font=('Segoe UI', 9),
            fg='#cccccc',
            bg='#1a1a1a',
            anchor='w'
        )
        self.weekly_usage_label.pack(fill='x', pady=(0, 2))
        
        # Weekly Progress bar
        weekly_progress_bg = tk.Frame(self.content_frame, bg='#2a2a2a', height=12)
        weekly_progress_bg.pack(fill='x', pady=(0, 2))
        weekly_progress_bg.pack_propagate(False)
        
        self.weekly_progress_fill = tk.Frame(weekly_progress_bg, bg='#8B6BB7', height=12)
        self.weekly_progress_fill.place(x=0, y=0, relheight=1, width=0)
        
        self.weekly_reset_label = tk.Label(
            self.content_frame,
            text="Resets in: --",
            font=('Segoe UI', 7),
            fg='#666666',
            bg='#1a1a1a',
            anchor='w'
        )
        self.weekly_reset_label.pack(fill='x')
        
        # Set opacity
        self.root.attributes('-alpha', self.config['opacity'])
        self.root.geometry('300x240')

    def on_icon_hover(self, widget, active_color):
        """Standard hover animation, disabled if clickthrough is on"""
        if not self.clickthrough_enabled:
            widget.config(fg=active_color)

    def on_icon_leave(self, widget, default_color):
        """Standard leave animation, disabled if clickthrough is on"""
        if not self.clickthrough_enabled:
            widget.config(fg=default_color)
    
    def start_drag(self, event):
        if not self.clickthrough_enabled:
            self.dragging = True
            self.drag_x = event.x_root - self.root.winfo_x()
            self.drag_y = event.y_root - self.root.winfo_y()
    
    def on_drag(self, event):
        if self.dragging:
            x = event.x_root - self.drag_x
            y = event.y_root - self.drag_y
            self.root.geometry(f'+{x}+{y}')
    
    def stop_drag(self, event):
        if self.dragging:
            self.dragging = False
            self.config['position']['x'] = self.root.winfo_x()
            self.config['position']['y'] = self.root.winfo_y()
            self.save_config()
    
    def position_window(self):
        self.root.update_idletasks()
        x = self.config['position']['x']
        y = self.config['position']['y']
        self.root.geometry(f'+{x}+{y}')
    
    def update_progress(self):
        """Update UI with latest usage data"""
        if not self.usage_data:
            return
        
        try:
            try:
                from dateutil import parser as date_parser
            except ImportError:
                import subprocess
                subprocess.check_call([sys.executable, "-m", "pip", "install", "python-dateutil"])
                from dateutil import parser as date_parser
            
            # Extract 5-hour usage
            five_hour = self.usage_data.get('five_hour', {})
            five_hour_utilization = five_hour.get('utilization', 0.0)
            five_hour_resets_at = five_hour.get('resets_at')
            
            # Display 5-hour usage
            self.five_hour_usage_label.config(text=f"{five_hour_utilization:.1f}% used")
            
            # Update 5-hour progress bar
            bar_width = int((five_hour_utilization / 100) * 284)
            self.five_hour_progress_fill.place(width=bar_width)
            
            # Color based on usage for 5-hour
            if five_hour_utilization >= 90:
                self.five_hour_progress_fill.config(bg='#ff4444')
            elif five_hour_utilization >= 70:
                self.five_hour_progress_fill.config(bg='#ffaa44')
            else:
                self.five_hour_progress_fill.config(bg='#CC785C')
            
            # Update 5-hour reset timer
            if five_hour_resets_at:
                try:
                    reset_time = date_parser.parse(five_hour_resets_at)
                    now = datetime.now(reset_time.tzinfo)
                    time_left = reset_time - now
                    
                    if time_left.total_seconds() > 0:
                        time_str = self.format_time_remaining(time_left.total_seconds())
                        self.five_hour_reset_label.config(text=f"Resets in: {time_str}")
                    else:
                        self.five_hour_reset_label.config(text="Resetting soon...")
                except:
                    self.five_hour_reset_label.config(text="Reset time error")
            else:
                if five_hour_utilization == 0:
                    self.five_hour_reset_label.config(text="No active period")
                else:
                    self.five_hour_reset_label.config(text="Reset time unavailable")
            
            # Extract weekly usage (note: API uses 'seven_day' not 'weekly')
            weekly = self.usage_data.get('seven_day', {})
            weekly_utilization = weekly.get('utilization', 0.0)
            weekly_resets_at = weekly.get('resets_at')
            
            # Display weekly usage
            self.weekly_usage_label.config(text=f"{weekly_utilization:.1f}% used")
            
            # Update weekly progress bar
            weekly_bar_width = int((weekly_utilization / 100) * 284)
            self.weekly_progress_fill.place(width=weekly_bar_width)
            
            # Color based on usage for weekly
            if weekly_utilization >= 90:
                self.weekly_progress_fill.config(bg='#ff4444')
            elif weekly_utilization >= 70:
                self.weekly_progress_fill.config(bg='#ffaa44')
            else:
                self.weekly_progress_fill.config(bg='#8B6BB7')
            
            # Update weekly reset timer
            if weekly_resets_at:
                try:
                    reset_time = date_parser.parse(weekly_resets_at)
                    now = datetime.now(reset_time.tzinfo)
                    time_left = reset_time - now
                    
                    if time_left.total_seconds() > 0:
                        time_str = self.format_time_remaining(time_left.total_seconds())
                        self.weekly_reset_label.config(text=f"Resets in: {time_str}")
                    else:
                        self.weekly_reset_label.config(text="Resetting soon...")
                except:
                    self.weekly_reset_label.config(text="Reset time error")
            else:
                if weekly_utilization == 0:
                    self.weekly_reset_label.config(text="No active period")
                else:
                    self.weekly_reset_label.config(text="Reset time unavailable")
                
        except Exception as e:
            self.five_hour_usage_label.config(text="Error displaying usage")
            self.weekly_usage_label.config(text="Error displaying usage")
        
        # Schedule next update
        self.root.after(1000, self.update_progress)
    
    def manual_refresh(self, event=None):
        """Manually trigger refresh"""
        if self.clickthrough_enabled: return
        def refresh():
            data = self.fetch_usage_data()
            if data:
                self.usage_data = data
                self.root.after(0, self.update_progress)
        
        threading.Thread(target=refresh, daemon=True).start()
    
    def show_settings(self, event=None):
        if self.clickthrough_enabled: return
        # Don't open multiple settings windows
        if self.settings_window and tk.Toplevel.winfo_exists(self.settings_window):
            self.settings_window.lift()
            self.settings_window.focus_force()
            return
        
        self.settings_window = tk.Toplevel(self.root)
        self.settings_window.title("Settings")
        self.settings_window.geometry("400x400")
        self.settings_window.attributes('-topmost', True)
        self.settings_window.configure(bg='#1a1a1a')
        self.settings_window.protocol("WM_DELETE_WINDOW", lambda: self.close_settings())
        
        # Account info
        tk.Label(
            self.settings_window,
            text="Account",
            font=('Segoe UI', 10, 'bold'),
            fg='#CC785C',
            bg='#1a1a1a'
        ).pack(pady=(20, 5))
        
        # Show session key snippet
        session_key = self.config.get('session_key', 'Not logged in')
        display_key = f"{session_key[:15]}..." if len(session_key) > 15 else session_key
        
        tk.Label(
            self.settings_window,
            text=f"Session: {display_key}",
            font=('Segoe UI', 8),
            fg='#666666',
            bg='#1a1a1a'
        ).pack(pady=(0, 5))
        
        # Separator
        separator1 = tk.Frame(self.settings_window, bg='#333333', height=1)
        separator1.pack(fill='x', padx=20, pady=15)
        
        # Opacity
        tk.Label(
            self.settings_window,
            text="Window Opacity",
            font=('Segoe UI', 9, 'bold'),
            fg='#cccccc',
            bg='#1a1a1a'
        ).pack(pady=(5, 5))
        
        opacity_frame = tk.Frame(self.settings_window, bg='#1a1a1a')
        opacity_frame.pack(pady=5)
        
        opacity_var = tk.DoubleVar(value=self.config['opacity'])
        opacity_value_label = tk.Label(
            opacity_frame,
            text=f"{int(opacity_var.get() * 100)}%",
            font=('Segoe UI', 9),
            fg='#888888',
            bg='#1a1a1a',
            width=5
        )
        opacity_value_label.pack(side='right', padx=(10, 0))
        
        def update_opacity_label(val):
            opacity_value_label.config(text=f"{int(float(val) * 100)}%")
            self.root.attributes('-alpha', float(val))
        
        opacity_slider = tk.Scale(
            opacity_frame,
            from_=0.3,
            to=1.0,
            resolution=0.05,
            variable=opacity_var,
            orient='horizontal',
            length=250,
            command=update_opacity_label,
            bg='#2a2a2a',
            fg='#CC785C',
            highlightthickness=0,
            troughcolor='#1a1a1a',
            activebackground='#CC785C',
            showvalue=0,
            sliderrelief='flat',
            width=15
        )
        opacity_slider.pack(side='left')
        
        # Separator
        separator2 = tk.Frame(self.settings_window, bg='#333333', height=1)
        separator2.pack(fill='x', padx=20, pady=15)
        
        # Poll interval with better UI
        tk.Label(
            self.settings_window,
            text="Update Interval",
            font=('Segoe UI', 9, 'bold'),
            fg='#cccccc',
            bg='#1a1a1a'
        ).pack(pady=(5, 5))
        
        interval_frame = tk.Frame(self.settings_window, bg='#1a1a1a')
        interval_frame.pack(pady=5)
        
        interval_var = tk.IntVar(value=self.config['poll_interval'])
        
        # Minus button
        def decrease_interval():
            current = interval_var.get()
            if current > 10:
                interval_var.set(current - 10)
        
        minus_btn = tk.Button(
            interval_frame,
            text="−",
            command=decrease_interval,
            bg='#3a3a3a',
            fg='#ffffff',
            font=('Segoe UI', 14, 'bold'),
            relief='flat',
            cursor='hand2',
            width=3,
            height=1
        )
        minus_btn.pack(side='left', padx=5)
        minus_btn.bind('<Enter>', lambda e: minus_btn.config(bg='#4a4a4a'))
        minus_btn.bind('<Leave>', lambda e: minus_btn.config(bg='#3a3a3a'))
        
        # Display value
        interval_display = tk.Label(
            interval_frame,
            textvariable=interval_var,
            font=('Segoe UI', 12, 'bold'),
            fg='#CC785C',
            bg='#2a2a2a',
            width=8,
            relief='flat',
            padx=10,
            pady=5
        )
        interval_display.pack(side='left', padx=5)
        
        # Plus button
        def increase_interval():
            current = interval_var.get()
            if current < 300:
                interval_var.set(current + 10)
        
        plus_btn = tk.Button(
            interval_frame,
            text="+",
            command=increase_interval,
            bg='#3a3a3a',
            fg='#ffffff',
            font=('Segoe UI', 14, 'bold'),
            relief='flat',
            cursor='hand2',
            width=3,
            height=1
        )
        plus_btn.pack(side='left', padx=5)
        plus_btn.bind('<Enter>', lambda e: plus_btn.config(bg='#4a4a4a'))
        plus_btn.bind('<Leave>', lambda e: plus_btn.config(bg='#3a3a3a'))
        
        tk.Label(
            self.settings_window,
            text="seconds",
            font=('Segoe UI', 8),
            fg='#666666',
            bg='#1a1a1a'
        ).pack(pady=(0, 10))
        
        # Save button
        def save_settings():
            self.config['opacity'] = opacity_var.get()
            self.config['poll_interval'] = interval_var.get()
            self.save_config()
            self.close_settings()
        
        tk.Button(
            self.settings_window,
            text="✓ Save Settings",
            command=save_settings,
            bg='#CC785C',
            fg='#ffffff',
            relief='flat',
            font=('Segoe UI', 10, 'bold'),
            cursor='hand2',
            padx=30,
            pady=10
        ).pack(pady=15)
        
        # Logout
        def logout():
            if messagebox.askyesno("Logout", "Log out and clear session?", parent=self.settings_window):
                self.config['session_key'] = None
                self.config['cookie_string'] = None
                self.save_config()
                self.close_settings()
                messagebox.showinfo("Logged Out", "Please restart the app to log in again.")
                self.root.quit()
        
        logout_btn = tk.Button(
            self.settings_window,
            text="🚪 Logout & Clear Session",
            command=logout,
            bg='#3a3a3a',
            fg='#ff8888',
            relief='flat',
            font=('Segoe UI', 9),
            cursor='hand2',
            padx=20,
            pady=8
        )
        logout_btn.pack()
        logout_btn.bind('<Enter>', lambda e: logout_btn.config(bg='#4a3a3a'))
        logout_btn.bind('<Leave>', lambda e: logout_btn.config(bg='#3a3a3a'))
    
    def close_settings(self):
        if self.settings_window:
            try:
                self.settings_window.destroy()
            except:
                pass
            self.settings_window = None
    
    def toggle_clickthrough(self, event=None):
        """Toggle clickthrough mode - makes EVERYTHING clickthrough except the icon itself"""
        self.clickthrough_enabled = not self.clickthrough_enabled
        
        if self.clickthrough_enabled:
            # Change the button color to something UNIQUE (not used anywhere else)
            self.clickthrough_btn.config(bg='#2b2b2b', fg='#44ff44')
            
            # Change cursors for all non-interactive icons to standard arrow
            for icon in [self.refresh_btn, self.settings_btn, self.close_btn, self.title_label]:
                icon.config(cursor='arrow')
            
            # Make the main colors clickthrough
            self.header.config(bg='#1a1a1a')
            self.btn_frame.config(bg='#1a1a1a')
            self.title_label.config(bg='#1a1a1a')
            self.refresh_btn.config(bg='#1a1a1a')
            self.settings_btn.config(bg='#1a1a1a')
            self.close_btn.config(bg='#1a1a1a')
            
            # Now set the whole window's transparent color to the main background color
            self.root.wm_attributes('-transparentcolor', '#1a1a1a')
        else:
            # Restore cursors to hand
            for icon in [self.refresh_btn, self.settings_btn, self.close_btn, self.title_label]:
                icon.config(cursor='hand2')
                
            # Restore original colors and remove transparency
            self.clickthrough_btn.config(bg='#2a2a2a', fg='#888888')
            self.header.config(bg='#2a2a2a')
            self.btn_frame.config(bg='#2a2a2a')
            self.title_label.config(bg='#2a2a2a')
            self.refresh_btn.config(bg='#2a2a2a')
            self.settings_btn.config(bg='#2a2a2a')
            self.close_btn.config(bg='#2a2a2a')
            
            self.root.wm_attributes('-transparentcolor', '')
    
    def on_clickthrough_hover(self, event):
        """Show tooltip on hover"""
        if self.clickthrough_enabled:
            tooltip_text = "Disable clickthrough"
            self.clickthrough_btn.config(fg='#66ff66')
        else:
            tooltip_text = "Enable clickthrough"
            self.clickthrough_btn.config(fg='#ffffff')
        
        # Create tooltip
        if not self.clickthrough_tooltip:
            self.clickthrough_tooltip = tk.Toplevel(self.root)
            self.clickthrough_tooltip.wm_overrideredirect(True)
            self.clickthrough_tooltip.wm_attributes('-topmost', True)
            
            label = tk.Label(
                self.clickthrough_tooltip,
                text=tooltip_text,
                bg='#3a3a3a',
                fg='#ffffff',
                font=('Segoe UI', 8),
                padx=8,
                pady=4,
                relief='solid',
                borderwidth=1
            )
            label.pack()
            
            # Position below the button
            x = self.clickthrough_btn.winfo_rootx()
            y = self.clickthrough_btn.winfo_rooty() + self.clickthrough_btn.winfo_height() + 2
            self.clickthrough_tooltip.wm_geometry(f"+{x}+{y}")
    
    def on_clickthrough_leave(self, event):
        """Hide tooltip on leave"""
        if self.clickthrough_tooltip:
            self.clickthrough_tooltip.destroy()
            self.clickthrough_tooltip = None
        
        if self.clickthrough_enabled:
            self.clickthrough_btn.config(fg='#44ff44')
        else:
            self.clickthrough_btn.config(fg='#888888')
    
    def on_close(self, event=None):
        if self.clickthrough_enabled: return
        self.polling_active = False
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass
        self.root.quit()
    
    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    app = ClaudeUsageBar()
    app.run()