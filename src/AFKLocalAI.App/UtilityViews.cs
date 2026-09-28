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

    private readonly Label _pc = UtilityViewParts.Body("Checking this PC…");
    private readonly Label _choiceHeading = UtilityViewParts.Section("Best installed choice");
    private readonly Label _installedHeading = UtilityViewParts.Section("Installed models");
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
        Height = 84, Font = Theme.Font(10), AccessibleName = "Installed model fit",
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
        UtilityViewParts.Add(stack, _choiceHeading);
        UtilityViewParts.Add(stack, _bestModel);
        UtilityViewParts.Add(stack, _recommendation);
        UtilityViewParts.Add(stack, _state);
        var actions = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Top, WrapContents = true,
            Margin = new Padding(0, 10, 0, 8) };
        foreach (var button in new[] { _use, _setup, _optimize, _refresh, _cancel }) actions.Controls.Add(button);
        UtilityViewParts.Add(stack, actions);
        UtilityViewParts.Add(stack, _installedHeading);
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
        _optimize.AccessibleName = "Optimize selected installed model";
        _optimize.Click += (_, _) => { if (SelectedModel is { } tag) OptimizeRequested?.Invoke(tag); };
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
        _optimize.Enabled = !busy && SelectedModel is not null;
        _refresh.Enabled = !busy;
        _cancel.Visible = busy;
    }

    public void ShowReport(UtilityReport report)
    {
        _report = report;
        var gpu = report.Gpus.Count == 0 ? "GPU unknown" : string.Join(" · ",
            report.Gpus.Select(item => $"{item.Name} ({UtilityViewParts.Gib(item.VramBytes)} VRAM)"));
        _pc.Text = $"{report.Cpu ?? "CPU unknown"} · {UtilityViewParts.Gib(report.RamBytes)} RAM\n{gpu}";
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
                UtilityViewParts.Gib(model.WeightsBytes), state }) { Tag = model.Tag };
            _models.Items.Add(item);
        }
        _models.Height = Math.Clamp(30 + _models.Items.Count * 28, 84, 210);
        _models.Visible = report.Models.Count > 0;
        _installedHeading.Visible = report.Models.Count > 0;
        var chosen = report.Recommendation?.Model ?? report.Models.FirstOrDefault()?.Tag;
        foreach (ListViewItem item in _models.Items)
            if ((string?)item.Tag == chosen) { item.Selected = true; item.Focused = true; break; }
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
        _use.Enabled = model is { Installed: true } && !_cancel.Visible &&
            _report?.InventoryState == "Fresh";
        _optimize.Enabled = _use.Enabled;
        _detail.Text = model is null ?
            _report?.SetupPlan is { } plan && _report.InventoryState == "Fresh"
                ? $"{plan.Model} is the existing setup tier choice at {UtilityViewParts.Context(plan.Context)}. " +
                  "This is a hardware-based plan, not an installed or measured model. Setup checks this PC again before downloading."
                : "Select a model to see the evidence behind its fit." :
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
    private readonly Label _setting = UtilityViewParts.Body();
    private readonly Label _state = UtilityViewParts.Body();
    private readonly TextBox _detail = new()
    {
        ReadOnly = true, Multiline = true, ScrollBars = ScrollBars.Vertical, Height = 230,
        BackColor = Theme.Sunken, ForeColor = Theme.SecondaryText, BorderStyle = BorderStyle.FixedSingle,
        Font = Theme.Font(9), AccessibleName = "Optimization evidence"
    };
    private readonly Button _measure = Theme.Button("Measure on this PC");
    private readonly Button _apply = Theme.Button("Apply recommendation");
    private readonly Button _override = Theme.Button("Apply chosen context");
    private readonly Button _refresh = Theme.Button("Check again");
    private readonly Button _cancel = Theme.Button("Cancel");
    private readonly ComboBox _contexts = new() { DropDownStyle = ComboBoxStyle.DropDownList,
        Width = 110, Font = Theme.Font(10), AccessibleName = "Chosen context" };
    private UtilityOptimization? _optimization;
    private bool _canApply;

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
        _recommended.Text = "Checking saved evidence…";
        _setting.Text = "Checking your setting…";
        _detail.Text = "";
        _contexts.Items.Clear();
        SetBusy(true, "Checking local model evidence…");
    }

    public void ShowOptimization(string model, UtilityOptimization optimization, bool canApply)
    {
        _optimization = optimization;
        _canApply = canApply;
        _model.Text = model;
        var measured = optimization.Measurements.FirstOrDefault(item =>
            item.Successful && item.Context == optimization.RecommendedContext);
        _recommended.Text = optimization.RecommendedContext is { } context
            ? $"{UtilityViewParts.Context(context)} · Measured on this PC" +
              (measured is null ? "" : $" · {measured.DecodeTokensPerSecond:0.#} tokens/s ({measured.Source})")
            : "No measured recommendation yet. Run a measurement to compare safe contexts.";
        var readyToApply = canApply && optimization.RecommendedContext is not null &&
            optimization.Selection.SelectedContext != optimization.RecommendedContext;
        _measure.Text = optimization.RecommendedContext is null ? "Measure on this PC" : "Measure again";
        UtilityViewParts.Primary(_apply, readyToApply);
        UtilityViewParts.Primary(_measure, !readyToApply && optimization.RecommendedContext is null);
        _setting.Text = optimization.Selection.SelectedContext is { } selected
            ? $"{UtilityViewParts.Context(selected)}" +
              (optimization.Selection.OverrideContext is not null ? " · Your override" : " · AFK recommendation applied")
            : "No context has been applied through Optimization.";
        _state.Text = canApply ? optimization.Confidence :
            $"{optimization.Confidence}. Use this model in Models & fit before applying a context.";
        _contexts.Items.Clear();
        foreach (var item in optimization.Estimates.Where(item => item.Safe && item.Context > 0))
            _contexts.Items.Add(new ContextOption(item.Context));
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
        SetBusy(false, _state.Text);
    }
}
