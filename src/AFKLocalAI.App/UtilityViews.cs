namespace AFKLocalAI.App;

internal static class UtilityViewParts
{
    public static Label Heading(string text) => new()
    {
        Text = text, AutoSize = true, Font = Theme.Display(27, FontStyle.Bold),
        ForeColor = Theme.PrimaryText, Margin = new Padding(0, 0, 0, 8)
    };

    public static Label Body(string text = "") => new()
    {
        Text = text, AutoSize = true, Font = Theme.Font(10), ForeColor = Theme.SecondaryText,
        MaximumSize = new Size(720, 0), Margin = new Padding(0, 0, 0, 7)
    };

    public static Label Section(string text) => new()
    {
        Text = text, AutoSize = true, Font = Theme.Display(14, FontStyle.Bold),
        ForeColor = Theme.PrimaryText, Margin = new Padding(0, 10, 0, 4)
    };

    public static TableLayoutPanel Stack() => new()
    {
        Dock = DockStyle.Top, AutoSize = true, ColumnCount = 1,
        Margin = Padding.Empty, Padding = Padding.Empty
    };

    public static void Add(TableLayoutPanel stack, Control control)
    {
        control.Dock = DockStyle.Top;
        stack.Controls.Add(control, 0, stack.RowCount++);
        stack.RowStyles.Add(new RowStyle(SizeType.AutoSize));
    }

    public static string Gib(long? bytes) => bytes is null ? "Unknown" :
        $"{bytes.Value / 1073741824d:0.#} GiB";

    public static string Context(int? context) => context is null or <= 0 ? "Unknown" :
        context.Value >= 1024 ? $"{context.Value / 1024}K" : context.Value.ToString();

    public static void Primary(Button button, bool primary)
    {
        button.BackColor = primary ? Theme.Action : Theme.Surface;
        button.ForeColor = primary ? Color.White : Theme.PrimaryText;
        button.FlatAppearance.BorderColor = primary ? Theme.Action : Theme.Border;
        button.FlatAppearance.MouseOverBackColor = primary ? Theme.ActionHover : Theme.Elevated;
        button.FlatAppearance.MouseDownBackColor = primary ? Color.Black : Theme.Sunken;
    }
}

/// <summary>Installed models first; detail is disclosed only for the selected row.</summary>
public sealed class ModelsFitPage : UserControl
{
    public event Action? RefreshRequested;
    public event Action? SetupRequested;
    public event Action<string>? OptimizeRequested;
    public event Action<string>? UseRequested;
    public event Action? CancelRequested;

    private readonly Label _cpu = new() { AutoSize = true, Text = "Checking…" };
    private readonly Label _ram = new() { AutoSize = true, Text = "Unknown" };
    private readonly Label _gpu = new() { AutoSize = true, Text = "Checking…" };
    private readonly Label _vram = new() { AutoSize = true, Text = "Unknown" };
    private readonly Label _choiceHeading = UtilityViewParts.Section("Best installed choice");
    private readonly Label _installedHeading = UtilityViewParts.Section("Installed models");
    private readonly Label _bestModel = new()
    {
        AutoSize = true, Font = Theme.Display(16, FontStyle.Bold),
        ForeColor = Theme.PrimaryText, Margin = new Padding(0, 0, 0, 4)
    };
    private readonly Label _recommendation = UtilityViewParts.Body("Checking installed models…");
    private readonly Label _state = UtilityViewParts.Body();
    private readonly Label _detailVerdict = new() { AutoSize = true, Font = Theme.Font(10, FontStyle.Bold), ForeColor = Theme.PrimaryText };
    private readonly Label _detailReason = UtilityViewParts.Body();
    private readonly ListView _models = new()
    {
        View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false,
        Height = 84, Font = Theme.Font(10), AccessibleName = "Installed model fit",
        BorderStyle = BorderStyle.None, BackColor = Theme.Surface, ForeColor = Theme.PrimaryText,
        OwnerDraw = true, HeaderStyle = ColumnHeaderStyle.Nonclickable
    };
    private readonly TextBox _detail = new()
    {
        ReadOnly = true, Multiline = true, ScrollBars = ScrollBars.Vertical,
        BackColor = Theme.Sunken, ForeColor = Theme.SecondaryText, BorderStyle = BorderStyle.None,
        Font = Theme.Mono(9), AccessibleName = "Why this model fits"
    };
    private readonly Button _use = Theme.Button("Use selected model", primary: true);
    private readonly Button _setup = Theme.Button("Set up and install a model");
    private readonly Button _optimize = Theme.Button("Optimization");
    private readonly Button _refresh = Theme.Button("Check again");
    private readonly Button _cancel = Theme.Button("Cancel");
    private UtilityReport? _report;

