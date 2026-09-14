using System.Drawing.Drawing2D;

namespace AFKLocalAI.App;

public static class Theme
{
    public static readonly Color Background = Color.FromArgb(23, 25, 29);
    public static readonly Color Surface = Color.FromArgb(32, 35, 41);
    public static readonly Color Elevated = Color.FromArgb(41, 45, 52);
    public static readonly Color Border = Color.FromArgb(59, 64, 73);
    public static readonly Color PrimaryText = Color.FromArgb(241, 243, 245);
    public static readonly Color SecondaryText = Color.FromArgb(184, 190, 200);
    public static readonly Color MutedText = Color.FromArgb(147, 154, 166);
    public static readonly Color Accent = Color.FromArgb(56, 189, 248);
    public static readonly Color Success = Color.FromArgb(85, 201, 141);
    public static readonly Color Warning = Color.FromArgb(229, 184, 90);
    public static readonly Color Failure = Color.FromArgb(226, 116, 107);

    public static Font Font(float size, FontStyle style = FontStyle.Regular) =>
        new("Segoe UI Variable Text", size, style, GraphicsUnit.Point);

    public static Button Button(string text, bool primary = false)
    {
        var button = new Button
        {
            Text = text,
            AutoSize = true,
            MinimumSize = new Size(128, 42),
            Padding = new Padding(16, 4, 16, 4),
            FlatStyle = FlatStyle.Flat,
            BackColor = primary ? Accent : Elevated,
            ForeColor = primary ? Color.FromArgb(10, 30, 40) : PrimaryText,
            Font = Font(10, FontStyle.Bold),
            Cursor = Cursors.Hand,
            UseVisualStyleBackColor = false
        };
        button.FlatAppearance.BorderColor = primary ? Accent : Border;
        button.FlatAppearance.BorderSize = 1;
        return button;
    }

    public static void Apply(Control root)
    {
        root.Font = Font(10);
        root.ForeColor = PrimaryText;
        root.BackColor = Background;
        foreach (Control child in root.Controls) Apply(child);
    }

    public static void PaintWordmark(Graphics graphics, Rectangle bounds)
    {
        graphics.SmoothingMode = SmoothingMode.AntiAlias;
        using var brush = new SolidBrush(Accent);
        graphics.FillEllipse(brush, bounds);
        using var pen = new Pen(Background, Math.Max(2, bounds.Width / 12f))
        {
            StartCap = LineCap.Round,
            EndCap = LineCap.Round
        };
        var left = bounds.Left + bounds.Width * .28f;
        var right = bounds.Right - bounds.Width * .28f;
        var top = bounds.Top + bounds.Height * .23f;
        var bottom = bounds.Bottom - bounds.Height * .22f;
        graphics.DrawLine(pen, left, bottom, bounds.Left + bounds.Width / 2f, top);
        graphics.DrawLine(pen, bounds.Left + bounds.Width / 2f, top, right, bottom);
        graphics.DrawLine(pen, bounds.Left + bounds.Width * .38f, bounds.Top + bounds.Height * .60f,
            bounds.Left + bounds.Width * .62f, bounds.Top + bounds.Height * .60f);
    }
}
