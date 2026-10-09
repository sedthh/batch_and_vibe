"""Windowed launch and optional borderless presentation; no game memory or saves."""
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import time

from .processes import game_processes

SETUP_KEY = r'Software\Lionhead Studios Ltd\Black & White\BWSetup'
RESOLUTIONS = ('800x600', '1024x768', '1280x720', '1280x960', '1600x900', '1920x1080')
BORDERLESS_PROPERTY = 'BWV.WindowedBorderless'
WS_CAPTION = 0x00C00000


def write_windowed_settings(width, height):
    """Change only the three documented display settings; roll back a failed write."""
    import winreg
    values={'FullScreen':0, 'ScreenW':width, 'ScreenH':height}
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, SETUP_KEY, 0,
                           winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE) as key:
        previous={}
        for name in values:
            try:previous[name]=winreg.QueryValueEx(key,name)
            except FileNotFoundError:previous[name]=None
        try:
            for name,value in values.items():winreg.SetValueEx(key,name,0,winreg.REG_DWORD,value)
        except OSError:
            for name,old in previous.items():
                if old is None:
                    try:winreg.DeleteValue(key,name)
                    except FileNotFoundError:pass
                else:winreg.SetValueEx(key,name,0,old[1],old[0])
            raise
    return previous


def restore_display_settings(previous):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER,SETUP_KEY,0,winreg.KEY_SET_VALUE) as key:
        for name,old in previous.items():
            if old is None:
                try:winreg.DeleteValue(key,name)
                except FileNotFoundError:pass
            else:winreg.SetValueEx(key,name,0,old[1],old[0])


