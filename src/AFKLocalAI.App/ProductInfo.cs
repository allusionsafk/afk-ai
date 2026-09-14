using System.Text.Json;
using System.Text.Json.Serialization;

namespace AFKLocalAI.App;

public sealed class ProductInfo
{
    [JsonPropertyName("schema_version")] public int SchemaVersion { get; init; }
    [JsonPropertyName("product_name")] public string ProductName { get; init; } = "";
    [JsonPropertyName("executable_name")] public string ExecutableName { get; init; } = "";
    [JsonPropertyName("installer_name")] public string InstallerName { get; init; } = "";
    [JsonPropertyName("display_version")] public string DisplayVersion { get; init; } = "";
    [JsonPropertyName("file_version")] public string FileVersion { get; init; } = "";
    [JsonPropertyName("channel")] public string Channel { get; init; } = "";
    [JsonPropertyName("architecture")] public string Architecture { get; init; } = "";
    [JsonPropertyName("app_id")] public string AppId { get; init; } = "";
    [JsonPropertyName("publisher")] public string Publisher { get; init; } = "";
    [JsonPropertyName("repository")] public string Repository { get; init; } = "";
    [JsonPropertyName("support_url")] public string SupportUrl { get; init; } = "";

    public static ProductInfo Load(string path)
    {
        ArgumentException.ThrowIfNullOrWhiteSpace(path);
        try
        {
            var product = JsonSerializer.Deserialize<ProductInfo>(File.ReadAllText(path))
                ?? throw new InvalidDataException("Product metadata is empty.");
            product.Validate();
            return product;
        }
        catch (JsonException exception)
        {
            throw new InvalidDataException("Product metadata is not valid JSON.", exception);
        }
    }

    private void Validate()
    {
        var values = new[]
        {
            ProductName, ExecutableName, InstallerName, DisplayVersion, FileVersion,
            Channel, Architecture, AppId, Publisher, Repository, SupportUrl
        };
        if (SchemaVersion != 1 || values.Any(string.IsNullOrWhiteSpace))
            throw new InvalidDataException("Product metadata is incomplete or uses an unsupported schema.");
        if (!Version.TryParse(FileVersion, out _) || !Uri.TryCreate(Repository, UriKind.Absolute, out _) ||
            !Uri.TryCreate(SupportUrl, UriKind.Absolute, out _))
            throw new InvalidDataException("Product metadata contains an invalid version or URL.");
    }
}