    public ModelsFitPage()
    {
        Dock = DockStyle.Fill;
        AutoScroll = true;
        BackColor = Theme.Background;
        var stack = UtilityViewParts.Stack();
        UtilityViewParts.Add(stack, UtilityViewParts.Heading("Models & fit"));
        UtilityViewParts.Add(stack, UtilityViewParts.Body("A clear view of what is installed and what this PC can run."));
        UtilityViewParts.Add(stack, UtilityViewParts.Section("This PC"));
        var machine = new SurfacePanel { Height = 99, Padding = new Padding(18, 9, 18, 9) };
        var machineGrid = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 2, BackColor = Theme.Surface };
        machineGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 72));
        machineGrid.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 28));
        machineGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
        machineGrid.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
        machineGrid.Controls.Add(HardwareFact("CPU", _cpu), 0, 0);
        machineGrid.Controls.Add(HardwareFact("RAM", _ram), 1, 0);
        machineGrid.Controls.Add(HardwareFact("GPU", _gpu), 0, 1);
        machineGrid.Controls.Add(HardwareFact("VRAM", _vram), 1, 1);
        machine.Controls.Add(machineGrid);
        UtilityViewParts.Add(stack, machine);
        var recommendationSurface = new SurfacePanel { Height = 139, Padding = new Padding(18, 11, 18, 9), Margin = new Padding(0, 11, 0, 0) };
        var recommendationStack = UtilityViewParts.Stack();
        recommendationStack.BackColor = Theme.Surface;
        _choiceHeading.Margin = new Padding(0, 0, 0, 6);
        _recommendation.Margin = new Padding(0, 0, 0, 6);
        _state.Margin = Padding.Empty;
        UtilityViewParts.Add(recommendationStack, _choiceHeading);
        UtilityViewParts.Add(recommendationStack, _bestModel);
        UtilityViewParts.Add(recommendationStack, _recommendation);
        UtilityViewParts.Add(recommendationStack, _state);
        recommendationSurface.Controls.Add(recommendationStack);
        UtilityViewParts.Add(stack, recommendationSurface);
        var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Top, WrapContents = true,
            Margin = new Padding(0, 10, 0, 8) };
        foreach (var button in new[] { _use, _setup, _optimize, _refresh, _cancel }) actions.Controls.Add(button);
        UtilityViewParts.Add(stack, actions);
        UtilityViewParts.Add(stack, _installedHeading);
        _models.Columns.Add("Model", 350);
        _models.Columns.Add("Fit at 8K", 125);
        _models.Columns.Add("Size", 90);
        _models.Columns.Add("State", 125);
        var rows = new ImageList { ImageSize = new Size(1, 32), ColorDepth = ColorDepth.Depth32Bit };
        rows.Images.Add(new Bitmap(1, 32));
        _models.SmallImageList = rows;
        _models.DrawColumnHeader += (_, e) => e.DrawDefault = true;
        _models.DrawSubItem += DrawModelCell;
        _models.Resize += (_, _) => ResizeModelColumns();
        UtilityViewParts.Add(stack, _models);
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Why this model?"));
        var detailSurface = new SurfacePanel { Height = 194, Padding = new Padding(16, 12, 16, 12), Fill = Theme.Sunken };
        var detailLayout = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, RowCount = 3, BackColor = Theme.Sunken };
        detailLayout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        detailLayout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        detailLayout.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
        _detailReason.MaximumSize = new Size(650, 0);
        _detailReason.Margin = new Padding(0, 4, 0, 8);
        _detail.Dock = DockStyle.Fill;
        detailLayout.Controls.Add(_detailVerdict, 0, 0);
        detailLayout.Controls.Add(_detailReason, 0, 1);
        detailLayout.Controls.Add(_detail, 0, 2);
        detailSurface.Controls.Add(detailLayout);
        UtilityViewParts.Add(stack, detailSurface);
        Controls.Add(stack);
        Resize += (_, _) => stack.Width = ClientSize.Width - Padding.Horizontal - 18;
        _models.SelectedIndexChanged += (_, _) => ShowSelection();
        _use.AccessibleName = "Use selected installed model";
        _setup.AccessibleName = "Set up and install a model";
        _refresh.AccessibleName = "Refresh model fit";
        _cancel.AccessibleName = "Cancel Utility operation";
        _use.Click += (_, _) => { if (SelectedModel is { } tag) UseRequested?.Invoke(tag); };
        _setup.Click += (_, _) => SetupRequested?.Invoke();
        _optimize.AccessibleName = "Optimize selected installed model";
        _optimize.Click += (_, _) => { if (SelectedModel is { } tag) OptimizeRequested?.Invoke(tag); };
        _refresh.Click += (_, _) => RefreshRequested?.Invoke();
        _cancel.Click += (_, _) => CancelRequested?.Invoke();
        SetBusy(false, "");
    }

    private static Control HardwareFact(string title, Label value)
    {
        var host = new FlowLayoutPanel { Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown,
            WrapContents = false, BackColor = Theme.Surface, Margin = Padding.Empty };
        host.Controls.Add(new Label { Text = title, AutoSize = true, Font = Theme.Font(8.5f, FontStyle.Bold),
            ForeColor = Theme.MutedText, Margin = Padding.Empty });
        value.Font = Theme.Font(10, FontStyle.Bold);
        value.ForeColor = Theme.PrimaryText;
        value.Margin = new Padding(0, 2, 4, 0);
        host.Controls.Add(value);
        return host;
    }

    private void ResizeModelColumns()
    {
        if (_models.Columns.Count != 4) return;
        _models.Columns[0].Width = Math.Max(240, _models.ClientSize.Width - 125 - 90 - 125 - 4);
    }

    private void DrawModelCell(object? sender, DrawListViewSubItemEventArgs e)
    {
        var item = e.Item;
        if (item is null) return;
        var selected = item.Selected;
        var highContrast = SystemInformation.HighContrast;
        var background = selected ? (highContrast ? SystemColors.Highlight : Theme.Elevated) : Theme.Surface;
        var foreground = selected && highContrast ? SystemColors.HighlightText :
            e.ColumnIndex == 0 ? Theme.PrimaryText : Theme.SecondaryText;
        using (var fill = new SolidBrush(background)) e.Graphics.FillRectangle(fill, e.Bounds);
        using (var line = new Pen(Theme.SurfaceBorder))
            e.Graphics.DrawLine(line, e.Bounds.Left, e.Bounds.Bottom - 1, e.Bounds.Right, e.Bounds.Bottom - 1);
        var font = e.ColumnIndex == 0 ? Theme.Font(9.5f, FontStyle.Bold) : Theme.Font(9.5f);
        using (font)
            TextRenderer.DrawText(e.Graphics, e.SubItem?.Text ?? "", font,
                Rectangle.Inflate(e.Bounds, -7, 0), foreground,
                TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis | TextFormatFlags.NoPrefix);
        if (item.Focused && e.ColumnIndex == 0 && _models.Focused)
            ControlPaint.DrawFocusRectangle(e.Graphics, item.Bounds);
    }

    public string? SelectedModel => _models.SelectedItems.Count > 0
        ? _models.SelectedItems[0].Tag as string : null;

    public void SetBusy(bool busy, string message)
    {
        _state.Text = message;
        _use.Enabled = !busy && SelectedModel is not null;
        _setup.Enabled = !busy;
        _optimize.Enabled = !busy && SelectedModel is not null;
        _refresh.Enabled = !busy;
        _cancel.Visible = busy;
    }

    public void ShowReport(UtilityReport report)
    {
        _report = report;
        _cpu.Text = report.Cpu ?? "Unknown";
        _ram.Text = UtilityViewParts.Gib(report.RamBytes);
        var largestGpu = report.Gpus.OrderByDescending(item => item.VramBytes ?? 0).FirstOrDefault();
        _gpu.Text = largestGpu is null ? "Unknown" : largestGpu.Name +
            (report.Gpus.Count > 1 ? $" (+{report.Gpus.Count - 1} other)" : "");
        _vram.Text = largestGpu is null ? "Unknown" : UtilityViewParts.Gib(largestGpu.VramBytes) +
            (report.Gpus.Count > 1 ? " (largest)" : "");
        var setupChoice = report.Models.Count == 0 && report.InventoryState == "Fresh"
            ? report.SetupPlan : null;
        _choiceHeading.Text = setupChoice is not null ? "Best choice for this PC (setup)" :
            report.Models.Count == 0 ? "No installed choice yet" : "Best installed choice";
        _bestModel.Text = report.Recommendation?.Model ?? setupChoice?.Model ?? "Not yet known";
        _recommendation.Text = report.Recommendation is { } rec
            ? $"{rec.Basis}. At {UtilityViewParts.Context(rec.Context)}; {rec.Source}."
            : setupChoice is { } plan
                ? $"Setup choice for this PC at {UtilityViewParts.Context(plan.Context)}. {plan.Basis}."
            : "No installed model has enough evidence for a confident recommendation yet.";
        _state.Text = report.Models.Count == 0
            ? report.InventoryState == "Fresh"
                ? "No models are installed. Set up AFK AI to install one."
                : "The local model service is unavailable. Check again after it starts."
            : $"{report.Models.Count} installed model{(report.Models.Count == 1 ? "" : "s")}. Fit is estimated, not measured.";
        _models.Items.Clear();
        foreach (var model in report.Models)
        {
            var state = model.Configured ? "Configured" : model.Loaded == true ? "Loaded" : "Installed";
            var item = new ListViewItem(new[] { model.Tag, model.Verdict,
                UtilityViewParts.Gib(model.WeightsBytes), state }) { Tag = model.Tag, ImageIndex = 0 };
            _models.Items.Add(item);
        }
        _models.Height = Math.Clamp(32 + _models.Items.Count * 32, 96, 260);
        _models.Visible = report.Models.Count > 0;
        _installedHeading.Visible = report.Models.Count > 0;
        var chosen = report.Recommendation?.Model ?? report.Models.FirstOrDefault()?.Tag;
        foreach (ListViewItem item in _models.Items)
            if ((string?)item.Tag == chosen) { item.Selected = true; item.Focused = true; break; }
        ResizeModelColumns();
        _setup.Visible = report.Models.Count == 0;
        _use.Visible = report.Models.Count > 0;
        _optimize.Visible = report.Models.Count > 0;
        _setup.Text = setupChoice is null ? "Set up and install a model" :
            $"Set up and install {setupChoice.Model}";
        UtilityViewParts.Primary(_setup, setupChoice is not null);
        ShowSelection();
    }

    private void ShowSelection()
    {
        var model = _report?.Models.FirstOrDefault(item => item.Tag == SelectedModel);
        var setupPlan = _report is { InventoryState: "Fresh", SetupPlan: { } plan } ? plan : null;
        _use.Enabled = model is { Installed: true } && !_cancel.Visible &&
            _report?.InventoryState == "Fresh";
        _optimize.Enabled = _use.Enabled;
        _detailVerdict.Text = model is null
            ? setupPlan is null ? "Select a model" : $"Setup choice · {setupPlan.Model}"
            : $"{model.Tag}  ·  {model.Verdict}";
        _detailReason.Text = model is null
            ? setupPlan is not null
                ? $"{setupPlan.Model} is a hardware-based setup choice at {UtilityViewParts.Context(setupPlan.Context)}. Setup checks again before downloading."
                : "Select an installed model to inspect its fit evidence."
            : model.Reason;
        _detail.Text = model is null ? "" :
            $"Weights           {UtilityViewParts.Gib(model.WeightsBytes)}  ·  Quantization {model.Quantization ?? "Unknown"}\r\n" +
            $"Context           {UtilityViewParts.Context(model.Context)}  ·  KV cache {UtilityViewParts.Gib(model.KvBytes)} ({model.KvSource ?? "unknown"})\r\n" +
            $"Estimated VRAM    {UtilityViewParts.Gib(model.VramRequiredBytes)}\r\n" +
            $"Installed Yes  ·  Configured {(model.Configured ? "Yes" : "No")}  ·  " +
            $"Loaded {(model.Loaded is null ? "Unknown" : model.Loaded.Value ? "Yes" : "No")}  ·  Healthy Not checked here";
    }
}

