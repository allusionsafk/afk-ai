using System.Text.Json;
using System.Text.Json.Serialization;

namespace AFKLocalAI.App;

public sealed class ProvisioningEvent
{
    public const string Prefix = "AFK-EVENT:";
    [JsonPropertyName("schema_version")] public int SchemaVersion { get; init; }
    [JsonPropertyName("timestamp_utc")] public DateTimeOffset TimestampUtc { get; init; }
    [JsonPropertyName("event_type")] public string EventType { get; init; } = "";
    [JsonPropertyName("phase")] public string Phase { get; init; } = "";
    [JsonPropertyName("status")] public string Status { get; init; } = "";
    [JsonPropertyName("code")] public string Code { get; init; } = "";
    [JsonPropertyName("message")] public string Message { get; init; } = "";

    public static bool TryParse(string line, out ProvisioningEvent? provisioningEvent)
    {
        provisioningEvent = null;
        if (string.IsNullOrWhiteSpace(line) || !line.StartsWith(Prefix, StringComparison.Ordinal)) return false;
        try
        {
            provisioningEvent = JsonSerializer.Deserialize<ProvisioningEvent>(line[Prefix.Length..]);
            return provisioningEvent is { SchemaVersion: 1 } && !string.IsNullOrWhiteSpace(provisioningEvent.Phase);
        }
        catch (JsonException)
        {
            provisioningEvent = null;
            return false;
        }
    }
}

public sealed record RecoveryAction(
    string ActionId,
    string Label,
    bool RequiresElevation,
    bool Automatic,
    string Explanation)
{
    public static RecoveryAction ForCode(string code) => code switch
    {
        "PREFLIGHT-READY" => new("retry", "Continue", false, true, "Check again and continue."),
        "PREFLIGHT-UNSUPPORTED-PLATFORM" => new("support-guidance", "View supported systems", false, false, "AFK LocalAI requires 64-bit Windows 11."),
        "PREFLIGHT-UNKNOWN" => new("support-guidance", "Open diagnostics", false, false, "No automatic change is safe with incomplete evidence."),
        "PREFLIGHT-FIRMWARE-VIRT-DISABLED" => new("firmware-guidance", "Show virtualization instructions", false, false, "Firmware guidance only."),
        "PREFLIGHT-WINDOWS-FEATURE-MISSING" => new("install-wsl", "Set up Windows virtualization", true, true, "Enable the supported Windows WSL path."),
        "PREFLIGHT-WINDOWS-REBOOT-REQUIRED" => new("restart-windows", "Restart Windows", true, false, "Restart to finish Windows changes."),
        "PREFLIGHT-HYPERVISOR-NOT-RUNNING" => new("restart-windows", "Restart Windows", true, false, "Restart to activate configured virtualization."),
        "PREFLIGHT-WSL-NOT-INSTALLED" => new("install-wsl", "Install WSL components", true, true, "Install WSL without a distribution."),
        "PREFLIGHT-WSL-UPDATE-REQUIRED" => new("update-wsl", "Update WSL", true, true, "Update the Windows WSL runtime."),
        "PREFLIGHT-WSL-UNHEALTHY" => new("update-wsl", "Repair WSL runtime", true, true, "Update the unhealthy WSL runtime."),
        "PREFLIGHT-DOCKER-REMOTE-CONTEXT" => new("docker-context-guidance", "Show Docker context guidance", false, false, "Preserve advanced remote Docker configuration."),
        "PREFLIGHT-DOCKER-LINUX-ENGINE-REQUIRED" => new("docker-linux-guidance", "Show Linux engine guidance", false, false, "A local Linux engine is required."),
        "PREFLIGHT-DOCKER-VERSION-UNSUPPORTED" => new("update-docker", "Update Docker Desktop", false, true, "Update Docker Desktop."),
        "PREFLIGHT-DOCKER-NOT-INSTALLED" => new("install-docker", "Install Docker Desktop", false, true, "Install Docker Desktop."),
        "PREFLIGHT-DOCKER-STARTING" => new("retry", "Check again", false, true, "Docker Desktop is still starting."),
        "PREFLIGHT-DOCKER-NOT-RUNNING" => new("start-docker", "Start Docker Desktop", false, true, "Start Docker Desktop."),
        _ => throw new ArgumentOutOfRangeException(nameof(code), code, "Unknown preflight reason code.")
    };
}

public sealed class ProvisioningState
{
    public const int CurrentSchemaVersion = 2;
    [JsonPropertyName("schema_version")] public int SchemaVersion { get; init; } = CurrentSchemaVersion;
    [JsonPropertyName("usable")] public bool Usable { get; set; }
    [JsonPropertyName("last_reason_code")] public string? LastReasonCode { get; set; }
    [JsonPropertyName("last_phase")] public string? LastPhase { get; set; }
    [JsonPropertyName("updated_at_utc")] public DateTimeOffset UpdatedAtUtc { get; set; } = DateTimeOffset.UtcNow;
}

public sealed class PreflightSummary
{
    [JsonPropertyName("schema_version")] public int SchemaVersion { get; init; }
    [JsonPropertyName("overall")] public string Overall { get; init; } = "";
    [JsonPropertyName("code")] public string Code { get; init; } = "";
    [JsonPropertyName("action")] public string? Action { get; init; }
    [JsonPropertyName("reboot_required")] public bool RebootRequired { get; init; }
    [JsonPropertyName("user_message")] public string[] UserMessage { get; init; } = Array.Empty<string>();
    [JsonPropertyName("diagnostics")] public string[] Diagnostics { get; init; } = Array.Empty<string>();
    [JsonPropertyName("components")] public Dictionary<string, JsonElement> Components { get; init; } = new();
}
