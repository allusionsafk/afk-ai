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
        MaximumSize = new Size(720, 0), Margin = new Padding(0, 0, 0, 12)
    };

    public static Label Section(string text) => new()
    {
        Text = text, AutoSize = true, Font = Theme.Display(14, FontStyle.Bold),
        ForeColor = Theme.PrimaryText, Margin = new Padding(0, 16, 0, 6)
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
}

/// <summary>Installed models first; detail is disclosed only for the selected row.</summary>
public sealed class ModelsFitPage : UserControl
{
    public event Action? RefreshRequested;
    public event Action? SetupRequested;
    public event Action? OptimizeRequested;
    public event Action<string>? UseRequested;
    public event Action? CancelRequested;

    private readonly Label _pc = UtilityViewParts.Body("Checking this PC…");
    private readonly Label _bestModel = new()
    {
        AutoSize = true, Font = Theme.Display(16, FontStyle.Bold),
        ForeColor = Theme.PrimaryText, Margin = new Padding(0, 0, 0, 4)
    };
    private readonly Label _recommendation = UtilityViewParts.Body("Checking installed models…");
    private readonly Label _state = UtilityViewParts.Body();
    private readonly ListView _models = new()
    {
        View = View.Details, FullRowSelect = true, MultiSelect = false, HideSelection = false,
        Height = 210, Font = Theme.Font(10), AccessibleName = "Installed model fit",
        BorderStyle = BorderStyle.FixedSingle
    };
    private readonly TextBox _detail = new()
    {
        ReadOnly = true, Multiline = true, ScrollBars = ScrollBars.Vertical, Height = 116,
        BackColor = Theme.Sunken, ForeColor = Theme.SecondaryText, BorderStyle = BorderStyle.FixedSingle,
        Font = Theme.Font(9), AccessibleName = "Why this model fits"
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
        UtilityViewParts.Add(stack, _pc);
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Best installed choice"));
        UtilityViewParts.Add(stack, _bestModel);
        UtilityViewParts.Add(stack, _recommendation);
        UtilityViewParts.Add(stack, _state);
        var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Top, WrapContents = true,
            Margin = new Padding(0, 10, 0, 8) };
        foreach (var button in new[] { _use, _setup, _optimize, _refresh, _cancel }) actions.Controls.Add(button);
        UtilityViewParts.Add(stack, actions);
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Installed models"));
        _models.Columns.Add("Model", 235);
        _models.Columns.Add("Fit at 8K", 125);
        _models.Columns.Add("Size", 85);
        _models.Columns.Add("State", 175);
        UtilityViewParts.Add(stack, _models);
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Why this model?"));
        UtilityViewParts.Add(stack, _detail);
        Controls.Add(stack);
        Resize += (_, _) => stack.Width = ClientSize.Width - Padding.Horizontal - 18;
        _models.SelectedIndexChanged += (_, _) => ShowSelection();
        _use.AccessibleName = "Use selected installed model";
        _setup.AccessibleName = "Set up and install a model";
        _refresh.AccessibleName = "Refresh model fit";
        _cancel.AccessibleName = "Cancel Utility operation";
        _use.Click += (_, _) => { if (SelectedModel is { } tag) UseRequested?.Invoke(tag); };
        _setup.Click += (_, _) => SetupRequested?.Invoke();
        _optimize.Click += (_, _) => OptimizeRequested?.Invoke();
        _refresh.Click += (_, _) => RefreshRequested?.Invoke();
        _cancel.Click += (_, _) => CancelRequested?.Invoke();
        SetBusy(false, "");
    }

    public string? SelectedModel => _models.SelectedItems.Count > 0
        ? _models.SelectedItems[0].Tag as string : null;

    public void SetBusy(bool busy, string message)
    {
        _state.Text = message;
        _use.Enabled = !busy && SelectedModel is not null;
        _setup.Enabled = !busy;
        _optimize.Enabled = !busy;
        _refresh.Enabled = !busy;
        _cancel.Visible = busy;
    }

    public void ShowReport(UtilityReport report)
    {
        _report = report;
        var gpu = report.Gpus.Count == 0 ? "GPU unknown" : string.Join(" · ",
            report.Gpus.Select(item => $"{item.Name} ({UtilityViewParts.Gib(item.VramBytes)} VRAM)"));
        _pc.Text = $"{report.Cpu ?? "CPU unknown"} · {UtilityViewParts.Gib(report.RamBytes)} RAM\n{gpu}";
        _bestModel.Text = report.Recommendation?.Model ?? "Not yet known";
        _recommendation.Text = report.Recommendation is { } rec
            ? $"{rec.Basis}. At {UtilityViewParts.Context(rec.Context)}; {rec.Source}."
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
                UtilityViewParts.Gib(model.WeightsBytes), state }) { Tag = model.Tag };
            _models.Items.Add(item);
        }
        var chosen = report.Recommendation?.Model ?? report.Models.FirstOrDefault()?.Tag;
        foreach (ListViewItem item in _models.Items)
            if ((string?)item.Tag == chosen) { item.Selected = true; item.Focused = true; break; }
        _setup.Visible = report.Models.Count == 0;
        ShowSelection();
    }

    private void ShowSelection()
    {
        var model = _report?.Models.FirstOrDefault(item => item.Tag == SelectedModel);
        _use.Enabled = model is { Installed: true } && !_cancel.Visible &&
            _report?.InventoryState == "Fresh";
        _detail.Text = model is null ? "Select a model to see the evidence behind its fit." :
            $"{model.Tag} · {model.Verdict}\r\n{model.Reason}\r\n" +
            $"Weights: {UtilityViewParts.Gib(model.WeightsBytes)} (local model size). " +
            $"Quantization: {model.Quantization ?? "Unknown"}.\r\n" +
            $"Context: {UtilityViewParts.Context(model.Context)}. " +
            $"KV cache: {UtilityViewParts.Gib(model.KvBytes)} ({model.KvSource ?? "unknown"}). " +
            $"Estimated VRAM need: {UtilityViewParts.Gib(model.VramRequiredBytes)}.\r\n" +
            $"Installed: Yes. Configured: {(model.Configured ? "Yes" : "No")}. " +
            $"Loaded: {(model.Loaded is null ? "Unknown" : model.Loaded.Value ? "Yes" : "No")}. " +
            "Healthy: Not checked here.";
    }
}

