using System.Drawing.Drawing2D;

namespace AFKLocalAI.App;

/// <summary>
/// The warm workbench: paper-toned grounds, near-black ink, one ink-coloured
/// primary action. Semantic colours (ready, attention, blocked) always travel
/// with a symbol and a word, never alone. See DESIGN.md.
/// </summary>
public static class Theme
{
    public static readonly Color Background = Color.FromArgb(248, 247, 243);   // paper
    public static readonly Color Rail = Color.FromArgb(238, 236, 230);
    public static readonly Color RailBorder = Color.FromArgb(217, 215, 207);
    public static readonly Color Surface = Color.FromArgb(255, 255, 255);
    public static readonly Color SurfaceBorder = Color.FromArgb(222, 221, 215);
    public static readonly Color Elevated = Color.FromArgb(240, 239, 233);     // hover
    public static readonly Color Sunken = Color.FromArgb(245, 243, 237);       // logs, details
    public static readonly Color Border = Color.FromArgb(201, 201, 194);
    public static readonly Color PrimaryText = Color.FromArgb(37, 38, 34);
    public static readonly Color SecondaryText = Color.FromArgb(78, 81, 75);
    public static readonly Color MutedText = Color.FromArgb(101, 104, 98);
    public static readonly Color Action = Color.FromArgb(41, 45, 43);
    public static readonly Color ActionHover = Color.FromArgb(70, 73, 71);
    public static readonly Color Link = Color.FromArgb(52, 70, 91);
    public static readonly Color Success = Color.FromArgb(47, 104, 68);
    public static readonly Color SuccessSurface = Color.FromArgb(229, 239, 229);
    public static readonly Color Warning = Color.FromArgb(136, 96, 40);
    public static readonly Color WarningSurface = Color.FromArgb(246, 236, 217);
    public static readonly Color Failure = Color.FromArgb(141, 64, 55);
    public static readonly Color FailureSurface = Color.FromArgb(251, 240, 235);

    // Kept for callers that still say Accent: the accent is now the ink action.
    public static Color Accent => Action;

    public static Font Font(float size, FontStyle style = FontStyle.Regular) =>
        new("Segoe UI Variable Text", size, style, GraphicsUnit.Point);

    public static Font Display(float size, FontStyle style = FontStyle.Regular) =>
        new("Segoe UI Variable Display", size, style, GraphicsUnit.Point);

    public static Font Mono(float size) => new("Cascadia Mono", size, FontStyle.Regular, GraphicsUnit.Point);

    public static Color ToneColor(StatusTone tone) => tone switch
    {
        StatusTone.Ready => Success,
        StatusTone.Working => SecondaryText,
        StatusTone.Attention => Warning,
        StatusTone.Blocked => Failure,
        _ => MutedText
    };

    public static Button Button(string text, bool primary = false)
    {
        var button = new WorkbenchButton
        {
            Text = text,
            AutoSize = true,
            MinimumSize = new Size(120, 40),
            Padding = new Padding(16, 2, 16, 2),
            Margin = new Padding(0, 0, 10, 0),
            FlatStyle = FlatStyle.Flat,
            BackColor = primary ? Action : Surface,
            ForeColor = primary ? Color.White : PrimaryText,
            Font = Font(10, FontStyle.Bold),
            Cursor = Cursors.Hand,
            UseVisualStyleBackColor = false,
            UseMnemonic = false
        };
        button.FlatAppearance.BorderColor = primary ? Action : Border;
        button.FlatAppearance.BorderSize = 1;
        button.FlatAppearance.MouseOverBackColor = primary ? ActionHover : Elevated;
        button.FlatAppearance.MouseDownBackColor = primary ? Color.Black : Sunken;
        return button;
    }

    public static SurfacePanel DetailSurface(TextBox textBox, int height)
    {
        textBox.BorderStyle = BorderStyle.None;
        textBox.BackColor = Sunken;
        textBox.ForeColor = SecondaryText;
        textBox.Font = Mono(9);
        textBox.Dock = DockStyle.Fill;
        textBox.Visible = true;
        var surface = new SurfacePanel
        {
            Dock = DockStyle.Top, Height = height, Padding = new Padding(14, 12, 14, 12),
            Fill = Sunken, Stroke = SurfaceBorder
        };
        surface.Controls.Add(textBox);
        return surface;
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
        using var brush = new SolidBrush(Action);
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

/// <summary>A real WinForms button with the workbench's modest radius and visible states.</summary>
public sealed class WorkbenchButton : Button
{
    private bool _hovered;
    private bool _pressed;

    public WorkbenchButton()
    {
        SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint |
            ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw, true);
    }

    protected override void OnMouseEnter(EventArgs e) { _hovered = true; Invalidate(); base.OnMouseEnter(e); }
    protected override void OnMouseLeave(EventArgs e) { _hovered = false; _pressed = false; Invalidate(); base.OnMouseLeave(e); }
    protected override void OnMouseDown(MouseEventArgs e) { _pressed = true; Invalidate(); base.OnMouseDown(e); }
    protected override void OnMouseUp(MouseEventArgs e) { _pressed = false; Invalidate(); base.OnMouseUp(e); }
    protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
    protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }
    protected override void OnEnabledChanged(EventArgs e) { Invalidate(); base.OnEnabledChanged(e); }

