"""한글 창을 띄우지 않고 한컴을 쓴다: 한컴 COM을 부르는 파이썬을 숨긴 데스크톱에서 돌린다.

COM은 서버(Hwp.exe)를 부른 쪽 프로세스의 데스크톱에 띄운다. 그래서 부르는 파이썬을 숨긴 데스크톱(CreateDesktop)에서
실행하면 한글 창·입력기 아이콘(HimTrayIcon)이 모두 그 데스크톱에 생겨 사람 화면에는 아무것도 뜨지 않는다.
확인한 것: 문서 열기·저장·쪽 그림 동안 기본 데스크톱의 보이는 한컴 창 0, 전경 창 바뀜 0, 끝난 뒤 남은 한글 0.
쓰지 않는 방법: Hwp.exe를 숨긴 데스크톱에 미리 띄우고 붙기 — COM이 무시하고 새 한글을 기본 데스크톱에 띄웠다.

사용: 스크립트를 숨긴 데스크톱에서 돌리기  python hwp_hidden.py 스크립트.py [인자…]
      hwp_tool.py는 한컴이 필요한 명령이면 저절로 옮겨 간다.
"""
import ctypes
import ctypes.wintypes as W
import msvcrt
import os
import subprocess
import sys
import tempfile
import threading
import time

DESK = "hwptool_hidden"
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
WNDENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HWND, W.LPARAM)
user32.CreateDesktopW.restype = W.HANDLE
user32.CreateDesktopW.argtypes = [W.LPCWSTR, W.LPCWSTR, ctypes.c_void_p, W.DWORD, W.DWORD, ctypes.c_void_p]
user32.GetThreadDesktop.restype = W.HANDLE
user32.GetUserObjectInformationW.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
user32.CloseDesktop.argtypes = [W.HANDLE]
HANCOM = ("hwp.exe", "himtrayicon.exe", "hwpapi.exe", "hncupdate.exe", "hwpviewer.exe")
OURS = HANCOM + ("python.exe", "pythonw.exe", "py.exe", "conhost.exe")     # 한컴·이 도구가 띄울 수 있는 프로세스


class STARTUPINFO(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR), ("lpTitle", W.LPWSTR),
                ("dwX", W.DWORD), ("dwY", W.DWORD), ("dwXSize", W.DWORD), ("dwYSize", W.DWORD),
                ("dwXCountChars", W.DWORD), ("dwYCountChars", W.DWORD), ("dwFillAttribute", W.DWORD),
                ("dwFlags", W.DWORD), ("wShowWindow", W.WORD), ("cbReserved2", W.WORD), ("lpReserved2", ctypes.c_void_p),
                ("hStdInput", W.HANDLE), ("hStdOutput", W.HANDLE), ("hStdError", W.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [("hProcess", W.HANDLE), ("hThread", W.HANDLE), ("dwProcessId", W.DWORD), ("dwThreadId", W.DWORD)]


def current_desktop():
    h = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
    buf = ctypes.create_unicode_buffer(256)
    n = W.DWORD()
    if not user32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(n)):   # UOI_NAME
        return ""
    return buf.value


def on_hidden():
    return current_desktop() == DESK


def run_hidden(argv, timeout=3600):
    """argv(첫 칸은 실행 파일)를 숨긴 데스크톱에서 콘솔 창 없이 돌리고, 출력을 이 프로세스의 stdout·stderr로 옮긴다. → 종료 코드"""
    hdesk = user32.CreateDesktopW(DESK, None, None, 0, 0x10000000, None)        # 있으면 연다. GENERIC_ALL
    if not hdesk:
        raise OSError(ctypes.get_last_error(), "숨긴 데스크톱을 만들지 못했습니다")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    td = tempfile.mkdtemp(prefix="hwptool_")
    paths = [os.path.join(td, "out.txt"), os.path.join(td, "err.txt")]
    files = [open(p, "wb") for p in paths]
    handles = [msvcrt.get_osfhandle(f.fileno()) for f in files]
    for h in handles:
        os.set_handle_inheritable(h, True)
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    si.lpDesktop = DESK
    si.dwFlags = 0x1 | 0x100          # STARTF_USESHOWWINDOW | STARTF_USESTDHANDLES
    si.wShowWindow = 0                # SW_HIDE
    si.hStdOutput, si.hStdError = handles
    pi = PROCESS_INFORMATION()
    cmd = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
    ok = kernel32.CreateProcessW(None, cmd, None, None, True, 0x08000000, None, os.getcwd(), ctypes.byref(si), ctypes.byref(pi))
    err = ctypes.get_last_error()
    for f in files:
        f.close()
    if not ok:
        user32.CloseDesktop(hdesk)
        raise OSError(err, "숨긴 데스크톱에서 실행하지 못했습니다")
    timed_out = kernel32.WaitForSingleObject(pi.hProcess, int(timeout * 1000)) != 0
    if timed_out:
        kernel32.TerminateProcess(pi.hProcess, 1)
        kernel32.WaitForSingleObject(pi.hProcess, 5000)
    code = W.DWORD()
    kernel32.GetExitCodeProcess(pi.hProcess, ctypes.byref(code))
    kernel32.CloseHandle(pi.hThread)
    kernel32.CloseHandle(pi.hProcess)
    user32.CloseDesktop(hdesk)
    for p, stream in zip(paths, (sys.stdout, sys.stderr)):
        text = open(p, "rb").read().decode("utf-8", errors="replace")
        if text:
            stream.write(text)
            stream.flush()
        os.remove(p)
    os.rmdir(td)
    if timed_out:
        print(f"시간 초과({timeout}초)로 멈췄습니다.", file=sys.stderr)
    return code.value


