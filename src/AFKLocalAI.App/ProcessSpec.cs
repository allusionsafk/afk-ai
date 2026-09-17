using System.Diagnostics;

namespace AFKLocalAI.App;

public sealed record ProcessSpec(
    string Purpose,
    string FileName,
    IReadOnlyList<string> Arguments,
    string WorkingDirectory,
    IReadOnlyList<string> RemovedEnvironment)
{
    public static ProcessSpec Hidden(
        string fileName,
        IEnumerable<string> arguments,
        string workingDirectory,
        string purpose = "process",
        IEnumerable<string>? removedEnvironment = null) =>
        new(purpose, fileName, arguments.ToArray(), Path.GetFullPath(workingDirectory),
            (removedEnvironment ?? Array.Empty<string>()).ToArray());

    public ProcessStartInfo CreateStartInfo()
    {
        var info = new ProcessStartInfo
        {
            FileName = FileName,
            WorkingDirectory = WorkingDirectory,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8
        };
        foreach (var argument in Arguments) info.ArgumentList.Add(argument);
        foreach (var name in RemovedEnvironment) info.Environment.Remove(name);
        return info;
    }
}
