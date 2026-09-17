namespace AFKLocalAI.App;

/// <summary>Every process the shell starts, as a structured argument list.</summary>
public sealed class ProvisioningController
{
    private readonly AppPaths _paths;
    public ProvisioningController(AppPaths paths) => _paths = paths;

    /// <summary>AFK AI's own pinned CPython, installed with this program.</summary>
    public string RuntimeInterpreter => Path.Combine(_paths.ProgramRoot, "runtime", "python", "python.exe");

    public string EngineEntryPoint => Path.Combine(_paths.ProgramRoot, "installer", "afk-payload.py");

    public ProcessSpec Preflight() => WindowsPowerShell(
        "preflight",
        "installer/Get-Preflight.ps1",
        "-Json", "-DataRoot", _paths.StateRoot);

    public ProcessSpec Recovery(string code, string actionId) => WindowsPowerShell(
        "recovery",
        "installer/Invoke-Recovery.ps1",
        "-Code", code, "-ActionId", actionId, "-DataRoot", _paths.StateRoot, "-EventStream");

    /// <summary>
    /// Setup, or with <paramref name="repair"/> a re-run of every product step.
    /// </summary>
    /// <remarks>
    /// Windows PowerShell 5.1 ships with Windows 11; PowerShell 7 does not. This
    /// used to launch pwsh.exe, so on a clean PC "Set up" failed to start at all.
    /// </remarks>
    public ProcessSpec Provision(bool repair = false) => WindowsPowerShell(
        repair ? "repair" : "provision",
        "installer/Install-LocalAI.ps1",
        repair ? "-Repair" : "-Resume", "-AcceptDefaults", "-DataRoot", _paths.StateRoot,
        "-LegacyInstallRoot", _paths.LegacyInstallRoot, "-EventStream");

    /// <summary>Full qualification (may load the model) or a cheap liveness probe.</summary>
    public ProcessSpec Status(bool liveness) => liveness
        ? Engine("status-liveness", "status", "--json", "--liveness")
        : Engine("status", "status", "--json");

    public ProcessSpec Start() => Engine("start", "start");

    /// <summary>Stops only what this installation can prove it owns.</summary>
    public ProcessSpec Stop() => Engine("stop", "stop");

    public ProcessSpec Diagnostics(string outputPath) => Engine("diagnostics", "diagnostics", "--output", outputPath);

    /// <summary>
    /// Runs one command on THIS installation's engine, on AFK AI's own runtime.
    /// </summary>
    /// <remarks>
    /// Deliberately NOT <c>py -m localai</c> (the ambient package name: on a
    /// machine with the private engineering workbench installed editable, Stop
    /// tore down that workbench and force-closed Docker Desktop and Ollama) and
    /// NOT <c>py.exe afk-payload.py</c> (whatever interpreter the launcher picks,
    /// with that interpreter's site-packages and PYTHON* settings). The entry
    /// point additionally refuses to run on any interpreter but this one.
    /// <para>
    /// <c>-I</c> isolates from PYTHON* variables and user site-packages (the
    /// embeddable runtime's ._pth already implies it); <c>-B</c> keeps bytecode
    /// out of the program directory so an uninstall leaves nothing behind.
    /// </para>
    /// </remarks>
    private ProcessSpec Engine(string purpose, params string[] command)
    {
        var arguments = new List<string>
        {
            "-I", "-B", EngineEntryPoint,
            "--program-root", _paths.ProgramRoot,
            "--data-root", _paths.DataRoot
        };
        arguments.AddRange(command);
        return ProcessSpec.Hidden(RuntimeInterpreter, arguments, _paths.ProgramRoot, purpose);
    }

    private ProcessSpec WindowsPowerShell(string purpose, string relativeScript, params string[] arguments)
    {
        var allArguments = new List<string>
        {
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", Path.Combine(_paths.ProgramRoot, relativeScript.Replace('/', Path.DirectorySeparatorChar))
        };
        allArguments.AddRange(arguments);
        // Reproduced: started from a PowerShell 7 terminal, this app inherits
        // PowerShell 7's PSModulePath, and Windows PowerShell then cannot load its
        // own Microsoft.PowerShell.Utility or CimCmdlets - so preflight, recovery
        // and setup fail for reasons that have nothing to do with the PC.
        return ProcessSpec.Hidden(WindowsPowerShellPath(), allArguments, _paths.ProgramRoot, purpose,
            removedEnvironment: new[] { "PSModulePath" });
    }

    /// <summary>The inbox Windows PowerShell, by full path rather than PATH lookup.</summary>
    public static string WindowsPowerShellPath()
    {
        var system = Environment.GetFolderPath(Environment.SpecialFolder.System);
        var inbox = Path.Combine(system, "WindowsPowerShell", "v1.0", "powershell.exe");
        return File.Exists(inbox) ? inbox : "powershell.exe";
    }
}
