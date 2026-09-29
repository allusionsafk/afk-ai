using AFKLocalAI.App;
using System.Drawing.Imaging;

internal static class UtilityCapture
{
    public static void Run(string directory)
    {
        Directory.CreateDirectory(directory);
        Application.EnableVisualStyles();
        var report = UtilityJson.ParseReport("""{"schema_version":1,"hardware":{"cpu":"Example 16-core CPU","ram_total_bytes":33930241638,"gpus":[{"name":"Example 12 GiB GPU","vram_total_bytes":12884901888}]},"models":[{"model":"qwen3.5:9b-32k","verdict":"Fits","reason":"Weights and estimated context cache fit usable VRAM.","weights_bytes":6764573491,"kv_bytes":536870912,"kv_source":"architecture estimate","quantization":"Q4_K_M","context":8192,"installed":true,"configured":true,"loaded":false},{"model":"qwen3.5:27b","verdict":"RAM-assisted","reason":"Weights and estimated context cache exceed usable VRAM; RAM may assist. Speed is unmeasured.","weights_bytes":17944831938,"kv_bytes":1073741824,"kv_source":"parameter estimate","quantization":"Q4_K_M","context":8192,"installed":true,"configured":false,"loaded":false}],"recommendation":{"model":"qwen3.5:9b-32k","basis":"configured model fits in VRAM","source":"rule-based estimate","context":8192},"selection":{"source_model":"qwen3.5:9b-32k","recommended_context":null,"selected_context":null,"override_context":null},"runtime":{"inventory_state":"Fresh","errors":[]}}""");
        var optimization = UtilityJson.ParseOptimization("""{"schema_version":1,"recommended_context":8192,"confidence":"bounded short-prompt measurement","measurements":[{"context":4096,"effective_context":4096,"successful":true,"source":"cache","median_first_token_seconds":0.8,"median_prompt_tokens_per_second":185.0,"median_decode_tokens_per_second":41.0,"measured_at":"2026-09-28T10:00:00Z"},{"context":8192,"effective_context":8192,"successful":true,"source":"fresh","median_first_token_seconds":1.1,"median_prompt_tokens_per_second":177.0,"median_decode_tokens_per_second":37.0,"measured_at":"2026-09-28T10:10:00Z"}],"estimates":[{"context":4096,"safe":true,"kv_method":"architecture metadata"},{"context":8192,"safe":true,"kv_method":"architecture metadata"},{"context":16384,"safe":false,"reason":"estimated memory exceeds headroom","kv_method":"architecture metadata"}],"user_setting":{"source_model":"qwen3.5:9b-32k","recommended_context":4096,"selected_context":4096,"override_context":4096}}""");
        var firstRun = UtilityJson.ParseReport("""{"schema_version":1,"hardware":{"cpu":"Example 16-core CPU","ram_total_bytes":33930241638,"gpus":[{"name":"Example 12 GiB GPU","vram_total_bytes":12884901888}]},"models":[],"setup_plan":{"model":"qwen3.5:9b","context":32768,"basis":"selected from detected NVIDIA VRAM; setup confirms hardware","source":"installer tier policy"},"runtime":{"inventory_state":"Fresh","errors":[]}}""");
        var fullInventory = report with { Models = new[] { report.Models[0] }.Concat(
            Enumerable.Range(1, 21).Select(index => report.Models[index % 2] with
            { Tag = $"sample-model-{index:00}:latest", Configured = false })).ToArray() };
        var multipleGpus = report with { Gpus = new[]
        {
            new UtilityGpu("Integrated graphics", null),
            new UtilityGpu("Example 12 GiB GPU", 12884901888)
        } };
        var unmeasured = optimization with { RecommendedContext = null,
            Measurements = Array.Empty<UtilityMeasurement>(), Confidence = "insufficient evidence",
            Selection = new UtilitySelection("qwen3.5:9b-32k", null, null, null) };
        var cached = optimization with { RecommendedContext = 4096,
            Measurements = optimization.Measurements.Take(1).ToArray(),
            Selection = new UtilitySelection("qwen3.5:9b-32k", null, null, null) };
        foreach (var (name, scale) in new[] { ("100", 1f), ("150", 1.5f), ("200", 2f) })
        {
            Capture(new ModelsFitPage(), page => ((ModelsFitPage)page).ShowReport(report),
                Path.Combine(directory, $"models-{name}.png"), scale);
            Capture(new OptimizationPage(), page => ((OptimizationPage)page).ShowOptimization("qwen3.5:9b-32k", optimization, true),
                Path.Combine(directory, $"optimization-{name}.png"), scale);
        }
        Capture(new ModelsFitPage(), page => ((ModelsFitPage)page).ShowReport(firstRun),
            Path.Combine(directory, "models-first-run-100.png"), 1f);
        Capture(new ModelsFitPage(), page => ((ModelsFitPage)page).ShowReport(fullInventory),
            Path.Combine(directory, "models-22-100.png"), 1f);
        Capture(new ModelsFitPage(), page => ((ModelsFitPage)page).ShowReport(multipleGpus),
            Path.Combine(directory, "models-multiple-gpus-100.png"), 1f);
        Capture(new OptimizationPage(), page => ((OptimizationPage)page).ShowOptimization("qwen3.5:9b-32k", unmeasured, true),
            Path.Combine(directory, "optimization-empty-100.png"), 1f);
        Capture(new OptimizationPage(), page => ((OptimizationPage)page).ShowOptimization("qwen3.5:9b-32k", cached, true),
            Path.Combine(directory, "optimization-cached-100.png"), 1f);
        Capture(new OptimizationPage(), page => ((OptimizationPage)page).ShowPending("qwen3.5:9b-32k"),
            Path.Combine(directory, "optimization-busy-100.png"), 1f);
        CaptureAbout(directory);
    }

    private static void CaptureAbout(string directory)
    {
        var root = Directory.GetCurrentDirectory();
        var product = ProductInfo.Load(Path.Combine(root, "installer", "version.json"));
        var paths = AppPaths.ForCurrentUser(Path.Combine(directory, "about-data"), root);
        using var icon = AppIcon.Create();
        using var form = new MainForm(paths, product, aboutOnly: true, icon)
        {
            StartPosition = FormStartPosition.Manual,
            Location = new Point(-10000, -10000),
            ClientSize = new Size(1120, 760)
        };
        form.Show();
        Application.DoEvents();
        form.PerformLayout();
        using var bitmap = new Bitmap(form.ClientSize.Width, form.ClientSize.Height);
        form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, form.ClientSize));
        bitmap.Save(Path.Combine(directory, "about-100.png"), ImageFormat.Png);
    }

    private static void Capture(UserControl page, Action<UserControl> populate, string path, float scale)
    {
        using var form = new Form
        {
            ClientSize = new Size((int)(750 * scale), (int)(700 * scale)),
            BackColor = Theme.Background, StartPosition = FormStartPosition.Manual,
            Location = new Point(-10000, -10000)
        };
        page.Dock = DockStyle.None;
        populate(page);
        if (scale != 1) page.Scale(new SizeF(scale, scale));
        page.Dock = DockStyle.Fill;
        form.Controls.Add(page);
        form.CreateControl();
        page.CreateControl();
        form.Show();
        Application.DoEvents();
        form.PerformLayout();
        page.PerformLayout();
        using var bitmap = new Bitmap(form.ClientSize.Width, form.ClientSize.Height);
        form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, form.ClientSize));
        bitmap.Save(path, ImageFormat.Png);
    }
}
