using System.Diagnostics;
using System.Text.Json;

namespace AFKLocalAI.App;

public sealed class MainForm : Form
{
    public static IReadOnlySet<string> AccessibilityContract { get; } = new HashSet<string>
    {
        "Prerequisite status", "Setup progress", "Primary action", "Retry prerequisite check",
        "Open diagnostics", "Open support"
    };
    public static Type ProcessRunnerType => typeof(HiddenProcessRunner);

    private readonly AppPaths _paths;
    private readonly ProductInfo _product;
    private readonly ProvisioningStateStore _stateStore;
    private readonly ProvisioningController _controller;
    private readonly HiddenProcessRunner _runner = new();
    private ProvisioningState _state;
    private PreflightSummary? _preflight;
    private string _pendingAction = "retry";

    private readonly Panel _content = new() { Dock = DockStyle.Fill, Padding = new Padding(48, 38, 48, 32) };
    private readonly Label _headline = new() { AutoSize = true };
    private readonly Label _subtitle = new() { AutoSize = true, MaximumSize = new Size(780, 0) };
    private readonly TableLayoutPanel _status = new() { AutoSize = true, Dock = DockStyle.Top, ColumnCount = 3 };
    private readonly TextBox _progress = new()
    {
        Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.FixedSingle,
        AccessibleName = "Setup progress", Dock = DockStyle.Fill
    };
    private readonly Button _primary = Theme.Button("Check this PC", primary: true);
    private readonly Button _retry = Theme.Button("Check again");
    private readonly Label _progressLabel = new() { Text = "Setup progress", AutoSize = true };

    public MainForm(AppPaths paths, ProductInfo product, bool aboutOnly, Icon icon)
    {
        _paths = paths;
        _product = product;
        _stateStore = new ProvisioningStateStore(paths);
        _controller = new ProvisioningController(paths);
        _state = _stateStore.LoadOrCreate();

        Text = $"{product.ProductName}  {product.DisplayVersion}";
        Icon = icon;
        MinimumSize = new Size(980, 650);
        Size = new Size(1120, 760);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Theme.Background;
        ForeColor = Theme.PrimaryText;
        AutoScaleMode = AutoScaleMode.Dpi;
        KeyPreview = true;

        Controls.Add(BuildShell());
        _primary.AccessibleName = "Primary action";
        _retry.AccessibleName = "Retry prerequisite check";
        _primary.Click += async (_, _) => await RunPrimaryActionAsync();
        _retry.Click += async (_, _) => await RefreshPreflightAsync();
        Shown += async (_, _) =>
        {
            if (aboutOnly) { ShowAbout(); return; }
            if (AppModeResolver.Resolve(_state) == AppMode.Home)
            {
                RenderHome();
                await RevalidateHomeReadinessAsync();
            }
            else { RenderSetup(); await RefreshPreflightAsync(); }
        };
    }

