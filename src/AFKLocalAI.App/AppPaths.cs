namespace AFKLocalAI.App;

public sealed record AppPaths(
    string ProgramRoot,
    string DataRoot,
    string StateRoot,
    string LogRoot,
    string DiagnosticsRoot,
    string ProvisioningStatePath,
    string LegacyInstallRoot)
{
    public static AppPaths ForCurrentUser(string? dataRootOverride = null, string? programRootOverride = null)
    {
        var programRoot = Path.GetFullPath(programRootOverride ?? AppContext.BaseDirectory);
        var dataRoot = Path.GetFullPath(dataRootOverride ?? Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AFK LocalAI"));
        var stateRoot = Path.Combine(dataRoot, "State");
        return new AppPaths(
            programRoot,
            dataRoot,
            stateRoot,
            Path.Combine(dataRoot, "Logs"),
            Path.Combine(dataRoot, "Diagnostics"),
            Path.Combine(stateRoot, "provisioning-state.json"),
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "localai"));
    }

    public void EnsureUserDirectories()
    {
        Directory.CreateDirectory(StateRoot);
        Directory.CreateDirectory(LogRoot);
        Directory.CreateDirectory(DiagnosticsRoot);
    }
}
