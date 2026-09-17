using System.Text.Json;
using System.Text.Json.Nodes;

namespace AFKLocalAI.App;

/// <summary>Creates the support report, with a privacy-bounded fallback.</summary>
/// <remarks>
/// The engine builds the real report from allowlisted structured facts and
/// never copies chats, prompts, documents, credentials or other projects'
/// inventories. When the engine itself cannot run (its runtime is missing or
/// corrupt) the shell writes a smaller report from facts it holds directly:
/// version, which program files failed verification, and the Home state.
/// </remarks>
public static class DiagnosticsWriter
{
    public static async Task<string> CreateAsync(
        AppPaths paths,
        ProductInfo product,
        ProvisioningController controller,
        HiddenProcessRunner runner,
        IntegrityResult integrity,
        HomeModel? home,
        CancellationToken cancellationToken)
    {
        paths.EnsureUserDirectories();
        var file = Path.Combine(paths.DiagnosticsRoot, $"AFKAI-Diagnostics-{DateTime.UtcNow:yyyyMMddTHHmmssZ}.json");
        string? engineProblem = null;
        if (integrity.Intact)
        {
            try
            {
                using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
                timeout.CancelAfter(TimeSpan.FromMinutes(3));
                var result = await runner.RunAsync(controller.Diagnostics(file), null, timeout.Token);
                if (result.ExitCode == 0 && File.Exists(file)) return file;
                engineProblem = $"engine exited {result.ExitCode}";
            }
            catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
            {
                engineProblem = "engine timed out";
            }
            catch (Exception exception) when (exception is System.ComponentModel.Win32Exception or InvalidOperationException or IOException)
            {
                engineProblem = "engine could not start";
            }
        }
        else
        {
            engineProblem = "installation failed verification";
        }

        var fallback = BuildFallback(product, integrity, home, engineProblem);
        await File.WriteAllTextAsync(file, Redact(fallback.ToJsonString(new JsonSerializerOptions { WriteIndented = true })), cancellationToken);
        return file;
    }

    public static JsonObject BuildFallback(ProductInfo product, IntegrityResult integrity, HomeModel? home, string? engineProblem)
    {
        static JsonArray First(IEnumerable<string> values) => new(values.Take(10).Select(v => (JsonNode)JsonValue.Create(v)!).ToArray());
        return new JsonObject
        {
            ["schema_version"] = 1,
            ["produced_by"] = "shell-fallback",
            ["generated_at_utc"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"),
            ["privacy"] = new JsonObject { ["excluded"] = new JsonArray("chat content", "prompts and generated text", "documents and file contents", "credentials, secrets and tokens", "browser data") },
            ["classification"] = integrity.Intact ? "unknown" : "corrupt_installation",
            ["engine_problem"] = engineProblem,
            ["product"] = new JsonObject { ["display_version"] = product.DisplayVersion, ["channel"] = product.Channel },
            ["installation"] = new JsonObject
            {
                ["manifest_present"] = integrity.ManifestPresent,
                ["interpreter_present"] = integrity.InterpreterPresent,
                ["intact"] = integrity.Intact,
                ["missing_count"] = integrity.Missing.Count,
                ["mismatched_count"] = integrity.Mismatched.Count,
                ["unexpected_count"] = integrity.Unexpected.Count,
                ["missing"] = First(integrity.Missing),
                ["mismatched"] = First(integrity.Mismatched),
                ["unexpected"] = First(integrity.Unexpected)
            },
            ["home"] = home is null ? null : new JsonObject
            {
                ["kind"] = home.Kind.ToString(),
                ["reason"] = home.Latest?.Reason,
                ["state"] = home.Latest?.State
            }
        };
    }

    public static string Redact(string text)
    {
        foreach (var folder in new[]
                 {
                     Environment.SpecialFolder.LocalApplicationData,
                     Environment.SpecialFolder.ApplicationData,
                     Environment.SpecialFolder.UserProfile
                 })
        {
            var value = Environment.GetFolderPath(folder);
            if (string.IsNullOrWhiteSpace(value) || value.Length < 4) continue;
            var label = folder switch
            {
                Environment.SpecialFolder.LocalApplicationData => "%LOCALAPPDATA%",
                Environment.SpecialFolder.ApplicationData => "%APPDATA%",
                _ => "%USERPROFILE%"
            };
            text = text.Replace(value, label, StringComparison.OrdinalIgnoreCase)
                       .Replace(value.Replace("\\", "\\\\"), label, StringComparison.OrdinalIgnoreCase);
        }
        return text;
    }
}
