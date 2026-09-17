using System.Text.Json;
using System.Text.Json.Serialization;

namespace AFKLocalAI.App;

/// <summary>
/// The engine's product status (readiness schema 2), exactly as
/// <c>afk-payload.py status --json</c> reports it.
/// </summary>
/// <remarks>
/// The shell renders this; it does not re-derive readiness from services,
/// ports or exit codes. Anything that does not parse as a well-formed schema-2
/// status is treated by callers as "could not observe", never as a verdict.
/// </remarks>
public sealed class ProductStatus
{
    public static class States
    {
        public const string Ready = "Ready";
        public const string Live = "Live";
        public const string Starting = "Starting";
        public const string Stopped = "Stopped";
        public const string Degraded = "Degraded";
        public const string Failed = "Failed";
        public const string NotInstalled = "NotInstalled";
        public const string Unknown = "Unknown";

        public static readonly IReadOnlySet<string> All = new HashSet<string>(StringComparer.Ordinal)
        {
            Ready, Live, Starting, Stopped, Degraded, Failed, NotInstalled, Unknown
        };
    }

    public const string ModeQualify = "qualify";
    public const string ModeLiveness = "liveness";

    [JsonPropertyName("schema_version")] public int SchemaVersion { get; init; }
    [JsonPropertyName("observed_at_utc")] public string? ObservedAtUtc { get; init; }
    [JsonPropertyName("mode")] public string Mode { get; init; } = "";
    [JsonPropertyName("state")] public string State { get; init; } = "";
    [JsonPropertyName("reason")] public string Reason { get; init; } = "";
    [JsonPropertyName("message")] public string Message { get; init; } = "";
    [JsonPropertyName("next_action")] public string NextAction { get; init; } = "";
    [JsonPropertyName("chat")] public ProductChat Chat { get; init; } = new();
    [JsonPropertyName("qualification")] public ProductQualification Qualification { get; init; } = new();
    [JsonPropertyName("model")] public string? Model { get; init; }
    [JsonPropertyName("services")] public ProductService[] Services { get; init; } = Array.Empty<ProductService>();
}

public sealed class ProductChat
{
    [JsonPropertyName("ready")] public bool Ready { get; init; }
    [JsonPropertyName("url")] public string? Url { get; init; }
    [JsonPropertyName("onboarding_required")] public bool? OnboardingRequired { get; init; }
}

public sealed class ProductQualification
{
    [JsonPropertyName("inference")] public string Inference { get; init; } = "";
    [JsonPropertyName("detail")] public string Detail { get; init; } = "";
}

public sealed class ProductService
{
    [JsonPropertyName("name")] public string Name { get; init; } = "";
    [JsonPropertyName("state")] public string State { get; init; } = "";
    [JsonPropertyName("detail")] public string Detail { get; init; } = "";
}

public static class ProductStatusParser
{
    public const string StartStatusPrefix = "AFK-STATUS:";

    /// <summary>
    /// Returns the last well-formed schema-2 status in <paramref name="output"/>,
    /// or null. With <paramref name="prefix"/>, only lines carrying that prefix
    /// are considered (the start command interleaves progress events).
    /// </summary>
    public static ProductStatus? TryParse(string? output, string? prefix = null)
    {
        if (string.IsNullOrWhiteSpace(output)) return null;
        var lines = output.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries);
        for (var index = lines.Length - 1; index >= 0; index--)
        {
            var line = lines[index].Trim();
            if (prefix is not null)
            {
                if (!line.StartsWith(prefix, StringComparison.Ordinal)) continue;
                line = line[prefix.Length..];
            }
            else if (!line.StartsWith('{'))
            {
                continue;
            }
            return Validate(line);
        }
        return null;
    }

    private static ProductStatus? Validate(string json)
    {
        ProductStatus? status;
        try { status = JsonSerializer.Deserialize<ProductStatus>(json); }
        catch (JsonException) { return null; }
        if (status is null || status.SchemaVersion != 2) return null;
        if (!ProductStatus.States.All.Contains(status.State) || string.IsNullOrWhiteSpace(status.Reason)) return null;
        if (status.Mode is not (ProductStatus.ModeQualify or ProductStatus.ModeLiveness)) return null;
        return status;
    }
}