class DisplayWindows:
    def __init__(self):
        from .overlay_windows import Windows
        self.win=Windows()
        u=self.win.u
        class MonitorInfo(ctypes.Structure):
            _fields_=[('size',wintypes.DWORD),('monitor',wintypes.RECT),
                      ('work',wintypes.RECT),('flags',wintypes.DWORD)]
        self.monitor_info=MonitorInfo
        for name,args,result in (
            ('MonitorFromWindow',[wintypes.HWND,wintypes.DWORD],wintypes.HANDLE),
            ('GetMonitorInfoW',[wintypes.HANDLE,ctypes.POINTER(MonitorInfo)],wintypes.BOOL),
            ('GetPropW',[wintypes.HWND,wintypes.LPCWSTR],wintypes.HANDLE),
            ('SetPropW',[wintypes.HWND,wintypes.LPCWSTR,wintypes.HANDLE],wintypes.BOOL),
            ('RemovePropW',[wintypes.HWND,wintypes.LPCWSTR],wintypes.HANDLE),
            ('GetMenu',[wintypes.HWND],wintypes.HMENU),
            ('SetMenu',[wintypes.HWND,wintypes.HMENU],wintypes.BOOL),
            ('GetClassNameW',[wintypes.HWND,wintypes.LPWSTR,ctypes.c_int],ctypes.c_int),
        ):
            fn=getattr(u,name);fn.argtypes=args;fn.restype=result

    def monitor(self, hwnd=None):
        monitor=self.win.u.MonitorFromWindow(hwnd,2)
        info=self.monitor_info();info.size=ctypes.sizeof(info)
        if not self.win.u.GetMonitorInfoW(monitor,ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        r=info.monitor
        return r.left,r.top,r.right,r.bottom

    def window(self, pid):
        return self.win.game_window(pid)

    def mode(self, pid):
        hwnd=self.window(pid)
        if not hwnd:return 'unavailable'
        style=self.win.get_long(hwnd,-16)
        # Borderless windows made by this launcher remain composited windowed
        # games. Unknown frameless windows are not proof of overlay support.
        if self.win.u.GetPropW(hwnd,BORDERLESS_PROPERTY):return 'borderless'
        if style & WS_CAPTION == WS_CAPTION:return 'windowed'
        return 'fullscreen'

    def make_borderless(self, hwnd, bounds):
        if (self.win.get_long(hwnd,-16) & WS_CAPTION != WS_CAPTION
                and not self.win.u.GetPropW(hwnd,BORDERLESS_PROPERTY)):
            raise ValueError('The game did not open windowed. Check that patch 1.42 is installed.')
        old_style=self.win.get_long(hwnd,-16)
        old_extended=self.win.get_long(hwnd,-20)
        menu=self.win.u.GetMenu(hwnd)
        try:
            ctypes.set_last_error(0)
            self.win.set_long(hwnd,-16,old_style & ~(WS_CAPTION | 0x00040000))
            if ctypes.get_last_error():raise ctypes.WinError(ctypes.get_last_error())
            ctypes.set_last_error(0)
            self.win.set_long(hwnd,-20,old_extended & ~(0x00000100|0x00000200|0x00000001|0x00020000))
            if ctypes.get_last_error():raise ctypes.WinError(ctypes.get_last_error())
            if menu and not self.win.u.SetMenu(hwnd,None):raise ctypes.WinError(ctypes.get_last_error())
            x,y,right,bottom=bounds
            if not self.win.u.SetWindowPos(hwnd,None,x,y,right-x,bottom-y,0x0020|0x0004|0x0010):
                raise ctypes.WinError(ctypes.get_last_error())
            if not self.win.u.SetPropW(hwnd,BORDERLESS_PROPERTY,1):
                raise ctypes.WinError(ctypes.get_last_error())
        except (OSError,ValueError):
            self.win.u.RemovePropW(hwnd,BORDERLESS_PROPERTY)
            self.win.set_long(hwnd,-16,old_style)
            self.win.set_long(hwnd,-20,old_extended)
            if menu:self.win.u.SetMenu(hwnd,menu)
            self.win.u.SetWindowPos(hwnd,None,0,0,0,0,0x0020|0x0001|0x0002|0x0004|0x0010)
            raise

    def render_window_ready(self, hwnd):
        """Ignore small startup dialogs, without requiring a full-screen client area."""
        name=ctypes.create_unicode_buffer(256)
        if not self.win.u.GetClassNameW(hwnd,name,256):return False
        rect=self.win.rect(hwnd)
        return bool(name.value!='#32770' and rect
                    and rect[2]-rect[0]>=640 and rect[3]-rect[1]>=480)

    def borderless_matches(self, hwnd, bounds):
        return bool(self.win.u.GetPropW(hwnd,BORDERLESS_PROPERTY)
                    and not self.win.get_long(hwnd,-16)&(WS_CAPTION|0x00040000)
                    and not self.win.u.GetMenu(hwnd)
                    and self.win.rect(hwnd)==bounds)


class DisplayMonitor:
    """Shared by scheduling and the final pre-HTTP check, including tool rounds."""
    def __init__(self, windows, pid, *, clock=time.monotonic):
        self.windows,self.pid,self.clock=windows,pid,clock
        self.overlay_alive=lambda:True
        self.cached=None;self.next_check=0;self.mode='unavailable';self.error=None

    def reason(self):
        pid=self.pid();now=self.clock()
        if pid!=self.cached or now>=self.next_check:
            self.cached=pid;self.next_check=now+.2
            try:
                self.mode=self.windows.mode(pid) if pid else 'unavailable'
                self.error=None
            except (OSError,ValueError) as error:
                self.mode='unavailable';self.error=type(error).__name__
        if self.mode=='fullscreen':return 'fullscreen_overlay_unavailable'
        if self.mode not in ('windowed','borderless'):return 'game_display_unavailable'
        if not self.overlay_alive():return 'chat_overlay_unavailable'
        return None


class GameLauncher:
    """Start one game, then apply borderless style without blocking Tk or asyncio."""
    def __init__(self, root, *, windows=None, discover=game_processes,
                 spawn=subprocess.Popen, configure=write_windowed_settings,
                 restore=restore_display_settings, clock=time.monotonic):
        self.root=Path(root);self.windows=windows or DisplayWindows()
        self.discover,self.spawn,self.configure,self.restore,self.clock=discover,spawn,configure,restore,clock
        self.pending=None
        self.process=None
        self._discovered_running=False
        self._next_discovery=0
        self._next_install_check=0
        self._installation_error=None

    @property
    def installation_error(self):
        now=self.clock()
        if now>=self._next_install_check:
            try:
                self._installation_error=(None if (self.root/'runblack.exe').is_file() else
                    'Cannot find runblack.exe in the selected installation. Restore it or restart the mod and select the game folder.')
            except OSError:
                self._installation_error='Cannot access the selected game installation. Check that its drive and folder are available.'
            self._next_install_check=now+1
        return self._installation_error

    @property
    def busy(self):
        if self.pending or (self.process is not None and self.process.poll() is None):
            return True
        # Process discovery is relatively expensive; don't do it every UI frame.
        now=self.clock()
        if now>=self._next_discovery:
            self._discovered_running=bool(self.discover())
            self._next_discovery=now+1
        return self._discovered_running

    def start(self, mode, resolution, hwnd=None):
        if mode not in ('windowed','borderless'):raise ValueError('Unknown game display mode')
        self._next_install_check=0
        if self.installation_error:raise ValueError(self.installation_error)
        if self.busy or self.discover():raise ValueError('Black & White is already running or starting. Close it before changing display mode.')
        if resolution not in RESOLUTIONS:raise ValueError('Choose a window size from the list.')
        bounds=self.windows.monitor(hwnd)
        width,height=(tuple(map(int,resolution.split('x'))) if mode=='windowed'
                      else (bounds[2]-bounds[0],bounds[3]-bounds[1]))
        if not (640<=width<=16384 and 480<=height<=16384):raise ValueError('Unsupported monitor size.')
        exe=self.root/'runblack.exe'
        if not exe.is_file():raise ValueError('runblack.exe is missing from the selected installation.')
        previous=self.configure(width,height)
        try:
            # No runas prompt: a child inherits the elevated launcher's token.
            process=self.spawn([str(exe)],cwd=str(self.root))
        except OSError:
            self.restore(previous)
            raise
        self.process=process
        self.pending={'process':process,'mode':mode,'bounds':bounds,'size':(width,height),
                      'deadline':self.clock()+60,'candidate':None,'stable_since':None,
                      'applied':False,'attempts':0,'verified_since':None}
        return 'Starting Black & White '+('borderless fullscreen' if mode=='borderless' else resolution+' windowed')+'.'

    def poll(self):
        if not self.pending:return None
        pending=self.pending;process=pending['process']
        if process.poll() is not None:
            self.pending=None
            raise RuntimeError('Black & White exited during startup. Check the game installation and display settings.')
        hwnd=self.windows.window(process.pid)
        if hwnd:
            rect=self.windows.win.rect(hwnd)
            if pending['mode']=='borderless' and self.windows.render_window_ready(hwnd):
                now=self.clock()
                candidate=(hwnd,rect)
                if not pending['applied']:
                    if candidate!=pending['candidate']:
                        pending['candidate']=candidate;pending['stable_since']=now
                    elif now-pending['stable_since']>=.5:
                        try:self.windows.make_borderless(hwnd,pending['bounds'])
                        except (OSError,ValueError):
                            self.pending=None
                            raise
                        pending['applied']=True;pending['attempts']+=1
                if pending['applied']:
                    if self.windows.borderless_matches(hwnd,pending['bounds']):
                        if pending['verified_since'] is None:pending['verified_since']=now
                        if now-pending['verified_since']>=1:
                            self.pending=None
                            return 'Black & White is running borderless; menu removed and monitor-sized client area verified.'
                    else:
                        pending['verified_since']=None
                        if pending['attempts']>=3:
                            self.pending=None
                            raise RuntimeError('The game did not retain borderless mode after three attempts. It was left running; no success was reported.')
                        # Startup can recreate the frame/menu. Retry only after
                        # another stable window sample, not every UI frame.
                        pending['applied']=False;pending['candidate']=candidate
                        pending['stable_since']=now
            elif pending['mode']=='windowed' and rect and (rect[2]-rect[0],rect[3]-rect[1])==pending['size']:
                self.pending=None
                return 'Black & White is running '+pending['mode']+'.'
        if self.clock()>=pending['deadline']:
            self.pending=None
            raise RuntimeError('Could not confirm the game window at the requested size. The game was left running; check its display settings.')
        return None
