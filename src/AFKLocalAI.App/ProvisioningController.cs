namespace AFKLocalAI.App;

public sealed class ProvisioningController
{
    private readonly AppPaths _paths;
    public ProvisioningController(AppPaths paths) => _paths = paths;

    public ProcessSpec Preflight() => PowerShell(
        "preflight",
        "installer/Get-Preflight.ps1",
        "-Json", "-DataRoot", _paths.StateRoot);

    public ProcessSpec Recovery(string code, string actionId) => PowerShell(
        "recovery",
        "installer/Invoke-Recovery.ps1",
        "-Code", code, "-ActionId", actionId, "-DataRoot", _paths.StateRoot, "-EventStream");

    public ProcessSpec Provision() => Pwsh(
        "provision",
        "installer/Install-LocalAI.ps1",
        "-Resume", "-AcceptDefaults", "-DataRoot", _paths.StateRoot,
        "-LegacyInstallRoot", _paths.LegacyInstallRoot, "-EventStream");

    public ProcessSpec Start() => Payload("start");

    /// <summary>Stops only what this installation can prove it owns.</summary>
    public ProcessSpec Stop() => Payload("stop");

    public ProcessSpec Health() => Payload("health");

    /// <summary>
    /// Runs one command against THIS installation's own Python payload.
    /// </summary>
    /// <remarks>
    /// Deliberately NOT "py -m localai &lt;command&gt;". That resolves through the
    /// ambient <c>localai</c> package name, so on a machine that also has the
    /// private engineering workbench installed editable it runs the workbench's
    /// code against the workbench's repository root. For Stop that was
    /// destructive - it tore down the workbench's compose project and
    /// force-closed Docker Desktop and Ollama for the whole machine. For Start
    /// and Health it is quieter but just as wrong: this product would operate,
    /// and report on, somebody else's stack. The entry point below is invoked by
    /// path, proves which package answered the import, and refuses rather than
    /// guessing.
    /// <para>
    /// "-B" keeps the interpreter from writing __pycache__ into the program
    /// directory: Setup never installed those files, so its uninstaller never
    /// removes them, and the whole installation would survive an uninstall.
    /// </para>
    /// </remarks>
    private ProcessSpec Payload(string command) => ProcessSpec.Hidden(
        "py.exe",
        new[]
        {
            "-B",
            Path.Combine(_paths.ProgramRoot, "installer", "afk-payload.py"),
            command,
            "--program-root", _paths.ProgramRoot
        },
        _paths.ProgramRoot,
        command);

    public ProcessSpec Diagnostics() => PowerShell(
        "diagnostics",
        "installer/Get-Preflight.ps1",
        "-Json", "-DataRoot", _paths.StateRoot);

    public Uri DashboardUri => new("http://127.0.0.1:3000/");

    private ProcessSpec PowerShell(string purpose, string relativeScript, params string[] arguments) =>
        Script("powershell.exe", purpose, relativeScript, arguments);

    private ProcessSpec Pwsh(string purpose, string relativeScript, params string[] arguments) =>
        Script("pwsh.exe", purpose, relativeScript, arguments);

    private ProcessSpec Script(string executable, string purpose, string relativeScript, params string[] arguments)
    {
        var allArguments = new List<string>
        {
            "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-File", Path.Combine(_paths.ProgramRoot, relativeScript.Replace('/', Path.DirectorySeparatorChar))
        };
        allArguments.AddRange(arguments);
        return ProcessSpec.Hidden(executable, allArguments, _paths.ProgramRoot, purpose);
    }
}
