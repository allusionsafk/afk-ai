using System.ComponentModel;
using System.Diagnostics;
using System.Text.Json;

namespace AFKLocalAI.App;

public sealed class MainForm : Form
{
    public static IReadOnlySet<string> AccessibilityContract { get; } = new HashSet<string>
    {
        "Prerequisite status", "Setup progress", "Primary action", "Retry prerequisite check",
        "Open diagnostics", "Open support", "Product status", "Status detail", "Service details",
        "Activity", "Check again", "Stop AFK AI", "Cancel operation", "Download progress"
    };
    public static Type ProcessRunnerType => typeof(HiddenProcessRunner);

    private enum Screen { Setup, Home, About }

    private readonly AppPaths _paths;
    private readonly ProductInfo _product;
    private readonly ProvisioningStateStore _stateStore;
    private readonly ProvisioningController _controller;
    private readonly HiddenProcessRunner _runner = new();
    private readonly LifecycleLog _log;
    private readonly CancellationTokenSource _lifetime = new();
    private readonly System.Windows.Forms.Timer _refreshTimer = new() { Interval = 1000 };
    private ProvisioningState _state;
    private PreflightSummary? _preflight;
    private string _pendingAction = "retry";
    private Screen _screen = Screen.Setup;

    private HomeModel _home = HomeModel.Initial;
    private Task<IntegrityResult>? _integrity;
    private CancellationTokenSource? _operation;
    private string? _operationName;
    private bool _probeInFlight;
    private bool _autoStartAttempted;
    private bool _userStopped;
    private DateTimeOffset? _lastAutoQualify;

    private readonly Panel _content = new() { Dock = DockStyle.Fill, Padding = new Padding(56, 44, 56, 32) };

    // Setup screen.
    private readonly Label _headline = new() { AutoSize = true };
    private readonly Label _subtitle = new() { AutoSize = true, MaximumSize = new Size(780, 0) };
    private readonly TableLayoutPanel _status = new() { AutoSize = true, Dock = DockStyle.Top, ColumnCount = 3 };
    private readonly TextBox _progress = new()
    {
        Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.FixedSingle,
        AccessibleName = "Setup progress", Dock = DockStyle.Fill
    };
    private readonly ProgressBar _setupDownload = new()
    {
        Dock = DockStyle.Top, Height = 8, Minimum = 0, Maximum = 100, Visible = false,
        AccessibleName = "Download progress"
    };
    private readonly Button _primary = Theme.Button("Check this PC", primary: true);
    private readonly Button _retry = Theme.Button("Check again");
    private readonly LinkLabel _progressLabel = new() { Text = "Show setup details", AutoSize = true };
    private readonly Label _setupMessage = new() { AutoSize = true, MaximumSize = new Size(720, 0) };
    private readonly Label _setupStage = new() { AutoSize = true, MaximumSize = new Size(720, 0), Visible = false };

    // Home screen.
    private readonly Label _homeStatus = new() { AutoSize = true };
    private readonly Label _homeModel = new() { AutoSize = true, Visible = false };
    private readonly Label _homeHeadline = new() { AutoSize = true, AccessibleName = "Product status", AccessibleRole = AccessibleRole.StaticText };
    private readonly Label _homeDetail = new() { AutoSize = true, MaximumSize = new Size(760, 0), AccessibleName = "Status detail" };
    private readonly Label _homeUpdated = new() { AutoSize = true };
    private readonly Button _homePrimary = Theme.Button("Please wait…", primary: true);
    private readonly Button _homeCheck = Theme.Button("Check again");
    private readonly Button _homeStop = Theme.Button("Stop");
    private readonly Button _homeCancel = Theme.Button("Cancel");
    private readonly Button _homeDiagnostics = Theme.Button("Diagnostics");
    private readonly LinkLabel _homeDetailsToggle = new() { AutoSize = true, Text = "Show details" };
    private readonly TableLayoutPanel _homeServices = new() { AutoSize = true, Dock = DockStyle.Top, ColumnCount = 3, Visible = false, AccessibleName = "Service details" };
    private readonly TextBox _homeActivity = new()
    {
        Multiline = true, ReadOnly = true, ScrollBars = ScrollBars.Vertical, BorderStyle = BorderStyle.FixedSingle,
        AccessibleName = "Activity", Dock = DockStyle.Fill, Visible = false
    };
    private HomeAction _homeAction = HomeAction.Wait;

