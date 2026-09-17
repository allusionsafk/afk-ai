using System.Text.Json;

namespace AFKLocalAI.App;

/// <summary>
/// A bounded, local record of what AFK AI did: setup phases, starts, stops, and
/// status transitions. Diagnostics reads only its structured codes back out.
/// </summary>
public sealed class LifecycleLog
{
    public const string FileName = "lifecycle-events.jsonl";
    public const long MaxBytes = 1024 * 1024;
    private const int MaxMessageLength = 300;

    private readonly string _path;
    private readonly object _gate = new();

    public LifecycleLog(AppPaths paths) : this(Path.Combine(paths.LogRoot, FileName)) { }

    public LifecycleLog(string path) => _path = path;

    public string PathName => _path;

    public void Append(string eventType, string phase, string status, string code, string message = "")
    {
        var record = new Dictionary<string, string>
        {
            ["timestamp_utc"] = DateTime.UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ"),
            ["event_type"] = eventType,
            ["phase"] = phase,
            ["status"] = status,
            ["code"] = code,
            ["message"] = message.Length > MaxMessageLength ? message[..MaxMessageLength] : message
        };
        Write(JsonSerializer.Serialize(record));
    }

    public void Append(ProvisioningEvent provisioningEvent) =>
        Append(provisioningEvent.EventType, provisioningEvent.Phase, provisioningEvent.Status,
            provisioningEvent.Code, provisioningEvent.Message);

    private void Write(string line)
    {
        lock (_gate)
        {
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(_path)!);
                var info = new FileInfo(_path);
                if (info.Exists && info.Length > MaxBytes)
                {
                    // Keep one previous generation; never grow without bound.
                    File.Move(_path, Path.ChangeExtension(_path, ".1.jsonl"), overwrite: true);
                }
                File.AppendAllText(_path, line + Environment.NewLine);
            }
            catch (Exception exception) when (exception is IOException or UnauthorizedAccessException)
            {
                // A log that cannot be written must never break the product.
            }
        }
    }
}
