using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;

// DesktopIconToggle - isolated demo.
// Explicit --run entry point; --check is a pure read-only environment probe.
internal static class Program
{
    // ---- exit codes (mirrored in Usage() and README.md) ----
    private const int EXIT_OK = 0;                 // help finished, or requested action succeeded
    private const int EXIT_BAD_ARGS = 2;           // invalid / missing / extra argument
    private const int EXIT_NO_EXPLORER = 3;        // explorer.exe not running
    private const int EXIT_NO_DESKTOP = 4;         // desktop window missing, stale or owner check failed
    private const int EXIT_ALREADY_RUNNING = 5;    // another instance holds the single-instance mutex
    private const int EXIT_HOOK_FAILED = 6;        // mouse hook installation failed
    private const int EXIT_UNSUPPORTED_OS = 7;     // not a supported Windows version
    private const int EXIT_RUNTIME_ERROR = 8;      // other initialization / runtime failure

    private const string MutexName = "DesktopIconToggle.SingleInstance";

    private const int SW_HIDE = 0;
    private const int SW_SHOW = 5;
    private const int WH_MOUSE_LL = 14;
    private const int WM_LBUTTONDOWN = 0x0201;
    private const int WM_QUIT = 0x0012;
    private const int WM_TIMER = 0x0113;
    private const int WM_APP_CLICK = 0x8000 + 33;
    private const int PM_NOREMOVE = 0x0000;
    private const int LVM_HITTEST = 0x1012;
    private const int SM_CXDOUBLECLK = 36;
    private const int SM_CYDOUBLECLK = 37;
    private const int PROCESS_VM_OPERATION = 0x0008;
    private const int PROCESS_VM_READ = 0x0010;
    private const int PROCESS_VM_WRITE = 0x0020;
    private const int MEM_COMMIT = 0x1000;
    private const int MEM_RESERVE = 0x2000;
    private const int MEM_RELEASE = 0x8000;
    private const int PAGE_READWRITE = 0x04;
    private const int TIMER_INTERVAL_MS = 1000;
    private const int CLICK_QUEUE_LIMIT = 64;
    private const int CLICK_BUDGET_MS = 750;
    private const int HITTEST_FAILED = -2;

    // verified desktop window identity, captured once at start-up
    private static IntPtr _host = IntPtr.Zero;      // window that hosts SHELLDLL_DefView (Progman or WorkerW)
    private static IntPtr _defView = IntPtr.Zero;   // SHELLDLL_DefView
    private static IntPtr _iconView = IntPtr.Zero;  // SysListView32 (desktop icon list)
    private static uint _shellPid;

    private static IntPtr _hook = IntPtr.Zero;
    private static HookProc _hookProc;
    private static ConsoleCancelEventHandler _cancelHandler;
    private static IntPtr _timerId = new IntPtr(1);
    private static bool _timerInstalled;
    private static uint _threadId;

    private static bool _wasVisible = true;         // visibility observed before we touched anything
    private static bool _armed;
    private static int _lastTick;
    private static int _lastX;
    private static int _lastY;

    private sealed class ClickEvent
    {
        public int X;
        public int Y;
    }

    private static readonly Queue<ClickEvent> _pending = new Queue<ClickEvent>();
    private static readonly object _pendingLock = new object();

    private delegate IntPtr HookProc(int nCode, IntPtr wParam, IntPtr lParam);

    [StructLayout(LayoutKind.Sequential)]
    private struct POINT { public int X; public int Y; }

    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int Left; public int Top; public int Right; public int Bottom; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MSLLHOOKSTRUCT { public POINT pt; public uint mouseData; public uint flags; public uint time; public IntPtr dwExtraInfo; }

    [StructLayout(LayoutKind.Sequential)]
    private struct MSG { public IntPtr hwnd; public uint message; public IntPtr wParam; public IntPtr lParam; public uint time; public POINT pt; }

