using System.Security.Cryptography;
using System.Text.Json;

namespace AFKLocalAI.App;

public sealed record IntegrityResult(
    bool ManifestPresent,
    bool InterpreterPresent,
    IReadOnlyList<string> Missing,
    IReadOnlyList<string> Mismatched,
    IReadOnlyList<string> Unexpected)
{
    public bool Intact => ManifestPresent && InterpreterPresent && Missing.Count == 0 && Mismatched.Count == 0 && Unexpected.Count == 0;

    public string Summary => !ManifestPresent
        ? "AFK AI's installation record is missing."
        : !InterpreterPresent
            ? "AFK AI's own runtime is missing."
            : $"{Missing.Count} missing, {Mismatched.Count} changed and {Unexpected.Count} unexpected program file(s).";
}

/// <summary>
/// Checks the files the shell is about to EXECUTE against the installed
/// <c>payload-manifest.json</c>, before executing them.
/// </summary>
/// <remarks>
/// The engine can report on the rest of the installation, but a truncated
/// interpreter DLL or a half-replaced entry point cannot be trusted to judge
/// itself. A partial update, an interrupted repair, or files removed by hand
/// therefore surface as "needs repair" instead of as a crash or a guess. This
/// detects corruption and mismatched installs; it is not a defence against
/// malware already running as this user (the manifest sits beside the files).
/// </remarks>
public static class InstallationIntegrity
{
    /// <summary>Trees whose every file must match the manifest, and nothing more.</summary>
    public static readonly string[] ExecutedTrees = { "runtime", "src/localai", "installer" };

    public static IntegrityResult Verify(string programRoot)
    {
        var root = Path.GetFullPath(programRoot);
        var interpreterPresent = File.Exists(Path.Combine(root, "runtime", "python", "python.exe"));
        var manifestPath = Path.Combine(root, "payload-manifest.json");
        Dictionary<string, string> expected;
        try
        {
            expected = ReadManifest(manifestPath);
        }
        catch (Exception exception) when (exception is IOException or UnauthorizedAccessException or JsonException or InvalidDataException)
        {
            return new IntegrityResult(false, interpreterPresent, Array.Empty<string>(), Array.Empty<string>(), Array.Empty<string>());
        }

        var missing = new List<string>();
        var mismatched = new List<string>();
        foreach (var (relative, digest) in expected.OrderBy(pair => pair.Key, StringComparer.OrdinalIgnoreCase))
        {
            if (!ExecutedTrees.Any(tree => relative.StartsWith(tree + "/", StringComparison.OrdinalIgnoreCase))) continue;
            var path = Path.Combine(root, relative.Replace('/', Path.DirectorySeparatorChar));
            if (!File.Exists(path)) { missing.Add(relative); continue; }
            try
            {
                using var stream = File.OpenRead(path);
                if (!string.Equals(Convert.ToHexString(SHA256.HashData(stream)), digest, StringComparison.OrdinalIgnoreCase))
                    mismatched.Add(relative);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                mismatched.Add(relative);
            }
        }

        var unexpected = new List<string>();
        foreach (var tree in ExecutedTrees)
        {
            var directory = Path.Combine(root, tree.Replace('/', Path.DirectorySeparatorChar));
            if (!Directory.Exists(directory)) continue;
            foreach (var file in Directory.EnumerateFiles(directory, "*", SearchOption.AllDirectories))
            {
                var relative = Path.GetRelativePath(root, file).Replace(Path.DirectorySeparatorChar, '/');
                if (relative.Split('/').Contains("__pycache__")) continue;
                if (!expected.ContainsKey(relative)) unexpected.Add(relative);
            }
        }
        unexpected.Sort(StringComparer.OrdinalIgnoreCase);
        return new IntegrityResult(true, interpreterPresent, missing, mismatched, unexpected);
    }

    private static Dictionary<string, string> ReadManifest(string path)
    {
        using var document = JsonDocument.Parse(File.ReadAllText(path));
        if (!document.RootElement.TryGetProperty("files", out var files) || files.ValueKind != JsonValueKind.Array)
            throw new InvalidDataException("Manifest has no file list.");
        var expected = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        foreach (var entry in files.EnumerateArray())
        {
            if (entry.ValueKind != JsonValueKind.Object) continue;
            var relative = entry.TryGetProperty("path", out var p) && p.ValueKind == JsonValueKind.String ? p.GetString() : null;
            var digest = entry.TryGetProperty("sha256", out var d) && d.ValueKind == JsonValueKind.String ? d.GetString() : null;
            if (string.IsNullOrWhiteSpace(relative) || string.IsNullOrWhiteSpace(digest)) continue;
            relative = relative.Replace('\\', '/');
            var parts = relative.Split('/');
            if (Path.IsPathRooted(relative) || parts.Contains("..") || parts.Contains("") || parts[0].Contains(':')) continue;
            expected[relative] = digest;
        }
        return expected;
    }
}
