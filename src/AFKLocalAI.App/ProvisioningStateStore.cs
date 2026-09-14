using System.Text.Json;

namespace AFKLocalAI.App;

public sealed class ProvisioningStateStore
{
    private static readonly JsonSerializerOptions JsonOptions = new() { WriteIndented = true };
    private readonly AppPaths _paths;

    public ProvisioningStateStore(AppPaths paths) => _paths = paths;

    public ProvisioningState LoadOrCreate()
    {
        _paths.EnsureUserDirectories();
        if (!File.Exists(_paths.ProvisioningStatePath)) return new ProvisioningState();
        try
        {
            var state = JsonSerializer.Deserialize<ProvisioningState>(
                File.ReadAllText(_paths.ProvisioningStatePath), JsonOptions)
                ?? throw new JsonException("State is empty.");
            if (state.SchemaVersion > ProvisioningState.CurrentSchemaVersion)
                throw new InvalidDataException($"State schema {state.SchemaVersion} was written by a newer AFK LocalAI.");
            if (state.SchemaVersion <= 0) throw new JsonException("State schema is invalid.");
            return state;
        }
        catch (JsonException)
        {
            var quarantine = Path.Combine(
                _paths.StateRoot,
                $"provisioning-state.corrupt-{DateTime.UtcNow:yyyyMMddTHHmmssZ}-{Guid.NewGuid():N}.json");
            File.Move(_paths.ProvisioningStatePath, quarantine);
            return new ProvisioningState();
        }
    }

    public void SaveAtomic(ProvisioningState state)
    {
        ArgumentNullException.ThrowIfNull(state);
        _paths.EnsureUserDirectories();
        state.UpdatedAtUtc = DateTimeOffset.UtcNow;
        var json = JsonSerializer.Serialize(state, JsonOptions);
        _ = JsonSerializer.Deserialize<ProvisioningState>(json, JsonOptions)
            ?? throw new InvalidDataException("State did not survive serialization.");

        var temporary = Path.Combine(_paths.StateRoot, $".provisioning-state.{Guid.NewGuid():N}.tmp");
        var backup = Path.Combine(_paths.StateRoot, $".provisioning-state.{Guid.NewGuid():N}.bak");
        try
        {
            File.WriteAllText(temporary, json);
            using (var stream = File.OpenRead(temporary))
                _ = JsonSerializer.Deserialize<ProvisioningState>(stream, JsonOptions)
                    ?? throw new InvalidDataException("Temporary state could not be validated.");
            if (File.Exists(_paths.ProvisioningStatePath))
            {
                File.Replace(temporary, _paths.ProvisioningStatePath, backup);
                File.Delete(backup);
            }
            else
            {
                File.Move(temporary, _paths.ProvisioningStatePath);
            }
        }
        finally
        {
            if (File.Exists(temporary)) File.Delete(temporary);
            if (File.Exists(backup)) File.Delete(backup);
        }
    }
}
