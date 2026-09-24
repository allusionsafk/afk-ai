namespace AFKLocalAI.App;

public enum StatusTone { Ready, Working, Attention, Blocked, Neutral }

/// <summary>A component state as a person reads it: a symbol, a word and a tone.</summary>
public sealed record StatusText(string Symbol, string Label, StatusTone Tone);

/// <summary>
/// Translates the engine's machine states into plain words. It never upgrades a
/// state: an unrecognised or unknown value is shown as unknown, and the raw code
/// stays in the setup details and diagnostics.
/// </summary>
public static class StatusPresentation
{
    private static readonly StatusText Unknown = new("?", "Unknown", StatusTone.Neutral);

    public static StatusText ForState(string? raw)
    {
        var value = (raw ?? "").Trim().Replace(' ', '_').ToUpperInvariant();
        return value switch
        {
            "READY" or "DOCKER_HEALTHY_LOCAL" or "LIVE" => new("✓", "Ready", StatusTone.Ready),
            "SUPPORTED" => new("✓", "Supported", StatusTone.Ready),
            "NOT_REQUIRED_FOR_CURRENT_HEALTHY_BACKEND" or "NOTREQUIRED" or "NOT_REQUIRED" => new("–", "Not needed", StatusTone.Neutral),
            "CHECKING" or "PENDING" or "STARTING" or "DOCKER_STARTING" => new("…", value == "PENDING" ? "Waiting" : value.EndsWith("STARTING") ? "Starting" : "Checking", StatusTone.Working),
            "CHECKED_DURING_SETUP" => new("–", "At setup", StatusTone.Neutral),
            "STOPPED" => new("○", "Stopped", StatusTone.Neutral),
            "DOCKER_INSTALLED_NOT_RUNNING" => new("!", "Not running", StatusTone.Attention),
            "DOCKER_NOT_INSTALLED" or "WSL_NOT_INSTALLED" or "NOTINSTALLED" or "NOT_INSTALLED" => new("!", "Not installed", StatusTone.Attention),
            "DOCKER_CONTEXT_REMOTE" => new("!", "Uses a remote engine", StatusTone.Attention),
            "DOCKER_LINUX_ENGINE_REQUIRED" => new("!", "Needs Linux containers", StatusTone.Attention),
            "DOCKER_VERSION_UNSUPPORTED" or "WSL_UPDATE_REQUIRED" => new("!", "Update needed", StatusTone.Attention),
            "WSL_UNHEALTHY" => new("!", "Not healthy", StatusTone.Attention),
            "WINDOWS_VIRTUALIZATION_REBOOT_REQUIRED" => new("!", "Restart needed", StatusTone.Attention),
            "WINDOWS_VIRTUALIZATION_FEATURE_MISSING" => new("!", "Turned off", StatusTone.Attention),
            "WINDOWS_HYPERVISOR_NOT_RUNNING" => new("!", "Hypervisor off", StatusTone.Attention),
            "DEGRADED" => new("!", "Degraded", StatusTone.Attention),
            "FIRMWARE_VIRTUALIZATION_DISABLED" => new("×", "Off in firmware", StatusTone.Blocked),
            "UNSUPPORTED_PLATFORM" => new("×", "Not supported", StatusTone.Blocked),
            "FAILED" => new("×", "Failed", StatusTone.Blocked),
            _ => Unknown
        };
    }

    /// <summary>
    /// The engine sends its message pre-wrapped for a console. Joins a line to the
    /// previous one unless the previous one ended a sentence, so the words wrap to
    /// the window instead of breaking mid-sentence.
    /// </summary>
    public static string Reflow(IEnumerable<string> lines)
    {
        var paragraphs = new List<string>();
        foreach (var raw in lines)
        {
            var line = raw.Trim();
            if (line.Length == 0) continue;
            if (paragraphs.Count > 0 && !EndsSentence(paragraphs[^1])) paragraphs[^1] += " " + line;
            else paragraphs.Add(line);
        }
        return string.Join(Environment.NewLine, paragraphs);
    }

    private static bool EndsSentence(string text) => text.Length > 0 && text[^1] is '.' or '!' or '?' or ':';

    /// <summary>The tone of a Home state, for the one-word status above the headline.</summary>
    public static StatusText ForHome(HomeKind kind) => kind switch
    {
        HomeKind.Ready => new("✓", "Ready", StatusTone.Ready),
        HomeKind.Starting => new("…", "Starting", StatusTone.Working),
        HomeKind.Checking => new("…", "Checking", StatusTone.Working),
        HomeKind.Stopped => new("○", "Stopped", StatusTone.Neutral),
        HomeKind.Degraded => new("!", "Needs attention", StatusTone.Attention),
        HomeKind.RepairNeeded => new("!", "Needs repair", StatusTone.Attention),
        HomeKind.Failed => new("×", "Not working", StatusTone.Blocked),
        _ => new("?", "Unknown", StatusTone.Neutral)
    };
}
