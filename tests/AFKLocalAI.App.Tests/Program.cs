using AFKLocalAI.App;
using System.Diagnostics;

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

    var programRoot = Path.Combine(scratch, "Program");
    var dataRoot = Path.Combine(scratch, "Data");
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

    var store = new ProvisioningStateStore(paths);
    var state = store.LoadOrCreate();
    state.Usable = true;
    state.LastReasonCode = "PREFLIGHT-READY";
    store.SaveAtomic(state);
    var roundTrip = store.LoadOrCreate();
    Check("state roundtrip preserves usable", roundTrip.Usable);
    Check("state roundtrip preserves reason", roundTrip.LastReasonCode == "PREFLIGHT-READY", roundTrip.LastReasonCode ?? "null");

    File.WriteAllText(paths.ProvisioningStatePath, "{ definitely not json");
    var recovered = store.LoadOrCreate();
    Check("corrupt state recovers safely", recovered.SchemaVersion == ProvisioningState.CurrentSchemaVersion);
    Check("corrupt state is quarantined", Directory.EnumerateFiles(paths.StateRoot, "provisioning-state.corrupt-*.json").Any());

    var eventLine = """AFK-EVENT:{"schema_version":1,"timestamp_utc":"2026-09-08T12:00:00Z","event_type":"phase-start","phase":"python","status":"running","code":"phase-start","message":"Starting Python."}""";
    Check("structured event parses", ProvisioningEvent.TryParse(eventLine, out var parsedEvent));
    Check("event phase retained", parsedEvent?.Phase == "python", parsedEvent?.Phase ?? "null");
    Check("ordinary console line is ignored", !ProvisioningEvent.TryParse("Installing Python...", out _));

    Check("docker absent maps to install", RecoveryAction.ForCode("PREFLIGHT-DOCKER-NOT-INSTALLED").ActionId == "install-docker");
    Check("remote context is guidance only", !RecoveryAction.ForCode("PREFLIGHT-DOCKER-REMOTE-CONTEXT").Automatic);
    Throws<ArgumentOutOfRangeException>("unknown recovery mapping fails closed", () => RecoveryAction.ForCode("PREFLIGHT-NOT-REAL"));

    var controller = new ProvisioningController(paths);
    var specs = new[]
    {
        controller.Preflight(),
        controller.Recovery("PREFLIGHT-DOCKER-NOT-INSTALLED", "install-docker"),
        controller.Provision(),
        controller.Start(),
        controller.Stop(),
        controller.Health(),
        controller.Diagnostics()
    };
    foreach (var spec in specs)
    {
        var startInfo = spec.CreateStartInfo();
        Check($"{spec.Purpose} hides console", startInfo.CreateNoWindow && !startInfo.UseShellExecute &&
            startInfo.WindowStyle == ProcessWindowStyle.Hidden, spec.FileName);
    }
    Check("preflight uses inbox Windows PowerShell", controller.Preflight().FileName.Equals("powershell.exe", StringComparison.OrdinalIgnoreCase));
    Check("preflight requests JSON", controller.Preflight().Arguments.Contains("-Json"));
    Check("provision requests event stream", controller.Provision().Arguments.Contains("-EventStream"));

    // Regression: every Python-facing command used to be "py -3.12 -m localai <cmd>",
    // which resolves through the ambient `localai` package name. On a machine that
    // also has the private engineering workbench installed editable, Stop tore down
    // that workbench's stack and force-closed Docker Desktop and Ollama for the whole
    // machine; Start and Health quietly operated on, and reported about, that same
    // foreign checkout. All three must run THIS installation's own payload by path.
    foreach (var (name, spec) in new[]
             {
                 ("start", controller.Start()),
                 ("stop", controller.Stop()),
                 ("health", controller.Health())
             })
    {
        var arguments = spec.Arguments;
        var rendered = string.Join(" ", arguments);
        Check($"{name} never invokes the ambient localai package",
            !arguments.Contains("localai") && !arguments.Contains("-m") && !arguments.Contains("-3.12"),
            rendered);
        Check($"{name} runs the verified payload entry point",
            arguments.Any(argument => argument.EndsWith(
                Path.Combine("installer", "afk-payload.py"), StringComparison.OrdinalIgnoreCase)),
            rendered);
        Check($"{name} names the command explicitly", arguments.Contains(name), rendered);
        // Regression: importing the payload writes __pycache__ into the program
        // directory. Setup never installed those files, so its uninstaller never
        // removes them - and the whole installation survives the uninstall.
        Check($"{name} writes no bytecode into the installation",
            arguments.Contains("-B"), rendered);
        Check($"{name} passes this installation's program root",
            arguments.Contains("--program-root") &&
            arguments[arguments.ToList().IndexOf("--program-root") + 1] == paths.ProgramRoot,
            rendered);
    }

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

    Check("fresh state opens setup mode", AppModeResolver.Resolve(new ProvisioningState()) == AppMode.Setup);
    Check("usable state opens home mode", AppModeResolver.Resolve(new ProvisioningState { Usable = true }) == AppMode.Home);
    Check("single-instance mutex is version-independent", AppIdentity.SingleInstanceMutexName == "Local\\AFKLocalAI-8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0");

    using var icon = AppIcon.Create();
    Check("application icon is available", icon.Width >= 32 && icon.Height >= 32, $"{icon.Width}x{icon.Height}");
    Check("shell exposes accessible setup labels", MainForm.AccessibilityContract.Contains("Prerequisite status") &&
        MainForm.AccessibilityContract.Contains("Setup progress"));
    Check("shell uses the hidden process runner", MainForm.ProcessRunnerType == typeof(HiddenProcessRunner));

    Directory.CreateDirectory(Path.Combine(programRoot, "installer"));
    File.WriteAllText(Path.Combine(programRoot, "installer", "Get-Preflight.ps1"), "# self-test fixture");
    File.WriteAllText(Path.Combine(programRoot, "installer", "Invoke-Recovery.ps1"), "# self-test fixture");
    var selfTest = SelfTestRunner.Run(paths, product);
    Check("shell self-test succeeds", selfTest.Success, string.Join(" | ", selfTest.Checks.Where(pair => !pair.Value).Select(pair => pair.Key)));
    Check("shell self-test serializes as JSON", selfTest.ToJson().Contains("\"success\":true", StringComparison.Ordinal));
}
finally
{
    if (Directory.Exists(scratch)) Directory.Delete(scratch, recursive: true);
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

Console.WriteLine();
if (failures.Count > 0)
{
    Console.Error.WriteLine($"AFK LocalAI core tests failed: {failures.Count}");
    foreach (var failure in failures) Console.Error.WriteLine($"  - {failure}");
    return 1;
}
Console.WriteLine($"AFK LocalAI core tests passed: {passed}");
return 0;
