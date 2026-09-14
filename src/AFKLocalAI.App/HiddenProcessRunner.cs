using System.Diagnostics;
using System.Text;

namespace AFKLocalAI.App;

public sealed record ProcessResult(int ExitCode, string StandardOutput, string StandardError)
{
    public bool Succeeded => ExitCode == 0;
}

/// <summary>
/// Runs a child process and reports its output <em>while it is still running</em>.
/// </summary>
/// <remarks>
/// This used to call ReadToEndAsync on both streams, wait for exit, and only then
/// replay the buffered stdout through the callback. Every line therefore arrived
/// after the work was already over, which is why setup and provisioning showed a
/// large, apparently dead progress area for minutes at a time while winget and
/// Docker did the actual work. Long operations are exactly the ones a user needs
/// to see.
/// <para>
/// Both streams are pumped concurrently, so a child that fills its stderr pipe
/// cannot deadlock against a parent that is only draining stdout. Each line is
/// delivered once, as it arrives, and also accumulated so the completed result
/// still carries the full text for diagnostics and tests.
/// </para>
/// </remarks>
public sealed class HiddenProcessRunner
{
    public async Task<ProcessResult> RunAsync(
        ProcessSpec spec,
        Action<string>? onOutput = null,
        CancellationToken cancellationToken = default)
    {
        using var process = new Process { StartInfo = spec.CreateStartInfo(), EnableRaisingEvents = true };
        if (!process.Start()) throw new InvalidOperationException($"Could not start {spec.Purpose}.");

        var stdout = new StringBuilder();
        var stderr = new StringBuilder();

        // Start both pumps before awaiting either, so neither pipe can fill while
        // the other is being drained.
        var pumps = Task.WhenAll(
            PumpAsync(process.StandardOutput, stdout, onOutput, cancellationToken),
            PumpAsync(process.StandardError, stderr, onOutput, cancellationToken));

        try
        {
            await process.WaitForExitAsync(cancellationToken).ConfigureAwait(false);
        }
        catch (OperationCanceledException)
        {
            TryKill(process);
            throw;
        }

        // The streams can still hold buffered lines after exit; drain them before
        // reporting, or the last thing the child said would be lost.
        await pumps.ConfigureAwait(false);

        return new ProcessResult(process.ExitCode, stdout.ToString(), stderr.ToString());
    }

    private static async Task PumpAsync(
        StreamReader reader,
        StringBuilder sink,
        Action<string>? onOutput,
        CancellationToken cancellationToken)
    {
        while (true)
        {
            string? line;
            try
            {
                line = await reader.ReadLineAsync(cancellationToken).ConfigureAwait(false);
            }
            catch (OperationCanceledException)
            {
                return;
            }
            catch (ObjectDisposedException)
            {
                // The process was torn down while we were reading.
                return;
            }

            if (line is null) return;

            sink.Append(line).Append('\n');
            if (!string.IsNullOrWhiteSpace(line)) onOutput?.Invoke(line);
        }
    }

    private static void TryKill(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill(entireProcessTree: true);
        }
        catch (InvalidOperationException) { }
        catch (NotSupportedException) { }
    }
}