def reexec_hidden(timeout=3600):
    """지금 스크립트를 같은 인자로 숨긴 데스크톱에서 다시 돌리고 그 종료 코드로 끝낸다(이미 숨긴 데스크톱이면 그냥 돌아온다)."""
    if on_hidden():
        return
    sys.exit(run_hidden([sys.executable, os.path.abspath(sys.argv[0])] + sys.argv[1:], timeout))


# ---------- 창 감시(회귀 시험용) ----------
def _proc_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)
    if not h:
        return ""
    buf = ctypes.create_unicode_buffer(520)
    n = W.DWORD(520)
    ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n))
    kernel32.CloseHandle(h)
    return os.path.basename(buf.value).lower() if ok else ""


def _win_pid(hwnd):
    pid = W.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", W.HWND), ("hwndFocus", W.HWND),
                ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND), ("hwndMoveSize", W.HWND), ("hwndCaret", W.HWND),
                ("rcCaret", W.RECT)]


def keyboard_focus():
    gi = GUITHREADINFO()
    gi.cbSize = ctypes.sizeof(gi)
    return gi.hwndFocus if user32.GetGUIThreadInfo(0, ctypes.byref(gi)) else None


def visible_hancom_windows(visible_only=True, skip_pids=()):
    """이 프로세스의 데스크톱(사람 화면)에 있는 한컴 프로세스의 최상위 창 수. visible_only=False면 숨은 창(트레이 아이콘 창·대화상자 포함)도 센다.
    skip_pids: 셈에서 뺄 프로세스(사람이 미리 열어 둔 한글)."""
    out = {}

    def cb(hwnd, lp):
        if not visible_only or user32.IsWindowVisible(hwnd):
            pid = _win_pid(hwnd)
            n = _proc_name(pid)
            if n in HANCOM and pid not in skip_pids:
                out[n] = out.get(n, 0) + 1
        return True
    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return out


class WindowWatch:
    """with WindowWatch() as w: … → w.result(): 사람 화면(이 프로세스의 데스크톱)에 보인 한컴 창 최대 수와 전경 창 바뀜."""

    def __init__(self, interval=0.02):
        self.interval = interval

    def __enter__(self):
        self.t0 = time.time()
        self.pre = set()                    # 시작 때 이미 있던 한컴 프로세스(사람이 연 한글) — 셈에서 뺀다

        def cb(hwnd, lp):
            pid = _win_pid(hwnd)
            if _proc_name(pid) in HANCOM:
                self.pre.add(pid)
            return True
        user32.EnumWindows(WNDENUMPROC(cb), 0)
        self.halt = False
        self.max_visible, self.max_any, self.seen, self.fg, self.focus = 0, 0, [], [], []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        return self

    def _run(self):
        last = user32.GetForegroundWindow()
        last_focus = keyboard_focus()
        while not self.halt:
            v = visible_hancom_windows(skip_pids=self.pre)
            if sum(v.values()) > self.max_visible:
                self.max_visible = sum(v.values())
                self.seen.append((round(time.time() - self.t0, 2), v))
            a = visible_hancom_windows(visible_only=False, skip_pids=self.pre)
            if sum(a.values()) > self.max_any:
                self.max_any = sum(a.values())
                self.seen.append((round(time.time() - self.t0, 2), {"숨은 창 포함": a}))
            f = keyboard_focus()
            if f != last_focus:
                self.focus.append((round(time.time() - self.t0, 2), self._who(f)))
                last_focus = f
            fg = user32.GetForegroundWindow()
            if fg != last:
                self.fg.append((round(time.time() - self.t0, 2), self._who(fg)))
                last = fg
            time.sleep(self.interval)

    def _who(self, hwnd):
        """바뀐 창이 한컴·이 도구 것이면 프로세스 이름, 아니면 '그 밖'(사람이 쓰는 프로그램·미리 열어 둔 한글 이름은 남기지 않는다)."""
        pid = _win_pid(hwnd) if hwnd else 0
        n = _proc_name(pid) if hwnd else ""
        return n if n in OURS and pid not in self.pre else "그 밖"

    def __exit__(self, *exc):
        time.sleep(0.3)
        self.halt = True
        self.thread.join()
        return False

    def result(self):
        ours_fg = [x for x in self.fg if x[1] in OURS]
        ours_focus = [x for x in self.focus if x[1] in OURS]
        return {"미리 열려 있던 한컴(셈에서 뺌)": len(self.pre),
                "보이는 한컴 창(최대)": self.max_visible, "사람 화면의 한컴 창(숨은 창 포함, 최대)": self.max_any,
                "전경 창 바뀜": len(self.fg), "한컴·도구로 간 전경": len(ours_fg), "키보드 포커스 바뀜": len(self.focus),
                "한컴·도구로 간 포커스": len(ours_focus),
                "자세히": {"보인 창": self.seen, "전경": self.fg, "포커스": self.focus}}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    sys.exit(run_hidden([sys.executable] + sys.argv[1:]))


if __name__ == "__main__":
    main()