    protected override void OnPaint(PaintEventArgs e)
    {
        // UserPaint does not clear the square corners around the rounded fill.
        // Paint them with the containing surface so no dark strips remain.
        e.Graphics.Clear(Parent?.BackColor ?? Theme.Background);
        var highContrast = SystemInformation.HighContrast;
        var fill = highContrast ? SystemColors.Control : !Enabled ? Theme.Elevated :
            _pressed ? FlatAppearance.MouseDownBackColor :
            _hovered ? FlatAppearance.MouseOverBackColor : BackColor;
        var ink = highContrast ? SystemColors.ControlText : Enabled ? ForeColor : Theme.MutedText;
        var stroke = highContrast ? SystemColors.WindowText : FlatAppearance.BorderColor;
        var rectangle = new Rectangle(1, 1, Math.Max(1, Width - 3), Math.Max(1, Height - 3));
        using var path = RoundedPath(rectangle, LogicalToDeviceUnits(7));
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        using (var brush = new SolidBrush(fill)) e.Graphics.FillPath(brush, path);
        if (highContrast || FlatAppearance.BorderSize > 0)
        {
            using var border = new Pen(stroke, 1);
            e.Graphics.DrawPath(border, path);
        }
        var flags = TextFormatFlags.VerticalCenter | TextFormatFlags.EndEllipsis |
            TextFormatFlags.NoPrefix | (TextAlign == ContentAlignment.MiddleLeft ? TextFormatFlags.Left : TextFormatFlags.HorizontalCenter);
        var textRectangle = Rectangle.Inflate(ClientRectangle, -Math.Max(8, Padding.Left), -2);
        TextRenderer.DrawText(e.Graphics, Text, Font, textRectangle, ink, flags);
        if (Focused && ShowFocusCues)
        {
            var focus = Rectangle.Inflate(rectangle, -3, -3);
            using var focusPath = RoundedPath(focus, LogicalToDeviceUnits(5));
            using var pen = new Pen(highContrast ? SystemColors.Highlight : (fill == Theme.Action ? Color.White : Theme.Action), 1.5f)
            { DashStyle = DashStyle.Dot };
            e.Graphics.DrawPath(pen, focusPath);
        }
    }

    private static GraphicsPath RoundedPath(Rectangle r, int radius)
    {
        radius = Math.Min(radius, Math.Min(r.Width, r.Height) / 2);
        var diameter = Math.Max(1, radius * 2);
        var path = new GraphicsPath();
        path.AddArc(r.Left, r.Top, diameter, diameter, 180, 90);
        path.AddArc(r.Right - diameter, r.Top, diameter, diameter, 270, 90);
        path.AddArc(r.Right - diameter, r.Bottom - diameter, diameter, diameter, 0, 90);
        path.AddArc(r.Left, r.Bottom - diameter, diameter, diameter, 90, 90);
        path.CloseFigure();
        return path;
    }
}

/// <summary>A white group surface with a quiet rounded border.</summary>
public sealed class SurfacePanel : Panel
{
    public int Radius { get; set; } = 10;
    public Color Fill { get; set; } = Theme.Surface;
    public Color Stroke { get; set; } = Theme.SurfaceBorder;

    public SurfacePanel()
    {
        SetStyle(ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer | ControlStyles.ResizeRedraw | ControlStyles.UserPaint, true);
        BackColor = Theme.Background;
    }

    protected override void OnPaint(PaintEventArgs e)
    {
        base.OnPaint(e);
        e.Graphics.SmoothingMode = SmoothingMode.AntiAlias;
        var rect = new Rectangle(0, 0, Width - 1, Height - 1);
        using var path = RoundedRect(rect, LogicalToDeviceUnits(Radius));
        using var fill = new SolidBrush(Fill);
        using var stroke = new Pen(Stroke);
        e.Graphics.FillPath(fill, path);
        e.Graphics.DrawPath(stroke, path);
    }

    private static GraphicsPath RoundedRect(Rectangle r, int radius)
    {
        var d = radius * 2;
        var path = new GraphicsPath();
        path.AddArc(r.Left, r.Top, d, d, 180, 90);
        path.AddArc(r.Right - d, r.Top, d, d, 270, 90);
        path.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
        path.AddArc(r.Left, r.Bottom - d, d, d, 90, 90);
        path.CloseFigure();
        return path;
    }
}