public sealed class OptimizationPage : UserControl
{
    private sealed record ContextOption(int Value)
    {
        public override string ToString() => UtilityViewParts.Context(Value);
    }
    public event Action? RefreshRequested;
    public event Action? MeasureRequested;
    public event Action? ApplyRecommendedRequested;
    public event Action<int>? ApplyOverrideRequested;
    public event Action? CancelRequested;
    private readonly Label _model = UtilityViewParts.Body("Checking the selected model…");
    private readonly Label _recommended = UtilityViewParts.Body();
    private readonly Label _recommendationBasis = UtilityViewParts.Body();
    private readonly Label _setting = UtilityViewParts.Body();
    private readonly Label _settingSource = UtilityViewParts.Body();
    private readonly Label _state = UtilityViewParts.Body();
    private readonly TableLayoutPanel _summary;
    private readonly Label _evidenceIntro = UtilityViewParts.Body();
    private readonly ListView _measurements = new()
    {
        View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false,
        HeaderStyle = ColumnHeaderStyle.Nonclickable, BorderStyle = BorderStyle.None,
        BackColor = Theme.Surface, ForeColor = Theme.PrimaryText, Font = Theme.Font(9.5f),
        AccessibleName = "Measured context comparison"
    };
    private readonly TextBox _detail = new()
    {
        ReadOnly = true, Multiline = true, ScrollBars = ScrollBars.Vertical,
        BackColor = Theme.Sunken, ForeColor = Theme.SecondaryText, BorderStyle = BorderStyle.None,
        Font = Theme.Mono(9), AccessibleName = "Optimization evidence"
    };
    private readonly SurfacePanel _measurementSurface;
    private readonly SurfacePanel _detailSurface;
    private readonly LinkLabel _detailToggle = new() { AutoSize = true, Text = "Show estimates and details" };
    private readonly Button _measure = Theme.Button("Measure on this PC");
    private readonly Button _apply = Theme.Button("Apply recommendation");
    private readonly Button _override = Theme.Button("Apply as my override");
    private readonly Button _refresh = Theme.Button("Check again");
    private readonly Button _cancel = Theme.Button("Cancel");
    private readonly ComboBox _contexts = new() { DropDownStyle = ComboBoxStyle.DropDownList,
        Width = 110, Font = Theme.Font(10), AccessibleName = "Chosen context" };
    private UtilityOptimization? _optimization;
    private bool _canApply;

