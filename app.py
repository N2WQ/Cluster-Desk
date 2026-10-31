"""Light desktop interface for the CC User replacement, currently in development."""
import csv
from copy import deepcopy
import json
import os
import sys
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP
from pathlib import Path
import queue
import re
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import ttk, messagebox, filedialog, simpledialog

from protocol import Connection, TrackedCommand, CommandRateLimitError, SETTING_COMMANDS, SETTING_LABELS, SPIDER_SETTING_COMMANDS, parse_setting, parse_spider_setting, parse_spot, setting_command, spot_command, history_command, frequency_history_command, older_filtered_spots_command, spider_history_text_command, spider_history_country_command, cc_history_comment_command, cc_history_spotter_command
from logger_bridge import LoggerBridge, TEXT_CATEGORIES, load_text_categories
from bands import BANDS, SPOT_BANDS, band_for
from serial_bridge import SerialBridge, list_serial_ports
from settings_store import save_settings, validate_settings_depth
from country_lookup import CountryIndex
from lotw_activity import read_activity, download_activity
from eqsl_activity import read_activity as read_eqsl_activity, download_activity as download_eqsl_activity
from filter_commands import filter_command, CC_FILTERS, CC_PER_BAND_CATEGORIES, CC_FILTER_BANDS, CC_DXBM_SEGMENTS, CC_DXBM_RANGES, SPIDER_FILTERS, SPIDER_STREAMS, SPIDER_PICKER_BANDS, spider_categories, spider_expression
from reports import ReportCollector
from announcements import parse_announcement, parse_talk, talk_sender, message_commands, is_weather, SPIDER_HISTORY
from directory import DirectoryCapture
from spot_log import SpotLog
from spot_archive import search_archive, search_recordings, search_ve7cc_archive, date_range, record_in_date_range, spot_from_archive
from alarms import compile_rules, matches, resolve_country_prefix
from nodes import load_nodes, load_startup_nodes, validate_node, validate_login_call
from talk_lists import load_talk_lists, validate_talk_list, MAX_LISTS
from filter_readback import FilterReadback, is_filter_change
from cluster_dates import parse_cluster_date
from us_states import US_STATES, CANADIAN_PROVINCES
from build_info import packaged_executable, executable_sha256
from cluster_mail import MailDirectory, MailRead, NODE_PROMPT, directory_command, directory_fields, directory_reply_draft, read_command, split_prompt_prefix
from station import StationCapture, node_location, profile_command, qrz_url
from chat import GroupReadback, group_command, parse_chat, parse_group_change
from locator import coordinate_path, grid_center, grid_path
from user_settings import SettingsReadback, RBN_FIELDS, skimmer_command

ROOT = Path(__file__).resolve().parent


def cluster_line_too_long(line):
    """Exclude a possible CR line terminator from the content limit."""
    return len(line) - int(line.endswith('\r')) > 8192


def search_band_label(text):
    """Resolve a full band label with or without spaces, not a bare number."""
    compact=''.join(text.casefold().split())
    return next((name for name,_,_ in BANDS if ''.join(name.casefold().split())==compact),None)


def settings_path(frozen=None):
    if frozen is None: frozen = getattr(sys, 'frozen', False)
    if not frozen: return ROOT / 'settings.json'
    local = os.environ.get('LOCALAPPDATA')
    return (Path(local) if local else Path.home() / 'AppData' / 'Local') / 'ClusterDesk' / 'settings.json'


SETTINGS = settings_path()
BG = '#f2f5f7'
WHITE = '#ffffff'
INK = '#1d3039'
MUTED = '#596e78'
ACCENT = '#077a74'
BAND_COLORS = dict(zip((band[0] for band in BANDS), (
    '#f5edf2', '#f7eee7', '#f5f2df', '#eef4e5', '#e5f3e8', '#e5f3f1',
    '#e8f1f8', '#eaedf9', '#f0ebf8', '#f7eaf2', '#f8eee9', '#f3f3e8', '#eff2e4',
    '#e5f4ef', '#ecf1fb', '#f6edf7')))
DARK_BAND_COLORS = dict(zip((band[0] for band in BANDS), (
    '#332631', '#382b25', '#333021', '#293126', '#24342d', '#203432',
    '#22313d', '#292e40', '#302a3e', '#382a38', '#392d29', '#313329', '#2b3328',
    '#213630', '#282f40', '#362b3d')))

def spot_order(spot, now=None):
    try:
        if not spot.date:
            now = now or datetime.now(timezone.utc).replace(tzinfo=None)
            stamp = datetime.strptime(spot.time.rstrip('Zz'), '%H%M')
            value = now.replace(hour=stamp.hour, minute=stamp.minute, second=0, microsecond=0)
            if value > now + timedelta(hours=12): return value - timedelta(days=1)
            # A node a few minutes ahead may already have crossed midnight.
            # Do not move older undated history almost a day into the future.
            if value < now - timedelta(hours=12):
                tomorrow = value + timedelta(days=1)
                if tomorrow <= now + timedelta(minutes=5): return tomorrow
            return value
        day = parse_cluster_date(spot.date)
        stamp = datetime.strptime(spot.time.rstrip('Zz'), '%H%M')
        return datetime.combine(day, stamp.time())
    except ValueError:
        return datetime.min


MONTH_ABBREVIATIONS = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec')


def format_utc_time(value):
    value=value.strip()
    return value[:2]+':'+value[2:4]+'Z' if re.fullmatch(r'[0-9]{4}[Zz]?',value) else value


def spot_time_label(spot):
    return format_utc_time(spot.time)


def format_spot_frequency(value):
    try:
        number=Decimal(value)
        if not number.is_finite():return value
        with localcontext() as context:
            context.rounding=ROUND_HALF_UP
            return format(number,'.2f')
    except (InvalidOperation,ValueError):
        return value


def spot_day_time(spot):
    try:
        date = parse_cluster_date(spot.date) if spot.date else None
        day = f'{MONTH_ABBREVIATIONS[date.month-1]} {date.day:02d}' if date else '--'
    except ValueError:
        day = '--'
    return f'{day} {spot_time_label(spot)}'


class NodeStateDialog(simpledialog.Dialog):
    """A theme-aware callsign prompt for the read-only node lookup."""
    def __init__(self,parent,bg):
        self.background=bg
        super().__init__(parent,title='Node state lookup')

    def body(self,master):
        self.configure(background=self.background);master.configure(background=self.background)
        ttk.Label(master,text='Look up node state',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(master,text='Enter a callsign, for example W1AW. The node response appears in Activity.',style='Muted.TLabel',wraplength=430).pack(anchor='w',pady=(6,10))
        self.entry=ttk.Entry(master,width=42);self.entry.pack(fill='x')
        self.error=tk.StringVar(master=self,value='')
        ttk.Label(master,textvariable=self.error,style='SettingsWarning.TLabel',wraplength=430).pack(anchor='w',pady=(6,0))
        return self.entry

    def buttonbox(self):
        buttons=ttk.Frame(self,padding=(5,0,5,10));buttons.pack(fill='x')
        ttk.Button(buttons,text='Cancel',command=self.cancel).pack(side='right')
        ttk.Button(buttons,text='Look up',style='Accent.TButton',command=self.ok).pack(side='right',padx=(0,8))
        self.bind('<Return>',self.ok);self.bind('<Escape>',self.cancel)

    def apply(self):
        self.result=self.entry.get()

    def validate(self):
        call=self.entry.get().strip()
        if not call.isascii() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/-]{0,31}',call):
            self.error.set('Enter a callsign using letters, numbers, / or -.')
            return False
        self.error.set('');return True


def ask_node_state_call(parent,bg):
    return NodeStateDialog(parent,bg).result


class ButtonHint:
    """Show an icon button's name on hover or keyboard focus."""
    def __init__(self, widget, text, palette):
        self.widget=widget;self.text=text;self.palette=palette;self.timer=None;self.popup=None
        for event in ('<Enter>','<FocusIn>'):widget.bind(event,self.schedule,add='+')
        for event in ('<Leave>','<FocusOut>','<ButtonPress>','<Destroy>'):widget.bind(event,self.hide,add='+')

    def schedule(self, _event=None):
        self.hide();self.timer=self.widget.after(500,self.show)

    def show(self):
        self.timer=None
        if not self.widget.winfo_exists():return
        background,foreground=self.palette()
        self.popup=tk.Toplevel(self.widget);self.popup.overrideredirect(True)
        tk.Label(self.popup,text=self.text(),background=background,foreground=foreground,
                 relief='solid',borderwidth=1,padx=8,pady=4,font=('Segoe UI',9)).pack()
        self.popup.update_idletasks()
        x=min(self.widget.winfo_rootx(),self.widget.winfo_screenwidth()-self.popup.winfo_reqwidth())
        y=self.widget.winfo_rooty()-self.popup.winfo_reqheight()-5
        if y<0:y=self.widget.winfo_rooty()+self.widget.winfo_height()+5
        self.popup.geometry(f'+{max(0,x)}+{max(0,y)}')

    def hide(self, _event=None):
        if self.timer is not None:
            self.widget.after_cancel(self.timer);self.timer=None
        if self.popup is not None:
            self.popup.destroy();self.popup=None


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._set_palette(False)
        self.title('Cluster Desk by N2WQ')
        try: self.iconbitmap(default=str(ROOT / 'assets' / 'clusterdesk.ico'))
        except tk.TclError: pass
        self.geometry('960x760'); self.minsize(780, 560)
        self.configure(bg=self.theme_bg)
        self.events = queue.Queue(maxsize=2048)
        self.secondary_events = queue.Queue(maxsize=1024); self.secondary_nodes = {}; self.secondary_next_id = 0; self.secondary_window = None
        self.logger_events = queue.Queue(maxsize=256); self.logger = None; self.logger_overflow = None; self.logger_target = None
        self.serial_events=queue.Queue(maxsize=256);self.serial_logger=None;self.serial_overflow=None;self.serial_window=None
        self.country_events=queue.Queue(maxsize=1);self.country_loading=False;self.country_generation=0;self.country_index=None
        self.country_status=tk.StringVar(value='No country file loaded')
        self.lotw_events=queue.Queue(maxsize=1);self.lotw_loading=False;self.lotw_generation=0;self.lotw_index=None
        self.lotw_status=tk.StringVar(value='No LoTW activity list loaded')
        self.eqsl_events=queue.Queue(maxsize=1);self.eqsl_loading=False;self.eqsl_generation=0;self.eqsl_index=None
        self.eqsl_status=tk.StringVar(value='No eQSL AG list loaded')
        self.serial_scan_events=queue.Queue(maxsize=1);self.serial_scan_pending=False
        self.spot_log = None; self.spot_log_visible_path = None; self.closing = False
        self.connection = None; self.generation = 0; self.partial = ''; self.discarding_long_line = False; self.connection_overflow = None
        self.command_history=deque(maxlen=50);self.command_history_index=None;self.command_history_draft='';self.command_history_navigating=False
        self.pending_manual_commands={};self.pending_spot_submissions={}
        self.password_dialog = None
        self.want_connection = False; self.retry_after = None; self.retry_attempt = 0; self.retry_streak = 0; self.connected_since = None; self.connection_target = None
        self.dialect = ''; self.node_settings = {}; self.last_error = ''
        self.rows = deque(maxlen=1000); self.spot_times = {}; self.spot_sources = {}; self.next_spot_id = 0; self.spot_source = None
        self.manual_reconnect = False; self.recent_connected_sources=deque(maxlen=50)
        self.spot_sort_column = 'time'; self.spot_sort_descending = True
        self._spot_render_tags = {}
        self.reports = deque(maxlen=200); self.report_collector = ReportCollector()
        self.spot_wwv_summary = tk.StringVar(value='WWV · No report received yet')
        self.announcements = deque(maxlen=200)
        self.talk_alerts=deque(maxlen=50);self.talk_alert_window=None
        self.unread_counts={'messages':0,'reports':0,'alarms':0};self._unread_pending=None
        self.chat_groups = set()
        self.chat_readback = None; self.chat_readback_deadline = None; self.chat_window = None
        self.settings_readback = None; self.settings_deadline = None
        self.alarm_rows = deque(maxlen=200); self.alarm_details = {}; self.last_alarm_sound = 0.0;self.alarm_unsaved=False;self.display_unsaved=False
        self.directory = DirectoryCapture()
        self.filter_readback = FilterReadback(); self.filter_window = None; self.filter_deadline = None; self.filter_readback_unresolved = False
        self.mail_directory = MailDirectory(); self.mail_window = None; self.mail_deadline = None
        self.mail_reader = None; self.mail_read = None; self.mail_read_deadline = None
        self.station_window = None; self.station_capture = None; self.station_deadline = None; self.station_target = None
        self.mail_compose_pending = False
        self._load_settings();self._set_palette(self.dark_theme.get());self.configure(background=self.theme_bg);self._local_settings_baseline=deepcopy(self._settings_snapshot())
        self.logger_route_epoch=0;self.logger_route.trace_add('write',self.logger_route_changed)
        self._styles();self._build()
        if self.settings_load_problem:
            self.append(f'\nSettings could not be loaded from {SETTINGS}: {self.settings_load_problem}. Defaults are in use; check the file before saving.\n')
        if self.skipped_startup_nodes:
            self.append(f'\n{self.skipped_startup_nodes} saved additional node(s) were not loaded because entries were invalid, duplicated, or exceeded the limit. Review Additional nodes.\n')
        if self.alarm_load_error:
            self.append(f'\nSaved alarm disabled: {self.alarm_load_error} Review the Alarms tab and apply corrected settings.\n')
        if self.country_file:self.start_country_load(self.country_file,persist=False)
        if self.lotw_file:self.start_lotw_load(self.lotw_file,persist=False)
        if self.eqsl_file:self.start_eqsl_load(self.eqsl_file,persist=False)
        self._compact_layout=None; self._spot_side_layout=None; self.bind('<Configure>',self.adjust_layout,add='+')
        self.protocol('WM_DELETE_WINDOW', self.quit_app)
        self.poll_after = self.after(50, self.poll)
        self.auto_start_after=self.after_idle(self.start_saved_connection) if self.auto_start.get() else None
        self.secondary_start_after=self.after_idle(self.start_saved_secondary_nodes) if self.startup_secondary_nodes else None

    def _load_settings(self):
        self._settings_file_seen=SETTINGS.exists()
        self.settings_load_problem=''
        try:
            settings = json.loads(SETTINGS.read_text(encoding='utf-8'))
            self._settings_file_seen=True
            if not isinstance(settings, dict): raise ValueError('Expected a settings object')
            validate_settings_depth(settings)
        except FileNotFoundError: settings = {}
        except (OSError, ValueError, RecursionError) as exc:
            self.settings_load_problem=str(exc) or 'Invalid settings file'
            settings = {}
        self._disk_settings_baseline=deepcopy(settings)
        self.host = tk.StringVar(value=str(settings.get('host', 'dxc.n2wq.com')))
        self.port = tk.StringVar(value=str(settings.get('port', '7300')))
        self.call = tk.StringVar(value=str(settings.get('call', '')))
        saved_grid=settings.get('local_grid','')
        if not isinstance(saved_grid,str): saved_grid=''
        try:
            if saved_grid: grid_center(saved_grid)
        except ValueError: saved_grid=''
        self.local_grid=tk.StringVar(value=saved_grid.strip().upper())
        self.saved_nodes = load_nodes(settings.get('nodes'))
        startup_nodes=settings.get('startup_secondary_nodes',[])
        self.startup_secondary_nodes = load_startup_nodes(startup_nodes)
        self.skipped_startup_nodes = max(0,len(startup_nodes)-len(self.startup_secondary_nodes)) if isinstance(startup_nodes,list) else int(startup_nodes is not None)
        self.talk_lists=load_talk_lists(settings.get('talk_lists'))
        country_file=settings.get('country_file','')
        self.country_file=country_file if isinstance(country_file,str) and len(country_file)<=2048 and '\x00' not in country_file else ''
        lotw_file=settings.get('lotw_file','')
        self.lotw_file=lotw_file if isinstance(lotw_file,str) and len(lotw_file)<=2048 and '\x00' not in lotw_file else ''
        eqsl_file=settings.get('eqsl_file','')
        self.eqsl_file=eqsl_file if isinstance(eqsl_file,str) and len(eqsl_file)<=2048 and '\x00' not in eqsl_file else ''
        hidden = settings.get('hidden_bands', [])
        self.hidden_bands = {name for name in hidden if isinstance(name, str) and name in {band[0] for band in BANDS}} if isinstance(hidden, list) else set()
        self.color_bands = settings.get('color_bands') is True
        self.hide_duplicate_spots = settings.get('hide_duplicate_spots') is True
        marker = settings.get('spot_marker_filter', 'All spots')
        self.spot_marker_filter = marker if marker in ('All spots', 'Marked -# only', 'Unmarked only') else 'All spots'
        hidden_text=settings.get('spot_hidden_text',{})
        if not isinstance(hidden_text,dict):hidden_text={}
        self.spot_hidden_text={field:value if isinstance(value,str) and len(value)<=200 and all(ord(c)>=32 and ord(c)!=127 for c in value) else ''
                               for field in ('dx','spotter','comment') for value in (hidden_text.get(field,''),)}
        self.hidden_bands_note = tk.StringVar(value=self.hidden_bands_label())
        self.auto_reconnect = tk.BooleanVar(value=settings.get('auto_reconnect', False) is True)
        self.dark_theme = tk.BooleanVar(value=settings.get('dark_theme', False) is True)
        self.auto_start = tk.BooleanVar(value=settings.get('auto_start', False) is True)
        alarm = settings.get('alarm', {})
        if not isinstance(alarm, dict): alarm = {}
        self.alarm_config = {key: alarm.get(key, '') if isinstance(alarm.get(key, ''), str) else '' for key in ('calls', 'states', 'ranges', 'cq_zones', 'countries')}
        self.alarm_config.update(enabled=alarm.get('enabled') is True, sound=alarm.get('sound') is True, auto_open=alarm.get('auto_open') is True, country_prefix_mode=alarm.get('country_prefix_mode') is True)
        self.alarm_rules = None
        self.alarm_load_error = ''
        if self.alarm_config['enabled']:
            try: self.alarm_rules = compile_rules(self.alarm_config['calls'], self.alarm_config['states'], self.alarm_config['ranges'], self.alarm_config['cq_zones'], self.alarm_config['countries'],self.alarm_config['country_prefix_mode'])
            except ValueError as exc:
                self.alarm_load_error = str(exc)
                self.alarm_config['enabled'] = False
        self.alarm_calls = tk.StringVar(value=self.alarm_config['calls']); self.alarm_states = tk.StringVar(value=self.alarm_config['states']); self.alarm_ranges = tk.StringVar(value=self.alarm_config['ranges'])
        self.alarm_cq = tk.StringVar(value=self.alarm_config['cq_zones'])
        self.alarm_countries = tk.StringVar(value=self.alarm_config['countries'])
        self.alarm_enabled = tk.BooleanVar(value=self.alarm_config['enabled']); self.alarm_sound = tk.BooleanVar(value=self.alarm_config['sound'])
        self.alarm_auto_open = tk.BooleanVar(value=self.alarm_config['auto_open'])
        self.alarm_country_prefix_mode=tk.BooleanVar(value=self.alarm_config['country_prefix_mode'])
        self.status = tk.StringVar(value='Disconnected')
        self.band = tk.StringVar(value='All bands'); self.selected_bands = None; self.search = tk.StringVar(); self.search_field = tk.StringVar(value='All fields')
        self.spot_source_filter=tk.StringVar(value='All sources');self.spot_source_filter_targets={}
        self.spot_search_note=tk.StringVar(value='Type to filter; press Enter to search nodes. Rapid repeats may disconnect.')
        self.pending_spot_searches={};self.spot_search_context=None
        self.spot_count = tk.StringVar(value='0 spots')
        self.spot_view_locked = tk.BooleanVar(value=False)
        self.spot_source_note = tk.StringVar(value='Connect to a node to receive spots.')
        self.spot_recovery_notice = False
        self.manual_uncertain_notice = False
        self.spot_uncertain_notice = False
        self.report_kind = tk.StringVar(value='All reports')
        self.report_count = tk.StringVar(value='Node default')
        self.report_node = tk.StringVar(value=''); self.report_targets = {}
        self.message_kind = tk.StringVar(value='All messages'); self.message_node = tk.StringVar(value='')
        self.message_search=tk.StringVar();self.message_source_filter=tk.StringVar(value='All sources');self.message_source_targets={}
        self.auto_open_talk=tk.BooleanVar(value=settings.get('auto_open_talk') is True)
        saved_logger_port = settings.get('logger_port', '7300')
        if isinstance(saved_logger_port, bool) or not isinstance(saved_logger_port, (str, int)):
            saved_logger_port = '7300'
        saved_logger_port = str(saved_logger_port).strip()
        if not saved_logger_port.isascii() or not saved_logger_port.isdigit() or len(saved_logger_port) > 5 or not 1 <= int(saved_logger_port) <= 65535:
            saved_logger_port = '7300'
        self.logger_port = tk.StringVar(value=saved_logger_port); self.logger_status = tk.StringVar(value='Stopped')
        self.logger_route=tk.StringVar(value='Main node')
        self.logger_ignore_bye = tk.BooleanVar(value=settings.get('logger_ignore_bye') is True)
        self.logger_spots_only = tk.BooleanVar(value=settings.get('logger_spots_only') is True)
        self.logger_text_categories=load_text_categories(settings.get('logger_text_categories'),settings.get('text_categories_version'))
        bands=settings.get('logger_spot_bands')
        self.logger_spot_bands=set(bands) if isinstance(bands,list) and all(isinstance(x,str) and x in SPOT_BANDS for x in bands) else set(SPOT_BANDS)
        try:
            serial_settings=SerialBridge(str(settings.get('serial_port','COM1')),int(settings.get('serial_baud',9600)),lambda *_:None)
            saved_serial_port,saved_serial_baud=serial_settings.port,serial_settings.baudrate
        except (TypeError,ValueError):saved_serial_port,saved_serial_baud='COM1',9600
        self.serial_port=tk.StringVar(value=saved_serial_port);self.serial_baud=tk.StringVar(value=str(saved_serial_baud));self.serial_status=tk.StringVar(value='Stopped')
        self.serial_ignore_bye=tk.BooleanVar(value=settings.get('serial_ignore_bye') is True)
        self.serial_spots_only=tk.BooleanVar(value=settings.get('serial_spots_only') is True)
        self.serial_local_echo=tk.BooleanVar(value=settings.get('serial_local_echo') is True)
        self.serial_text_categories=load_text_categories(settings.get('serial_text_categories'),settings.get('text_categories_version'))
        bands=settings.get('serial_spot_bands')
        self.serial_spot_bands=set(bands) if isinstance(bands,list) and all(isinstance(x,str) and x in SPOT_BANDS for x in bands) else set(SPOT_BANDS)
        self.spot_log_status = tk.StringVar(value='Not recording')
        self.keyword_text = tk.StringVar(); self.spider_expression = tk.StringVar(); self.spider_line = tk.StringVar(value='1')
        self.cc_filter_kind = tk.StringVar(value='Keywords')
        self.cc_filter_band = tk.StringVar(value='All bands')
        self.spider_filter_kind = tk.StringVar(value='Band or range')
        self.spider_stream = tk.StringVar(value='Spots')

    def _settings_snapshot(self):
        saved_call=self.call.get().strip()
        if saved_call.isascii():saved_call=saved_call.upper()
        return {'country_file':self.country_file, 'lotw_file':self.lotw_file, 'eqsl_file':self.eqsl_file, 'startup_secondary_nodes':self.startup_secondary_nodes, 'dark_theme':self.dark_theme.get(), 'host': self.host.get().strip(), 'port': self.port.get().strip(),
                'call': saved_call, 'local_grid': self.local_grid.get(), 'auto_reconnect': self.auto_reconnect.get(), 'auto_start': self.auto_start.get(), 'auto_open_talk': self.auto_open_talk.get(), 'alarm': self.alarm_config, 'nodes': self.saved_nodes, 'talk_lists': self.talk_lists, 'hidden_bands': sorted(self.hidden_bands), 'color_bands': self.color_bands, 'hide_duplicate_spots': self.hide_duplicate_spots, 'spot_marker_filter': self.spot_marker_filter, 'spot_hidden_text': dict(self.spot_hidden_text), 'logger_ignore_bye': self.logger_ignore_bye.get(), 'logger_spots_only': self.logger_spots_only.get(), 'text_categories_version': 2, 'logger_text_categories': sorted(self.logger_text_categories), 'logger_spot_bands': sorted(self.logger_spot_bands), 'logger_port': self.logger_port.get().strip(), 'serial_port': self.serial_port.get().strip(), 'serial_baud': self.serial_baud.get().strip(), 'serial_ignore_bye': self.serial_ignore_bye.get(), 'serial_spots_only': self.serial_spots_only.get(), 'serial_local_echo': self.serial_local_echo.get(), 'serial_text_categories': sorted(self.serial_text_categories), 'serial_spot_bands': sorted(self.serial_spot_bands)}

    def _save_settings(self):
        try:
            local=deepcopy(self._settings_snapshot())
            category_group=('text_categories_version','logger_text_categories','serial_text_categories')
            group_changed=any(local[key]!=self._local_settings_baseline.get(key) for key in category_group)
            merged=save_settings(SETTINGS,local,self._local_settings_baseline,self._disk_settings_baseline,
                                 groups=(category_group,),baseline_exists=self._settings_file_seen)
            for key,value in local.items():
                if (group_changed and key in category_group) or key not in self._local_settings_baseline or value != self._local_settings_baseline[key] or (key not in self._disk_settings_baseline and merged.get(key)==value):
                    self._disk_settings_baseline[key]=deepcopy(merged[key])
            self._local_settings_baseline=local
            self._settings_file_seen=True
            self._last_alarm_saved=merged.get('alarm')==local['alarm']
            hidden=merged.get('hidden_bands',[])
            effective_hidden=sorted({name for name in hidden if isinstance(name,str) and name in {band[0] for band in BANDS}}) if isinstance(hidden,list) else []
            marker=merged.get('spot_marker_filter','All spots')
            if marker not in ('All spots','Marked -# only','Unmarked only'):marker='All spots'
            text_values=merged.get('spot_hidden_text',{})
            if not isinstance(text_values,dict):text_values={}
            effective_text={field:value if isinstance(value,str) and len(value)<=200 and all(ord(c)>=32 and ord(c)!=127 for c in value) else '' for field in ('dx','spotter','comment') for value in (text_values.get(field,''),)}
            effective={'hidden_bands':effective_hidden,'color_bands':merged.get('color_bands') is True,'hide_duplicate_spots':merged.get('hide_duplicate_spots') is True,'spot_marker_filter':marker,'spot_hidden_text':effective_text}
            self._last_display_saved=all(effective[key]==local[key] for key in effective)
            if self.alarm_unsaved and self._last_alarm_saved:
                self.alarm_unsaved=False
                if hasattr(self,'alarm_note'):self.update_alarm_note()
            if self.display_unsaved and self._last_display_saved:
                self.display_unsaved=False
                if hasattr(self,'hidden_bands_note'):self.hidden_bands_note.set(self.hidden_bands_label())
            return True
        except OSError as exc:
            self.append(f'\nCould not save connection settings: {exc}\n')
            return False

    def _set_palette(self, dark):
        if dark:
            self.theme_bg,self.theme_surface,self.theme_ink,self.theme_muted,self.theme_accent = '#0c121b','#17212d','#e6edf1','#a9bac5','#f4b449'
        else:
            self.theme_bg,self.theme_surface,self.theme_ink,self.theme_muted,self.theme_accent = BG,WHITE,INK,MUTED,ACCENT

    def _styles(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        dark=self.dark_theme.get()
        border='#394b59' if dark else '#d7e1e4'
        hover='#293d49' if dark else '#edf7f5'
        pressed='#344e57' if dark else '#d4eae7'
        disabled='#252f3a' if dark else '#edf1f3'
        disabled_text='#8a9ba7' if dark else '#899ba3'
        heading='#263441' if dark else '#e6edef'
        selected='#36545a' if dark else '#c7e7e1'
        selected_text=self.theme_ink if dark else '#123b40'
        button_highlight='#526675' if dark else '#ffffff'
        button_shadow='#080d12' if dark else '#a9bcc1'
        focus_ring='#9dd3ff' if dark else '#165b91'
        style.configure('.', font=('Segoe UI', 10), background=self.theme_bg, foreground=self.theme_ink)
        style.configure('TFrame', background=self.theme_bg)
        style.configure('Surface.TFrame', background=self.theme_surface)
        style.configure('TLabel', background=self.theme_bg, foreground=self.theme_ink)
        style.configure('Muted.TLabel', foreground=self.theme_muted, background=self.theme_bg, font=('Segoe UI', 9))
        style.configure('SettingsWarning.TLabel',foreground='#ffc17a' if dark else '#973918',background=self.theme_bg,font=('Segoe UI Semibold',9))
        style.configure('Title.TLabel', foreground=self.theme_ink, background=self.theme_bg, font=('Segoe UI Semibold', 17))
        style.configure('Heading.TLabel', foreground=self.theme_ink, background=self.theme_bg, font=('Segoe UI Semibold', 14))
        style.configure('Eyebrow.TLabel', foreground=self.theme_accent, background=self.theme_bg, font=('Segoe UI Semibold', 9))
        style.configure('EmptyTitle.TLabel', foreground=self.theme_muted, background=self.theme_surface, font=('Segoe UI Semibold', 12))
        style.configure('EmptyHint.TLabel', foreground=self.theme_muted, background=self.theme_surface, font=('Segoe UI', 9))
        style.configure('TButton', padding=(11, 6), background=self.theme_surface, foreground=self.theme_ink, bordercolor=border, lightcolor=button_highlight, darkcolor=button_shadow, relief='raised', borderwidth=2)
        style.map('TButton', background=[('pressed', pressed), ('active', hover), ('disabled', disabled)], foreground=[('disabled', disabled_text)], relief=[('pressed','sunken'),('!pressed','raised')], bordercolor=[('focus',focus_ring)])
        style.configure('Header.TButton',padding=(5,3),font=('Segoe UI',9))
        style.configure('Band.TButton',padding=(4,3),font=('Segoe UI Semibold',9))
        style.configure('BandCount.TLabel',foreground=self.theme_accent,background=self.theme_bg,font=('Segoe UI Semibold',9))
        style.configure('BandSelected.TButton',padding=(4,3),font=('Segoe UI Semibold',9),background=selected,foreground=selected_text,bordercolor=self.theme_accent,lightcolor=button_highlight,darkcolor=button_shadow,relief='sunken')
        style.map('BandSelected.TButton',background=[('pressed',pressed),('active',hover)],relief=[('pressed','sunken'),('!pressed','sunken')])
        style.configure('Accent.TButton', background=self.theme_accent, foreground=self.theme_surface, bordercolor=self.theme_accent, lightcolor='#ffd992' if dark else '#59b7ab', darkcolor='#80551e' if dark else '#04534e', font=('Segoe UI Semibold', 10))
        style.map('Accent.TButton', background=[('pressed', '#cb8d29' if dark else '#075e57'), ('active', '#ffc76c' if dark else '#096d64'), ('disabled', disabled)], foreground=[('disabled', disabled_text)])
        style.configure('CommandSend.Accent.TButton',font=('Segoe UI Symbol',14),padding=(8,4))
        style.configure('Icon.TButton',font=('Segoe UI Symbol',14),padding=(8,4))
        style.configure('Refresh.Icon.TButton',foreground='#8ac5fa' if dark else '#17679a')
        style.configure('Lock.Toolbutton',padding=(11,6),background=self.theme_surface,foreground=self.theme_ink,borderwidth=2,relief='raised')
        style.map('Lock.Toolbutton',background=[('selected',selected),('pressed',pressed),('active',hover)],
                  relief=[('selected','sunken'),('pressed','sunken'),('!selected','raised')],
                  bordercolor=[('focus',focus_ring),('selected',self.theme_accent)])
        style.configure('TCheckbutton',background=self.theme_bg,foreground=self.theme_ink,
                        indicatorbackground=self.theme_surface,indicatorforeground=self.theme_accent,
                        upperbordercolor=self.theme_muted,lowerbordercolor=self.theme_muted)
        style.map('TCheckbutton',background=[('active',self.theme_bg)],foreground=[('disabled',disabled_text)],
                  indicatorbackground=[('disabled',disabled),('pressed',pressed)],
                  indicatorforeground=[('disabled',disabled_text)])
        style.configure('TEntry', fieldbackground=self.theme_surface, foreground=self.theme_ink, bordercolor=border, lightcolor=border, darkcolor=border, padding=7)
        style.map('TEntry', bordercolor=[('focus', self.theme_accent)], lightcolor=[('focus', self.theme_accent)], darkcolor=[('focus', self.theme_accent)])
        style.configure('TCombobox', fieldbackground=self.theme_surface, foreground=self.theme_ink, background=self.theme_surface, bordercolor=border, lightcolor=border, darkcolor=border, arrowcolor=self.theme_muted, padding=6)
        style.map('TCombobox', fieldbackground=[('disabled', disabled), ('readonly', self.theme_surface)], background=[('disabled',disabled),('active',hover),('readonly',self.theme_surface)], foreground=[('disabled',disabled_text),('readonly',self.theme_ink)])
        style.configure('Treeview', background=self.theme_surface, fieldbackground=self.theme_surface, foreground=self.theme_ink, rowheight=32, borderwidth=0, font=('Segoe UI', 10))
        style.configure('Spot.Treeview', rowheight=25)
        style.configure('Treeview.Heading', background=heading, foreground=self.theme_ink if dark else '#49636d', font=('Segoe UI Semibold', 9), padding=(10, 9), relief='flat', borderwidth=0)
        style.map('Treeview.Heading', background=[('active', hover)])
        style.configure('Spot.Treeview.Heading', padding=(9,5))
        style.map('Treeview', background=[('selected', selected)], foreground=[('selected', selected_text)])
        style.configure('TNotebook', background=self.theme_bg, bordercolor=self.theme_bg, lightcolor=self.theme_bg, darkcolor=self.theme_bg, borderwidth=0, relief='flat')
        style.configure('TNotebook.Tab', padding=(11, 6), background=self.theme_bg, foreground=self.theme_muted, bordercolor=self.theme_bg, lightcolor=self.theme_bg, darkcolor=self.theme_bg, borderwidth=0, font=('Segoe UI Semibold', 9))
        style.map('TNotebook.Tab', padding=[('selected', (11, 6)), ('!selected', (11, 6))], background=[('selected', selected), ('active', hover)], foreground=[('selected', selected_text), ('active', self.theme_ink)])
        style.configure('Desk.TSeparator', background=border)
        for direction in ('Vertical.TScrollbar','Horizontal.TScrollbar'):
            style.configure(direction,background=heading,troughcolor=self.theme_bg,arrowcolor=self.theme_muted,
                            bordercolor=self.theme_bg,lightcolor=heading,darkcolor=heading,relief='flat',borderwidth=0)
            style.map(direction,background=[('active',hover)])
        self.option_add('*TCombobox*Listbox.background',self.theme_surface)
        self.option_add('*TCombobox*Listbox.foreground',self.theme_ink)
        self.option_add('*TCombobox*Listbox.selectBackground',selected)
        self.option_add('*TCombobox*Listbox.selectForeground',selected_text)
        self.option_add('*Text.insertBackground',self.theme_ink)

    def switch_theme(self):
        self.dark_theme.set(not self.dark_theme.get())
        self._set_palette(self.dark_theme.get());self.configure(background=self.theme_bg);self._styles()
        def update_widgets(widget):
            for child in widget.winfo_children():
                if isinstance(child,tk.Toplevel):child.configure(background=self.theme_bg)
                elif isinstance(child,tk.Canvas):child.configure(background=self.theme_bg)
                elif isinstance(child,tk.Text):
                    child.configure(background=self.theme_surface,foreground=self.theme_ink,insertbackground=self.theme_ink,
                                    selectbackground='#36545a' if self.dark_theme.get() else '#c7e7e1',selectforeground=self.theme_ink)
                elif isinstance(child,tk.Listbox):
                    child.configure(background=self.theme_surface,foreground=self.theme_ink,
                                    selectbackground='#36545a' if self.dark_theme.get() else '#c7e7e1',selectforeground=self.theme_ink)
                elif isinstance(child,ttk.Combobox):
                    popup=str(child)+'.popdown.f.l'
                    if self.tk.call('winfo','exists',popup):
                        self.tk.call(popup,'configure','-background',self.theme_surface,'-foreground',self.theme_ink,
                                     '-selectbackground','#36545a' if self.dark_theme.get() else '#c7e7e1','-selectforeground',self.theme_ink)
                update_widgets(child)
        update_widgets(self)
        self.status_dot.configure(background=self.theme_bg)
        self.refresh_status_indicator()
        self.style_spot_inspector()
        self.spots.tag_configure('alternate',background='#1c2935' if self.dark_theme.get() else '#f5f9fa')
        for name,color in (DARK_BAND_COLORS if self.dark_theme.get() else BAND_COLORS).items():self.spots.tag_configure('band-'+name,background=color)
        self.theme_button.configure(text='Light' if self.dark_theme.get() else 'Dark',image=self.ui_icons['sun' if self.dark_theme.get() else 'moon'])
        suffix='dark' if self.dark_theme.get() else 'light'
        connection_icon='stop' if self.connect_btn.cget('text')=='Disconnect' else 'play'
        self.connect_btn.configure(image=self.ui_icons[connection_icon+'_dark' if self.dark_theme.get() else connection_icon])
        for widget,name in ((self.history_button,'book'),(self.qrz_button,'globe'),(self.clear_spots_button,'trash'),(self.spot_lock_button,'lock'),
                            (self.display_options_button,'monitor'),(self.empty_display_options_button,'monitor'),(self.station_info_button,'house')):
            widget.configure(image=self.ui_icons[name+'_'+suffix])
        self.spot_button.configure(image=(self.ui_icons['post_'+suffix],'disabled',self.ui_icons['post_disabled_'+suffix]))
        if not self._save_settings():self.append('\nTheme changed for this session, but could not be saved.\n')

    def make_ui_icons(self):
        icons={name:tk.PhotoImage(master=self,width=16,height=16) for name in ('play','stop','play_dark','stop_dark','sun','moon')}
        for y in range(2,14):
            for x in range(2,14):
                if x-2 <= (5.5-abs(y-7.5))*1.7:
                    icons['play'].put('#169447',(x,y));icons['play_dark'].put('#65dc8a',(x,y))
                if 3<=x<=12 and 3<=y<=12:
                    icons['stop'].put('#d53b3b',(x,y));icons['stop_dark'].put('#ff7d78',(x,y))
                if (x-7.5)**2+(y-7.5)**2<=13:icons['sun'].put('#e2a227',(x,y))
                if (x-7)**2+(y-8)**2<=25 and (x-10)**2+(y-5)**2>18:icons['moon'].put('#7899c4',(x,y))
        for x,y in ((7,1),(7,14),(1,7),(14,7),(3,3),(12,3),(3,12),(12,12)):
            icons['sun'].put('#e2a227',(x,y))
        def stroke(image,points,color):
            for (x1,y1),(x2,y2) in zip(points,points[1:]):
                steps=max(abs(x2-x1),abs(y2-y1))
                for i in range(steps+1):
                    x=round(x1+(x2-x1)*i/steps) if steps else x1
                    y=round(y1+(y2-y1)*i/steps) if steps else y1
                    image.put(color,(x,y))
        shapes={
            'book': [[(1,3),(5,3),(8,5),(11,3),(14,3),(14,13),(10,13),(8,14),(6,13),(1,13),(1,3)],
                     [(8,5),(8,14)],[(3,6),(5,6)],[(10,6),(12,6)],[(3,9),(5,9)],[(10,9),(12,9)]],
            'globe': [[(8,1),(11,2),(13,4),(14,7),(13,10),(11,12),(8,14),(5,12),(3,10),(2,7),(3,4),(5,2),(8,1)],
                      [(8,1),(6,4),(5,7),(6,10),(8,14)],[(8,1),(10,4),(11,7),(10,10),(8,14)],
                      [(3,5),(13,5)],[(2,8),(14,8)],[(3,11),(13,11)]],
            'trash': [[(3,4),(13,4)],[(6,3),(6,2),(10,2),(10,3)],[(4,5),(5,14),(11,14),(12,5)],
                      [(7,7),(7,12)],[(9,7),(9,12)]],
            'lock': [[(4,7),(4,5),(5,3),(6,2),(9,2),(11,4),(11,7)],
                     [(3,7),(12,7),(12,14),(3,14),(3,7)],[(7,10),(8,10),(8,12)]],
            'monitor': [[(1,2),(14,2),(14,11),(1,11),(1,2)],[(7,12),(7,14)],[(4,14),(11,14)]],
            'house': [[(1,7),(8,1),(15,7)],[(3,6),(3,14),(13,14),(13,6)],[(7,14),(7,10),(9,10),(9,14)]],
            'post': [[(1,3),(5,3),(8,5),(11,4)],[(1,3),(1,13),(5,13),(8,14),(11,13),(14,13),(14,8)],
                     [(8,5),(8,14)],[(3,6),(5,6)],[(3,9),(5,9)],[(9,10),(14,4)],[(9,10),(8,12),(10,11),(15,5),(14,4)]],
        }
        palettes={
            'light':{'book':'#7350a3','globe':'#176a9c','trash':'#a6423c','lock':'#80520a','monitor':'#08766d','house':'#a34c2c','post':'#355ca3'},
            'dark':{'book':'#c6a5f3','globe':'#8bc9f3','trash':'#f19b92','lock':'#f4c36a','monitor':'#77d5c2','house':'#f2aa84','post':'#9ebfff'},
        }
        for suffix,disabled_color in (('light','#899ba3'),('dark','#8a9ba7')):
            for name,paths in shapes.items():
                icon=tk.PhotoImage(master=self,width=16,height=16)
                for path in paths:stroke(icon,path,palettes[suffix][name])
                icons[name+'_'+suffix]=icon.zoom(5).subsample(4)
            icon=tk.PhotoImage(master=self,width=16,height=16)
            for path in shapes['post']:stroke(icon,path,disabled_color)
            icons['post_disabled_'+suffix]=icon.zoom(5).subsample(4)
        return icons

    def _build(self):
        self.ui_icons=self.make_ui_icons()
        outer = ttk.Frame(self, padding=(20, 12)); outer.pack(fill='both', expand=True)
        if self.settings_load_problem:
            ttk.Label(outer,text='Settings could not be loaded; defaults are in use. See Activity.',style='SettingsWarning.TLabel').pack(fill='x',pady=(0,6))
        top = ttk.Frame(outer); top.pack(fill='x')
        brand = ttk.Frame(top); brand.pack(side='left')
        self.brand_title=ttk.Label(brand,text='Cluster Desk by N2WQ',style='Title.TLabel');self.brand_title.pack(anchor='w')
        ttk.Button(top, text='Saved nodes…', style='Header.TButton', width=0, command=self.open_nodes).pack(side='left', padx=(12,8))
        ttk.Button(top, text='My grid…', style='Header.TButton', width=0, command=self.open_local_grid).pack(side='left')
        ttk.Button(top, text='Additional nodes…', style='Header.TButton', width=0, command=self.open_secondary_nodes).pack(side='left', padx=(8,0))
        status_box = ttk.Frame(top); status_box.pack(side='right')
        self.status_dot = tk.Label(status_box, text='●', fg=self.theme_muted, bg=self.theme_bg, font=('Segoe UI', 10)); self.status_dot.pack(side='left', padx=(0, 5))
        ttk.Label(status_box, textvariable=self.status, style='Muted.TLabel').pack(side='left')
        self.status.trace_add('write', lambda *_: self.refresh_status_indicator()); self.refresh_status_indicator()
        ttk.Separator(outer, orient='horizontal', style='Desk.TSeparator').pack(fill='x', pady=(6, 0))
        conn = ttk.Frame(outer); conn.pack(fill='x', pady=(12, 14)); self.connection_bar=conn
        self.connection_entries=[]
        for label, var, width in [('CLUSTER', self.host, 25), ('PORT', self.port, 7), ('CALLSIGN', self.call, 13)]:
            box = ttk.Frame(conn); box.pack(side='left', padx=(0, 10))
            ttk.Label(box, text=label, style='Muted.TLabel').pack(anchor='w', pady=(0, 4))
            entry=ttk.Entry(box, textvariable=var, width=width);entry.pack();self.connection_entries.append(entry)
        actions = ttk.Frame(conn); actions.pack(side='left', anchor='s', padx=(3, 0))
        self.connect_btn = ttk.Button(actions, text='Connect', image=self.ui_icons['play_dark' if self.dark_theme.get() else 'play'], compound='left', command=self.toggle_connection); self.connect_btn.pack(side='left')
        ttk.Checkbutton(actions, text='Auto reconnect', variable=self.auto_reconnect, command=self.reconnect_option_changed).pack(side='left', padx=(5, 0))
        self.notebook = ttk.Notebook(outer)
        spot_page = ttk.Frame(self.notebook, padding=(0, 8, 0, 0)); self.notebook.add(spot_page, text='DX spots')
        self.console_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(self.console_page, text='Activity')
        settings_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(settings_page, text='Settings')
        filter_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(filter_page, text='Filters')
        report_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(report_page, text='Propagation')
        ann_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(ann_page, text='Messages')
        self.ann_page=ann_page;self.report_page=report_page
        directory_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(directory_page, text='Connections')
        logger_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(logger_page, text='Loggers')
        alarm_page = ttk.Frame(self.notebook, padding=(0, 12, 0, 0)); self.notebook.add(alarm_page, text='Alarms')
        self.alarm_page = alarm_page
        alarm_header=ttk.Frame(alarm_page);alarm_header.pack(fill='x')
        ttk.Label(alarm_header, text='Spot alarms', style='Heading.TLabel').pack(side='left')
        ttk.Button(alarm_header,text='Choose bands…',command=self.open_alarm_band_picker).pack(side='right')
        ttk.Checkbutton(alarm_header,text='Use *prefix as country',variable=self.alarm_country_prefix_mode).pack(side='right',padx=12)
        ttk.Label(alarm_page, text='Filled fields must match. Range: 14000-14350. Country: exact node label or ID.', style='Muted.TLabel').pack(anchor='w', pady=(5, 8))
        form = ttk.Frame(alarm_page); form.pack(fill='x')
        for label, variable in [('DX CALLS (* and ?)', self.alarm_calls), ('DX STATES', self.alarm_states), ('RANGES kHz', self.alarm_ranges), ('CQ ZONES', self.alarm_cq), ('COUNTRY / ID', self.alarm_countries)]:
            column = ttk.Frame(form); column.pack(side='left', fill='x', expand=True, padx=(0, 8))
            ttk.Label(column, text=label, style='Muted.TLabel').pack(anchor='w')
            ttk.Entry(column, textvariable=variable, width=10).pack(fill='x', pady=(3, 0))
        controls = ttk.Frame(alarm_page); controls.pack(fill='x', pady=(8, 5))
        ttk.Checkbutton(controls, text='Enable alarms', variable=self.alarm_enabled).pack(side='left')
        ttk.Checkbutton(controls, text='Sound', variable=self.alarm_sound).pack(side='left', padx=12)
        ttk.Checkbutton(controls, text='Auto open', variable=self.alarm_auto_open).pack(side='left')
        ttk.Button(controls, text='Apply', command=self.apply_alarm).pack(side='left', padx=8)
        ttk.Button(controls, text='Clear matches', command=self.clear_alarms).pack(side='right')
        self.alarm_note = tk.StringVar(value='Enabled.' if self.alarm_rules else 'Disabled.')
        self.update_alarm_note()
        ttk.Label(controls, textvariable=self.alarm_note, style='Muted.TLabel').pack(side='left', padx=8)
        ttk.Label(alarm_page, text='All received spots are checked, including requested history and duplicates. Sound is limited to once per five seconds.', style='Muted.TLabel').pack(anchor='w', pady=(3, 8))
        alarm_actions=ttk.Frame(alarm_page);alarm_actions.pack(side='bottom',fill='x',pady=(8,0))
        ttk.Button(alarm_actions,text='View details…',command=self.open_alarm_detail).pack(side='left')
        ttk.Button(alarm_actions,text='Show in spots',command=self.show_alarm_spot).pack(side='left',padx=8)
        alarm_table = ttk.Frame(alarm_page); alarm_table.pack(fill='both', expand=True)
        self.alarm_tree = ttk.Treeview(alarm_table, columns=('time', 'dx', 'frequency', 'state', 'spotter', 'comment'), show='headings')
        for col, heading, width in [('time','UTC',75),('dx','DX CALL',130),('frequency','kHz',100),('state','STATE',70),('spotter','SPOTTER',130),('comment','COMMENT',320)]:
            self.alarm_tree.heading(col, text=heading,anchor='e' if col=='frequency' else 'center'); self.alarm_tree.column(col, width=width, minwidth=55, stretch=col == 'comment',anchor='e' if col=='frequency' else 'center')
        alarm_scroll = ttk.Scrollbar(alarm_table, orient='vertical', command=self.alarm_tree.yview)
        alarm_scroll.pack(side='right', fill='y'); self.alarm_tree.pack(side='left', fill='both', expand=True)
        self.alarm_tree.configure(yscrollcommand=alarm_scroll.set)
        directory_header = ttk.Frame(directory_page); directory_header.pack(fill='x', pady=(0, 10))
        ttk.Label(directory_header, text='Users and nodes', style='Heading.TLabel').pack(side='left')
        self.directory_note = tk.StringVar(value='Connect to a node to request a list.')
        ttk.Label(directory_header, textvariable=self.directory_note, style='Muted.TLabel').pack(side='left', padx=12)
        directory_tools = ttk.Frame(directory_page); directory_tools.pack(fill='x', pady=(0, 10))
        self.directory_buttons = []
        for label, command in [('Local connections','who'), ('Node configuration','sh/config/node'), ('Network configuration','sh/config')]:
            button=ttk.Button(directory_tools, text=label, command=lambda c=command: self.request_directory(c), state='disabled')
            button.pack(side='left', padx=(0, 8)); self.directory_buttons.append(button)
        ttk.Button(directory_tools, text='Clear view', command=self.clear_directory).pack(side='right')
        ttk.Label(directory_page, text='Node output is shown as received; live traffic may appear among the results.', style='Muted.TLabel').pack(side='bottom', anchor='w', pady=(8, 0))
        directory_panel = ttk.Frame(directory_page); directory_panel.pack(fill='both', expand=True)
        self.directory_text = tk.Text(directory_panel, background=self.theme_surface, foreground=self.theme_ink, wrap='none', relief='flat', padx=12, pady=10, font=('Consolas', 10), state='disabled')
        directory_vscroll=ttk.Scrollbar(directory_panel, orient='vertical', command=self.directory_text.yview)
        directory_hscroll=ttk.Scrollbar(directory_panel, orient='horizontal', command=self.directory_text.xview)
        directory_vscroll.pack(side='right', fill='y'); directory_hscroll.pack(side='bottom', fill='x')
        self.directory_text.pack(side='left', fill='both', expand=True)
        self.directory_text.configure(yscrollcommand=directory_vscroll.set, xscrollcommand=directory_hscroll.set)
        ann_header = ttk.Frame(ann_page); ann_header.pack(fill='x', pady=(0, 10))
        ttk.Label(ann_header, text='Messages', style='Heading.TLabel').pack(side='left')
        self.ann_note = tk.StringVar(value='Connect to a node to receive or request announcements.')
        ttk.Button(ann_header, text='Cluster mail…', command=self.open_mail).pack(side='right')
        ttk.Button(ann_header, text='Chat groups…', command=self.open_chat).pack(side='right',padx=(0,8))
        ttk.Label(ann_header, textvariable=self.ann_note, style='Muted.TLabel').pack(side='left', padx=12)
        ann_tools = ttk.Frame(ann_page); ann_tools.pack(fill='x', pady=(0, 10))
        self.ann_button=ttk.Button(ann_tools, text='Get announcements', command=self.request_announcements, state='disabled'); self.ann_button.pack(side='left')
        self.wx_button=ttk.Button(ann_tools, text='Get weather', command=self.request_weather, state='disabled'); self.wx_button.pack(side='left', padx=(8, 0))
        ttk.Button(ann_tools, text='Compose message…', command=self.open_message).pack(side='left', padx=8)
        ttk.Button(ann_tools, text='Clear all', command=self.clear_announcements).pack(side='right')
        message_filter = ttk.Combobox(ann_tools, textvariable=self.message_kind, values=['All messages', 'Announcements', 'Weather', 'Direct messages', 'Group chat', 'History (unclassified)'], state='readonly', width=19)
        message_filter.pack(side='right', padx=8); message_filter.bind('<<ComboboxSelected>>', lambda event: self.render_announcements())
        ann_node_bar=ttk.Frame(ann_page);ann_node_bar.pack(fill='x',pady=(0,8))
        ttk.Label(ann_node_bar,text='REQUEST FROM',style='Muted.TLabel').pack(side='left',padx=(0,8))
        self.message_node_box=ttk.Combobox(ann_node_bar,textvariable=self.message_node,state='readonly',width=24)
        self.message_node_box.pack(side='left');self.message_node.trace_add('write',lambda *_:self.update_report_actions())
        ttk.Label(ann_node_bar,text='SHOW',style='Muted.TLabel').pack(side='left',padx=(12,6))
        self.message_source_box=ttk.Combobox(ann_node_bar,textvariable=self.message_source_filter,values=['All sources'],state='readonly',width=19)
        self.message_source_box.pack(side='left');self.message_source_box.bind('<<ComboboxSelected>>',lambda e:self.render_announcements())
        ttk.Label(ann_node_bar,text='FIND',style='Muted.TLabel').pack(side='left',padx=(12,6))
        self.message_search_box=ttk.Entry(ann_node_bar,textvariable=self.message_search,width=15);self.message_search_box.pack(side='left',fill='x',expand=True)
        self.message_search.trace_add('write',lambda *_:self.render_announcements())
        ann_table = ttk.Frame(ann_page)
        self.ann_tree = ttk.Treeview(ann_table, columns=('message','source'), show='headings')
        self.ann_tree.heading('message', text='NODE MESSAGE'); self.ann_tree.column('message', width=700, minwidth=300)
        self.ann_tree.heading('source',text='RECEIVED FROM');self.ann_tree.column('source',width=180,minwidth=120)
        ann_scroll=ttk.Scrollbar(ann_table, orient='vertical', command=self.ann_tree.yview)
        ann_scroll.pack(side='right', fill='y'); self.ann_tree.pack(side='left', fill='both', expand=True); self.ann_tree.configure(yscrollcommand=ann_scroll.set)
        ann_detail_frame = ttk.Frame(ann_page); ann_detail_frame.pack(side='bottom', fill='x')
        ann_detail_header = ttk.Frame(ann_detail_frame); ann_detail_header.pack(fill='x', pady=(8, 4))
        ttk.Label(ann_detail_header, text='SELECTED MESSAGE', style='Muted.TLabel').pack(side='left')
        ttk.Checkbutton(ann_detail_header,text='Auto open direct messages',variable=self.auto_open_talk,command=self._save_settings).pack(side='left',padx=12)
        self.reply_button = ttk.Button(ann_detail_header, text='Reply…', command=self.reply_to_talk, state='disabled')
        self.reply_button.pack(side='right')
        ann_text_frame=ttk.Frame(ann_detail_frame);ann_text_frame.pack(fill='x')
        self.ann_detail = tk.Text(ann_text_frame, height=3, background=self.theme_surface, foreground=self.theme_ink, wrap='word', relief='flat', padx=8, pady=6, font=('Segoe UI', 10), state='disabled')
        ann_detail_scroll=ttk.Scrollbar(ann_text_frame,orient='vertical',command=self.ann_detail.yview)
        ann_detail_scroll.pack(side='right',fill='y');self.ann_detail.pack(side='left',fill='x',expand=True)
        self.ann_detail.configure(yscrollcommand=ann_detail_scroll.set)
        self.ann_tree.bind('<<TreeviewSelect>>', lambda event: self.show_announcement())
        self.ann_footer_hint=ttk.Label(ann_detail_frame,text='Most recently received first. Raw output is in the corresponding node activity view.',style='Muted.TLabel');self.ann_footer_hint.pack(anchor='w',pady=(8,0))
        ann_table.pack(fill='both', expand=True)
        report_header = ttk.Frame(report_page); report_header.pack(fill='x', pady=(0, 10))
        ttk.Label(report_header, text='Propagation reports', style='Heading.TLabel').pack(side='left')
        self.report_note = tk.StringVar(value='Connect to a node, then request WWV or WCY reports.')
        ttk.Button(report_header, text='Station lookup…', command=self.open_propagation_lookup).pack(side='right')
        ttk.Label(report_header, textvariable=self.report_note, style='Muted.TLabel').pack(side='left', padx=12)
        report_tools = ttk.Frame(report_page); report_tools.pack(fill='x', pady=(0, 10))
        self.wwv_button = ttk.Button(report_tools, text='Get WWV', command=lambda: self.request_report('WWV'), state='disabled'); self.wwv_button.pack(side='left')
        self.wcy_button = ttk.Button(report_tools, text='Get WCY', command=lambda: self.request_report('WCY'), state='disabled'); self.wcy_button.pack(side='left', padx=8)
        ttk.Label(report_tools,text='HISTORY',style='Muted.TLabel').pack(side='left',padx=(4,6))
        ttk.Combobox(report_tools,textvariable=self.report_count,values=['Node default','5','10','20','50','100'],state='readonly',width=12).pack(side='left')
        ttk.Button(report_tools, text='Clear view', command=self.clear_reports).pack(side='right')
        report_filter = ttk.Combobox(report_tools, textvariable=self.report_kind, values=['All reports','WWV','WCY'], state='readonly', width=13)
        report_filter.pack(side='right', padx=8); report_filter.bind('<<ComboboxSelected>>', lambda event: self.render_reports())
        report_node_bar=ttk.Frame(report_page);report_node_bar.pack(fill='x',pady=(0,8))
        ttk.Label(report_node_bar,text='REQUEST FROM',style='Muted.TLabel').pack(side='left',padx=(0,8))
        self.report_node_box=ttk.Combobox(report_node_bar,textvariable=self.report_node,state='readonly',width=40)
        self.report_node_box.pack(side='left');self.report_node.trace_add('write',lambda *_:self.update_report_actions())
        self.refresh_report_targets()
        report_table = ttk.Frame(report_page); self.report_table = report_table
        self.report_tree = ttk.Treeview(report_table, columns=('type','report','source'), show='headings')
        self.report_tree.heading('type', text='TYPE'); self.report_tree.heading('report', text='NODE REPORT');self.report_tree.heading('source',text='RECEIVED FROM')
        self.report_tree.column('type', width=75, minwidth=65, stretch=False); self.report_tree.column('report', width=700, minwidth=300);self.report_tree.column('source',width=180,minwidth=120)
        report_scroll=ttk.Scrollbar(report_table, orient='vertical', command=self.report_tree.yview)
        report_scroll.pack(side='right', fill='y'); self.report_tree.pack(side='left', fill='both', expand=True); self.report_tree.configure(yscrollcommand=report_scroll.set)
        self.report_footer_hints = (
            ttk.Label(report_page, text='History: date · hour UTC · SFI · A · K · details · logger. WCY details include Exp.K, R, SA, GMF and Aurora.', style='Muted.TLabel'),
            ttk.Label(report_page, text='Shown as received from the node. Reports are not interpreted or checked for freshness.', style='Muted.TLabel'))
        for hint in reversed(self.report_footer_hints): hint.pack(side='bottom', anchor='w', pady=(8, 0))
        report_table.pack(fill='both', expand=True)
        filter_header = ttk.Frame(filter_page); filter_header.pack(fill='x', pady=(0, 10))
        ttk.Label(filter_header, text='Node filters', style='Heading.TLabel').pack(side='left')
        self.filter_note = tk.StringVar(value='Connect to a cluster to manage its filters.')
        ttk.Label(filter_header, textvariable=self.filter_note, style='Muted.TLabel').pack(side='left', padx=12)
        self.filter_refresh = ttk.Button(filter_header, text='Show node filters', command=self.show_filters, state='disabled'); self.filter_refresh.pack(side='right')
        ttk.Label(filter_page, text='Changes apply to the main node and affect its traffic to the app and loggers.', style='Muted.TLabel').pack(anchor='w', pady=(0, 14))
        self.cc_filter = ttk.Frame(filter_page)
        choice = ttk.Frame(self.cc_filter); choice.pack(fill='x', pady=(0, 8))
        ttk.Label(choice, text='FILTER', style='Muted.TLabel').pack(side='left', padx=(0, 8))
        cc_filter_selector = ttk.Combobox(choice, textvariable=self.cc_filter_kind, values=list(CC_FILTERS), width=25, state='readonly')
        cc_filter_selector.pack(side='left'); cc_filter_selector.bind('<<ComboboxSelected>>', lambda event: self.keyword_text.set(''))
        ttk.Label(choice, text='VALUES', style='Muted.TLabel').pack(side='left', padx=(14, 8))
        ttk.Entry(choice, textvariable=self.keyword_text, width=35).pack(side='left', fill='x', expand=True)
        self.cc_band_picker = ttk.Button(choice, text='Choose…', command=lambda:self.open_filter_value_picker(self.keyword_text,self.cc_filter_kind,'CC Cluster',self))
        self.cc_band_picker.pack(side='left', padx=(8,0))
        scope_row=ttk.Frame(self.cc_filter);scope_row.pack(fill='x',pady=(0,8))
        ttk.Label(scope_row,text='APPLY TO BAND',style='Muted.TLabel').pack(side='left',padx=(0,8))
        self.cc_scope_box=ttk.Combobox(scope_row,textvariable=self.cc_filter_band,values=CC_FILTER_BANDS,state='disabled',width=14,height=len(CC_FILTER_BANDS))
        self.cc_scope_box.pack(side='left')
        ttk.Label(scope_row,text='Country/state spots only · changing filter selects All',style='Muted.TLabel').pack(side='left',padx=8)
        self.cc_filter_hint=tk.StringVar()
        def update_cc_filter_hint(*_):
            supported=self.cc_filter_kind.get() in CC_PER_BAND_CATEGORIES
            self.cc_scope_box.configure(state='readonly' if supported else 'disabled')
            self.cc_filter_band.set('All bands')
            hint = ('Changes listed bands or segments; other bands may still pass. Modes mean frequency ranges.'
                    if self.cc_filter_kind.get() == 'Band / frequency segment' else
                    'Replaces the entire node keyword filter; enter all desired keywords.' if self.cc_filter_kind.get() == 'Keywords' else
                    'Use country prefixes or states. All bands can replace or clear per-band rules.' if supported else
                    'Use comma-separated country prefixes or states.')
            self.cc_filter_hint.set(hint)
            self.cc_band_picker.configure(state='normal' if self.cc_filter_kind.get() == 'Band / frequency segment' or self.cc_filter_kind.get().endswith(('country','state')) else 'disabled')
        self.cc_filter_kind.trace_add('write', update_cc_filter_hint); update_cc_filter_hint()
        ttk.Label(self.cc_filter, textvariable=self.cc_filter_hint, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        cc_actions = ttk.Frame(self.cc_filter); cc_actions.pack(anchor='w')
        ttk.Button(cc_actions, text='Pass matching', command=lambda: self.change_filter('pass')).pack(side='left')
        ttk.Button(cc_actions, text='Reject matching', command=lambda: self.change_filter('reject')).pack(side='left', padx=8)
        ttk.Button(cc_actions, text='Turn selected filter off', command=lambda: self.change_filter('off')).pack(side='left')
        cc_footer=ttk.Frame(self.cc_filter);cc_footer.pack(anchor='w',pady=(8,0))
        ttk.Button(cc_footer,text='Reset all node filters…',command=self.reset_cc_filters).pack(side='left')
        ttk.Button(cc_footer,text='Look up node state…',command=self.lookup_main_state).pack(side='left',padx=(8,0))
        self.spider_filter = ttk.Frame(filter_page)
        ttk.Label(self.spider_filter, text='Each line can hold both accept and reject rules. Clear removes both. Accept rules can exclude nonmatching traffic.', style='Muted.TLabel').pack(anchor='w', pady=(0, 6))
        stream_row = ttk.Frame(self.spider_filter); stream_row.pack(fill='x', pady=(0, 8))
        ttk.Label(stream_row, text='TRAFFIC', style='Muted.TLabel').pack(side='left', padx=(0, 8))
        stream_selector = ttk.Combobox(stream_row, textvariable=self.spider_stream, values=list(SPIDER_STREAMS), width=17, state='readonly')
        stream_selector.pack(side='left')
        spider_fields = ttk.Frame(self.spider_filter); spider_fields.pack(fill='x')
        line_field = ttk.Frame(spider_fields); line_field.pack(side='left', padx=(0, 12))
        ttk.Label(line_field, text='LINE', style='Muted.TLabel').pack(anchor='w')
        ttk.Combobox(line_field, textvariable=self.spider_line, values=[str(i) for i in range(10)], width=4, state='readonly').pack(pady=(4, 0))
        spider_category = ttk.Frame(spider_fields); spider_category.pack(side='left', padx=(0, 12))
        ttk.Label(spider_category, text='FILTER', style='Muted.TLabel').pack(anchor='w')
        spider_selector=ttk.Combobox(spider_category, textvariable=self.spider_filter_kind, values=spider_categories('Spots'), width=19, state='readonly')
        spider_selector.pack(pady=(4, 0)); spider_selector.bind('<<ComboboxSelected>>', lambda event: self.spider_expression.set(''))
        def update_spider_stream(*_):
            stream = self.spider_stream.get()
            choices = spider_categories(stream)
            spider_selector.configure(values=choices)
            self.spider_filter_kind.set('Band or range' if stream in ('Spots','RBN spots') else 'Spotter callsign text'); self.spider_expression.set('')
        self.spider_stream.trace_add('write', update_spider_stream)
        expression_field = ttk.Frame(spider_fields); expression_field.pack(side='left', fill='x', expand=True)
        self.spider_value_hint = tk.StringVar()
        def update_spider_hint(*_):
            category = self.spider_filter_kind.get()
            if category == 'Advanced expression': hint = 'EXPRESSION  •  EXAMPLE: on 20m and by G'
            elif category == 'Band or range': hint = 'BAND OR WHOLE kHz RANGE  •  20m or 14000/14350'
            elif category == 'Minimum skimmers': hint = 'MINIMUM 1–9  •  9 INCLUDES NINE OR MORE'
            elif category == 'Minimum RBN strength': hint = 'MINIMUM SIGNAL  •  0–99 dB'
            elif category.endswith(('callsign text', ' text')): hint = 'TEXT CONTAINS  •  END WITH * FOR STARTS WITH'
            elif category.endswith('zone'): hint = 'ZONE NUMBERS OR PREFIXES  •  EXAMPLE: 5,14'
            elif category.endswith('state'): hint = '2-LETTER STATES  •  NODE USDB REQUIRED'
            else: hint = 'PREFIXES OR NODE COUNTRY NUMBERS  •  NOT ARRL DXCC'
            self.spider_value_hint.set(hint)
        self.spider_filter_kind.trace_add('write', update_spider_hint); update_spider_hint()
        ttk.Label(expression_field, textvariable=self.spider_value_hint, style='Muted.TLabel').pack(anchor='w')
        expression_row=ttk.Frame(expression_field);expression_row.pack(fill='x',pady=(4,0))
        ttk.Entry(expression_row, textvariable=self.spider_expression).pack(side='left',fill='x',expand=True)
        spider_band_button=ttk.Button(expression_row,text='Choose…',command=lambda:self.open_filter_value_picker(self.spider_expression,self.spider_filter_kind,'DXSpider',self,stream=self.spider_stream))
        spider_band_button.pack(side='left',padx=(6,0))
        def update_spider_band_button(*_):
            spider_band_button.configure(state='normal' if self.spider_filter_kind.get()=='Band or range' or self.spider_filter_kind.get().endswith(('country','state')) else 'disabled')
        self.spider_filter_kind.trace_add('write',update_spider_band_button);update_spider_band_button()
        spider_actions = ttk.Frame(self.spider_filter); spider_actions.pack(anchor='w', pady=(10, 0))
        ttk.Button(spider_actions, text='Accept matching', command=lambda: self.change_filter('accept')).pack(side='left')
        ttk.Button(spider_actions, text='Reject matching', command=lambda: self.change_filter('reject')).pack(side='left', padx=8)
        ttk.Button(spider_actions, text='Clear this line', command=lambda: self.change_filter('clear')).pack(side='left')
        ttk.Button(spider_actions, text='Clear all lines…', command=self.clear_spider_stream).pack(side='left', padx=(8,0))
        spider_lookups=ttk.Frame(self.spider_filter);spider_lookups.pack(anchor='w',pady=(8,0))
        ttk.Button(spider_lookups,text='Find node country number…',command=self.lookup_main_prefix).pack(side='left')
        ttk.Button(spider_lookups,text='Look up node state…',command=self.lookup_main_state).pack(side='left',padx=(8,0))
        ttk.Label(filter_page, text='After a change, use Show node filters to check the node response.', style='Muted.TLabel').pack(anchor='w', side='bottom', pady=(8, 0))
        ttk.Label(logger_page, text='Connect a logging program', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(logger_page, text='Use a local Telnet connection to receive spots and send commands to the main node.', style='Muted.TLabel').pack(anchor='w', pady=(3, 6))
        logger_bar = ttk.Frame(logger_page); logger_bar.pack(fill='x')
        ttk.Label(logger_bar, text='LOCAL PORT', style='Muted.TLabel').pack(side='left', padx=(0, 8))
        self.logger_port_entry = ttk.Entry(logger_bar, textvariable=self.logger_port, width=8); self.logger_port_entry.pack(side='left', padx=(0, 8))
        self.logger_start = ttk.Button(logger_bar, text='Start listener', style='Accent.TButton', command=self.start_logger); self.logger_start.pack(side='left')
        self.logger_stop = ttk.Button(logger_bar, text='Stop listener', command=self.stop_logger, state='disabled'); self.logger_stop.pack(side='left', padx=8)
        ttk.Label(logger_bar, textvariable=self.logger_status, style='Muted.TLabel').pack(side='left', padx=8)
        ttk.Button(logger_bar,text='Spot bands…',command=self.open_logger_band_options).pack(side='right')
        ttk.Label(logger_page, text='Connect to 127.0.0.1 and the port above. Up to four local programs can send cluster commands.', style='Muted.TLabel').pack(anchor='w', pady=(6, 2))
        logger_options=ttk.Frame(logger_page);logger_options.pack(anchor='w')
        self.logger_ignore_check=ttk.Checkbutton(logger_options,text='Ignore BYE from logger',variable=self.logger_ignore_bye)
        self.logger_ignore_check.pack(side='left',padx=(0,12))
        self.logger_spots_check=ttk.Checkbutton(logger_options,text='Spots only (no node replies)',variable=self.logger_spots_only)
        self.logger_spots_check.pack(side='left',padx=(0,12))
        ttk.Button(logger_options,text='Node text…',command=self.open_logger_text_options).pack(side='left',padx=(0,12))
        ttk.Button(logger_options,text='Serial logger…',command=self.open_serial_logger).pack(side='left')
        ttk.Separator(logger_page).pack(fill='x', pady=8)
        ttk.Label(logger_page, text='Record received spots', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(logger_page, text='CSV recording includes requested history and duplicates.', style='Muted.TLabel').pack(anchor='w', pady=(3, 6))
        log_bar = ttk.Frame(logger_page); log_bar.pack(fill='x')
        self.log_start = ttk.Button(log_bar, text='Start recording…', command=self.start_spot_log); self.log_start.pack(side='left')
        self.log_resume = ttk.Button(log_bar, text='Resume recording…', command=self.resume_spot_log); self.log_resume.pack(side='left', padx=(8, 0))
        self.log_stop = ttk.Button(log_bar, text='Stop recording', command=self.stop_spot_log, state='disabled'); self.log_stop.pack(side='left', padx=8)
        ttk.Button(log_bar, text='View recording…', command=self.open_spot_archive).pack(side='left', padx=(0, 8))
        ttk.Button(log_bar,text='View parts…',command=self.open_spot_archive_parts).pack(side='left')
        ttk.Label(logger_page,textvariable=self.spot_log_status,style='Muted.TLabel').pack(anchor='w',pady=(4,0))
        settings_header = ttk.Frame(settings_page); settings_header.pack(fill='x', pady=(0, 10))
        ttk.Button(settings_header,text='QSL data…',command=self.open_lotw_file).pack(side='right',padx=(8,0))
        ttk.Button(settings_header,text='Country file…',command=self.open_country_file).pack(side='right',padx=(8,0))
        ttk.Label(settings_header, text='Node settings', style='Heading.TLabel').pack(side='left')
        self.settings_note = tk.StringVar(value='Connect to a node to manage settings.')
        ttk.Label(settings_header, textvariable=self.settings_note, style='Muted.TLabel').pack(side='left', padx=12)
        self.settings_refresh = ttk.Button(settings_header, text='Refresh settings', command=self.refresh_settings, state='disabled')
        self.settings_refresh.pack(side='right')
        settings_table = ttk.Frame(settings_page)
        self.settings_tree = ttk.Treeview(settings_table, columns=('setting', 'value'), show='headings')
        self.settings_tree.heading('setting', text='SETTING'); self.settings_tree.heading('value', text='VALUE')
        self.settings_tree.column('setting', width=280, minwidth=180); self.settings_tree.column('value', width=400, minwidth=160)
        settings_scroll = ttk.Scrollbar(settings_table, orient='vertical', command=self.settings_tree.yview)
        settings_scroll.pack(side='right', fill='y'); self.settings_tree.pack(side='left', fill='both', expand=True); self.settings_tree.configure(yscrollcommand=settings_scroll.set)
        settings_actions = ttk.Frame(settings_page); settings_actions.pack(side='bottom',fill='x',pady=(10,0))
        self.setting_enable = ttk.Button(settings_actions, text='Enable selected', command=lambda: self.change_setting(True), state='disabled'); self.setting_enable.pack(side='left')
        self.setting_disable = ttk.Button(settings_actions, text='Disable selected', command=lambda: self.change_setting(False), state='disabled'); self.setting_disable.pack(side='left', padx=8)
        ttk.Button(settings_actions, text='My node profile…', command=self.open_node_profile).pack(side='left', padx=4)
        self.skimmer_button=ttk.Button(settings_actions,text='Skimmer categories…',command=self.open_skimmer_categories,state='disabled');self.skimmer_button.pack(side='left',padx=4)
        ttk.Button(settings_actions,text='About this build…',command=self.open_build_info).pack(side='right')
        self.settings_tree.bind('<<TreeviewSelect>>', lambda event: self.update_setting_actions())
        ttk.Label(settings_page, text='DXSpider refresh can expose account fields in Activity and to connected loggers. Settings can interact.', style='Muted.TLabel').pack(side='bottom',anchor='w',pady=(8,0))
        settings_table.pack(fill='both',expand=True)
        suffix='dark' if self.dark_theme.get() else 'light'
        band_shortcuts=ttk.Frame(spot_page);band_shortcuts.pack(fill='x',pady=(0,8));self.band_shortcuts=band_shortcuts
        ttk.Label(band_shortcuts,text='DISPLAY\nBANDS',style='Muted.TLabel').grid(row=0,column=0,sticky='w',padx=(0,8))
        self.band_shortcut_buttons={};self.band_shortcut_counts={}
        shortcut_bands=[('All','All bands'),('160','160 m'),('80','80 m'),('60','60 m'),('40','40 m'),('30','30 m'),('20','20 m'),('17','17 m'),
                        ('15','15 m'),('12','12 m'),('10','10 m'),('6','6 m'),('4','4 m'),('2 m','2 m'),('1.25 m','1.25 m'),('70 cm','70 cm')]
        for index,(label,value) in enumerate(shortcut_bands):
            group=ttk.Frame(band_shortcuts);group.grid(row=index//8,column=index%8+1,sticky='w',padx=(0,3),pady=(0,3))
            button=ttk.Button(group,text=label,width=5,style='Band.TButton',command=lambda v=value:self.toggle_band(v))
            button.pack(side='left');self.band_shortcut_buttons[value]=button
            count=ttk.Label(group,text='0',width=4,anchor='center',style='BandCount.TLabel',cursor='hand2')
            count.pack(side='left');count.bind('<Button-1>',lambda event,v=value:self.toggle_band(v))
            self.band_shortcut_counts[value]=count
        spot_filters = ttk.Frame(spot_page); spot_filters.pack(fill='x', pady=(0, 10));self.spot_filters=spot_filters
        ttk.Label(spot_filters, text='SEARCH  Ctrl+F', style='Muted.TLabel').pack(side='left', padx=(0,6))
        self.search_box = ttk.Entry(spot_filters, textvariable=self.search, width=22); self.search_box.pack(side='left')
        self.search_box.bind('<Return>',self.search_connected_nodes)
        self.search_field_box = ttk.Combobox(spot_filters, textvariable=self.search_field, values=['All fields','DX call','Spotter','Comment','Frequency','DX country','Spotter country','DX grid','Spotter grid','Origin'], state='readonly', width=15)
        self.search_field_box.pack(side='left',padx=8)
        self.band_box = ttk.Combobox(spot_filters, textvariable=self.band, values=['All bands'] + [x[0] for x in BANDS] + ['Other','Choose bands…'], state='readonly', width=11)
        self.band_box.pack(side='left')
        self.band_box.bind('<<ComboboxSelected>>',lambda event:self.open_band_picker() if self.band.get()=='Choose bands…' else None)
        self.spot_source_box=ttk.Combobox(spot_filters,textvariable=self.spot_source_filter,values=['All sources'],state='readonly',width=32)
        self.spot_source_box.pack(side='left',padx=(8,0));self.spot_source_box.bind('<<ComboboxSelected>>',lambda e:self.render_spots())
        self.spot_source_filter.trace_add('write',self.spot_search_source_changed)
        self.search.trace_add('write', self.spot_search_changed)
        self.search_field.trace_add('write', self.spot_search_changed)
        self.band.trace_add('write',self.spot_band_changed)
        self.update_band_shortcuts()
        spot_summary=ttk.Frame(spot_page);spot_summary.pack(fill='x',pady=(0,8))
        ttk.Label(spot_summary,textvariable=self.spot_search_note,style='Muted.TLabel',anchor='w').pack(side='left',fill='x',expand=True)
        ttk.Label(spot_summary,textvariable=self.spot_wwv_summary,style='Muted.TLabel',anchor='e').pack(side='right')
        workspace=ttk.Frame(spot_page);self.spot_workspace=workspace
        table = ttk.Frame(workspace);self.spot_table=table
        self.spots = ttk.Treeview(table, columns=('time','frequency','band','dx','spotter','comment'), show='headings', selectmode='browse', style='Spot.Treeview')
        self.spots.tag_configure('alternate', background='#1c2935' if self.dark_theme.get() else '#f5f9fa')
        for name,color in (DARK_BAND_COLORS if self.dark_theme.get() else BAND_COLORS).items(): self.spots.tag_configure('band-'+name,background=color)
        self.spot_headings = dict(time='DATE / UTC',frequency='FREQ  kHz',band='BAND',dx='DX CALL',spotter='SPOTTER',comment='COMMENT')
        for col, width in [('time',105), ('frequency',95), ('band',80), ('dx',105), ('spotter',105), ('comment',330)]:
            alignment='e' if col=='frequency' else 'w'
            self.spots.heading(col, text=self.spot_headings[col], anchor=alignment, command=lambda c=col:self.sort_spots(c)); self.spots.column(col, width=width, minwidth=55, anchor=alignment, stretch=(col == 'comment'))
        scroll=ttk.Scrollbar(table, orient='vertical', command=self.spots.yview); scroll.pack(side='right', fill='y')
        self.spots.pack(side='left', fill='both', expand=True); self.spots.configure(yscrollcommand=scroll.set)
        self.spot_empty = ttk.Frame(self.spots, style='Surface.TFrame')
        self.spot_empty_title = tk.StringVar(value='Your spot stream is ready')
        self.spot_empty_hint = tk.StringVar(value='Connect to a cluster to start receiving spots.')
        ttk.Label(self.spot_empty, textvariable=self.spot_empty_title, style='EmptyTitle.TLabel').pack()
        ttk.Label(self.spot_empty, textvariable=self.spot_empty_hint, style='EmptyHint.TLabel', wraplength=420, justify='center').pack(pady=(4, 0))
        self.empty_spot_actions=ttk.Frame(self.spot_empty,style='Surface.TFrame')
        self.reset_spot_filters_button=ttk.Button(self.empty_spot_actions,text='Reset search, band and source',command=self.reset_spot_filters)
        self.empty_display_options_button=ttk.Button(self.empty_spot_actions,text='Display options',image=self.ui_icons['monitor_'+suffix],compound='left',command=self.open_display_bands)
        self.spot_empty.place(relx=.5, rely=.5, anchor='center')
        spot_footer=ttk.Frame(spot_page); spot_footer.pack(side='bottom', fill='x');self.spot_footer=spot_footer
        spot_detail_header=ttk.Frame(spot_footer); spot_detail_header.pack(fill='x', pady=(6, 4))
        self.spot_detail_title=ttk.Label(spot_detail_header, text='SELECTED SPOT', style='Muted.TLabel');self.spot_detail_title.pack(side='left')
        self.station_info_button=ttk.Button(spot_detail_header,text='Station info',image=self.ui_icons['house_'+suffix],compound='none',width=4,command=self.open_station)
        self.station_info_button.pack(side='right',padx=(8,0))
        self.station_info_hint=ButtonHint(self.station_info_button,lambda:'Station info',lambda:(self.theme_surface,self.theme_ink))
        self.display_options_button=ttk.Button(spot_detail_header,text='Display options',image=self.ui_icons['monitor_'+suffix],compound='none',width=4,command=self.open_display_bands)
        self.display_options_button.pack(side='right',padx=8)
        self.display_options_hint=ButtonHint(self.display_options_button,lambda:'Display options',lambda:(self.theme_surface,self.theme_ink))
        tools=ttk.Frame(spot_detail_header);tools.pack(side='right')
        ttk.Label(tools,textvariable=self.spot_count,style='Muted.TLabel').pack(side='left',padx=10)
        self.clear_spots_button=ttk.Button(tools,text='Clear spots',image=self.ui_icons['trash_'+suffix],compound='none',width=4,command=self.clear_spots);self.clear_spots_button.pack(side='right')
        self.clear_spots_hint=ButtonHint(self.clear_spots_button,lambda:'Clear spots · right-click for selected bands',lambda:(self.theme_surface,self.theme_ink))
        self.clear_spots_menu=tk.Menu(self,tearoff=False)
        self.clear_spots_menu.add_command(label='Clear spots in selected bands',command=self.clear_selected_band_spots)
        self.clear_spots_menu.add_command(label='Clear all spots',command=self.clear_spots)
        self.clear_spots_button.bind('<Button-3>',self.open_clear_spots_menu)
        self.clear_spots_button.bind('<Shift-F10>',self.open_clear_spots_menu)
        self.spot_lock_button=ttk.Checkbutton(tools,text='Lock scroll',image=self.ui_icons['lock_'+suffix],compound='none',variable=self.spot_view_locked,style='Lock.Toolbutton',width=4)
        self.spot_lock_button.pack(side='right',padx=(0,8))
        self.spot_lock_hint=ButtonHint(self.spot_lock_button,lambda:'Unlock scroll' if self.spot_view_locked.get() else 'Lock scroll',lambda:(self.theme_surface,self.theme_ink))
        self.qrz_button=ttk.Button(tools,text='QRZ',image=self.ui_icons['globe_'+suffix],compound='none',width=4,command=self.open_qrz_selector);self.qrz_button.pack(side='right',padx=(6,0))
        self.qrz_hint=ButtonHint(self.qrz_button,lambda:'QRZ lookup',lambda:(self.theme_surface,self.theme_ink))
        self.history_button=ttk.Button(tools,text='History',image=self.ui_icons['book_'+suffix],compound='none',width=4,command=self.open_older_spots);self.history_button.pack(side='right',padx=(6,0))
        self.history_hint=ButtonHint(self.history_button,lambda:'History (older spots)',lambda:(self.theme_surface,self.theme_ink))
        self.refresh_spots_button=ttk.Button(tools,text='⟳',width=3,style='Refresh.Icon.TButton',command=self.refresh_spots);self.refresh_spots_button.pack(side='right',padx=6);self.update_report_actions()
        self.refresh_spots_hint=ButtonHint(self.refresh_spots_button,lambda:'Refresh spots',lambda:(self.theme_surface,self.theme_ink))
        ttk.Label(spot_detail_header, textvariable=self.hidden_bands_note, style='Muted.TLabel').pack(side='right', padx=4)
        detail_frame=ttk.Frame(spot_page); detail_frame.pack(in_=spot_footer,fill='x');self.spot_detail_frame=detail_frame
        self.inspector_heading=ttk.Label(detail_frame,text='SELECTED SPOT',style='Muted.TLabel')
        self.spot_detail = tk.Text(detail_frame, height=2, background=self.theme_surface, foreground=self.theme_ink, wrap='word', relief='flat', padx=8, pady=5, font=('Segoe UI', 10), state='disabled')
        self._detail_selected=None;self.style_spot_inspector()
        detail_scroll=ttk.Scrollbar(detail_frame, orient='vertical', command=self.spot_detail.yview)
        detail_scroll.pack(side='right', fill='y'); self.spot_detail.pack(side='left', fill='x', expand=True)
        self.spot_detail.configure(yscrollcommand=detail_scroll.set)
        self.show_spot_detail()
        self.spots.bind('<<TreeviewSelect>>', lambda event: self.show_spot_detail())
        self.spots.bind('<Shift-Button-1>',self.open_qrz_from_spot_event)
        self.spot_copy_menu=tk.Menu(self.spots,tearoff=False)
        for label,field in (('Copy DX call','dx'),('Copy frequency (kHz)','frequency'),('Copy spotter call','spotter')):
            self.spot_copy_menu.add_command(label=label,command=lambda f=field:self.copy_selected_spot(f))
        self.spots.bind('<Button-3>',self.open_spot_copy_menu)
        self.spots.bind('<Control-c>',lambda event:self.copy_selected_spot('dx'))
        self.spot_source_label=ttk.Label(spot_footer, textvariable=self.spot_source_note, style='Muted.TLabel');self.spot_source_label.pack(anchor='w', pady=(7, 0))
        self.spot_footer_hint=ttk.Label(spot_footer,style='Muted.TLabel')
        workspace.pack(fill='both',expand=True)
        table.pack(side='left',fill='both', expand=True)
        activity_header=ttk.Frame(self.console_page);activity_header.pack(fill='x',pady=(0,8))
        ttk.Label(activity_header, text='Activity', style='Heading.TLabel').pack(side='left')
        ttk.Button(activity_header,text='Jump to latest',command=lambda:self.transcript.see('end')).pack(side='right')
        transcript_frame=ttk.Frame(self.console_page); transcript_frame.pack(fill='both', expand=True)
        self.transcript=tk.Text(transcript_frame, background=self.theme_surface, foreground=self.theme_ink, insertbackground=self.theme_ink, wrap='word', relief='flat', padx=14, pady=12, font=('Consolas', 10), state='disabled')
        transcript_scroll=ttk.Scrollbar(transcript_frame, orient='vertical', command=self.transcript.yview); transcript_scroll.pack(side='right', fill='y')
        self.transcript.pack(side='left', fill='both', expand=True); self.transcript.configure(yscrollcommand=transcript_scroll.set)
        bottom=ttk.Frame(outer); bottom.pack(side='bottom', fill='x', pady=(15, 0));self.command_bar=bottom
        self.command_label=ttk.Label(bottom,text='COMMAND  ·  ENTER TO SEND',style='Muted.TLabel');self.command_label.pack(anchor='w',pady=(0,4))
        row=ttk.Frame(bottom);row.pack(fill='x');self.command_row=row
        self.command_text=tk.StringVar();self.command_draft_target=None
        def command_edited(*_):
            self.command_draft_target=(self.generation,self.connection) if self.command_text.get() and self.connection and self.status.get()=='Connected' else None
            if not self.command_history_navigating:self.command_history_index=None;self.command_history_draft=''
        self.command_text.trace_add('write',command_edited)
        self.command=ttk.Entry(row,textvariable=self.command_text); self.command.pack(side='left', fill='x', expand=True); self.command.bind('<Return>', self.send)
        self.command.bind('<Up>',lambda event:self.recall_command(-1));self.command.bind('<Down>',lambda event:self.recall_command(1))
        self.send_btn=ttk.Button(row, text='↵', width=3, style='CommandSend.Accent.TButton', command=self.send, state='disabled'); self.send_btn.pack(side='left', padx=(8, 0))
        self.send_hint=ButtonHint(self.send_btn,lambda:'Send command',lambda:(self.theme_surface,self.theme_ink))
        self.spot_button=ttk.Button(row,text='Post a spot',image=(self.ui_icons['post_'+suffix],'disabled',self.ui_icons['post_disabled_'+suffix]),compound='none',width=4,command=self.open_spot,state='disabled')
        self.spot_button.pack(side='left',padx=(8,0));self.spot_button_hint=ButtonHint(self.spot_button,lambda:'Post a spot',lambda:(self.theme_surface,self.theme_ink));self.update_report_actions()
        self.theme_button=ttk.Button(row,text='Light' if self.dark_theme.get() else 'Dark',image=self.ui_icons['sun' if self.dark_theme.get() else 'moon'],compound='none',width=4,command=self.switch_theme);self.theme_button.pack(side='left',padx=(8,0))
        self.theme_hint=ButtonHint(self.theme_button,lambda:'Switch to light theme' if self.dark_theme.get() else 'Switch to dark theme',lambda:(self.theme_surface,self.theme_ink))
        self.notebook.pack(fill='both', expand=True)
        self.bind('<Control-f>',self.focus_spot_search)
        self.notebook.bind('<<NotebookTabChanged>>',self.unread_tab_changed,add='+')
        self.notebook.bind('<Configure>',lambda event:self.schedule_unread_refresh(),add='+')

    def focus_spot_search(self, event=None):
        self.notebook.select(0)
        self.search_box.focus_set()
        return 'break'

    def schedule_unread_refresh(self):
        if self.closing:return
        if self._unread_pending is None:self._unread_pending=self.after_idle(self.refresh_unread_tabs)

    def update_band_shortcuts(self):
        for name,button in self.band_shortcut_buttons.items():
            selected = self.selected_bands is None if name=='All bands' else self.selected_bands is not None and name in self.selected_bands
            button.configure(style='BandSelected.TButton' if selected else 'Band.TButton')

    def toggle_band(self, name):
        if name=='All bands':
            self.band.set('All bands');return
        selected=set() if self.selected_bands is None else set(self.selected_bands)
        if name in selected:selected.remove(name)
        else:selected.add(name)
        self.selected_bands=selected
        self.band.set(next(iter(selected)) if len(selected)==1 else f'{len(selected)} bands' if selected else 'No bands')

    def open_band_picker(self):
        dialog=tk.Toplevel(self);dialog.title('Choose bands');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Select bands to show',style='Heading.TLabel').pack(anchor='w')
        grid=ttk.Frame(panel);grid.pack(pady=(8,0))
        names=[name for name,_,_ in BANDS]+['Other']
        initial={name for name in names if name not in self.hidden_bands} if self.selected_bands is None else set(self.selected_bands)
        choices={}
        for index,name in enumerate(names):
            choices[name]=tk.BooleanVar(master=dialog,value=name in initial)
            ttk.Checkbutton(grid,text=name,variable=choices[name]).grid(row=index//4,column=index%4,sticky='w',padx=(0,12),pady=3)
        ttk.Label(panel,text='All bands uses your saved display options.',style='Muted.TLabel').pack(anchor='w',pady=(8,0))
        actions=ttk.Frame(panel);actions.pack(fill='x',pady=(10,0))
        def restore():
            selected=self.selected_bands
            self.band.set('All bands' if selected is None else next(iter(selected)) if len(selected)==1 else f'{len(selected)} bands' if selected else 'No bands')
        def cancel():restore();dialog.destroy()
        def apply():
            selected={name for name in names if choices[name].get()}
            if self.selected_bands is not None or selected != initial:self.selected_bands=selected
            restore();dialog.destroy()
        def show_all():self.band.set('All bands');dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW',cancel)
        dialog.bind('<Escape>',lambda event:cancel())
        ttk.Button(actions,text='All bands',command=show_all).pack(side='left')
        ttk.Button(actions,text='Clear selection',command=lambda:[choice.set(False) for choice in choices.values()]).pack(side='left',padx=(8,0))
        ttk.Button(actions,text='Cancel',command=cancel).pack(side='right')
        ttk.Button(actions,text='Apply',command=apply,style='Accent.TButton').pack(side='right',padx=(0,8))
        dialog.grab_set();dialog.focus_set()

    def spot_band_changed(self, *_):
        value=self.band.get()
        if value=='All bands':self.selected_bands=None
        elif value in SPOT_BANDS:self.selected_bands={value}
        self.hidden_bands_note.set(self.hidden_bands_label())
        self.update_band_shortcuts();self.render_spots()

    def refresh_unread_tabs(self):
        self._unread_pending=None
        compact=self.notebook.winfo_width()<850
        for key,page,label in (('messages',self.ann_page,'Messages'),('reports',self.report_page,'Propagation'),('alarms',self.alarm_page,'Alarms')):
            count=self.unread_counts[key]
            suffix=(' •' if compact else ' ('+('999+' if count>=1000 else str(count))+')') if count else ''
            self.notebook.tab(page,text=label+suffix)

    def mark_unread(self, key):
        pages={'messages':self.ann_page,'reports':self.report_page,'alarms':self.alarm_page}
        if self.notebook.select()!=str(pages[key]):
            self.unread_counts[key]=min(1000,self.unread_counts[key]+1);self.schedule_unread_refresh()

    def clear_unread(self, key):
        if self.unread_counts[key]:self.unread_counts[key]=0;self.schedule_unread_refresh()

    def unread_tab_changed(self, _event=None):
        selected=self.notebook.select()
        if selected==str(self.console_page) and (self.manual_uncertain_notice or self.spot_uncertain_notice):
            self.manual_uncertain_notice=False;self.spot_uncertain_notice=False;self.update_spot_recovery_display()
        for key,page in (('messages',self.ann_page),('reports',self.report_page),('alarms',self.alarm_page)):
            if selected==str(page):self.clear_unread(key)

    def adjust_layout(self, event):
        if event.widget is not self or event.height < 100:return
        self.adjust_spot_inspector(event.width >= 1200 and event.height >= 680)
        compact=event.height < 680
        if compact == self._compact_layout:return
        self._compact_layout=compact
        if compact:
            self.command_label.pack_forget()
            self.band_shortcuts.pack_forget()
            self.spot_footer_hint.pack_forget();self.ann_footer_hint.pack_forget()
            for hint in self.report_footer_hints: hint.pack_forget()
            self.connection_bar.pack_configure(pady=(4,6));self.command_bar.pack_configure(pady=(6,0))
            self.spot_detail.configure(height=1);self.ann_detail.configure(height=2)
        else:
            self.band_shortcuts.pack(fill='x',pady=(0,8),before=self.spot_filters)
            self.command_label.pack(anchor='w',pady=(0,4),before=self.command_row)
            self.ann_footer_hint.pack(anchor='w',pady=(8,0))
            for hint in reversed(self.report_footer_hints): hint.pack(side='bottom',anchor='w',pady=(8,0),before=self.report_table)
            self.connection_bar.pack_configure(pady=(6,8));self.command_bar.pack_configure(pady=(8,0))
            self.spot_detail.configure(height=2);self.ann_detail.configure(height=3)
        self.update_spot_recovery_display()

    def adjust_spot_inspector(self, side):
        if side == self._spot_side_layout:return
        self._spot_side_layout=side
        self.spot_detail_frame.pack_forget()
        if side:
            self.spot_detail_title.pack_forget()
            self.spot_detail_frame.configure(width=300);self.spot_detail_frame.pack_propagate(False)
            self.spot_detail_frame.pack(in_=self.spot_workspace,side='right',fill='y',padx=(12,0),before=self.spot_table)
            self.inspector_heading.pack(side='top',anchor='w',pady=(0,5),before=self.spot_detail)
            self.spot_detail.configure(width=32);self.spot_detail.pack_configure(fill='both')
        else:
            self.spot_detail_title.pack(side='left')
            self.inspector_heading.pack_forget();self.spot_detail_frame.pack_propagate(True)
            self.spot_detail.configure(width=80);self.spot_detail.pack_configure(fill='x')
            self.spot_detail_frame.pack(in_=self.spot_footer,fill='x',before=self.spot_source_label)
        self.style_spot_inspector()

    def style_spot_inspector(self):
        side=getattr(self,'_spot_side_layout',False)
        self.spot_detail.tag_configure('callsign',font=('Segoe UI Semibold',13 if side else 10),foreground=self.theme_accent)
        self.spot_detail.tag_configure('placeholder',foreground=self.theme_muted)

    def open_build_info(self):
        dialog=tk.Toplevel(self);dialog.title('About Cluster Desk by N2WQ');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Cluster Desk by N2WQ',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Standalone beta · functionality is still under development.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        ttk.Label(panel,text='BUILD FINGERPRINT (SHA-256)',style='Muted.TLabel').pack(anchor='w')
        fingerprint=tk.StringVar(master=dialog,value='Calculating…')
        ttk.Entry(panel,textvariable=fingerprint,state='readonly',width=68).pack(fill='x',pady=(4,8))
        ttk.Label(panel,text='Include this fingerprint in a beta report. It identifies the executable file.',style='Muted.TLabel',wraplength=500).pack(anchor='w')
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(12,0))
        copy=ttk.Button(buttons,text='Copy fingerprint',state='disabled');copy.pack(side='left')
        ttk.Button(buttons,text='Close',command=dialog.destroy).pack(side='right')
        def copy_value():self.clipboard_clear();self.clipboard_append(fingerprint.get())
        copy.configure(command=copy_value)
        path=packaged_executable()
        if path is None:fingerprint.set('Source mode: no packaged executable');return
        result=queue.Queue(maxsize=1);pending={'job':None}
        def calculate():
            try:result.put((True,executable_sha256(path)))
            except OSError:result.put((False,'Could not read the running executable'))
        def finish():
            pending['job']=None
            if not dialog.winfo_exists():return
            try:ok,value=result.get_nowait()
            except queue.Empty:pending['job']=self.after(100,finish);return
            fingerprint.set(value)
            if ok:copy.configure(state='normal')
        def stop(event):
            if event.widget is dialog and pending['job'] is not None:
                self.after_cancel(pending['job']);pending['job']=None
        dialog.bind('<Destroy>',stop)
        threading.Thread(target=calculate,daemon=True).start();pending['job']=self.after(100,finish)

    def open_country_file(self):
        dialog=tk.Toplevel(self);dialog.title('Country file');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Local country estimates',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Choose a full CTY.DAT file with prefix entries for country filters and estimates.\nGet CTY.DAT opens a browser; save the file, then choose it here.\nThe data may be outdated; portable calls may be ambiguous.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        ttk.Label(panel,textvariable=self.country_status,style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(0,10))
        row=ttk.Frame(panel);row.pack(fill='x')
        def choose():
            path=filedialog.askopenfilename(parent=dialog,title='Choose CTY.DAT',filetypes=[('Country files','*.dat'),('All files','*.*')])
            if path:self.start_country_load(path,persist=True)
        def get_file():
            try:
                if not webbrowser.open_new_tab('https://www.country-files.com/cty/cty.dat'):
                    raise OSError('The web browser could not be opened.')
            except (OSError,webbrowser.Error) as exc:messagebox.showerror('Country file',str(exc),parent=dialog)
        def clear():
            self.country_generation+=1;self.country_index=None;self.country_file='';self.country_status.set('No country file loaded')
            self.update_alarm_note()
            self.show_spot_detail()
            if not self._save_settings():messagebox.showwarning('Country file','Cleared for this session, but settings could not be saved. See Activity.',parent=dialog)
        ttk.Button(row,text='Choose file…',style='Accent.TButton',command=choose).pack(side='left')
        ttk.Button(row,text='Get CTY.DAT…',command=get_file).pack(side='left',padx=(8,0))
        ttk.Button(row,text='Clear',command=clear).pack(side='left',padx=8)
        ttk.Button(row,text='Close',command=dialog.destroy).pack(side='right')

    def open_lotw_file(self):
        dialog=tk.Toplevel(self);dialog.title('QSL data');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='LoTW activity',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='A + beside a DX call means an exact match in the activity list.\nThe last upload does not guarantee a QSO confirmation. The list may be outdated.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        ttk.Label(panel,textvariable=self.lotw_status,style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(0,10))
        row=ttk.Frame(panel);row.pack(fill='x')
        def choose():
            path=filedialog.askopenfilename(parent=dialog,title='Choose LoTW activity CSV',filetypes=[('CSV files','*.csv'),('All files','*.*')])
            if path:self.start_lotw_load(path,persist=True)
        def clear():
            self.lotw_generation+=1;self.lotw_index=None;self.lotw_file='';self.lotw_status.set('No LoTW activity list loaded')
            self.render_spots()
            if not self._save_settings():messagebox.showwarning('LoTW activity','Cleared for this session, but settings could not be saved. See Activity.',parent=dialog)
        ttk.Button(row,text='Download latest',style='Accent.TButton',command=lambda:self.start_lotw_load(str(SETTINGS.parent/'lotw-user-activity.csv'),persist=True,download=True)).pack(side='left')
        ttk.Button(row,text='Choose file…',command=choose).pack(side='left',padx=8)
        ttk.Button(row,text='Clear',command=clear).pack(side='left')
        ttk.Separator(panel,orient='horizontal').pack(fill='x',pady=14)
        ttk.Label(panel,text='eQSL Authenticity Guaranteed',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='A * indicates an exact call match in the dated eQSL AG list.\nIt is not a list of all eQSL users and does not guarantee a QSO confirmation.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        ttk.Label(panel,textvariable=self.eqsl_status,style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(0,10))
        eqrow=ttk.Frame(panel);eqrow.pack(fill='x')
        def choose_eqsl():
            path=filedialog.askopenfilename(parent=dialog,title='Choose dated eQSL AG list',filetypes=[('Text files','*.txt'),('All files','*.*')])
            if path:self.start_eqsl_load(path,persist=True)
        def clear_eqsl():
            self.eqsl_generation+=1;self.eqsl_index=None;self.eqsl_file='';self.eqsl_status.set('No eQSL AG list loaded')
            self.render_spots()
            if not self._save_settings():messagebox.showwarning('eQSL AG','Cleared for this session, but settings could not be saved. See Activity.',parent=dialog)
        ttk.Button(eqrow,text='Download latest',style='Accent.TButton',command=lambda:self.start_eqsl_load(str(SETTINGS.parent/'eqsl-ag-dated.txt'),persist=True,download=True)).pack(side='left')
        ttk.Button(eqrow,text='Choose file…',command=choose_eqsl).pack(side='left',padx=8)
        ttk.Button(eqrow,text='Clear',command=clear_eqsl).pack(side='left')
        ttk.Button(panel,text='Close',command=dialog.destroy).pack(anchor='e',pady=(12,0))

    def start_eqsl_load(self,path,persist,download=False):
        if self.eqsl_loading:
            self.eqsl_status.set('An eQSL list is already loading; try again shortly.');return
        if not isinstance(path,str) or not path or len(path)>2048 or '\x00' in path:
            self.eqsl_status.set('Invalid eQSL list path.');return
        self.eqsl_generation+=1;generation=self.eqsl_generation;self.eqsl_loading=True
        self.eqsl_status.set('Downloading eQSL AG list…' if download else 'Loading eQSL AG list…')
        def load():
            try:result=((download_eqsl_activity(path) if download else read_eqsl_activity(path)),None)
            except Exception as exc:result=(None,str(exc))
            self.eqsl_events.put_nowait((generation,path,persist,*result))
        threading.Thread(target=load,daemon=True).start()

    def poll_eqsl(self):
        try:generation,path,persist,index,error=self.eqsl_events.get_nowait()
        except queue.Empty:return
        self.eqsl_loading=False
        if generation!=self.eqsl_generation:return
        if error is not None:
            self.eqsl_status.set('Could not load eQSL AG list; previous data remains active.' if self.eqsl_index else 'Could not load eQSL AG list.')
            self.append('\neQSL AG: '+error+'\n');return
        self.eqsl_index=index;self.eqsl_file=path
        self.eqsl_status.set(f'Loaded {len(index.calls):,} calls · list {index.list_time} · {index.skipped} unsupported calls skipped')
        self.render_spots()
        if persist and not self._save_settings():self.eqsl_status.set('Loaded for this session; settings were not saved. See Activity.')

    def start_lotw_load(self,path,persist,download=False):
        if self.lotw_loading:
            self.lotw_status.set('An activity list is already loading; try again shortly.');return
        if not isinstance(path,str) or not path or len(path)>2048 or '\x00' in path:
            self.lotw_status.set('Invalid activity file path.');return
        self.lotw_generation+=1;generation=self.lotw_generation;self.lotw_loading=True
        self.lotw_status.set('Downloading activity list…' if download else 'Loading activity list…')
        def load():
            try:result=((download_activity(path) if download else read_activity(path)),None)
            except Exception as exc:result=(None,str(exc))
            self.lotw_events.put_nowait((generation,path,persist,*result))
        threading.Thread(target=load,daemon=True).start()

    def poll_lotw(self):
        try:generation,path,persist,index,error=self.lotw_events.get_nowait()
        except queue.Empty:return
        self.lotw_loading=False
        if generation!=self.lotw_generation:return
        if error is not None:
            self.lotw_status.set('Could not load activity list; previous data remains active.' if self.lotw_index else 'Could not load activity list.')
            self.append('\nLoTW activity: '+error+'\n');return
        self.lotw_index=index;self.lotw_file=path
        self.lotw_status.set(f'Loaded {len(index):,} calls · {Path(path).name} · list may be out of date')
        self.render_spots()
        if persist and not self._save_settings():self.lotw_status.set('Loaded for this session; settings were not saved. See Activity.')

    def start_country_load(self,path,persist):
        if self.country_loading:
            self.country_status.set('A country file is already loading; try again shortly.');return
        if not isinstance(path,str) or not path or len(path)>2048 or '\x00' in path:
            self.country_status.set('Invalid country file path.');return
        self.country_generation+=1;generation=self.country_generation;self.country_loading=True
        self.country_status.set('Loading country file…')
        def load():
            try:result=(CountryIndex.from_file(path),None)
            except Exception as exc:result=(None,str(exc))
            self.country_events.put_nowait((generation,path,persist,*result))
        threading.Thread(target=load,daemon=True).start()

    def poll_country(self):
        try:generation,path,persist,index,error=self.country_events.get_nowait()
        except queue.Empty:return
        self.country_loading=False
        if generation!=self.country_generation:return
        if error is not None:
            self.country_status.set('Could not load country file; previous data remains active.' if self.country_index else 'Could not load country file.')
            self.append('\nCountry file: '+error+'\n');return
        self.country_index=index;self.country_file=path
        self.update_alarm_note()
        self.country_status.set('Loaded: '+Path(path).name+' · data may be out of date')
        self.show_spot_detail()
        if persist and not self._save_settings():self.country_status.set('Loaded for this session; settings were not saved. See Activity.')

    def open_local_grid(self):
        dialog=tk.Toplevel(self);dialog.title('My grid');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='My grid',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Used locally to estimate distance and true bearing to a spot with a grid.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        value=tk.StringVar(value=self.local_grid.get());ttk.Label(panel,text='MAIDENHEAD GRID  ·  4 OR 6 CHARACTERS',style='Muted.TLabel').pack(anchor='w')
        entry=ttk.Entry(panel,textvariable=value,width=32);entry.pack(fill='x',pady=(4,10));entry.focus_set()
        ttk.Label(panel,text='Leave blank to turn off estimates. This does not change the node profile.',style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def apply():
            grid=value.get().strip()
            try:
                if grid:grid_center(grid)
            except ValueError as exc:messagebox.showerror('My grid',str(exc),parent=dialog);return
            self.local_grid.set(grid.upper());self.show_spot_detail()
            if not self._save_settings():messagebox.showwarning('My grid','Applied for this session, but settings could not be saved. See Activity.',parent=dialog)
            dialog.destroy()
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Apply',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))

    def toggle_connection(self):
        if self.want_connection or self.connection or self.retry_after is not None:
            self.disconnect()
        else:
            self.connect()

    def set_connection_button(self, active):
        self.connect_btn.configure(text='Disconnect' if active else 'Connect',
                                   image=self.ui_icons[('stop' if active else 'play')+('_dark' if self.dark_theme.get() else '')],style='TButton', state='normal')

    def connect(self):
        try:
            node=validate_node('Main node',self.host.get(),self.port.get())
            host=node['host'];port=node['port'];call=validate_login_call(self.call.get())
            if any(item['source'] == (host.casefold(),port,call) and item['status'] != 'Disconnected' for item in self.secondary_nodes.values()):
                raise ValueError('That node and callsign are already connected as an additional node.')
        except ValueError as exc:
            messagebox.showerror('Connection',str(exc)); return
        self.disconnect(); self._save_settings()
        source = (host.casefold(), port, call)
        self.manual_reconnect = source in self.recent_connected_sources
        if self.spot_source != source and not any(item['status'] != 'Disconnected' for item in self.secondary_nodes.values()):
            self.rows.clear(); self.spot_times.clear(); self.spot_sources.clear(); self.render_spots(); self.clear_alarms()
            self.clear_spot_recovery_notice()
        self.spot_source = source; self.update_spot_source()
        if not any(item['status'] != 'Disconnected' for item in self.secondary_nodes.values()):
            self.reports.clear(); self.clear_unread('reports'); self.render_reports(); self.report_note.set('Connecting; reports from the prior node were cleared.')
            self.announcements.clear(); self.clear_unread('messages'); self.render_announcements(); self.ann_note.set('Connecting; messages from the prior node were cleared.')
        self.want_connection=True; self.retry_attempt=0; self.connection_target=(host,port,call)
        self._start_connection()

    def start_secondary_node(self, host, port, call, auto_reconnect=False):
        node=validate_node('Additional node',host,port);host=node['host'];port=node['port'];call=validate_login_call(call)
        source=(host.casefold(),port,call)
        if len(self.secondary_nodes)>=3: raise ValueError('Remove an additional node before adding another; the limit is three.')
        if any(item['source']==source for item in self.secondary_nodes.values()) or (self.connection_target and (self.connection_target[0].casefold(),self.connection_target[1],self.connection_target[2])==source):
            raise ValueError('That node and callsign are already in use.')
        self.secondary_next_id+=1;ident=self.secondary_next_id
        self.secondary_nodes[ident]={'source':source,'connection':None,'status':'Connecting…','dialect':'','partial':'',
                                     'overflow':threading.Event(),'epoch':0,'retry_after':None,'retry_attempt':0,'retry_streak':0,'connected_since':None,'discarding_long_line':False,
                                     'auto_reconnect':bool(auto_reconnect),'activity':'','activity_trimmed':0,'view':None,'report_collector':ReportCollector(),'chat_groups':set(),'chat_capture':None,'chat_deadline':None,
                                     'filter_capture':None,'filter_deadline':None,'filter_view':None,'filter_unresolved':False,'pending_manual_commands':{},'pending_spot_submissions':{}}
        try:self._start_secondary_attempt(ident)
        except Exception:
            self.secondary_nodes.pop(ident,None);raise
        self.render_secondary_nodes();self.update_spot_source();self.refresh_report_targets()
        return ident

    def start_saved_secondary_nodes(self):
        self.secondary_start_after=None
        if self.closing:return
        for item in self.startup_secondary_nodes:
            try:self.start_secondary_node(item['host'],item['port'],item['call'],item['auto_reconnect'])
            except (ValueError,OSError) as exc:self.append(f'\nCould not start additional node {item["host"]}:{item["port"]}: {exc}\n')

    def _start_secondary_attempt(self, ident):
        item=self.secondary_nodes.get(ident)
        if not item or self.closing:return
        self.interrupt_secondary_manual_commands(item)
        self.interrupt_spot_submissions(item)
        self.interrupt_station_for(ident);self.interrupt_secondary_chat(item);self.interrupt_secondary_filter(item)
        item['filter_unresolved']=False
        item['retry_after']=None;item['epoch']+=1;epoch=item['epoch'];item['partial']='';item['discarding_long_line']=False;item['dialect']='';item['report_collector']=ReportCollector();item['chat_groups'].clear();item['chat_capture']=None;item['chat_deadline']=None
        item['overflow']=overflow=threading.Event();item['status']='Connecting…';connection=None
        def emit(kind,value):
            try:self.secondary_events.put((ident,epoch,kind,value),timeout=.5)
            except queue.Full:
                overflow.set()
                if connection:connection.close()
        connection=Connection(emit);item['connection']=connection
        host,port,call=item['source'];connection.start(host,port,call)
        self.render_secondary_nodes();self.update_reply_action();self.refresh_report_targets()

    def _schedule_secondary_retry(self, ident):
        item=self.secondary_nodes.get(ident)
        if not item or not item['auto_reconnect'] or item['retry_after'] is not None or self.closing:return
        if item['connected_since'] is not None and time.monotonic()-item['connected_since'] >= 30:item['retry_streak']=0
        item['connected_since']=None
        delay=min(60,2 ** min(item['retry_streak']+1,6));item['retry_streak']+=1;item['retry_attempt']+=1
        item['status']=f'Reconnecting in {delay}s…'
        item['retry_after']=self.after(delay*1000,lambda i=ident:self._start_secondary_attempt(i))

    def stop_secondary_node(self, ident):
        self.interrupt_station_for(ident)
        item=self.secondary_nodes.pop(ident,None)
        if item:
            self.interrupt_secondary_manual_commands(item)
            self.interrupt_spot_submissions(item)
            self.interrupt_secondary_chat(item)
            self.interrupt_secondary_filter(item)
            if item['retry_after'] is not None:self.after_cancel(item['retry_after'])
            if item['connection']:item['connection'].close()
            if item['view'] is not None and item['view']['window'].winfo_exists():item['view']['window'].destroy()
            if item['filter_view'] is not None and item['filter_view']['window'].winfo_exists():item['filter_view']['window'].destroy()
        self.render_secondary_nodes();self.update_spot_source();self.update_reply_action();self.refresh_report_targets()

    def open_secondary_nodes(self):
        if self.secondary_window is not None and self.secondary_window.winfo_exists():self.secondary_window.lift();return
        dialog=tk.Toplevel(self);self.secondary_window=dialog;dialog.title('Additional nodes');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Additional nodes',style='Heading.TLabel').pack(anchor='w')
        summary=(f'{self.skipped_startup_nodes} saved node(s) were not loaded at startup; add them again if needed.' if self.skipped_startup_nodes else
                 'Spots join the main list. Choose a node below to manage its filters or precision.')
        ttk.Label(panel,text=summary,style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(5,8))
        self.secondary_tree=ttk.Treeview(panel,columns=('host','port','call','status'),show='headings',height=3,selectmode='browse')
        for name,label,width in [('host','HOST',190),('port','PORT',65),('call','CALLSIGN',105),('status','STATUS',150)]:
            self.secondary_tree.heading(name,text=label);self.secondary_tree.column(name,width=width,minwidth=55)
        self.secondary_tree.pack(fill='both',expand=True)
        saved_row=ttk.Frame(panel);saved_row.pack(fill='x',pady=(10,0))
        ttk.Label(saved_row,text='SAVED NODE',style='Muted.TLabel').pack(side='left',padx=(0,8))
        saved_name=tk.StringVar(master=dialog)
        saved_selector=ttk.Combobox(saved_row,textvariable=saved_name,values=[node['name'] for node in self.saved_nodes],state='readonly',width=28)
        saved_selector.pack(side='left')
        ttk.Label(saved_row,text='Optional: fills the address below.',style='Muted.TLabel').pack(side='left',padx=8)
        fields=ttk.Frame(panel);fields.pack(fill='x',pady=(10,0))
        host=tk.StringVar(master=dialog);port=tk.StringVar(master=dialog,value='7300');call=tk.StringVar(master=dialog,value=self.call.get())
        def refresh_saved(_event=None):saved_selector.configure(values=[node['name'] for node in self.saved_nodes])
        def use_saved(_event=None):
            node=next((node for node in self.saved_nodes if node['name']==saved_name.get()),None)
            if node:host.set(node['host']);port.set(str(node['port']))
        saved_selector.bind('<FocusIn>',refresh_saved);saved_selector.bind('<<ComboboxSelected>>',use_saved)
        for label,var,width in [('HOST',host,24),('PORT',port,7),('CALLSIGN',call,12)]:
            box=ttk.Frame(fields);box.pack(side='left',padx=(0,8));ttk.Label(box,text=label,style='Muted.TLabel').pack(anchor='w')
            ttk.Entry(box,textvariable=var,width=width).pack(pady=(3,0))
        retry=tk.BooleanVar(master=dialog,value=False)
        ttk.Checkbutton(panel,text='Automatically reconnect this node',variable=retry).pack(anchor='w',pady=(8,0))
        remember=tk.BooleanVar(master=dialog,value=False)
        ttk.Checkbutton(panel,text='Connect this node when the app starts',variable=remember).pack(anchor='w',pady=(4,0))
        ttk.Button(panel,text='Manage startup nodes…',command=self.open_startup_secondary_nodes).pack(anchor='w',pady=(6,0))
        ttk.Label(panel,text='Up to three additional nodes. Disconnect / remove cancels retries.',style='Muted.TLabel').pack(anchor='w',pady=8)
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def add():
            try:
                if remember.get() and len(self.startup_secondary_nodes)>=3:raise ValueError('The startup list is full. Remove a saved node before adding another.')
                ident=self.start_secondary_node(host.get(),port.get(),call.get(),auto_reconnect=retry.get())
                if remember.get():
                    h,p,c=self.secondary_nodes[ident]['source']
                    self.startup_secondary_nodes=load_startup_nodes([*self.startup_secondary_nodes,{'host':h,'port':p,'call':c,'auto_reconnect':retry.get()}])
                    if not self._save_settings():messagebox.showwarning('Additional nodes','Connected for this session, but the startup setting could not be saved. See Activity.',parent=dialog)
            except (ValueError,OSError) as exc:messagebox.showerror('Additional nodes',str(exc),parent=dialog)
        def remove():
            selected=self.secondary_tree.selection()
            if selected:
                ident=int(selected[0]);source=self.secondary_nodes[ident]['source']
                before=len(self.startup_secondary_nodes)
                self.startup_secondary_nodes=[item for item in self.startup_secondary_nodes if (item['host'].casefold(),item['port'],item['call'])!=source]
                self.stop_secondary_node(ident)
                if len(self.startup_secondary_nodes)!=before and not self._save_settings():messagebox.showwarning('Additional nodes','Removed for this session, but the startup setting could not be saved. See Activity.',parent=dialog)
        def activity():
            selected=self.secondary_tree.selection()
            if selected:self.open_secondary_activity(int(selected[0]))
        def filters():
            selected=self.secondary_tree.selection()
            if selected:self.open_secondary_filters(int(selected[0]))
            else:messagebox.showinfo('Node filters','Select an additional node first.',parent=dialog)
        def precision():
            selected=self.secondary_tree.selection()
            if selected:self.open_secondary_precision(int(selected[0]))
            else:messagebox.showinfo('Spot frequency precision','Select an additional node first.',parent=dialog)
        def close():self.secondary_window=None;dialog.destroy()
        ttk.Button(buttons,text='Connect',style='Accent.TButton',command=add).pack(side='left')
        ttk.Button(buttons,text='Disconnect / remove',command=remove).pack(side='left',padx=8)
        ttk.Button(buttons,text='Node activity…',command=activity).pack(side='left')
        ttk.Button(buttons,text='Close',command=close).pack(side='right')
        node_tools=ttk.Frame(panel);node_tools.pack(anchor='w',pady=(8,0))
        ttk.Button(node_tools,text='Manage selected node filters…',command=filters).pack(side='left')
        ttk.Button(node_tools,text='Frequency precision…',command=precision).pack(side='left',padx=(8,0))
        dialog.protocol('WM_DELETE_WINDOW',close);self.render_secondary_nodes()

    def open_secondary_precision(self,ident):
        item=self.secondary_nodes.get(ident)
        if not item or not item['connection'] or item['status']!='Connected' or item['dialect']!='CC Cluster':
            messagebox.showerror('Spot frequency precision','Select a connected CC Cluster node.',parent=self);return
        epoch=item['epoch'];connection=item['connection'];host,port,call=item['source']
        dialog=tk.Toplevel(self);dialog.title('Spot frequency precision');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Spot frequency precision',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text=f'{host}:{port} · {call}',style='Muted.TLabel').pack(anchor='w',pady=(4,8))
        ttk.Label(panel,text='Request one or two decimals from this node. Setting a value changes the node profile.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        note=tk.StringVar(master=dialog,value='Check node activity for the response.')
        def valid():
            current=self.secondary_nodes.get(ident)
            return bool(current is item and item['epoch']==epoch and item['connection'] is connection and item['status']=='Connected' and item['dialect']=='CC Cluster')
        def request(command):
            try:
                if not valid():raise OSError('The connection changed. Close this window and open it again.')
                connection.send(command)
            except (OSError,ValueError) as exc:messagebox.showerror('Spot frequency precision',str(exc),parent=dialog);return
            note.set('Request queued. Check node activity for the response.')
        buttons=ttk.Frame(panel);buttons.pack(anchor='w')
        ttk.Button(buttons,text='1 decimal',command=lambda:request('set/res 1')).pack(side='left')
        ttk.Button(buttons,text='2 decimals',command=lambda:request('set/res 2')).pack(side='left',padx=8)
        ttk.Button(buttons,text='Show current',command=lambda:request('sh/res')).pack(side='left')
        ttk.Label(panel,textvariable=note,style='Muted.TLabel').pack(anchor='w',pady=(8,8))
        footer=ttk.Frame(panel);footer.pack(fill='x')
        def activity():
            if not valid():messagebox.showerror('Spot frequency precision','The connection changed. Close this window and open it again.',parent=dialog);return
            self.open_secondary_activity(ident)
        ttk.Button(footer,text='Node activity…',command=activity).pack(side='left')
        ttk.Button(footer,text='Close',command=dialog.destroy).pack(side='right')

    def open_secondary_filters(self, ident):
        item=self.secondary_nodes.get(ident)
        if not item or item['status']!='Connected' or item['dialect'] not in ('CC Cluster','DXSpider'):
            messagebox.showerror('Node filters','Select a connected CC Cluster or DXSpider node.',parent=self);return
        epoch,connection,dialect=item['epoch'],item['connection'],item['dialect']
        host,port,call=item['source']
        dialog=tk.Toplevel(self);dialog.title('Additional node filters');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Node filters',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text=f'{host}:{port} · {call} · {dialect}',style='Muted.TLabel').pack(anchor='w',pady=(4,8))
        ttk.Label(panel,text='Changes affect traffic sent by this node. Live traffic may appear in the readback.',style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        note=tk.StringVar(master=dialog,value='Review the selected filter before sending.')
        def current():
            target=self.secondary_nodes.get(ident)
            return bool(target is item and target['epoch']==epoch and target['connection'] is connection and target['status']=='Connected' and target['dialect']==dialect)
        def ensure_current():
            if not current():raise OSError('The node connection changed. Close this window and open it again.')
        category=tk.StringVar(master=dialog,value='Keywords' if dialect=='CC Cluster' else 'Band or range')
        band=tk.StringVar(master=dialog,value='All bands')
        value=tk.StringVar(master=dialog)
        stream=tk.StringVar(master=dialog,value='Spots');line=tk.StringVar(master=dialog,value='0')
        if dialect=='DXSpider':
            row=ttk.Frame(panel);row.pack(fill='x',pady=(0,8))
            ttk.Label(row,text='TRAFFIC',style='Muted.TLabel').pack(side='left',padx=(0,8))
            ttk.Combobox(row,textvariable=stream,values=list(SPIDER_STREAMS),state='readonly',width=18).pack(side='left')
            ttk.Label(row,text='LINE',style='Muted.TLabel').pack(side='left',padx=(14,8))
            ttk.Combobox(row,textvariable=line,values=[str(i) for i in range(10)],state='readonly',width=4).pack(side='left')
        ttk.Label(panel,text='FILTER',style='Muted.TLabel').pack(anchor='w')
        selector=ttk.Combobox(panel,textvariable=category,values=list(CC_FILTERS) if dialect=='CC Cluster' else spider_categories('Spots'),state='readonly',width=36)
        selector.pack(fill='x',pady=(3,8))
        if dialect=='DXSpider':
            def stream_changed(*_):
                selector.configure(values=spider_categories(stream.get()));category.set('Band or range' if stream.get() in ('Spots','RBN spots') else 'Spotter callsign text');value.set('')
            stream.trace_add('write',stream_changed)
        selector.bind('<<ComboboxSelected>>',lambda event:value.set(''))
        ttk.Label(panel,text='VALUES OR EXPRESSION',style='Muted.TLabel').pack(anchor='w')
        value_row=ttk.Frame(panel);value_row.pack(fill='x',pady=(3,8))
        ttk.Entry(value_row,textvariable=value,width=55).pack(side='left',fill='x',expand=True)
        if dialect=='DXSpider':
            picker=ttk.Button(value_row,text='Choose…',command=lambda:self.open_filter_value_picker(value,category,dialect,dialog,stream=stream,ensure_current=ensure_current))
            picker.pack(side='left',padx=(6,0))
            def update_picker(*_):picker.configure(state='normal' if category.get()=='Band or range' or category.get().endswith(('country','state')) else 'disabled')
            category.trace_add('write',update_picker);update_picker()
        if dialect=='CC Cluster':
            band_row=ttk.Frame(panel);band_row.pack(fill='x',pady=(0,8))
            ttk.Label(band_row,text='APPLY TO BAND',style='Muted.TLabel').pack(side='left',padx=(0,8))
            scope_box=ttk.Combobox(band_row,textvariable=band,values=CC_FILTER_BANDS,state='disabled',width=14,height=len(CC_FILTER_BANDS));scope_box.pack(side='left')
            ttk.Label(band_row,text='Country/state spots only · changing filter selects All',style='Muted.TLabel').pack(side='left',padx=8)
            def choose_bands():
                try:ensure_current()
                except OSError as exc:messagebox.showerror('Choose bands',str(exc),parent=dialog);return
                self.open_filter_value_picker(value,category,dialect,dialog,ensure_current=ensure_current)
            picker=ttk.Button(panel,text='Choose bands and segments…',command=choose_bands)
            picker.pack(anchor='w',pady=(0,8))
            def update_picker(*_):
                picker.configure(state='normal' if category.get()=='Band / frequency segment' or category.get().endswith(('country','state')) else 'disabled')
                picker.configure(text='Choose countries…' if category.get().endswith('country') else 'Choose states / provinces…' if category.get().endswith('state') else 'Choose bands and segments…')
                supported=category.get() in CC_PER_BAND_CATEGORIES
                scope_box.configure(state='readonly' if supported else 'disabled')
                band.set('All bands')
            category.trace_add('write',update_picker);update_picker()
        if dialect=='DXSpider':
            hint=tk.StringVar(master=dialog)
            def update_hint(*_):
                kind=category.get()
                example=('Example: 20m or 14000/14350.' if kind=='Band or range' else
                         'Example: on 20m and by G.' if kind=='Advanced expression' else
                         'Use 2-letter states. State matching requires USDB on the node.' if kind.endswith('state') else
                         'Use prefixes or node country numbers; these are not necessarily ARRL DXCC codes.' if kind.endswith('country') else
                         'Use zone numbers or prefixes, separated by commas.' if kind.endswith('zone') else
                         'Enter a minimum from 1 to 9.' if kind=='Minimum skimmers' else
                         'Enter a minimum from 0 to 99 dB.' if kind=='Minimum RBN strength' else
                         'Use comma-separated values; end text with * to match the start.')
                hint.set(example+' Accept rules may exclude other traffic; Clear line removes both accept and reject.')
            category.trace_add('write',update_hint);update_hint()
            ttk.Label(panel,textvariable=hint,style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(0,8))
        else:
            hint=tk.StringVar(master=dialog)
            def update_cc_hint(*_):
                hint.set('Changes listed bands or segments; other bands may still pass. Modes mean frequency ranges.' if category.get()=='Band / frequency segment' else
                         'Replaces the entire node keyword filter; enter all desired keywords.' if category.get()=='Keywords' else
                         'All bands can replace or clear per-band rules. Check the node readback.' if category.get() in CC_PER_BAND_CATEGORIES else
                         'Example: K,VE for countries. Check the node readback.')
            category.trace_add('write',update_cc_hint);update_cc_hint()
            ttk.Label(panel,textvariable=hint,style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(0,8))
        def send(action):
            try:
                ensure_current()
                if dialect=='CC Cluster':command=filter_command(dialect,action,value.get(),category=category.get(),band=band.get())
                else:
                    if action!='clear' and category.get() not in spider_categories(stream.get()):raise ValueError('Choose a filter supported for this traffic type.')
                    expression='' if action=='clear' else spider_expression(category.get(),value.get())
                    command=filter_command(dialect,action,expression,int(line.get()),stream=stream.get())
                connection.send(command)
            except (ValueError,OSError) as exc:
                note.set('Change not sent; review the error and try again.')
                messagebox.showerror('Node filters',str(exc),parent=dialog);return
            self.note_secondary_filter_change(item)
            note.set('Change requested; show node filters and check its response.')
        def clear_all():
            selected_stream=stream.get()
            try:ensure_current();command=filter_command('DXSpider','clear_all',stream=selected_stream)
            except (ValueError,OSError) as exc:
                note.set('Clear not sent; review the error and try again.')
                messagebox.showerror('Node filters',str(exc),parent=dialog);return
            if not messagebox.askyesno('Clear node filters',f'Clear every accept and reject line for {selected_stream} on {host}?',parent=dialog):return
            try:
                ensure_current()
                if stream.get()!=selected_stream:raise OSError('The traffic type changed. Review the selection and try again.')
                connection.send(command)
            except (ValueError,OSError) as exc:
                note.set('Clear not sent; review the error and try again.')
                messagebox.showerror('Node filters',str(exc),parent=dialog);return
            self.note_secondary_filter_change(item)
            note.set('Clear requested; show node filters and check its response.')
        def reset_cc():
            try:ensure_current()
            except OSError as exc:
                note.set('Reset not sent; review the error and try again.')
                messagebox.showerror('Node filters',str(exc),parent=dialog);return
            if not messagebox.askyesno('Reset node filters',f'Reset all filters on {host} to that node’s defaults?\n\nThis changes the node profile and cannot be undone automatically.',parent=dialog):return
            try:
                ensure_current();connection.send(filter_command('CC Cluster','reset_all'))
            except (OSError,ValueError) as exc:
                note.set('Reset not sent; review the error and try again.')
                messagebox.showerror('Node filters',str(exc),parent=dialog);return
            self.note_secondary_filter_change(item)
            note.set('Reset requested; show node filters and check its response.')
        def show():
            try:
                ensure_current()
                capture=item['filter_capture']
                if capture and capture.state in ('queued','waiting','receiving'):raise OSError('A filter readback is already in progress for this node.')
                command=connection.send_tracked('sh/filter')
            except OSError as exc:messagebox.showerror('Node filters',str(exc),parent=dialog);return
            capture=FilterReadback();capture.queued(dialect,call);capture.ambiguous=item.get('filter_unresolved',False);capture.command=command;item['filter_capture']=capture;item['filter_deadline']=time.monotonic()+15
            self.open_secondary_filter_readback(item);note.set('Readback requested; check the response window.')
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(0,8))
        if dialect=='CC Cluster':
            ttk.Button(buttons,text='Pass matching',command=lambda:send('pass')).pack(side='left')
            ttk.Button(buttons,text='Reject matching',command=lambda:send('reject')).pack(side='left',padx=8)
            ttk.Button(buttons,text='Turn filter off',command=lambda:send('off')).pack(side='left')
        else:
            ttk.Button(buttons,text='Accept matching',command=lambda:send('accept')).pack(side='left')
            ttk.Button(buttons,text='Reject matching',command=lambda:send('reject')).pack(side='left',padx=8)
            ttk.Button(buttons,text='Clear line',command=lambda:send('clear')).pack(side='left')
            ttk.Button(buttons,text='Clear all…',command=clear_all).pack(side='left',padx=(8,0))
        footer=ttk.Frame(panel);footer.pack(fill='x')
        ttk.Button(footer,text='Show node filters',command=show).pack(side='left')
        ttk.Button(footer,text='Look up node state…',command=lambda:self.lookup_node_state(dialog,connection,ensure_current,lambda:self.show_secondary_activity_latest(ident))).pack(side='left',padx=(8,0))
        ttk.Button(footer,text='Close',command=dialog.destroy).pack(side='right')
        if dialect=='CC Cluster':ttk.Button(panel,text='Reset all node filters…',command=reset_cc).pack(anchor='w',pady=(8,0))
        else:ttk.Button(panel,text='Find node country number…',command=lambda:self.lookup_prefix(dialog,connection,ensure_current,lambda:self.open_secondary_activity(ident))).pack(anchor='w',pady=(8,0))
        ttk.Label(panel,textvariable=note,style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(8,0))

    def open_secondary_filter_readback(self,item):
        view=item['filter_view']
        if view and view['window'].winfo_exists():
            self.render_secondary_filter(item);view['window'].lift();return
        host,port,call=item['source']
        dialog=tk.Toplevel(self);dialog.title('Node filter readback');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text=f'{host}:{port} · {call}',style='Heading.TLabel').pack(anchor='w')
        status=tk.StringVar(master=dialog);ttk.Label(panel,textvariable=status,style='Muted.TLabel').pack(anchor='w',pady=(5,8))
        frame=ttk.Frame(panel);frame.pack(fill='both',expand=True)
        output=tk.Text(frame,width=85,height=18,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',font=('Consolas',10),state='disabled')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=output.yview);scroll.pack(side='right',fill='y');output.pack(side='left',fill='both',expand=True);output.configure(yscrollcommand=scroll.set)
        ttk.Label(panel,text='Live traffic or other command output may appear in this response.',style='Muted.TLabel').pack(anchor='w',pady=(8,0))
        def close():item['filter_view']=None;dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW',close)
        item['filter_view']={'window':dialog,'text':output,'status':status}
        self.render_secondary_filter(item)

    def render_secondary_filter(self,item):
        view=item.get('filter_view');capture=item.get('filter_capture')
        if not view or not view['window'].winfo_exists() or not capture:return
        status={'queued':'Waiting to send…','waiting':'Waiting for a recognized response…','receiving':'Receiving…','complete':'Response ended.','partial':'Partial or ambiguous response.','idle':'No response.'}[capture.state]
        if capture.ambiguous:status+=' An earlier filter request may have produced this response. Reconnect to clear uncertainty.'
        if capture.truncated:status+=' Output exceeded the limit and was truncated.'
        if capture.stale:status+=' A filter change was requested after this readback; refresh to check the node.'
        if capture.session_ended:status+=' This readback is from a previous connection; refresh after reconnecting.'
        view['status'].set(status)
        output=view['text'];position=output.yview()[0]
        output.configure(state='normal');output.delete('1.0','end');output.insert('1.0',capture.text());output.configure(state='disabled');output.yview_moveto(position)

    def interrupt_secondary_filter(self,item):
        capture=item.get('filter_capture')
        if capture and capture.state in ('queued','waiting','receiving'):
            item['filter_unresolved']=True
            capture.interrupt();item['filter_deadline']=None;self.render_secondary_filter(item)

    def note_secondary_filter_change(self,item):
        capture=item.get('filter_capture')
        if not capture:return
        if capture.state in ('queued','waiting','receiving'):self.interrupt_secondary_filter(item)
        elif capture.state in ('complete','partial'):
            capture.stale=True;self.render_secondary_filter(item)

    def end_secondary_filter_session(self,item):
        self.interrupt_secondary_filter(item)
        capture=item.get('filter_capture')
        if capture and capture.state in ('complete','partial'):
            capture.session_ended=True;self.render_secondary_filter(item)

    def feed_secondary_filter(self,item,line):
        capture=item.get('filter_capture')
        if capture and capture.feed(line):
            if capture.state in ('complete','partial'):item['filter_deadline']=None
            return True
        return False

    def open_startup_secondary_nodes(self):
        dialog=tk.Toplevel(self);dialog.title('Additional nodes at startup');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Additional nodes at startup',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Removing an entry stops future automatic starts; current connections stay open.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        frame=ttk.Frame(panel);frame.pack(fill='both',expand=True)
        tree=ttk.Treeview(frame,columns=('host','port','call','retry'),show='headings',height=4,selectmode='browse')
        for key,label,width in [('host','HOST',190),('port','PORT',65),('call','CALLSIGN',105),('retry','RECONNECT',100)]:
            tree.heading(key,text=label);tree.column(key,width=width,minwidth=55)
        tree.pack(fill='both',expand=True)
        note=tk.StringVar(value='Select an entry to remove it from the startup list.')
        ttk.Label(panel,textvariable=note,style='Muted.TLabel').pack(anchor='w',pady=(8,0))
        displayed=[]
        def refresh():
            tree.delete(*tree.get_children())
            displayed[:]=[dict(item) for item in self.startup_secondary_nodes]
            for index,item in enumerate(self.startup_secondary_nodes):
                tree.insert('', 'end',iid=str(index),values=(item['host'],item['port'],item['call'],'On' if item['auto_reconnect'] else 'Off'))
        def remove():
            selection=tree.selection()
            if not selection:note.set('Select an entry first.');return
            index=int(selection[0])
            if index>=len(displayed) or displayed[index] not in self.startup_secondary_nodes:
                refresh();note.set('The startup list changed; select an entry again.');return
            self.startup_secondary_nodes.remove(displayed[index]);refresh()
            note.set('Removed from the startup list.' if self._save_settings() else 'Removed for this session; settings were not saved. See Activity.')
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(10,0))
        ttk.Button(buttons,text='Remove selected',command=remove).pack(side='left')
        ttk.Button(buttons,text='Close',command=dialog.destroy).pack(side='right')
        refresh()

    def render_secondary_nodes(self):
        if self.secondary_window is None or not self.secondary_window.winfo_exists():return
        selection=self.secondary_tree.selection();self.secondary_tree.delete(*self.secondary_tree.get_children())
        for ident,item in self.secondary_nodes.items():
            host,port,call=item['source'];self.secondary_tree.insert('', 'end',iid=str(ident),values=(host,port,call,item['status']))
        if selection and self.secondary_tree.exists(selection[0]):self.secondary_tree.selection_set(selection[0])

    def append_secondary_activity(self, item, text):
        combined=item['activity']+text;trim=max(0,len(combined)-200000)
        item['activity_trimmed']=item.get('activity_trimmed',0)+trim;item['activity']=combined[trim:]

    def render_secondary_activity(self, item):
        view=item['view']
        if not view or not view['window'].winfo_exists():return
        view['status'].set(item['status'])
        follow=view['text'].yview()[1]>=.999
        count=view['text'].count('1.0','@0,0','chars') if not follow else None
        offset=max(0,(count[0] if count else 0)-(item['activity_trimmed']-view['rendered_trim']))
        view['text'].configure(state='normal');view['text'].delete('1.0','end')
        view['text'].insert('1.0',item['activity'])
        if follow:view['text'].see('end')
        else:view['text'].yview(f'1.0 + {offset} chars')
        view['rendered_trim']=item['activity_trimmed'];view['text'].configure(state='disabled')
        view['send'].configure(state='normal' if item['status']=='Connected' else 'disabled')

    def open_secondary_activity(self, ident):
        item=self.secondary_nodes.get(ident)
        if not item:return
        if item['view'] and item['view']['window'].winfo_exists():item['view']['window'].lift();return
        host,port,call=item['source'];dialog=tk.Toplevel(self);dialog.title(f'Node activity — {host}:{port}');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text=f'{host}:{port} · {call}',style='Heading.TLabel').pack(anchor='w')
        status_row=ttk.Frame(panel);status_row.pack(fill='x',pady=(5,8))
        status=tk.StringVar(master=dialog);ttk.Label(status_row,textvariable=status,style='Muted.TLabel').pack(side='left')
        frame=ttk.Frame(panel);frame.pack(fill='both',expand=True)
        output=tk.Text(frame,width=75,height=16,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',padx=10,pady=8,font=('Consolas',10),state='disabled')
        ttk.Button(status_row,text='Jump to latest',command=lambda:output.see('end')).pack(side='right')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=output.yview);scroll.pack(side='right',fill='y');output.pack(side='left',fill='both',expand=True);output.configure(yscrollcommand=scroll.set)
        ttk.Label(panel,text='Commands entered here go only to this node. Output may contain account information.',style='Muted.TLabel').pack(anchor='w',pady=(8,4))
        bar=ttk.Frame(panel);bar.pack(fill='x');command_text=tk.StringVar(master=dialog);command=ttk.Entry(bar,textvariable=command_text);command.pack(side='left',fill='x',expand=True)
        draft_epoch=None
        def command_edited(*_):
            nonlocal draft_epoch
            draft_epoch=item['epoch'] if command_text.get() else None
        command_text.trace_add('write',command_edited)
        def send(_event=None):
            try:
                current=self.secondary_nodes.get(ident)
                if current is not item or item['status']!='Connected':raise OSError('This node is not connected.')
                if draft_epoch != item['epoch']:raise OSError('The connection changed. Review and edit the command before sending it.')
                if item['chat_capture'] and item['chat_capture'].state in ('queued','waiting','receiving'):raise OSError('Wait for the membership readback to finish.')
                value=command.get().strip()
                if len(value)>500:raise ValueError('Enter a command of at most 500 characters.')
                tracked=TrackedCommand(value);item['connection'].send(tracked)
            except (OSError,ValueError,UnicodeError) as exc:messagebox.showerror('Node command',str(exc),parent=dialog);return
            item['pending_manual_commands'][id(tracked)]=tracked
            command.delete(0,'end')
        send_button=ttk.Button(bar,text='Send command',command=send);send_button.pack(side='left',padx=(8,0));command.bind('<Return>',send)
        def close():item['view']=None;dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW',close)
        item['view']={'window':dialog,'text':output,'status':status,'send':send_button,'rendered_trim':item['activity_trimmed']}
        self.render_secondary_activity(item)

    def poll_secondary_nodes(self):
        changed=False;refresh=False;activity_changed=set();filter_changed=set();messages_changed=False;reports_changed=False
        for ident,item in list(self.secondary_nodes.items()):
            if item['overflow'].is_set():
                self.interrupt_secondary_manual_commands(item)
                self.interrupt_spot_submissions(item)
                self.interrupt_station_for(ident)
                self.end_secondary_filter_session(item)
                item['overflow'].clear();item['epoch']+=1;item['status']='Event queue full';self.interrupt_secondary_chat(item);item['chat_groups'].clear();item['connection'].close();refresh=True
                self._schedule_secondary_retry(ident)
                self.append_secondary_activity(item,'\nEvent queue full; connection closed.\n');activity_changed.add(ident)
                self.append(f"\nAdditional node {item['source'][0]}: event queue full; connection closed.\n")
        for _ in range(20):
            try:ident,epoch,kind,value=self.secondary_events.get_nowait()
            except queue.Empty:break
            item=self.secondary_nodes.get(ident)
            if item is None or item['epoch'] != epoch:continue
            if kind=='status':
                item['status']=value;refresh=True;activity_changed.add(ident)
                if value == 'Connected':
                    item['connected_since']=time.monotonic()
                    if item['retry_attempt'] or item['source'] in self.recent_connected_sources:
                        prefix='Connected after retry.' if item['retry_attempt'] else 'Reconnected.'
                        notice=prefix+' Recent spot history was requested; it may contain duplicates or leave gaps.'
                        self.append_secondary_activity(item,'\n'+notice+'\n')
                        self.append(f"\nAdditional node {item['source'][0]}: {notice}\n")
                        self.show_spot_recovery_notice()
                    self.remember_connected_source(item['source'])
                    item['retry_attempt']=0
                elif value == 'Disconnected':self.interrupt_secondary_manual_commands(item);self.interrupt_spot_submissions(item);self.interrupt_secondary_chat(item);self.end_secondary_filter_session(item);item['chat_groups'].clear();self.interrupt_station_for(ident);self._schedule_secondary_retry(ident)
            elif kind=='dialect':item['dialect']=value;refresh=True
            elif kind=='sent':
                item.get('pending_manual_commands',{}).pop(id(value),None)
                item.get('pending_spot_submissions',{}).pop(id(value),None)
                self.append_secondary_activity(item,'\n← '+value+'\n');activity_changed.add(ident)
                capture=item['filter_capture']
                if capture and capture.state=='queued' and value is capture.command:
                    capture.sent();item['filter_deadline']=time.monotonic()+15;self.render_secondary_filter(item)
                elif capture and capture.state=='queued' and value.lower() in ('sh/filter','sh/filter bandtime'):
                    capture.ambiguous=True;item['filter_unresolved']=True;self.render_secondary_filter(item)
                elif capture and capture.state in ('waiting','receiving'):self.interrupt_secondary_filter(item)
                elif capture and capture.state in ('complete','partial') and is_filter_change(value,item['dialect']):self.note_secondary_filter_change(item)
                if item['chat_capture'] and item['chat_capture'].state in ('queued','waiting','receiving'):
                    if item['chat_capture'].state=='queued' and value.lower()=='stat/user':item['chat_capture'].sent()
                    else:self.interrupt_secondary_chat(item)
                if self.station_target == (ident,epoch,item['connection']) and self.station_capture and self.station_capture.state in ('queued','waiting','receiving'):
                    if self.station_capture.state=='queued' and value.lower()==self.station_capture.command.lower():
                        self.station_capture.sent();self.render_station()
                    else:self.interrupt_station()
            elif kind=='error':
                self.append(f"\nAdditional node {item['source'][0]}: {value}\n")
                self.append_secondary_activity(item,'\nConnection error: '+value+'\n');activity_changed.add(ident)
            elif kind=='notice':
                self.append(f"\nAdditional node {item['source'][0]}: {value}\n")
                self.append_secondary_activity(item,'\n'+value+'\n');activity_changed.add(ident)
            elif kind=='password_required':
                connection=item['connection']
                def request_is_current():
                    current=self.secondary_nodes.get(ident)
                    return current is item and current['epoch']==epoch and current['connection'] is connection
                password=simpledialog.askstring('Additional node password',f"Password for {item['source'][0]} as {item['source'][2]}:",show='*',parent=self)
                if not request_is_current():
                    password=None;continue
                if password is None:self.stop_secondary_node(ident);continue
                try:connection.provide_password(password)
                except (OSError,ValueError,UnicodeError) as exc:
                    messagebox.showerror('Additional node password',str(exc),parent=self)
                    if request_is_current():self.stop_secondary_node(ident)
                finally:password=None
            elif kind=='data':
                self.append_secondary_activity(item,value);activity_changed.add(ident)
                item['partial']+=value
                lines=item['partial'].split('\n');item['partial']=lines.pop()
                if item['discarding_long_line']:
                    if lines:lines.pop(0);item['discarding_long_line']=False
                    else:item['partial']=''
                lines=[None if cluster_line_too_long(line) else line for line in lines]
                if cluster_line_too_long(item['partial']):
                    item['partial']='';item['discarding_long_line']=True;lines.append(None)
                for line in lines:
                    if line is None:
                        self.interrupt_station_for(ident);self.interrupt_secondary_filter(item);self.interrupt_secondary_chat(item)
                        item['report_collector']=ReportCollector()
                        notice='Skipped an overlong cluster line; affected readbacks may be incomplete.'
                        self.append_secondary_activity(item,'\n'+notice+'\n')
                        self.append(f"\nAdditional node {item['source'][0]}: {notice}\n")
                        continue
                    for part in split_prompt_prefix(line):
                        part=part.rstrip('\r')
                        if self.feed_secondary_filter(item,part):filter_changed.add(ident)
                        if self.station_target == (ident,epoch,item['connection']) and self.station_capture and self.station_capture.feed(part):
                            if self.station_capture.state=='complete':self.station_deadline=None
                            self.render_station()
                        if item['dialect']=='DXSpider':
                            group_change=parse_group_change(part)
                            if group_change:
                                self.interrupt_secondary_chat(item)
                                action,group=group_change
                                if action=='leaving':item['chat_groups'].discard(group)
                                elif len(item['chat_groups'])<100:item['chat_groups'].add(group)
                            self.feed_secondary_chat(item,part)
                        announcement=parse_announcement(part)
                        category=('weather' if is_weather(announcement) else 'history' if SPIDER_HISTORY.match(announcement) else 'announcements') if announcement else None
                        if not announcement and item['dialect'] in ('DXSpider','CC Cluster'):
                            announcement=parse_talk(part,item['source'][2])
                            if announcement:category='talk'
                            elif item['dialect']=='DXSpider':
                                announcement=parse_chat(part,item['source'][2],item['chat_groups'])
                                if announcement:category='chat'
                        if announcement:
                            self.announcements.append((announcement,item['source'],category));messages_changed=True;self.mark_unread('messages')
                            if category=='talk' and self.auto_open_talk.get():self.show_talk_alert(announcement,item['source'])
                            self.ann_note.set('Messages from connected nodes.')
                        report=item['report_collector'].feed(part)
                        if report:
                            self.reports.append((*report,item['source']));reports_changed=True;self.mark_unread('reports')
                            self.report_note.set('Reports from connected nodes.')
                        spot=parse_spot(part,item['dialect'])
                        if spot:
                            self.add_spot(spot,source=item['source']);changed=True
                            if self.logger:self.logger.publish(spot)
                            if self.serial_logger:self.serial_logger.publish(spot)
                if NODE_PROMPT.fullmatch(item['partial']):
                    prompt=item['partial'];item['partial']=''
                    if self.feed_secondary_filter(item,prompt):filter_changed.add(ident)
                    self.feed_secondary_chat(item,prompt)
                    if self.station_target == (ident,epoch,item['connection']) and self.station_capture and self.station_capture.feed(prompt):
                        if self.station_capture.state=='complete':self.station_deadline=None
                        self.render_station()
        for item in self.secondary_nodes.values():
            if item['chat_deadline'] is not None and time.monotonic() >= item['chat_deadline']:self.interrupt_secondary_chat(item)
            if item['filter_deadline'] is not None and time.monotonic() >= item['filter_deadline']:self.interrupt_secondary_filter(item)
        if changed:self.render_spots(preserve_anchor=True);self.update_spot_source()
        if messages_changed:self.render_announcements()
        if reports_changed:self.render_reports()
        for ident in filter_changed:
            if ident in self.secondary_nodes:self.render_secondary_filter(self.secondary_nodes[ident])
        if refresh:self.render_secondary_nodes();self.update_spot_source();self.update_reply_action();self.refresh_report_targets()
        for ident in activity_changed:
            if ident in self.secondary_nodes:self.render_secondary_activity(self.secondary_nodes[ident])

    def finish_secondary_chat(self, item):
        capture=item['chat_capture'];item['chat_deadline']=None
        if not capture:return
        if capture.state=='complete':item['chat_groups']=set(capture.groups)
        host,port,call=item['source']
        if self.chat_window is not None and self.chat_window.winfo_exists() and self.chat_target_name.get()==f'{host}:{port} · {call}':
            if capture.state=='complete':self.chat_note.set('Node groups: '+(', '.join(sorted(item['chat_groups'])) if item['chat_groups'] else '(none)'))
            elif capture.state=='partial':self.chat_note.set('Membership response incomplete; previous groups retained. Check node activity.')

    def interrupt_secondary_chat(self, item):
        capture=item['chat_capture']
        if capture and capture.state in ('queued','waiting','receiving'):
            capture.interrupt();self.finish_secondary_chat(item)

    def feed_secondary_chat(self, item, line):
        capture=item['chat_capture']
        if capture and capture.feed(line) and capture.state in ('complete','partial'):self.finish_secondary_chat(item)

    def open_nodes(self):
        dialog = tk.Toplevel(self); dialog.title('Saved nodes'); dialog.transient(self); dialog.configure(bg=self.theme_bg)
        panel = ttk.Frame(dialog, padding=20); panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Saved nodes', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel, text='Choose a node to fill the address. Connect separately using your callsign.', style='Muted.TLabel').pack(anchor='w', pady=(5, 10))
        ttk.Checkbutton(panel,text='Connect to the last main node when the app starts',variable=self.auto_start,command=self._save_settings).pack(anchor='w',pady=(0,10))
        table = ttk.Frame(panel); table.pack(fill='both', expand=True)
        tree = ttk.Treeview(table, columns=('name', 'host', 'port'), show='headings', height=6, selectmode='browse')
        for col, title, width in [('name', 'NAME', 160), ('host', 'HOST', 220), ('port', 'PORT', 70)]:
            tree.heading(col, text=title); tree.column(col, width=width, minwidth=60)
        scroll = ttk.Scrollbar(table, orient='vertical', command=tree.yview); scroll.pack(side='right', fill='y')
        tree.pack(side='left', fill='both', expand=True); tree.configure(yscrollcommand=scroll.set)
        name = tk.StringVar(); host = tk.StringVar(); port = tk.StringVar(value='7300')
        fields = ttk.Frame(panel); fields.pack(fill='x', pady=(10, 0))
        for label, variable, width in [('NAME', name, 18), ('HOST', host, 25), ('PORT', port, 7)]:
            box = ttk.Frame(fields); box.pack(side='left', fill='x', expand=True, padx=(0, 8))
            ttk.Label(box, text=label, style='Muted.TLabel').pack(anchor='w')
            ttk.Entry(box, textvariable=variable, width=width).pack(fill='x', pady=(3, 0))
        note = tk.StringVar(value='Select a row to edit or use it. Saving an existing name updates that node.')
        ttk.Label(panel, textvariable=note, style='Muted.TLabel').pack(anchor='w', pady=8)
        def refresh():
            tree.delete(*tree.get_children())
            for index, item in enumerate(self.saved_nodes): tree.insert('', 'end', iid=str(index), values=(item['name'], item['host'], item['port']))
        def selected():
            selection = tree.selection()
            return int(selection[0]) if selection else None
        def select_row(_event=None):
            index = selected()
            if index is None: return
            node = self.saved_nodes[index]; name.set(node['name']); host.set(node['host']); port.set(str(node['port']))
        def save():
            try:
                node = validate_node(name.get(), host.get(), port.get())
                index = next((i for i, item in enumerate(self.saved_nodes) if item['name'].casefold() == node['name'].casefold()), None)
                if index is None and len(self.saved_nodes) >= 50: raise ValueError('The list can contain up to 50 nodes.')
            except ValueError as exc: messagebox.showerror('Saved nodes', str(exc), parent=dialog); return
            if index is None: self.saved_nodes.append(node)
            else: self.saved_nodes[index] = node
            refresh(); note.set('Saved.' if self._save_settings() else 'Changed in memory, but could not save; see Activity.')
        def remove():
            index = selected()
            if index is None: note.set('Select a node to remove.'); return
            self.saved_nodes.pop(index); refresh()
            note.set('Removed.' if self._save_settings() else 'Changed in memory, but could not save; see Activity.')
        def use():
            index = selected()
            if index is None: note.set('Select a node first.'); return
            if self.connection or self.want_connection:
                messagebox.showerror('Saved nodes', 'Disconnect before choosing a different node.', parent=dialog); return
            node = self.saved_nodes[index]; self.host.set(node['host']); self.port.set(str(node['port']))
            dialog.destroy()
        tree.bind('<<TreeviewSelect>>', select_row)
        buttons = ttk.Frame(panel); buttons.pack(fill='x')
        ttk.Button(buttons, text='Save node', command=save).pack(side='left')
        ttk.Button(buttons, text='Remove', command=remove).pack(side='left', padx=8)
        ttk.Button(buttons, text='Close', command=dialog.destroy).pack(side='right')
        ttk.Button(buttons, text='Use selected', style='Accent.TButton', command=use).pack(side='right', padx=8)
        refresh()

    def _start_connection(self):
        if not self.want_connection or not self.connection_target: return
        self.command_history.clear();self.command_history_index=None;self.command_history_draft=''
        self.filter_readback_unresolved=False
        for entry in self.connection_entries:entry.configure(state='readonly')
        had_filter_draft = (bool(self.keyword_text.get() or self.spider_expression.get()) or
                            self.cc_filter_kind.get()!='Keywords' or self.spider_stream.get()!='Spots' or
                            self.spider_filter_kind.get()!='Band or range' or self.spider_line.get()!='1')
        self.keyword_text.set('');self.cc_filter_kind.set('Keywords')
        self.spider_stream.set('Spots');self.spider_filter_kind.set('Band or range')
        self.spider_expression.set('');self.spider_line.set('1')
        if had_filter_draft:self.append('\nFilter form cleared because the connection changed. Review the node filters before entering a new request.\n')
        host,port,call = self.connection_target
        self.generation += 1; generation=self.generation; self.partial=''; self.discarding_long_line=False
        self.report_collector = ReportCollector()
        self.directory = DirectoryCapture(); self.render_directory()
        self.directory_note.set('Waiting for the node to connect.')
        self.dialect=''; self.last_error=''; self.node_settings.clear(); self.chat_groups.clear(); self.render_settings()
        self.settings_note.set('Waiting for the node to identify itself.')
        connection = None
        overflow = threading.Event(); self.connection_overflow = overflow
        def emit(kind, value):
            try: self.events.put((generation,kind,value),timeout=0.5)
            except queue.Full:
                overflow.set()
                if connection: connection.close()
        connection=Connection(emit); self.connection=connection
        self.status.set('Connecting…'); self.set_connection_button(True)
        connection.start(host,port,call)

    def disconnect(self):
        self.command_history.clear();self.command_history_index=None;self.command_history_draft=''
        self.interrupt_manual_commands()
        self.interrupt_spot_submissions()
        if getattr(self,'auto_start_after',None) is not None:
            self.after_cancel(self.auto_start_after);self.auto_start_after=None
        self.close_password_dialog()
        self.interrupt_chat_readback()
        self.interrupt_settings_readback()
        self.interrupt_station_for(None)
        self.end_filter_session()
        self.interrupt_mail()
        self.want_connection = False; self.connection_target = None; self.retry_attempt = 0; self.retry_streak = 0; self.connected_since = None
        if self.retry_after is not None:
            self.after_cancel(self.retry_after); self.retry_after = None
        if self.mail_compose_pending:
            self.append('\nMail: Connection closed; delivery may be unknown.\n')
            self.mail_compose_pending = False
        self.generation += 1
        if self.connection: self.connection.close(); self.connection=None
        self.connection_overflow = None
        self.status.set('Disconnected')
        for entry in self.connection_entries:entry.configure(state='normal')
        self.update_spot_source()
        if hasattr(self, 'settings_note') and self.node_settings:
            self.settings_note.set('Last read from the previous connection; reconnect to refresh.')
        if hasattr(self, 'connect_btn'):
            self.set_connection_button(False); self.send_btn.configure(state='disabled')
            self.settings_refresh.configure(state='disabled')
            self.refresh_report_targets()
            self.update_report_actions()
            for button in self.directory_buttons: button.configure(state='disabled')
            self.directory_disconnected()
            if self.reports: self.report_note.set('Main node disconnected; additional nodes may still deliver reports.')
            if self.announcements: self.ann_note.set('Main node disconnected; additional nodes may still deliver messages.')
            self.update_setting_actions()
            self.update_filter_view()

    def reconnect_option_changed(self):
        self._save_settings()
        if not self.auto_reconnect.get() and self.retry_after is not None: self.disconnect()

    def start_saved_connection(self):
        self.auto_start_after=None
        if self.auto_start.get() and not self.closing and not self.want_connection and self.connection is None:
            self.connect()

    def close_password_dialog(self):
        if self.password_dialog is not None:
            try: self.password_dialog.destroy()
            except tk.TclError: pass
            self.password_dialog = None

    def ask_password(self):
        if not self.connection or self.password_dialog is not None: return
        generation, connection = self.generation, self.connection
        dialog = tk.Toplevel(self); self.password_dialog = dialog
        dialog.title('Cluster password'); dialog.transient(self); dialog.configure(bg=self.theme_bg); dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=20); panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Password required', style='Heading.TLabel').pack(anchor='w')
        host, port, _ = self.connection_target
        ttk.Label(panel, text=f'{host}:{port} requested a password. Telnet sends it without encryption.', style='Muted.TLabel').pack(anchor='w', pady=(6, 12))
        password = tk.StringVar()
        entry = ttk.Entry(panel, textvariable=password, show='•', width=45); entry.pack(fill='x'); entry.focus_set()
        ttk.Label(panel, text='The password is not saved. Reconnecting may require it again.', style='Muted.TLabel').pack(anchor='w', pady=(8, 12))
        buttons = ttk.Frame(panel); buttons.pack(fill='x')
        def cancel():
            password.set(''); self.close_password_dialog()
            if generation == self.generation and connection is self.connection: self.disconnect()
        def submit(_event=None):
            try:
                if generation != self.generation or connection is not self.connection: raise OSError('The connection changed.')
                connection.provide_password(password.get())
            except (OSError, ValueError, UnicodeError) as exc:
                messagebox.showerror('Cluster password', str(exc), parent=dialog); return
            password.set(''); self.close_password_dialog()
        dialog.protocol('WM_DELETE_WINDOW', cancel); entry.bind('<Return>', submit)
        ttk.Button(buttons, text='Cancel', command=cancel).pack(side='right')
        ttk.Button(buttons, text='Sign in', style='Accent.TButton', command=submit).pack(side='right', padx=(0, 8))

    def _retry_connection(self):
        self.retry_after = None
        self._start_connection()

    def _schedule_reconnect(self):
        if self.connected_since is not None and time.monotonic()-self.connected_since >= 30:self.retry_streak=0
        self.connected_since=None
        delay = min(60, 2 ** min(self.retry_streak + 1, 6)); self.retry_streak += 1; self.retry_attempt += 1
        for entry in self.connection_entries:entry.configure(state='readonly')
        self.status.set(f'Reconnecting in {delay} seconds…')
        self.set_connection_button(True)
        self.retry_after = self.after(delay * 1000, self._retry_connection)

    def lookup_prefix(self, parent, connection, ensure_current, show_activity):
        try:
            ensure_current()
        except OSError as exc:
            messagebox.showerror('Country lookup', str(exc), parent=parent); return
        prefix = simpledialog.askstring('Country lookup', 'Enter a callsign or prefix. In the node response, CC is the country number:', parent=parent)
        if prefix is None: return
        prefix = prefix.strip()
        if not prefix.isascii():
            messagebox.showerror('Country lookup', 'Enter a callsign or prefix using ASCII letters, numbers, / or -.', parent=parent); return
        prefix = prefix.upper()
        if not re.fullmatch(r'[A-Z0-9][A-Z0-9/-]{0,31}', prefix, flags=re.ASCII):
            messagebox.showerror('Country lookup', 'Enter a callsign or prefix using letters, numbers, / or -.', parent=parent); return
        try:
            ensure_current()
            connection.send('show/prefix ' + prefix)
        except (OSError, ValueError, UnicodeError) as exc:
            messagebox.showerror('Country lookup', str(exc), parent=parent); return
        show_activity()

    def lookup_main_prefix(self):
        epoch, connection = self.generation, self.connection
        def ensure_current():
            if not connection or self.generation != epoch or self.connection is not connection or self.status.get() != 'Connected' or self.dialect != 'DXSpider':
                raise OSError('Connect to a DXSpider node and try again.')
        self.lookup_prefix(self, connection, ensure_current, lambda:self.notebook.select(self.console_page))

    def lookup_node_state(self,parent,connection,ensure_current,show_activity):
        try:ensure_current()
        except OSError as exc:messagebox.showerror('Node state lookup',str(exc),parent=parent);return
        call=ask_node_state_call(parent,self.theme_bg)
        if call is None:return
        call=call.strip()
        if not call.isascii() or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/-]{0,31}',call):
            messagebox.showerror('Node state lookup','Enter a callsign using letters, numbers, / or -.',parent=parent);return
        call=call.upper()
        try:
            ensure_current();connection.send('sh/usdb '+call)
        except (OSError,ValueError,UnicodeError) as exc:
            messagebox.showerror('Node state lookup',str(exc),parent=parent);return
        show_activity()

    def lookup_main_state(self):
        generation,connection,dialect=self.generation,self.connection,self.dialect
        def ensure_current():
            if (not connection or self.generation!=generation or self.connection is not connection
                    or self.status.get()!='Connected' or self.dialect!=dialect or dialect not in ('CC Cluster','DXSpider')):
                raise OSError('The connection changed. Reconnect and try again.')
        self.lookup_node_state(self,connection,ensure_current,self.show_main_activity_latest)

    def show_main_activity_latest(self):
        self.notebook.select(self.console_page);self.update_idletasks();self.transcript.see('end')

    def show_secondary_activity_latest(self,ident):
        self.open_secondary_activity(ident);self.update_idletasks()
        item=self.secondary_nodes.get(ident)
        if item and item['view']:item['view']['text'].see('end')

    def send(self, event=None):
        command=self.command.get().strip()
        if not command: return
        try:
            if not self.connection or self.status.get() != 'Connected': raise OSError('Not connected')
            if self.command_draft_target != (self.generation,self.connection):raise OSError('The connection changed. Review and edit the command before sending it.')
            tracked=TrackedCommand(command);self.connection.send(tracked)
        except (OSError,UnicodeError,ValueError) as exc: messagebox.showerror('Send',str(exc)); return
        self.pending_manual_commands[id(tracked)]=tracked
        if not self.command_history or self.command_history[-1]!=command:self.command_history.append(command)
        self.command_history_index=None;self.command_history_draft=''
        if self.dialect == 'CC Cluster': self.settings_note.set('A command was queued; refresh settings to check current values.')
        self.command.delete(0,'end')

    def interrupt_manual_commands(self):
        if not self.pending_manual_commands:return
        count=len(self.pending_manual_commands);self.pending_manual_commands.clear()
        self.append(f'\n{count} manually entered command{"s" if count != 1 else ""} had no send confirmation when the connection ended; delivery is unknown. Review Activity before retrying.\n')
        self.manual_uncertain_notice=True;self.update_spot_recovery_display()

    def interrupt_secondary_manual_commands(self,item):
        pending=item.get('pending_manual_commands')
        if not pending:return
        count=len(pending);pending.clear()
        notice=f'{count} manually entered command{"s" if count != 1 else ""} had no send confirmation when the connection ended; delivery is unknown.'
        self.append_secondary_activity(item,'\n'+notice+'\n');self.render_secondary_activity(item)
        self.append(f"\nAdditional node {item['source'][0]}: {notice} Review Activity before retrying.\n")
        self.manual_uncertain_notice=True;self.update_spot_recovery_display()

    def interrupt_spot_submissions(self,item=None):
        pending=self.pending_spot_submissions if item is None else item.get('pending_spot_submissions')
        if not pending:return
        count=len(pending);pending.clear()
        notice=f'{count} spot submission{"s" if count != 1 else ""} had no send confirmation when the connection ended; delivery is unknown. Check before posting again.'
        if item is not None:
            self.append_secondary_activity(item,'\n'+notice+'\n');self.render_secondary_activity(item)
            notice=f"Additional node {item['source'][0]}: "+notice
        self.append('\n'+notice+'\n');self.spot_uncertain_notice=True;self.update_spot_recovery_display()

    def recall_command(self,direction):
        if not self.command_history:return 'break'
        if self.command_history_index is None:
            if direction>0:return 'break'
            self.command_history_draft=self.command_text.get();self.command_history_index=len(self.command_history)-1
        else:
            self.command_history_index=max(0,min(len(self.command_history),self.command_history_index+direction))
        text=self.command_history_draft if self.command_history_index==len(self.command_history) else self.command_history[self.command_history_index]
        self.command_history_navigating=True
        try:self.command_text.set(text);self.command.icursor('end')
        finally:self.command_history_navigating=False
        return 'break'

    def queue_spot(self, generation, frequency, call, comment, test=False):
        if generation != self.generation or not self.connection or self.status.get() != 'Connected':
            raise OSError('The cluster connection changed. Close this form and open it again.')
        if test and self.dialect != 'CC Cluster': raise ValueError('Test spots are available only for CC Cluster.')
        command = spot_command(frequency, call, comment, test=test, dialect=self.dialect)
        self.connection.send(command)

    def open_spot(self):
        targets={}
        for label,(ident,epoch,connection) in self.report_targets.items():
            dialect=self.dialect if ident is None else self.secondary_nodes[ident]['dialect']
            if dialect in ('DXSpider','CC Cluster'):targets[label]=(ident,epoch,connection,dialect)
        if not targets:
            messagebox.showerror('Post a DX spot','Connect to a supported cluster first.',parent=self);return
        dialog = tk.Toplevel(self); dialog.title('Post a DX spot'); dialog.transient(self); dialog.resizable(False, False); dialog.configure(bg=self.theme_bg)
        panel = ttk.Frame(dialog, padding=20); panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Post a DX spot', style='Heading.TLabel').pack(anchor='w')
        target_name=tk.StringVar(value=next(iter(targets)));hint=tk.StringVar()
        ttk.Label(panel, textvariable=hint, style='Muted.TLabel',wraplength=480).pack(anchor='w', pady=(5, 15))
        ttk.Label(panel,text='SEND THROUGH',style='Muted.TLabel').pack(anchor='w',pady=(0,4))
        ttk.Combobox(panel,textvariable=target_name,values=list(targets),state='readonly',width=48).pack(fill='x',pady=(0,10))
        frequency = tk.StringVar(); call = tk.StringVar(); comment = tk.StringVar(); test = tk.BooleanVar(value=False)
        send_label = tk.StringVar(value='Send spot')
        spot_entries=[]
        for label, variable in [('FREQUENCY  kHz', frequency), ('DX CALLSIGN', call), ('COMMENT  optional', comment)]:
            ttk.Label(panel, text=label, style='Muted.TLabel').pack(anchor='w', pady=(0, 4))
            entry = ttk.Entry(panel, textvariable=variable, width=50); entry.pack(fill='x', pady=(0, 10))
            spot_entries.append(entry)
            if variable is frequency: entry.focus_set()
        test_box=ttk.Checkbutton(panel,text='Test spot (DXTEST, intended to return only to you)',variable=test)
        test_box.pack(anchor='w',pady=(0,8))
        def update_spot_type(*_):
            supported=targets[target_name.get()][3]=='CC Cluster'
            if not supported and test.get():test.set(False)
            test_box.configure(state='normal' if supported else 'disabled')
            send_label.set('Send test spot' if test.get() else 'Send spot')
            hint.set(('DXTEST is intended to return the spot only to you.' if test.get() else f'This sends the spot through {target_name.get()}, which may share it with other users.')+' Enter in a field sends the spot.')
        test.trace_add('write',update_spot_type);target_name.trace_add('write',update_spot_type);update_spot_type()
        buttons=ttk.Frame(panel); buttons.pack(fill='x', pady=(5, 0))
        def submit():
            try:
                ident,epoch,connection,dialect=targets[target_name.get()]
                if ident is None:
                    valid=epoch==self.generation and connection is self.connection and self.status.get()=='Connected' and self.dialect==dialect
                else:
                    item=self.secondary_nodes.get(ident)
                    valid=bool(item and item['epoch']==epoch and item['connection'] is connection and item['status']=='Connected' and item['dialect']==dialect)
                if not valid:raise OSError('The selected connection changed. Close this form and open it again.')
                if test.get() and dialect!='CC Cluster':raise ValueError('Test spots are available only for CC Cluster.')
                command=spot_command(frequency.get(),call.get(),comment.get(),test=test.get(),dialect=dialect)
                tracked=TrackedCommand(command)
                connection.send(tracked)
            except (OSError, ValueError, UnicodeError, KeyError) as exc:
                messagebox.showerror('Post a DX spot', str(exc), parent=dialog); return
            if ident is None:self.pending_spot_submissions[id(tracked)]=tracked
            else:item['pending_spot_submissions'][id(tracked)]=tracked
            self.append(f'\nSpot queued through {target_name.get()}. Check node activity for its response.\n')
            self.notebook.select(self.console_page); dialog.destroy()
        def submit_from_entry(_event):
            submit();return 'break'
        for entry in spot_entries:entry.bind('<Return>',submit_from_entry)
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right')
        ttk.Button(buttons, textvariable=send_label, style='Accent.TButton', command=submit).pack(side='right', padx=(0, 8))

    def refresh_spots(self):
        targets=dict(self.report_targets)
        if not targets:
            messagebox.showerror('Refresh spots','Connect to a cluster first.',parent=self);return
        def request(name, dialog=None):
            try:
                if self.report_targets.get(name) != targets[name]:raise OSError('The selected connection changed. Open this form again.')
                self.node_for_request(name).send('sh/mydx/100')
            except (OSError,KeyError) as exc:
                messagebox.showerror('Refresh spots',str(exc),parent=dialog or self);return
            self.append(f'\nSpot history requested from {name}.\n')
            if dialog:dialog.destroy()
        if len(targets)==1:
            request(next(iter(targets)));return
        dialog=tk.Toplevel(self);dialog.title('Refresh spots');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=20);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Refresh spots',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Choose the node to request recent spots from.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        selected=self.spots.selection();source=self.spot_sources.get(selected[0]) if selected else None
        def target_source(target):
            ident=target[0]
            return self.spot_source if ident is None else self.secondary_nodes[ident]['source']
        initial=next((name for name,target in targets.items() if source and target_source(target)==source),next(iter(targets)))
        node=tk.StringVar(value=initial)
        ttk.Combobox(panel,textvariable=node,values=list(targets),state='readonly',width=45).pack(fill='x',pady=(0,10))
        ttk.Label(panel,text='Results can duplicate spots and trigger alarms.',style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Request spots',style='Accent.TButton',command=lambda:request(node.get(),dialog)).pack(side='right',padx=(0,8))

    def open_older_spots(self):
        targets = {}
        if self.connection and self.status.get() == 'Connected' and self.dialect == 'DXSpider':
            label = 'Main connection' + (f' · {self.connection_target[0]}:{self.connection_target[1]}' if self.connection_target else '')
            targets[label] = (None, self.generation, self.connection)
        for ident, item in self.secondary_nodes.items():
            if item['status'] == 'Connected' and item['dialect'] == 'DXSpider' and item['connection']:
                host, port, call = item['source']
                targets[f'{host}:{port} · {call}'] = (ident, item['epoch'], item['connection'])
        if not targets:
            cc_connected = ((self.connection and self.status.get() == 'Connected' and self.dialect == 'CC Cluster')
                            or any(item['status'] == 'Connected' and item['dialect'] == 'CC Cluster' and item['connection']
                                   for item in self.secondary_nodes.values()))
            message = ('Older filtered pages are available for DXSpider nodes. For CC Cluster, use Search to look for specific spots, or Refresh spots for recent filtered spots.'
                       if cc_connected else 'Connect to a DXSpider node to request older filtered spots.')
            messagebox.showinfo('Older spots', message, parent=self)
            return
        dialog = tk.Toplevel(self); dialog.title('Older spots'); dialog.transient(self); dialog.configure(bg=self.theme_bg); dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=20); panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Older spots', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel, text='Request older spots passing this node’s filter.', style='Muted.TLabel').pack(anchor='w', pady=(5, 12))
        ttk.Label(panel, text='DXSPIDER NODE', style='Muted.TLabel').pack(anchor='w')
        selected = self.spots.selection()
        source = self.spot_sources.get(selected[0]) if selected else None
        def target_source(target):
            ident = target[0]
            return self.spot_source if ident is None else self.secondary_nodes[ident]['source']
        initial = next((name for name, target in targets.items() if source and target_source(target) == source), next(iter(targets)))
        node = tk.StringVar(value=initial)
        ttk.Combobox(panel, textvariable=node, values=list(targets), state='readonly', width=42).pack(fill='x', pady=(4, 10))
        ttk.Label(panel, text='PAGE', style='Muted.TLabel').pack(anchor='w')
        labels = [f'{page}  ·  spots {(page-1)*100+1}–{page*100}' for page in range(2, 11)]
        page = tk.StringVar(value=labels[0])
        page_box=ttk.Combobox(panel, textvariable=page, values=labels, state='disabled', width=28)
        page_box.pack(anchor='w', pady=(4, 10))
        day_row=ttk.Frame(panel);day_row.pack(fill='x',pady=(0,10))
        ttk.Label(day_row,text='START DAYS AGO',style='Muted.TLabel').pack(side='left',padx=(0,8))
        days=tk.StringVar(master=dialog,value='1');ttk.Entry(day_row,textvariable=days,width=5).pack(side='left')
        days.trace_add('write',lambda *_:page_box.configure(state='disabled' if days.get().strip() else 'readonly'))
        ttk.Label(day_row,text='1 = yesterday · clear to use page',style='Muted.TLabel').pack(side='left',padx=8)
        ttk.Label(panel, text='Some nodes reject numbered pages. If a page fails, try days ago.\n“Not found” may also mean no matches. Days ago starts on that\nday and searches older records. Results may overlap, miss spots\nor trigger alarms; check node activity.', style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        buttons = ttk.Frame(panel); buttons.pack(fill='x')
        def selected_target():
            name = node.get(); ident, epoch, connection = targets[name]
            if ident is None:
                current = epoch == self.generation and connection is self.connection and self.status.get() == 'Connected' and self.dialect == 'DXSpider'
            else:
                item = self.secondary_nodes.get(ident)
                current = bool(item and item['epoch'] == epoch and item['connection'] is connection and item['status'] == 'Connected' and item['dialect'] == 'DXSpider')
            if not current: raise OSError('The selected connection changed. Open this form again.')
            return name, ident, connection
        def show_activity():
            try: _, ident, _ = selected_target()
            except (KeyError, OSError) as exc: messagebox.showerror('Older spots', str(exc), parent=dialog); return
            if ident is None: self.notebook.select(self.console_page)
            else: self.open_secondary_activity(ident)
        def submit():
            try:
                name, ident, connection = selected_target()
                command = older_filtered_spots_command(labels.index(page.get()) + 2,days.get().strip())
                connection.send(command)
            except (KeyError, ValueError, OSError) as exc:
                messagebox.showerror('Older spots', str(exc), parent=dialog); return
            self.append(f'\nOlder spot history requested from {name}: {command}.\n')
            self.spot_search_note.set('Older spots requested · sort date or scroll to find them; display filters still apply.')
            dialog.destroy()
        ttk.Button(buttons, text='Open node activity', command=show_activity).pack(side='left')
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right')
        ttk.Button(buttons, text='Request spots', style='Accent.TButton', command=submit).pack(side='right', padx=(0, 8))

    def request_spot_history(self, generation, call, mode='Callsign'):
        if generation != self.generation or not self.connection or self.status.get() != 'Connected':
            raise OSError('The cluster connection changed. Close this form and open it again.')
        self.connection.send(history_command(self.dialect, call, mode))

    def open_spot_history(self):
        selected = self.spots.selection()
        spot = next((s for ident, s in self.rows if selected and ident == selected[0]), None)
        spot_source = self.spot_sources.get(selected[0]) if selected else None
        targets = {}
        if self.connection and self.status.get() == 'Connected' and self.dialect in ('DXSpider','CC Cluster'):
            label = 'Main connection' + (f' · {self.connection_target[0]}:{self.connection_target[1]}' if self.connection_target else '')
            targets[label] = (None, self.generation, self.connection, self.dialect, self.spot_source)
        for ident, item in self.secondary_nodes.items():
            if item['connection'] and item['status'] == 'Connected' and item['dialect'] in ('DXSpider','CC Cluster'):
                host,port,call=item['source'];label=f'{host}:{port} · {call}'
                targets[label]=(ident,item['epoch'],item['connection'],item['dialect'],item['source'])
        if not targets:
            messagebox.showerror('Spot history', 'Connect to a supported cluster first.', parent=self); return
        initial = next((label for label, target in targets.items() if spot_source and target[4] == spot_source), next(iter(targets)))
        dialog = tk.Toplevel(self); dialog.title('Find spot history'); dialog.transient(self); dialog.configure(bg=self.theme_bg); dialog.resizable(False, False)
        panel = ttk.Frame(dialog, padding=20); panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Find spot history', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel, text='Ask the selected node for matching spots.', style='Muted.TLabel').pack(anchor='w', pady=(5, 12))
        mode=tk.StringVar(value='Callsign'); hint=tk.StringVar();target_name=tk.StringVar(value=initial)
        ttk.Label(panel,text='SEARCH BY',style='Muted.TLabel').pack(anchor='w')
        selector=ttk.Combobox(panel,textvariable=mode,values=['Callsign','Band','Frequency range'],state='readonly',width=20);selector.pack(anchor='w',pady=(4,8))
        ttk.Label(panel,text='NODE',style='Muted.TLabel').pack(anchor='w')
        ttk.Combobox(panel,textvariable=target_name,values=list(targets),state='readonly',width=40).pack(fill='x',pady=(4,8))
        ttk.Label(panel, textvariable=hint, style='Muted.TLabel').pack(anchor='w')
        call = tk.StringVar(value=spot.dx if spot else '')
        def update_hint(*_):
            dialect=targets[target_name.get()][3]
            if mode.get() == 'Band': text='BAND  ·  EXAMPLE: 20m, 1.25m, 70cm OR MICROWAVE'
            elif mode.get() == 'Frequency range': text='WHOLE kHz RANGE  ·  EXAMPLE: 14000-14350'
            else: text='CALLSIGN OR COUNTRY PREFIX  ·  F* FOR COUNTRY' if dialect == 'CC Cluster' else 'CALLSIGN PATTERN  ·  * AND ? ARE NODE-DEFINED'
            hint.set(text)
        mode.trace_add('write',update_hint);target_name.trace_add('write',update_hint);update_hint()
        selector.bind('<<ComboboxSelected>>',lambda event: call.set(''))
        entry = ttk.Entry(panel, textvariable=call, width=40); entry.pack(fill='x', pady=(4, 10)); entry.focus_set()
        count_row=ttk.Frame(panel);count_row.pack(fill='x',pady=(0,10))
        ttk.Label(count_row,text='NUMBER OF SPOTS',style='Muted.TLabel').pack(side='left',padx=(0,8))
        history_count=tk.StringVar(value='30')
        ttk.Combobox(count_row,textvariable=history_count,values=('10','30','50','100'),state='readonly',width=6).pack(side='left')
        ttk.Label(count_row,text='The node may return fewer.',style='Muted.TLabel').pack(side='left',padx=8)
        day_row=ttk.Frame(panel);day_row.pack(fill='x',pady=(0,10))
        ttk.Label(day_row,text='START DAYS AGO',style='Muted.TLabel').pack(side='left',padx=(0,8))
        history_days=tk.StringVar();day_entry=ttk.Entry(day_row,textvariable=history_days,width=5);day_entry.pack(side='left')
        ttk.Label(day_row,text='DXSpider only · blank for recent',style='Muted.TLabel').pack(side='left',padx=8)
        def update_days(*_):
            enabled=targets[target_name.get()][3]=='DXSpider'
            if not enabled:history_days.set('')
            day_entry.configure(state='normal' if enabled else 'disabled')
        target_name.trace_add('write',update_days);update_days()
        ttk.Label(panel,text='A days-back search starts on that day and searches older records.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Label(panel, text='Results join the spot stream and may duplicate existing rows or trigger alarms.\nCurrent display filters still apply.', style='Muted.TLabel').pack(anchor='w', pady=(0, 12))
        buttons=ttk.Frame(panel); buttons.pack(fill='x')
        def submit(_event=None):
            try:
                ident,epoch,connection,dialect,source=targets[target_name.get()]
                if ident is None:
                    if epoch != self.generation or connection is not self.connection or self.status.get() != 'Connected' or self.dialect != dialect:
                        raise OSError('The selected connection changed. Close this form and open it again.')
                else:
                    item=self.secondary_nodes.get(ident)
                    if not item or item['epoch'] != epoch or item['connection'] is not connection or item['status'] != 'Connected' or item['dialect'] != dialect:
                        raise OSError('The selected connection changed. Close this form and open it again.')
                connection.send(history_command(dialect,call.get(),mode.get(),history_count.get(),history_days.get().strip()))
            except (OSError, ValueError, UnicodeError, KeyError) as exc:
                messagebox.showerror('Spot history', str(exc), parent=dialog); return
            self.append(f'\nHistory requested through {target_name.get()} for {call.get().strip().upper()}.\n')
            dialog.destroy()
        entry.bind('<Return>', submit)
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right')
        ttk.Button(buttons, text='Find spots', style='Accent.TButton', command=submit).pack(side='right', padx=(0, 8))

    def open_node_profile(self):
        if not self.connection or self.status.get() != 'Connected' or self.dialect not in ('DXSpider', 'CC Cluster'):
            messagebox.showerror('My node profile', 'Connect to a supported cluster first.', parent=self); return
        generation=self.generation; connection=self.connection; dialect=self.dialect
        dialog=tk.Toplevel(self);dialog.title('My node profile');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='My node profile',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text=f'Change one field at a time on the connected {dialect} node.',style='Muted.TLabel').pack(anchor='w',pady=(5,8))
        ttk.Label(panel,text='Changes may be shared with other nodes. Check Activity for the response.',style='Muted.TLabel').pack(anchor='w',pady=(0,10))
        for label in ('Name','QTH','Grid'):
            row=ttk.Frame(panel);row.pack(fill='x',pady=4)
            ttk.Label(row,text=label.upper(),style='Muted.TLabel',width=8).pack(side='left')
            value=tk.StringVar(master=dialog);ttk.Entry(row,textvariable=value,width=36).pack(side='left',fill='x',expand=True)
            def apply(field=label,variable=value):
                try:
                    if generation != self.generation or connection is not self.connection or self.status.get() != 'Connected' or self.dialect != dialect:
                        raise OSError('The connection changed. Close this window and open it again.')
                    connection.send(profile_command(field,variable.get(),dialect=dialect))
                except (OSError,ValueError) as exc:messagebox.showerror('My node profile',str(exc),parent=dialog);return
                note.set(f'{field} change requested. Check Activity for the node response.')
            ttk.Button(row,text='Set '+label,command=apply).pack(side='left',padx=(8,0))
        if dialect=='CC Cluster':
            ttk.Separator(panel).pack(fill='x',pady=(10,8))
            ttk.Label(panel,text='SPOT FREQUENCY PRECISION',style='Muted.TLabel').pack(anchor='w')
            ttk.Label(panel,text='Request one or two decimals from this node. Setting a value changes the node profile.',style='Muted.TLabel').pack(anchor='w',pady=(3,6))
            precision_row=ttk.Frame(panel);precision_row.pack(anchor='w')
            def precision(command):
                try:
                    if generation!=self.generation or connection is not self.connection or self.status.get()!='Connected' or self.dialect!='CC Cluster':
                        raise OSError('The connection changed. Close this window and open it again.')
                    connection.send(command)
                except (OSError,ValueError) as exc:messagebox.showerror('Spot frequency precision',str(exc),parent=dialog);return
                note.set('Request queued. Check Activity for the node response.')
            ttk.Button(precision_row,text='1 decimal',command=lambda:precision('set/res 1')).pack(side='left')
            ttk.Button(precision_row,text='2 decimals',command=lambda:precision('set/res 2')).pack(side='left',padx=8)
            ttk.Button(precision_row,text='Show current',command=lambda:precision('sh/res')).pack(side='left')
        note=tk.StringVar(master=dialog,value='A grid change may update the node location. My grid is a separate local setting.')
        ttk.Label(panel,textvariable=note,style='Muted.TLabel').pack(anchor='w',pady=(10,8))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def view_profile():
            if generation != self.generation or connection is not self.connection or self.status.get() != 'Connected' or not self.connection_target:
                messagebox.showerror('My node profile','The connection changed. Close this window and open it again.',parent=dialog);return
            self.open_station(initial_call=self.connection_target[2])
        ttk.Button(buttons,text='View current profile…',command=view_profile).pack(side='left')
        ttk.Button(buttons,text='Close',command=dialog.destroy).pack(side='right')

    def open_station(self, initial_call=None):
        if self.station_window is not None and self.station_window.winfo_exists():
            self.station_window.lift()
            if initial_call is not None:
                if self.station_capture and self.station_capture.state in ('queued','waiting','receiving'):
                    messagebox.showerror('Station info','Wait for the current lookup to finish.',parent=self.station_window); return
                if self.station_main_label is None:
                    messagebox.showerror('Station info','Reopen this window to use the main connection.',parent=self.station_window);return
                self.station_node.set(self.station_main_label);self.station_call.set(initial_call);self.station_lookup()
            return
        targets={}
        if self.connection and self.status.get() == 'Connected':
            label='Main connection' + (f' · {self.connection_target[0]}:{self.connection_target[1]}' if self.connection_target else '')
            targets[label]=(None,self.generation,self.connection,self.spot_source)
        for ident,item in self.secondary_nodes.items():
            if item['connection'] and item['status']=='Connected':
                host,port,call=item['source']; targets[f'{host}:{port} · {call}']=(ident,item['epoch'],item['connection'],item['source'])
        if not targets:
            messagebox.showerror('Station info','Connect to a cluster first.',parent=self);return
        selected=self.spots.selection()
        spot=next((s for ident,s in self.rows if selected and ident==selected[0]),None)
        source=self.spot_sources.get(selected[0]) if selected and initial_call is None else None
        initial=next((name for name,target in targets.items() if source and target[3]==source),next(iter(targets)))
        dialog=tk.Toplevel(self);self.station_window=dialog;dialog.title('Station info');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Station info',style='Heading.TLabel').pack(anchor='w')
        bar=ttk.Frame(panel);bar.pack(fill='x',pady=(10,8))
        ttk.Label(bar,text='CALLSIGN',style='Muted.TLabel').pack(side='left',padx=(0,8))
        call=tk.StringVar(value=initial_call if initial_call is not None else spot.dx if spot else '');self.station_call=call
        entry=ttk.Entry(bar,textvariable=call,width=22);entry.pack(side='left')
        nodebar=ttk.Frame(panel);nodebar.pack(fill='x',pady=(0,8))
        ttk.Label(nodebar,text='NODE',style='Muted.TLabel').pack(side='left',padx=(0,8))
        target_name=tk.StringVar(value=initial);self.station_node=target_name
        self.station_main_label=next((name for name,target in targets.items() if target[0] is None),None)
        ttk.Combobox(nodebar,textvariable=target_name,values=list(targets),state='readonly',width=40).pack(side='left',fill='x',expand=True)
        self.station_note=tk.StringVar(value='Request details from the selected node.')
        ttk.Label(panel,textvariable=self.station_note,style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        frame=ttk.Frame(panel);frame.pack(fill='both',expand=True)
        self.station_text=tk.Text(frame,width=70,height=12,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',padx=10,pady=8,font=('Consolas',10),state='disabled')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.station_text.yview);scroll.pack(side='right',fill='y')
        self.station_text.pack(side='left',fill='both',expand=True);self.station_text.configure(yscrollcommand=scroll.set)
        ttk.Label(panel,text='Information comes from the selected node; live traffic may appear among the results.',style='Muted.TLabel').pack(anchor='w',pady=(8,8))
        def lookup(_event=None):
            try:
                ident,epoch,connection,_=targets[target_name.get()]
                if ident is None:
                    valid=epoch==self.generation and connection is self.connection and self.status.get()=='Connected'
                else:
                    item=self.secondary_nodes.get(ident)
                    valid=bool(item and item['epoch']==epoch and item['connection'] is connection and item['status']=='Connected')
                if not valid:raise OSError('The selected connection changed. Close and reopen this window.')
                if self.station_capture and self.station_capture.state in ('queued','waiting','receiving'):raise OSError('Wait for the current lookup to finish.')
                capture=StationCapture(call.get());connection.send(capture.command)
            except (OSError,ValueError,KeyError) as exc:messagebox.showerror('Station info',str(exc),parent=dialog);return
            self.station_target=(ident,epoch,connection);self.station_capture=capture;self.station_deadline=time.monotonic()+15;self.render_station()
        def close():
            self.interrupt_station();self.station_window=None;dialog.destroy()
        self.station_lookup=lookup
        ttk.Button(bar,text='Look up',style='Accent.TButton',command=lookup).pack(side='left',padx=8)
        ttk.Button(panel,text='Close',command=close).pack(anchor='e');entry.bind('<Return>',lookup)
        dialog.protocol('WM_DELETE_WINDOW',close);entry.focus_set()
        self.station_capture=None;self.station_deadline=None;self.station_target=None
        if initial_call is not None:lookup()

    def render_station(self):
        if self.station_window is None or not self.station_window.winfo_exists() or not self.station_capture: return
        capture=self.station_capture
        label={'queued':'Waiting to send…','waiting':'Waiting for a recognized response…','receiving':'Receiving…','complete':'Node response received.','partial':'Incomplete or unrecognized response; check Activity.'}[capture.state]
        if capture.not_found and capture.state == 'complete': label='The node reports no user record for this callsign.'
        if capture.truncated: label+=' Display limit reached.'
        self.station_note.set(label);self.station_text.configure(state='normal');self.station_text.delete('1.0','end')
        text='\n'.join(capture.lines)
        location=node_location(capture.lines) if capture.state=='complete' and not capture.truncated else None
        if location and self.local_grid.get():
            km,bearing=coordinate_path(grid_center(self.local_grid.get()),location)
            direction=f'{round(bearing)%360:03d}° true' if bearing is not None else 'bearing unavailable'
            text+=f'\n\nEstimate from your grid center to node location: {direction} · {km:.0f} km / {km*0.621371:.0f} mi'
        self.station_text.insert('1.0',text);self.station_text.configure(state='disabled')

    def interrupt_station(self):
        if self.station_capture: self.station_capture.interrupt();self.station_deadline=None;self.render_station()

    def interrupt_station_for(self, ident):
        if self.station_target and self.station_target[0] == ident: self.interrupt_station()

    def refresh_report_targets(self):
        if not hasattr(self,'report_node_box'): return
        if hasattr(self,'message_source_box'):self.refresh_message_source_filter()
        targets={}
        if self.connection and self.status.get() == 'Connected':
            label='Main connection' + (f' · {self.connection_target[0]}:{self.connection_target[1]}' if self.connection_target else '')
            targets[label]=(None,self.generation,self.connection)
        for ident,item in self.secondary_nodes.items():
            if item['connection'] and item['status']=='Connected':
                host,port,call=item['source'];targets[f'{host}:{port} · {call}']=(ident,item['epoch'],item['connection'])
        self.report_targets=targets
        current=self.report_node.get()
        self.report_node_box.configure(values=list(targets))
        self.message_node_box.configure(values=list(targets))
        if not current and targets:self.report_node.set(next(iter(targets)))
        if not self.message_node.get() and targets:self.message_node.set(next(iter(targets)))
        self.update_report_actions()

    def update_report_actions(self):
        if hasattr(self,'wwv_button'):
            state='normal' if self.report_node.get() in self.report_targets else 'disabled'
            self.wwv_button.configure(state=state);self.wcy_button.configure(state=state)
        if hasattr(self,'ann_button'):
            state='normal' if self.message_node.get() in self.report_targets else 'disabled'
            self.ann_button.configure(state=state);self.wx_button.configure(state=state)

        if hasattr(self,'refresh_spots_button'):
            self.refresh_spots_button.configure(state='normal' if self.report_targets else 'disabled')
        if hasattr(self,'spot_button'):
            available=any((self.dialect if ident is None else self.secondary_nodes[ident]['dialect']) in ('DXSpider','CC Cluster') for ident,_,_ in self.report_targets.values())
            self.spot_button.configure(state='normal' if available else 'disabled')

    def node_for_request(self, name):
        target=self.report_targets.get(name)
        if not target:raise OSError('Choose a connected node.')
        ident,epoch,connection=target
        if ident is None:
            valid=epoch==self.generation and connection is self.connection and self.status.get()=='Connected'
        else:
            item=self.secondary_nodes.get(ident)
            valid=bool(item and item['epoch']==epoch and item['connection'] is connection and item['status']=='Connected')
        if not valid:raise OSError('The selected connection changed. Choose a connected node again.')
        return connection

    def request_report(self, kind):
        try:
            if kind not in ('WWV', 'WCY'): raise ValueError('Unknown report type.')
            connection=self.node_for_request(self.report_node.get())
            count=self.report_count.get()
            if count not in ('Node default','5','10','20','50','100'): raise ValueError('Choose a history count from the list.')
            command='sh/'+kind.lower()
            if count != 'Node default': command += ('/' if kind == 'WWV' else ' ') + count
            connection.send(command)
        except (OSError, ValueError) as exc: messagebox.showerror('Propagation reports', str(exc)); return
        self.report_note.set(f'{kind} requested from {self.report_node.get()}; check its activity for the complete response.')

    def open_propagation_lookup(self):
        dialog=tk.Toplevel(self);dialog.title('Station lookup');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Station lookup',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Results appear in the selected node’s Activity. Prefix and moon require DXSpider.',style='Muted.TLabel',wraplength=480).pack(anchor='w',pady=(5,12))
        form=ttk.Frame(panel);form.pack(fill='x')
        ttk.Label(form,text='LOOKUP',style='Muted.TLabel').grid(row=0,column=0,sticky='w',padx=(0,10))
        ttk.Label(form,text='CALLSIGN OR PREFIX',style='Muted.TLabel').grid(row=0,column=1,sticky='w')
        kind=tk.StringVar(value='Prefix');call=tk.StringVar()
        ttk.Combobox(form,textvariable=kind,values=('Prefix','Heading','Sun','Moon','Time'),state='readonly',width=15).grid(row=1,column=0,sticky='ew',padx=(0,10),pady=(4,0))
        entry=ttk.Entry(form,textvariable=call,width=22);entry.grid(row=1,column=1,sticky='ew',pady=(4,0));entry.focus_set()
        ttk.Label(panel,text='NODE',style='Muted.TLabel').pack(anchor='w',pady=(12,4))
        node=tk.StringVar(value=self.report_node.get());targets=dict(self.report_targets)
        node_box=ttk.Combobox(panel,textvariable=node,values=list(targets),state='readonly',width=48);node_box.pack(fill='x')
        ttk.Label(panel,text='For Sun or Time, leave the callsign blank to use the node’s default. Node locations can be approximate.',style='Muted.TLabel',wraplength=480).pack(anchor='w',pady=(10,12))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def send_lookup():
            try:
                if node.get() not in targets or self.report_targets.get(node.get()) != targets[node.get()]:
                    raise OSError('The selected connection changed. Open this form again.')
                self.request_propagation_lookup(node.get(),kind.get(),call.get())
            except (OSError,ValueError) as exc:messagebox.showerror('Station lookup',str(exc),parent=dialog);return
            self.report_note.set(f'{kind.get()} requested from {node.get()}; check its activity for the response.')
            dialog.destroy()
        ttk.Button(buttons,text='Request',style='Accent.TButton',command=send_lookup).pack(side='left')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')

    def request_propagation_lookup(self,node,kind,call):
        connection=self.node_for_request(node)
        ident=self.report_targets[node][0]
        dialect=self.dialect if ident is None else self.secondary_nodes[ident]['dialect']
        commands={'Prefix':'sh/prefix','Heading':'sh/heading','Sun':'sh/sun','Moon':'sh/moon','Time':'sh/time'}
        if kind not in commands:raise ValueError('Choose a lookup from the list.')
        if dialect not in ('DXSpider','CC Cluster') or (dialect=='CC Cluster' and kind not in ('Heading','Sun','Time')):
            raise ValueError('Choose a DXSpider node for this lookup; CC Cluster supports Heading, Sun and Time here.')
        name=call.strip()
        if not name.isascii():raise ValueError('Enter one ASCII callsign or prefix, up to 20 characters.')
        name=name.upper()
        if (not name and kind not in ('Sun','Time')) or (name and (not re.fullmatch(r'[A-Z0-9]+(?:[/-][A-Z0-9]+)*',name) or len(name)>20)):
            raise ValueError('Enter one callsign or prefix, up to 20 characters.')
        command='sh/head' if dialect=='CC Cluster' and kind=='Heading' else commands[kind]
        connection.send(command+(' '+name if name else ''))

    def request_announcements(self):
        try:
            self.node_for_request(self.message_node.get()).send('sh/ann')
        except OSError as exc: messagebox.showerror('Announcements', str(exc)); return
        self.message_kind.set('All messages'); self.render_announcements()
        self.ann_note.set(f'Announcements requested from {self.message_node.get()}; check its activity for the complete response.')

    def request_weather(self):
        try:
            self.node_for_request(self.message_node.get()).send('sh/wx')
        except OSError as exc: messagebox.showerror('Weather', str(exc)); return
        self.message_kind.set('Weather'); self.render_announcements()
        self.ann_note.set(f'Weather requested from {self.message_node.get()}; check its activity for the complete response.')

    def open_mail(self):
        if not self.connection or self.status.get() != 'Connected':
            messagebox.showerror('Cluster mail', 'Connect to a cluster first.', parent=self); return
        if self.mail_window is not None and self.mail_window.winfo_exists(): self.mail_window.lift(); return
        generation=self.generation; connection=self.connection; dialect=self.dialect
        dialog=tk.Toplevel(self); self.mail_window=dialog; dialog.title('Cluster mail'); dialog.transient(self); dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16); panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Cluster mail',style='Heading.TLabel').pack(anchor='w')
        self.mail_note=tk.StringVar(value='Request a directory from the node.')
        ttk.Label(panel,textvariable=self.mail_note,style='Muted.TLabel').pack(anchor='w',pady=(5,8))
        filters=ttk.Frame(panel);filters.pack(fill='x',pady=(0,8))
        ttk.Label(filters,text='LIST',style='Muted.TLabel').pack(side='left',padx=(0,6))
        list_kind=tk.StringVar(value='Recent');subject=tk.StringVar();mail_count=tk.StringVar()
        kinds=['Recent','Own','New','Subject'] + (['Bulletins'] if self.dialect == 'CC Cluster' else ['From','To'])
        ttk.Combobox(filters,textvariable=list_kind,values=kinds,state='readonly',width=12).pack(side='left')
        ttk.Label(filters,text='SEARCH',style='Muted.TLabel').pack(side='left',padx=(12,6))
        ttk.Entry(filters,textvariable=subject,width=20).pack(side='left')
        if self.dialect == 'DXSpider':
            ttk.Label(filters,text='COUNT',style='Muted.TLabel').pack(side='left',padx=(12,6))
            ttk.Entry(filters,textvariable=mail_count,width=5).pack(side='left')
        list_hint = ('New: since last directory; Own includes public and sent messages. Subject uses one word.' if self.dialect == 'CC Cluster'
                     else 'New: since last login. Subject uses one word; From and To use a callsign. Count is optional.')
        ttk.Label(panel,text=list_hint,style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        frame=ttk.Frame(panel); frame.pack(fill='both',expand=True)
        self.mail_tree=ttk.Treeview(frame,columns=('number','flags','size','to','from','date','subject'),show='headings',height=7)
        for key,title,width in (('number','NO.',60),('flags','FLAGS',52),('size','SIZE',55),('to','TO',90),('from','FROM',90),('date','DATE / UTC',110),('subject','SUBJECT',235)):
            self.mail_tree.heading(key,text=title);self.mail_tree.column(key,width=width,minwidth=45,stretch=(key=='subject'))
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.mail_tree.yview);scroll.pack(side='right',fill='y')
        self.mail_tree.pack(side='left',fill='both',expand=True);self.mail_tree.configure(yscrollcommand=scroll.set)
        self.mail_detail=tk.StringVar(value='Select a message to see its original directory line.')
        detail_frame=ttk.Frame(panel);detail_frame.pack(fill='x',pady=(5,0))
        self.mail_detail_text=tk.Text(detail_frame,height=2,width=65,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',padx=8,pady=3,font=('Segoe UI',9),state='disabled')
        detail_scroll=ttk.Scrollbar(detail_frame,orient='vertical',command=self.mail_detail_text.yview);detail_scroll.pack(side='right',fill='y')
        self.mail_detail_text.pack(side='left',fill='x',expand=True);self.mail_detail_text.configure(yscrollcommand=detail_scroll.set)
        def set_mail_detail(value):
            self.mail_detail.set(value)
            self.mail_detail_text.configure(state='normal');self.mail_detail_text.delete('1.0','end');self.mail_detail_text.insert('1.0',value);self.mail_detail_text.configure(state='disabled')
        self.set_mail_detail=set_mail_detail
        set_mail_detail(self.mail_detail.get())
        def show_mail_detail(event=None):
            selection=self.mail_tree.selection()
            set_mail_detail(self.mail_tree.item(selection[0],'tags')[1] if selection else 'Select a message to see its original directory line.')
        self.mail_tree.bind('<<TreeviewSelect>>',show_mail_detail)
        ttk.Label(panel,text='Reading may mark a message read and may send a receipt. The response appears in Activity.',style='Muted.TLabel').pack(anchor='w',pady=(8,8))
        number_row=ttk.Frame(panel);number_row.pack(fill='x',pady=(0,8))
        ttk.Label(number_row,text='MESSAGE NUMBER',style='Muted.TLabel').pack(side='left',padx=(0,8))
        manual_number=tk.StringVar()
        ttk.Entry(number_row,textvariable=manual_number,width=12).pack(side='left')
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def still_connected():
            if generation != self.generation or connection is not self.connection or self.status.get() != 'Connected' or self.dialect != dialect:
                raise OSError('The connection changed. Close and reopen cluster mail.')
        def get_list():
            try:
                still_connected()
                if self.mail_directory.state in ('queued','waiting','receiving'): raise OSError('A directory request is already in progress.')
                if self.mail_read and self.mail_read.state in ('queued','waiting','receiving'): raise OSError('A message read is already in progress.')
                command=directory_command(self.dialect,list_kind.get(),subject.get(),mail_count.get())
                connection.send(command)
            except (OSError,ValueError) as exc: messagebox.showerror('Cluster mail',str(exc),parent=dialog); return
            self.mail_directory.queued(self.dialect,command);self.mail_deadline=time.monotonic()+15;self.render_mail()
        def read_number(number):
            try:
                still_connected()
                if self.mail_directory.state in ('queued','waiting','receiving') or (self.mail_read and self.mail_read.state in ('queued','waiting','receiving')): raise OSError('Wait for the current mail request to finish.')
                command=read_command(number); number=command.split()[1]
                connection.send(command)
            except (OSError,ValueError) as exc: messagebox.showerror('Cluster mail',str(exc),parent=dialog); return
            self.append(f'\nMail message {number} requested; the node response follows.\n')
            close()
            self.open_mail_reader(number)
        def read_selected():
            selection=self.mail_tree.selection()
            if not selection: messagebox.showerror('Cluster mail','Select a message first.',parent=dialog); return
            read_number(self.mail_tree.item(selection[0],'tags')[0])
        def draft_to_sender():
            try:
                still_connected()
                if self.mail_directory.state != 'complete':raise ValueError('Wait for a complete mail list before preparing a draft.')
                selection=self.mail_tree.selection()
                if not selection:raise ValueError('Select a message first.')
                draft=directory_reply_draft(self.mail_tree.item(selection[0],'tags')[1],dialect)
                if not draft:raise ValueError('The sender or subject in this directory entry cannot be used for a draft.')
            except (OSError,ValueError,IndexError) as exc:messagebox.showerror('Draft to sender',str(exc),parent=dialog);return
            self.open_mail_compose(*draft)
        def delete_selected():
            selection=self.mail_tree.selection()
            if not selection:
                messagebox.showerror('Cluster mail','Select a message first.',parent=dialog);return
            try:
                still_connected()
                if self.mail_directory.state in ('queued','waiting','receiving') or (self.mail_read and self.mail_read.state in ('queued','waiting','receiving')):
                    raise OSError('Wait for the current mail request to finish.')
                number=read_command(self.mail_tree.item(selection[0],'tags')[0]).split()[1]
            except (OSError,ValueError,IndexError) as exc:
                messagebox.showerror('Cluster mail',str(exc),parent=dialog);return
            if not messagebox.askyesno('Delete cluster mail',f'Request deletion of message {number} on the connected node?\n\nCheck Activity for the node response.',parent=dialog,default='no'):
                return
            try:
                still_connected()
                if self.mail_directory.state in ('queued','waiting','receiving') or (self.mail_read and self.mail_read.state in ('queued','waiting','receiving')):
                    raise OSError('Wait for the current mail request to finish.')
                connection.send(('DEL ' if dialect=='CC Cluster' else 'KILL ')+number)
            except OSError as exc:
                messagebox.showerror('Cluster mail',str(exc),parent=dialog);return
            self.append(f'\nDeletion of mail message {number} requested; check the node response.\n')
            self.mail_note.set(f'Deletion of message {number} requested. Check Activity, then refresh the list.')
        def close():
            self.mail_directory.interrupt();self.mail_deadline=None;dialog.destroy();self.mail_window=None
        dialog.protocol('WM_DELETE_WINDOW',close)
        ttk.Button(number_row,text='Read number',command=lambda:read_number(manual_number.get())).pack(side='left',padx=(8,0))
        ttk.Button(number_row,text='Draft to sender…',command=draft_to_sender).pack(side='left',padx=(8,0))
        ttk.Button(buttons,text='Get mail list',command=get_list).pack(side='left')
        ttk.Button(buttons,text='Read selected',command=read_selected).pack(side='left',padx=8)
        if self.dialect in ('DXSpider','CC Cluster'):
            ttk.Button(buttons,text='Compose…',command=self.open_mail_compose).pack(side='left')
        if self.dialect in ('DXSpider','CC Cluster'):
            ttk.Button(buttons,text='Delete selected…',command=delete_selected).pack(side='left',padx=8)
        ttk.Button(buttons,text='Close',command=close).pack(side='right')
        dialog.update_idletasks(); width=dialog.winfo_reqwidth();height=dialog.winfo_reqheight()
        x=max(0,min(self.winfo_rootx()+(self.winfo_width()-width)//2,self.winfo_screenwidth()-width))
        y=max(0,min(self.winfo_rooty()+(self.winfo_height()-height)//2,self.winfo_screenheight()-height))
        dialog.geometry(f'+{x}+{y}')
        self.mail_directory=MailDirectory(); self.render_mail()

    def render_mail(self):
        if self.mail_window is None or not self.mail_window.winfo_exists(): return
        directory=self.mail_directory
        label={'idle':'Request a directory from the node.','queued':'Waiting to send…','waiting':'Waiting for a recognized response…','receiving':'Receiving…','complete':f'{len(directory.rows)} messages listed.','partial':'Partial result: response interrupted or timed out.'}[directory.state]
        if directory.truncated: label+=' List truncated at 500 entries.'
        selection=self.mail_tree.selection()
        selected_number=self.mail_tree.item(selection[0],'tags')[0] if selection else None
        self.mail_note.set(label);self.mail_tree.delete(*self.mail_tree.get_children())
        for number,line in directory.rows:
            item=self.mail_tree.insert('', 'end',values=directory_fields(line,directory.dialect or self.dialect),tags=(number,line))
            if number==selected_number:self.mail_tree.selection_set(item)
        if not self.mail_tree.selection():self.set_mail_detail('Select a message to see its original directory line.')

    def interrupt_mail(self):
        self.mail_directory.interrupt();self.mail_deadline=None;self.render_mail()
        if self.mail_read:
            self.mail_read.interrupt(); self.mail_read_deadline = None; self.render_mail_reader()

    def open_mail_reader(self, number):
        if self.mail_reader is not None and self.mail_reader.winfo_exists(): self.mail_reader.destroy()
        self.mail_read = MailRead(number,self.dialect); self.mail_read_deadline = time.monotonic() + 15
        generation, connection = self.generation, self.connection
        dialog=tk.Toplevel(self); self.mail_reader=dialog; dialog.title(f'Mail message {number}'); dialog.transient(self); dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16); panel.pack(fill='both',expand=True)
        ttk.Label(panel,text=f'Mail message {number}',style='Heading.TLabel').pack(anchor='w')
        self.mail_read_note=tk.StringVar(value='Waiting to send…')
        ttk.Label(panel,textvariable=self.mail_read_note,style='Muted.TLabel').pack(anchor='w',pady=(5,8))
        frame=ttk.Frame(panel); frame.pack(fill='both',expand=True)
        self.mail_read_text=tk.Text(frame,height=15,width=85,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',padx=10,pady=8,font=('Segoe UI',10),state='disabled')
        scroll=ttk.Scrollbar(frame,orient='vertical',command=self.mail_read_text.yview);scroll.pack(side='right',fill='y')
        self.mail_read_text.pack(side='left',fill='both',expand=True);self.mail_read_text.configure(yscrollcommand=scroll.set)
        detail=('Unverified raw output: unrelated traffic may appear, and the message may be absent. Also in Activity.' if self.dialect=='CC Cluster' else 'Live node traffic may appear in the response. Complete output is also in Activity.')
        ttk.Label(panel,text=detail,style='Muted.TLabel').pack(anchor='w',pady=(8,8))
        def close():
            self.mail_read.interrupt();self.mail_read_deadline=None;self.mail_reader=None;dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW',close)
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def reply():
            draft=self.mail_read.reply_draft() if self.mail_read else None
            if generation != self.generation or connection is not self.connection or self.status.get() != 'Connected' or not draft:
                messagebox.showerror('Reply to mail','The message or connection is no longer available.',parent=dialog);return
            self.open_mail_compose(*draft)
        self.mail_reply_button=ttk.Button(buttons,text='Reply…',command=reply,state='disabled');self.mail_reply_button.pack(side='left')
        ttk.Button(buttons,text='Close',command=close).pack(side='right')

    def render_mail_reader(self):
        if self.mail_reader is None or not self.mail_reader.winfo_exists() or self.mail_read is None: return
        capture=self.mail_read
        self.mail_reply_button.configure(state='normal' if capture.reply_draft() else 'disabled')
        label={'queued':'Waiting to send…','waiting':'Waiting for a recognized response…','receiving':'Receiving node response…','complete':'Node response received.','partial':'Incomplete response: interrupted or timed out.'}[capture.state]
        if capture.dialect=='CC Cluster' and capture.state=='complete':label='Raw output stopped at a node prompt; the requested message may be absent.'
        if capture.truncated: label+=' Display limit reached.'
        self.mail_read_note.set(label)
        self.mail_read_text.configure(state='normal');self.mail_read_text.delete('1.0','end')
        self.mail_read_text.insert('1.0','\n'.join(capture.lines));self.mail_read_text.configure(state='disabled')

    def open_mail_compose(self, initial_recipient='', initial_subject=''):
        if not self.connection or self.status.get() != 'Connected' or self.dialect not in ('DXSpider','CC Cluster'):
            messagebox.showerror('Compose mail','Connect to a supported cluster first.',parent=self);return
        generation=self.generation;connection=self.connection;dialect=self.dialect
        dialog=tk.Toplevel(self);dialog.title('Compose cluster mail');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Compose cluster mail',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text=('CC Cluster composition is experimental; check Activity for the node response.' if dialect=='CC Cluster' else 'Up to 10 comma-separated recipients. The node decides whether to accept and deliver mail.'),style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        recipient=tk.StringVar(master=dialog,value=initial_recipient);subject=tk.StringVar(master=dialog,value=initial_subject)
        for label,var in [(('RECIPIENTS / BULLETIN ADDRESSES' if dialect=='DXSpider' else 'RECIPIENT CALLSIGN'),recipient),('SUBJECT  ·  UP TO 30 CHARACTERS',subject)]:
            ttk.Label(panel,text=label,style='Muted.TLabel').pack(anchor='w')
            ttk.Entry(panel,textvariable=var,width=55).pack(fill='x',pady=(3,8))
        bulletin=tk.BooleanVar(master=dialog,value=False);receipt=tk.BooleanVar(master=dialog,value=False)
        if dialect == 'DXSpider':
            options=ttk.Frame(panel);options.pack(fill='x',pady=(0,8))
            ttk.Checkbutton(options,text='Public bulletin (may be distributed to other nodes)',variable=bulletin).pack(anchor='w')
            ttk.Checkbutton(options,text='Request a read receipt',variable=receipt).pack(anchor='w')
        ttk.Label(panel,text='MESSAGE',style='Muted.TLabel').pack(anchor='w')
        body=tk.Text(panel,height=8,width=55,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',font=('Segoe UI',10));body.pack(fill='both',expand=True,pady=(3,8))
        ttk.Label(panel,text='Plain ASCII only; lines starting with / are not supported.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def submit():
            try:
                if generation!=self.generation or connection is not self.connection or self.status.get()!='Connected' or self.dialect!=dialect:raise OSError('The connection changed. Close this form and open it again.')
                if bulletin.get() or receipt.get():
                    connection.send_mail(recipient.get(),subject.get(),body.get('1.0','end-1c'),dialect=dialect,bulletin=bulletin.get(),receipt=receipt.get())
                else:
                    connection.send_mail(recipient.get(),subject.get(),body.get('1.0','end-1c'),dialect=dialect)
            except (OSError,ValueError,UnicodeError) as exc:
                messagebox.showerror('Compose mail',str(exc),parent=dialog);return
            self.mail_compose_pending = True
            self.append('\nMail exchange queued. Follow its progress in Activity.\n')
            self.notebook.select(self.console_page);dialog.destroy()
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Send mail',style='Accent.TButton',command=submit).pack(side='right',padx=(0,8))

    def open_chat(self):
        if self.chat_window is not None and self.chat_window.winfo_exists(): self.chat_window.lift();return
        targets={}
        if self.connection and self.status.get()=='Connected' and self.dialect=='DXSpider':
            targets['Main connection']=(None,self.generation,self.connection)
        for ident,item in self.secondary_nodes.items():
            if item['connection'] and item['status']=='Connected' and item['dialect']=='DXSpider':
                host,port,call=item['source'];targets[f'{host}:{port} · {call}']=(ident,item['epoch'],item['connection'])
        if not targets:
            messagebox.showerror('Chat groups','Connect to a DXSpider node first.',parent=self);return
        dialog=tk.Toplevel(self);self.chat_window=dialog;dialog.title('Chat groups');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Chat groups',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Join or leave a group on the selected node.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        target_name=tk.StringVar(value=next(iter(targets)));self.chat_target_name=target_name
        ttk.Label(panel,text='NODE',style='Muted.TLabel').pack(anchor='w')
        ttk.Combobox(panel,textvariable=target_name,values=list(targets),state='readonly',width=50).pack(fill='x',pady=(3,8))
        group=tk.StringVar();message=tk.StringVar();history_lines=tk.StringVar()
        ttk.Label(panel,text='GROUP (optional for history)',style='Muted.TLabel').pack(anchor='w')
        ttk.Entry(panel,textvariable=group,width=50).pack(fill='x',pady=(3,8))
        ttk.Label(panel,text='MESSAGE',style='Muted.TLabel').pack(anchor='w')
        ttk.Entry(panel,textvariable=message,width=50).pack(fill='x',pady=(3,8))
        ttk.Label(panel,text='HISTORY LINES TO SEARCH (optional)',style='Muted.TLabel').pack(anchor='w')
        ttk.Entry(panel,textvariable=history_lines,width=12).pack(anchor='w',pady=(3,8))
        note=tk.StringVar(value='Chat history also appears in Messages and node activity.');self.chat_note=note
        ttk.Label(panel,textvariable=note,style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        ttk.Label(panel,text='Refresh groups may reveal private account fields in the selected node activity.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def selected_connection():
            ident,epoch,connection=targets[target_name.get()]
            if ident is None:
                valid=epoch==self.generation and connection is self.connection and self.status.get()=='Connected' and self.dialect=='DXSpider'
            else:
                item=self.secondary_nodes.get(ident)
                valid=bool(item and item['epoch']==epoch and item['connection'] is connection and item['status']=='Connected' and item['dialect']=='DXSpider')
            if not valid:raise OSError('The selected connection changed. Close this window and open it again.')
            return ident,connection
        def run(action):
            try:
                ident,connection=selected_connection()
                capture=self.chat_readback if ident is None else self.secondary_nodes[ident]['chat_capture']
                if capture and capture.state in ('queued','waiting','receiving'): raise OSError('Wait for the membership readback to finish.')
                connection.send(group_command(action,group.get(),message.get(),history_lines.get()))
            except (OSError,ValueError,UnicodeError,KeyError) as exc:messagebox.showerror('Chat groups',str(exc),parent=dialog);return
            if action=='Send': message.set('')
            note.set(action+' requested. Check the selected node activity for its response.')
            if action=='Find groups':
                if ident is None:self.notebook.select(self.console_page)
                else:self.open_secondary_activity(ident)
        for action in ('Join','Leave','History','Send'):
            ttk.Button(buttons,text=action,command=lambda a=action:run(a),style='Accent.TButton' if action=='Send' else 'TButton').pack(side='left',padx=(0,6))
        def refresh():
            try:
                ident,connection=selected_connection()
                if ident is None:
                    if not self.connection_target:raise OSError('The connection changed. Close this window and open it again.')
                    if self.chat_readback and self.chat_readback.state in ('queued','waiting','receiving'): raise OSError('A membership readback is already in progress.')
                    if self.settings_readback and self.settings_readback.state in ('queued','waiting','receiving'): raise OSError('Wait for settings refresh to finish.')
                    call=self.connection_target[2]
                else:
                    item=self.secondary_nodes[ident];existing=item['chat_capture']
                    if existing and existing.state in ('queued','waiting','receiving'):raise OSError('A membership readback is already in progress.')
                    if self.station_target and self.station_target[0]==ident and self.station_capture and self.station_capture.state in ('queued','waiting','receiving'):raise OSError('Wait for station lookup to finish.')
                    call=item['source'][2]
                capture=GroupReadback(call);connection.send('stat/user')
            except (OSError,KeyError) as exc:messagebox.showerror('Chat groups',str(exc),parent=dialog);return
            if ident is None:self.chat_readback=capture;self.chat_readback_deadline=time.monotonic()+15
            else:item['chat_capture']=capture;item['chat_deadline']=time.monotonic()+15
            note.set('Waiting for node membership response…')
        def close():self.chat_window=None;dialog.destroy()
        group_actions=ttk.Frame(panel);group_actions.pack(fill='x',pady=(8,0))
        refresh_button=ttk.Button(group_actions,text='Refresh groups',command=refresh);refresh_button.pack(side='left')
        def target_changed(*_):
            note.set('Chat history also appears in Messages and node activity.')
        target_name.trace_add('write',target_changed);target_changed()
        ttk.Button(group_actions,text='Find active groups',command=lambda:run('Find groups')).pack(side='left',padx=(8,0))
        ttk.Button(buttons,text='Close',command=close).pack(side='right');dialog.protocol('WM_DELETE_WINDOW',close)

    def interrupt_chat_readback(self):
        if self.chat_readback: self.chat_readback.interrupt();self.chat_readback_deadline=None;self.update_chat_readback()

    def update_chat_readback(self):
        capture=self.chat_readback
        if not capture:return
        if capture.state=='complete':self.chat_groups=set(capture.groups)
        if self.chat_window is not None and self.chat_window.winfo_exists() and self.chat_target_name.get()=='Main connection':
            if capture.state=='complete':self.chat_note.set('Node groups: '+(', '.join(sorted(self.chat_groups)) if self.chat_groups else '(none)'))
            elif capture.state=='partial':self.chat_note.set('Membership response incomplete; previous groups retained. Check Activity.')

    def queue_message(self, generation, kind, text, recipient=''):
        if generation != self.generation or not self.connection or self.status.get() != 'Connected':
            raise OSError('The cluster connection changed. Close this form and open it again.')
        commands = message_commands(self.dialect, kind, text, recipient)
        if len(commands) == 1: self.connection.send(commands[0])
        else: self.connection.send_batch(commands)

    def queue_secondary_message(self, ident, epoch, connection, kind, text, recipient, dialect='DXSpider', direct_only=False):
        item = self.secondary_nodes.get(ident)
        if (not item or item['epoch'] != epoch or item['connection'] is not connection
                or item['status'] != 'Connected' or item['dialect'] != dialect or dialect not in ('DXSpider','CC Cluster')):
            raise OSError('The cluster connection changed. Close this form and open it again.')
        if direct_only and kind != 'Direct message': raise ValueError('Only direct messages are available in a reply.')
        commands = message_commands(dialect, kind, text, recipient)
        if len(commands) == 1: connection.send(commands[0])
        else: connection.send_batch(commands)

    def open_message(self, initial_recipient='', secondary_id=None):
        targets={}
        for label,(ident,epoch,connection) in self.report_targets.items():
            dialect=self.dialect if ident is None else self.secondary_nodes[ident]['dialect']
            if dialect in ('DXSpider','CC Cluster'):targets[label]=(ident,epoch,connection,dialect)
        locked=bool(initial_recipient)
        if locked:
            selected=next((label for label,target in targets.items() if target[0]==secondary_id),None)
        else:
            selected=self.message_node.get() if self.message_node.get() in targets else next(iter(targets),None)
        if not selected:
            messagebox.showerror('Compose message','Connect to a supported cluster node first.',parent=self);return
        dialog = tk.Toplevel(self); dialog.title('Compose message'); dialog.transient(self); dialog.resizable(False, False); dialog.configure(bg=self.theme_bg)
        panel = ttk.Frame(dialog, padding=20); panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Compose message', style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel, text='Delivery depends on the selected node.', style='Muted.TLabel').pack(anchor='w', pady=(5, 12))
        kind = tk.StringVar(value='Direct message'); recipient = tk.StringVar(value=initial_recipient); body = tk.StringVar();target_name=tk.StringVar(value=selected)
        ttk.Label(panel, text='TYPE', style='Muted.TLabel').pack(anchor='w')
        chooser = ttk.Combobox(panel, textvariable=kind, values=(['Direct message'] if locked else ['Direct message', 'Local announcement', 'Network announcement']), state='readonly', width=40)
        chooser.pack(fill='x', pady=(4, 10))
        ttk.Label(panel,text='SEND THROUGH',style='Muted.TLabel').pack(anchor='w')
        if locked:ttk.Label(panel,textvariable=target_name,style='Muted.TLabel').pack(anchor='w',pady=(4,10))
        else:ttk.Combobox(panel,textvariable=target_name,values=list(targets),state='readonly',width=40).pack(fill='x',pady=(4,10))
        def target_changed(*_):
            direct_only=locked
            chooser.configure(values=['Direct message'] if direct_only else ['Direct message','Local announcement','Network announcement'])
            if direct_only and kind.get()!='Direct message':kind.set('Direct message')
        target_name.trace_add('write',target_changed);target_changed()
        ttk.Label(panel, text='RECIPIENT CALLSIGNS (direct messages; comma separated)', style='Muted.TLabel').pack(anchor='w')
        ttk.Entry(panel, textvariable=recipient, width=50).pack(fill='x', pady=(4, 10))
        if not locked:
            list_row=ttk.Frame(panel);list_row.pack(fill='x',pady=(0,10))
            list_name=tk.StringVar(master=dialog)
            list_box=ttk.Combobox(list_row,textvariable=list_name,state='readonly',width=20,values=[item['name'] for item in self.talk_lists])
            list_box.pack(side='left');list_box.set('Saved recipient lists')
            def select_list(_event=None):
                match=next((item for item in self.talk_lists if item['name']==list_name.get()),None)
                if match:recipient.set(match['recipients'])
            list_box.bind('<<ComboboxSelected>>',select_list)
            def save_list():
                name=simpledialog.askstring('Save recipient list','List name:',parent=dialog)
                if name is None:return
                try:
                    record=validate_talk_list(name,recipient.get())
                    existing=next((i for i,item in enumerate(self.talk_lists) if item['name'].casefold()==record['name'].casefold()),None)
                    if existing is None and len(self.talk_lists)>=MAX_LISTS:raise ValueError('Remove a list before adding another; the limit is 20.')
                    if existing is not None:
                        if not messagebox.askyesno('Replace recipient list',f'Replace {self.talk_lists[existing]["name"]}?',parent=dialog):return
                        self.talk_lists[existing]=record
                    else:self.talk_lists.append(record)
                    list_box.configure(values=[item['name'] for item in self.talk_lists]);list_name.set(record['name'])
                    if not self._save_settings():messagebox.showwarning('Recipient lists','List changed for this session but could not be saved. See Activity.',parent=dialog)
                except ValueError as exc:messagebox.showerror('Recipient lists',str(exc),parent=dialog)
            def delete_list():
                existing=next((i for i,item in enumerate(self.talk_lists) if item['name']==list_name.get()),None)
                if existing is None:messagebox.showinfo('Recipient lists','Select a saved list first.',parent=dialog);return
                if not messagebox.askyesno('Delete recipient list',f'Delete {self.talk_lists[existing]["name"]}?',parent=dialog):return
                self.talk_lists.pop(existing);list_box.configure(values=[item['name'] for item in self.talk_lists]);list_name.set('Saved recipient lists')
                if not self._save_settings():messagebox.showwarning('Recipient lists','List changed for this session but could not be saved. See Activity.',parent=dialog)
            save_button=ttk.Button(list_row,text='Save list…',command=save_list);save_button.pack(side='left',padx=(8,0))
            delete_button=ttk.Button(list_row,text='Delete list…',command=delete_list);delete_button.pack(side='left',padx=(8,0))
            def list_state(*_):
                direct=kind.get()=='Direct message';list_box.configure(state='readonly' if direct else 'disabled')
                for button in (save_button,delete_button):button.configure(state='normal' if direct else 'disabled')
            kind.trace_add('write',list_state);list_state()
        ttk.Label(panel, text='MESSAGE', style='Muted.TLabel').pack(anchor='w')
        ttk.Entry(panel, textvariable=body, width=50).pack(fill='x', pady=(4, 10))
        hint = tk.StringVar()
        def update_hint(*_):
            hint.set({'Direct message': 'Up to 10 stations. Each receives a separate TALK command.', 'Local announcement': 'Sent to users on this node.', 'Network announcement': 'May be shared across the cluster network.'}.get(kind.get(), ''))
        kind.trace_add('write', update_hint);target_name.trace_add('write',update_hint);update_hint()
        ttk.Label(panel, textvariable=hint, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
        buttons = ttk.Frame(panel); buttons.pack(fill='x')
        def submit():
            try:
                ident,epoch,connection,dialect=targets[target_name.get()]
                if ident is None:
                    if epoch != self.generation or connection is not self.connection or self.status.get() != 'Connected' or self.dialect != dialect:
                        raise OSError('The cluster connection changed. Close this form and open it again.')
                    if locked and kind.get() != 'Direct message':raise ValueError('Only direct messages are available in a reply.')
                    self.queue_message(epoch,kind.get(),body.get(),recipient.get())
                else:self.queue_secondary_message(ident,epoch,connection,kind.get(),body.get(),recipient.get(),dialect,locked)
            except (OSError, ValueError, UnicodeError, KeyError) as exc:
                messagebox.showerror('Compose message', str(exc), parent=dialog); return
            self.append(f'\nMessage queued through {target_name.get()}. A disconnect can leave some recipients unsent.\n')
            self.notebook.select(self.console_page); dialog.destroy()
        ttk.Button(buttons, text='Cancel', command=dialog.destroy).pack(side='right')
        ttk.Button(buttons, text='Send message', style='Accent.TButton', command=submit).pack(side='right', padx=(0, 8))

    def request_directory(self, command):
        try:
            if command not in ('who', 'sh/config/node', 'sh/config'): raise ValueError('Unknown directory request.')
            if not self.connection or self.status.get() != 'Connected': raise OSError('Connect to a cluster first.')
            self.connection.send(command)
        except (OSError, ValueError) as exc: messagebox.showerror('Users and nodes', str(exc)); return
        self.directory_note.set('Requested. The prior result remains until a response is recognized.')

    def clear_directory(self):
        self.directory = DirectoryCapture(); self.render_directory(); self.directory_note.set('View cleared.')

    def render_directory(self):
        self.directory_text.configure(state='normal'); self.directory_text.delete('1.0','end')
        self.directory_text.insert('1.0', self.directory.text()); self.directory_text.configure(state='disabled')

    def directory_disconnected(self):
        if self.directory.lines:
            state = 'Partial result' if self.directory.active or not self.directory.complete else 'Result'
            self.directory_note.set(f'{state} from the previous connection.')
        else: self.directory_note.set('Connect to a node to request a list.')
        self.directory.active=False

    def clear_announcements(self):
        self.announcements.clear(); self.clear_unread('messages'); self.render_announcements(); self.ann_note.set('View cleared.')

    @staticmethod
    def source_label(source):
        if source and source[:2] == ('archive',0):return f'Recording · {source[2]}'
        return f'{source[0]}:{source[1]} · {source[2]}' if source else 'Main connection'

    def refresh_message_source_filter(self):
        sources={entry[1] for entry in self.announcements if isinstance(entry,tuple) and len(entry)>1 and entry[1]}
        if self.connection_target:
            host,port,call=self.connection_target;sources.add((host.casefold(),port,call))
        sources.update(item['source'] for item in self.secondary_nodes.values())
        targets={self.source_label(source):source for source in sources}
        if any(not isinstance(entry,tuple) or len(entry)<2 or entry[1] is None for entry in self.announcements):targets['Unknown source']=None
        current=self.message_source_filter.get()
        if current != 'All sources' and current not in targets and current in self.message_source_targets:
            targets[current]=self.message_source_targets[current]
        self.message_source_targets=targets
        self.message_source_box.configure(values=['All sources',*sorted(targets,key=str.casefold)])

    def render_announcements(self):
        selected = self.ann_tree.selection()
        previous = tuple(self.ann_tree.item(selected[0], 'values')) if selected else None
        self.ann_tree.delete(*self.ann_tree.get_children());self.ann_row_sources={}
        self.refresh_message_source_filter();source_label=self.message_source_filter.get()
        source_filtered=source_label != 'All sources';selected_source=self.message_source_targets.get(source_label)
        search=self.message_search.get().strip().casefold()
        restore = None
        for entry in reversed(self.announcements):
            if isinstance(entry,tuple):
                text,source=entry[:2]
                category=entry[2] if len(entry)>2 else ('weather' if is_weather(text) else 'history' if SPIDER_HISTORY.match(text) else 'announcements')
            else:
                text,source=entry,None
                category='weather' if is_weather(text) else 'history' if SPIDER_HISTORY.match(text) else 'announcements'
            kind={'Announcements':'announcements','Weather':'weather','Direct messages':'talk','Group chat':'chat','History (unclassified)':'history'}.get(self.message_kind.get())
            if kind and category != kind: continue
            if source_filtered and source != selected_source: continue
            if search and search not in text.casefold(): continue
            values=(text,self.source_label(source));item=self.ann_tree.insert('', 'end', values=values)
            self.ann_row_sources[item]=source
            if previous == values and restore is None: restore = item
        if restore: self.ann_tree.selection_set(restore)
        self.show_announcement()

    def show_announcement(self):
        selected = self.ann_tree.selection()
        text = self.ann_tree.item(selected[0], 'values')[0] if selected else ''
        self.ann_detail.configure(state='normal'); self.ann_detail.delete('1.0','end')
        self.ann_detail.insert('1.0',text); self.ann_detail.configure(state='disabled')
        self.update_reply_action()

    def show_talk_alert(self, text, source):
        self.talk_alerts.append((text,source))
        if self.talk_alert_window is None or not self.talk_alert_window.winfo_exists():
            dialog=tk.Toplevel(self);self.talk_alert_window=dialog;dialog.title('Direct messages');dialog.transient(self);dialog.configure(bg=self.theme_bg)
            panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
            ttk.Label(panel,text='Direct messages',style='Heading.TLabel').pack(anchor='w')
            ttk.Label(panel,text='Recent messages received while this window is open.',style='Muted.TLabel').pack(anchor='w',pady=(5,8))
            frame=ttk.Frame(panel);frame.pack(fill='both',expand=True)
            self.talk_alert_text=tk.Text(frame,width=70,height=10,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',padx=10,pady=8,font=('Segoe UI',10),state='disabled')
            scroll=ttk.Scrollbar(frame,orient='vertical',command=self.talk_alert_text.yview);scroll.pack(side='right',fill='y')
            self.talk_alert_text.pack(side='left',fill='both',expand=True);self.talk_alert_text.configure(yscrollcommand=scroll.set)
            buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(10,0))
            def close():
                self.talk_alert_window=None;self.talk_alerts.clear();dialog.destroy()
            def open_messages():
                self.message_kind.set('Direct messages');self.message_source_filter.set('All sources');self.message_search.set('')
                self.render_announcements();self.notebook.select(5);self.deiconify();self.lift();close()
            ttk.Button(buttons,text='Show direct messages',style='Accent.TButton',command=open_messages).pack(side='left')
            ttk.Button(buttons,text='Close',command=close).pack(side='right');dialog.protocol('WM_DELETE_WINDOW',close)
        output=self.talk_alert_text;output.configure(state='normal');output.delete('1.0','end')
        output.insert('1.0','\n\n'.join(f'{self.source_label(source)}\n{text}' for text,source in self.talk_alerts));output.configure(state='disabled');output.see('end')

    def selected_talk_target(self):
        if not hasattr(self, 'ann_tree'): return None
        selected = self.ann_tree.selection()
        if not selected: return None
        source = getattr(self, 'ann_row_sources', {}).get(selected[0])
        text = self.ann_tree.item(selected[0], 'values')[0]
        if self.connection_target:
            main = (self.connection_target[0].casefold(), self.connection_target[1], self.connection_target[2])
            if (source is None or source == main) and self.connection and self.status.get() == 'Connected' and self.dialect in ('DXSpider','CC Cluster'):
                sender = talk_sender(text, main[2])
                if sender: return sender, None
        if source is not None:
            for ident, item in self.secondary_nodes.items():
                if item['source'] == source and item['connection'] and item['status'] == 'Connected' and item['dialect'] in ('DXSpider','CC Cluster'):
                    sender = talk_sender(text, source[2])
                    if sender: return sender, ident
        return None

    def update_reply_action(self):
        if hasattr(self, 'reply_button'):
            self.reply_button.configure(state='normal' if self.selected_talk_target() else 'disabled')

    def reply_to_talk(self):
        target = self.selected_talk_target()
        if target:self.open_message(target[0], secondary_id=target[1])

    def clear_reports(self):
        self.reports.clear(); self.clear_unread('reports'); self.render_reports(); self.report_note.set('View cleared. Request WWV or WCY to load reports.')

    def render_reports(self):
        latest=next((entry for entry in reversed(self.reports) if entry[0]=='WWV'),None)
        if latest:
            source=latest[2] if len(latest)>2 else None
            text=' '.join(latest[1].split())
            self.spot_wwv_summary.set(f'Last received WWV · {self.source_label(source)} · {text[:110]}' + ('…' if len(text)>110 else ''))
        else:self.spot_wwv_summary.set('WWV · No report received yet')
        self.report_tree.delete(*self.report_tree.get_children())
        for entry in reversed(self.reports):
            kind,text=entry[:2];source=entry[2] if len(entry)>2 else None
            if self.report_kind.get() in ('All reports', kind):
                self.report_tree.insert('', 'end', values=(kind, text, self.source_label(source)))

    def refresh_settings(self):
        try:
            if not self.connection or self.status.get() != 'Connected': raise OSError('Connect to a cluster first.')
            if self.dialect == 'CC Cluster':
                self.connection.send('sh/settings')
                self.settings_note.set('Readback requested; values update as the node replies.')
            elif self.dialect == 'DXSpider' and self.connection_target:
                if self.settings_readback and self.settings_readback.state in ('queued', 'waiting', 'receiving'): raise OSError('A settings readback is already in progress.')
                if self.chat_readback and self.chat_readback.state in ('queued', 'waiting', 'receiving'): raise OSError('Wait for group refresh to finish.')
                capture = SettingsReadback(self.connection_target[2]); self.connection.send('stat/user')
                self.settings_readback = capture; self.settings_deadline = time.monotonic() + 15
                self.settings_note.set('Waiting for current-user settings…')
            else: raise OSError('Connect to a supported cluster first.')
        except OSError as exc: messagebox.showerror('Refresh settings',str(exc))

    def interrupt_settings_readback(self):
        if self.settings_readback and self.settings_readback.state in ('queued', 'waiting', 'receiving'):
            self.settings_readback.interrupt(); self.settings_deadline = None
            self.settings_note.set('Settings refresh incomplete; previous values retained.')

    def finish_settings_readback(self):
        capture = self.settings_readback
        if not capture or capture.state not in ('complete', 'partial'): return
        self.settings_deadline = None
        if capture.state == 'complete':
            self.node_settings = {name:capture.values.get(name,'Unknown') for name in (*SPIDER_SETTING_COMMANDS,*RBN_FIELDS)}
            self.settings_note.set('Recognized settings refreshed; fields omitted by the node are unknown.')
            self.render_settings()
        else: self.settings_note.set('Settings refresh incomplete; previous values retained.')

    def update_filter_view(self):
        self.cc_filter.pack_forget(); self.spider_filter.pack_forget()
        ready = bool(self.connection and self.status.get() == 'Connected')
        self.filter_refresh.configure(state='normal' if ready else 'disabled')
        if ready and self.dialect == 'CC Cluster':
            self.cc_filter.pack(fill='x', anchor='nw'); self.filter_note.set('CC Cluster')
        elif ready and self.dialect == 'DXSpider':
            self.spider_filter.pack(fill='x', anchor='nw'); self.filter_note.set('DXSpider')
        else: self.filter_note.set('Connect to a cluster to manage its filters.')

    def open_filter_value_picker(self,value,category,dialect,parent,*,stream=None,ensure_current=None):
        if parent is self and ensure_current is None:
            generation,connection,status,node_dialect=self.generation,self.connection,self.status.get(),self.dialect
            def ensure_current():
                if (generation!=self.generation or connection is not self.connection
                        or status!=self.status.get() or node_dialect!=self.dialect):
                    raise OSError('The connection changed. Close this window and open it again.')
        kind=category.get()
        if kind.endswith('country'):
            self.open_filter_country_picker(value,category,parent,ensure_current)
        elif kind.endswith('state'):
            self.open_filter_state_picker(value,category,parent,ensure_current)
        elif dialect=='CC Cluster' and kind=='Band / frequency segment':
            self.open_cc_band_picker(value,category,parent,ensure_current)
        elif dialect=='DXSpider' and kind=='Band or range':
            self.open_spider_band_picker(value,category,stream,parent,ensure_current)

    def open_filter_state_picker(self,value,category,parent,ensure_current=None):
        if ensure_current:
            try:ensure_current()
            except OSError as exc:messagebox.showerror('Choose states / provinces',str(exc),parent=parent);return
        original=value.get();original_category=category.get()
        tokens=[part.strip() for part in original.split(',') if part.strip()]
        regions=US_STATES+CANADIAN_PROVINCES
        available={code for code,_ in regions};selected={part.upper() for part in tokens if part.upper() in available}
        dialog=tk.Toplevel(parent);dialog.title('Choose states / provinces');dialog.transient(parent);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Choose states / provinces',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Selection fills the form; it does not change the node. Other typed values are kept.\nU.S. states appear first, then Canada. Coverage depends on the node.\nDXSpider may pass all states without USDB; Canadian coverage depends on its data.',style='Muted.TLabel',wraplength=700).pack(anchor='w',pady=(5,10))
        viewport=ttk.Frame(panel);viewport.pack(fill='both',expand=True)
        canvas=tk.Canvas(viewport,highlightthickness=0,bd=0,bg=self.theme_bg)
        canvas.grid(row=0,column=0,sticky='nsew')
        viewport.rowconfigure(0,weight=1);viewport.columnconfigure(0,weight=1)
        grid=ttk.Frame(canvas);canvas.create_window(0,0,window=grid,anchor='nw')
        variables={}
        for i,(code,name) in enumerate(regions):
            variable=tk.BooleanVar(master=dialog,value=code in selected);variables[code]=variable
            check=ttk.Checkbutton(grid,text=f'{name} ({code})',variable=variable)
            check.grid(row=i%16,column=i//16,sticky='w',padx=(0,12),pady=1)
            def reveal(event,widget=check):
                canvas.update_idletasks();top=widget.winfo_y();bottom=top+widget.winfo_height()
                visible_top=canvas.canvasy(0);visible_bottom=visible_top+canvas.winfo_height()
                if top<visible_top or bottom>visible_bottom:
                    canvas.yview_moveto(max(0,(top if top<visible_top else bottom-canvas.winfo_height())/max(1,grid.winfo_height())))
                left=widget.winfo_x();right=left+widget.winfo_width()
                visible_left=canvas.canvasx(0);visible_right=visible_left+canvas.winfo_width()
                if left<visible_left or right>visible_right:
                    canvas.xview_moveto(max(0,(left if left<visible_left else right-canvas.winfo_width())/max(1,grid.winfo_width())))
            check.bind('<FocusIn>',reveal)
            check.bind('<MouseWheel>',lambda event:canvas.yview_scroll(-int(event.delta/120),'units'))
        ttk.Label(panel,text='Up to 50 values and 200 characters.',style='Muted.TLabel').pack(anchor='w',pady=(8,0))
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(8,0))
        def clear_states():
            for variable in variables.values():variable.set(False)
        def apply():
            try:
                if ensure_current:ensure_current()
                if category.get()!=original_category or value.get()!=original:
                    raise ValueError('The filter changed. Close this window and open it again.')
                chosen={code for code,variable in variables.items() if variable.get()}
                result=[part for part in tokens if part.upper() not in available or part.upper() in chosen]
                included={part.upper() for part in result}
                result.extend(code for code,_ in regions if code in chosen and code not in included)
                if len(result)>50 or len(','.join(result))>200:raise ValueError('Choose fewer states: the filter allows up to 50 values and 200 characters.')
            except (OSError,ValueError) as exc:messagebox.showerror('Choose states / provinces',str(exc),parent=dialog);return
            value.set(','.join(result));dialog.destroy()
        ttk.Button(buttons,text='Clear selection',command=clear_states).pack(side='left')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Use selection',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))
        grid.update_idletasks();canvas.configure(scrollregion=(0,0,grid.winfo_reqwidth(),grid.winfo_reqheight()),width=grid.winfo_reqwidth(),height=1)
        dialog.update_idletasks()
        available_height=max(80,parent.winfo_screenheight()-100-(dialog.winfo_reqheight()-1))
        available_width=max(120,parent.winfo_screenwidth()-100-(dialog.winfo_reqwidth()-grid.winfo_reqwidth()))
        grid_height=grid.winfo_reqheight();grid_width=grid.winfo_reqwidth()
        if grid_height>available_height:
            scrollbar=ttk.Scrollbar(viewport,orient='vertical',command=canvas.yview);scrollbar.grid(row=0,column=1,sticky='ns');canvas.configure(yscrollcommand=scrollbar.set)
        if grid_width>available_width:
            scrollbar=ttk.Scrollbar(viewport,orient='horizontal',command=canvas.xview);scrollbar.grid(row=1,column=0,sticky='ew');canvas.configure(xscrollcommand=scrollbar.set)
        canvas.configure(width=min(grid_width,available_width),height=min(grid_height,available_height))
        canvas.bind('<MouseWheel>',lambda event:canvas.yview_scroll(-int(event.delta/120),'units'))
        dialog.update_idletasks();width,height=dialog.winfo_reqwidth(),dialog.winfo_reqheight()
        x=max(0,min(parent.winfo_rootx()+(parent.winfo_width()-width)//2,parent.winfo_screenwidth()-width))
        y=max(0,min(parent.winfo_rooty()+(parent.winfo_height()-height)//2,parent.winfo_screenheight()-height))
        dialog.geometry(f'+{x}+{y}')

    def open_filter_country_picker(self,value,category,parent,ensure_current=None):
        if ensure_current:
            try:ensure_current()
            except OSError as exc:messagebox.showerror('Choose countries',str(exc),parent=parent);return
        index=self.country_index
        if not index:
            messagebox.showinfo('Choose countries','Load a CTY.DAT country file in Settings first.',parent=parent);return
        countries=index.filter_countries()
        if not countries:
            messagebox.showinfo('Choose countries','The loaded country file has no unambiguous usable prefixes.',parent=parent);return
        original=value.get();original_category=category.get()
        tokens=[part.strip() for part in original.split(',') if part.strip()]
        available={prefix for prefix,_,_ in countries}
        chosen={part.upper() for part in tokens if part.upper() in available}
        dialog=tk.Toplevel(parent);dialog.title('Choose countries');dialog.transient(parent);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Choose countries',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Click countries to select or deselect them. This fills the form; it does not change the node.\nThe form accepts up to 50 values and 200 characters. Country data may be outdated;\nnodes may interpret prefixes differently.',style='Muted.TLabel',wraplength=540).pack(anchor='w',pady=(5,10))
        controls=ttk.Frame(panel);controls.pack(fill='x',pady=(0,6))
        ttk.Label(controls,text='CONTINENT',style='Muted.TLabel').pack(side='left',padx=(0,6))
        continent=tk.StringVar(master=dialog,value='All')
        ttk.Combobox(controls,textvariable=continent,values=('All','AF','AN','AS','EU','NA','OC','SA'),state='readonly',width=8).pack(side='left')
        ttk.Label(controls,text='FIND',style='Muted.TLabel').pack(side='left',padx=(14,6))
        query=tk.StringVar(master=dialog);ttk.Entry(controls,textvariable=query,width=24).pack(side='left',fill='x',expand=True)
        frame=ttk.Frame(panel);frame.pack(fill='both',expand=True)
        listing=tk.Listbox(frame,selectmode='multiple',exportselection=False,height=14,width=58,
                           bg=self.theme_surface,fg=self.theme_ink,selectbackground='#36545a' if self.dark_theme.get() else '#c7e7e1',selectforeground=self.theme_ink)
        scroll=ttk.Scrollbar(frame,orient='vertical',command=listing.yview);scroll.pack(side='right',fill='y')
        listing.pack(side='left',fill='both',expand=True);listing.configure(yscrollcommand=scroll.set)
        visible=[];selected_note=tk.StringVar(master=dialog)
        def sync(*_):
            selected={visible[i][0] for i in listing.curselection()}
            chosen.difference_update(prefix for prefix,_,_ in visible);chosen.update(selected)
            selected_note.set(f'{len(chosen)} {"country" if len(chosen)==1 else "countries"} selected')
        def refresh(*_):
            sync();listing.delete(0,'end');visible.clear()
            term=query.get().strip().casefold();region=continent.get()
            for prefix,name,area in countries:
                if region!='All' and area!=region:continue
                if term and term not in (prefix+' '+name+' '+area).casefold():continue
                visible.append((prefix,name,area));listing.insert('end',f'{name}  ·  {prefix}  ·  {area}')
                if prefix in chosen:listing.selection_set('end')
        listing.bind('<<ListboxSelect>>',sync);query.trace_add('write',refresh);continent.trace_add('write',refresh);refresh()
        selection_row=ttk.Frame(panel);selection_row.pack(fill='x',pady=(6,8))
        ttk.Label(selection_row,textvariable=selected_note,style='Muted.TLabel').pack(side='left')
        def select_shown():
            if visible:listing.selection_set(0,'end')
            sync()
        def clear_shown():
            listing.selection_clear(0,'end');sync()
        ttk.Button(selection_row,text='Clear shown',command=clear_shown).pack(side='right')
        ttk.Button(selection_row,text='Select shown',command=select_shown).pack(side='right',padx=(0,6))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def apply():
            sync()
            try:
                if ensure_current:ensure_current()
                if self.country_index is not index or category.get()!=original_category or value.get()!=original:
                    raise ValueError('The filter or country data changed. Close this window and open it again.')
                result=[part for part in tokens if part.upper() not in available or part.upper() in chosen]
                included={part.upper() for part in result}
                result.extend(prefix for prefix in sorted(chosen) if prefix not in included)
                if len(result)>50 or len(','.join(result))>200:raise ValueError('Choose fewer countries: the filter allows up to 50 values and 200 characters.')
            except (OSError,ValueError) as exc:messagebox.showerror('Choose countries',str(exc),parent=dialog);return
            value.set(','.join(result));dialog.destroy()
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Use selection',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))
        dialog.update_idletasks()
        width,height=dialog.winfo_reqwidth(),dialog.winfo_reqheight()
        x=max(0,min(parent.winfo_rootx()+(parent.winfo_width()-width)//2,parent.winfo_screenwidth()-width))
        y=max(0,min(parent.winfo_rooty()+(parent.winfo_height()-height)//2,parent.winfo_screenheight()-height))
        dialog.geometry(f'+{x}+{y}')

    def open_spider_band_picker(self, value=None, category=None, stream=None, parent=None, ensure_current=None):
        if value is None:
            generation,connection=self.generation,self.connection
            def ensure_current():
                if generation!=self.generation or connection is not self.connection or not connection or self.status.get()!='Connected' or self.dialect!='DXSpider':
                    raise OSError('The connection changed. Close this window and open it again.')
            value=self.spider_expression;category=self.spider_filter_kind;stream=self.spider_stream;parent=self
        if category.get()!='Band or range':return
        original_stream=stream.get();original_value=value.get()
        choices=SPIDER_PICKER_BANDS
        tokens=[part.strip() for part in original_value.split(',') if part.strip()]
        custom=[part for part in tokens if part.casefold() not in choices]
        dialog=tk.Toplevel(parent);dialog.title('Choose node bands');dialog.transient(parent);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Choose bands',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='The node determines which bands it supports. Existing ranges are retained.',style='Muted.TLabel').pack(anchor='w',pady=(4,8))
        grid=ttk.Frame(panel);grid.pack(anchor='w')
        selected={}
        for index,band in enumerate(choices):
            selected[band]=tk.BooleanVar(master=dialog,value=band in [part.casefold() for part in tokens])
            label='1.25 m' if band=='220' else band
            ttk.Checkbutton(grid,text=label,variable=selected[band]).grid(row=index//5,column=index%5,sticky='w',padx=(0,14),pady=3)
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(10,0))
        def apply():
            try:
                if ensure_current:ensure_current()
                if category.get()!='Band or range' or stream.get()!=original_stream or value.get()!=original_value:raise OSError('The filter changed. Close this window and open it again.')
            except OSError as exc:messagebox.showerror('Choose node bands',str(exc),parent=dialog);return
            value.set(','.join([band for band in choices if selected[band].get()]+custom));dialog.destroy()
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Use selection',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))

    def open_cc_band_picker(self, value=None, category=None, parent=None, ensure_current=None):
        if value is None and category is None and parent is None and ensure_current is None:
            generation,connection=self.generation,self.connection
            def ensure_current():
                if generation!=self.generation or connection is not self.connection or not connection or self.status.get()!='Connected' or self.dialect!='CC Cluster':
                    raise OSError('The connection changed. Close this window and open it again.')
        value=value if value is not None else self.keyword_text
        category=category if category is not None else self.cc_filter_kind
        parent=parent if parent is not None else self
        if category.get() != 'Band / frequency segment': return
        original_value=value.get()
        dialog=tk.Toplevel(parent);dialog.title('Choose bands and segments');dialog.transient(parent);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Bands and frequency segments',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Select one or more rows. Ctrl-click adds or removes a row.',style='Muted.TLabel').pack(anchor='w',pady=(5,4))
        ttk.Label(panel,text='Labels describe frequency ranges, not the detected mode. Ranges are in kHz.',style='Muted.TLabel').pack(anchor='w',pady=(0,8))
        table=ttk.Frame(panel);table.pack(fill='both',expand=True)
        tree=ttk.Treeview(table,columns=('selection','range'),show='headings',selectmode='extended',height=14)
        tree.heading('selection',text='BAND / SEGMENT');tree.heading('range',text='REFERENCE RANGE  kHz')
        tree.column('selection',width=190,minwidth=140);tree.column('range',width=220,minwidth=150)
        scroll=ttk.Scrollbar(table,orient='vertical',command=tree.yview);scroll.pack(side='right',fill='y')
        tree.pack(side='left',fill='both',expand=True);tree.configure(yscrollcommand=scroll.set)
        for band,modes in CC_DXBM_SEGMENTS.items():
            if band!='70':tree.insert('', 'end',iid=band,values=(f'{band} m  ·  whole band','Node-defined band'))
            for mode in modes:
                key=f'{band}-{mode}';tree.insert('', 'end',iid=key,values=(f'{key}  ·  70 cm' if band=='70' else key,CC_DXBM_RANGES[key]))
        tree.insert('', 'end',iid='MW-MW',values=('MW-MW',CC_DXBM_RANGES['MW-MW']))
        chosen=[part.strip().upper() for part in original_value.split(',') if part.strip()]
        for key in chosen:
            if tree.exists(key):tree.selection_add(key)
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(10,0))
        def apply():
            try:
                if ensure_current:ensure_current()
            except OSError as exc:messagebox.showerror('Choose bands',str(exc),parent=dialog);return
            if category.get() != 'Band / frequency segment' or value.get()!=original_value:
                messagebox.showerror('Choose bands','The filter changed. Close this window and open it again.',parent=dialog);return
            selected=set(tree.selection());value.set(','.join(item for item in tree.get_children() if item in selected));dialog.destroy()
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Use selection',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))

    def show_cc_bandtime(self):
        try:
            if not self.connection or self.status.get()!='Connected' or self.dialect!='CC Cluster':
                raise OSError('Connect to a CC Cluster node first.')
            if self.filter_readback.state in ('queued','waiting','receiving'):
                raise OSError('A filter readback is already in progress.')
            command=self.connection.send_tracked('sh/filter bandtime')
        except (OSError,ValueError) as exc:messagebox.showerror('Time filter',str(exc),parent=self);return
        self.filter_readback.queued(self.dialect,self.connection_target[2] if self.connection_target else self.call.get(),bandtime=True)
        self.filter_readback.ambiguous=self.filter_readback_unresolved
        self.filter_readback.command=command;self.filter_deadline=time.monotonic()+15
        self.open_filter_readback()

    def show_filters(self):
        try:
            if not self.connection or self.status.get() != 'Connected': raise OSError('Connect to a cluster first.')
            if self.filter_readback.state in ('queued', 'waiting', 'receiving'): raise OSError('A filter readback is already in progress.')
            command=self.connection.send_tracked('sh/filter')
        except OSError as exc: messagebox.showerror('Node filters', str(exc)); return
        self.filter_readback.queued(self.dialect,self.connection_target[2] if self.connection_target else self.call.get()); self.filter_readback.command=command; self.filter_deadline = time.monotonic() + 15
        self.filter_readback.ambiguous=self.filter_readback_unresolved
        self.open_filter_readback()

    def open_filter_readback(self):
        if self.filter_window is None or not self.filter_window.winfo_exists():
            self.filter_window = tk.Toplevel(self); self.filter_window.title('Node filter readback'); self.filter_window.transient(self);self.filter_window.configure(bg=self.theme_bg)
            panel=ttk.Frame(self.filter_window, padding=16); panel.pack(fill='both', expand=True)
            self.filter_readback_note=tk.StringVar()
            ttk.Label(panel, textvariable=self.filter_readback_note, style='Muted.TLabel').pack(anchor='w', pady=(0, 8))
            frame=ttk.Frame(panel); frame.pack(fill='both', expand=True)
            self.filter_readback_text=tk.Text(frame, width=85, height=18, wrap='word', background=self.theme_surface, foreground=self.theme_ink, relief='flat', font=('Consolas', 10), state='disabled')
            scroll=ttk.Scrollbar(frame, orient='vertical', command=self.filter_readback_text.yview); scroll.pack(side='right', fill='y')
            self.filter_readback_text.pack(side='left', fill='both', expand=True); self.filter_readback_text.configure(yscrollcommand=scroll.set)
            ttk.Label(panel, text='Live traffic or other command output may appear in this response.', style='Muted.TLabel').pack(anchor='w', pady=(8, 0))
            self.filter_window.update_idletasks()
            width = self.filter_window.winfo_reqwidth(); height = self.filter_window.winfo_reqheight()
            x = max(0, min(self.winfo_rootx() + (self.winfo_width() - width) // 2, self.winfo_screenwidth() - width))
            y = max(0, min(self.winfo_rooty() + (self.winfo_height() - height) // 2, self.winfo_screenheight() - height))
            self.filter_window.geometry(f'+{x}+{y}')
        self.render_filter_readback(); self.filter_window.lift()

    def render_filter_readback(self):
        if self.filter_window is None or not self.filter_window.winfo_exists(): return
        capture = self.filter_readback
        status = {'queued':'Waiting to send…', 'waiting':'Waiting for a recognized response…', 'receiving':'Receiving…', 'complete':'Response ended.', 'partial':'Partial or ambiguous response.', 'idle':'No response.'}[capture.state]
        if capture.ambiguous: status += ' An earlier filter request may have produced this response. Reconnect to clear uncertainty.'
        if capture.truncated: status += ' Output exceeded the limit and was truncated.'
        if capture.stale: status += ' A filter change was requested after this readback; refresh to check the node.'
        if capture.session_ended: status += ' This readback is from a previous connection; refresh after reconnecting.'
        self.filter_readback_note.set(status)
        position = self.filter_readback_text.yview()[0]
        self.filter_readback_text.configure(state='normal'); self.filter_readback_text.delete('1.0','end')
        self.filter_readback_text.insert('1.0',capture.text()); self.filter_readback_text.configure(state='disabled')
        self.filter_readback_text.yview_moveto(position)

    def interrupt_filter_readback(self):
        if self.filter_readback.state in ('queued','waiting','receiving'):self.filter_readback_unresolved=True
        self.filter_readback.interrupt(); self.filter_deadline = None
        self.render_filter_readback()

    def note_filter_change(self):
        if self.filter_readback.state in ('queued','waiting','receiving'):self.interrupt_filter_readback()
        elif self.filter_readback.state in ('complete','partial'):
            self.filter_readback.stale=True;self.render_filter_readback()

    def end_filter_session(self):
        self.interrupt_filter_readback()
        if self.filter_readback.state in ('complete','partial'):
            self.filter_readback.session_ended=True;self.render_filter_readback()

    def change_filter(self, action):
        try:
            if not self.connection or self.status.get() != 'Connected': raise OSError('Connect to a cluster first.')
            if self.dialect == 'CC Cluster':
                command = filter_command(self.dialect, action, self.keyword_text.get(), category=self.cc_filter_kind.get(),band=self.cc_filter_band.get())
            else:
                if action != 'clear' and self.spider_filter_kind.get() not in spider_categories(self.spider_stream.get()):
                    raise ValueError('Choose a filter supported for this traffic type.')
                expression = '' if action == 'clear' else spider_expression(self.spider_filter_kind.get(), self.spider_expression.get())
                command = filter_command(self.dialect, action, expression, int(self.spider_line.get()), stream=self.spider_stream.get())
            self.connection.send(command)
        except (OSError, ValueError) as exc:
            self.filter_note.set('Change not sent. Review the error and try again.')
            messagebox.showerror('Node filters', str(exc)); return
        self.note_filter_change()
        self.filter_note.set('Change requested. Show node filters to check the response.')

    def clear_spider_stream(self):
        connection=self.connection;generation=self.generation;stream=self.spider_stream.get()
        if not connection or self.status.get() != 'Connected' or self.dialect != 'DXSpider':
            messagebox.showerror('Node filters', 'Connect to a DXSpider node first.', parent=self);return
        if not messagebox.askyesno('Clear node filters', f'Clear every accept and reject filter line for {stream} on this node?', parent=self):return
        if connection is not self.connection or generation != self.generation or self.status.get() != 'Connected' or self.dialect != 'DXSpider' or stream != self.spider_stream.get():
            messagebox.showerror('Node filters', 'The connection or traffic type changed. Review the selection and try again.', parent=self);return
        try:
            connection.send(filter_command('DXSpider','clear_all',stream=stream))
        except (OSError,ValueError) as exc:
            self.filter_note.set('Clear not sent. Review the error and try again.')
            messagebox.showerror('Node filters',str(exc),parent=self);return
        self.note_filter_change()
        self.filter_note.set('Clear requested. Show node filters to check the response.')

    def reset_cc_filters(self):
        connection=self.connection;generation=self.generation
        if not connection or self.status.get()!='Connected' or self.dialect!='CC Cluster':
            messagebox.showerror('Node filters','Connect to a CC Cluster node first.',parent=self);return
        if not messagebox.askyesno('Reset node filters','Reset all filters on this node to its defaults?\n\nThis changes the node profile and cannot be undone automatically.',parent=self):return
        if connection is not self.connection or generation!=self.generation or self.status.get()!='Connected' or self.dialect!='CC Cluster':
            messagebox.showerror('Node filters','The connection changed. Review the selection and try again.',parent=self);return
        try:connection.send(filter_command('CC Cluster','reset_all'))
        except (OSError,ValueError) as exc:
            self.filter_note.set('Reset not sent. Review the error and try again.')
            messagebox.showerror('Node filters',str(exc),parent=self);return
        self.note_filter_change()
        self.filter_note.set('Reset requested. Show node filters to check the response.')

    def update_setting_actions(self):
        selected = self.settings_tree.selection()
        name = selected[0] if selected else ''
        try:
            setting_command(self.dialect, name, True)
            available = bool(self.connection and self.status.get() == 'Connected')
        except ValueError: available = False
        state = 'normal' if available else 'disabled'
        self.setting_enable.configure(state=state); self.setting_disable.configure(state=state)
        self.skimmer_button.configure(state='normal' if self.connection and self.status.get()=='Connected' and self.dialect=='DXSpider' else 'disabled')

    def open_skimmer_categories(self):
        if not self.connection or self.status.get()!='Connected' or self.dialect!='DXSpider':return
        connection=self.connection;generation=self.generation
        dialog=tk.Toplevel(self);dialog.title('Skimmer categories');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='DXSpider skimmer categories',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Applying a selection replaces all existing skimmer categories on the node.',style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(5,10))
        options=(('CW','CW'),('BEACON','Beacons'),('PSK','PSK / FSK / MSK'),('RTTY','RTTY'),('FT','FT4 / FT8'))
        chosen={}
        for token,label in options:
            chosen[token]=tk.BooleanVar(value=False)
            ttk.Checkbutton(panel,text=label,variable=chosen[token]).pack(anchor='w')
        ttk.Label(panel,text='Selections start empty. Refresh settings to inspect current values. Category flags can remain on while skimmer delivery is off.',style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(10,12))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def submit(action):
            try:
                if self.connection is not connection or self.generation!=generation or self.status.get()!='Connected' or self.dialect!='DXSpider':
                    raise OSError('The connection changed. Open this dialog again.')
                command=skimmer_command(action,[key for key,var in chosen.items() if var.get()])
                connection.send(command);self.interrupt_settings_readback()
            except (OSError,ValueError) as exc:messagebox.showerror('Skimmer categories',str(exc),parent=dialog);return
            self.settings_note.set('Skimmer change requested; refresh settings to check node values.')
            dialog.destroy()
        ttk.Button(buttons,text='Apply selected',style='Accent.TButton',command=lambda:submit('selected')).pack(side='left')
        ttk.Button(buttons,text='Enable all',command=lambda:submit('all')).pack(side='left',padx=8)
        ttk.Button(buttons,text='Disable',command=lambda:submit('off')).pack(side='left')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right',padx=(8,0))

    def change_setting(self, enabled):
        selected = self.settings_tree.selection()
        if not selected: return
        name = selected[0]
        try:
            if not self.connection or self.status.get() != 'Connected': raise OSError('Connect to a supported node first.')
            command = setting_command(self.dialect, name, enabled)
            self.connection.send(command)
        except (OSError, ValueError) as exc: messagebox.showerror('Node settings', str(exc)); return
        if self.dialect == 'DXSpider': self.interrupt_settings_readback()
        self.settings_note.set('Change requested; displayed values may be stale. ' + ('Refresh settings to check.' if self.dialect == 'CC Cluster' else 'Check the node response in Activity.'))

    def open_serial_logger(self):
        if self.serial_window is not None and self.serial_window.winfo_exists():self.serial_window.lift();return
        dialog=tk.Toplevel(self);self.serial_window=dialog;dialog.title('Serial logger');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Serial logger',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Connect one logging program using a COM port. Basic 8-N-1; no flow control.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        fields=ttk.Frame(panel);fields.pack(fill='x')
        ttk.Label(fields,text='COM PORT',style='Muted.TLabel').pack(side='left',padx=(0,6))
        port_entry=ttk.Combobox(fields,textvariable=self.serial_port,width=9);port_entry.pack(side='left')
        find=ttk.Button(fields,text='Find ports',command=self.find_serial_ports);find.pack(side='left',padx=(6,0))
        ttk.Label(fields,text='BAUD',style='Muted.TLabel').pack(side='left',padx=(12,6))
        baud_entry=ttk.Combobox(fields,textvariable=self.serial_baud,values=('1200','2400','4800','9600','19200','38400','57600','115200'),state='readonly',width=9);baud_entry.pack(side='left')
        options=ttk.Frame(panel);options.pack(fill='x',pady=(10,0))
        ignore=ttk.Checkbutton(options,text='Ignore BYE',variable=self.serial_ignore_bye);ignore.pack(side='left',padx=(0,12))
        spots=ttk.Checkbutton(options,text='Spots only (no node replies)',variable=self.serial_spots_only);spots.pack(side='left')
        ttk.Button(options,text='Node text…',command=lambda:self.open_logger_text_options('serial')).pack(side='left',padx=(12,0))
        ttk.Button(options,text='Spot bands…',command=lambda:self.open_logger_band_options('serial')).pack(side='left',padx=(8,0))
        echo=ttk.Checkbutton(panel,text='Echo received commands back to the serial logger',variable=self.serial_local_echo);echo.pack(anchor='w',pady=(7,0))
        ttk.Label(panel,textvariable=self.serial_status,style='Muted.TLabel').pack(anchor='w',pady=(10,4))
        ttk.Label(panel,text='Commands follow the destination selected on the Loggers tab. Stop closes the port.',style='Muted.TLabel',wraplength=500).pack(anchor='w',pady=(0,10))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        start=ttk.Button(buttons,text='Start serial',style='Accent.TButton',command=self.start_serial_logger);start.pack(side='left')
        stop=ttk.Button(buttons,text='Stop serial',command=self.stop_serial_logger);stop.pack(side='left',padx=8)
        def close():
            self.serial_window=None
            if self.serial_status.get()=='Finding ports…':self.serial_status.set('Stopped')
            dialog.destroy()
        ttk.Button(buttons,text='Close',command=close).pack(side='right');dialog.protocol('WM_DELETE_WINDOW',close)
        self.serial_widgets=(port_entry,baud_entry,start,stop,find,ignore,spots,echo);self.update_serial_widgets()

    def update_serial_widgets(self):
        if self.serial_window is None or not self.serial_window.winfo_exists():return
        port,baud,start,stop,find,ignore,spots,echo=self.serial_widgets;active=self.serial_logger is not None
        port.configure(state='disabled' if active else 'normal');baud.configure(state='disabled' if active else 'readonly')
        start.configure(state='disabled' if active else 'normal');stop.configure(state='normal' if active else 'disabled')
        find.configure(state='disabled' if active or self.serial_scan_pending else 'normal')
        for option in (ignore,spots,echo):option.configure(state='disabled' if active else 'normal')

    def find_serial_ports(self):
        if self.serial_scan_pending or self.serial_logger or self.serial_window is None:return
        window=self.serial_window;self.serial_scan_pending=True;self.serial_status.set('Finding ports…');self.update_serial_widgets()
        def scan():
            try:result=(list_serial_ports(),None)
            except Exception as exc:result=(None,str(exc))
            self.serial_scan_events.put_nowait((window,*result))
        threading.Thread(target=scan,daemon=True).start()

    def start_serial_logger(self):
        if self.serial_logger:return
        bridge=None;overflow=threading.Event();self.serial_overflow=overflow
        def emit(kind,value):
            target=self.logger_target if kind=='command' else None
            try:self.serial_events.put_nowait((bridge,kind,value,target))
            except queue.Full:
                overflow.set()
                if bridge:bridge.stop()
        try:bridge=SerialBridge(self.serial_port.get().strip(),int(self.serial_baud.get()),emit,ignore_bye=self.serial_ignore_bye.get(),spots_only=self.serial_spots_only.get(),text_categories=self.serial_text_categories,spot_bands=self.serial_spot_bands,local_echo=self.serial_local_echo.get())
        except ValueError as exc:messagebox.showerror('Serial logger',str(exc),parent=self.serial_window or self);return
        self.serial_logger=bridge;self.serial_status.set('Opening…');self.update_serial_widgets();bridge.start()

    def stop_serial_logger(self):
        if self.serial_logger:self.serial_logger.stop();self.serial_status.set('Stopping…')

    def poll_serial_logger(self):
        try:window,ports,error=self.serial_scan_events.get_nowait()
        except queue.Empty:pass
        else:
            self.serial_scan_pending=False
            if window is self.serial_window and window.winfo_exists():
                if ports is not None:self.serial_widgets[0].configure(values=ports)
                if not self.serial_logger:
                    self.serial_status.set('Could not find ports: '+error if error is not None else
                                           f'Found {len(ports)} port(s). Select one or enter it manually.' if ports else
                                           'No ports found. Enter a port manually.')
            self.update_serial_widgets()
        bridge=self.serial_logger
        if bridge and self.serial_overflow and self.serial_overflow.is_set():
            bridge.stop();self.serial_status.set('Serial event queue full; stopping')
        for _ in range(100):
            try:source,kind,value,target=self.serial_events.get_nowait()
            except queue.Empty:break
            if source is not self.serial_logger:continue
            if self.serial_overflow and self.serial_overflow.is_set() and kind!='stopped':continue
            if kind=='started':self.serial_status.set(f'{value[0]} · {value[1]} baud')
            elif kind=='error':self.serial_status.set('Serial error: '+value);self.append('\nSerial logger: '+value+'\n')
            elif kind=='command':
                try:
                    self.send_logger_command(target,value)
                except (OSError,ValueError,UnicodeError) as exc:source.reply(str(exc))
            elif kind=='stopped':
                self.serial_logger=None;self.serial_overflow=None
                if not self.serial_status.get().startswith(('Serial error:','Serial event queue full')):self.serial_status.set('Stopped')
                self.update_serial_widgets()
        if self.serial_logger and self.serial_overflow and self.serial_overflow.is_set() and not self.serial_logger.thread.is_alive():
            self.serial_logger=None;self.serial_overflow=None;self.update_serial_widgets()

    def open_logger_text_options(self, transport='telnet'):
        serial = transport == 'serial'
        parent = self.serial_window if serial and self.serial_window and self.serial_window.winfo_exists() else self
        active = lambda: self.serial_logger if serial else self.logger
        if active():
            messagebox.showerror('Logger node text','Stop this logger before changing node-text categories.',parent=parent);return
        dialog=tk.Toplevel(parent);dialog.title('Logger node text');dialog.transient(parent);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Node text for '+('serial' if serial else 'Telnet')+' loggers',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Recognized categories only. Other text includes command responses and unrecognized formats.',style='Muted.TLabel',wraplength=480).pack(anchor='w',pady=(5,10))
        categories = self.serial_text_categories if serial else self.logger_text_categories
        choices={}
        for key,label in (('announcements','Announcements'),('weather','Weather'),('talk','Direct messages'),('chat','Group chat'),('wwv','WWV reports'),('wcy','WCY reports'),('other','Other node text, including command responses')):
            choices[key]=tk.BooleanVar(master=dialog,value=key in categories)
            ttk.Checkbutton(panel,text=label,variable=choices[key]).pack(anchor='w',pady=2)
        ttk.Label(panel,text='Spots only overrides these choices. Telnet and serial settings are separate.',style='Muted.TLabel').pack(anchor='w',pady=(8,10))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def apply():
            if active():
                messagebox.showerror('Logger node text','Stop this logger before changing node-text categories.',parent=dialog);return
            selected={key for key,value in choices.items() if value.get()}
            if serial:self.serial_text_categories=selected
            else:self.logger_text_categories=selected
            self._save_settings();dialog.destroy()
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Apply',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))

    def open_logger_band_options(self, transport='telnet'):
        serial=transport=='serial'
        parent=self.serial_window if serial and self.serial_window and self.serial_window.winfo_exists() else self
        active=lambda:self.serial_logger if serial else self.logger
        if active():
            messagebox.showerror('Logger spot bands','Stop this logger before changing its bands.',parent=parent);return
        dialog=tk.Toplevel(parent);dialog.title('Logger spot bands');dialog.transient(parent);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Spot bands for '+('serial' if serial else 'Telnet')+' loggers',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Only selected bands are forwarded. Other includes unclassified frequencies.',style='Muted.TLabel',wraplength=480).pack(anchor='w',pady=(5,10))
        chosen=self.serial_spot_bands if serial else self.logger_spot_bands;variables={};grid=ttk.Frame(panel);grid.pack(fill='x')
        for index,name in enumerate([item[0] for item in BANDS]+['Other']):
            variables[name]=tk.BooleanVar(master=dialog,value=name in chosen)
            ttk.Checkbutton(grid,text=name,variable=variables[name]).grid(row=index//3,column=index%3,sticky='w',padx=(0,18),pady=2)
        ttk.Label(panel,text='Telnet and serial choices are separate; node delivery and the spot display are unchanged.',style='Muted.TLabel',wraplength=480).pack(anchor='w',pady=(10,8))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def apply():
            if active():
                messagebox.showerror('Logger spot bands','Stop this logger before changing its bands.',parent=dialog);return
            selected={name for name,value in variables.items() if value.get()}
            if serial:self.serial_spot_bands=selected
            else:self.logger_spot_bands=selected
            self._save_settings();dialog.destroy()
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Apply',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))

    def start_logger(self):
        try:
            port = int(self.logger_port.get())
            if not 1 <= port <= 65535: raise ValueError
        except ValueError:
            messagebox.showerror('Logging programs', 'Enter a port number from 1 to 65535.'); return
        if self.logger: return
        bridge = None
        overflow = threading.Event(); self.logger_overflow = overflow
        def emit(kind, value):
            target = self.logger_target if kind == 'command' else None
            try: self.logger_events.put_nowait((bridge, kind, value, target))
            except queue.Full:
                overflow.set()
                if bridge: bridge.stop()
        bridge = LoggerBridge(port, emit, ignore_bye=self.logger_ignore_bye.get(), spots_only=self.logger_spots_only.get(), text_categories=self.logger_text_categories, spot_bands=self.logger_spot_bands); self.logger = bridge
        self.logger_status.set('Starting…'); self.logger_start.configure(state='disabled'); self.logger_stop.configure(state='normal'); self.logger_ignore_check.configure(state='disabled'); self.logger_spots_check.configure(state='disabled'); self.logger_port_entry.configure(state='disabled')
        bridge.start()

    def stop_logger(self):
        if self.logger:
            self.logger.stop(); self.logger_status.set('Stopping…'); self.logger_stop.configure(state='disabled')

    def poll_logger(self):
        if self.logger and self.logger_overflow and self.logger_overflow.is_set():
            self.logger.stop()
            if not self.logger_status.get().startswith('Listener:'):
                self.logger_status.set('Listener: event queue full; stopping')
                self.append('\nLogging listener: event queue full; stopping.\n')
        for _ in range(100):
            try: bridge, kind, value, target = self.logger_events.get_nowait()
            except queue.Empty: break
            if bridge is not self.logger: continue
            if self.logger_overflow and self.logger_overflow.is_set() and kind != 'stopped': continue
            if kind == 'listening': self.logger_status.set(f'Listening on 127.0.0.1:{value} · 0 clients')
            elif kind == 'clients': self.logger_status.set(f'Listening on 127.0.0.1:{bridge.bound_port} · {value} clients')
            elif kind in ('error', 'warning'):
                self.append(f'\nLogging listener: {value}\n'); self.logger_status.set(f'Listener: {value}')
            elif kind == 'command':
                ident, command = value
                try:
                    self.send_logger_command(target,command)
                except (OSError, ValueError, UnicodeError) as exc: bridge.reply(ident, str(exc))
            elif kind == 'stopped':
                self.logger = None; self.logger_overflow = None; self.logger_start.configure(state='normal'); self.logger_stop.configure(state='disabled'); self.logger_ignore_check.configure(state='normal'); self.logger_spots_check.configure(state='normal'); self.logger_port_entry.configure(state='normal')
                if not self.logger_status.get().startswith('Listener:'): self.logger_status.set('Stopped')
        if self.logger and self.logger_overflow and self.logger_overflow.is_set() and not self.logger.thread.is_alive():
            self.logger = None; self.logger_overflow = None
            self.logger_start.configure(state='normal'); self.logger_stop.configure(state='disabled'); self.logger_ignore_check.configure(state='normal'); self.logger_spots_check.configure(state='normal'); self.logger_port_entry.configure(state='normal')

    def render_settings(self):
        selection = self.settings_tree.selection()
        position = self.settings_tree.yview()[0]
        self.settings_tree.delete(*self.settings_tree.get_children())
        names = [name for name in SETTING_COMMANDS if name in self.node_settings]
        names += [name for name in self.node_settings if name not in SETTING_COMMANDS]
        for name in names:
            self.settings_tree.insert('', 'end', iid=name, values=(SETTING_LABELS.get(name, name), self.node_settings[name]))
        if selection and self.settings_tree.exists(selection[0]): self.settings_tree.selection_set(selection[0])
        self.settings_tree.yview_moveto(position)
        self.update_setting_actions()

    def clear_spots(self):
        self.rows.clear(); self.spot_times.clear(); self.spot_sources.clear(); self.render_spots()
        self.clear_spot_recovery_notice()

    def open_clear_spots_menu(self,event):
        self.clear_spots_menu.entryconfigure(0,state='normal' if self.selected_bands else 'disabled')
        x=event.x_root if getattr(event,'num',None)==3 else self.clear_spots_button.winfo_rootx()
        y=event.y_root if getattr(event,'num',None)==3 else self.clear_spots_button.winfo_rooty()+self.clear_spots_button.winfo_height()
        try:self.clear_spots_menu.tk_popup(x,y)
        finally:self.clear_spots_menu.grab_release()
        return 'break'

    def clear_selected_band_spots(self):
        if not self.selected_bands:return
        retained=deque(maxlen=self.rows.maxlen)
        for ident,spot in self.rows:
            if (band_for(spot.frequency) or 'Other') in self.selected_bands:
                self.spot_times.pop(ident,None);self.spot_sources.pop(ident,None)
            else:retained.append((ident,spot))
        self.rows=retained;self.render_spots()

    def show_spot_recovery_notice(self):
        self.spot_recovery_notice = True
        self.update_spot_recovery_display()

    def clear_spot_recovery_notice(self):
        self.spot_recovery_notice = False
        self.update_spot_recovery_display()

    def update_spot_recovery_display(self):
        delivery='Spot or command' if self.manual_uncertain_notice and self.spot_uncertain_notice else 'Spot' if self.spot_uncertain_notice else 'Command'
        uncertain=self.manual_uncertain_notice or self.spot_uncertain_notice
        notice=(f'{delivery} delivery unknown; see Activity. History may have gaps or duplicates.' if uncertain and self.spot_recovery_notice else
                f'{delivery} delivery unknown; see Activity before retrying.' if uncertain else
                'Recent history after reconnect may contain duplicates or leave gaps.')
        active=uncertain or self.spot_recovery_notice
        self.spot_footer_hint.configure(text=notice if active else '')
        if active and not getattr(self,'_compact_layout',False):
            self.spot_footer_hint.pack(anchor='w',pady=(4,0))
        else:
            self.spot_footer_hint.pack_forget()
        if active and getattr(self,'_compact_layout',False):
            self.spot_source_label.configure(textvariable='',text=notice)
        else:
            self.spot_source_label.configure(textvariable=self.spot_source_note)

    def hidden_bands_label(self):
        count=len(self.hidden_bands)
        label = f'{count} band{"s" if count != 1 else ""} hidden' if count else 'No bands hidden by options'
        selected=getattr(self,'selected_bands',None)
        if selected is not None and selected & self.hidden_bands:
            label = (f'Showing {next(iter(selected))} · {count} hidden in All' if len(selected)==1 else f'Showing selected bands · {count} hidden in All')
        if self.spot_marker_filter != 'All spots':label+=f' · {self.spot_marker_filter}'
        if any(part.strip() for value in self.spot_hidden_text.values() for part in value.split(',')):label+=' · text hidden'
        if self.display_unsaved:label='not saved · '+label
        return label

    def open_display_bands(self):
        dialog=tk.Toplevel(self);dialog.title('Spot display options');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=20);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Hide bands from the spot view',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='These choices affect this display only. Node delivery, logs and alarms are unchanged.',style='Muted.TLabel').pack(anchor='w',pady=(5,12))
        grid=ttk.Frame(panel);grid.pack(fill='x')
        variables={}
        for index,(name,_,_) in enumerate(BANDS):
            var=tk.BooleanVar(master=dialog,value=name in self.hidden_bands);variables[name]=var
            ttk.Checkbutton(grid,text=name,variable=var).grid(row=index//4,column=index%4,sticky='w',padx=(0,20),pady=4)
        use_color=tk.BooleanVar(master=dialog,value=self.color_bands)
        ttk.Checkbutton(panel,text='Use light band colors in the spot list',variable=use_color).pack(anchor='w',pady=(10,0))
        hide_duplicates=tk.BooleanVar(master=dialog,value=self.hide_duplicate_spots)
        ttk.Checkbutton(panel,text='Hide identical spots in the spot list',variable=hide_duplicates).pack(anchor='w',pady=(4,0))
        ttk.Label(panel,text='SPOTTER MARKER',style='Muted.TLabel').pack(anchor='w',pady=(10,3))
        marker=tk.StringVar(master=dialog,value=self.spot_marker_filter)
        ttk.Combobox(panel,textvariable=marker,values=('All spots','Marked -# only','Unmarked only'),state='readonly',width=22).pack(anchor='w')
        ttk.Label(panel,text='-# commonly marks skimmers; unmarked does not necessarily mean human.',style='Muted.TLabel').pack(anchor='w',pady=(4,0))
        hidden_text_draft=dict(self.spot_hidden_text)
        ttk.Button(panel,text='Hide text…',command=lambda:self.open_hidden_spot_text(dialog,hidden_text_draft)).pack(anchor='w',pady=(8,0))
        buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=(14,0))
        def reset():
            for var in variables.values():var.set(False)
        def apply():
            self.hidden_bands={name for name,var in variables.items() if var.get()}
            self.color_bands=use_color.get()
            self.hide_duplicate_spots=hide_duplicates.get()
            self.spot_marker_filter=marker.get()
            self.spot_hidden_text=dict(hidden_text_draft)
            self.hidden_bands_note.set(self.hidden_bands_label());self.render_spots()
            self._last_display_saved=None;saved=self._save_settings()
            if saved is False or (saved is True and self._last_display_saved is False):
                self.display_unsaved=True;self.hidden_bands_note.set(self.hidden_bands_label())
                if saved is True:self.append('\nSpot display settings differ from the saved file; reopen the app to review them.\n')
                messagebox.showwarning('Spot display options','Applied for this session, but settings could not be saved. See Activity.',parent=dialog)
            dialog.destroy()
        ttk.Button(buttons,text='Show all bands',command=reset).pack(side='left')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Apply',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))

    def open_hidden_spot_text(self,parent,draft):
        dialog=tk.Toplevel(parent);dialog.title('Hide spot text');dialog.transient(parent);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Hide matching spots',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Enter text to hide, separated by commas. Matching ignores case.\nUse Apply in Display options to save all changes.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        values={}
        for field,label in (('dx','DX CALL'),('spotter','SPOTTER'),('comment','COMMENT')):
            values[field]=tk.StringVar(master=dialog,value=draft[field])
            ttk.Label(panel,text=label,style='Muted.TLabel').pack(anchor='w')
            ttk.Entry(panel,textvariable=values[field],width=48).pack(fill='x',pady=(3,8))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def apply():
            result={field:var.get().strip() for field,var in values.items()}
            if any(len(value)>200 or any(ord(c)<32 or ord(c)==127 for c in value) for value in result.values()):
                messagebox.showerror('Hide spot text','Use up to 200 characters per field without control characters.',parent=dialog);return
            draft.update(result)
            dialog.destroy()
        def clear_fields():
            for var in values.values():var.set('')
        ttk.Button(buttons,text='Clear fields',command=clear_fields).pack(side='left')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        ttk.Button(buttons,text='Use text',style='Accent.TButton',command=apply).pack(side='right',padx=(0,8))

    def any_node_connected(self):
        return self.status.get() == 'Connected' or any(item['status'] == 'Connected' for item in self.secondary_nodes.values())

    def remember_connected_source(self, source):
        if source in self.recent_connected_sources:self.recent_connected_sources.remove(source)
        self.recent_connected_sources.append(source)

    def refresh_spot_source_filter(self):
        sources={source for source in self.spot_sources.values() if source}
        if self.connection_target:
            host,port,call=self.connection_target;sources.add((host.casefold(),port,call))
        sources.update(item['source'] for item in self.secondary_nodes.values())
        targets={self.source_label(source):source for source in sources}
        if any(source is None for source in self.spot_sources.values()):targets['Unknown source']=None
        current=self.spot_source_filter.get()
        if current != 'All sources' and current not in targets and current in self.spot_source_filter_targets:
            targets[current]=self.spot_source_filter_targets[current]
        self.spot_source_filter_targets=targets
        self.spot_source_box.configure(values=['All sources',*sorted(targets,key=str.casefold)])

    def update_spot_source(self):
        if hasattr(self, 'spot_source_box'): self.refresh_spot_source_filter()
        if hasattr(self, 'spot_empty_hint') and not self.rows:
            self.spot_empty_hint.set('Waiting for spots. If none arrive, check node filters and spot settings.' if self.any_node_connected() else 'Connect to a cluster to start receiving spots.')
        selected = self.spot_source_filter.get()
        if selected != 'All sources' and selected in self.spot_source_filter_targets:
            source = self.spot_source_filter_targets[selected]
            if source is None:
                self.spot_source_note.set('Showing spots with unknown source.'); return
            if source[:2] == ('archive',0):
                self.spot_source_note.set(f'Showing saved recording · {source[2]}'); return
            target = self.connection_target
            main_source = (target[0].casefold(),target[1],target[2]) if target else None
            connected = bool(self.connection and self.status.get() == 'Connected' and main_source == source)
            connected = connected or any(item['source'] == source and item['status'] == 'Connected' and item['connection'] for item in self.secondary_nodes.values())
            status = 'connected' if connected else 'not connected'
            self.spot_source_note.set(f'Showing {self.source_label(source)} · {status}'); return
        sources={value for value in self.spot_sources.values() if value}
        if len(sources)>1:
            self.spot_source_note.set('Spots from multiple connections · select a spot to see its source'); return
        if len(sources)==1 and self.spot_source not in sources:
            source=next(iter(sources))
            if source[:2] == ('archive',0):self.spot_source_note.set(f'Imported recording · {source[2]}'); return
            host,port,call=source
            self.spot_source_note.set(f'Spots received from {host}:{port} as {call}'); return
        if not self.spot_source:
            self.spot_source_note.set('Waiting for spots from connected nodes.' if self.any_node_connected() else 'Connect to a node to receive spots.'); return
        host, port, call = self.spot_source
        target=self.connection_target
        main_source=(target[0].casefold(),target[1],target[2]) if target else None
        connected=bool(self.connection and self.status.get()=='Connected' and main_source==self.spot_source)
        connected=connected or any(item['source']==self.spot_source and item['status']=='Connected' and item['connection'] for item in self.secondary_nodes.values())
        status = 'connected' if connected else 'not connected'
        self.spot_source_note.set(f'Spots from {host}:{port} as {call} · {status}')

    def logger_route_changed(self, *_):
        self.logger_route_epoch+=1;self.refresh_logger_target()

    def refresh_logger_target(self):
        self.logger_target=(self.logger_route_epoch,'main',self.generation,self.connection) if self.status.get()=='Connected' and self.connection else None

    def send_logger_command(self,target,command):
        if not target or target != self.logger_target or target[0]!=self.logger_route_epoch:
            raise OSError('The selected connection changed or was not connected; command not sent.')
        if target[1]!='main' or target[2]!=self.generation or target[3] is not self.connection or self.status.get()!='Connected':raise OSError('Cluster connection changed; command not sent.')
        self.connection.send(command)
        if self.dialect=='CC Cluster':self.settings_note.set('A logger command was sent; refresh settings to check current values.')

    def refresh_status_indicator(self):
        status = self.status.get()
        self.refresh_logger_target()
        color = ('#65dc8a' if self.dark_theme.get() else '#187c42') if status == 'Connected' else '#bd783e' if 'error' in status.lower() else self.theme_muted
        self.status_dot.configure(fg=color)
        self.update_reply_action(); self.refresh_report_targets()
        if hasattr(self, 'spot_empty_hint') and not self.rows:
            self.spot_empty_hint.set('Waiting for spots. If none arrive, check node filters and spot settings.' if self.any_node_connected() else 'Connect to a cluster to start receiving spots.')

    def insert_spot_row(self, spot, source, unknown_date=False):
        self.next_spot_id += 1
        if len(self.rows) == self.rows.maxlen:
            old_id = self.rows[0][0]; self.spot_times.pop(old_id, None); self.spot_sources.pop(old_id, None)
        ident = f'spot-{self.next_spot_id}'
        self.spot_times[ident] = datetime.min if unknown_date else spot_order(spot)
        self.spot_sources[ident] = source
        self.rows.append((ident, spot))
        return ident

    def add_spot(self, spot, source=None):
        source = self.spot_source if source is None else source
        ident=self.insert_spot_row(spot,source)
        if self.spot_log: self.spot_log.publish(spot, source)
        if self.alarm_rules and matches(self.alarm_rules, spot, self.country_index):
            if len(self.alarm_rows) == self.alarm_rows.maxlen:
                old_row=self.alarm_rows[0];self.alarm_tree.delete(old_row);self.alarm_details.pop(old_row,None)
            row = self.alarm_tree.insert('', 0, values=(spot_time_label(spot), spot.dx, format_spot_frequency(spot.frequency), spot.dx_state, spot.spotter, spot.comment))
            self.alarm_rows.append(row);self.alarm_details[row]=(ident,spot,source);self.mark_unread('alarms')
            self.update_alarm_note()
            if self.alarm_config['auto_open']: self.notebook.select(self.alarm_page)
            now = time.monotonic()
            if self.alarm_config['sound'] and now - self.last_alarm_sound >= 5:
                self.bell(); self.last_alarm_sound = now

    def open_alarm_band_picker(self):
        dialog=tk.Toplevel(self);dialog.title('Alarm bands');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=16);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Alarm frequency ranges',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Choose full display bands. Existing custom ranges are kept; edit them in the Alarms tab.',style='Muted.TLabel',wraplength=490).pack(anchor='w',pady=(5,10))
        ranges={name:f'{low}-{high}' for name,low,high in BANDS}
        existing=[part for part in re.split(r'[\s,]+',self.alarm_ranges.get().strip()) if part]
        custom=[part for part in existing if part not in ranges.values()]
        grid=ttk.Frame(panel);grid.pack(anchor='w');choices={}
        for index,(name,_,_) in enumerate(BANDS):
            choices[name]=tk.BooleanVar(master=dialog,value=ranges[name] in existing)
            ttk.Checkbutton(grid,text=name,variable=choices[name]).grid(row=index//4,column=index%4,sticky='w',padx=(0,18),pady=3)
        ttk.Label(panel,text='Use selection fills the ranges field; Apply on the Alarms tab saves the rule.',style='Muted.TLabel',wraplength=490).pack(anchor='w',pady=(10,8))
        buttons=ttk.Frame(panel);buttons.pack(fill='x')
        def use_selection():
            self.alarm_ranges.set(', '.join(custom+[ranges[name] for name,_,_ in BANDS if choices[name].get()]));dialog.destroy()
        ttk.Button(buttons,text='Use selection',style='Accent.TButton',command=use_selection).pack(side='left')
        ttk.Button(buttons,text='Cancel',command=dialog.destroy).pack(side='right')
        dialog.grab_set()

    def apply_alarm(self):
        config = {'calls': self.alarm_calls.get().strip(), 'states': self.alarm_states.get().strip(),
                  'ranges': self.alarm_ranges.get().strip(), 'cq_zones': self.alarm_cq.get().strip(), 'countries': self.alarm_countries.get().strip(), 'enabled': self.alarm_enabled.get(), 'sound': self.alarm_sound.get(), 'auto_open': self.alarm_auto_open.get(), 'country_prefix_mode':self.alarm_country_prefix_mode.get()}
        try:
            rules = compile_rules(config['calls'], config['states'], config['ranges'], config['cq_zones'], config['countries'],config['country_prefix_mode']) if config['enabled'] else None
            if not config['enabled'] and any(config[k] for k in ('calls', 'states', 'ranges', 'cq_zones', 'countries')):
                compile_rules(config['calls'], config['states'], config['ranges'], config['cq_zones'], config['countries'],config['country_prefix_mode'])
            if rules and rules.country_prefixes:
                if not self.country_index:raise ValueError('Load a country file in Settings before enabling country-prefix alarms.')
                if any(not resolve_country_prefix(self.country_index,prefix) for prefix in rules.country_prefixes):raise ValueError('A country prefix was not recognized in the loaded country file.')
        except ValueError as exc:
            messagebox.showerror('Spot alarms', str(exc), parent=self); return
        self.alarm_config = config; self.alarm_rules = rules; self.alarm_load_error = ''
        self._last_alarm_saved=None
        saved=self._save_settings()
        if saved is False or (saved is True and self._last_alarm_saved is False):
            self.alarm_unsaved=True
            if saved is True:self.append('\nAlarm settings differ from the saved file; reopen the app to review them.\n')
            messagebox.showwarning('Spot alarms','The alarm settings apply to this session, but could not be saved. See Activity for details.',parent=self)
        self.update_alarm_note()

    def update_alarm_note(self):
        if self.alarm_unsaved:status='Not saved · see Activity'
        elif self.alarm_load_error:status='Disabled · review saved alarm'
        elif not self.alarm_rules:status='Disabled.'
        elif self.alarm_rules.country_prefixes and not self.country_index:status='Country file required for *prefix.'
        elif self.alarm_rules.country_prefixes and any(not resolve_country_prefix(self.country_index,prefix) for prefix in self.alarm_rules.country_prefixes):status='A country prefix is not recognized.'
        else:status=f'{len(self.alarm_rows)} matching spots' if self.alarm_rows else 'Enabled.'
        self.alarm_note.set(status)

    def clear_alarms(self):
        self.alarm_tree.delete(*self.alarm_tree.get_children()); self.alarm_rows.clear(); self.alarm_details.clear(); self.clear_unread('alarms')
        self.update_alarm_note()

    def selected_alarm(self):
        selection=self.alarm_tree.selection()
        return self.alarm_details.get(selection[0]) if selection else None

    def show_alarm_spot(self):
        selected=self.selected_alarm()
        if not selected:
            messagebox.showinfo('Alarm match','Select an alarm match first.',parent=self);return
        ident,_,_=selected
        if not self.spots.exists(ident):
            messagebox.showinfo('Alarm match','This spot is no longer in the spot list or is hidden by display filters.',parent=self);return
        self.notebook.select(0);self.spots.selection_set(ident);self.spots.see(ident);self.show_spot_detail()

    def open_alarm_detail(self):
        selected=self.selected_alarm()
        if not selected:
            messagebox.showinfo('Alarm match','Select an alarm match first.',parent=self);return
        _,spot,source=selected
        dialog=tk.Toplevel(self);dialog.title('Alarm match');dialog.transient(self);dialog.configure(bg=self.theme_bg)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Alarm match',style='Heading.TLabel').pack(anchor='w')
        lines=[f'DX: {spot.dx}  ·  {format_spot_frequency(spot.frequency)} kHz',f'Spotter: {spot.spotter}  ·  {spot.date} {spot_time_label(spot)}'.strip(),f'Comment: {spot.comment}']
        if source:
            lines.append(f'Received from: {source[0]}:{source[1]} as {source[2]}')
        else:lines.append('Receiving connection unavailable')
        for label,country,dxcc,state,grid,cq,itu in (('DX',spot.dx_country,spot.dx_dxcc,spot.dx_state,spot.dx_locator,spot.dx_cq,spot.dx_itu),('Spotter',spot.spotter_country,spot.spotter_dxcc,spot.spotter_state,spot.spotter_locator,spot.spotter_cq,spot.spotter_itu)):
            parts=[value for value in (country,f'Node country {dxcc}' if dxcc else '',state,grid,f'CQ {cq}' if cq else '',f'ITU {itu}' if itu else '') if value]
            if parts:lines.append(label+': '+' · '.join(parts))
        if spot.origin:lines.append('Origin: '+spot.origin)
        output=tk.Text(panel,width=70,height=10,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',padx=10,pady=8,font=('Segoe UI',10))
        output.pack(fill='both',expand=True,pady=(8,10));output.insert('1.0','\n'.join(lines));output.configure(state='disabled')
        ttk.Button(panel,text='Close',command=dialog.destroy).pack(anchor='e')

    def start_spot_log(self):
        if self.spot_log: return
        path = filedialog.asksaveasfilename(parent=self, title='Create a new spot log', defaultextension='.csv', filetypes=[('CSV files', '*.csv')])
        if not path: return
        try: self.spot_log = SpotLog(path)
        except OSError as exc:
            messagebox.showerror('Cannot start recording', f'Choose a new file. Existing files are not overwritten.\n\n{exc}', parent=self)
            return
        self.spot_log_visible_path=self.spot_log.current_path
        self.spot_log_status.set(f'Recording to {path}')
        self.log_start.configure(state='disabled'); self.log_resume.configure(state='disabled'); self.log_stop.configure(state='normal')

    def resume_spot_log(self):
        if self.spot_log: return
        path = filedialog.askopenfilename(parent=self, title='Resume a Cluster Desk by N2WQ recording', filetypes=[('CSV files', '*.csv')])
        if not path: return
        try: self.spot_log = SpotLog(path, append=True)
        except (OSError, ValueError, UnicodeError, csv.Error) as exc:
            messagebox.showerror('Cannot resume recording', str(exc), parent=self); return
        self.spot_log_visible_path=self.spot_log.current_path
        self.spot_log_status.set(f'Recording to {path}')
        self.log_start.configure(state='disabled'); self.log_resume.configure(state='disabled'); self.log_stop.configure(state='normal')

    def open_spot_archive_parts(self):
        paths=filedialog.askopenfilenames(parent=self,title='Select recording parts in order',filetypes=[('Cluster Desk by N2WQ CSV','*.csv')])
        if paths:self.open_spot_archive(paths)

    def open_spot_archive(self, paths=None):
        if paths is None:
            path = filedialog.askopenfilename(parent=self, title='Open a spot recording', filetypes=[('Spot recordings', '*.csv *.txt'), ('Cluster Desk CSV', '*.csv'), ('VE7CC text logs', '*.txt')])
            if not path:return
            paths=(path,)
        else:paths=tuple(paths)
        if not 1 <= len(paths) <= 50:
            messagebox.showerror('Spot recording','Select between 1 and 50 recording files.',parent=self);return
        path=paths[0];multiple=len(paths)>1
        original_log = not multiple and Path(path).suffix.casefold() == '.txt'
        dialog = tk.Toplevel(self); dialog.title('Spot recording'); dialog.transient(self); dialog.configure(bg=self.theme_bg)
        panel = ttk.Frame(dialog, padding=16); panel.pack(fill='both', expand=True)
        ttk.Label(panel, text='Spot recording', style='Heading.TLabel').pack(anchor='w')
        note = tk.StringVar(value='Loading…'); ttk.Label(panel, textvariable=note, style='Muted.TLabel').pack(anchor='w', pady=(5, 8))
        tools=ttk.Frame(panel);tools.pack(fill='x',pady=(0,8))
        ttk.Label(tools,text='SEARCH',style='Muted.TLabel').pack(side='left',padx=(0,6))
        search=tk.StringVar();ttk.Entry(tools,textvariable=search,width=30).pack(side='left')
        band=tk.StringVar(value='All bands')
        ttk.Combobox(tools,textvariable=band,values=['All bands']+[item[0] for item in BANDS]+['Other'],state='readonly',width=12).pack(side='left',padx=8)
        date_tools=ttk.Frame(panel);date_tools.pack(fill='x',pady=(0,8))
        ttk.Label(date_tools,text='SPOT DATE (UTC)',style='Muted.TLabel').pack(side='left',padx=(0,6))
        date_start=tk.StringVar();date_end=tk.StringVar()
        ttk.Entry(date_tools,textvariable=date_start,width=12).pack(side='left')
        ttk.Label(date_tools,text='to',style='Muted.TLabel').pack(side='left',padx=6)
        ttk.Entry(date_tools,textvariable=date_end,width=12).pack(side='left')
        ttk.Label(date_tools,text='YYYY-MM-DD; blank for any date',style='Muted.TLabel').pack(side='left',padx=8)
        frame=ttk.Frame(panel); frame.pack(fill='both', expand=True)
        tree=ttk.Treeview(frame, columns=('date','time','frequency','dx','spotter','comment'), show='headings', height=12)
        for col, label, width in [('date','DATE',110),('time','UTC',70),('frequency','kHz',90),('dx','DX CALL',110),('spotter','SPOTTER',110),('comment','COMMENT',280)]:
            tree.heading(col,text=label,anchor='e' if col=='frequency' else 'center'); tree.column(col,width=width,minwidth=55,stretch=col=='comment',anchor='e' if col=='frequency' else 'center')
        scroll=ttk.Scrollbar(frame, orient='vertical', command=tree.yview); scroll.pack(side='right',fill='y')
        tree.pack(side='left',fill='both',expand=True); tree.configure(yscrollcommand=scroll.set)
        detail=tk.Text(panel,height=4,wrap='word',background=self.theme_surface,foreground=self.theme_ink,relief='flat',padx=8,pady=6,font=('Segoe UI',9),state='disabled')
        detail.pack(fill='x',pady=(8,0))
        archive_hint=('VE7CC log: dates unavailable; % escapes are preserved as written.' if original_log else 'Files are searched in selection order; later files appear first. Select a row to see its filename.' if multiple else 'Opening the recording does not replay spots or trigger alarms.')
        ttk.Label(panel,text=archive_hint,style='Muted.TLabel').pack(anchor='w',pady=(7,0))
        rows=[]; active={'cancel':None,'timer':None,'generation':0,'jobs':set()}
        def later(delay,callback):
            def run():
                active['jobs'].discard(job)
                callback()
            job=self.after(delay,run);active['jobs'].add(job);return job
        def cancel_later(job):
            if job in active['jobs']:
                self.after_cancel(job);active['jobs'].discard(job)
        def stop_search():
            active['generation']+=1
            if active['cancel']:active['cancel'].set()
            for job in tuple(active['jobs']):cancel_later(job)
            active['timer']=None
        actions=ttk.Frame(panel);actions.pack(fill='x',pady=(8,0))
        def load_shown():
            try:
                pending=[(spot_from_archive(row),str(row.get('recording_file') or path)) for row in rows]
                if not pending:raise ValueError('There are no displayed records to load.')
            except (ValueError,TypeError) as exc:messagebox.showerror('Load saved spots',str(exc),parent=dialog);return
            if not messagebox.askyesno('Load saved spots',f'Add {len(pending)} displayed saved spots to the spot list?\n\nThe list holds 1,000 spots; older entries may be removed. Saved spots are not sent to loggers, recorded again, or checked by alarms.',parent=dialog):return
            for spot,filename in pending:self.insert_spot_row(spot,('archive',0,filename),unknown_date=not bool(spot.date))
            self.render_spots();self.update_spot_source();self.notebook.select(0)
            note.set(f'Loaded {len(pending)} saved spots into the spot list.')
        load_button=ttk.Button(actions,text='Load shown into spots…',command=load_shown,state='disabled');load_button.pack(side='left')
        ttk.Button(actions,text='Close',command=dialog.destroy).pack(side='right')
        def close():
            dialog.destroy()
        dialog.protocol('WM_DELETE_WINDOW',close)
        dialog.bind('<Destroy>', lambda event: stop_search() if event.widget is dialog else None)
        def select(_event=None):
            selection=tree.selection(); record=rows[int(selection[0])] if selection else None
            detail.configure(state='normal'); detail.delete('1.0','end')
            if record:
                def shown_value(name,value):
                    return format_spot_frequency(value) if name=='frequency_khz' else format_utc_time(value) if name=='time_utc' else value
                detail.insert('1.0','\n'.join(f'{name}: {shown_value(name,value)}' for name,value in record.items() if value))
            detail.configure(state='disabled')
        tree.bind('<<TreeviewSelect>>',select)
        def start_search():
            active['timer']=None;active['generation']+=1;generation=active['generation']
            if active['cancel']: active['cancel'].set()
            cancelled=threading.Event();active['cancel']=cancelled;results=queue.Queue(maxsize=1)
            query=search.get().strip().casefold();selected_band=band.get()
            try: first,last=date_range(date_start.get(),date_end.get())
            except ValueError as exc:
                note.set(str(exc)); return
            note.set('Searching recording…');load_button.configure(state='disabled')
            def predicate(row):
                return record_in_date_range(row,first,last) and (selected_band == 'All bands' or (band_for(row['frequency_khz']) or 'Other') == selected_band) and (not query or any(query in value.casefold() for value in (*row.values(),format_spot_frequency(row['frequency_khz']),format_utc_time(row['time_utc']))))
            def work():
                try:
                    result=search_recordings(paths,cancelled,predicate) if multiple else (search_ve7cc_archive if original_log else search_archive)(path,cancelled,predicate)
                    results.put(('ok',result))
                except (OSError,UnicodeError,ValueError,csv.Error) as exc: results.put(('error',str(exc)))
            def finish():
                if generation != active['generation'] or not dialog.winfo_exists(): return
                try: kind,value=results.get_nowait()
                except queue.Empty: later(100,finish);return
                if kind == 'error': note.set('Could not open recording: '+value);return
                if value is None: return
                total, matched, loaded=value;rows[:]=loaded;tree.delete(*tree.get_children());select()
                for index in range(len(rows)-1,-1,-1):
                    row=rows[index];tree.insert('', 'end', iid=str(index), values=(row['date'],format_utc_time(row['time_utc']),format_spot_frequency(row['frequency_khz']),row['dx_call'],row['spotter'],row['comment']))
                load_button.configure(state='normal' if rows else 'disabled')
                name=f'{len(paths)} files' if multiple else Path(path).name
                order='later files first' if multiple else 'newest recorded first'
                note.set(f'{name} · {len(rows)} shown of {matched} matches in {total} records · {order}')
            threading.Thread(target=work,daemon=True).start();later(100,finish)
        def schedule(*_):
            if active['cancel']: active['cancel'].set()
            active['generation']+=1
            if active['timer'] is not None: cancel_later(active['timer'])
            rows.clear();tree.delete(*tree.get_children());select()
            note.set('Searching recording…');load_button.configure(state='disabled')
            active['timer']=later(300,start_search)
        search.trace_add('write',schedule);band.trace_add('write',schedule);date_start.trace_add('write',schedule);date_end.trace_add('write',schedule);start_search()

    def stop_spot_log(self):
        if self.spot_log:
            self.spot_log.stop(); self.log_stop.configure(state='disabled')
            self.spot_log_status.set('Finishing recording…')

    def poll_spot_log(self):
        if self.spot_log and self.spot_log.current_path != self.spot_log_visible_path:
            self.spot_log_visible_path=self.spot_log.current_path
            if not self.spot_log.stopping:self.spot_log_status.set(f'Recording to {self.spot_log_visible_path}')
            self.append(f'\nSpot recording continued in {self.spot_log_visible_path}.\n')
        if self.spot_log and not self.spot_log.thread.is_alive():
            error = self.spot_log.error
            self.spot_log = None
            self.log_start.configure(state='normal'); self.log_resume.configure(state='normal'); self.log_stop.configure(state='disabled')
            self.spot_log_status.set(error or 'Recording stopped')
            if error: self.append('\n' + error + '\n')

    def sort_spots(self, column):
        if column not in self.spot_headings: return
        if column == self.spot_sort_column: self.spot_sort_descending = not self.spot_sort_descending
        else: self.spot_sort_column = column; self.spot_sort_descending = column == 'time'
        self.render_spots()

    def reset_spot_filters(self):
        self.search.set('');self.search_field.set('All fields');self.band.set('All bands')
        self.spot_source_filter.set('All sources');self.render_spots()

    def spot_search_changed(self, *_):
        self.cancel_pending_spot_searches()
        self.spot_search_note.set('Type to filter; press Enter to search nodes. Rapid repeats may disconnect.')
        self.render_spots()

    def spot_search_source_changed(self, *_):
        self.cancel_pending_spot_searches()
        self.spot_search_note.set('Press Enter to search the selected connected sources.')
        self.update_spot_source()

    def cancel_pending_spot_searches(self):
        for request in self.pending_spot_searches.values():
            self.after_cancel(request['after'])
        if self.pending_spot_searches and not self.closing:
            self.append('\nPending node searches canceled because the search changed.\n')
        self.pending_spot_searches.clear();self.spot_search_context=None

    def spot_search_status(self):
        context=self.spot_search_context
        if not context:return
        sent=context['sent'];waiting=len(self.pending_spot_searches)
        if waiting:self.spot_search_note.set(f'{sent} node(s) queued · {waiting} waiting for node limit. See Activity.')
        elif context['failed']:self.spot_search_note.set(f'{sent} node(s) queued; some searches failed. See Activity.')
        elif context['recent']:self.spot_search_note.set(f'{sent} node(s) queued · some matches limited to recent node-filtered spots.')
        else:self.spot_search_note.set(f'{sent} node(s) queued · results may arrive later.')

    def retry_spot_search(self, source, request):
        if self.pending_spot_searches.get(source) is not request:return
        connection=request['connection']
        current=(self.connection is connection and self.status.get()=='Connected' and self.spot_source==source and self.dialect==request['dialect'])
        current=current or any(item['connection'] is connection and item['status']=='Connected' and item['source']==source and item['dialect']==request['dialect'] for item in self.secondary_nodes.values())
        if not current:
            reason='connection changed before the delayed search could be sent'
        elif time.monotonic() >= request['deadline']:
            reason='node command limit did not clear in time'
        else:
            try:connection.send_batch(request['commands'])
            except CommandRateLimitError:
                request['after']=self.after(1000,lambda:self.retry_spot_search(source,request));return
            except (OSError,ValueError,UnicodeError) as exc:reason=str(exc)
            else:
                del self.pending_spot_searches[source];self.spot_search_context['sent']+=1
                self.append(f'\nDelayed search queued for {self.source_label(source)}; results may arrive later.\n')
                self.spot_search_status();return
        del self.pending_spot_searches[source];self.spot_search_context['failed'].append(f'{self.source_label(source)}: {reason}')
        self.append(f'\nDelayed search not sent for {self.source_label(source)}: {reason}.\n')
        self.spot_search_status()

    def search_connected_nodes(self, event=None):
        self.cancel_pending_spot_searches()
        query=self.search.get().strip()
        if not query:
            self.spot_search_note.set('Enter a search term before requesting node history.')
            return 'break'
        selected=self.spot_source_filter.get()
        source=None if selected=='All sources' else self.spot_source_filter_targets.get(selected)
        targets=[]
        if self.connection and self.status.get()=='Connected' and self.dialect in ('CC Cluster','DXSpider'):
            targets.append((self.spot_source,self.connection,self.dialect))
        for item in self.secondary_nodes.values():
            if item['connection'] and item['status']=='Connected' and item['dialect'] in ('CC Cluster','DXSpider'):
                targets.append((item['source'],item['connection'],item['dialect']))
        if selected!='All sources':
            targets=[target for target in targets if target[0]==source and source is not None]
        if not targets:
            self.spot_search_note.set('No supported connected node for this source; searching the local list only.')
            return 'break'
        field=self.search_field.get();sent=0;failed=[];recent=False;delayed=[]
        for node_source,connection,dialect in targets:
            commands=[]
            if field=='All fields' and dialect=='DXSpider':
                try:commands.append(spider_history_text_command(field,query))
                except ValueError:pass
            elif field in ('All fields','DX call') and dialect=='CC Cluster' and not any(c in query for c in '*?'):
                try:commands.append(history_command(dialect,query,count=100))
                except ValueError:pass
            if field=='All fields' and dialect=='CC Cluster':
                try:commands.append(cc_history_comment_command(query))
                except ValueError:pass
            if field in ('All fields','Spotter') and dialect=='CC Cluster':
                try:commands.append(cc_history_spotter_command(query))
                except ValueError:pass
            if dialect=='DXSpider' and field in ('All fields','DX country','Spotter country'):
                for country_field in (('DX country','Spotter country') if field=='All fields' else (field,)):
                    try:commands.append(spider_history_country_command(country_field,query))
                    except ValueError:pass
            if field in ('All fields','Frequency'):
                try:commands.append(frequency_history_command(dialect,query))
                except ValueError:pass
            band_query=search_band_label(query) if field=='All fields' else None
            if band_query:
                try:commands.append(history_command(dialect,band_query,mode='Band',count=100))
                except ValueError:pass
            if field in ('DX call','Spotter','Comment','Origin') and dialect=='DXSpider':
                try:commands.append(spider_history_text_command(field,query))
                except ValueError:commands.append('sh/mydx/100');recent=True
            elif field=='Comment' and dialect=='CC Cluster':
                try:commands.append(cc_history_comment_command(query))
                except ValueError:commands.append('sh/mydx/100');recent=True
            elif field=='DX call' and dialect=='CC Cluster':
                commands.append('sh/mydx/100');recent=True
            elif field!='DX call':commands.append('sh/mydx/100');recent=True
            if not commands:
                failed.append(f'{self.source_label(node_source)}: use a valid callsign or prefix without wildcards')
                continue
            try:
                connection.send_batch(commands);sent+=1
            except CommandRateLimitError:
                request={'connection':connection,'dialect':dialect,'commands':tuple(commands),'deadline':time.monotonic()+30,'after':None}
                self.pending_spot_searches[node_source]=request;delayed.append(node_source)
            except (OSError,ValueError,UnicodeError) as exc:failed.append(f'{self.source_label(node_source)}: {exc}')
        note=f'History commands queued for {sent} node(s); delivery is not guaranteed. Results may arrive later and duplicate spots.'
        if recent:note+=' Some matches are limited to up to 100 recent node-filtered spots per node; older or filtered spots may be missing.'
        if failed:note+=' Not requested or incomplete: '+'; '.join(failed)
        if delayed:note+=' Waiting to retry the node command limit: '+', '.join(self.source_label(source) for source in delayed)+'.'
        self.spot_search_context={'sent':sent,'failed':failed,'recent':recent}
        for source in delayed:
            request=self.pending_spot_searches[source]
            request['after']=self.after(1000,lambda source=source,request=request:self.retry_spot_search(source,request))
        self.spot_search_status()
        self.append('\n'+note+'\n')
        return 'break'

    def render_spots(self, preserve_anchor=False):
        selection = self.spots.selection(); position = self.spots.yview()[0]
        current = self.spots.get_children(); anchor = ''
        if preserve_anchor and current and (position > 0 or self.spot_view_locked.get()):
            if self.spots.winfo_ismapped():
                for y in range(min(self.spots.winfo_height(), 120)):
                    anchor = self.spots.identify_row(y)
                    if anchor: break
            else:
                # Treeview cannot identify pixels on a hidden tab. Its yview
                # fraction still identifies the first row in the old list.
                index = min(len(current)-1, max(0, round(position * len(current))))
                anchor = current[index]
        self.refresh_spot_source_filter()
        source_label=self.spot_source_filter.get(); source_filtered=source_label != 'All sources'
        selected_source=self.spot_source_filter_targets.get(source_label)
        counts = {name: 0 for name in self.band_shortcut_buttons}
        counts['All bands'] = len(self.rows)
        for _, spot in self.rows:
            band_name = band_for(spot.frequency)
            if band_name in counts: counts[band_name] += 1
        for name, count in counts.items():
            self.band_shortcut_counts[name].configure(text=str(count))
        search = self.search.get().strip().casefold(); search_field=self.search_field.get(); band = self.band.get(); shown=0
        search_band=search_band_label(search) if search_field=='All fields' else None
        hidden_text={field:[part.strip().casefold() for part in value.split(',') if part.strip()] for field,value in self.spot_hidden_text.items()}
        desired=[]
        selected_spot=next((spot for ident,spot in self.rows if selection and ident == selection[0] and (not source_filtered or self.spot_sources.get(ident)==selected_source)),None)
        seen=set()
        column=self.spot_sort_column
        for name,label in self.spot_headings.items():
            if name=='dx' and (self.lotw_index or self.eqsl_index):label='DX  (+ LoTW / * AG)'
            self.spots.heading(name,text=label+('  ↓' if self.spot_sort_descending else '  ↑') if name==column else label)
        def sort_key(item):
            ident,spot=item
            if column == 'time': return self.spot_times[ident]
            if column == 'frequency':
                try: return float(spot.frequency)
                except ValueError: return -1.0
            if column == 'band':
                name=band_for(spot.frequency)
                return next((index for index, item in enumerate(BANDS) if item[0] == name), len(BANDS))
            return getattr(spot,column).casefold()
        for ident, spot in sorted(reversed(self.rows), key=sort_key, reverse=self.spot_sort_descending):
            if source_filtered and self.spot_sources.get(ident) != selected_source: continue
            if any(term in getattr(spot,field).casefold() for field,terms in hidden_text.items() for term in terms):continue
            marked = spot.spotter.upper().endswith('-#')
            if self.spot_marker_filter == 'Marked -# only' and not marked: continue
            if self.spot_marker_filter == 'Unmarked only' and marked: continue
            b = band_for(spot.frequency)
            if self.selected_bands is None:
                if b in self.hidden_bands: continue
            elif (b or 'Other') not in self.selected_bands: continue
            frequency_label = format_spot_frequency(spot.frequency)
            search_values = {'DX call': (spot.dx,), 'Spotter': (spot.spotter,), 'Comment': (spot.comment,),
                             'Frequency': (spot.frequency,frequency_label), 'DX country': (spot.dx_country,spot.dx_dxcc),
                             'Spotter country': (spot.spotter_country,spot.spotter_dxcc),
                             'DX grid': (spot.dx_locator,), 'Spotter grid': (spot.spotter_locator,), 'Origin': (spot.origin,)}
            searchable = search_values.get(search_field, (spot.dx, spot.spotter, spot.comment, spot.frequency, frequency_label, spot_day_time(spot), spot.date, spot.time, b or 'Other', spot.dx_country, spot.dx_dxcc, spot.spotter_country, spot.spotter_dxcc, spot.dx_locator, spot.spotter_locator, spot.origin))
            if search and search_band != b and not any(search in value.casefold() for value in searchable): continue
            if self.hide_duplicate_spots:
                if (selection and spot == selected_spot and ident != selection[0]) or spot in seen: continue
                seen.add(spot)
            tags=('band-'+b,) if self.color_bands and b in BAND_COLORS else ('alternate',) if shown % 2 else ()
            dx_label=spot.dx+' +' if self.lotw_index and spot.dx.isascii() and spot.dx.upper() in self.lotw_index else spot.dx
            if self.eqsl_index and spot.dx.isascii() and spot.dx.upper() in self.eqsl_index.calls:dx_label+= '*' if dx_label.endswith('+') else ' *'
            if (self.spot_sources.get(ident) or ())[:2]==('archive',0):dx_label+=' [saved]'
            desired.append((ident,(spot_day_time(spot), frequency_label, b or 'Other', dx_label, spot.spotter, spot.comment),tags)); shown += 1
        desired_ids={item[0] for item in desired}; current_ids=set(current)
        incremental = preserve_anchor and [ident for ident in current if ident in desired_ids] == [ident for ident,_,_ in desired if ident in current_ids]
        if incremental:
            obsolete=[ident for ident in current if ident not in desired_ids]
            if obsolete:self.spots.delete(*obsolete)
            for index,(ident,values,tags) in enumerate(desired):
                if ident not in current_ids:self.spots.insert('',index,iid=ident,values=values,tags=tags)
                elif self._spot_render_tags.get(ident) != tags:self.spots.item(ident,tags=tags)
        else:
            self.spots.delete(*current)
            for ident,values,tags in desired:self.spots.insert('', 'end', iid=ident, values=values, tags=tags)
        self._spot_render_tags={ident:tags for ident,_,tags in desired}
        if selection and self.spots.exists(selection[0]) and self.spots.selection() != selection:self.spots.selection_set(selection[0])
        if anchor and self.spots.exists(anchor) and shown:
            self.spots.yview_moveto(self.spots.index(anchor) / shown)
        else:
            self.spots.yview_moveto(position)
        self.show_spot_detail()
        count=len(self.rows)
        if self.rows.maxlen is not None and count==self.rows.maxlen:
            self.spot_count.set(f'{shown} shown · list full')
        else:
            self.spot_count.set(f'{shown} shown · {count} in list')
        if shown:
            self.reset_spot_filters_button.pack_forget()
            self.empty_display_options_button.pack_forget()
            self.empty_spot_actions.pack_forget()
            self.spot_empty.place_forget()
        else:
            self.spot_empty_title.set('No matching spots' if self.rows else 'Your spot stream is ready')
            active=[]
            if search: active.append('search')
            if self.selected_bands is not None: active.append('bands')
            if source_filtered: active.append('source')
            if self.spot_marker_filter != 'All spots': active.append('spotter marker')
            if any(hidden_text.values()):active.append('hidden text')
            if self.hidden_bands and band == 'All bands': active.append('hidden bands')
            if self.rows:
                hint = 'Band counts include all retained spots.'
                hint += ' Active filters: ' + ', '.join(active) + '.' if active else ' Choose another band to see other spots.'
            else:
                hint = 'Waiting for spots. If none arrive, check node filters and spot settings.' if self.any_node_connected() else 'Connect to a cluster to start receiving spots.'
            self.spot_empty_hint.set(hint)
            if self.rows:
                self.empty_spot_actions.pack(pady=(6,0))
                self.reset_spot_filters_button.pack(side='left')
                if self.spot_marker_filter != 'All spots' or any(hidden_text.values()) or (self.hidden_bands and band == 'All bands'):self.empty_display_options_button.pack(side='left',padx=(6,0))
                else:self.empty_display_options_button.pack_forget()
            else:
                self.reset_spot_filters_button.pack_forget();self.empty_display_options_button.pack_forget();self.empty_spot_actions.pack_forget()
            self.spot_empty.place(relx=.5, rely=.5, anchor='center')

    def copy_selected_spot(self,field):
        selection=self.spots.selection()
        spot=next((spot for ident,spot in self.rows if selection and ident==selection[0]),None)
        if spot is None or field not in ('dx','frequency','spotter'):return 'break'
        try:self.clipboard_clear();self.clipboard_append(getattr(spot,field))
        except tk.TclError:messagebox.showerror('Copy spot','Could not access the clipboard.',parent=self)
        return 'break'

    def open_spot_copy_menu(self,event):
        row=self.spots.identify_row(event.y)
        if not row:return 'break'
        self.spots.selection_set(row);self.spots.focus(row);self.show_spot_detail()
        try:self.spot_copy_menu.tk_popup(event.x_root,event.y_root)
        finally:self.spot_copy_menu.grab_release()
        return 'break'

    def open_qrz_selector(self):
        selection=self.spots.selection()
        spot=next((spot for ident,spot in self.rows if selection and ident==selection[0]),None)
        if spot is None:
            messagebox.showinfo('QRZ lookup','Select a spot first.',parent=self);return
        dialog=tk.Toplevel(self);dialog.title('QRZ lookup');dialog.transient(self);dialog.configure(bg=self.theme_bg);dialog.resizable(False,False)
        panel=ttk.Frame(dialog,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='Open on QRZ',style='Heading.TLabel').pack(anchor='w')
        ttk.Label(panel,text='Choose a callsign to open in your web browser.',style='Muted.TLabel').pack(anchor='w',pady=(5,10))
        def open_call(call):
            if self.open_qrz_call(call,parent=dialog):dialog.destroy()
        ttk.Button(panel,text=f'DX: {spot.dx[:40]}',command=lambda:open_call(spot.dx)).pack(fill='x',pady=(0,6))
        ttk.Button(panel,text=f'Spotter: {spot.spotter[:40]}',command=lambda:open_call(spot.spotter)).pack(fill='x')
        ttk.Button(panel,text='Cancel',command=dialog.destroy).pack(anchor='e',pady=(10,0))

    def open_qrz_call(self, call, parent=None):
        try:
            url=qrz_url(call)
            if not webbrowser.open_new_tab(url):raise OSError('The web browser could not be opened.')
        except (ValueError,OSError,webbrowser.Error) as exc:
            messagebox.showerror('QRZ lookup',str(exc),parent=parent or self);return False
        return True

    def open_qrz_from_spot_event(self, event):
        row=self.spots.identify_row(event.y);column=self.spots.identify_column(event.x)
        if not row or column not in ('#4','#5'):return None
        spot=next((spot for ident,spot in self.rows if ident==row),None)
        if spot is None:return None
        self.spots.selection_set(row);self.open_qrz_call(spot.dx if column=='#4' else spot.spotter)
        return 'break'

    def show_spot_detail(self):
        selection = self.spots.selection()
        spot = next((spot for ident, spot in self.rows if selection and ident == selection[0]), None)
        text = 'Select a spot to see its details.'
        if spot is not None:
            lines = [f'{spot.dx} · {format_spot_frequency(spot.frequency)} kHz · spotted by {spot.spotter} · {spot.date} {spot_time_label(spot)}', spot.comment]
            for label, country, dxcc, state, locator, cq, itu in (
                ('DX', spot.dx_country, spot.dx_dxcc, spot.dx_state, spot.dx_locator, spot.dx_cq, spot.dx_itu),
                ('Spotter', spot.spotter_country, spot.spotter_dxcc, spot.spotter_state, spot.spotter_locator, spot.spotter_cq, spot.spotter_itu)):
                parts = [value for value in (country, f'Node country {dxcc}' if dxcc else '', state, locator,
                         f'CQ {cq}' if cq else '', f'ITU {itu}' if itu else '') if value]
                if parts: lines.append(f'{label}: ' + ' · '.join(parts))
            if spot.origin: lines.append(f'Origin: {spot.origin}')
            if self.lotw_index and spot.dx.isascii():
                last_upload=self.lotw_index.get(spot.dx.upper())
                if last_upload:lines.append(f'LoTW activity: last upload {last_upload.replace("T", " ")} UTC · not a QSO confirmation')
            if self.eqsl_index and spot.dx.isascii() and spot.dx.upper() in self.eqsl_index.calls:
                last_upload=self.eqsl_index.calls[spot.dx.upper()]
                lines.append(f'eQSL AG: last upload/import {last_upload if last_upload != "0000-00-00" else "date unavailable"} · list {self.eqsl_index.list_time} · not a QSO confirmation')
            source = self.spot_sources.get(selection[0])
            if source:
                if source[:2]==('archive',0):lines.append(f'Imported from recording: {source[2]}')
                else:
                    host, port, login = source
                    lines.append(f'Received from: {host}:{port} as {login}')
            if self.country_index:
                for label,call,node_country in (('DX',spot.dx,spot.dx_country),('Spotter',spot.spotter,spot.spotter_country)):
                    if not node_country:
                        estimate=self.country_index.lookup(call)
                        if estimate:
                            lines.append(f'Local CTY estimate ({label}): {estimate.name} · {estimate.prefix} · CQ {estimate.cq} · ITU {estimate.itu}')
            if self.local_grid.get() and spot.dx_locator:
                try:
                    km,bearing=grid_path(self.local_grid.get(),spot.dx_locator)
                    direction=f'{round(bearing)%360:03d}° true' if bearing is not None else 'bearing unavailable'
                    lines.append(f'Grid-center estimate: {direction} · {km:.0f} km / {km*0.621371:.0f} mi')
                except ValueError: pass
            text = '\n'.join(lines)
        selected_id=selection[0] if spot is not None else None
        same_selection=selected_id == self._detail_selected
        if same_selection and self.spot_detail.get('1.0','end-1c') == text:return
        position=self.spot_detail.yview()[0] if same_selection else 0.0
        self.spot_detail.configure(state='normal'); self.spot_detail.delete('1.0','end')
        self.spot_detail.insert('1.0',text)
        if spot is not None:self.spot_detail.tag_add('callsign','1.0',f'1.0 + {len(spot.dx)} chars')
        else:self.spot_detail.tag_add('placeholder','1.0','end-1c')
        self.spot_detail.configure(state='disabled');self._detail_selected=selected_id
        self.spot_detail.yview_moveto(position)

    def append(self, text):
        follow = self.transcript.yview()[1] >= .999
        if not follow:
            self.transcript.mark_set('activity_view_anchor', '@0,0')
            self.transcript.mark_gravity('activity_view_anchor', 'left')
        self.transcript.configure(state='normal'); self.transcript.insert('end',text)
        if int(self.transcript.index('end-1c').split('.')[0]) > 3000: self.transcript.delete('1.0','1001.0')
        chars = self.transcript.count('1.0', 'end-1c', 'chars')[0]
        if chars > 1000000: self.transcript.delete('1.0', f'1.0 + {chars-900000} chars')
        if follow:self.transcript.see('end')
        else:
            self.transcript.yview('activity_view_anchor')
            self.transcript.mark_unset('activity_view_anchor')
        self.transcript.configure(state='disabled')

    def poll(self):
        if self.connection_overflow and self.connection_overflow.is_set():
            self.command_history.clear();self.command_history_index=None;self.command_history_draft=''
            self.interrupt_manual_commands()
            self.interrupt_spot_submissions()
            self.close_password_dialog()
            self.interrupt_chat_readback()
            self.interrupt_settings_readback()
            self.interrupt_station_for(None)
            self.interrupt_mail()
            self.end_filter_session()
            if self.mail_compose_pending:
                self.append('\nMail: Connection closed; delivery may be unknown.\n')
                self.mail_compose_pending = False
            self.connection_overflow = None; self.generation += 1
            if self.connection: self.connection.close(); self.connection = None
            self.last_error = 'Incoming data exceeded the application queue; the connection was closed.'
            self.append('\nConnection error: ' + self.last_error + '\n')
            self.status.set('Connection error — see Activity')
            self.update_spot_source()
            self.send_btn.configure(state='disabled')
            self.refresh_report_targets()
            self.update_report_actions()
            for button in self.directory_buttons: button.configure(state='disabled')
            self.directory_disconnected()
            self.settings_refresh.configure(state='disabled')
            self.set_connection_button(False)
            if self.node_settings: self.settings_note.set('Last read from the previous connection; reconnect to refresh.')
            if self.reports: self.report_note.set('Main node disconnected; additional nodes may still deliver reports.')
            if self.announcements: self.ann_note.set('Main node disconnected; additional nodes may still deliver messages.')
            self.update_setting_actions(); self.update_filter_view()
            if self.want_connection and self.auto_reconnect.get(): self._schedule_reconnect()
            else:
                self.want_connection = False; self.connection_target = None
                for entry in self.connection_entries:entry.configure(state='normal')
        changed = False; settings_changed = False; reports_changed = False; announcements_changed = False; directory_changed = False; filter_changed = False; mail_changed = False; mail_read_changed = False; station_changed = False
        for _ in range(20):
            try: generation,kind,value=self.events.get_nowait()
            except queue.Empty: break
            if generation != self.generation: continue
            if kind == 'status':
                self.status.set(value)
                self.update_spot_source()
                self.send_btn.configure(state='normal' if value == 'Connected' else 'disabled')
                self.refresh_report_targets()
                self.update_report_actions()
                for button in self.directory_buttons: button.configure(state='normal' if value == 'Connected' else 'disabled')
                if value == 'Disconnected':
                    self.command_history.clear();self.command_history_index=None;self.command_history_draft=''
                    self.interrupt_manual_commands()
                    self.interrupt_spot_submissions()
                    self.close_password_dialog()
                    self.interrupt_chat_readback()
                    self.interrupt_settings_readback()
                    self.interrupt_station_for(None)
                    self.interrupt_mail()
                    self.end_filter_session()
                    self.connection=None; self.set_connection_button(False); self.settings_refresh.configure(state='disabled')
                    if self.node_settings: self.settings_note.set('Last read from the previous connection; reconnect to refresh.')
                    if self.reports: self.report_note.set('Main node disconnected; additional nodes may still deliver reports.')
                    if self.announcements: self.ann_note.set('Main node disconnected; additional nodes may still deliver messages.')
                    self.directory_disconnected()
                    if self.last_error: self.status.set('Connection error — see Activity')
                    if self.want_connection and self.auto_reconnect.get(): self._schedule_reconnect()
                    else:
                        self.want_connection=False; self.connection_target=None
                        for entry in self.connection_entries:entry.configure(state='normal')
                elif value == 'Connected' and self.dialect in ('CC Cluster', 'DXSpider'):
                    self.settings_refresh.configure(state='normal')
                if value == 'Connected':
                    self.connected_since=time.monotonic()
                    if self.retry_attempt:
                        self.append('\nConnected after retry. Recent spot history was requested; it may contain duplicates or leave gaps.\n')
                        self.show_spot_recovery_notice()
                    elif self.manual_reconnect:
                        self.append('\nReconnected. Recent spot history was requested; it may contain duplicates or leave gaps.\n')
                        self.show_spot_recovery_notice()
                    self.manual_reconnect = False
                    if self.connection_target:
                        host,port,call=self.connection_target
                        self.remember_connected_source((host.casefold(),port,call))
                    self.retry_attempt=0
                    self.report_note.set('Connected. Request WWV or WCY to load reports.')
                    self.ann_note.set('Connected. Request announcements or wait for new ones.')
                    self.directory_note.set('Connected. Choose a list to request.')
                self.update_setting_actions()
                self.update_filter_view()
            elif kind == 'dialect':
                self.dialect = value
                if value == 'DXSpider':
                    self.node_settings = {name:'Unknown' for name in (*SPIDER_SETTING_COMMANDS,*RBN_FIELDS)}; settings_changed = True
                    self.settings_note.set('DXSpider: values are unknown until the node reports a change.')
                else: self.settings_note.set('Read from the connected CC Cluster node.')
            elif kind == 'password_required': self.ask_password()
            elif kind == 'mail_status':
                self.append('\nMail: '+value+'\n')
                if value in ('Node reports the message saved.', 'Node reports the message accepted.', 'Node rejected mail: registration required.', 'Node rejected mail: no valid recipient.', 'Node response ended; message acceptance is unknown.', 'Mail exchange ended; delivery may be unknown.'):
                    self.mail_compose_pending = False
            elif kind == 'error': self.last_error=value; self.append('\nConnection error: '+value+'\n')
            elif kind == 'notice': self.append('\n'+value+'\n')
            elif kind == 'sent':
                self.append('\n← '+value+'\n')
                self.pending_manual_commands.pop(id(value),None)
                self.pending_spot_submissions.pop(id(value),None)
                if self.filter_readback.state in ('queued','waiting','receiving'):
                    if value is self.filter_readback.command and self.filter_readback.state == 'queued':
                        self.filter_readback.sent(); self.filter_deadline=time.monotonic()+15; filter_changed = True
                    elif self.filter_readback.state == 'queued' and value.lower() in ('sh/filter','sh/filter bandtime'):
                        self.filter_readback.ambiguous=True; self.filter_readback_unresolved=True; filter_changed=True
                    elif self.filter_readback.state in ('waiting','receiving'):self.interrupt_filter_readback()
                elif self.filter_readback.state in ('complete','partial') and is_filter_change(value,self.dialect):self.note_filter_change()
                if self.mail_directory.state in ('queued','waiting','receiving'):
                    if self.mail_directory.state == 'queued' and value.lower() == self.mail_directory.command.lower():
                        self.mail_directory.sent(); mail_changed = True
                    else:self.interrupt_mail()
                if self.mail_read and self.mail_read.state in ('queued','waiting','receiving'):
                    if self.mail_read.state == 'queued' and value.upper() == f'READ {self.mail_read.number}':
                        self.mail_read.sent(); mail_read_changed = True
                    else:self.interrupt_mail()
                if self.station_capture and self.station_target and self.station_target[0] is None and self.station_capture.state in ('queued','waiting','receiving'):
                    if self.station_capture.state == 'queued' and value.lower() == self.station_capture.command.lower():
                        self.station_capture.sent();station_changed=True
                    else:self.interrupt_station()
                if self.chat_readback and self.chat_readback.state in ('queued','waiting','receiving'):
                    if self.chat_readback.state == 'queued' and value.lower() == 'stat/user':self.chat_readback.sent()
                    else:self.interrupt_chat_readback()
                if self.settings_readback and self.settings_readback.state in ('queued','waiting','receiving'):
                    if self.settings_readback.state == 'queued' and value.lower() == 'stat/user':self.settings_readback.sent()
                    else:self.interrupt_settings_readback()
            elif kind == 'data':
                self.append(value); self.partial += value
                lines=self.partial.split('\n'); self.partial=lines.pop()
                discarded = 0
                if self.discarding_long_line:
                    if lines:
                        lines.pop(0); self.discarding_long_line=False
                    else:
                        self.partial=''
                parts=[]
                for line in lines:
                    if cluster_line_too_long(line):
                        discarded+=1; parts.append(None)
                    else: parts.extend(split_prompt_prefix(line))
                if cluster_line_too_long(self.partial):
                    self.partial=''; self.discarding_long_line=True; discarded+=1; parts.append(None)
                if discarded:
                    self.append(f'\nSkipped {discarded} overlong cluster line(s); Activity may be truncated.\n')
                for line in parts:
                    if line is None:
                        self.interrupt_chat_readback(); self.interrupt_settings_readback()
                        self.interrupt_station_for(None); self.interrupt_mail(); self.interrupt_filter_readback()
                        if self.directory.active:
                            self.directory.active=False; self.directory.complete=False
                            self.directory_note.set('Partial result: a cluster line was too long.')
                            directory_changed=True
                        self.report_collector=ReportCollector()
                        continue
                    if self.settings_readback and self.settings_readback.feed(line) and self.settings_readback.state in ('complete', 'partial'): self.finish_settings_readback()
                    if self.chat_readback and self.chat_readback.feed(line) and self.chat_readback.state in ('complete','partial'):
                        self.chat_readback_deadline=None;self.update_chat_readback()
                    if self.station_capture and self.station_target and self.station_target[0] is None and self.station_capture.feed(line):
                        station_changed=True
                        if self.station_capture.state=='complete': self.station_deadline=None
                    if self.mail_read and self.mail_read.feed(line):
                        mail_read_changed = True
                        if self.mail_read.state == 'complete': self.mail_read_deadline = None
                    if self.mail_directory.feed(line):
                        mail_changed=True
                        if self.mail_directory.state == 'complete': self.mail_deadline=None
                    if self.filter_readback.feed(line):
                        filter_changed = True
                        if self.filter_readback.state in ('complete','partial'): self.filter_deadline = None
                    if self.directory.feed(line):
                        directory_changed = True
                        if self.directory.truncated: self.directory_note.set('Result exceeds the view limit; remaining lines are omitted.')
                        elif self.directory.complete: self.directory_note.set('Node response received; it may include live traffic.')
                        else: self.directory_note.set('Receiving node response…')
                    if self.dialect == 'DXSpider':
                        group_change=parse_group_change(line)
                        if group_change:
                            if self.chat_readback and self.chat_readback.state in ('queued','waiting','receiving'):self.interrupt_chat_readback()
                            action,group=group_change
                            if action == 'leaving': self.chat_groups.discard(group)
                            elif len(self.chat_groups)<100: self.chat_groups.add(group)
                    announcement = parse_announcement(line)
                    message_category = ('weather' if is_weather(announcement) else 'history' if SPIDER_HISTORY.match(announcement) else 'announcements') if announcement else None
                    if not announcement and self.dialect in ('DXSpider','CC Cluster') and self.connection_target:
                        announcement = parse_talk(line, self.connection_target[2])
                        if announcement: message_category='talk'
                        elif self.dialect == 'DXSpider':
                            announcement = parse_chat(line, self.connection_target[2],self.chat_groups)
                            if announcement: message_category='chat'
                    if announcement:
                        self.announcements.append((announcement,self.spot_source,message_category)); announcements_changed = True;self.mark_unread('messages')
                        if message_category=='talk' and self.auto_open_talk.get():self.show_talk_alert(announcement,self.spot_source)
                        self.ann_note.set('Received from the connected node.')
                    report = self.report_collector.feed(line)
                    if report:
                        self.reports.append((*report,self.spot_source)); reports_changed = True;self.mark_unread('reports')
                        self.report_note.set('Received from the connected node.')
                    spot=parse_spot(line.rstrip('\r'), self.dialect)
                    if spot:
                        self.add_spot(spot); changed=True
                        if self.logger: self.logger.publish(spot)
                        if self.serial_logger:self.serial_logger.publish(spot)
                    elif self.logger_route.get()=='Main node':
                        category = ('announcements' if message_category == 'history' else message_category) or (report[0].lower() if report else 'other')
                        for bridge in (self.logger,self.serial_logger):
                            if bridge:
                                if category == 'other':bridge.publish_text(line.rstrip('\r'))
                                else:bridge.publish_text(line.rstrip('\r'),category=category)
                    if self.dialect == 'CC Cluster':
                        setting = parse_setting(line.rstrip('\r'))
                        if setting:
                            name, setting_value = setting
                            self.node_settings[name] = setting_value; settings_changed = True
                    elif self.dialect == 'DXSpider' and self.connection_target:
                        setting = parse_spider_setting(line.rstrip('\r'), self.connection_target[2])
                        if setting:
                            overlapping = bool(self.settings_readback and self.settings_readback.state in ('queued','waiting','receiving'))
                            self.interrupt_settings_readback()
                            name, setting_value = setting
                            self.node_settings[name] = setting_value; settings_changed = True
                            self.settings_note.set('Settings refresh interrupted by a setting response; refresh again.' if overlapping else 'Values reflect recognized node responses; other values remain unknown.')
                if NODE_PROMPT.fullmatch(self.partial):
                    prompt = self.partial; self.partial = ''
                    if self.logger_route.get()=='Main node':
                        if self.logger:self.logger.publish_text(prompt)
                        if self.serial_logger:self.serial_logger.publish_text(prompt)
                    if self.settings_readback and self.settings_readback.feed(prompt) and self.settings_readback.state in ('complete', 'partial'): self.finish_settings_readback()
                    if self.chat_readback and self.chat_readback.feed(prompt) and self.chat_readback.state in ('complete','partial'):
                        self.chat_readback_deadline=None;self.update_chat_readback()
                    if self.station_capture and self.station_target and self.station_target[0] is None and self.station_capture.feed(prompt):
                        station_changed=True
                        if self.station_capture.state=='complete': self.station_deadline=None
                    if self.mail_read and self.mail_read.feed(prompt):
                        mail_read_changed = True
                        if self.mail_read.state == 'complete': self.mail_read_deadline = None
                    if self.mail_directory.feed(prompt):
                        mail_changed = True
                        if self.mail_directory.state == 'complete': self.mail_deadline = None
                    if self.filter_readback.feed(prompt):
                        filter_changed = True
                        if self.filter_readback.state in ('complete','partial'): self.filter_deadline = None
                    if self.directory.feed(prompt):
                        directory_changed = True
                        if self.directory.complete: self.directory_note.set('Node response received; it may include live traffic.')
        if changed: self.render_spots(preserve_anchor=True)
        if settings_changed: self.render_settings()
        if reports_changed: self.render_reports()
        if announcements_changed: self.render_announcements()
        if directory_changed: self.render_directory()
        if self.filter_deadline is not None and time.monotonic() >= self.filter_deadline:
            self.interrupt_filter_readback()
        elif filter_changed: self.render_filter_readback()
        if self.mail_deadline is not None and time.monotonic() >= self.mail_deadline: self.interrupt_mail()
        elif mail_changed: self.render_mail()
        if self.mail_read_deadline is not None and time.monotonic() >= self.mail_read_deadline:
            self.mail_read.interrupt(); self.mail_read_deadline=None; mail_read_changed=True
        if mail_read_changed: self.render_mail_reader()
        if self.station_deadline is not None and time.monotonic() >= self.station_deadline:
            self.interrupt_station()
        elif station_changed: self.render_station()
        if self.chat_readback_deadline is not None and time.monotonic() >= self.chat_readback_deadline:self.interrupt_chat_readback()
        if self.settings_deadline is not None and time.monotonic() >= self.settings_deadline:self.interrupt_settings_readback()
        self.poll_secondary_nodes()
        self.poll_logger()
        self.poll_serial_logger()
        self.poll_country()
        self.poll_lotw()
        self.poll_eqsl()
        self.poll_spot_log()
        delay=1 if not self.events.empty() or not self.secondary_events.empty() else 50
        self.poll_after = self.after(delay,self.poll)

    def quit_app(self):
        if self.closing: return
        self.closing = True
        self.cancel_pending_spot_searches()
        if self.secondary_start_after is not None:self.after_cancel(self.secondary_start_after);self.secondary_start_after=None
        if self._unread_pending is not None:
            self.after_cancel(self._unread_pending);self._unread_pending=None
        for ident in list(self.secondary_nodes):self.stop_secondary_node(ident)
        self.disconnect()
        self.after_cancel(self.poll_after)
        if self.logger:
            self.logger.stop(); self.logger.thread.join(timeout=1)
        if self.serial_logger:
            self.serial_logger.stop();self.serial_logger.thread.join(timeout=2)
        if self.spot_log:
            self.spot_log.stop(); self.spot_log_status.set('Finishing recording…')
            self.after(50, self._finish_quit)
        else: self.destroy()

    def _finish_quit(self):
        if self.spot_log.thread.is_alive():
            self.after(50, self._finish_quit); return
        error = self.spot_log.error
        if error: messagebox.showerror('Spot recording', error, parent=self)
        self.destroy()

if __name__ == '__main__': App().mainloop()
