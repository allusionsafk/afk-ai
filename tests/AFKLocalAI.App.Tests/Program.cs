using AFKLocalAI.App;
using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;

var failures = new List<string>();
var passed = 0;

void Check(string name, bool condition, string detail = "")
{
    if (condition)
    {
        passed++;
        Console.WriteLine($"PASS {name}");
        return;
    }
    failures.Add(string.IsNullOrWhiteSpace(detail) ? name : $"{name}: {detail}");
}

void Throws<T>(string name, Action action) where T : Exception
{
    try { action(); failures.Add($"{name}: expected {typeof(T).Name}"); }
    catch (T) { passed++; Console.WriteLine($"PASS {name}"); }
}

var root = Directory.GetCurrentDirectory();
var metadataPath = Path.Combine(root, "installer", "version.json");
var product = ProductInfo.Load(metadataPath);
Check("product name", product.ProductName == "AFK LocalAI", product.ProductName);
Check("display version", product.DisplayVersion == "0.2.0-rc1", product.DisplayVersion);
Check("file version", product.FileVersion == "0.2.0.0", product.FileVersion);
Check("canonical executable", product.ExecutableName == "AFKLocalAI.exe", product.ExecutableName);
Check("stable app id", product.AppId == "{8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0}", product.AppId);

var scratch = Path.Combine(root, "build", "dotnet-core-tests", Guid.NewGuid().ToString("N"));
Directory.CreateDirectory(scratch);
try
{
    var badMetadata = Path.Combine(scratch, "version.json");
    File.WriteAllText(badMetadata, """{"schema_version":1,"product_name":"AFK LocalAI"}""");
    Throws<InvalidDataException>("incomplete metadata fails closed", () => ProductInfo.Load(badMetadata));

    var programRoot = Path.Combine(scratch, "Program Files Ünïcode", "AFK AI");
    var dataRoot = Path.Combine(scratch, "Data", "AFK AI");
    var paths = AppPaths.ForCurrentUser(dataRoot, programRoot);
    Check("program root is explicit", paths.ProgramRoot == Path.GetFullPath(programRoot), paths.ProgramRoot);
    Check("data is outside program files", !paths.DataRoot.StartsWith(paths.ProgramRoot, StringComparison.OrdinalIgnoreCase));
    Check("state directory", paths.StateRoot == Path.Combine(Path.GetFullPath(dataRoot), "State"), paths.StateRoot);
    Check("log directory", paths.LogRoot == Path.Combine(Path.GetFullPath(dataRoot), "Logs"), paths.LogRoot);
    Check("diagnostics directory", paths.DiagnosticsRoot == Path.Combine(Path.GetFullPath(dataRoot), "Diagnostics"), paths.DiagnosticsRoot);

    var hidden = ProcessSpec.Hidden("powershell.exe", new[] { "-NoProfile", "-Command", "exit 0" }, root);
    ProcessStartInfo info = hidden.CreateStartInfo();
    Check("shell execution disabled", !info.UseShellExecute);
    Check("console creation disabled", info.CreateNoWindow);
    Check("window style hidden", info.WindowStyle == ProcessWindowStyle.Hidden);
    Check("stdout redirected", info.RedirectStandardOutput);
    Check("stderr redirected", info.RedirectStandardError);
    Check("working directory retained", info.WorkingDirectory == Path.GetFullPath(root), info.WorkingDirectory);

    // ------------------------------------------------------------ resume state
    var store = new ProvisioningStateStore(paths);
    var state = store.LoadOrCreate();
    state.SetupCompleted = true;
    state.LastReasonCode = "PREFLIGHT-READY";
    store.SaveAtomic(state);
    var roundTrip = store.LoadOrCreate();
    Check("state roundtrip preserves setup completion", roundTrip.SetupCompleted);
    Check("state roundtrip preserves reason", roundTrip.LastReasonCode == "PREFLIGHT-READY", roundTrip.LastReasonCode ?? "null");
    Check("state no longer persists a 'usable' readiness claim", !File.ReadAllText(paths.ProvisioningStatePath).Contains("\"usable\""));

    // Regression: schema 2 stored usable=true after setup exited 0, and Home said
    // "Your local AI is ready" from it forever. It migrates to "setup completed" only.
    File.WriteAllText(paths.ProvisioningStatePath, """{"schema_version":2,"usable":true,"last_reason_code":"SETUP-COMPLETE"}""");
    var migrated = store.LoadOrCreate();
    Check("schema 2 usable migrates to setup completed", migrated.SetupCompleted && migrated.SchemaVersion == ProvisioningState.CurrentSchemaVersion);
    File.WriteAllText(paths.ProvisioningStatePath, """{"schema_version":2,"usable":false}""");
    Check("schema 2 unusable migrates to setup not completed", !store.LoadOrCreate().SetupCompleted);

    File.WriteAllText(paths.ProvisioningStatePath, "{ definitely not json");
    var recovered = store.LoadOrCreate();
    Check("corrupt state recovers safely", recovered.SchemaVersion == ProvisioningState.CurrentSchemaVersion && !recovered.SetupCompleted);
    Check("corrupt state is quarantined", Directory.EnumerateFiles(paths.StateRoot, "provisioning-state.corrupt-*.json").Any());

    var eventLine = """AFK-EVENT:{"schema_version":1,"timestamp_utc":"2026-09-08T12:00:00Z","event_type":"phase-start","phase":"runtime","status":"running","code":"phase-start","message":"Starting runtime."}""";
    Check("structured event parses", ProvisioningEvent.TryParse(eventLine, out var parsedEvent));
    Check("event phase retained", parsedEvent?.Phase == "runtime", parsedEvent?.Phase ?? "null");
    Check("ordinary console line is ignored", !ProvisioningEvent.TryParse("Installing Ollama...", out _));
    var progressLine = """AFK-EVENT:{"schema_version":1,"timestamp_utc":"2026-09-08T12:00:00Z","event_type":"progress","phase":"pulls","status":"running","code":"download","message":"Downloading: 42%","data":{"completed":42,"total":100,"percent":42}}""";
    Check("download progress carries a percent", ProvisioningEvent.TryParse(progressLine, out var progressEvent) && progressEvent?.Percent == 42);
    var bogusProgress = """AFK-EVENT:{"schema_version":1,"event_type":"progress","phase":"pulls","status":"running","code":"download","message":"x","data":{"percent":400}}""";
    Check("an out-of-range percent is ignored", ProvisioningEvent.TryParse(bogusProgress, out var bogusEvent) && bogusEvent?.Percent is null);

    Check("docker absent maps to install", RecoveryAction.ForCode("PREFLIGHT-DOCKER-NOT-INSTALLED").ActionId == "install-docker");
    Check("remote context is guidance only", !RecoveryAction.ForCode("PREFLIGHT-DOCKER-REMOTE-CONTEXT").Automatic);
    Throws<ArgumentOutOfRangeException>("unknown recovery mapping fails closed", () => RecoveryAction.ForCode("PREFLIGHT-NOT-REAL"));

    // ---------------------------------------------------------- process specs
    var controller = new ProvisioningController(paths);
    var specs = new[]
    {
        controller.Preflight(),
        controller.Recovery("PREFLIGHT-DOCKER-NOT-INSTALLED", "install-docker"),
        controller.Provision(),
        controller.Provision(repair: true),
        controller.Status(liveness: false),
        controller.Status(liveness: true),
        controller.Start(),
        controller.Stop(),
        controller.Diagnostics(Path.Combine(paths.DiagnosticsRoot, "report.json"))
    };
    foreach (var spec in specs)
    {
        var startInfo = spec.CreateStartInfo();
        Check($"{spec.Purpose} hides console", startInfo.CreateNoWindow && !startInfo.UseShellExecute &&
            startInfo.WindowStyle == ProcessWindowStyle.Hidden, spec.FileName);
        Check($"{spec.Purpose} never starts PowerShell 7 or a Python launcher",
            !spec.FileName.EndsWith("pwsh.exe", StringComparison.OrdinalIgnoreCase) &&
            !spec.FileName.EndsWith("py.exe", StringComparison.OrdinalIgnoreCase), spec.FileName);
    }
    Check("preflight uses inbox Windows PowerShell", controller.Preflight().FileName.EndsWith("powershell.exe", StringComparison.OrdinalIgnoreCase));
    Check("preflight requests JSON", controller.Preflight().Arguments.Contains("-Json"));
    Check("provision requests event stream", controller.Provision().Arguments.Contains("-EventStream"));
    // Regression: provisioning launched pwsh.exe, which Windows 11 does not ship,
    // so "Set up" could not even start on a clean PC.
    Check("provision runs on Windows PowerShell 5.1", controller.Provision().FileName.EndsWith("powershell.exe", StringComparison.OrdinalIgnoreCase));
    Check("repair re-runs product phases", controller.Provision(repair: true).Arguments.Contains("-Repair") &&
        !controller.Provision(repair: true).Arguments.Contains("-Resume"));
    foreach (var spec in new[] { controller.Preflight(), controller.Recovery("PREFLIGHT-READY", "retry"), controller.Provision() })
        Check($"{spec.Purpose} does not inherit PowerShell 7's module path", !spec.CreateStartInfo().Environment.ContainsKey("PSModulePath"));

    // Regression: engine commands were "py -3.12 -m localai" (ambient package: Stop
    // tore down a private workbench and force-closed Docker Desktop and Ollama) and
    // then "py.exe afk-payload.py" (right package, whatever interpreter the launcher
    // chose). They must run THIS installation's engine on ITS OWN runtime.
    var ownedInterpreter = Path.Combine(paths.ProgramRoot, "runtime", "python", "python.exe");
    foreach (var (name, spec) in new[]
             {
                 ("status", controller.Status(liveness: false)),
                 ("status", controller.Status(liveness: true)),
                 ("start", controller.Start()),
                 ("stop", controller.Stop()),
                 ("diagnostics", controller.Diagnostics(Path.Combine(paths.DiagnosticsRoot, "r.json")))
             })
    {
        var arguments = spec.Arguments;
        var rendered = string.Join(" ", arguments);
        Check($"{spec.Purpose} runs on AFK AI's own interpreter", spec.FileName == ownedInterpreter, spec.FileName);
        Check($"{spec.Purpose} never invokes the ambient localai package",
            !arguments.Contains("localai") && !arguments.Contains("-m") && !arguments.Contains("-3.12"), rendered);
        Check($"{spec.Purpose} runs the verified payload entry point",
            arguments.Any(argument => argument == Path.Combine(paths.ProgramRoot, "installer", "afk-payload.py")), rendered);
        Check($"{spec.Purpose} is isolated from PYTHON* settings", arguments.Take(2).SequenceEqual(new[] { "-I", "-B" }), rendered);
        Check($"{spec.Purpose} names the command explicitly", arguments.Contains(name), rendered);
        Check($"{spec.Purpose} passes this installation's program root",
            arguments[arguments.ToList().IndexOf("--program-root") + 1] == paths.ProgramRoot, rendered);
        Check($"{spec.Purpose} passes this installation's data root",
            arguments[arguments.ToList().IndexOf("--data-root") + 1] == paths.DataRoot, rendered);
    }
    Check("liveness status never loads the model", controller.Status(liveness: true).Arguments.Contains("--liveness"));
    Check("qualify status is a full check", !controller.Status(liveness: false).Arguments.Contains("--liveness"));

    var options = CommandLineOptions.Parse(new[] { "--self-test", "--data-root", dataRoot });
    Check("self-test option parses", options.SelfTest);
    Check("data-root option parses", options.DataRoot == Path.GetFullPath(dataRoot), options.DataRoot ?? "null");
    Throws<ArgumentException>("unknown command-line option fails closed", () => CommandLineOptions.Parse(new[] { "--mystery" }));
    var uninstallOptions = CommandLineOptions.Parse(new[] { "--stop", "--silent" });
    Check("uninstall stop option parses", uninstallOptions.Stop);
    Check("silent option parses", uninstallOptions.Silent);
    Check("self-test failures never open a dialog", !CommandLineFailurePolicy.ShouldShowDialog(new[] { "--self-test" }));
    Check("silent failures never open a dialog", !CommandLineFailurePolicy.ShouldShowDialog(new[] { "--silent" }));
    Check("interactive launch failures remain visible", CommandLineFailurePolicy.ShouldShowDialog(Array.Empty<string>()));
    Check("command-line failure text is diagnostic", CommandLineFailurePolicy.Format(new InvalidOperationException("fixture failure")).Contains("fixture failure", StringComparison.Ordinal));
    // The uninstaller's stop must not fail the uninstall when the runtime is gone.
    Check("uninstall stop succeeds when the owned runtime is missing", UninstallStop.Run(paths) == 0);

    Check("fresh state opens setup mode", AppModeResolver.Resolve(new ProvisioningState()) == AppMode.Setup);
    Check("completed setup opens home mode", AppModeResolver.Resolve(new ProvisioningState { SetupCompleted = true }) == AppMode.Home);
    Check("single-instance mutex is version-independent", AppIdentity.SingleInstanceMutexName == "Local\\AFKLocalAI-8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0");

    using var icon = AppIcon.Create();
    Check("application icon is available", icon.Width >= 32 && icon.Height >= 32, $"{icon.Width}x{icon.Height}");
    Check("shell exposes accessible setup labels", MainForm.AccessibilityContract.Contains("Prerequisite status") &&
        MainForm.AccessibilityContract.Contains("Setup progress"));
    Check("shell exposes accessible home labels", MainForm.AccessibilityContract.Contains("Product status") &&
        MainForm.AccessibilityContract.Contains("Primary action") && MainForm.AccessibilityContract.Contains("Activity"));
    Check("shell uses the hidden process runner", MainForm.ProcessRunnerType == typeof(HiddenProcessRunner));

    Directory.CreateDirectory(Path.Combine(programRoot, "installer"));
    File.WriteAllText(Path.Combine(programRoot, "installer", "Get-Preflight.ps1"), "# self-test fixture");
    File.WriteAllText(Path.Combine(programRoot, "installer", "Invoke-Recovery.ps1"), "# self-test fixture");
    File.WriteAllText(Path.Combine(programRoot, "installer", "afk-payload.py"), "# self-test fixture");
    Directory.CreateDirectory(Path.Combine(programRoot, "runtime", "python"));
    File.WriteAllText(Path.Combine(programRoot, "runtime", "python", "python.exe"), "MZ fixture");
    var selfTest = SelfTestRunner.Run(paths, product);
    Check("shell self-test succeeds", selfTest.Success, string.Join(" | ", selfTest.Checks.Where(pair => !pair.Value).Select(pair => pair.Key)));
    Check("shell self-test checks the owned runtime", selfTest.Checks.ContainsKey("owned_runtime_present") && selfTest.Checks.ContainsKey("engine_entry_present"));
    Check("shell self-test serializes as JSON", selfTest.ToJson().Contains("\"success\":true", StringComparison.Ordinal));

    // -------------------------------------------------------------- integrity
    {
        var installed = Path.Combine(scratch, "Installed");
        var files = new Dictionary<string, string>
        {
            ["runtime/python/python.exe"] = "interpreter",
            ["runtime/python/python314.dll"] = "dll",
            ["runtime/afk-runtime.json"] = "{}",
            ["src/localai/readiness.py"] = "print('ok')",
            ["installer/afk-payload.py"] = "entry",
            ["AFKLocalAI.exe"] = "shell"
        };
        WriteManifest(installed, files);
        Check("an intact installation verifies", InstallationIntegrity.Verify(installed).Intact);

        File.WriteAllText(Path.Combine(installed, "runtime", "python", "python314.dll"), "truncated");
        File.Delete(Path.Combine(installed, "installer", "afk-payload.py"));
        File.WriteAllText(Path.Combine(installed, "src", "localai", "stale_module.py"), "x = 1");
        var damaged = InstallationIntegrity.Verify(installed);
        Check("a changed runtime file is detected", damaged.Mismatched.SequenceEqual(new[] { "runtime/python/python314.dll" }), string.Join(",", damaged.Mismatched));
        Check("a missing entry point is detected", damaged.Missing.SequenceEqual(new[] { "installer/afk-payload.py" }), string.Join(",", damaged.Missing));
        Check("a stale importable module is detected", damaged.Unexpected.SequenceEqual(new[] { "src/localai/stale_module.py" }), string.Join(",", damaged.Unexpected));
        Check("a damaged installation is never intact", !damaged.Intact);

        var noManifest = Path.Combine(scratch, "NoManifest");
        Directory.CreateDirectory(Path.Combine(noManifest, "runtime", "python"));
        File.WriteAllText(Path.Combine(noManifest, "runtime", "python", "python.exe"), "x");
        Check("an installation without its record is never intact", !InstallationIntegrity.Verify(noManifest).Intact);

        var escaping = Path.Combine(scratch, "Escaping");
        Directory.CreateDirectory(escaping);
        File.WriteAllText(Path.Combine(escaping, "payload-manifest.json"),
            """{"files":[{"path":"../runtime/python/python.exe","sha256":"00"},{"path":"C:/Windows/notepad.exe","sha256":"00"}]}""");
        var escaped = InstallationIntegrity.Verify(escaping);
        Check("manifest paths cannot escape the installation", escaped.Missing.Count == 0 && escaped.Mismatched.Count == 0 && !escaped.Intact);
    }

    // ------------------------------------------------------------ lifecycle log
    {
        var log = new LifecycleLog(Path.Combine(scratch, "Logs", LifecycleLog.FileName));
        for (var i = 0; i < 3; i++) log.Append("status", "status", "Ready", "READY", new string('x', 5000));
        var lines = File.ReadAllLines(log.PathName);
        Check("lifecycle log appends one JSON line per event", lines.Length == 3);
        Check("lifecycle log bounds message length", lines.All(line => line.Length < 1000));
        File.WriteAllText(log.PathName, new string('y', (int)LifecycleLog.MaxBytes + 10));
        log.Append("status", "status", "Stopped", "NOT_STARTED");
        Check("lifecycle log rotates instead of growing without bound",
            new FileInfo(log.PathName).Length < 1000 && File.Exists(Path.ChangeExtension(log.PathName, ".1.jsonl")));
    }

    // ------------------------------------------------------ diagnostics fallback
    {
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var damaged = new IntegrityResult(true, false, new[] { "runtime/python/python.exe" }, Array.Empty<string>(), Array.Empty<string>());
        var fallback = DiagnosticsWriter.Redact(DiagnosticsWriter.BuildFallback(product, damaged, HomeModel.Initial, "engine could not start").ToJsonString() +
            $" {Path.Combine(profile, "AppData", "Local", "AFK LocalAI")}");
        Check("fallback diagnostics classify a corrupt installation", fallback.Contains("corrupt_installation"));
        Check("fallback diagnostics redact the user profile", string.IsNullOrWhiteSpace(profile) || !fallback.Contains(profile, StringComparison.OrdinalIgnoreCase));
        Check("fallback diagnostics state their privacy exclusions", fallback.Contains("chat content"));
    }
}
finally
{
    if (Directory.Exists(scratch)) Directory.Delete(scratch, recursive: true);
}

