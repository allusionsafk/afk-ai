using System.Text.Json;
using System.Text.RegularExpressions;

namespace AFKLocalAI.App;

public sealed record UtilityGpu(string Name, long? VramBytes);
public sealed record UtilityModel(
    string Tag, string Verdict, string Reason, string? Purpose, string? Quantization,
    long? WeightsBytes, long? KvBytes, string? KvSource, long? VramRequiredBytes,
    int Context, bool Installed, bool Configured, bool? Loaded);
public sealed record UtilityRecommendation(string Model, string Basis, string Source, int Context);
public sealed record UtilitySelection(
    string? SourceModel, int? RecommendedContext, int? SelectedContext, int? OverrideContext);
public sealed record UtilityReport(
    string? Cpu, long? RamBytes, IReadOnlyList<UtilityGpu> Gpus,
    IReadOnlyList<UtilityModel> Models, UtilityRecommendation? Recommendation,
    UtilitySelection Selection, string InventoryState, IReadOnlyList<string> Errors);
public sealed record UtilityMeasurement(
    int Context, int? EffectiveContext, bool Successful, string Source,
    double? FirstTokenSeconds, double? PromptTokensPerSecond,
    double? DecodeTokensPerSecond, string? MeasuredAt);
public sealed record UtilityEstimate(int Context, bool Safe, string Reason, string KvMethod);
public sealed record UtilityOptimization(
    int? RecommendedContext, string Confidence,
    IReadOnlyList<UtilityMeasurement> Measurements,
    IReadOnlyList<UtilityEstimate> Estimates, UtilitySelection Selection);

/// <summary>Strict, bounded parser for the app-owned Python Utility JSON contract.</summary>
public static class UtilityJson
{
    private const int MaxOutputChars = 2_000_000;
    private static readonly Regex ModelTag = new(
        @"^[a-z0-9][a-z0-9._-]{0,63}(?:/[a-z0-9][a-z0-9._-]{0,63}){0,2}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,63})?$",
        RegexOptions.CultureInvariant, TimeSpan.FromMilliseconds(100));

    public static UtilityReport ParseReport(string json)
    {
        using var document = Open(json);
        var root = document.RootElement;
        var hardware = Obj(root, "hardware");
        var gpus = new List<UtilityGpu>();
        foreach (var gpu in Array(hardware, "gpus", 8))
            gpus.Add(new UtilityGpu(Text(gpu, "name") ?? "Unknown GPU", Integer(gpu, "vram_total_bytes")));

        var models = new List<UtilityModel>();
        foreach (var row in Array(root, "models", 128, required: true))
        {
            var tag = Text(row, "model");
            if (tag is null || !ModelTag.IsMatch(tag)) throw new InvalidDataException("Invalid Utility model name.");
            models.Add(new UtilityModel(
                tag, Text(row, "verdict") ?? "Unknown", Text(row, "reason") ?? "No fit evidence is available.",
                Text(row, "purpose"), Text(row, "quantization"),
                Integer(row, "weights_bytes"), Integer(row, "kv_bytes"), Text(row, "kv_source"),
                Integer(row, "vram_required_bytes"), Context(row, "context") ?? 0,
                Flag(row, "installed") == true, Flag(row, "configured") == true, Flag(row, "loaded")));
        }
        UtilityRecommendation? recommendation = null;
        if (TryObj(root, "recommendation", out var rec))
        {
            var model = Text(rec, "model");
            if (model is not null && ModelTag.IsMatch(model))
                recommendation = new UtilityRecommendation(
                    model, Text(rec, "basis") ?? "Based on a local fit estimate",
                    Text(rec, "source") ?? "unknown", Context(rec, "context") ?? 0);
        }
        var runtime = TryObj(root, "runtime", out var found) ? found : default;
        var errors = runtime.ValueKind == JsonValueKind.Object
            ? Array(runtime, "errors", 8).Select(item => item.ValueKind == JsonValueKind.String
                ? Limit(item.GetString(), 200) ?? "Runtime detail unavailable" : "Runtime detail unavailable").ToArray()
            : System.Array.Empty<string>();
        return new UtilityReport(
            Text(hardware, "cpu"), Integer(hardware, "ram_total_bytes"), gpus, models,
            recommendation, Selection(root, "selection"),
            Text(runtime, "inventory_state") ?? "Unknown", errors);
    }