    public OptimizationPage()
    {
        _measurementSurface = new SurfacePanel { Height = 130, Padding = new Padding(12, 10, 12, 10) };
        _detailSurface = Theme.DetailSurface(_detail, 150);
        Dock = DockStyle.Fill;
        AutoScroll = true;
        BackColor = Theme.Background;
        var stack = UtilityViewParts.Stack();
        UtilityViewParts.Add(stack, UtilityViewParts.Heading("Optimization"));
        UtilityViewParts.Add(stack, UtilityViewParts.Body("Measure a model here, then choose a context. Nothing changes until you apply it."));
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Selected model"));
        _model.Font = Theme.Mono(10);
        UtilityViewParts.Add(stack, _model);
        _summary = new TableLayoutPanel { Dock = DockStyle.Top, Height = 118, ColumnCount = 2,
            BackColor = Theme.Background, Margin = new Padding(0, 3, 0, 2) };
        _summary.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 60));
        _summary.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 40));
        var afkColumn = UtilityViewParts.Stack();
        afkColumn.BackColor = Theme.Background;
        UtilityViewParts.Add(afkColumn, UtilityViewParts.Section("AFK recommendation"));
        _recommended.Font = Theme.Display(17, FontStyle.Bold);
        _recommended.ForeColor = Theme.PrimaryText;
        _recommended.Margin = new Padding(0, 0, 0, 4);
        _recommended.MaximumSize = new Size(440, 0);
        _recommendationBasis.MaximumSize = new Size(400, 0);
        UtilityViewParts.Add(afkColumn, _recommended);
        UtilityViewParts.Add(afkColumn, _recommendationBasis);
        var settingColumn = UtilityViewParts.Stack();
        settingColumn.BackColor = Theme.Background;
        UtilityViewParts.Add(settingColumn, UtilityViewParts.Section("Current setting"));
        _setting.Font = Theme.Display(15, FontStyle.Bold);
        _setting.ForeColor = Theme.PrimaryText;
        _setting.Margin = new Padding(0, 0, 0, 4);
        _settingSource.MaximumSize = new Size(285, 0);
        UtilityViewParts.Add(settingColumn, _setting);
        UtilityViewParts.Add(settingColumn, _settingSource);
        _summary.Controls.Add(afkColumn, 0, 0);
        _summary.Controls.Add(settingColumn, 1, 0);
        UtilityViewParts.Add(stack, _summary);
        UtilityViewParts.Add(stack, _state);
        var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Top, WrapContents = true,
            Margin = new Padding(0, 12, 0, 12) };
        foreach (var button in new[] { _measure, _apply, _refresh, _cancel }) actions.Controls.Add(button);
        UtilityViewParts.Add(stack, actions);
        var overrideRow = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Top, WrapContents = true,
            Margin = new Padding(0, 0, 0, 12) };
        overrideRow.Controls.Add(UtilityViewParts.Body("Choose a different safe estimate:"));
        overrideRow.Controls.Add(_contexts);
        overrideRow.Controls.Add(_override);
        UtilityViewParts.Add(stack, overrideRow);
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Measurement evidence"));
        UtilityViewParts.Add(stack, _evidenceIntro);
        _measurements.Columns.Add("Context", 88);
        _measurements.Columns.Add("Effective", 94);
        _measurements.Columns.Add("First token", 92);
        _measurements.Columns.Add("Prompt/s", 92);
        _measurements.Columns.Add("Decode/s", 94);
        _measurements.Columns.Add("Evidence", 220);
        var rows = new ImageList { ImageSize = new Size(1, 31), ColorDepth = ColorDepth.Depth32Bit };
        rows.Images.Add(new Bitmap(1, 31));
        _measurements.SmallImageList = rows;
        _measurements.Dock = DockStyle.Fill;
        _measurementSurface.Controls.Add(_measurements);
        _measurementSurface.Resize += (_, _) =>
            _measurements.Columns[5].Width = Math.Max(130, _measurements.ClientSize.Width - 88 - 94 - 92 - 92 - 94 - 24);
        UtilityViewParts.Add(stack, _measurementSurface);
        _detailToggle.LinkColor = _detailToggle.ActiveLinkColor = Theme.Link;
        _detailToggle.Font = Theme.Font(9.5f);
        _detailToggle.LinkClicked += (_, _) =>
        {
            _detailSurface.Visible = !_detailSurface.Visible;
            _detailToggle.Text = _detailSurface.Visible ? "Hide estimates and details" : "Show estimates and details";
        };
        UtilityViewParts.Add(stack, _detailToggle);
        UtilityViewParts.Add(stack, _detailSurface);
        _detailSurface.Visible = false;
        Controls.Add(stack);
        Resize += (_, _) => stack.Width = ClientSize.Width - Padding.Horizontal - 18;
        _measure.AccessibleName = "Measure model performance on this PC";
        _apply.AccessibleName = "Apply AFK recommended context";
        _override.AccessibleName = "Apply chosen context as user override";
        _cancel.AccessibleName = "Cancel measurement";
        _measure.Click += (_, _) => MeasureRequested?.Invoke();
        _apply.Click += (_, _) => ApplyRecommendedRequested?.Invoke();
        _override.Click += (_, _) => { if (_contexts.SelectedItem is ContextOption option) ApplyOverrideRequested?.Invoke(option.Value); };
        _refresh.Click += (_, _) => RefreshRequested?.Invoke();
        _cancel.Click += (_, _) => CancelRequested?.Invoke();
        SetBusy(false, "");
    }

    public void SetBusy(bool busy, string message)
    {
        _state.Text = message;
        _measure.Enabled = !busy;
        _apply.Enabled = !busy && _canApply && _optimization?.RecommendedContext is not null;
        _override.Enabled = !busy && _canApply && _contexts.SelectedItem is ContextOption;
        _refresh.Enabled = !busy;
        _contexts.Enabled = !busy;
        _cancel.Visible = busy;
    }

    public void ShowPending(string model)
    {
        _optimization = null;
        _canApply = false;
        _model.Text = model;
        _summary.Height = 150;
        _recommended.Text = "Checking saved evidence…";
        _recommendationBasis.Text = "";
        _setting.Text = "Checking your setting…";
        _settingSource.Text = "";
        _evidenceIntro.Text = "Checking measurements and estimates…";
        _measurements.Items.Clear();
        _measurementSurface.Visible = false;
        _detailSurface.Visible = false;
        _detailToggle.Text = "Show estimates and details";
        _detail.Text = "";
        _contexts.Items.Clear();
        SetBusy(true, "Checking local model evidence…");
    }

    public void ShowOptimization(string model, UtilityOptimization optimization, bool canApply)
    {
        _optimization = optimization;
        _canApply = canApply;
        _model.Text = model;
        _summary.Height = optimization.RecommendedContext is null ? 150 : 118;
        var measured = optimization.Measurements.FirstOrDefault(item =>
            item.Successful && item.Context == optimization.RecommendedContext);
        _recommended.Text = optimization.RecommendedContext is { } context
            ? UtilityViewParts.Context(context) : "No measured recommendation yet";
        _recommendationBasis.Text = optimization.RecommendedContext is not null
            ? $"{(measured?.Source == "cache" ? "Cached measurement" : "Measured on this PC")} · {optimization.Confidence}" +
              (measured is null ? "" : $" · {measured.DecodeTokensPerSecond:0.#} tokens/s decode")
            : "Measure on this PC to compare safe contexts. Memory estimates alone do not establish performance.";
        var readyToApply = canApply && optimization.RecommendedContext is not null &&
            optimization.Selection.SelectedContext != optimization.RecommendedContext;
        _measure.Text = optimization.RecommendedContext is null ? "Measure on this PC" : "Measure again";
        UtilityViewParts.Primary(_apply, readyToApply);
        UtilityViewParts.Primary(_measure, !readyToApply && optimization.RecommendedContext is null);
        _setting.Text = optimization.Selection.SelectedContext is { } selected
            ? UtilityViewParts.Context(selected) : "No context applied";
        _settingSource.Text = optimization.Selection.SelectedContext is null
            ? "Nothing has been changed through Optimization."
            : optimization.Selection.OverrideContext is not null
                ? "Your override · separate from AFK's recommendation"
                : "AFK recommendation applied";
        _state.Text = canApply ? "" :
            "Use this model in Models & fit before applying a context.";
        _contexts.Items.Clear();
        foreach (var item in optimization.Estimates.Where(item => item.Safe && item.Context > 0))
            _contexts.Items.Add(new ContextOption(item.Context));
        if (_contexts.Items.Count > 0) _contexts.SelectedIndex = 0;
        _measurements.Items.Clear();
        foreach (var item in optimization.Measurements)
        {
            _measurements.Items.Add(new ListViewItem(new[]
            {
                UtilityViewParts.Context(item.Context), UtilityViewParts.Context(item.EffectiveContext),
                item.Successful ? $"{item.FirstTokenSeconds:0.##} s" : "Incomplete",
                item.Successful ? $"{item.PromptTokensPerSecond:0.#}" : "—",
                item.Successful ? $"{item.DecodeTokensPerSecond:0.#}" : "—",
                $"{(item.Source == "cache" ? "Cached" : "Measured")} · {item.MeasuredAt ?? "date unknown"}"
            }) { ImageIndex = 0 });
        }
        _measurementSurface.Height = Math.Clamp(52 + _measurements.Items.Count * 32, 112, 240);
        _measurementSurface.Visible = _measurements.Items.Count > 0;
        _evidenceIntro.Text = _measurements.Items.Count == 0
            ? "No measurements yet. The estimates below explain excluded contexts."
            : "First token is in seconds; prompt and decode throughput are tokens per second.";
        var lines = new List<string>();
        foreach (var item in optimization.Measurements)
            lines.Add($"{UtilityViewParts.Context(item.Context)}: " +
                (item.Successful
                    ? $"{item.Source} measurement · effective {UtilityViewParts.Context(item.EffectiveContext)} · " +
                      $"first token {item.FirstTokenSeconds:0.##}s · prompt {item.PromptTokensPerSecond:0.#}/s · " +
                      $"decode {item.DecodeTokensPerSecond:0.#}/s · {item.MeasuredAt}"
                    : "measurement incomplete"));
        foreach (var item in optimization.Estimates.Where(item => !item.Safe))
            lines.Add($"{UtilityViewParts.Context(item.Context)}: excluded estimate · {item.Reason}");
        _detail.Text = lines.Count > 0 ? string.Join(Environment.NewLine + Environment.NewLine, lines)
            : "No measurements yet. Memory estimates alone do not establish performance.";
        _detailSurface.Visible = false;
        _detailToggle.Text = "Show estimates and details";
        SetBusy(false, _state.Text);
    }
}