    private Control BuildShell()
    {
        var shell = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, BackColor = Theme.Background };
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 236));
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        shell.Controls.Add(BuildSidebar(), 0, 0);
        shell.Controls.Add(_content, 1, 0);
        return shell;
    }

    private Control BuildSidebar()
    {
        var panel = new Panel { Dock = DockStyle.Fill, BackColor = Theme.Surface, Padding = new Padding(24, 26, 20, 20) };
        var mark = new PictureBox { Location = new Point(24, 26), Size = new Size(42, 42), AccessibleName = "AFK LocalAI" };
        mark.Paint += (_, eventArgs) => Theme.PaintWordmark(eventArgs.Graphics, new Rectangle(2, 2, 38, 38));
        var name = new Label { Text = "AFK LocalAI", AutoSize = true, Location = new Point(78, 28), Font = Theme.Font(13, FontStyle.Bold), ForeColor = Theme.PrimaryText };
        var version = new Label { Text = $"Friend Beta  {_product.DisplayVersion}", AutoSize = true, Location = new Point(79, 52), Font = Theme.Font(8.5f), ForeColor = Theme.MutedText };
        panel.Controls.Add(mark);
        panel.Controls.Add(name);
        panel.Controls.Add(version);

        var navigation = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true,
            Location = new Point(20, 112), Width = 194
        };
        navigation.Controls.Add(SideButton("Home", () =>
        {
            if (AppModeResolver.Resolve(_state) == AppMode.Home) RenderHome();
            else RenderSetup();
        }));
        navigation.Controls.Add(SideButton("Setup & repair", async () => { RenderSetup(); await RefreshPreflightAsync(); }));
        navigation.Controls.Add(SideButton("Diagnostics", async () => await OpenDiagnosticsAsync()));
        navigation.Controls.Add(SideButton("Data folder", () => OpenPath(_paths.DataRoot)));
        navigation.Controls.Add(SideButton("About", ShowAbout));
        panel.Controls.Add(navigation);

        var support = SideButton("Support", () => OpenUri(_product.SupportUrl));
        support.AccessibleName = "Open support";
        support.Location = new Point(20, 650);
        support.Anchor = AnchorStyles.Left | AnchorStyles.Bottom;
        panel.Controls.Add(support);
        return panel;
    }

    private static Button SideButton(string text, Action action)
    {
        var button = new Button
        {
            Text = text, Width = 190, Height = 42, TextAlign = ContentAlignment.MiddleLeft,
            FlatStyle = FlatStyle.Flat, BackColor = Theme.Surface, ForeColor = Theme.SecondaryText,
            Font = Theme.Font(10), Cursor = Cursors.Hand, Margin = new Padding(0, 0, 0, 5)
        };
        button.FlatAppearance.BorderSize = 0;
        button.FlatAppearance.MouseOverBackColor = Theme.Elevated;
        button.Click += (_, _) => action();
        return button;
    }

    private void RenderSetup()
    {
        _content.Controls.Clear();
        _headline.Text = "Let’s get this PC ready";
        _headline.Font = Theme.Font(25, FontStyle.Bold);
        _headline.ForeColor = Theme.PrimaryText;
        _subtitle.Text = "AFK LocalAI checks Windows, virtualization, WSL, Docker, and your hardware before it downloads anything large.";
        _subtitle.Font = Theme.Font(11);
        _subtitle.ForeColor = Theme.SecondaryText;

        _status.AccessibleName = "Prerequisite status";
        _status.BackColor = Theme.Surface;
        _status.Padding = new Padding(22, 16, 22, 16);
        _status.ColumnStyles.Clear();
        _status.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 190));
        _status.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 128));
        _status.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        SetStatusRows(new Dictionary<string, string> { ["Windows"] = "Checking", ["Virtualization"] = "Checking", ["WSL"] = "Checking", ["Docker"] = "Checking", ["Hardware"] = "Pending" });

        _progress.BackColor = Theme.Elevated;
        _progress.ForeColor = Theme.SecondaryText;
        _progress.Font = new Font("Cascadia Mono", 9, FontStyle.Regular, GraphicsUnit.Point);
        _progressLabel.Font = Theme.Font(9, FontStyle.Bold);
        _progressLabel.ForeColor = Theme.MutedText;
        _progress.Text = "Waiting for the prerequisite check…";

        var header = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 105, FlowDirection = FlowDirection.TopDown, WrapContents = false };
        header.Controls.Add(_headline);
        header.Controls.Add(_subtitle);
        var statusHost = new Panel { Dock = DockStyle.Top, Height = 250, Padding = new Padding(0, 6, 0, 20) };
        statusHost.Controls.Add(_status);
        var progressHost = new Panel { Dock = DockStyle.Fill, Padding = new Padding(0, 14, 0, 70) };
        progressHost.Controls.Add(_progress);
        progressHost.Controls.Add(_progressLabel);
        _progressLabel.Dock = DockStyle.Top;
        _progressLabel.Padding = new Padding(0, 0, 0, 8);
        var actions = new FlowLayoutPanel { Dock = DockStyle.Bottom, Height = 58, FlowDirection = FlowDirection.LeftToRight };
        actions.Controls.Add(_primary);
        actions.Controls.Add(_retry);
        var diagnostics = Theme.Button("Diagnostics");
        diagnostics.AccessibleName = "Open diagnostics";
        diagnostics.Click += async (_, _) => await OpenDiagnosticsAsync();
        actions.Controls.Add(diagnostics);

        _content.Controls.Add(progressHost);
        _content.Controls.Add(statusHost);
        _content.Controls.Add(header);
        _content.Controls.Add(actions);
    }

    private void RenderHome()
    {
        _content.Controls.Clear();
        var title = Heading("Your local AI is ready", 25);
        var intro = Body("Everything runs on this PC. Start the services, then open your private chat in the browser.");
        var openChat = Theme.Button("Open Chat", primary: true);
        openChat.Click += (_, _) => OpenUri(_controller.DashboardUri.ToString());
        var start = Theme.Button("Start");
        start.Click += async (_, _) => await RunHomeCommandAsync(_controller.Start(), "AFK LocalAI is starting.");
        var stop = Theme.Button("Stop");
        stop.Click += async (_, _) => await RunHomeCommandAsync(_controller.Stop(), "AFK LocalAI is stopped.");
        var health = Theme.Button("Health check");
        health.Click += async (_, _) => await RunHomeCommandAsync(_controller.Health(), "Health check completed.");
        var repair = Theme.Button("Setup & repair");
        repair.Click += async (_, _) => { RenderSetup(); await RefreshPreflightAsync(); };
        var actions = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 64, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(0, 12, 0, 0) };
        foreach (var button in new[] { openChat, start, stop, health, repair }) actions.Controls.Add(button);
        var privacy = new Label
        {
            Text = "LOCAL BY DEFAULT\nChat and model traffic stay on loopback unless you explicitly change the advanced network settings.",
            Dock = DockStyle.Top, Height = 90, Padding = new Padding(20), BackColor = Theme.Surface,
            ForeColor = Theme.SecondaryText, Font = Theme.Font(10)
        };
        _content.Controls.Add(privacy);
        _content.Controls.Add(actions);
        _content.Controls.Add(intro);
        _content.Controls.Add(title);
    }

    private async Task RefreshPreflightAsync()
    {
        SetBusy(true, "Checking this PC…");
        var result = await _runner.RunAsync(_controller.Preflight());
        var json = result.StandardOutput.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries).LastOrDefault(line => line.TrimStart().StartsWith('{'));
        if (json is null)
        {
            ShowFailure("The prerequisite check did not return a result.", result.StandardError);
            SetBusy(false);
            return;
        }
        try
        {
            _preflight = JsonSerializer.Deserialize<PreflightSummary>(json) ?? throw new JsonException("Empty result.");
            _state.LastReasonCode = _preflight.Code;
            _stateStore.SaveAtomic(_state);
            UpdatePreflightDisplay(_preflight);
        }
        catch (JsonException exception) { ShowFailure("The prerequisite result could not be read.", exception.Message); }
        finally { SetBusy(false); }
    }

    private async Task RevalidateHomeReadinessAsync()
    {
        var result = await _runner.RunAsync(_controller.Preflight());
        var json = result.StandardOutput.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries)
            .LastOrDefault(line => line.TrimStart().StartsWith('{'));
        if (json is null)
        {
            _state.Usable = false;
            _stateStore.SaveAtomic(_state);
            RenderSetup();
            ShowFailure("AFK LocalAI could not re-check this PC.", result.StandardError);
            return;
        }
        try
        {
            var summary = JsonSerializer.Deserialize<PreflightSummary>(json);
            if (summary is null || summary.Overall == "READY") return;
            _state.Usable = false;
            _state.LastReasonCode = summary.Code;
            _stateStore.SaveAtomic(_state);
            _preflight = summary;
            RenderSetup();
            UpdatePreflightDisplay(summary);
        }
        catch (JsonException)
        {
            _state.Usable = false;
            _stateStore.SaveAtomic(_state);
            RenderSetup();
            ShowFailure("AFK LocalAI could not read the prerequisite result.", "Run the check again or open Diagnostics.");
        }
    }

    private void UpdatePreflightDisplay(PreflightSummary summary)
    {
        string Component(string key) => summary.Components.TryGetValue(key, out var value) ? value.ToString().Replace('_', ' ') : "Not checked";
        SetStatusRows(new Dictionary<string, string>
        {
            ["Windows"] = Component("platform"), ["Virtualization"] = Component("windows_virtualization"),
            ["WSL"] = Component("wsl"), ["Docker"] = Component("docker"), ["Hardware"] = "Checked during setup"
        });
        _progress.Text = string.Join(Environment.NewLine, summary.UserMessage.Concat(new[] { "", $"Reason code: {summary.Code}" }));
        if (summary.Overall == "READY")
        {
            _pendingAction = "provision";
            _primary.Text = "Set up AFK LocalAI";
            _headline.Text = "This PC is ready";
            return;
        }
        var recovery = RecoveryAction.ForCode(summary.Code);
        _pendingAction = recovery.ActionId;
        _primary.Text = recovery.Label;
        _headline.Text = summary.Overall == "RECOVERABLE_BLOCKER" ? "One step is needed" : "Setup needs your attention";
    }

    private async Task RunPrimaryActionAsync()
    {
        if (_pendingAction == "provision") { await RunProvisioningAsync(); return; }
        if (_preflight is null) { await RefreshPreflightAsync(); return; }
        var recovery = RecoveryAction.ForCode(_preflight.Code);
        if (!recovery.Automatic)
        {
            MessageBox.Show($"{recovery.Explanation}\n\nReason code: {_preflight.Code}\n\nOpen Diagnostics if you need help sharing this result with support.",
                "AFK LocalAI setup", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        SetBusy(true, $"{recovery.Label}…");
        // stderr is streamed live by the runner now; re-appending it here would
        // print every error line twice.
        var result = await _runner.RunAsync(_controller.Recovery(_preflight.Code, recovery.ActionId), AppendProcessLine);
        SetBusy(false);
        await RefreshPreflightAsync();
    }

    private async Task RunProvisioningAsync()
    {
        SetBusy(true, "Setting up your local AI…");
        _progress.Clear();
        var result = await _runner.RunAsync(_controller.Provision(), AppendProcessLine);
        if (result.ExitCode == 0)
        {
            _state.Usable = true;
            _state.LastReasonCode = "SETUP-COMPLETE";
            _stateStore.SaveAtomic(_state);
            RenderHome();
        }
        else if (result.ExitCode == 10) await RefreshPreflightAsync();
        else ShowFailure("Setup stopped before AFK LocalAI became usable.", result.StandardError);
        SetBusy(false);
    }

    private async Task RunHomeCommandAsync(ProcessSpec spec, string successMessage)
    {
        UseWaitCursor = true;
        var result = await _runner.RunAsync(spec);
        UseWaitCursor = false;
        MessageBox.Show(result.Succeeded ? successMessage : $"The action did not complete.\n\n{result.StandardError}",
            "AFK LocalAI", MessageBoxButtons.OK, result.Succeeded ? MessageBoxIcon.Information : MessageBoxIcon.Warning);
    }

    private async Task OpenDiagnosticsAsync()
    {
        _paths.EnsureUserDirectories();
        var result = await _runner.RunAsync(_controller.Diagnostics());
        var text = RedactUserPath(result.StandardOutput + Environment.NewLine + result.StandardError);
        var path = Path.Combine(_paths.DiagnosticsRoot, $"AFKLocalAI-Diagnostics-{DateTime.UtcNow:yyyyMMddTHHmmssZ}.txt");
        await File.WriteAllTextAsync(path, $"AFK LocalAI {_product.DisplayVersion}{Environment.NewLine}{text}");
        OpenPath(_paths.DiagnosticsRoot);
    }

    private static string RedactUserPath(string value)
    {
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        return string.IsNullOrWhiteSpace(profile) ? value : value.Replace(profile, "%USERPROFILE%", StringComparison.OrdinalIgnoreCase);
    }

    private void SetStatusRows(IReadOnlyDictionary<string, string> rows)
    {
        _status.SuspendLayout();
        _status.Controls.Clear();
        _status.RowStyles.Clear();
        _status.RowCount = rows.Count;
        var index = 0;
        foreach (var pair in rows)
        {
            _status.RowStyles.Add(new RowStyle(SizeType.Absolute, 38));
            var name = new Label { Text = pair.Key, AutoSize = true, ForeColor = Theme.PrimaryText, Font = Theme.Font(10, FontStyle.Bold), Anchor = AnchorStyles.Left };
            var state = new Label { Text = pair.Value, AutoSize = true, ForeColor = StatusColor(pair.Value), Anchor = AnchorStyles.Left };
            var detail = new Label { Text = StatusDetail(pair.Key, pair.Value), AutoSize = true, ForeColor = Theme.MutedText, Anchor = AnchorStyles.Left };
            _status.Controls.Add(name, 0, index); _status.Controls.Add(state, 1, index); _status.Controls.Add(detail, 2, index);
            index++;
        }
        _status.ResumeLayout();
    }

    private static Color StatusColor(string value) => value.ToUpperInvariant() switch
    {
        "READY" or "SUPPORTED" => Theme.Success,
        "CHECKING" or "PENDING" or "STARTING" => Theme.Warning,
        "UNKNOWN" or "NOT CHECKED" => Theme.MutedText,
        _ => Theme.Failure
    };

    private static string StatusDetail(string name, string value) => name switch
    {
        "Windows" => "64-bit Windows 11",
        "Virtualization" => "Firmware and Windows platform",
        "WSL" => "Only when the Docker backend needs it",
        "Docker" => "Local Linux engine",
        _ => value == "Pending" ? "Selected after prerequisites" : "Runtime fit"
    };

    private void SetBusy(bool busy, string? message = null)
    {
        _primary.Enabled = !busy;
        _retry.Enabled = !busy;
        UseWaitCursor = busy;
        if (!string.IsNullOrWhiteSpace(message)) AppendText(message);
    }

    private void AppendProcessLine(string line)
    {
        if (InvokeRequired) { BeginInvoke(() => AppendProcessLine(line)); return; }
        if (ProvisioningEvent.TryParse(line, out var parsed)) AppendText(parsed?.Message ?? line);
        else AppendText(line);
    }

    private void AppendText(string? text)
    {
        if (string.IsNullOrWhiteSpace(text) || _progress.IsDisposed) return;
        _progress.AppendText((string.IsNullOrEmpty(_progress.Text) ? "" : Environment.NewLine) + text.Trim());
    }

    private void ShowFailure(string headline, string detail)
    {
        _headline.Text = headline;
        _progress.Text = $"{detail.Trim()}\r\n\r\nOpen Diagnostics for the reason code and support-ready details.";
    }

    private void ShowAbout()
    {
        _content.Controls.Clear();
        _content.Controls.Add(Body($"Version {_product.DisplayVersion}  •  {_product.Channel}\n\nLocal-first Windows AI setup and launcher.\n\nSource: {_product.Repository}\nSupport: {_product.SupportUrl}\n\nAFK LocalAI is not affiliated with the upstream LocalAI project by mudler."));
        _content.Controls.Add(Heading("About AFK LocalAI", 25));
    }

    private static Label Heading(string text, float size) => new()
    {
        Text = text, AutoSize = true, Dock = DockStyle.Top, Padding = new Padding(0, 0, 0, 12),
        Font = Theme.Font(size, FontStyle.Bold), ForeColor = Theme.PrimaryText
    };

    private static Label Body(string text) => new()
    {
        Text = text, AutoSize = true, Dock = DockStyle.Top, MaximumSize = new Size(780, 0),
        Font = Theme.Font(11), ForeColor = Theme.SecondaryText
    };

    private static void OpenPath(string path)
    {
        Directory.CreateDirectory(path);
        Process.Start(new ProcessStartInfo("explorer.exe", $"\"{path}\"") { UseShellExecute = true });
    }

    private static void OpenUri(string uri) => Process.Start(new ProcessStartInfo(uri) { UseShellExecute = true });
}