    public static UtilityOptimization ParseOptimization(string json)
    {
        using var document = Open(json);
        var root = document.RootElement;
        var measurements = new List<UtilityMeasurement>();
        foreach (var row in Array(root, "measurements", 8, required: true))
        {
            var context = Context(row, "context") ?? 0;
            var effective = Context(row, "effective_context");
            var successful = Flag(row, "successful") == true && context > 0 && effective == context;
            var source = Text(row, "source");
            measurements.Add(new UtilityMeasurement(
                context, effective, successful, source is "fresh" or "cache" ? source : "unknown",
                Number(row, "median_first_token_seconds"), Number(row, "median_prompt_tokens_per_second"),
                Number(row, "median_decode_tokens_per_second"), Text(row, "measured_at")));
        }
        var estimates = new List<UtilityEstimate>();
        foreach (var row in Array(root, "estimates", 8, required: true))
            estimates.Add(new UtilityEstimate(Context(row, "context") ?? 0,
                Flag(row, "safe") == true, Text(row, "reason") ?? "Unknown",
                Text(row, "kv_method") ?? "unknown"));
        return new UtilityOptimization(
            Context(root, "recommended_context"), Text(root, "confidence") ?? "insufficient evidence",
            measurements, estimates, Selection(root, "user_setting"));
    }

    private static JsonDocument Open(string json)
    {
        if (string.IsNullOrEmpty(json) || json.Length > MaxOutputChars)
            throw new InvalidDataException("Utility response is empty or too large.");
        try
        {
            var document = JsonDocument.Parse(json, new JsonDocumentOptions { MaxDepth = 16 });
            if (document.RootElement.ValueKind != JsonValueKind.Object ||
                Integer(document.RootElement, "schema_version") != 1)
            {
                document.Dispose();
                throw new InvalidDataException("Unsupported Utility response version.");
            }
            return document;
        }
        catch (JsonException error) { throw new InvalidDataException("Invalid Utility response.", error); }
    }

    private static UtilitySelection Selection(JsonElement root, string property)
    {
        if (!TryObj(root, property, out var value)) return new(null, null, null, null);
        return new(Text(value, "source_model"), Context(value, "recommended_context"),
            Context(value, "selected_context"), Context(value, "override_context"));
    }

    private static JsonElement Obj(JsonElement root, string property) =>
        TryObj(root, property, out var value) ? value : throw new InvalidDataException($"Missing Utility {property}.");

    private static bool TryObj(JsonElement root, string property, out JsonElement value)
    {
        value = default;
        return root.ValueKind == JsonValueKind.Object && root.TryGetProperty(property, out value) &&
            value.ValueKind == JsonValueKind.Object;
    }

    private static IEnumerable<JsonElement> Array(JsonElement root, string property, int maximum, bool required = false)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.Array)
        {
            if (required) throw new InvalidDataException($"Missing Utility {property}.");
            return Enumerable.Empty<JsonElement>();
        }
        if (value.GetArrayLength() > maximum) throw new InvalidDataException("Utility list is too large.");
        return value.EnumerateArray().ToArray();
    }

    private static string? Text(JsonElement root, string property)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.String) return null;
        return Limit(value.GetString(), 256);
    }

    private static string? Limit(string? value, int maximum) =>
        value is { Length: > 0 } && value.Length <= maximum && !value.Any(char.IsControl) ? value : null;

    private static long? Integer(JsonElement root, string property)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.Number || !value.TryGetInt64(out var number) || number < 0)
            return null;
        return number;
    }

    private static int? Context(JsonElement root, string property)
    {
        var number = Integer(root, property);
        return number is >= 1024 and <= 1_048_576 ? (int)number : null;
    }

    private static double? Number(JsonElement root, string property)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(property, out var value) ||
            value.ValueKind != JsonValueKind.Number || !value.TryGetDouble(out var number) ||
            !double.IsFinite(number) || number < 0) return null;
        return number;
    }

    private static bool? Flag(JsonElement root, string property)
    {
        if (root.ValueKind != JsonValueKind.Object || !root.TryGetProperty(property, out var value)) return null;
        return value.ValueKind switch { JsonValueKind.True => true, JsonValueKind.False => false, _ => null };
    }
}
