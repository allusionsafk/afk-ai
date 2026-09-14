using System.Diagnostics;
using System.Text;
using System.Text.Json;

namespace AFKLocalAI.App;

public enum AppMode { Setup, Home }

public static class AppModeResolver
{
    public static AppMode Resolve(ProvisioningState state) => state.Usable ? AppMode.Home : AppMode.Setup;
}

public static class AppIdentity
{
    public const string SingleInstanceMutexName = @"Local\AFKLocalAI-8A8A2D4D-CE75-4A2D-A39B-56B4206F93D0";
}

public static class CommandLineFailurePolicy
{
    public static bool ShouldShowDialog(IEnumerable<string> arguments) =>
        !arguments.Any(argument =>
            argument.Equals("--silent", StringComparison.OrdinalIgnoreCase) ||
            argument.Equals("--self-test", StringComparison.OrdinalIgnoreCase));

    public static string Format(Exception exception) =>
        $"AFK LocalAI could not start: {exception}";
}

public sealed record CommandLineOptions(
    bool SelfTest = false,
    bool Diagnostics = false,
    bool DataFolder = false,
    bool About = false,
    bool Stop = false,
    bool Silent = false,
    string? DataRoot = null)
{
    public static CommandLineOptions Parse(string[] arguments)
    {
        var result = new CommandLineOptions();
        for (var index = 0; index < arguments.Length; index++)
        {
            var argument = arguments[index];
            result = argument switch
            {
                "--self-test" => result with { SelfTest = true },
                "--diagnostics" => result with { Diagnostics = true },
                "--data-folder" => result with { DataFolder = true },
                "--about" => result with { About = true },
                "--stop" => result with { Stop = true },
                "--silent" => result with { Silent = true },
                "--data-root" when index + 1 < arguments.Length =>
                    result with { DataRoot = Path.GetFullPath(arguments[++index]) },
                _ => throw new ArgumentException($"Unknown or incomplete AFK LocalAI option '{argument}'.")
            };
        }
        return result;
    }
}

public sealed record SelfTestSummary(
    string Product,
    string Version,
    bool Success,
    IReadOnlyDictionary<string, bool> Checks)
{
    public string ToJson() => JsonSerializer.Serialize(this, new JsonSerializerOptions
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower
    });
}

public static class SelfTestRunner
{
    public static SelfTestSummary Run(AppPaths paths, ProductInfo product)
    {
        var checks = new SortedDictionary<string, bool>(StringComparer.Ordinal)
        {
            ["architecture_x64"] = Environment.Is64BitProcess,
            ["metadata_valid"] = product.ProductName == "AFK LocalAI" && product.ExecutableName == "AFKLocalAI.exe",
            ["data_outside_program"] = !paths.DataRoot.StartsWith(paths.ProgramRoot, StringComparison.OrdinalIgnoreCase),
            ["preflight_present"] = File.Exists(Path.Combine(paths.ProgramRoot, "installer", "Get-Preflight.ps1")),
            ["recovery_present"] = File.Exists(Path.Combine(paths.ProgramRoot, "installer", "Invoke-Recovery.ps1"))
        };
        try
        {
            var store = new ProvisioningStateStore(paths);
            var state = store.LoadOrCreate();
            store.SaveAtomic(state);
            checks["state_roundtrip"] = store.LoadOrCreate().SchemaVersion == ProvisioningState.CurrentSchemaVersion;
        }
        catch { checks["state_roundtrip"] = false; }

        var controller = new ProvisioningController(paths);
        foreach (var spec in new[] { controller.Preflight(), controller.Recovery("PREFLIGHT-READY", "retry"), controller.Provision() })
        {
            var info = spec.CreateStartInfo();
            checks[$"{spec.Purpose}_hidden"] =
                info.CreateNoWindow && !info.UseShellExecute && info.WindowStyle == ProcessWindowStyle.Hidden;
        }
        return new SelfTestSummary(product.ProductName, product.DisplayVersion, checks.Values.All(value => value), checks);
    }
}

internal static class Program
{
    [STAThread]
    private static int Main(string[] arguments)
    {
        var showFailureDialog = CommandLineFailurePolicy.ShouldShowDialog(arguments);
        try
        {
            var options = CommandLineOptions.Parse(arguments);
            var product = ProductInfo.Load(Path.Combine(AppContext.BaseDirectory, "version.json"));
            var paths = AppPaths.ForCurrentUser(options.DataRoot);

            if (options.SelfTest)
            {
                var summary = SelfTestRunner.Run(paths, product);
                WriteStandardOutput(summary.ToJson());
                return summary.Success ? 0 : 1;
            }
            if (options.Stop)
            {
                var result = new HiddenProcessRunner().RunAsync(new ProvisioningController(paths).Stop()).GetAwaiter().GetResult();
                return result.ExitCode;
            }
            if (options.Diagnostics || options.DataFolder)
            {
                paths.EnsureUserDirectories();
                if (options.Diagnostics) CreateDiagnostics(paths, product);
                OpenPath(options.Diagnostics ? paths.DiagnosticsRoot : paths.DataRoot);
                return 0;
            }

            using var mutex = new Mutex(initiallyOwned: true, AppIdentity.SingleInstanceMutexName, out var created);
            if (!created) return 0;
            ApplicationConfiguration.Initialize();
            using var icon = AppIcon.Create();
            Application.Run(new MainForm(paths, product, options.About, icon));
            return 0;
        }
        catch (Exception exception)
        {
            if (showFailureDialog)
                MessageBox.Show(
                    $"AFK LocalAI could not start.\n\n{exception.Message}",
                    "AFK LocalAI",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error);
            else
                WriteStandardError(CommandLineFailurePolicy.Format(exception));
            return 1;
        }
    }

    private static void OpenPath(string path) =>
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true });

    private static void CreateDiagnostics(AppPaths paths, ProductInfo product)
    {
        var controller = new ProvisioningController(paths);
        var result = new HiddenProcessRunner().RunAsync(controller.Diagnostics()).GetAwaiter().GetResult();
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var content = result.StandardOutput + Environment.NewLine + result.StandardError;
        if (!string.IsNullOrWhiteSpace(profile))
            content = content.Replace(profile, "%USERPROFILE%", StringComparison.OrdinalIgnoreCase);
        var report = Path.Combine(paths.DiagnosticsRoot, $"AFKLocalAI-Diagnostics-{DateTime.UtcNow:yyyyMMddTHHmmssZ}.txt");
        File.WriteAllText(report, $"AFK LocalAI {product.DisplayVersion}{Environment.NewLine}{content}");
    }

    private static void WriteStandardOutput(string value)
    {
        WriteStandardStream(Console.OpenStandardOutput(), value);
    }

    private static void WriteStandardError(string value)
    {
        WriteStandardStream(Console.OpenStandardError(), value);
    }

    private static void WriteStandardStream(Stream stream, string value)
    {
        var bytes = Encoding.UTF8.GetBytes(value + Environment.NewLine);
        using (stream)
        {
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush();
        }
    }
}