    [StructLayout(LayoutKind.Sequential)]
    private struct LVHITTESTINFO { public POINT pt; public uint flags; public int iItem; public int iGroup; public int iSubItem; }

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindow(string cls, string win);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr FindWindowEx(IntPtr parent, IntPtr child, string cls, string win);
    [DllImport("user32.dll")]
    private static extern bool IsWindow(IntPtr h);
    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr h, int cmd);
    [DllImport("user32.dll")]
    private static extern IntPtr GetParent(IntPtr h);
    [DllImport("user32.dll")]
    private static extern bool GetClientRect(IntPtr h, out RECT r);
    [DllImport("user32.dll")]
    private static extern IntPtr WindowFromPoint(POINT p);
    [DllImport("user32.dll")]
    private static extern bool ScreenToClient(IntPtr h, ref POINT p);
    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int index);
    [DllImport("user32.dll")]
    private static extern uint GetDoubleClickTime();
    [DllImport("user32.dll")]
    private static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
    [DllImport("user32.dll")]
    private static extern IntPtr SetWindowsHookEx(int id, HookProc proc, IntPtr module, uint threadId);
    [DllImport("user32.dll")]
    private static extern bool UnhookWindowsHookEx(IntPtr hook);
    [DllImport("user32.dll")]
    private static extern IntPtr CallNextHookEx(IntPtr hook, int code, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern int GetMessage(out MSG msg, IntPtr hwnd, uint filterMin, uint filterMax);
    [DllImport("user32.dll")]
    private static extern bool PeekMessage(out MSG msg, IntPtr hwnd, uint filterMin, uint filterMax, uint remove);
    [DllImport("user32.dll")]
    private static extern bool TranslateMessage(ref MSG msg);
    [DllImport("user32.dll")]
    private static extern IntPtr DispatchMessage(ref MSG msg);
    [DllImport("user32.dll")]
    private static extern bool PostThreadMessage(uint idThread, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")]
    private static extern IntPtr SetTimer(IntPtr hwnd, IntPtr id, uint elapse, IntPtr proc);
    [DllImport("user32.dll")]
    private static extern bool KillTimer(IntPtr hwnd, IntPtr id);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern IntPtr SendMessage(IntPtr h, int msg, IntPtr wParam, IntPtr lParam);
    [DllImport("kernel32.dll")]
    private static extern uint GetCurrentThreadId();
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(int access, bool inherit, uint pid);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr VirtualAllocEx(IntPtr proc, IntPtr addr, IntPtr size, int type, int protect);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool VirtualFreeEx(IntPtr proc, IntPtr addr, IntPtr size, int type);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool WriteProcessMemory(IntPtr proc, IntPtr addr, byte[] buffer, IntPtr size, out IntPtr written);
    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool ReadProcessMemory(IntPtr proc, IntPtr addr, byte[] buffer, IntPtr size, out IntPtr read);
    [DllImport("kernel32.dll")]
    private static extern bool CloseHandle(IntPtr handle);

    private static int Main(string[] args)
    {
        // argument parsing happens before any run-time initialization
        if (args.Length == 1)
        {
            string mode = args[0];
            if (string.Equals(mode, "--check", StringComparison.OrdinalIgnoreCase)) return Check();
            if (string.Equals(mode, "--run", StringComparison.OrdinalIgnoreCase)) return Run();
            if (string.Equals(mode, "--help", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "-h", StringComparison.OrdinalIgnoreCase)
                || string.Equals(mode, "/?", StringComparison.OrdinalIgnoreCase))
            {
                Usage();
                return EXIT_OK;
            }
            Console.Error.WriteLine("Unknown argument: " + mode);
            Usage();
            return EXIT_BAD_ARGS;
        }

        if (args.Length > 1)
        {
            Console.Error.WriteLine("Only one mode argument is accepted; got " + args.Length + ".");
            Usage();
            return EXIT_BAD_ARGS;
        }

        Usage();
        return EXIT_OK;
    }

    private static void Usage()
    {
        Console.WriteLine("DesktopIconToggle - toggle Explorer desktop icons from a blank desktop double-click");
        Console.WriteLine("  --check  read-only environment/desktop window check (no changes, exit code only)");
        Console.WriteLine("  --run    start listener, hide desktop icons, double-click blank desktop to toggle");
        Console.WriteLine("  --help   show this text (also used when no argument is given)");
        Console.WriteLine("Exit codes:");
        Console.WriteLine("  0  help finished, or the requested operation succeeded");
        Console.WriteLine("  2  invalid argument (unknown, missing or extra)");
        Console.WriteLine("  3  explorer.exe is not running");
        Console.WriteLine("  4  desktop window missing, stale, or owner check failed");
        Console.WriteLine("  5  another instance is already running");
        Console.WriteLine("  6  mouse hook installation failed");
        Console.WriteLine("  7  unsupported operating system");
        Console.WriteLine("  8  other initialization or runtime error");
    }

    private static bool IsSupportedOs()
    {
        // pure .NET property access; no Windows API is called here
        if (Environment.OSVersion.Platform != PlatformID.Win32NT) return false;
        return Environment.OSVersion.Version.Major >= 6;
    }

    private static Process[] ExplorerProcesses()
    {
        try { return Process.GetProcessesByName("explorer"); }
        catch (Exception) { return new Process[0]; }
    }

    // ---------------------------------------------------------------- --check

    // Read-only path: no mutex, no hook, no timer, no message loop, no
    // visibility change, no file/registry write, no cross-process VM access.
    private static int Check()
    {
        if (!IsSupportedOs())
        {
            Console.WriteLine("os      : unsupported (" + Environment.OSVersion.VersionString + ")");
            Console.WriteLine("result  : FAILED code " + EXIT_UNSUPPORTED_OS + " (read-only, nothing modified)");
            return EXIT_UNSUPPORTED_OS;
        }

        Console.WriteLine("os      : windows " + Environment.OSVersion.VersionString);

        Process[] shells = ExplorerProcesses();
        Console.WriteLine("explorer: " + (shells.Length > 0 ? shells.Length + " process(es)" : "not running"));
        if (shells.Length == 0)
        {
            Console.WriteLine("result  : FAILED code " + EXIT_NO_EXPLORER + " (read-only, nothing modified)");
            return EXIT_NO_EXPLORER;
        }

        IntPtr host;
        IntPtr defView;
        IntPtr iconView;
        bool found = LocateDesktop(out host, out defView, out iconView);
        int rc = EXIT_OK;

        Console.WriteLine("defview : " + Hex(defView) + " (SHELLDLL_DefView)");
        Console.WriteLine("icons   : " + Hex(iconView) + " (SysListView32)");
        Console.WriteLine("host    : " + Hex(host) + " (desktop host)");
        if (!found)
        {
            rc = EXIT_NO_DESKTOP;
        }
        else
        {
            uint pid;
            GetWindowThreadProcessId(iconView, out pid);
            bool shellOwned = false;
            for (int i = 0; i < shells.Length; i++)
            {
                if ((uint)shells[i].Id == pid) shellOwned = true;
            }
            Console.WriteLine("owner   : pid " + pid + (shellOwned ? " (explorer)" : " (not explorer)"));

            if (!shellOwned)
            {
                rc = EXIT_NO_DESKTOP;
            }
            else
            {
                uint defPid;
                GetWindowThreadProcessId(defView, out defPid);
                if (defPid != pid)
                {
                    Console.WriteLine("defview : owner pid " + defPid + " does not match icon view owner");
                    rc = EXIT_NO_DESKTOP;
                }
                else
                {
                    Console.WriteLine("visible : " + (IsWindowVisible(iconView) ? "icons shown" : "icons hidden"));
                }
            }
        }

        Console.WriteLine("result  : " + (rc == EXIT_OK
            ? "OK (read-only, nothing modified)"
            : "FAILED code " + rc + " (read-only, nothing modified)"));
        return rc;
    }

    private static string Hex(IntPtr h)
    {
        if (h == IntPtr.Zero) return "not found";
        return "0x" + h.ToInt64().ToString("X");
    }

    // ----------------------------------------------------------------- --run

    private static int Run()
    {
        if (!IsSupportedOs())
        {
            Console.Error.WriteLine("Unsupported operating system; nothing changed.");
            return EXIT_UNSUPPORTED_OS;
        }

        Process[] shells = ExplorerProcesses();
        if (shells.Length == 0)
        {
            Console.Error.WriteLine("explorer.exe is not running; nothing changed.");
            return EXIT_NO_EXPLORER;
        }

        bool created;
        Mutex mutex = new Mutex(true, MutexName, out created);
        try
        {
            if (!created)
            {
                Console.Error.WriteLine("Another DesktopIconToggle instance is already running.");
                return EXIT_ALREADY_RUNNING;
            }

            try
            {
                return RunCore(shells);
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("Runtime error: " + ex.Message);
                return EXIT_RUNTIME_ERROR;
            }
            finally
            {
                ReleaseRuntime();
            }
        }
        finally
        {
            if (created)
            {
                try { mutex.ReleaseMutex(); }
                catch (ApplicationException) { }
            }
            mutex.Close();
        }
    }

    private static int RunCore(Process[] shells)
    {
        IntPtr host;
        IntPtr defView;
        IntPtr iconView;
        if (!LocateDesktop(out host, out defView, out iconView))
        {
            Console.Error.WriteLine("Explorer desktop icon view not found; nothing changed.");
            return EXIT_NO_DESKTOP;
        }

        uint pid;
        GetWindowThreadProcessId(iconView, out pid);
        bool shellOwned = false;
        for (int i = 0; i < shells.Length; i++)
        {
            if ((uint)shells[i].Id == pid) shellOwned = true;
        }
        if (!shellOwned)
        {
            Console.Error.WriteLine("Desktop icon view is not owned by explorer; nothing changed.");
            return EXIT_NO_DESKTOP;
        }

        _host = host;
        _defView = defView;
        _iconView = iconView;
        _shellPid = pid;

        // record the real pre-run state, then set the documented default explicitly
        _wasVisible = IsWindowVisible(_iconView);
        ShowWindow(_iconView, SW_HIDE);

        // create this thread's message queue once (required by PostThreadMessage)
        MSG seed;
        PeekMessage(out seed, IntPtr.Zero, 0x0400, 0x0400, PM_NOREMOVE);
        _threadId = GetCurrentThreadId();

        _hookProc = HookCallback;
        _hook = SetWindowsHookEx(WH_MOUSE_LL, _hookProc, IntPtr.Zero, 0);
        if (_hook == IntPtr.Zero)
        {
            Console.Error.WriteLine("Mouse hook installation failed: " + Marshal.GetLastWin32Error());
            return EXIT_HOOK_FAILED;
        }

        if (SetTimer(IntPtr.Zero, _timerId, TIMER_INTERVAL_MS, IntPtr.Zero) == IntPtr.Zero)
        {
            Console.Error.WriteLine("Timer creation failed; listener not started.");
            return EXIT_RUNTIME_ERROR;
        }
        _timerInstalled = true;

        _cancelHandler = OnCancelKeyPress;
        Console.CancelKeyPress += _cancelHandler;
        Console.WriteLine("Running. Desktop icons hidden; double-click a blank desktop area to toggle. Ctrl+C exits.");
        Console.WriteLine("Desktop icons are restored to their pre-run state on exit.");

        MessageLoop();
        return EXIT_OK;
    }

    private static void OnCancelKeyPress(object sender, ConsoleCancelEventArgs e)
    {
        e.Cancel = true;
        RequestShutdown();
    }

    private static void RequestShutdown()
    {
        // wake the blocked message loop so it can unwind normally
        PostThreadMessage(_threadId, WM_QUIT, IntPtr.Zero, IntPtr.Zero);
    }

    private static void MessageLoop()
    {
        // blocking loop; the periodic check comes from WM_TIMER, not from polling
        while (true)
        {
            MSG msg;
            int got = GetMessage(out msg, IntPtr.Zero, 0, 0);
            if (got == 0 || got == -1) break; // WM_QUIT or message error

            if (msg.message == WM_TIMER)
            {
                if (!IdentityValid())
                {
                    Console.Error.WriteLine("Desktop window changed or disappeared; listener stopped without touching the new window.");
                    break;
                }
                continue;
            }

            if (msg.message == WM_APP_CLICK)
            {
                if (!DrainClicks()) break;
                continue;
            }

            TranslateMessage(ref msg);
            DispatchMessage(ref msg);
        }
    }

    // Processes queued clicks in arrival order, outside the hook callback,
    // with a bounded time budget per wake-up.
    private static bool DrainClicks()
    {
        int start = Environment.TickCount;
        while (true)
        {
            ClickEvent click;
            lock (_pendingLock)
            {
                if (_pending.Count == 0) return true;
                click = _pending.Dequeue();
            }

            if (unchecked(Environment.TickCount - start) > CLICK_BUDGET_MS)
            {
                lock (_pendingLock) _pending.Clear();
                _armed = false;
                Console.Error.WriteLine("Click processing exceeded its time budget; remaining clicks ignored.");
                return true;
            }

            if (!IdentityValid())
            {
                lock (_pendingLock) _pending.Clear();
                _armed = false;
                Console.Error.WriteLine("Desktop window changed or disappeared; listener stopped without touching the new window.");
                return false;
            }

            HandleClick(click.X, click.Y);
        }
    }

    private static IntPtr HookCallback(int nCode, IntPtr wParam, IntPtr lParam)
    {
        // short callback: queue the point and wake the loop; hit testing stays outside
        if (nCode >= 0 && wParam == (IntPtr)WM_LBUTTONDOWN)
        {
            try
            {
                MSLLHOOKSTRUCT info = (MSLLHOOKSTRUCT)Marshal.PtrToStructure(lParam, typeof(MSLLHOOKSTRUCT));
                bool queued = false;
                lock (_pendingLock)
                {
                    if (_pending.Count < CLICK_QUEUE_LIMIT)
                    {
                        ClickEvent click = new ClickEvent();
                        click.X = info.pt.X;
                        click.Y = info.pt.Y;
                        _pending.Enqueue(click);
                        queued = true;
                    }
                }
                if (queued) PostThreadMessage(_threadId, WM_APP_CLICK, IntPtr.Zero, IntPtr.Zero);
            }
            catch (Exception)
            {
                // never swallow or delay normal mouse delivery
            }
        }
        return CallNextHookEx(_hook, nCode, wParam, lParam);
    }

    // --------------------------------------------------------- desktop lookup

    private static bool LocateDesktop(out IntPtr host, out IntPtr defView, out IntPtr iconView)
    {
        host = IntPtr.Zero;
        defView = IntPtr.Zero;
        iconView = IntPtr.Zero;

        IntPtr progman = FindWindow("Progman", null);
        if (progman != IntPtr.Zero)
        {
            IntPtr candidate = FindWindowEx(progman, IntPtr.Zero, "SHELLDLL_DefView", null);
            if (candidate != IntPtr.Zero)
            {
                host = progman;
                defView = candidate;
            }
        }

        if (defView == IntPtr.Zero)
        {
            IntPtr worker = IntPtr.Zero;
            while (true)
            {
                worker = FindWindowEx(IntPtr.Zero, worker, "WorkerW", null);
                if (worker == IntPtr.Zero) break;
                IntPtr candidate = FindWindowEx(worker, IntPtr.Zero, "SHELLDLL_DefView", null);
                if (candidate != IntPtr.Zero)
                {
                    // keep the exact host relationship instead of "any WorkerW"
                    IntPtr parent = GetParent(candidate);
                    host = parent != IntPtr.Zero ? parent : worker;
                    defView = candidate;
                    break;
                }
            }
        }

        if (defView == IntPtr.Zero) return false;
        iconView = FindWindowEx(defView, IntPtr.Zero, "SysListView32", null);
        return iconView != IntPtr.Zero;
    }

    // Confirms the recorded windows still exist with the same identity and owner.
    private static bool IdentityValid()
    {
        if (_iconView == IntPtr.Zero || _defView == IntPtr.Zero || _host == IntPtr.Zero) return false;
        if (!IsWindow(_host) || !IsWindow(_defView) || !IsWindow(_iconView)) return false;
        if (GetParent(_defView) != _host) return false;
        if (FindWindowEx(_defView, IntPtr.Zero, "SysListView32", null) != _iconView) return false;

        uint pid;
        GetWindowThreadProcessId(_iconView, out pid);
        if (pid != _shellPid) return false;
        GetWindowThreadProcessId(_defView, out pid);
        if (pid != _shellPid) return false;
        return true;
    }

    // ---------------------------------------------------------- run behaviour

    // Restores the visibility observed before start-up, but only while the
    // original windows are still valid; never force-shows what was hidden.
    private static void RestoreVisibility()
    {
        if (!IdentityValid()) return;
        ShowWindow(_iconView, _wasVisible ? SW_SHOW : SW_HIDE);
    }

    private static void ReleaseRuntime()
    {
        if (_cancelHandler != null)
        {
            Console.CancelKeyPress -= _cancelHandler;
            _cancelHandler = null;
        }

        if (_hook != IntPtr.Zero)
        {
            UnhookWindowsHookEx(_hook);
            _hook = IntPtr.Zero;
        }

        if (_timerInstalled)
        {
            KillTimer(IntPtr.Zero, _timerId);
            _timerInstalled = false;
        }

        RestoreVisibility();

        lock (_pendingLock)
        {
            _pending.Clear();
        }
        _armed = false;
    }

    private static void HandleClick(int x, int y)
    {
        if (!IsBlankDesktopPoint(x, y))
        {
            _armed = false;
            return;
        }

        int now = Environment.TickCount;
        int delta = unchecked(now - _lastTick);
        int cx = Math.Max(GetSystemMetrics(SM_CXDOUBLECLK), 2);
        int cy = Math.Max(GetSystemMetrics(SM_CYDOUBLECLK), 2);

        bool matched = _armed
            && delta >= 0
            && delta <= (int)GetDoubleClickTime()
            && Math.Abs(x - _lastX) * 2 <= cx
            && Math.Abs(y - _lastY) * 2 <= cy;

        if (matched)
        {
            _armed = false;
            ToggleIcons();
        }
        else
        {
            _armed = true;
            _lastTick = now;
            _lastX = x;
            _lastY = y;
        }
    }

    private static void ToggleIcons()
    {
        if (!IdentityValid()) return;
        bool visible = IsWindowVisible(_iconView);
        ShowWindow(_iconView, visible ? SW_HIDE : SW_SHOW);
    }

    // Blank-desktop predicate with two strategies:
    //  - icons shown: the point must hit the verified icon view and the item
    //    hit test must report no icon under the cursor;
    //  - icons hidden: the hidden SysListView32 is not required (and is not
    //    project hit-tested); the point must hit the verified SHELLDLL_DefView
    //    or its desktop host, inside that window's client area.
    private static bool IsBlankDesktopPoint(int x, int y)
    {
        if (!IdentityValid()) return false;

        POINT screen;
        screen.X = x;
        screen.Y = y;
        IntPtr hit = WindowFromPoint(screen);
        if (hit == IntPtr.Zero) return false;

        if (IsWindowVisible(_iconView))
        {
            if (hit != _iconView) return false;

            POINT client;
            client.X = x;
            client.Y = y;
            if (!ScreenToClient(_iconView, ref client)) return false;
            if (!PointInClient(_iconView, client)) return false;

            int item = HitTestItem(_iconView, client.X, client.Y);
            return item == -1; // an icon under the cursor, or a failed test, abandons
        }

        if (hit != _defView && hit != _host) return false; // taskbar, other apps, unrelated WorkerW

        POINT hostClient;
        hostClient.X = x;
        hostClient.Y = y;
        if (!ScreenToClient(hit, ref hostClient)) return false;
        return PointInClient(hit, hostClient);
    }

    private static bool PointInClient(IntPtr window, POINT client)
    {
        RECT rect;
        if (!GetClientRect(window, out rect)) return false;
        return client.X >= rect.Left && client.X < rect.Right
            && client.Y >= rect.Top && client.Y < rect.Bottom;
    }

    // Returns -1 for "no item", >= 0 for an icon, -2 when the test could not
    // be completed (permission, allocation or stale window).
    private static int HitTestItem(IntPtr view, int x, int y)
    {
        if (!IsWindow(view)) return HITTEST_FAILED;

        uint pid;
        GetWindowThreadProcessId(view, out pid);
        if (pid == 0) return HITTEST_FAILED;

        IntPtr proc = OpenProcess(PROCESS_VM_OPERATION | PROCESS_VM_READ | PROCESS_VM_WRITE, false, pid);
        if (proc == IntPtr.Zero) return HITTEST_FAILED;

        int size = Marshal.SizeOf(typeof(LVHITTESTINFO));
        IntPtr remote = VirtualAllocEx(proc, IntPtr.Zero, (IntPtr)size, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE);
        if (remote == IntPtr.Zero)
        {
            CloseHandle(proc);
            return HITTEST_FAILED;
        }

        IntPtr local = Marshal.AllocHGlobal(size);
        try
        {
            LVHITTESTINFO info;
            info.pt.X = x;
            info.pt.Y = y;
            info.flags = 0;
            info.iItem = -1;
            info.iGroup = 0;
            info.iSubItem = 0;
            Marshal.StructureToPtr(info, local, false);

            byte[] buffer = new byte[size];
            Marshal.Copy(local, buffer, 0, size);

            IntPtr moved;
            if (!WriteProcessMemory(proc, remote, buffer, (IntPtr)size, out moved)) return HITTEST_FAILED;
            SendMessage(view, LVM_HITTEST, IntPtr.Zero, remote);
            if (!IsWindow(view)) return HITTEST_FAILED;
            if (!ReadProcessMemory(proc, remote, buffer, (IntPtr)size, out moved)) return HITTEST_FAILED;

            Marshal.Copy(buffer, 0, local, size);
            LVHITTESTINFO result = (LVHITTESTINFO)Marshal.PtrToStructure(local, typeof(LVHITTESTINFO));
            return result.iItem < 0 ? -1 : result.iItem;
        }
        finally
        {
            Marshal.FreeHGlobal(local);
            VirtualFreeEx(proc, remote, IntPtr.Zero, MEM_RELEASE);
            CloseHandle(proc);
        }
    }
}