    public MainForm(AppPaths paths, ProductInfo product, bool aboutOnly, Icon icon)
    {
        _paths = paths;
        _product = product;
        _stateStore = new ProvisioningStateStore(paths);
        _controller = new ProvisioningController(paths);
        _log = new LifecycleLog(paths);
        _state = _stateStore.LoadOrCreate();

        Text = $"AFK AI  {product.DisplayVersion}";
        Icon = icon;
        // Sizes are design pixels at 100%; a per-monitor-aware window has to scale
        // them itself, and never open larger than the screen it opens on.
        var workArea = System.Windows.Forms.Screen.PrimaryScreen?.WorkingArea.Size ?? new Size(int.MaxValue, int.MaxValue);
        MinimumSize = FitTo(workArea, LogicalToDeviceUnits(new Size(980, 650)));
        Size = FitTo(workArea, LogicalToDeviceUnits(new Size(1120, 760)));
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Theme.Background;
        ForeColor = Theme.PrimaryText;
        AutoScaleMode = AutoScaleMode.Dpi;
        KeyPreview = true;

        Controls.Add(BuildShell());
        _primary.AccessibleName = "Primary action";
        _retry.AccessibleName = "Retry prerequisite check";
        _primary.Click += (_, _) => Guard(RunPrimaryActionAsync);
        _retry.Click += (_, _) => Guard(RefreshPreflightAsync);

        _homePrimary.AccessibleName = "Primary action";
        _homeCheck.AccessibleName = "Check again";
        _homeStop.AccessibleName = "Stop AFK AI";
        _homeCancel.AccessibleName = "Cancel operation";
        _homeDiagnostics.AccessibleName = "Open diagnostics";
        _homePrimary.Click += (_, _) => Guard(RunHomeActionAsync);
        _homeCheck.Click += (_, _) => Guard(() => RefreshHomeAsync(qualify: true));
        _homeStop.Click += (_, _) => Guard(StopAsync);
        _homeCancel.Click += (_, _) => _operation?.Cancel();
        _homeDiagnostics.Click += (_, _) => Guard(OpenDiagnosticsAsync);
        _progressLabel.LinkClicked += (_, _) => SetSetupDetails(!_progress.Visible);
        _homeDetailsToggle.LinkClicked += (_, _) =>
        {
            _homeServices.Visible = !_homeServices.Visible;
            _homeDetailsToggle.Text = _homeServices.Visible ? "Hide details" : "Show details";
        };

        _refreshTimer.Tick += (_, _) => Guard(OnRefreshTickAsync);
        Activated += (_, _) => Guard(OnActivatedAsync);
        FormClosing += OnFormClosing;

        Shown += (_, _) => Guard(async () =>
        {
            if (aboutOnly) { ShowAbout(); return; }
            _integrity = Task.Run(() => InstallationIntegrity.Verify(_paths.ProgramRoot));
            if (AppModeResolver.Resolve(_state) == AppMode.Home)
            {
                RenderHome();
                _refreshTimer.Start();
                await RefreshHomeAsync(qualify: true, allowAutoStart: true);
            }
            else { RenderSetup(); await RefreshPreflightAsync(); }
        });
    }

    // ------------------------------------------------------------------ shell

    private static Size FitTo(Size bounds, Size size) =>
        new(Math.Min(bounds.Width, size.Width), Math.Min(bounds.Height, size.Height));