public sealed class OptimizationPage : UserControl
{
    public event Action? RefreshRequested;
    public event Action? MeasureRequested;
    public event Action? ApplyRecommendedRequested;
    public event Action<int>? ApplyOverrideRequested;
    public event Action? CancelRequested;
    private readonly Label _model = UtilityViewParts.Body("Checking the selected model…");
    private readonly Label _recommended = UtilityViewParts.Body();
    private readonly Label _setting = UtilityViewParts.Body();
    private readonly Label _state = UtilityViewParts.Body();
    private readonly TextBox _detail = new()
    {
        ReadOnly = true, Multiline = true, ScrollBars = ScrollBars.Vertical, Height = 230,
        BackColor = Theme.Sunken, ForeColor = Theme.SecondaryText, BorderStyle = BorderStyle.FixedSingle,
        Font = Theme.Font(9), AccessibleName = "Optimization evidence"
    };
    private readonly Button _measure = Theme.Button("Measure on this PC", primary: true);
    private readonly Button _apply = Theme.Button("Apply recommendation");
    private readonly Button _override = Theme.Button("Apply chosen context");
    private readonly Button _refresh = Theme.Button("Check again");
    private readonly Button _cancel = Theme.Button("Cancel");
    private readonly ComboBox _contexts = new() { DropDownStyle = ComboBoxStyle.DropDownList,
        Width = 110, Font = Theme.Font(10), AccessibleName = "Chosen context" };
    private UtilityOptimization? _optimization;

    public OptimizationPage()
    {
        Dock = DockStyle.Fill;
        AutoScroll = true;
        BackColor = Theme.Background;
        var stack = UtilityViewParts.Stack();
        UtilityViewParts.Add(stack, UtilityViewParts.Heading("Optimization"));
        UtilityViewParts.Add(stack, UtilityViewParts.Body("Measure a model here, then choose a context. Nothing changes until you apply it."));
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Selected model"));
        UtilityViewParts.Add(stack, _model);
        UtilityViewParts.Add(stack, UtilityViewParts.Section("AFK recommendation"));
        UtilityViewParts.Add(stack, _recommended);
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Your setting"));
        UtilityViewParts.Add(stack, _setting);
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
        UtilityViewParts.Add(stack, UtilityViewParts.Section("Evidence"));
        UtilityViewParts.Add(stack, _detail);
        Controls.Add(stack);
        Resize += (_, _) => stack.Width = ClientSize.Width - Padding.Horizontal - 18;
        _measure.AccessibleName = "Measure model performance on this PC";
        _apply.AccessibleName = "Apply AFK recommended context";
        _override.AccessibleName = "Apply chosen context as user override";
        _cancel.AccessibleName = "Cancel measurement";
        _measure.Click += (_, _) => MeasureRequested?.Invoke();
        _apply.Click += (_, _) => ApplyRecommendedRequested?.Invoke();
        _override.Click += (_, _) => { if (_contexts.SelectedItem is int context) ApplyOverrideRequested?.Invoke(context); };
        _refresh.Click += (_, _) => RefreshRequested?.Invoke();
        _cancel.Click += (_, _) => CancelRequested?.Invoke();
        SetBusy(false, "");
    }

    public void SetBusy(bool busy, string message)
    {
        _state.Text = message;
        _measure.Enabled = !busy;
        _apply.Enabled = !busy && _optimization?.RecommendedContext is not null;
        _override.Enabled = !busy && _contexts.SelectedItem is int;
        _refresh.Enabled = !busy;
        _contexts.Enabled = !busy;
        _cancel.Visible = busy;
    }

    public void ShowOptimization(string model, UtilityOptimization optimization)
    {
        _optimization = optimization;
        _model.Text = model;
        var measured = optimization.Measurements.FirstOrDefault(item =>
            item.Successful && item.Context == optimization.RecommendedContext);
        _recommended.Text = optimization.RecommendedContext is { } context
            ? $"{UtilityViewParts.Context(context)} · Measured on this PC" +
              (measured is null ? "" : $" · {measured.DecodeTokensPerSecond:0.#} tokens/s ({measured.Source})")
            : "No measured recommendation yet. Run a measurement to compare safe contexts.";
        _setting.Text = optimization.Selection.SelectedContext is { } selected
            ? $"{UtilityViewParts.Context(selected)}" +
              (optimization.Selection.OverrideContext is not null ? " · Your override" : " · AFK recommendation applied")
            : "No context has been applied through Optimization.";
        _state.Text = optimization.Confidence;
        _contexts.Items.Clear();
        foreach (var item in optimization.Estimates.Where(item => item.Safe && item.Context > 0))
            _contexts.Items.Add(item.Context);
        if (_contexts.Items.Count > 0) _contexts.SelectedIndex = 0;
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
        SetBusy(false, optimization.Confidence);
    }
}