// ----------------------------------------------------------- status contract
{
    static string Status(string state, string mode = "qualify", string reason = "READY", bool chatReady = true,
        string? url = "http://127.0.0.1:3000/", string inference = "passed", string? model = "qwen3.5:9b-32k",
        string next = "open_chat", string onboarding = "false") =>
        $$"""{"schema_version":2,"observed_at_utc":"2026-09-16T10:00:00Z","mode":"{{mode}}","state":"{{state}}","reason":"{{reason}}","message":"m","next_action":"{{next}}","chat":{"ready":{{(chatReady ? "true" : "false")}},"url":{{(url is null ? "null" : $"\"{url}\"")}},"onboarding_required":{{onboarding}}},"qualification":{"inference":"{{inference}}","detail":""},"model":{{(model is null ? "null" : $"\"{model}\"")}},"services":[]}""";

    static string Live(string? model = "qwen3.5:9b-32k") =>
        Status("Live", mode: "liveness", reason: "LIVE", chatReady: false, url: null, inference: "not_run", next: "check", model: model);

    var ready = ProductStatusParser.TryParse("noise\n" + Status("Ready"));
    Check("a schema-2 status parses from the last JSON line", ready?.State == "Ready" && ready.Chat.Ready);
    Check("malformed status output is not a verdict", ProductStatusParser.TryParse("{ nope") is null);
    Check("an unknown state is not a verdict", ProductStatusParser.TryParse(Status("Awesome")) is null);
    Check("an old schema is not a verdict", ProductStatusParser.TryParse(Status("Ready").Replace("\"schema_version\":2", "\"schema_version\":1")) is null);
    Check("empty output is not a verdict", ProductStatusParser.TryParse("") is null && ProductStatusParser.TryParse(null) is null);
    var started = ProductStatusParser.TryParse("AFK-EVENT:{}\n{\"stray\":1}\nAFK-STATUS:" + Status("Degraded", reason: "INFERENCE_FAILED", chatReady: false, url: null, inference: "failed", next: "diagnostics"), ProductStatusParser.StartStatusPrefix);
    Check("start's final status is taken from its AFK-STATUS line", started?.Reason == "INFERENCE_FAILED");

    var t0 = new DateTimeOffset(2026, 9, 16, 10, 0, 0, TimeSpan.Zero);
    StatusObserved Seen(DateTimeOffset at, string json) => new(at, ProductStatusParser.TryParse(json)!);

    // READY comes only from a qualified engine status.
    var home = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Ready")));
    Check("a qualified engine READY shows Ready", home.Kind == HomeKind.Ready && ChatGate.CanOpen(home, t0));
    Check("only Ready says ready", HomePresenter.Present(home, t0).Headline == "Your local AI is ready");
    foreach (var kind in Enum.GetValues<HomeKind>().Where(k => k != HomeKind.Ready))
        Check($"{kind} never says ready", !HomePresenter.Present(HomeModel.Initial with { Kind = kind }, t0).Headline.Contains("ready", StringComparison.OrdinalIgnoreCase));
    Check("a non-Ready kind can never offer Open Chat", Enum.GetValues<HomeKind>().Where(k => k != HomeKind.Ready)
        .All(kind => HomePresenter.Present(home with { Kind = kind }, t0).Primary != HomeAction.OpenChat));

    var inconsistent = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Ready", inference: "not_run")));
    Check("a READY without passed inference is not trusted", inconsistent.Kind == HomeKind.Unknown && !ChatGate.CanOpen(inconsistent, t0));
    var remote = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Ready", url: "http://evil.example/")));
    Check("a READY pointing chat off this PC is not trusted", remote.Kind == HomeKind.Unknown && !ChatGate.CanOpen(remote, t0));
    var readyByLiveness = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Ready", mode: "liveness")));
    Check("a READY claimed by a liveness probe is not trusted", readyByLiveness.Kind == HomeKind.Unknown);

    // Liveness keeps, never creates.
    var liveOnly = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Live()));
    Check("liveness alone never creates Ready", liveOnly.Kind == HomeKind.Checking && !ChatGate.CanOpen(liveOnly, t0));
    Check("liveness alone asks for a full check", RefreshPolicy.ShouldQualify(liveOnly));
    var kept = HomeReducer.Reduce(home, Seen(t0.AddMinutes(2), Live()));
    Check("liveness keeps a fresh qualification", kept.Kind == HomeKind.Ready && ChatGate.CanOpen(kept, t0.AddMinutes(2)));
    var otherModel = HomeReducer.Reduce(home, Seen(t0.AddMinutes(2), Live(model: "other:1b")));
    Check("a different model voids the qualification", otherModel.Kind == HomeKind.Checking);
    var expired = HomeReducer.Reduce(home, Seen(t0 + HomeReducer.QualificationLifetime + TimeSpan.FromSeconds(1), Live()));
    Check("an expired qualification is not kept", expired.Kind == HomeKind.Checking);
    Check("an expired Ready cannot open chat", !ChatGate.CanOpen(home, t0 + HomeReducer.QualificationLifetime + TimeSpan.FromSeconds(1)));
    Check("an expired Ready requalifies before opening", ChatGate.BeforeOpen(home, t0.AddHours(1)) == ChatGateDecision.Requalify);
    Check("a fresh Ready still probes before opening", ChatGate.BeforeOpen(home, t0.AddMinutes(1)) == ChatGateDecision.ProbeLiveness);
    Check("a clock that went backwards does not extend a qualification", !ChatGate.CanOpen(home, t0.AddMinutes(-5)));

    // THE stale-ready regression: the runtime fails after Ready was shown.
    var regressed = HomeReducer.Reduce(kept, Seen(t0.AddMinutes(3), Status("Degraded", mode: "liveness", reason: "WEBUI_BACKEND_UNAVAILABLE", chatReady: false, url: null, inference: "not_run", next: "repair")));
    Check("a backend failure immediately drops Ready", regressed.Kind == HomeKind.Degraded && regressed.Qualified is null);
    Check("a dropped Ready cannot open chat", !ChatGate.CanOpen(regressed, t0.AddMinutes(3)));
    var recoveredLive = HomeReducer.Reduce(regressed, Seen(t0.AddMinutes(4), Live()));
    Check("recovery after a regression must be re-proven", recoveredLive.Kind == HomeKind.Checking && !ChatGate.CanOpen(recoveredLive, t0.AddMinutes(4)));
    var stopped = HomeReducer.Reduce(home, Seen(t0.AddMinutes(1), Status("Stopped", mode: "liveness", reason: "DOCKER_UNREACHABLE", chatReady: false, url: null, inference: "not_run", next: "start")));
    Check("a stopped runtime offers Start, not chat", stopped.Kind == HomeKind.Stopped && HomePresenter.Present(stopped, t0).Primary == HomeAction.Start);
    var unobservable = HomeReducer.Reduce(home, new EngineUnavailable(t0.AddMinutes(1), "timed out", RepairNeeded: false));
    Check("an unobservable engine is Unknown, not Ready", unobservable.Kind == HomeKind.Unknown && !ChatGate.CanOpen(unobservable, t0.AddMinutes(1)));
    var corrupt = HomeReducer.Reduce(home, new EngineUnavailable(t0.AddMinutes(1), "runtime missing", RepairNeeded: true));
    Check("a corrupt runtime asks for repair", corrupt.Kind == HomeKind.RepairNeeded && HomePresenter.Present(corrupt, t0).Primary == HomeAction.Repair);
    var refused = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Failed", reason: "RUNTIME_UNVERIFIED", chatReady: false, url: null, inference: "not_run", next: "repair")));
    Check("an unverified runtime reported by the engine asks for repair", refused.Kind == HomeKind.RepairNeeded);
    var collision = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Failed", reason: "FOREIGN_PROJECT_COLLISION", chatReady: false, url: null, inference: "not_run", next: "diagnostics")));
    Check("a foreign collision offers diagnostics, never start or chat", collision.Kind == HomeKind.Failed && HomePresenter.Present(collision, t0).Primary == HomeAction.Diagnostics);

    // Regression (adversarial review of PR #24): Home dropped a Ready only after
    // a SUCCESSFUL setup or repair. A failed repair left the old qualification in
    // place, liveness kept it alive, and Open Chat routed to a runtime the repair
    // had just torn down. Any lifecycle operation voids the qualification first.
    foreach (var operationName in new[] { "repair", "setup" })
    {
        var readyBefore = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Ready")));
        var begun = HomeReducer.Reduce(readyBefore, new LifecycleOperationStarted(t0.AddMinutes(1), operationName));
        Check($"{operationName} start voids the qualification",
            begun.Kind != HomeKind.Ready && begun.Qualified is null && begun.NeedsQualification);
        Check($"chat cannot open once {operationName} has started",
            !ChatGate.CanOpen(begun, t0.AddMinutes(1)) && ChatGate.BeforeOpen(begun, t0.AddMinutes(1)) == ChatGateDecision.Requalify &&
            HomePresenter.Present(begun, t0.AddMinutes(1)).Primary != HomeAction.OpenChat);
        // The operation fails; the services it did not reach still answer liveness.
        var afterFailure = HomeReducer.Reduce(begun, Seen(t0.AddMinutes(2), Live()));
        Check($"a failed {operationName} is not Ready even while liveness passes",
            afterFailure.Kind != HomeKind.Ready && !ChatGate.CanOpen(afterFailure, t0.AddMinutes(2)) &&
            ChatGate.BeforeOpen(afterFailure, t0.AddMinutes(2)) == ChatGateDecision.Requalify);
        var afterUnobservable = HomeReducer.Reduce(begun, new EngineUnavailable(t0.AddMinutes(2), "exit 1", RepairNeeded: false));
        Check($"a failed {operationName} with no status is not Ready", afterUnobservable.Kind != HomeKind.Ready && !ChatGate.CanOpen(afterUnobservable, t0.AddMinutes(2)));
        var requalified = HomeReducer.Reduce(afterFailure, Seen(t0.AddMinutes(3), Status("Ready")));
        Check($"only fresh qualification restores Ready after {operationName}", requalified.Kind == HomeKind.Ready && ChatGate.CanOpen(requalified, t0.AddMinutes(3)));
    }
    var mainForm = File.ReadAllText(Path.Combine(root, "src", "AFKLocalAI.App", "MainForm.cs"));
    var provisioning = mainForm.IndexOf("private async Task RunProvisioningAsync(bool repair)", StringComparison.Ordinal);
    var invalidate = provisioning < 0 ? -1 : mainForm.IndexOf("new LifecycleOperationStarted(", provisioning, StringComparison.Ordinal);
    var launch = provisioning < 0 ? -1 : mainForm.IndexOf("_controller.Provision(repair)", provisioning, StringComparison.Ordinal);
    var firstAwait = provisioning < 0 ? -1 : mainForm.IndexOf("await ", provisioning, StringComparison.Ordinal);
    Check("setup and repair invalidate Ready before any work begins",
        provisioning >= 0 && invalidate > provisioning && invalidate < launch && invalidate < firstAwait,
        $"provisioning={provisioning} invalidate={invalidate} firstAwait={firstAwait} launch={launch}");

    // Onboarding is a separate fact, and chat is where it happens.
    var onboarding = HomeReducer.Reduce(HomeModel.Initial, Seen(t0, Status("Ready", onboarding: "true")));
    var onboardingView = HomePresenter.Present(onboarding, t0);
    Check("onboarding still routes to chat", onboardingView.Primary == HomeAction.OpenChat && ChatGate.CanOpen(onboarding, t0));
    Check("onboarding is explained", onboardingView.PrimaryLabel.Contains("account") && onboardingView.Detail.Contains("account"));

    Check("unknown engine actions fall back to diagnostics", HomePresenter.ActionFor("rm -rf") == HomeAction.Diagnostics && HomePresenter.ActionFor("open_chat") == HomeAction.Diagnostics);
    foreach (var url in new[] { "http://127.0.0.1:3000/", "http://localhost:3000/" })
        Check($"loopback chat url accepted: {url}", ChatGate.IsLoopbackChatUrl(url));
    foreach (var url in new[] { "https://127.0.0.1:3000/", "http://127.0.0.1:3001/", "http://192.168.1.5:3000/", "http://127.0.0.1.evil.example:3000/",
                 "http://user@127.0.0.1:3000/", "http://127.0.0.1:3000/?next=//evil", "http://127.0.0.1:3000/admin", "file:///C:/Windows/notepad.exe",
                 "javascript:alert(1)", "", "not a url" })
        Check($"non-chat url refused: {url}", !ChatGate.IsLoopbackChatUrl(url));

    Check("automatic full checks back off after a recent attempt", !RefreshPolicy.ShouldAutoQualify(liveOnly, t0.AddMinutes(-1), t0));
    Check("automatic full checks resume after the backoff", RefreshPolicy.ShouldAutoQualify(liveOnly, t0 - RefreshPolicy.AutoQualifyBackoff, t0));
    Check("ready state polls less often than transitional states",
        RefreshPolicy.LivenessInterval(HomeKind.Ready) > RefreshPolicy.LivenessInterval(HomeKind.Starting));
}