    private Control BuildShell()
    {
        var shell = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, BackColor = Theme.Background };
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 228));
        shell.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        shell.Controls.Add(BuildSidebar(), 0, 0);
        shell.Controls.Add(_content, 1, 0);
        return shell;
    }

    private Control BuildSidebar()
    {
        var panel = new Panel { Dock = DockStyle.Fill, BackColor = Theme.Rail, Padding = new Padding(20, 26, 16, 20), Margin = Padding.Empty };
        panel.Paint += (_, eventArgs) =>
        {
            using var pen = new Pen(Theme.RailBorder);
            eventArgs.Graphics.DrawLine(pen, panel.Width - 1, 0, panel.Width - 1, panel.Height);
        };
        // The lockup lays itself out, so the name and channel never overlap at any scale.
        var lockup = new TableLayoutPanel { AutoSize = true, ColumnCount = 2, RowCount = 2, Location = new Point(20, 26), BackColor = Theme.Rail };
        lockup.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        lockup.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
        var mark = new PictureBox { Size = new Size(36, 36), AccessibleName = "AFK AI", Margin = new Padding(0, 2, 12, 0) };
        mark.Paint += (_, eventArgs) => Theme.PaintWordmark(eventArgs.Graphics, new Rectangle(1, 1, mark.Width - 3, mark.Height - 3));
        var name = new Label { Text = "AFK AI", AutoSize = true, Font = Theme.Display(14, FontStyle.Bold), ForeColor = Theme.PrimaryText, Margin = Padding.Empty };
        var version = new Label { Text = $"Beta · {_product.DisplayVersion}", AutoSize = true, Font = Theme.Font(8.5f), ForeColor = Theme.MutedText, Margin = new Padding(1, 0, 0, 0) };
        lockup.Controls.Add(mark, 0, 0);
        lockup.SetRowSpan(mark, 2);
        lockup.Controls.Add(name, 1, 0);
        lockup.Controls.Add(version, 1, 1);
        panel.Controls.Add(lockup);

        var navigation = new FlowLayoutPanel
        {
            FlowDirection = FlowDirection.TopDown, WrapContents = false, AutoSize = true,
            Location = new Point(12, 132), Width = 200, BackColor = Theme.Rail
        };
        navigation.Controls.Add(SideButton("Home", () => Guard(async () =>
        {
            if (AppModeResolver.Resolve(_state) != AppMode.Home) { RenderSetup(); return; }
            RenderHome();
            _refreshTimer.Start();
            await RefreshHomeAsync(qualify: _home.NeedsQualification);
        })));
        navigation.Controls.Add(SideButton("Setup & repair", () => Guard(async () =>
        {
            if (IsBusy) return;
            RenderSetup();
            await RefreshPreflightAsync();
        })));
        navigation.Controls.Add(SideButton("Diagnostics", () => Guard(OpenDiagnosticsAsync)));
        navigation.Controls.Add(SideButton("Data folder", () => OpenPath(_paths.DataRoot)));
        navigation.Controls.Add(SideButton("About", ShowAbout));
        panel.Controls.Add(navigation);

        var support = SideButton("Support", () => OpenExternal(_product.SupportUrl));
        support.AccessibleName = "Open support";
        // Placed from the rail's real height: a bottom anchor taken before the rail
        // is sized keeps the button below the window at every size.
        panel.Controls.Add(support);
        void PlaceSupport() => support.Location =
            new Point(navigation.Left, Math.Max(navigation.Bottom, panel.ClientSize.Height - panel.Padding.Bottom - support.Height));
        panel.Resize += (_, _) => PlaceSupport();
        PlaceSupport();
        return panel;
    }

    private static Button SideButton(string text, Action action)
    {
        var button = new Button
        {
            Text = text, Width = 196, Height = 38, TextAlign = ContentAlignment.MiddleLeft,
            FlatStyle = FlatStyle.Flat, BackColor = Theme.Rail, ForeColor = Theme.SecondaryText,
            Font = Theme.Font(10), Cursor = Cursors.Hand, Margin = new Padding(0, 0, 0, 2),
            Padding = new Padding(8, 0, 0, 0), AccessibleName = text, UseMnemonic = false
        };
        button.FlatAppearance.BorderSize = 0;
        button.FlatAppearance.MouseOverBackColor = Theme.Elevated;
        button.FlatAppearance.MouseDownBackColor = Theme.SurfaceBorder;
        button.Click += (_, _) => action();
        return button;
    }

    /// <summary>Every UI-triggered operation runs here, so no failure escapes as a crash.</summary>
    private async void Guard(Func<Task> action)
    {
        try { await action(); }
        catch (OperationCanceledException) when (_lifetime.IsCancellationRequested) { }
        catch (Exception exception)
        {
            _log.Append("error", "shell", "failure", exception.GetType().Name);
            EndOperation();
            if (_screen == Screen.Home)
            {
                ApplyObservation(new EngineUnavailable(DateTimeOffset.UtcNow,
                    "Something went wrong while AFK AI was working. Check again, or create a diagnostics report for support.",
                    RepairNeeded: false));
            }
            else
            {
                ShowFailure("Something went wrong", exception.Message);
                SetBusy(false);
            }
        }
    }

    private void Guard(Action action) => Guard(() => { action(); return Task.CompletedTask; });

    private bool IsBusy => _operation is not null;

    private void OnFormClosing(object? sender, FormClosingEventArgs eventArgs)
    {
        if (IsBusy && _operationName is "setup" or "repair" && eventArgs.CloseReason == CloseReason.UserClosing)
        {
            var answer = MessageBox.Show(
                "AFK AI is still setting up. If you close now, setup stops where it is; open AFK AI again to continue.\n\nClose anyway?",
                "AFK AI", MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
            if (answer != DialogResult.Yes) { eventArgs.Cancel = true; return; }
        }
        _refreshTimer.Stop();
        // Cancelling kills the whole child process tree, so no engine process is
        // left holding program files open for an update or uninstall.
        _operation?.Cancel();
        _lifetime.Cancel();
    }

    // ------------------------------------------------------------------- home

    private void RenderHome()
    {
        _screen = Screen.Home;
        _content.Controls.Clear();
        _homeStatus.Font = Theme.Font(10, FontStyle.Bold);
        _homeStatus.Margin = new Padding(2, 0, 0, 6);
        _homeHeadline.Font = Theme.Display(28, FontStyle.Bold);
        _homeHeadline.ForeColor = Theme.PrimaryText;
        _homeHeadline.MaximumSize = new Size(760, 0);
        _homeDetail.Font = Theme.Font(11);
        _homeDetail.ForeColor = Theme.SecondaryText;
        _homeDetail.Margin = new Padding(3, 8, 0, 0);
        _homeModel.Font = Theme.Mono(9.5f);
        _homeModel.ForeColor = Theme.SecondaryText;
        _homeModel.Margin = new Padding(3, 10, 0, 0);
        _homeUpdated.Font = Theme.Font(9);
        _homeUpdated.ForeColor = Theme.MutedText;
        _homeUpdated.Margin = new Padding(3, 6, 0, 0);
        _homeDetailsToggle.LinkColor = Theme.Link;
        _homeDetailsToggle.ActiveLinkColor = Theme.Link;
        _homeDetailsToggle.Font = Theme.Font(9.5f);
        _homeActivity.BackColor = Theme.Sunken;
        _homeActivity.ForeColor = Theme.SecondaryText;
        _homeActivity.Font = Theme.Mono(9);
        _homeServices.BackColor = Theme.Surface;
        _homeServices.Padding = new Padding(18, 10, 18, 10);

        var header = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false, Padding = new Padding(0, 0, 0, 18) };
        header.Controls.Add(_homeStatus);
        header.Controls.Add(_homeHeadline);
        header.Controls.Add(_homeDetail);
        header.Controls.Add(_homeModel);
        header.Controls.Add(_homeUpdated);

        var actions = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(0, 4, 0, 14) };
        foreach (var button in new[] { _homePrimary, _homeCheck, _homeStop, _homeCancel, _homeDiagnostics }) actions.Controls.Add(button);

        var details = new Panel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(0, 6, 0, 6) };
        details.Controls.Add(_homeServices);
        details.Controls.Add(_homeDetailsToggle);
        _homeDetailsToggle.Dock = DockStyle.Top;

        var activityHost = new Panel { Dock = DockStyle.Fill, Padding = new Padding(0, 10, 0, 10) };
        activityHost.Controls.Add(_homeActivity);

        var privacy = new Label
        {
            Text = "On this PC · Local by default: chat and model traffic stay on this PC unless you change the advanced network settings.",
            Dock = DockStyle.Bottom, Padding = new Padding(2, LogicalToDeviceUnits(14), 0, 0),
            ForeColor = Theme.SecondaryText, Font = Theme.Font(9)
        };
        // Room for two lines at any scale, so a narrow window wraps instead of clipping.
        privacy.Height = privacy.Padding.Top + 2 * privacy.Font.Height + LogicalToDeviceUnits(6);

        _content.Controls.Add(activityHost);
        _content.Controls.Add(details);
        _content.Controls.Add(actions);
        _content.Controls.Add(header);
        _content.Controls.Add(privacy);
        AcceptButton = _homePrimary;
        RenderHomeState();
    }

    private void RenderHomeState()
    {
        if (_screen != Screen.Home || IsDisposed) return;
        var now = DateTimeOffset.UtcNow;
        var view = HomePresenter.Present(_home, now);
        _homeHeadline.Text = IsBusy && _operationName is { } name ? OperationHeadline(name) : view.Headline;
        // The headline stays in ink; the state speaks through a word, a symbol and a tone.
        var state = StatusPresentation.ForHome(_home.Kind, IsBusy ? _operationName : null);
        _homeStatus.Text = $"{state.Symbol}  {state.Label}";
        _homeStatus.ForeColor = Theme.ToneColor(state.Tone);
        _homeDetail.Text = view.Detail;
        _homeModel.Text = _home.Latest?.Model is { Length: > 0 } model ? $"Model  {model}" : "";
        _homeModel.Visible = _homeModel.Text.Length > 0;
        _homeUpdated.Text = _home.ObservedAt is { } at ? $"Last checked {at.ToLocalTime():t}" : "";
        _homeAction = view.Primary;
        _homePrimary.Text = view.PrimaryLabel;
        _homePrimary.Enabled = view.PrimaryEnabled && !IsBusy;
        _homeCheck.Enabled = !IsBusy && !_probeInFlight;
        _homeCheck.Visible = view.Primary != HomeAction.Check;
        _homeStop.Visible = view.CanStop && !IsBusy;
        _homeCancel.Visible = IsBusy && _operationName == "start";
        _homeDiagnostics.Enabled = !IsBusy;
        _homeDiagnostics.Visible = view.Primary != HomeAction.Diagnostics;
        RenderServices(_home.Latest?.Services ?? Array.Empty<ProductService>());
    }

    private static string OperationHeadline(string operation) => operation switch
    {
        "start" => "Starting AFK AI",
        "stop" => "Stopping AFK AI",
        "open-chat" => "Checking chat",
        "diagnostics" => "Creating a diagnostics report",
        _ => "Working"
    };

    private void RenderServices(IReadOnlyList<ProductService> services)
    {
        _homeServices.SuspendLayout();
        _homeServices.Controls.Clear();
        _homeServices.RowStyles.Clear();
        _homeServices.RowCount = Math.Max(1, services.Count);
        for (var index = 0; index < services.Count; index++)
        {
            var service = services[index];
            _homeServices.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            var shown = StatusPresentation.ForState(service.State);
            _homeServices.Controls.Add(new Label { Text = service.Name, AutoSize = true, ForeColor = Theme.PrimaryText, Font = Theme.Font(9.5f, FontStyle.Bold), Margin = new Padding(0, 4, 24, 4) }, 0, index);
            _homeServices.Controls.Add(new Label { Text = $"{shown.Symbol}  {shown.Label}", AutoSize = true, ForeColor = Theme.ToneColor(shown.Tone), Font = Theme.Font(9.5f, FontStyle.Bold), Margin = new Padding(0, 4, 24, 4) }, 1, index);
            _homeServices.Controls.Add(new Label { Text = service.Detail, AutoSize = true, ForeColor = Theme.MutedText, Font = Theme.Font(9.5f), Margin = new Padding(0, 4, 0, 4) }, 2, index);
        }
        _homeServices.ResumeLayout();
    }

    private async Task<Observation> ObserveAsync(bool liveness, CancellationToken cancellationToken)
    {
        var at = DateTimeOffset.UtcNow;
        var integrity = await (_integrity ??= Task.Run(() => InstallationIntegrity.Verify(_paths.ProgramRoot)));
        if (!integrity.Intact)
        {
            return new EngineUnavailable(at,
                $"{integrity.Summary} Reinstall AFK AI to repair it; your chats and settings are kept.", RepairNeeded: true);
        }
        try
        {
            using var timeout = CancellationTokenSource.CreateLinkedTokenSource(cancellationToken);
            timeout.CancelAfter(liveness ? TimeSpan.FromSeconds(60) : TimeSpan.FromMinutes(4));
            var result = await _runner.RunAsync(_controller.Status(liveness), null, timeout.Token);
            var status = ProductStatusParser.TryParse(result.StandardOutput);
            return status is null
                ? new EngineUnavailable(DateTimeOffset.UtcNow, "AFK AI could not read its own status.", RepairNeeded: result.ExitCode == 3)
                : new StatusObserved(DateTimeOffset.UtcNow, status);
        }
        catch (OperationCanceledException) when (!cancellationToken.IsCancellationRequested)
        {
            return new EngineUnavailable(DateTimeOffset.UtcNow, "Checking AFK AI took too long. Check again in a moment.", RepairNeeded: false);
        }
        catch (Exception exception) when (exception is Win32Exception or InvalidOperationException or IOException)
        {
            return new EngineUnavailable(DateTimeOffset.UtcNow,
                "AFK AI's own runtime could not start. Reinstall AFK AI to repair it; your chats and settings are kept.", RepairNeeded: true);
        }
    }

    private void ApplyObservation(Observation observation)
    {
        var before = _home;
        _home = HomeReducer.Reduce(_home, observation);
        if (before.Kind != _home.Kind || before.Latest?.Reason != _home.Latest?.Reason)
        {
            _log.Append("status", "status", _home.Kind.ToString(),
                _home.Latest?.Reason ?? (observation is EngineUnavailable u && u.RepairNeeded ? "RUNTIME_UNVERIFIED" : "ENGINE_UNAVAILABLE"));
        }
        RenderHomeState();
    }

    private async Task RefreshHomeAsync(bool qualify, bool allowAutoStart = false)
    {
        if (_probeInFlight || IsBusy) return;
        _probeInFlight = true;
        RenderHomeState();
        try
        {
            ApplyObservation(await ObserveAsync(liveness: !qualify, _lifetime.Token));
        }
        finally
        {
            _probeInFlight = false;
            RenderHomeState();
        }

        // "Open AFK AI and it gets itself ready": start once per launch when the
        // engine says the only thing missing is AFK AI's own runtime - never
        // after the person chose Stop, and never for a repair or collision.
        var view = HomePresenter.Present(_home, DateTimeOffset.UtcNow);
        if (allowAutoStart && !_autoStartAttempted && !_userStopped &&
            _home.Kind == HomeKind.Stopped && view.Primary == HomeAction.Start)
        {
            _autoStartAttempted = true;
            await StartAsync();
        }
    }

    private async Task OnRefreshTickAsync()
    {
        if (_screen != Screen.Home || IsBusy || _probeInFlight || WindowState == FormWindowState.Minimized || !Visible) return;
        var now = DateTimeOffset.UtcNow;
        if (RefreshPolicy.ShouldAutoQualify(_home, _lastAutoQualify, now))
        {
            _lastAutoQualify = now;
            await RefreshHomeAsync(qualify: true);
            return;
        }
        if (_home.ObservedAt is not { } last || now - last >= RefreshPolicy.LivenessInterval(_home.Kind))
            await RefreshHomeAsync(qualify: false);
    }

    private async Task OnActivatedAsync()
    {
        if (_screen != Screen.Home || _home.ObservedAt is not { } last) return;
        if (DateTimeOffset.UtcNow - last >= RefreshPolicy.ActivationStaleness) await RefreshHomeAsync(qualify: false);
    }

    private Task RunHomeActionAsync() => _homeAction switch
    {
        HomeAction.OpenChat => OpenChatAsync(),
        HomeAction.Start => StartAsync(),
        HomeAction.Repair => RepairAsync(),
        HomeAction.Check => RefreshHomeAsync(qualify: true),
        HomeAction.Diagnostics => OpenDiagnosticsAsync(),
        _ => Task.CompletedTask
    };

    /// <summary>Open Chat is a gate: fresh engine evidence first, browser second.</summary>
    private async Task OpenChatAsync()
    {
        var decision = ChatGate.BeforeOpen(_home, DateTimeOffset.UtcNow);
        var operation = BeginOperation("open-chat");
        try
        {
            ApplyObservation(await ObserveAsync(liveness: decision == ChatGateDecision.ProbeLiveness, operation));
        }
        finally { EndOperation(); }

        if (ChatGate.CanOpen(_home, DateTimeOffset.UtcNow))
        {
            _log.Append("chat", "open-chat", "opened", "READY");
            OpenExternal(_home.Qualified!.ChatUrl);
        }
        else
        {
            _log.Append("chat", "open-chat", "refused", _home.Latest?.Reason ?? _home.Kind.ToString());
        }
    }

    private async Task StartAsync()
    {
        _userStopped = false;
        var operation = BeginOperation("start");
        ClearActivity();
        AppendActivity("Starting AFK AI. The first start can take a few minutes.");
        try
        {
            var integrity = await (_integrity ??= Task.Run(() => InstallationIntegrity.Verify(_paths.ProgramRoot)));
            if (!integrity.Intact)
            {
                ApplyObservation(await ObserveAsync(liveness: true, operation));
                return;
            }
            var result = await _runner.RunAsync(_controller.Start(), OnEngineLine, operation);
            var status = ProductStatusParser.TryParse(result.StandardOutput, ProductStatusParser.StartStatusPrefix);
            ApplyObservation(status is null
                ? new EngineUnavailable(DateTimeOffset.UtcNow, "AFK AI could not confirm that it started. Check again, or create a diagnostics report.", result.ExitCode == 3)
                : new StatusObserved(DateTimeOffset.UtcNow, status));
            _log.Append("start", "start", status?.State ?? "unknown", status?.Reason ?? $"exit-{result.ExitCode}");
        }
        catch (OperationCanceledException) when (!_lifetime.IsCancellationRequested)
        {
            AppendActivity("Start cancelled.");
            _log.Append("start", "start", "cancelled", "cancelled");
        }
        catch (Win32Exception)
        {
            ApplyObservation(new EngineUnavailable(DateTimeOffset.UtcNow,
                "AFK AI's own runtime could not start. Reinstall AFK AI to repair it; your chats and settings are kept.", RepairNeeded: true));
        }
        finally { EndOperation(); }
        if (_home.Kind != HomeKind.Ready && _home.Kind != HomeKind.RepairNeeded) await RefreshHomeAsync(qualify: false);
    }

    private async Task StopAsync()
    {
        _userStopped = true;
        var operation = BeginOperation("stop");
        ClearActivity();
        try
        {
            var result = await _runner.RunAsync(_controller.Stop(), AppendActivity, operation);
            _log.Append("stop", "stop", result.Succeeded ? "success" : "failure", $"exit-{result.ExitCode}");
        }
        catch (Win32Exception)
        {
            AppendActivity("AFK AI's own runtime could not start, so nothing was stopped.");
        }
        finally { EndOperation(); }
        await RefreshHomeAsync(qualify: false);
    }

    private async Task RepairAsync()
    {
        var answer = MessageBox.Show(
            "Repair re-runs AFK AI's setup steps: it re-checks this PC, re-downloads the chat model if it is missing, " +
            "and recreates AFK AI's own services.\n\nYour chats, accounts and settings are kept. Nothing outside AFK AI is removed.\n\nRepair now?",
            "Repair AFK AI", MessageBoxButtons.YesNo, MessageBoxIcon.Question, MessageBoxDefaultButton.Button1);
        if (answer != DialogResult.Yes) return;
        var integrity = await (_integrity ??= Task.Run(() => InstallationIntegrity.Verify(_paths.ProgramRoot)));
        if (!integrity.Intact)
        {
            MessageBox.Show(
                $"{integrity.Summary}\n\nAFK AI's program files need to be reinstalled before it can repair itself. " +
                "Run the AFK AI installer again; your chats and settings are kept.",
                "Repair AFK AI", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        RenderSetup();
        await RunProvisioningAsync(repair: true);
    }

    private void OnEngineLine(string line)
    {
        if (InvokeRequired) { BeginInvoke(() => OnEngineLine(line)); return; }
        if (line.StartsWith(ProductStatusParser.StartStatusPrefix, StringComparison.Ordinal)) return;
        if (ProvisioningEvent.TryParse(line, out var parsed) && parsed is not null)
        {
            if (parsed.EventType != "output") _log.Append(parsed);
            AppendActivity(parsed.Message);
            return;
        }
        AppendActivity(line);
    }

    private CancellationToken BeginOperation(string name)
    {
        _operation?.Dispose();
        _operation = CancellationTokenSource.CreateLinkedTokenSource(_lifetime.Token);
        _operationName = name;
        UseWaitCursor = name != "start";
        if (_screen == Screen.Home) RenderHomeState();
        return _operation.Token;
    }

    private void EndOperation()
    {
        _operation?.Dispose();
        _operation = null;
        _operationName = null;
        UseWaitCursor = false;
        if (_screen == Screen.Home) RenderHomeState();
    }

    private void ClearActivity()
    {
        _homeActivity.Clear();
        _homeActivity.Visible = true;
    }

    private void AppendActivity(string? text)
    {
        if (InvokeRequired) { BeginInvoke(() => AppendActivity(text)); return; }
        if (string.IsNullOrWhiteSpace(text) || _homeActivity.IsDisposed) return;
        _homeActivity.Visible = true;
        _homeActivity.AppendText((string.IsNullOrEmpty(_homeActivity.Text) ? "" : Environment.NewLine) + text.Trim());
    }

    // ------------------------------------------------------------------ setup

    private void RenderSetup()
    {
        _screen = Screen.Setup;
        _refreshTimer.Stop();
        _content.Controls.Clear();
        _headline.Text = _state.SetupCompleted ? "Setup & repair" : "Let’s get this PC ready";
        _headline.Font = Theme.Display(28, FontStyle.Bold);
        _headline.ForeColor = Theme.PrimaryText;
        _subtitle.Text = "AFK AI checks Windows, virtualization, WSL, Docker, and your hardware before it downloads anything large.";
        _subtitle.Font = Theme.Font(11);
        _subtitle.ForeColor = Theme.SecondaryText;
        _subtitle.Margin = new Padding(3, 8, 0, 0);

        _status.AccessibleName = "Prerequisite status";
        _status.BackColor = Theme.Surface;
        _status.Dock = DockStyle.Fill;
        _status.ColumnCount = 4;
        _status.ColumnStyles.Clear();
        _status.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 30));
        _status.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 150));
        _status.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 190));
        _status.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
        SetStatusRows(new Dictionary<string, string> { ["Windows"] = "Checking", ["Virtualization"] = "Checking", ["WSL"] = "Checking", ["Docker"] = "Checking", ["Hardware"] = "Pending" });

        _setupMessage.Font = Theme.Font(11);
        _setupMessage.ForeColor = Theme.PrimaryText;
        _setupMessage.Text = "Checking this PC…";
        _setupStage.Font = Theme.Font(10);
        _setupStage.ForeColor = Theme.SecondaryText;
        _progress.BackColor = Theme.Sunken;
        _progress.ForeColor = Theme.SecondaryText;
        _progress.Font = Theme.Mono(9);
        _progressLabel.Font = Theme.Font(9.5f);
        _progressLabel.LinkColor = _progressLabel.ActiveLinkColor = Theme.Link;
        _progress.Text = "Waiting for the prerequisite check…";
        _setupDownload.Visible = false;

        var header = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false, Padding = new Padding(0, 0, 0, 22) };
        header.Controls.Add(_headline);
        header.Controls.Add(_subtitle);
        var statusSurface = new SurfacePanel { Dock = DockStyle.Fill, Padding = new Padding(18, 12, 18, 12) };
        statusSurface.Controls.Add(_status);
        var statusHost = new Panel { Dock = DockStyle.Top, Height = 234, Padding = new Padding(0, 0, 0, 22) };
        statusHost.Controls.Add(statusSurface);
        var messageHost = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.TopDown, WrapContents = false, Padding = new Padding(0, 0, 0, 10) };
        messageHost.Controls.Add(_setupMessage);
        messageHost.Controls.Add(_setupStage);
        var progressHost = new Panel { Dock = DockStyle.Fill, Padding = new Padding(0, 6, 0, 16) };
        progressHost.Controls.Add(_progress);
        progressHost.Controls.Add(_setupDownload);
        SetSetupDetails(false);
        // The next step sits right under the message that explains it.
        var actions = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, FlowDirection = FlowDirection.LeftToRight, Padding = new Padding(0, 8, 0, 6) };
        actions.Controls.Add(_primary);
        actions.Controls.Add(_retry);
        var diagnostics = Theme.Button("Diagnostics");
        diagnostics.AccessibleName = "Open diagnostics";
        diagnostics.Click += (_, _) => Guard(OpenDiagnosticsAsync);
        actions.Controls.Add(diagnostics);

        var detailsToggle = new Panel { Dock = DockStyle.Top, Height = 34 };
        detailsToggle.Controls.Add(_progressLabel);
        _progressLabel.Location = new Point(0, 8);

        // Docked top controls stack in reverse order of adding.
        _content.Controls.Add(progressHost);
        _content.Controls.Add(detailsToggle);
        _content.Controls.Add(actions);
        _content.Controls.Add(messageHost);
        _content.Controls.Add(statusHost);
        _content.Controls.Add(header);
        AcceptButton = _primary;
    }

    /// <summary>The engine's full log is one click away; it opens by itself while setup runs.</summary>
    private void SetSetupDetails(bool visible)
    {
        _progress.Visible = visible;
        _progressLabel.Text = visible ? "Hide setup details" : "Show setup details";
    }

    private async Task RefreshPreflightAsync()
    {
        if (IsBusy) return;
        SetBusy(true, "Checking this PC…");
        try
        {
            ProcessResult result;
            try { result = await _runner.RunAsync(_controller.Preflight(), null, _lifetime.Token); }
            catch (Win32Exception)
            {
                ShowFailure("AFK AI could not check this PC", "Windows PowerShell could not be started.");
                return;
            }
            var json = result.StandardOutput.Split(new[] { "\r\n", "\n" }, StringSplitOptions.RemoveEmptyEntries).LastOrDefault(line => line.TrimStart().StartsWith('{'));
            if (json is null)
            {
                ShowFailure("The prerequisite check did not return a result.", result.StandardError);
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
        }
        finally { SetBusy(false); }
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
        _setupMessage.Text = StatusPresentation.Reflow(summary.UserMessage);
        if (summary.Overall == "READY")
        {
            _pendingAction = _state.SetupCompleted ? "repair" : "provision";
            _primary.Text = _state.SetupCompleted ? "Repair AFK AI" : "Set up AFK AI";
            _headline.Text = _state.SetupCompleted ? "This PC is ready" : "This PC is ready for AFK AI";
            return;
        }
        RecoveryAction recovery;
        try { recovery = RecoveryAction.ForCode(summary.Code); }
        catch (ArgumentOutOfRangeException)
        {
            _pendingAction = "retry";
            _primary.Text = "Check again";
            _headline.Text = "Setup needs your attention";
            return;
        }
        _pendingAction = recovery.ActionId;
        _primary.Text = recovery.Label;
        _headline.Text = summary.Overall == "RECOVERABLE_BLOCKER" ? "One step is needed" : "Setup needs your attention";
    }

    private async Task RunPrimaryActionAsync()
    {
        if (_pendingAction == "provision") { await RunProvisioningAsync(repair: false); return; }
        if (_pendingAction == "repair") { await RepairAsync(); return; }
        if (_preflight is null) { await RefreshPreflightAsync(); return; }
        RecoveryAction recovery;
        try { recovery = RecoveryAction.ForCode(_preflight.Code); }
        catch (ArgumentOutOfRangeException) { await RefreshPreflightAsync(); return; }
        if (!recovery.Automatic)
        {
            MessageBox.Show($"{recovery.Explanation}\n\nReason code: {_preflight.Code}\n\nOpen Diagnostics if you need help sharing this result with support.",
                "AFK AI setup", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        SetBusy(true, $"{recovery.Label}…");
        try
        {
            // stderr is streamed live by the runner; re-appending it here would
            // print every error line twice.
            await _runner.RunAsync(_controller.Recovery(_preflight.Code, recovery.ActionId), AppendProcessLine, _lifetime.Token);
        }
        catch (Win32Exception)
        {
            ShowFailure("AFK AI could not run that step", "Windows PowerShell could not be started.");
        }
        finally { SetBusy(false); }
        await RefreshPreflightAsync();
    }

    private async Task RunProvisioningAsync(bool repair)
    {
        if (IsBusy) return;
        // Void the previous qualification BEFORE anything runs: if this fails or
        // is cancelled, a Ready from before it must not survive through liveness.
        _home = HomeReducer.Reduce(_home, new LifecycleOperationStarted(DateTimeOffset.UtcNow, repair ? "repair" : "setup"));
        var operation = BeginOperation(repair ? "repair" : "setup");
        SetBusy(true, repair ? "Repairing AFK AI…" : "Setting up AFK AI…");
        _progress.Clear();
        SetSetupDetails(true);
        _headline.Text = repair ? "Repairing AFK AI" : "Setting up AFK AI";
        _setupMessage.Text = "This takes a while the first time: AFK AI downloads its runtime and a model that fits this PC.";
        _setupStage.Visible = true;
        ProcessResult? result = null;
        try
        {
            var integrity = await (_integrity ??= Task.Run(() => InstallationIntegrity.Verify(_paths.ProgramRoot)));
            if (!integrity.Intact)
            {
                ShowFailure("AFK AI needs to be reinstalled",
                    $"{integrity.Summary}\r\nRun the AFK AI installer again; your chats and settings are kept.");
                return;
            }
            result = await _runner.RunAsync(_controller.Provision(repair), AppendProcessLine, operation);
        }
        catch (OperationCanceledException) when (!_lifetime.IsCancellationRequested)
        {
            AppendText("Setup was cancelled. Open AFK AI again to continue.");
        }
        catch (Win32Exception)
        {
            ShowFailure("Setup could not start", "Windows PowerShell could not be started.");
        }
        finally
        {
            EndOperation();
            SetBusy(false);
            _setupDownload.Visible = false;
            _setupStage.Visible = false;
        }
        if (result is null) return;

        _log.Append(repair ? "repair" : "provision", repair ? "repair" : "provision",
            result.ExitCode == 0 ? "success" : "failure", $"exit-{result.ExitCode}");
        if (result.ExitCode == 0)
        {
            _state.SetupCompleted = true;
            _state.LastReasonCode = repair ? "REPAIR-COMPLETE" : "SETUP-COMPLETE";
            _stateStore.SaveAtomic(_state);
            // Setup finishing is not a readiness claim: Home asks the engine.
            _home = HomeModel.Initial;
            RenderHome();
            _refreshTimer.Start();
            await RefreshHomeAsync(qualify: true);
        }
        else if (result.ExitCode == 10) await RefreshPreflightAsync();
        else ShowFailure(repair ? "Repair stopped before AFK AI was ready" : "Setup stopped before AFK AI was ready",
            "The step that failed is shown above.");
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
            var shown = StatusPresentation.ForState(pair.Value);
            var tone = Theme.ToneColor(shown.Tone);
            var symbol = new Label { Text = shown.Symbol, AutoSize = true, ForeColor = tone, Font = Theme.Font(11, FontStyle.Bold), Anchor = AnchorStyles.Left };
            var name = new Label { Text = pair.Key, AutoSize = true, ForeColor = Theme.PrimaryText, Font = Theme.Font(10, FontStyle.Bold), Anchor = AnchorStyles.Left };
            var state = new Label { Text = shown.Label, AutoSize = true, ForeColor = tone, Font = Theme.Font(10, FontStyle.Bold), Anchor = AnchorStyles.Left, AccessibleName = $"{pair.Key}: {shown.Label}" };
            var detail = new Label { Text = StatusDetail(pair.Key, pair.Value), AutoSize = true, ForeColor = Theme.MutedText, Anchor = AnchorStyles.Left };
            _status.Controls.Add(symbol, 0, index); _status.Controls.Add(name, 1, index); _status.Controls.Add(state, 2, index); _status.Controls.Add(detail, 3, index);
            index++;
        }
        _status.ResumeLayout();
    }

    private static Color StatusColor(string value) => value.ToUpperInvariant() switch
    {
        "READY" or "SUPPORTED" => Theme.Success,
        "CHECKING" or "PENDING" or "STARTING" or "NOTCHECKED" => Theme.Warning,
        "UNKNOWN" or "NOT CHECKED" or "NOTREQUIRED" => Theme.MutedText,
        _ => Theme.Failure
    };

    private static string StatusDetail(string name, string value) => name switch
    {
        "Windows" => "64-bit Windows 11",
        "Virtualization" => "Firmware and Windows platform",
        "WSL" => "Only when the Docker backend needs it",
        "Docker" => "Local Linux engine",
        _ => value == "Pending" ? "Chosen after the checks above" : "The model that fits this PC is chosen during setup"
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
        if (ProvisioningEvent.TryParse(line, out var parsed) && parsed is not null)
        {
            if (parsed.EventType != "output") _log.Append(parsed);
            if (parsed.Percent is { } percent)
            {
                _setupDownload.Visible = true;
                _setupDownload.Value = percent;
                return;
            }
            AppendText(parsed.Message);
            if (!string.IsNullOrWhiteSpace(parsed.Message)) _setupStage.Text = parsed.Message.Trim();
            return;
        }
        AppendText(line);
    }

    private void AppendText(string? text)
    {
        if (string.IsNullOrWhiteSpace(text) || _progress.IsDisposed) return;
        _progress.AppendText((string.IsNullOrEmpty(_progress.Text) ? "" : Environment.NewLine) + text.Trim());
    }

    private void ShowFailure(string headline, string detail)
    {
        _headline.Text = headline;
        _setupMessage.Text = detail.Trim();
        _progress.Text = $"{detail.Trim()}\r\n\r\nOpen Diagnostics for the reason code and support-ready details.";
    }

    // ------------------------------------------------------ diagnostics/about

    private async Task OpenDiagnosticsAsync()
    {
        if (IsBusy) return;
        var operation = BeginOperation("diagnostics");
        string file;
        try
        {
            var integrity = await (_integrity ??= Task.Run(() => InstallationIntegrity.Verify(_paths.ProgramRoot)));
            file = await DiagnosticsWriter.CreateAsync(_paths, _product, _controller, _runner, integrity, _home, operation);
        }
        finally { EndOperation(); }
        _log.Append("diagnostics", "diagnostics", "created", "report");
        Process.Start(new ProcessStartInfo("explorer.exe", $"/select,\"{file}\"") { UseShellExecute = true });
    }

    private void ShowAbout()
    {
        _screen = Screen.About;
        _refreshTimer.Stop();
        _content.Controls.Clear();
        _content.Controls.Add(Body($"Version {_product.DisplayVersion}  •  Beta\n\nA local-first AI workspace for Windows. Chat runs on this PC.\n\nSource: {_product.Repository}\nSupport: {_product.SupportUrl}\n\nAFK AI is not affiliated with the upstream LocalAI project by mudler."));
        _content.Controls.Add(Heading("About AFK AI", 25));
    }

    private static Label Heading(string text, float size) => new()
    {
        Text = text, AutoSize = true, Dock = DockStyle.Top, Padding = new Padding(0, 0, 0, 12),
        Font = Theme.Display(size, FontStyle.Bold), ForeColor = Theme.PrimaryText
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

    /// <summary>Only http(s) URLs the product itself supplies are handed to the shell.</summary>
    private static void OpenExternal(string uri)
    {
        if (!Uri.TryCreate(uri, UriKind.Absolute, out var parsed) ||
            parsed.Scheme is not ("http" or "https")) return;
        Process.Start(new ProcessStartInfo(parsed.AbsoluteUri) { UseShellExecute = true });
    }
}
