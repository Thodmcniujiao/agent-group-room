// Portable launcher: starts the bundled Python runtime with all data kept beside the executable.
using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Agent Group Room")]
[assembly: System.Reflection.AssemblyProduct("Agent Group Room (portable)")]

static class Program
{
    [STAThread]
    static int Main(string[] args)
    {
        string root = AppDomain.CurrentDomain.BaseDirectory;
        string python = Path.Combine(root, "runtime", "pythonw.exe");
        string app = Path.Combine(root, "app");
        string launch = Path.Combine(app, "gui", "launch.py");
        if (!File.Exists(python) || !File.Exists(launch))
        {
            MessageBox.Show("便携包不完整：缺少 runtime 或 app 文件夹。请重新解压整个压缩包。\n\nThe portable package is incomplete. Extract the whole archive again.",
                "代理协作室 · Agent Group Room", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        string data = Path.Combine(root, "data");
        try { Directory.CreateDirectory(data); }
        catch (Exception error)
        {
            MessageBox.Show("无法在程序目录创建 data 文件夹，请把便携包放到可写的位置。\n\n" + error.Message,
                "代理协作室 · Agent Group Room", MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        string arguments = "\"" + launch + "\"";
        foreach (string arg in args) if (arg == "--browser") arguments += " --browser";
        var start = new ProcessStartInfo(python, arguments) { UseShellExecute = false, WorkingDirectory = app };
        start.EnvironmentVariables["TIERED_DATA_HOME"] = data;
        start.EnvironmentVariables["TIERED_GUI_PYTHON"] = python;
        start.EnvironmentVariables["TIERED_GUI_PORT_BASE"] = "49335";
        start.EnvironmentVariables["PYTHONNOUSERSITE"] = "1";
        start.EnvironmentVariables.Remove("PYTHONHOME");
        start.EnvironmentVariables.Remove("PYTHONPATH");
        Process.Start(start);
        return 0;
    }
}