// ---------------------------------------------------------------- live output
// Regression: the runner used to ReadToEnd both streams, wait for exit, and only
// then replay stdout through the callback. Every line arrived after the work was
// over, which is why a real install watched an empty progress panel for minutes
// while winget and Docker did the actual work.
{
    var runner = new HiddenProcessRunner();
    var seen = new System.Collections.Concurrent.ConcurrentQueue<string>();
    var firstLineSeen = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);

    // Emits a line, then stays alive. If the callback only fires at exit, the
    // wait below times out.
    var script = "Write-Output 'AFK-LIVE-1'; Start-Sleep -Milliseconds 2500; Write-Output 'AFK-LIVE-2'";
    var spec = ProcessSpec.Hidden("powershell.exe",
        new[] { "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script }, root, "live");

    var run = runner.RunAsync(spec, line =>
    {
        seen.Enqueue(line);
        if (line.Contains("AFK-LIVE-1", StringComparison.Ordinal)) firstLineSeen.TrySetResult();
    });

    var arrivedEarly = await Task.WhenAny(firstLineSeen.Task, Task.Delay(TimeSpan.FromSeconds(2))) == firstLineSeen.Task;
    Check("output arrives before the child exits", arrivedEarly && !run.IsCompleted,
        $"early={arrivedEarly} processExited={run.IsCompleted}");

    var liveResult = await run;
    Check("streamed output is still captured in the result",
        liveResult.StandardOutput.Contains("AFK-LIVE-2", StringComparison.Ordinal));
    Check("each line is delivered exactly once",
        seen.Count(l => l.Contains("AFK-LIVE-1", StringComparison.Ordinal)) == 1,
        $"count={seen.Count(l => l.Contains("AFK-LIVE-1", StringComparison.Ordinal))}");
}

// Both streams are drained concurrently, so a chatty stderr cannot deadlock a
// parent that is only reading stdout.
{
    var runner = new HiddenProcessRunner();
    var script = "1..200 | ForEach-Object { Write-Output \"out-$_\"; [Console]::Error.WriteLine(\"err-$_\") }";
    var spec = ProcessSpec.Hidden("powershell.exe",
        new[] { "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script }, root, "both-streams");
    var both = runner.RunAsync(spec);
    var finished = await Task.WhenAny(both, Task.Delay(TimeSpan.FromSeconds(60))) == both;
    Check("concurrent stdout and stderr do not deadlock", finished);
    if (finished)
    {
        var r = await both;
        Check("stdout fully captured", r.StandardOutput.Contains("out-200", StringComparison.Ordinal));
        Check("stderr fully captured", r.StandardError.Contains("err-200", StringComparison.Ordinal));
    }
}

// Cancellation stops the wait and does not hang.
{
    var runner = new HiddenProcessRunner();
    using var cts = new CancellationTokenSource();
    var spec = ProcessSpec.Hidden("powershell.exe",
        new[] { "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", "Start-Sleep -Seconds 120" }, root, "cancel");
    var pending = runner.RunAsync(spec, null, cts.Token);
    cts.CancelAfter(TimeSpan.FromMilliseconds(300));
    var cancelled = false;
    try { await pending; } catch (OperationCanceledException) { cancelled = true; }
    Check("cancellation is observed", cancelled);
}

// Reproduced: a Windows PowerShell child that inherits PowerShell 7's module path
// cannot resolve its own cmdlets. With the spec's environment removal it can.
{
    var runner = new HiddenProcessRunner();
    var previous = Environment.GetEnvironmentVariable("PSModulePath");
    var pwshModules = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "PowerShell", "7", "Modules");
    Environment.SetEnvironmentVariable("PSModulePath", pwshModules + ";" + (previous ?? ""));
    try
    {
        var probe = "try { (Get-Command Get-FileHash -ErrorAction Stop).Source } catch { 'MISSING' }";
        var arguments = new[] { "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", probe };
        var isolated = await runner.RunAsync(ProcessSpec.Hidden(ProvisioningController.WindowsPowerShellPath(), arguments, root, "isolated", new[] { "PSModulePath" }));
        Check("Windows PowerShell children resolve their own cmdlets", isolated.StandardOutput.Contains("Microsoft.PowerShell.Utility"), isolated.StandardOutput);
    }
    finally
    {
        Environment.SetEnvironmentVariable("PSModulePath", previous);
    }
}

// A missing executable is an exception the shell can catch - not a hang or a crash.
{
    var runner = new HiddenProcessRunner();
    var spec = ProcessSpec.Hidden(Path.Combine(root, "no-such-runtime", "python.exe"), Array.Empty<string>(), root, "missing");
    var threw = false;
    try { await runner.RunAsync(spec); } catch (System.ComponentModel.Win32Exception) { threw = true; }
    Check("a missing runtime surfaces as a catchable launch failure", threw);
}

Console.WriteLine();
if (failures.Count > 0)
{
    Console.Error.WriteLine($"AFK LocalAI core tests failed: {failures.Count}");
    foreach (var failure in failures) Console.Error.WriteLine($"  - {failure}");
    return 1;
}
Console.WriteLine($"AFK LocalAI core tests passed: {passed}");
return 0;

static void WriteManifest(string programRoot, IReadOnlyDictionary<string, string> files)
{
    var rows = new List<string>();
    foreach (var (relative, content) in files)
    {
        var path = Path.Combine(programRoot, relative.Replace('/', Path.DirectorySeparatorChar));
        Directory.CreateDirectory(Path.GetDirectoryName(path)!);
        File.WriteAllText(path, content);
        var digest = Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(content)));
        rows.Add($$"""{"path":"{{relative}}","size":{{content.Length}},"sha256":"{{digest}}"}""");
    }
    File.WriteAllText(Path.Combine(programRoot, "payload-manifest.json"), $$"""{"schema_version":1,"files":[{{string.Join(",", rows)}}]}""");
}
